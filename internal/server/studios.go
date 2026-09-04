package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	fashionPrompt     = "The most extravagant fashion models in the most unique and exquisite dresses ever made, of all shapes and sizes and colors, the new Fashion beauty on beauty"
	microgreensPrompt = "Red Rambo radish microgreens, deep violet-purple cotyledons, plum and amethyst leaves, ruby-magenta stems, soil-grown microgreens only, no people, no hands, extreme macro photography, dew droplets, 100mm macro lens, photorealistic culinary still, Belarro Berlin harvest"
)

type studioSpec struct {
	Slug   string
	Title  string
	Kicker string
	Prompt string
	GPU    int
	Socket string
	Wall   string
	Scope  string
	Branch bool
	Lane   string
	Worker string
}

func builtinStudios() []studioSpec {
	return []studioSpec{
		{
			Slug:   "fashion",
			Title:  "Fashion",
			Kicker: "GPU 3 · FP8 · beauty on beauty",
			Prompt: fashionPrompt,
			GPU:    3,
			Socket: "flux-gpu3.sock",
			Wall:   "/collections/fashion",
			Scope:  "fashion",
			Lane:   "fashion",
			Worker: "flux-gpu3",
		},
		{
			Slug:   "microgreens",
			Title:  "Microgreens",
			Kicker: "GPU 0 · BF16 · Belarro culinary",
			Prompt: microgreensPrompt,
			GPU:    0,
			Socket: "flux-gpu0.sock",
			Wall:   "/collections/microgreens",
			Scope:  "microgreens",
			Branch: true,
			Lane:   "microgreens",
			Worker: "flux-gpu0",
		},
	}
}

func studioBySlug(slug string) (studioSpec, bool) {
	slug = strings.ToLower(strings.TrimSpace(slug))
	for _, st := range builtinStudios() {
		if st.Slug == slug {
			return st, true
		}
	}
	return studioSpec{}, false
}

func (s Server) studioPausePath(slug string) string {
	return filepath.Join(s.cfg.Root, ".fluxd", "studio_"+slug+".pause")
}

func (s Server) studioPaused(slug string) bool {
	_, err := os.Stat(s.studioPausePath(slug))
	return err == nil
}

func (s Server) setStudioPaused(slug string, pause bool) error {
	path := s.studioPausePath(slug)
	if pause {
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			return err
		}
		return os.WriteFile(path, []byte("paused\n"), 0o644)
	}
	_ = os.Remove(path)
	return nil
}

func (s Server) studioSocketPath(st studioSpec) string {
	return filepath.Join(s.cfg.Root, ".fluxd", st.Socket)
}

func (s Server) studioStatePath(st studioSpec) string {
	if st.Branch {
		return protocolBranchStatePath(s.cfg.Root, st.Slug)
	}
	if st.Slug == "fashion" {
		return filepath.Join(s.cfg.Root, ".fluxd", "protocol_stream_gpu3.json")
	}
	return filepath.Join(s.cfg.Root, ".fluxd", "protocol_stream.json")
}

func (s Server) studioPidPath(st studioSpec) string {
	if st.Branch {
		return protocolBranchPidPath(s.cfg.Root, st.Slug)
	}
	if st.Slug == "fashion" {
		return filepath.Join(s.cfg.Root, ".fluxd", "protocol_stream_gpu3.pid")
	}
	return filepath.Join(s.cfg.Root, ".fluxd", "protocol_stream.pid")
}

func unixWorkerAlive(sock string) bool {
	conn, err := net.DialTimeout("unix", sock, 800*time.Millisecond)
	if err != nil {
		return false
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(800 * time.Millisecond))
	if _, err := conn.Write([]byte(`{"op":"jobs"}` + "\n")); err != nil {
		return false
	}
	buf := make([]byte, 8)
	_, err = conn.Read(buf)
	return err == nil || len(buf) > 0
}

func pidAlive(path string) bool {
	raw, err := os.ReadFile(path)
	if err != nil {
		return false
	}
	pid, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || pid <= 1 {
		return false
	}
	return processExists(pid)
}

