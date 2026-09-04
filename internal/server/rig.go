package server

import (
	"context"
	"encoding/csv"
	"encoding/json"
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

	"local/flux/internal/daemon"
)

var rigHub = struct {
	sync.Mutex
	clients map[chan map[string]any]struct{}
	latest  map[string]any
}{clients: map[chan map[string]any]struct{}{}}

type rigGPU struct {
	Index       int              `json:"index"`
	UUID        string           `json:"uuid"`
	Name        string           `json:"name"`
	Compute     float64          `json:"compute_percent"`
	MemoryUsed  float64          `json:"memory_used_mib"`
	MemoryTotal float64          `json:"memory_total_mib"`
	MemoryFree  float64          `json:"memory_free_mib"`
	Temperature float64          `json:"temperature_c"`
	Power       float64          `json:"power_w"`
	PowerLimit  float64          `json:"power_limit_w"`
	Performing  string           `json:"performing"`
	Detail      string           `json:"detail"`
	Brief       string           `json:"brief"`
	Wall        string           `json:"wall"`
	Assignment  map[string]any   `json:"assignment"`
	Worker      map[string]any   `json:"worker"`
	Flux        map[string]any   `json:"flux"`
	Occupants   []map[string]any `json:"occupants"`
	Processes   []rigProcess     `json:"processes"`
	Tasks       []map[string]any `json:"tasks"`
}

type rigProcess struct {
	PID       int     `json:"pid"`
	MemoryMiB float64 `json:"memory_mib"`
	Name      string  `json:"name"`
	Suite     string  `json:"suite"`
	Task      string  `json:"task"`
	Command   string  `json:"command"`
	Model     string  `json:"model"`
	Precision string  `json:"precision"`
	Path      string  `json:"path"`
}

func (s Server) rigPage(w http.ResponseWriter, r *http.Request) {
	rel := strings.Trim(strings.TrimPrefix(r.URL.Path, "/rig"), "/")
	if rel == "" || rel == "index.html" {
		http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "rig.html"))
		return
	}
	file := filepath.Join(s.cfg.Root, "apps", "tea", "public", filepath.FromSlash(rel))
	if info, err := os.Stat(file); err == nil && !info.IsDir() {
		http.ServeFile(w, r, file)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "rig.html"))
}

func (s Server) domainsPage(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "domains.html"))
}

func (s Server) rigSnapshot() map[string]any {
	gpus, err := probeRigGPUs()
	if err != nil {
		return map[string]any{"ok": false, "error": err.Error(), "updated_at": time.Now().UTC()}
	}
	attachRigProcesses(gpus)
	if s.fleetOn() {
		for _, status := range s.pool.Status() {
			if status.GPU < 0 || status.GPU >= len(gpus) {
				continue
			}
			g := &gpus[status.GPU]
			g.Worker = map[string]any{"name": status.Name, "up": status.Up, "loaded": status.Loaded, "backend": status.Backend, "active": status.Active, "jobs": status.Jobs, "error": status.Error}
			for _, job := range status.JobList {
				state := fmt.Sprint(job["status"])
				if state == "running" || state == "queued" {
					g.Tasks = append(g.Tasks, compactRigTask(job))
				}
			}
		}
	}
	s.attachLiveWork(gpus)
	return map[string]any{"ok": true, "host": hostname(), "updated_at": time.Now().UTC(), "gpus": gpus, "capacity": rigCapacity(gpus), "inventory": map[string]any{"knotext": knotextPresent()}}
}

func (s Server) rigStatusAPI(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	snap := s.rigSnapshot()
	if ok, _ := snap["ok"].(bool); !ok {
		w.WriteHeader(http.StatusServiceUnavailable)
	}
	_ = json.NewEncoder(w).Encode(snap)
}

func (s Server) runRigHub(ctx context.Context) {
	tick := time.NewTicker(time.Second)
	defer tick.Stop()
	publish := func() {
		snap := s.rigSnapshot()
		rigHub.Lock()
		rigHub.latest = snap
		clients := make([]chan map[string]any, 0, len(rigHub.clients))
		for ch := range rigHub.clients {
			clients = append(clients, ch)
		}
		rigHub.Unlock()
		for _, ch := range clients {
			select {
			case ch <- snap:
			default:
			}
		}
	}
	publish()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			publish()
		}
	}
}

