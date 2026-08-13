package server

import (
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

type stallionMotionRequest struct {
	Modes      []string `json:"modes"`
	Frames     int      `json:"frames"`
	FPS        int      `json:"fps"`
	Seed       int      `json:"seed"`
	Rounds     int      `json:"rounds"`
	Continuous bool     `json:"continuous"`
}

var stallionMotionModes = map[string]bool{
	"spectral_loop": true,
	"continuity":    true,
	"kinetic":       true,
}

var stallionMotionStartMu sync.Mutex

func (s Server) stallionMotionLab(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/studies/stallion/" {
		http.Redirect(w, r, "/studies/stallion", http.StatusPermanentRedirect)
		return
	}
	if r.URL.Path != "/studies/stallion" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "stallion-lab.html"))
}

func (s Server) stallionMotionAPI(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		if r.URL.Query().Get("history") == "1" {
			history, err := s.stallionMotionHistory()
			if err != nil {
				writeError(w, http.StatusInternalServerError, err.Error())
				return
			}
			writeJSON(w, http.StatusOK, history)
			return
		}
		status, err := s.stallionMotionStatus()
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, status)
	case http.MethodPost:
		s.startStallionMotion(w, r)
	case http.MethodDelete:
		s.stopStallionMotion(w)
	default:
		w.Header().Set("Allow", "GET, POST, DELETE")
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s Server) stallionMotionHistory() (map[string]any, error) {
	runsRoot := filepath.Join(s.stallionMotionRoot(), "runs")
	entries, err := os.ReadDir(runsRoot)
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{"ok": true, "run_count": 0, "result_count": 0, "runs": []any{}}, nil
	}
	if err != nil {
		return nil, err
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() > entries[j].Name() })
	runs := make([]map[string]any, 0, len(entries))
	resultCount := 0
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		runID := filepath.Base(entry.Name())
		raw, readErr := os.ReadFile(filepath.Join(runsRoot, runID, "status.json"))
		if readErr != nil {
			continue
		}
		var status map[string]any
		if json.Unmarshal(raw, &status) != nil {
			continue
		}
		results := compactStallionResults(status["results"], runID)
		resultCount += len(results)
		run := map[string]any{
			"run_id":       runID,
			"state":        stringValue(status["state"]),
			"source_kind":  stringValue(status["source_kind"]),
			"started_at":   status["started_at"],
			"completed_at": status["completed_at"],
			"frames":       status["frames"],
			"fps":          status["fps"],
			"rounds":       status["rounds"],
			"result_count": len(results),
			"results":      results,
			"manifest_url": "/studies/stallion/results/" + runID + "/manifest.json",
		}
		if sheet := filepath.ToSlash(stringValue(status["contact_sheet"])); sheet != "" {
			run["contact_sheet_url"] = "/studies/stallion/results/" + runID + "/" + sheet
		}
		runs = append(runs, run)
	}
	return map[string]any{
		"ok":           true,
		"run_count":    len(runs),
		"result_count": resultCount,
		"runs":         runs,
	}, nil
}

func compactStallionResults(rawResults any, runID string) []map[string]any {
	rawRows, _ := rawResults.([]any)
	results := make([]map[string]any, 0, len(rawRows))
	for _, raw := range rawRows {
		row, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		result := map[string]any{
			"run_id":          runID,
			"mode":            stringValue(row["mode"]),
			"round":           row["round"],
			"rank":            row["rank"],
			"family":          row["family"],
			"description":     stringValue(row["description"]),
			"selection_score": row["selection_score"],
		}
		for _, key := range []string{"video", "poster"} {
			name := filepath.ToSlash(stringValue(row[key]))
			if name != "" {
				result[key+"_url"] = "/studies/stallion/results/" + runID + "/" + name
			}
		}
		if metrics, ok := row["metrics"].(map[string]any); ok {
			compact := make(map[string]any)
			for _, key := range []string{"frames", "unique_frames", "mean_visual_jump", "p95_visual_jump", "worst_visual_jump", "direction_reversals", "loop_seam_jump", "selection_score"} {
				if value, exists := metrics[key]; exists {
					compact[key] = value
				}
			}
			result["metrics"] = compact
		}
		results = append(results, result)
	}
	return results
}