func processExists(pid int) bool {
	_, err := os.Stat(filepath.Join("/proc", strconv.Itoa(pid)))
	return err == nil
}

func streamerCmdRunning(needle string) bool {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return false
	}
	for _, entry := range entries {
		if !entry.IsDir() {
			continue
		}
		raw, err := os.ReadFile(filepath.Join("/proc", entry.Name(), "cmdline"))
		if err != nil || len(raw) == 0 {
			continue
		}
		cmd := strings.ReplaceAll(string(raw), "\x00", " ")
		if strings.Contains(cmd, "protocol_stream.py") && strings.Contains(cmd, needle) {
			return true
		}
	}
	return false
}

func (s Server) studioKickstart(st studioSpec, workerUp, streamerUp, paused bool) []string {
	need := make([]string, 0, 4)
	if !workerUp {
		need = append(need, fmt.Sprintf("GPU %d worker is down — Start worker, then Start stream", st.GPU))
	}
	if paused {
		need = append(need, "studio is paused — press Start stream")
	} else if !streamerUp {
		need = append(need, "streamer is idle — press Start 256 × 28")
	}
	if workerUp && streamerUp && !paused {
		need = append(need, "nothing — stream is live")
	}
	return need
}

func (s Server) studioSnapshot(st studioSpec) map[string]any {
	sock := s.studioSocketPath(st)
	workerUp := unixWorkerAlive(sock)
	state := readProtocolStreamStateFile(s.studioStatePath(st))
	status := ""
	if state != nil {
		status, _ = state["status"].(string)
	}
	paused := s.studioPaused(st.Slug)
	needle := st.Socket
	if st.Branch {
		needle = "--branch " + st.Slug
	}
	streamerUp := streamerCmdRunning(needle)
	if st.Branch {
		streamerUp = streamerUp || pidAlive(s.studioPidPath(st))
	}
	snap := map[string]any{
		"slug":      st.Slug,
		"title":     st.Title,
		"kicker":    st.Kicker,
		"prompt":    st.Prompt,
		"gpu":       st.GPU,
		"socket":    st.Socket,
		"wall":      st.Wall,
		"scope":     st.Scope,
		"branch":    st.Branch,
		"lane":      st.Lane,
		"worker":    st.Worker,
		"worker_up": workerUp,
		"streamer":  streamerUp,
		"paused":    paused,
		"status":    status,
		"stream":    state,
		"kickstart": s.studioKickstart(st, workerUp, streamerUp, paused),
		"control":   "/studio/" + st.Slug,
	}
	if st.Slug == "microgreens" {
		snap["study"] = s.loadMicrogreensStudy()
		snap["audit"] = digestAudit(s.juryDirForLane("microgreens"), 24)
		snap["jury_up"] = s.microgreensJuryAlive()
		snap["frames"] = s.listStudyFrames(st.Slug, 8)
	}
	return snap
}

func (s Server) studiosPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/")
	if path == "/studios" || path == "/studio" {
		http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "studios.html"))
		return
	}
	if r.URL.Path == "/studio/microgreens" || r.URL.Path == "/studio/microgreens/" {
		http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "study-microgreens.html"))
		return
	}
	if strings.HasPrefix(r.URL.Path, "/studio/") {
		http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "studio.html"))
		return
	}
	http.NotFound(w, r)
}

func (s Server) studiosAPI(w http.ResponseWriter, r *http.Request) {
	rel := strings.Trim(strings.TrimPrefix(r.URL.Path, "/api/studios"), "/")
	if rel == "" {
		if r.Method != http.MethodGet {
			methodNotAllowed(w, http.MethodGet)
			return
		}
		rows := make([]map[string]any, 0, 2)
		for _, st := range builtinStudios() {
			rows = append(rows, s.studioSnapshot(st))
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "studios": rows})
		return
	}
	st, ok := studioBySlug(rel)
	if !ok {
		writeError(w, http.StatusNotFound, "unknown studio")
		return
	}
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "studio": s.studioSnapshot(st)})
	case http.MethodPost:
		s.controlStudio(w, r, st)
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
	}
}