func (s Server) rigWS(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	conn, err := upgradeWebSocket(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	defer conn.Close()

	done := make(chan struct{})
	go func() {
		conn.readLoop()
		close(done)
	}()

	updates := make(chan map[string]any, 4)
	rigHub.Lock()
	rigHub.clients[updates] = struct{}{}
	latest := rigHub.latest
	rigHub.Unlock()
	defer func() {
		rigHub.Lock()
		delete(rigHub.clients, updates)
		rigHub.Unlock()
	}()

	send := func(snap map[string]any) bool {
		if snap == nil {
			return true
		}
		raw, err := json.Marshal(snap)
		if err != nil {
			return false
		}
		return conn.writeText(raw) == nil
	}
	if latest == nil {
		latest = s.rigSnapshot()
	}
	if !send(latest) {
		return
	}
	ping := time.NewTicker(wsPingInterval)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-done:
			return
		case <-ping.C:
			if conn.writePing() != nil {
				return
			}
		case snap := <-updates:
			if !send(snap) {
				return
			}
		}
	}
}

func (s Server) attachLiveWork(gpus []rigGPU) {
	dir := filepath.Join(s.cfg.Root, ".fluxd")
	streams := loadRigStreams(dir)
	byIndex := map[int]*rigGPU{}
	for i := range gpus {
		byIndex[gpus[i].Index] = &gpus[i]
	}
	for gpu, asg := range streams {
		g := byIndex[gpu]
		if g == nil {
			continue
		}
		if g.Assignment == nil || assignmentRank(asg) >= assignmentRank(g.Assignment) {
			g.Assignment = asg
		}
	}
	for i := range gpus {
		g := &gpus[i]
		decorateRigGPU(g)
		name := fmt.Sprintf("flux-gpu%d", g.Index)
		client := daemon.NewNamed(s.cfg, name)
		resp, err := client.Request(map[string]any{"op": "jobs"})
		if err == nil {
			active := 0
			if g.Worker == nil {
				g.Worker = map[string]any{}
			}
			g.Worker["name"] = name
			g.Worker["up"] = true
			g.Worker["loaded"] = resp.Loaded
			if resp.Backend != "" {
				g.Worker["backend"] = resp.Backend
			}
			seen := map[string]bool{}
			for _, t := range g.Tasks {
				seen[fmt.Sprint(t["id"])] = true
			}
			for _, job := range resp.Jobs {
				state := fmt.Sprint(job["status"])
				if state != "running" && state != "queued" {
					continue
				}
				active++
				id := fmt.Sprint(job["id"])
				if seen[id] {
					continue
				}
				g.Tasks = append(g.Tasks, compactRigTask(job))
				seen[id] = true
			}
			g.Worker["active"] = active
			g.Worker["jobs"] = len(resp.Jobs)
		}
		if g.Flux != nil {
			if g.Worker == nil {
				g.Worker = map[string]any{}
			}
			g.Worker["loaded"] = true
			g.Worker["model"] = g.Flux["model"]
			g.Worker["precision"] = g.Flux["precision"]
			g.Worker["vram_mib"] = g.Flux["memory_mib"]
		}
		g.Performing, g.Detail, g.Brief, g.Wall = describeGPU(*g)
	}
}

func decorateRigGPU(g *rigGPU) {
	occupants := make([]map[string]any, 0, len(g.Processes))
	for i := range g.Processes {
		p := &g.Processes[i]
		p.Model, p.Precision, p.Path = parseRigModel(p.Command, p.Suite, p.Task)
		occupants = append(occupants, map[string]any{
			"pid": p.PID, "suite": p.Suite, "task": p.Task, "model": p.Model,
			"precision": p.Precision, "path": p.Path, "memory_mib": p.MemoryMiB,
		})
		if p.Suite != "FLUX renderer" {
			continue
		}
		g.Flux = map[string]any{
			"present": true, "model": p.Model, "precision": p.Precision,
			"path": p.Path, "memory_mib": p.MemoryMiB, "pid": p.PID,
			"socket": fmt.Sprintf("flux-gpu%d.sock", g.Index),
		}
	}
	g.Occupants = occupants
}

