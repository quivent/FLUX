package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const beautyPipelinePrompt = "Editorial image of astonishing contemporary beauty, tactile material, off-centre composition, reserved highlights, one vivid accent, visible surface, specific light, no generic luxury photography"

func beautyPipelineStatePath(root string) string {
	return filepath.Join(root, ".fluxd", "protocol_stream_gpu3.json")
}

func beautyPipelinePIDPath(root string) string {
	return filepath.Join(root, ".fluxd", "beauty_pipeline.pid")
}

func beautyPipelineProcess(root string) (*os.Process, bool) {
	raw, err := os.ReadFile(beautyPipelinePIDPath(root))
	if err != nil {
		return nil, false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || pid <= 1 || syscall.Kill(pid, 0) != nil {
		return nil, false
	}
	process, err := os.FindProcess(pid)
	return process, err == nil
}

func (s Server) beautyPipelineAPI(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		state := readProtocolStreamStateFile(beautyPipelineStatePath(s.cfg.Root))
		if state == nil {
			state = map[string]any{"status": "idle", "stage": "idle"}
		}
		_, running := beautyPipelineProcess(s.cfg.Root)
		worker := map[string]any{"live": false, "loaded": false}
		if response, err := s.client.Request(map[string]any{"op": "ping"}); err == nil {
			worker = map[string]any{"live": true, "loaded": response.Loaded, "device": response.Device}
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "running": running, "pipeline": state, "worker": worker,
		})
	case http.MethodPost:
		var request struct {
			Action   string  `json:"action"`
			Prompt   string  `json:"prompt"`
			N        int     `json:"n"`
			Steps    int     `json:"steps"`
			Width    int     `json:"width"`
			Height   int     `json:"height"`
			Guidance float64 `json:"guidance"`
			Seed     string  `json:"seed"`
			AdvisorTimeout float64 `json:"advisor_timeout"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			writeError(w, http.StatusBadRequest, "invalid JSON body")
			return
		}
		switch strings.ToLower(strings.TrimSpace(request.Action)) {
		case "start", "run":
			s.startBeautyPipeline(w, request.Prompt, request.N, request.Steps, request.Width, request.Height, request.Guidance, request.Seed, request.AdvisorTimeout)
		case "stop":
			s.stopBeautyPipeline(w)
		default:
			writeError(w, http.StatusBadRequest, "action must be start or stop")
		}
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
	}
}

func (s Server) startBeautyPipeline(w http.ResponseWriter, prompt string, n, steps, width, height int, guidance float64, seed string, advisorTimeout float64) {
	if _, running := beautyPipelineProcess(s.cfg.Root); running {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "started": false, "pipeline": readProtocolStreamStateFile(beautyPipelineStatePath(s.cfg.Root)),
		})
		return
	}
	if n < 0 || n > 10000 {
		writeError(w, http.StatusBadRequest, "n must be between 0 and 10000; 0 runs continuously")
		return
	}
	if steps == 0 {
		steps = 18
	}
	if steps < 4 || steps > 40 {
		writeError(w, http.StatusBadRequest, "steps must be between 4 and 40")
		return
	}
	if width == 0 {
		width = 512
	}
	if height == 0 {
		height = 512
	}
	if width != 512 || height != 512 {
		writeError(w, http.StatusBadRequest, "the latency-first Beauty loop is fixed at 512x512")
		return
	}
	if guidance == 0 {
		guidance = 3.5
	}
	prompt = strings.TrimSpace(prompt)
	if prompt == "" {
		prompt = beautyPipelinePrompt
	}
	if err := os.MkdirAll(filepath.Join(s.cfg.Root, ".fluxd"), 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	socketPath, _, _, _ := s.client.Paths()
	logPath := filepath.Join(s.cfg.Root, ".fluxd", "beauty_pipeline.log")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	args := []string{
		"-u", filepath.Join(s.cfg.Root, "beauty_pipeline.py"),
		"--prompt", prompt, "--n", strconv.Itoa(n), "--steps", strconv.Itoa(steps),
		"--width", strconv.Itoa(width), "--height", strconv.Itoa(height),
		"--guidance", strconv.FormatFloat(guidance, 'f', -1, 64),
		"--socket", socketPath, "--state", beautyPipelineStatePath(s.cfg.Root),
		"--pid", beautyPipelinePIDPath(s.cfg.Root), "--lane", "fashion",
	}
	if strings.TrimSpace(seed) != "" {
		args = append(args, "--seed", strings.TrimSpace(seed))
	}
	if advisorTimeout > 0 {
		args = append(args, "--advisor-timeout", strconv.FormatFloat(advisorTimeout, 'f', -1, 64))
	}
	command := exec.Command(s.cfg.Python, args...)
	command.Dir = s.cfg.Root
	command.Stdout = logFile
	command.Stderr = logFile
	command.Env = append(os.Environ(),
		"OUT_DIR="+s.cfg.OutputDir,
		"FLUX_OUTPUT_DIR="+s.cfg.OutputDir,
		"PYTHONUNBUFFERED=1",
	)
	command.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := command.Start(); err != nil {
		_ = logFile.Close()
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	pid := command.Process.Pid
	_ = command.Process.Release()
	_ = logFile.Close()
	go func() {
		if err := s.client.Start(true); err != nil {
			_ = appendBeautyPipelineLog(logPath, "worker start: "+err.Error())
		}
	}()
	time.Sleep(150 * time.Millisecond)
	writeJSON(w, http.StatusAccepted, map[string]any{
		"ok": true, "started": true, "pid": pid,
		"pipeline": readProtocolStreamStateFile(beautyPipelineStatePath(s.cfg.Root)),
	})
}

func appendBeautyPipelineLog(path, message string) error {
	file, err := os.OpenFile(path, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	defer file.Close()
	_, err = fmt.Fprintf(file, "%s %s\n", time.Now().UTC().Format(time.RFC3339), message)
	return err
}

func (s Server) stopBeautyPipeline(w http.ResponseWriter) {
	process, running := beautyPipelineProcess(s.cfg.Root)
	if running {
		_ = process.Signal(syscall.SIGTERM)
	}
	state := readProtocolStreamStateFile(beautyPipelineStatePath(s.cfg.Root))
	if state == nil {
		state = map[string]any{}
	}
	state["status"] = "stopping"
	state["updated_at"] = time.Now().Unix()
	if raw, err := json.MarshalIndent(state, "", "  "); err == nil {
		_ = os.WriteFile(beautyPipelineStatePath(s.cfg.Root), append(raw, '\n'), 0o644)
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "stopped": running, "pipeline": state})
}

// beautyMetricsAPI returns EGRL tractability metrics for the operator who is
// only periodically in the loop. It shells out to beauty_eye_gate.py metrics,
// which computes everything from the append-only ledgers, and passes the JSON
// through. On any failure it returns a well-formed zeroed payload so the site
// never breaks — the point is at-a-glance tractability, not a hard dependency.
func (s Server) beautyMetricsAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	script := filepath.Join(s.cfg.Root, "beauty_eye_gate.py")
	cmd := exec.Command(s.cfg.Python, script, "--output-dir", s.cfg.OutputDir, "metrics")
	cmd.Dir = s.cfg.Root
	out, err := cmd.Output()
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": false, "error": err.Error(),
			"decided": 0, "crowns": 0, "kills": 0, "pending": 0,
			"critic_operator_agreement_rate": nil, "blind_submission_rate": nil,
			"override_yield": nil, "final_submissions": 0,
		})
		return
	}
	var metrics map[string]any
	if jsonErr := json.Unmarshal(out, &metrics); jsonErr != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "metrics parse failed"})
		return
	}
	metrics["ok"] = true
	writeJSON(w, http.StatusOK, metrics)
}