func (s Server) controlStudio(w http.ResponseWriter, r *http.Request, st studioSpec) {
	var req struct {
		Action    string   `json:"action"`
		N         int      `json:"n"`
		Steps     int      `json:"steps"`
		Prompt    string   `json:"prompt"`
		Varieties []string `json:"varieties"`
		Shots     []string `json:"shots"`
		Life      int      `json:"life"`
		Guidance  float64  `json:"guidance"`
		Depth     int      `json:"depth"`
		Seed      string   `json:"seed"`
		Judge     bool     `json:"judge"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil && err != io.EOF {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	action := strings.ToLower(strings.TrimSpace(req.Action))
	if action == "" {
		action = "start"
	}
	switch action {
	case "stop", "pause":
		if err := s.setStudioPaused(st.Slug, true); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		s.stopStudioStreamer(st)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "stopped": true, "studio": s.studioSnapshot(st)})
	case "configure":
		s.writeMicrogreensStudy(req.Varieties, req.Shots, req.Life, req.Guidance, req.Steps, req.Depth, req.N, req.Seed)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "saved": true, "studio": s.studioSnapshot(st)})
	case "reload":
		s.writeMicrogreensStudy(req.Varieties, req.Shots, req.Life, req.Guidance, req.Steps, req.Depth, req.N, req.Seed)
		_ = s.setStudioPaused(st.Slug, false)
		s.stopStudioStreamer(st)
		_ = os.Remove(s.studioStatePath(st))
		if err := s.startStudioStreamer(st, req.N, req.Steps, req.Prompt); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		time.Sleep(200 * time.Millisecond)
		writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "reloaded": true, "studio": s.studioSnapshot(st)})
	case "start", "kickstart":
		s.writeMicrogreensStudy(req.Varieties, req.Shots, req.Life, req.Guidance, req.Steps, req.Depth, req.N, req.Seed)
		_ = s.setStudioPaused(st.Slug, false)
		if !unixWorkerAlive(s.studioSocketPath(st)) {
			if err := s.startStudioWorker(st); err != nil {
				writeError(w, http.StatusServiceUnavailable, "worker down: "+err.Error())
				return
			}
		}
		if err := s.startStudioStreamer(st, req.N, req.Steps, req.Prompt); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		time.Sleep(200 * time.Millisecond)
		writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "started": true, "studio": s.studioSnapshot(st)})
	case "judge-start":
		if err := s.startMicrogreensJury(); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "jury": true, "studio": s.studioSnapshot(st)})
	default:
		writeError(w, http.StatusBadRequest, "action must be start, stop, configure, reload, or judge-start")
	}
}

func (s Server) stopStudioStreamer(st studioSpec) {
	needle := st.Socket
	if st.Branch {
		needle = "--branch " + st.Slug
	}
	entries, _ := os.ReadDir("/proc")
	for _, entry := range entries {
		raw, err := os.ReadFile(filepath.Join("/proc", entry.Name(), "cmdline"))
		if err != nil || len(raw) == 0 {
			continue
		}
		cmd := strings.ReplaceAll(string(raw), "\x00", " ")
		if !strings.Contains(cmd, "protocol_stream.py") || !strings.Contains(cmd, needle) {
			continue
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 1 {
			continue
		}
		proc, err := os.FindProcess(pid)
		if err == nil {
			_ = proc.Kill()
		}
	}
	statePath := s.studioStatePath(st)
	state := readProtocolStreamStateFile(statePath)
	if state == nil {
		state = map[string]any{"lane": st.Lane}
	}
	state["status"] = "stopped"
	state["updated_at"] = time.Now().Unix()
	if encoded, err := json.MarshalIndent(state, "", "  "); err == nil {
		_ = os.WriteFile(statePath, append(encoded, '\n'), 0o644)
	}
}

func (s Server) startStudioStreamer(st studioSpec, n, steps int, prompt string) error {
	if n != 512 {
		n = 256
	}
	if steps != 18 {
		steps = 28
	}
	if strings.TrimSpace(prompt) == "" {
		prompt = st.Prompt
	}
	sock := s.studioSocketPath(st)
	statePath := s.studioStatePath(st)
	logPath := filepath.Join(s.cfg.Root, ".fluxd", "studio_"+st.Slug+".log")
	if err := os.MkdirAll(filepath.Dir(statePath), 0o755); err != nil {
		return err
	}
	if st.Branch {
		if err := os.MkdirAll(filepath.Join(s.cfg.OutputDir, "collections", st.Slug), 0o755); err != nil {
			return err
		}
	}
	args := []string{
		filepath.Join(s.cfg.Root, "protocol_stream.py"),
		"--n", strconv.Itoa(n),
		"--steps", strconv.Itoa(steps),
		"--depth", "2",
		"--prompt", prompt,
		"--socket", sock,
		"--state", statePath,
		"--lane", st.Lane,
	}
	if st.Branch {
		args = append(args, "--branch", st.Slug)
	}
	logf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(s.cfg.Python, append([]string{"-u"}, args...)...)
	cmd.Dir = s.cfg.Root
	cmd.Stdout = logf
	cmd.Stderr = logf
	cmd.Env = append(os.Environ(),
		"OUT_DIR="+s.cfg.OutputDir,
		"FLUX_OUTPUT_DIR="+s.cfg.OutputDir,
		"FLUX_HTTP=http://127.0.0.1:7861",
	)
	if err := cmd.Start(); err != nil {
		_ = logf.Close()
		return err
	}
	_ = os.WriteFile(s.studioPidPath(st), []byte(strconv.Itoa(cmd.Process.Pid)+"\n"), 0o644)
	_ = cmd.Process.Release()
	return nil
}

func (s Server) startStudioWorker(st studioSpec) error {
	sock := s.studioSocketPath(st)
	if unixWorkerAlive(sock) {
		return nil
	}
	logPath := filepath.Join(s.cfg.Root, ".fluxd", st.Worker+".log")
	args := []string{
		filepath.Join(s.cfg.Root, "worker.py"),
		"--socket", sock,
		"--state", filepath.Join(s.cfg.Root, ".fluxd", st.Worker+".jobs.jsonl"),
		"--profile", filepath.Join(s.cfg.Root, ".fluxd", st.Worker+".profile.json"),
		"--model-dir", s.cfg.ModelDir,
		"--out-dir", s.cfg.OutputDir,
		"--backend", "cuda",
		"--preload",
	}
	if st.GPU == 3 {
		fp8 := filepath.Join(filepath.Dir(s.cfg.ModelDir), "FLUX.1-dev-fp8", "flux1-dev-fp8.safetensors")
		if _, err := os.Stat(fp8); err == nil {
			args = append(args, "--fp8-transformer", fp8)
		}
	}
	logf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(s.cfg.Python, args...)
	cmd.Dir = s.cfg.Root
	cmd.Stdout = logf
	cmd.Stderr = logf
	cmd.Env = append(os.Environ(),
		fmt.Sprintf("CUDA_VISIBLE_DEVICES=%d", st.GPU),
		fmt.Sprintf("FLUX_WORKER_GPU=%d", st.GPU),
		"OUT_DIR="+s.cfg.OutputDir,
		"FLUX_OUTPUT_DIR="+s.cfg.OutputDir,
	)
	if err := cmd.Start(); err != nil {
		_ = logf.Close()
		return err
	}
	_ = cmd.Process.Release()
	_ = logf.Close()
	return nil
}

func (s Server) microgreensStudyPath() string {
	return filepath.Join(s.cfg.Root, ".fluxd", "study_microgreens.json")
}

func (s Server) loadMicrogreensStudy() map[string]any {
	cfg := map[string]any{
		"varieties": []string{"red-rambo", "pea", "sunflower", "broccoli", "nasturtium", "amaranth"},
		"shots":     []string{"macro", "bunch", "crudo", "steak", "catalog"},
		"life":      80,
		"guidance":  4.0,
		"steps":     28,
		"n":         256,
		"depth":     2,
		"seed":      "random",
		"judge":     true,
	}
	raw, err := os.ReadFile(s.microgreensStudyPath())
	if err != nil {
		return cfg
	}
	var data map[string]any
	if json.Unmarshal(raw, &data) != nil {
		return cfg
	}
	for k, v := range data {
		cfg[k] = v
	}
	return cfg
}

func (s Server) writeMicrogreensStudy(varieties, shots []string, life int, guidance float64, steps, depth, n int, seed string) {
	cfg := s.loadMicrogreensStudy()
	if len(varieties) > 0 {
		cfg["varieties"] = varieties
	}
	if len(shots) > 0 {
		cfg["shots"] = shots
	}
	if life > 0 {
		cfg["life"] = life
	}
	if guidance > 0 {
		cfg["guidance"] = guidance
	}
	if steps == 18 || steps == 28 {
		cfg["steps"] = steps
	}
	if depth >= 1 && depth <= 3 {
		cfg["depth"] = depth
	}
	if n == 256 || n == 512 {
		cfg["n"] = n
	}
	if seed == "random" || seed == "sequential" {
		cfg["seed"] = seed
	}
	if err := os.MkdirAll(filepath.Dir(s.microgreensStudyPath()), 0o755); err != nil {
		return
	}
	raw, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return
	}
	_ = os.WriteFile(s.microgreensStudyPath(), append(raw, '\n'), 0o644)
}

func (s Server) listStudyFrames(slug string, limit int) []map[string]any {
	dir := filepath.Join(s.cfg.OutputDir, "collections", slug)
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	type item struct {
		name string
		mod  int64
	}
	items := make([]item, 0)
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(strings.ToLower(entry.Name()), ".png") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		items = append(items, item{name: entry.Name(), mod: info.ModTime().Unix()})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].mod > items[j].mod })
	if limit > 0 && len(items) > limit {
		items = items[:limit]
	}
	out := make([]map[string]any, 0, len(items))
	for _, it := range items {
		rel := filepath.ToSlash(filepath.Join("collections", slug, it.name))
		out = append(out, map[string]any{
			"name": it.name,
			"path": "/outputs/" + rel,
			"url":  "/outputs/" + rel,
		})
	}
	return out
}

func (s Server) microgreensJuryPidPath() string {
	return filepath.Join(s.cfg.Root, ".fluxd", "study_microgreens_jury.pid")
}

func (s Server) microgreensJuryAlive() bool {
	return pidAlive(s.microgreensJuryPidPath())
}

func (s Server) startMicrogreensJury() error {
	if s.microgreensJuryAlive() {
		return nil
	}
	out := filepath.Join(s.cfg.OutputDir, "collections", "microgreens")
	if err := os.MkdirAll(out, 0o755); err != nil {
		return err
	}
	logPath := filepath.Join(s.cfg.Root, ".fluxd", "study_microgreens_jury.log")
	logf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(s.cfg.Python, "-u", filepath.Join(s.cfg.Root, "moj_evaluator.py"), "--serve")
	cmd.Dir = s.cfg.Root
	cmd.Stdout = logf
	cmd.Stderr = logf
	cmd.Env = append(os.Environ(),
		"MOJ_JOBS_LEDGER="+filepath.Join(s.cfg.Root, ".fluxd", "flux-gpu0.jobs.jsonl"),
		"MOJ_OUTPUT_DIR="+out,
		"OUT_DIR="+out,
		"FLUX_OUTPUT_DIR="+out,
	)
	if err := cmd.Start(); err != nil {
		_ = logf.Close()
		return err
	}
	_ = os.WriteFile(s.microgreensJuryPidPath(), []byte(strconv.Itoa(cmd.Process.Pid)+"\n"), 0o644)
	_ = cmd.Process.Release()
	return nil
}