func parseRigModel(command, suite, task string) (model, precision, path string) {
	lower := strings.ToLower(command)
	if strings.Contains(lower, "worker.py") {
		dir := argAfter(command, "--model-dir", "")
		fp8 := argAfter(command, "--fp8-transformer", "")
		if dir != "" {
			path = dir
			model = filepath.Base(strings.TrimSuffix(dir, "/"))
		} else {
			model = "FLUX.1-dev"
		}
		if fp8 != "" {
			precision = "FP8"
			path = fp8
			if model == "FLUX.1-dev" {
				model = "FLUX.1-dev"
			}
		} else {
			precision = "BF16"
		}
		return model, precision, path
	}
	if strings.Contains(lower, "vllm") {
		path = argAfter(command, "--model", "")
		served := argAfter(command, "--served-model-name", "")
		if path != "" {
			model = filepath.Base(strings.TrimSuffix(path, "/"))
		}
		if served != "" {
			if model != "" && served != model {
				model = served + " · " + model
			} else if model == "" {
				model = served
			}
		}
		switch {
		case strings.Contains(lower, "awq"):
			precision = "AWQ"
		case strings.Contains(lower, "fp8"):
			precision = "FP8"
		case strings.Contains(lower, "nvfp4"), strings.Contains(lower, "fp4"):
			precision = "FP4"
		}
		if model == "" {
			model = task
		}
		return model, precision, path
	}
	if strings.Contains(lower, "moj_evaluator.py") {
		return "MoJ visual jury", "", ""
	}
	if model == "" {
		model = strings.TrimSpace(task)
	}
	if model == "" {
		model = strings.TrimSpace(suite)
	}
	return model, precision, path
}

func assignmentRank(m map[string]any) int {
	if m == nil {
		return -1
	}
	switch fmt.Sprint(m["status"]) {
	case "running":
		return 3
	case "error":
		return 2
	case "stopped", "done":
		return 0
	default:
		return 1
	}
}

func loadRigStreams(dir string) map[int]map[string]any {
	out := map[int]map[string]any{}
	entries, err := os.ReadDir(dir)
	if err != nil {
		return out
	}
	for _, entry := range entries {
		name := entry.Name()
		if !strings.HasSuffix(name, ".json") {
			continue
		}
		if !strings.Contains(name, "stream") && name != "arcane_stream.json" {
			continue
		}
		raw, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			continue
		}
		var body map[string]any
		if json.Unmarshal(raw, &body) != nil || body == nil {
			continue
		}
		gpu := gpuFromBody(body)
		if gpu < 0 {
			gpu = gpuFromSocket(fmt.Sprint(body["socket"]))
		}
		if strings.Contains(name, "gpu3") {
			gpu = 3
		}
		if strings.Contains(name, "gpu1") || strings.Contains(name, "governor_train") {
			if gpu < 0 {
				gpu = 1
			}
		}
		if gpu < 0 {
			continue
		}
		lane := strings.ToLower(fmt.Sprint(body["lane"]))
		if lane == "" || lane == "<nil>" {
			if strings.Contains(name, "motion") {
				lane = "motion"
			} else if strings.Contains(name, "arcane") {
				lane = "arcane"
			} else if strings.Contains(name, "governor") || strings.Contains(name, "spectral") {
				lane = "spectral"
			} else {
				lane = "fashion"
			}
		}
		wall := strings.TrimSpace(fmt.Sprint(body["wall"]))
		if wall == "" || wall == "<nil>" {
			if lane == "fashion" {
				wall = "/gallery"
			} else if lane == "motion" {
				wall = "not on /gallery"
			}
		}
		asg := map[string]any{
			"file":      name,
			"lane":      lane,
			"label":     laneLabel(lane),
			"status":    body["status"],
			"prompt":    body["prompt"],
			"submitted": body["submitted"],
			"done":      body["done"],
			"running":   body["running"],
			"n":         body["n"],
			"wall":      wall,
			"id":        body["id"],
			"gallery":   body["gallery"],
		}
		if prev, ok := out[gpu]; ok && assignmentRank(prev) > assignmentRank(asg) {
			continue
		}
		out[gpu] = asg
	}
	return out
}

