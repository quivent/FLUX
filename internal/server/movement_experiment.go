package server

import (
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
)

const movementDraftName = "gpu0-silk-wind-001.json"

var movementMu sync.Mutex

type movementPatch struct {
	Prompt      string   `json:"prompt"`
	Steps       int      `json:"steps"`
	Size        int      `json:"size"`
	RenderCount int      `json:"render_count"`
	Guidance    float64  `json:"guidance"`
	Orbit       float64  `json:"orbit"`
	Arc         float64  `json:"arc"`
	SeedLock    float64  `json:"seed_lock"`
	PaceMS      int      `json:"pace_ms"`
	Start       bool     `json:"start"`
	Restart     bool     `json:"restart"`
	Stop        bool     `json:"stop"`
}

func (s Server) movementDraftPath() string {
	return filepath.Join(s.cfg.Root, "atlas_drafts", movementDraftName)
}

func (s Server) movementStatePath() string {
	return filepath.Join(s.cfg.Root, ".fluxd", "motion_stream.json")
}

func (s Server) movementPIDPath() string {
	return filepath.Join(s.cfg.Root, ".fluxd", "motion_stream.pid")
}

func (s Server) teaMovementAPI(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, http.StatusOK, s.movementSnapshot())
	case http.MethodPost:
		s.patchMovement(w, r)
	case http.MethodDelete:
		s.stopMovementStreamer()
		writeJSON(w, http.StatusOK, s.movementSnapshot())
	default:
		w.Header().Set("Allow", "GET, POST, DELETE")
		writeError(w, http.StatusMethodNotAllowed, "method not allowed")
	}
}

func (s Server) movementSnapshot() map[string]any {
	draft := s.readMovementDraft()
	state := map[string]any{}
	if raw, err := os.ReadFile(s.movementStatePath()); err == nil {
		_ = json.Unmarshal(raw, &state)
	}
	pid, running := s.movementPID()
	id := stringValue(draft["id"])
	if id == "" {
		id = "gpu0-silk-wind-001"
	}
	return map[string]any{
		"ok":       true,
		"running":  running,
		"pid":      pid,
		"id":       id,
		"sphere":   id + ".sphere",
		"draft":    draft,
		"state":    state,
		"pace":     loadDeskPace(s.cfg.Root),
		"wall":     "/movement",
		"output":   "/outputs/atlas/" + id + ".sphere/",
	}
}

func (s Server) readMovementDraft() map[string]any {
	draft := map[string]any{}
	raw, err := os.ReadFile(s.movementDraftPath())
	if err != nil {
		return draft
	}
	_ = json.Unmarshal(raw, &draft)
	return draft
}

func (s Server) writeMovementDraft(draft map[string]any) error {
	raw, err := json.MarshalIndent(draft, "", "  ")
	if err != nil {
		return err
	}
	tmp := s.movementDraftPath() + ".tmp"
	if err := os.WriteFile(tmp, append(raw, '\n'), 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.movementDraftPath())
}

func (s Server) movementPID() (int, bool) {
	raw, err := os.ReadFile(s.movementPIDPath())
	if err != nil {
		return 0, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || pid < 2 {
		return 0, false
	}
	if err := syscall.Kill(pid, 0); err != nil {
		return pid, false
	}
	return pid, true
}

func (s Server) patchMovement(w http.ResponseWriter, r *http.Request) {
	movementMu.Lock()
	defer movementMu.Unlock()
	var req movementPatch
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid movement request")
		return
	}
	draft := s.readMovementDraft()
	if draft == nil {
		draft = map[string]any{}
	}
	if strings.TrimSpace(req.Prompt) != "" {
		draft["prompt"] = strings.TrimSpace(req.Prompt)
	}
	if req.Steps != 0 {
		draft["steps"] = clampInt(req.Steps, 8, 48, 28)
	}
	if req.Size != 0 {
		draft["size"] = clampInt(req.Size, 512, 1024, 768)
	}
	if req.RenderCount != 0 {
		draft["render_count"] = clampInt(req.RenderCount, 8, 256, 64)
	}
	if req.Guidance != 0 {
		draft["guidance"] = clampFloat(req.Guidance, 1, 12)
	}
	if req.Orbit != 0 {
		draft["orbit"] = clampFloat(req.Orbit, 0.02, 1.5)
	}
	if req.Arc != 0 {
		draft["arc"] = clampFloat(req.Arc, 0.2, 2.5)
	}
	if req.SeedLock != 0 {
		draft["seed_lock"] = clampFloat(req.SeedLock, 0.05, 0.95)
	}
	if err := s.writeMovementDraft(draft); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if req.PaceMS != 0 {
		p := loadDeskPace(s.cfg.Root)
		p.MovementMS = clampInt(req.PaceMS, 24, 240, 83)
		_ = saveDeskPace(s.cfg.Root, p)
	}
	_, running := s.movementPID()
	if req.Stop {
		s.stopMovementStreamer()
	} else if req.Start || req.Restart {
		if running {
			s.stopMovementStreamer()
			time.Sleep(400 * time.Millisecond)
		}
		if err := s.startMovementStreamer(); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
	}
	writeJSON(w, http.StatusOK, s.movementSnapshot())
}

func (s Server) startMovementStreamer() error {
	if _, running := s.movementPID(); running {
		return nil
	}
	logPath := filepath.Join(s.cfg.Root, ".fluxd", "motion_stream.log")
	if err := os.MkdirAll(filepath.Dir(logPath), 0o755); err != nil {
		return err
	}
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(s.cfg.Python, "-u", filepath.Join(s.cfg.Root, "gpu0_motion_stream.py"))
	cmd.Dir = s.cfg.Root
	cmd.Stdout, cmd.Stderr = logFile, logFile
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	cmd.Env = append(os.Environ(),
		"OUT_DIR="+s.cfg.OutputDir,
		"FLUX_OUTPUT_DIR="+s.cfg.OutputDir,
	)
	if err := cmd.Start(); err != nil {
		_ = logFile.Close()
		return err
	}
	if err := os.WriteFile(s.movementPIDPath(), []byte(strconv.Itoa(cmd.Process.Pid)+"\n"), 0o644); err != nil {
		_ = syscall.Kill(-cmd.Process.Pid, syscall.SIGTERM)
		_ = logFile.Close()
		return err
	}
	_ = cmd.Process.Release()
	_ = logFile.Close()
	return nil
}

func (s Server) stopMovementStreamer() {
	pid, _ := s.movementPID()
	if pid >= 2 {
		_ = syscall.Kill(pid, syscall.SIGTERM)
		_ = syscall.Kill(-pid, syscall.SIGTERM)
		time.Sleep(200 * time.Millisecond)
		_ = syscall.Kill(pid, syscall.SIGKILL)
		_ = syscall.Kill(-pid, syscall.SIGKILL)
	}
	_ = os.Remove(s.movementPIDPath())
}