func (s Server) stallionMotionRoot() string {
	return filepath.Join(s.cfg.OutputDir, "studies", "stallion-motion")
}

func (s Server) stallionMotionStatus() (map[string]any, error) {
	root := s.stallionMotionRoot()
	latestRaw, err := os.ReadFile(filepath.Join(root, "latest.json"))
	if errors.Is(err, os.ErrNotExist) {
		return map[string]any{"ok": true, "state": "idle", "protocol": "tea.stallion-motion.v1"}, nil
	}
	if err != nil {
		return nil, err
	}
	var latest map[string]any
	if err := json.Unmarshal(latestRaw, &latest); err != nil {
		return nil, err
	}
	runID := filepath.Base(stringValue(latest["run_id"]))
	if runID == "." || runID == "" {
		return nil, errors.New("invalid Stallion latest run")
	}
	statusRaw, err := os.ReadFile(filepath.Join(root, "runs", runID, "status.json"))
	if err != nil {
		return map[string]any{"ok": true, "state": "starting", "run_id": runID}, nil
	}
	var status map[string]any
	if err := json.Unmarshal(statusRaw, &status); err != nil {
		return nil, err
	}
	status["ok"] = true
	status["run_id"] = runID
	if results, ok := status["results"].([]any); ok {
		for _, raw := range results {
			row, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			for _, key := range []string{"video", "poster"} {
				name := filepath.ToSlash(stringValue(row[key]))
				if name != "" {
					row[key+"_url"] = "/studies/stallion/results/" + runID + "/" + name
				}
			}
		}
	}
	if sheet := filepath.ToSlash(stringValue(status["contact_sheet"])); sheet != "" {
		status["contact_sheet_url"] = "/studies/stallion/results/" + runID + "/" + sheet
	}
	status["manifest_url"] = "/studies/stallion/results/" + runID + "/manifest.json"
	return status, nil
}