func gpuFromBody(body map[string]any) int {
	v, ok := body["gpu"]
	if !ok {
		return -1
	}
	switch t := v.(type) {
	case float64:
		return int(t)
	case json.Number:
		n, err := t.Int64()
		if err != nil {
			return -1
		}
		return int(n)
	case string:
		n, err := strconv.Atoi(strings.TrimSpace(t))
		if err != nil {
			return -1
		}
		return n
	default:
		return -1
	}
}

func gpuFromSocket(socket string) int {
	base := filepath.Base(socket)
	if strings.HasPrefix(base, "flux-gpu") && strings.HasSuffix(base, ".sock") {
		n, err := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(base, "flux-gpu"), ".sock"))
		if err == nil {
			return n
		}
	}
	return -1
}

func laneLabel(lane string) string {
	switch strings.ToLower(lane) {
	case "fashion":
		return "Fashion · beauty-on-beauty stills"
	case "motion":
		return "Motion atlas path · off gallery"
	case "arcane":
		return "Arcane Fortiche mine"
	case "spectral", "governor-train", "train":
		return "Governor · spectral training"
	default:
		if lane == "" || lane == "<nil>" {
			return "Unassigned FLUX worker"
		}
		return strings.ToUpper(lane[:1]) + lane[1:] + " generation"
	}
}

func describeGPU(g rigGPU) (performing, detail, brief, wall string) {
	asg := g.Assignment
	if asg != nil {
		performing = fmt.Sprint(asg["label"])
		brief = strings.TrimSpace(fmt.Sprint(asg["prompt"]))
		wall = strings.TrimSpace(fmt.Sprint(asg["wall"]))
		if wall == "<nil>" {
			wall = ""
		}
		if fmt.Sprint(asg["gallery"]) == "false" && wall == "" {
			wall = "not on /gallery"
		}
		status := fmt.Sprint(asg["status"])
		done, n := asg["done"], asg["n"]
		run := asg["running"]
		parts := []string{status}
		if run != nil && fmt.Sprint(run) != "<nil>" && fmt.Sprint(run) != "" {
			parts = append(parts, fmt.Sprintf("%v in flight", run))
		}
		if done != nil && fmt.Sprint(done) != "<nil>" {
			if n != nil && fmt.Sprint(n) != "<nil>" && fmt.Sprint(n) != "" {
				parts = append(parts, fmt.Sprintf("%v / %v this stream", done, n))
			} else {
				parts = append(parts, fmt.Sprintf("%v done", done))
			}
		}
		detail = strings.Join(parts, " · ")
	}
	if performing == "" || performing == "<nil>" {
		performing = performingFromProcesses(g)
	}
	if brief == "" || brief == "<nil>" {
		for _, t := range g.Tasks {
			if p := strings.TrimSpace(fmt.Sprint(t["prompt"])); p != "" && p != "<nil>" {
				brief = p
				break
			}
		}
	}
	if performing == "" {
		up, _ := false, false
		if g.Worker != nil {
			up, _ = g.Worker["up"].(bool)
		}
		if up {
			performing = "FLUX worker idle"
			detail = "model resident, no in-flight job"
		} else if len(g.Processes) == 0 {
			performing = "Empty seat"
		} else {
			performing = g.Processes[0].Suite
			detail = g.Processes[0].Task
		}
	}
	if len(brief) > 220 {
		brief = strings.TrimSpace(brief[:217]) + "…"
	}
	return performing, detail, brief, wall
}

func performingFromProcesses(g rigGPU) string {
	names := []string{}
	seen := map[string]bool{}
	for _, p := range g.Processes {
		label := p.Task
		if p.Suite == "Inference" && p.Task != "" {
			label = inferenceLabel(p.Task)
		} else if p.Suite != "" && p.Suite != "CUDA process" {
			label = p.Suite
			if p.Task != "" && p.Task != "image generation worker" {
				label = p.Suite + " · " + p.Task
			}
		}
		if label == "" || seen[label] {
			continue
		}
		seen[label] = true
		names = append(names, label)
	}
	if len(names) == 0 {
		return ""
	}
	return strings.Join(names, " + ")
}

func inferenceLabel(task string) string {
	switch strings.ToLower(strings.TrimSpace(task)) {
	case "governor", "governor-gemma":
		return "Governor Gemma inference"
	case "governor-qwen", "qwen38", "qwen":
		return "Governor Qwen inference"
	case "hive-research":
		return "Hive research inference"
	case "pixtral":
		return "Pixtral visual critic"
	case "drafter", "dflash", "mtp":
		return "Speculative drafter"
	case "jury":
		return "Jury inference"
	default:
		return "vLLM · " + task
	}
}

