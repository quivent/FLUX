package server

import (
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
	"syscall"
	"time"
)

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
	Worker      map[string]any   `json:"worker"`
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

func (s Server) rigStatusAPI(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	gpus, err := probeRigGPUs()
	if err != nil {
		w.WriteHeader(http.StatusServiceUnavailable)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": false, "error": err.Error(), "updated_at": time.Now().UTC()})
		return
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
	_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "host": hostname(), "updated_at": time.Now().UTC(), "gpus": gpus, "capacity": rigCapacity(gpus), "inventory": map[string]any{"knotext": knotextPresent()}})
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
		suite, task := classifyRigProcess(command, strings.TrimSpace(row[2]))
		g := byUUID[strings.TrimSpace(row[0])]
		g.Processes = append(g.Processes, rigProcess{PID: pid, MemoryMiB: atof(row[3]), Name: filepath.Base(strings.TrimSpace(row[2])), Suite: suite, Task: task, Command: command})
	}
}

func classifyRigProcess(command, name string) (string, string) {
	lower := strings.ToLower(command)
	switch {
	case strings.Contains(lower, "protocol_r2_stream.py"):
		return "Beauty Protocol R2", "continuous protocol stream"
	case strings.Contains(lower, "protocol_stream.py"):
		return "Beauty Protocol", argAfter(command, "--lane", "continuous generation")
	case strings.Contains(lower, "moj_evaluator.py"):
		return "Ministry of Judgment", "visual jury evaluator"
	case strings.Contains(lower, "vllm serve"):
		return "Inference", argAfter(command, "--served-model-name", "vLLM model server")
	case strings.Contains(lower, "worker.py"):
		return "FLUX Renderer", "image generation worker"
	default:
		return "CUDA process", name
	}
}

func compactRigTask(job map[string]any) map[string]any {
	return map[string]any{"id": job["id"], "status": job["status"], "phase": job["phase"], "step": job["step"], "total_steps": job["total_steps"], "prompt": job["prompt"], "filename": job["filename"], "model": job["model_family"]}
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