func (s Server) startStallionMotion(w http.ResponseWriter, r *http.Request) {
	stallionMotionStartMu.Lock()
	defer stallionMotionStartMu.Unlock()
	current, err := s.stallionMotionStatus()
	if err == nil && (stringValue(current["state"]) == "running" || stringValue(current["state"]) == "starting") {
		writeJSON(w, http.StatusConflict, map[string]any{"ok": false, "error": "a Stallion motion experiment is already running", "run": current})
		return
	}
	var req stallionMotionRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 32<<10)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid Stallion motion request")
		return
	}
	if len(req.Modes) == 0 {
		req.Modes = []string{"spectral_loop", "continuity", "kinetic"}
	}
	seen := make(map[string]bool)
	modes := make([]string, 0, len(req.Modes))
	for _, mode := range req.Modes {
		mode = strings.ToLower(strings.TrimSpace(mode))
		if !stallionMotionModes[mode] {
			writeError(w, http.StatusBadRequest, fmt.Sprintf("unknown motion mode %q", mode))
			return
		}
		if !seen[mode] {
			seen[mode] = true
			modes = append(modes, mode)
		}
	}
	frames := clampInt(req.Frames, 8, 96, 24)
	fps := clampInt(req.FPS, 4, 24, 10)
	rounds := clampInt(req.Rounds, 1, 12, 3)
	seed := req.Seed
	if seed == 0 {
		seed = 7
	}
	runID := fmt.Sprintf("stallion-motion-%s-%03d", time.Now().UTC().Format("20060102-150405"), time.Now().Nanosecond()/1_000_000)
	root := s.stallionMotionRoot()
	if err := os.MkdirAll(filepath.Join(root, "runs"), 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	commandArgs := []string{
		filepath.Join(s.cfg.Root, "scripts", "stallion_motion_graph.py"),
		"--source", filepath.Join(s.cfg.Root, "apps", "tea", "public", "assets", "stallion-atlas-grid.jpg"),
		"--protocol", filepath.Join(s.cfg.Root, "apps", "tea", "protocols", "stallion-motion-v1.json"),
		"--output-root", root,
		"--run-id", runID,
		"--modes", strings.Join(modes, ","),
		"--frames", strconv.Itoa(frames),
		"--fps", strconv.Itoa(fps),
		"--seed", strconv.Itoa(seed),
		"--rounds", strconv.Itoa(rounds),
	}
	if req.Continuous {
		commandArgs = append(commandArgs, "--continuous", "--round-pause", "1")
	}
	command := exec.Command(s.cfg.Python, commandArgs...)
	command.Env = append(os.Environ(), "OMP_NUM_THREADS=12", "OPENBLAS_NUM_THREADS=12", "MKL_NUM_THREADS=12")
	command.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	logPath := filepath.Join(root, "runner.log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	command.Stdout, command.Stderr = logFile, logFile
	if err := command.Start(); err != nil {
		_ = logFile.Close()
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	pid := command.Process.Pid
	if err := writeStallionLatest(root, runID); err != nil {
		_ = syscall.Kill(-pid, syscall.SIGTERM)
		_ = logFile.Close()
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = command.Process.Release()
	_ = logFile.Close()
	writeJSON(w, http.StatusAccepted, map[string]any{
		"ok": true, "state": "starting", "run_id": runID, "pid": pid,
		"frames": frames, "fps": fps, "rounds": rounds, "continuous": req.Continuous, "modes": modes,
		"message": func() string {
			if req.Continuous {
				return "Continuous Stallion motion exploration started; it will run until stopped"
			}
			return fmt.Sprintf("Stallion motion exploration started: %d candidates", rounds*len(modes))
		}(),
	})
}

func writeStallionLatest(root, runID string) error {
	payload, err := json.MarshalIndent(map[string]any{
		"run_id":     runID,
		"status_url": filepath.ToSlash(filepath.Join("runs", runID, "status.json")),
	}, "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(root, "latest.json.go.tmp")
	if err := os.WriteFile(tmp, append(payload, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(root, "latest.json"))
}

func (s Server) stopStallionMotion(w http.ResponseWriter) {
	status, err := s.stallionMotionStatus()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if stringValue(status["state"]) != "running" && stringValue(status["state"]) != "starting" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "state": stringValue(status["state"]), "message": "no Stallion motion run is active"})
		return
	}
	runID := filepath.Base(stringValue(status["run_id"]))
	raw, err := os.ReadFile(filepath.Join(s.stallionMotionRoot(), "runs", runID, "pid"))
	if err != nil {
		writeError(w, http.StatusConflict, "active run has not written its pid yet")
		return
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || pid < 2 {
		writeError(w, http.StatusInternalServerError, "invalid Stallion motion pid")
		return
	}
	if err := syscall.Kill(-pid, syscall.SIGTERM); err != nil && !errors.Is(err, syscall.ESRCH) {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "state": "stopping", "run_id": runID})
}

func (s Server) stallionMotionResult(w http.ResponseWriter, r *http.Request) {
	rel := strings.TrimPrefix(r.URL.Path, "/studies/stallion/results/")
	clean := filepath.Clean(filepath.FromSlash(rel))
	if clean == "." || filepath.IsAbs(clean) || strings.HasPrefix(clean, "..") {
		http.NotFound(w, r)
		return
	}
	ext := strings.ToLower(filepath.Ext(clean))
	if ext != ".json" && ext != ".jpg" && ext != ".mp4" {
		http.NotFound(w, r)
		return
	}
	root := filepath.Join(s.stallionMotionRoot(), "runs")
	path := filepath.Join(root, clean)
	abs, err := filepath.Abs(path)
	if err != nil || !pathInside(root, abs) {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, abs)
}