func rigCapacity(gpus []rigGPU) map[string]any {
	var best int
	var bestFree float64
	var totalFree float64
	for _, gpu := range gpus {
		totalFree += gpu.MemoryFree
		if gpu.MemoryFree > bestFree {
			best, bestFree = gpu.Index, gpu.MemoryFree
		}
	}
	mem := map[string]uint64{}
	if raw, err := os.ReadFile("/proc/meminfo"); err == nil {
		for _, line := range strings.Split(string(raw), "\n") {
			fields := strings.Fields(line)
			if len(fields) < 2 {
				continue
			}
			v, _ := strconv.ParseUint(fields[1], 10, 64)
			mem[strings.TrimSuffix(fields[0], ":")] = v * 1024
		}
	}
	var fs syscall.Statfs_t
	_ = syscall.Statfs("/", &fs)
	return map[string]any{"ram_total_bytes": mem["MemTotal"], "ram_available_bytes": mem["MemAvailable"], "disk_total_bytes": fs.Blocks * uint64(fs.Bsize), "disk_available_bytes": fs.Bavail * uint64(fs.Bsize), "vram_free_mib": totalFree, "best_gpu": best, "best_gpu_free_mib": bestFree}
}

func knotextPresent() bool {
	for _, root := range []string{"/models", "/home/ubuntu/models", "/opt/apps", "/home/ubuntu/CLIs"} {
		found := false
		_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
			if err != nil {
				return filepath.SkipDir
			}
			if strings.Contains(strings.ToLower(d.Name()), "knotext") {
				found = true
				return filepath.SkipAll
			}
			if d.IsDir() && path != root && strings.Count(strings.TrimPrefix(path, root), string(os.PathSeparator)) >= 3 {
				return filepath.SkipDir
			}
			return nil
		})
		if found {
			return true
		}
	}
	return false
}

func probeRigGPUs() ([]rigGPU, error) {
	out, err := exec.Command("nvidia-smi", "--query-gpu=index,uuid,name,utilization.gpu,memory.used,memory.total,memory.free,temperature.gpu,power.draw,power.limit", "--format=csv,noheader,nounits").Output()
	if err != nil {
		return nil, fmt.Errorf("nvidia-smi: %w", err)
	}
	rows, err := csv.NewReader(strings.NewReader(string(out))).ReadAll()
	if err != nil {
		return nil, err
	}
	gpus := make([]rigGPU, 0, len(rows))
	for _, row := range rows {
		if len(row) < 10 {
			continue
		}
		gpus = append(gpus, rigGPU{Index: atoi(row[0]), UUID: strings.TrimSpace(row[1]), Name: strings.TrimSpace(row[2]), Compute: atof(row[3]), MemoryUsed: atof(row[4]), MemoryTotal: atof(row[5]), MemoryFree: atof(row[6]), Temperature: atof(row[7]), Power: atof(row[8]), PowerLimit: atof(row[9]), Worker: map[string]any{"name": fmt.Sprintf("flux-gpu%d", atoi(row[0])), "up": false}, Processes: []rigProcess{}, Tasks: []map[string]any{}})
	}
	sort.Slice(gpus, func(i, j int) bool { return gpus[i].Index < gpus[j].Index })
	return gpus, nil
}

func attachRigProcesses(gpus []rigGPU) {
	byUUID := map[string]*rigGPU{}
	for i := range gpus {
		byUUID[gpus[i].UUID] = &gpus[i]
	}
	out, err := exec.Command("nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name,used_gpu_memory", "--format=csv,noheader,nounits").Output()
	if err != nil {
		return
	}
	rows, _ := csv.NewReader(strings.NewReader(string(out))).ReadAll()
	for _, row := range rows {
		if len(row) < 4 || byUUID[strings.TrimSpace(row[0])] == nil {
			continue
		}
		pid := atoi(row[1])
		command := procCommand(pid)
		g := byUUID[strings.TrimSpace(row[0])]
		suite, task := classifyRigProcess(command, strings.TrimSpace(row[2]), pid, g.Index)
		g.Processes = append(g.Processes, rigProcess{PID: pid, MemoryMiB: atof(row[3]), Name: filepath.Base(strings.TrimSpace(row[2])), Suite: suite, Task: task, Command: command})
	}
}

func classifyRigProcess(command, name string, pid, gpu int) (string, string) {
	lower := strings.ToLower(command)
	switch {
	case strings.Contains(lower, "gpu0_motion_stream.py"):
		return "Motion experiment", "atlas path, off gallery"
	case strings.Contains(lower, "arcane_atlas_stream.py"):
		return "Arcane mine", "Fortiche stills"
	case strings.Contains(lower, "protocol_r2_stream.py"):
		return "Beauty Protocol R2", "publish settled fashion frames"
	case strings.Contains(lower, "protocol_stream.py"):
		lane := argAfter(command, "--lane", "")
		if strings.Contains(lower, "fashion") || lane == "fashion" {
			return "Fashion streamer", "beauty-on-beauty stills"
		}
		if lane != "" {
			return "Protocol streamer", lane
		}
		return "Protocol streamer", "continuous generation"
	case strings.Contains(lower, "moj_evaluator.py"):
		out := strings.ToLower(procEnv(pid, "OUT_DIR") + " " + procEnv(pid, "FLUX_OUTPUT_DIR"))
		switch {
		case strings.Contains(out, "microgreens"):
			return "Microgreens jury", "visual evaluator"
		case strings.Contains(out, "arcane"):
			return "Arcane jury", "visual evaluator"
		case gpu == 3:
			return "Fashion jury", "visual evaluator"
		default:
			return "MoJ jury", "visual evaluator"
		}
	case strings.Contains(lower, "vllm"):
		return "Inference", argAfter(command, "--served-model-name", "vLLM model server")
	case strings.Contains(lower, "worker.py"):
		if strings.Contains(lower, "flux-gpu0") {
			return "FLUX renderer", "GPU 0 BF16 worker"
		}
		if strings.Contains(lower, "flux-gpu3") {
			return "FLUX renderer", "GPU 3 FP8 worker"
		}
		return "FLUX renderer", "image generation worker"
	default:
		return "CUDA process", name
	}
}

func compactRigTask(job map[string]any) map[string]any {
	kind := job["kind"]
	if kind == nil || fmt.Sprint(kind) == "" {
		kind = "still"
	}
	return map[string]any{
		"id":          job["id"],
		"status":      job["status"],
		"phase":       job["phase"],
		"kind":        kind,
		"step":        job["step"],
		"total_steps": job["total_steps"],
		"prompt":      job["prompt"],
		"filename":    job["filename"],
		"model":       job["model_family"],
		"atlas_done":  job["atlas_done"],
		"atlas_total": job["atlas_total"],
	}
}

func procEnv(pid int, key string) string {
	raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/environ", pid))
	if err != nil {
		return ""
	}
	prefix := key + "="
	for _, part := range strings.Split(string(raw), "\x00") {
		if strings.HasPrefix(part, prefix) {
			return strings.TrimPrefix(part, prefix)
		}
	}
	return ""
}

func procCommand(pid int) string {
	b, err := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", pid))
	if err != nil {
		return ""
	}
	command := strings.TrimSpace(strings.ReplaceAll(string(b), "\x00", " "))
	if strings.Contains(command, "VLLM::EngineCore") {
		if status, err := os.ReadFile(fmt.Sprintf("/proc/%d/status", pid)); err == nil {
			for _, line := range strings.Split(string(status), "\n") {
				if strings.HasPrefix(line, "PPid:") {
					if parent := atoi(strings.TrimPrefix(line, "PPid:")); parent > 1 {
						if resolved := procCommand(parent); resolved != "" {
							return resolved
						}
					}
				}
			}
		}
	}
	return command
}
func argAfter(command, flag, fallback string) string {
	fields := strings.Fields(command)
	for i := range fields {
		if fields[i] == flag && i+1 < len(fields) {
			return fields[i+1]
		}
	}
	return fallback
}
func atoi(v string) int     { n, _ := strconv.Atoi(strings.TrimSpace(v)); return n }
func atof(v string) float64 { n, _ := strconv.ParseFloat(strings.TrimSpace(v), 64); return n }
func hostname() string {
	h, err := os.Hostname()
	if err != nil {
		return "rig"
	}
	return h
}
