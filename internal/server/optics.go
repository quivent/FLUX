package server

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"local/flux/internal/jury"
)

// GET /api/optics — who is online, who is judging, what the loop is.
// Fingerprints live /v1/models. Does not trust remembered tenant labels.

func (s Server) opticsAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	writeJSON(w, http.StatusOK, s.collectOptics())
}

func (s Server) collectOptics() map[string]any {
	gpus := probeGPUs()
	sockets := probeSockets()
	hats := hatsFromSockets(sockets)
	fashionJury, _ := jury.GetConfig(s.cfg.OutputDir)
	arcaneJury, _ := jury.GetConfig(s.arcaneOutputDir())
	fashionStream := readProtocolStreamStateLane(s.cfg.Root, "fashion")
	arcaneStream := map[string]any{}
	if raw, err := os.ReadFile(filepath.Join(s.cfg.Root, ".fluxd", "arcane_stream.json")); err == nil {
		_ = json.Unmarshal(raw, &arcaneStream)
	}
	return map[string]any{
		"ok":      true,
		"machine": machineLine(gpus),
		"gpus":    gpus,
		"sockets": sockets,
		"hats":    hats,
		"flux":    s.fluxOccupants(),
		"jury": map[string]any{
			"arcane":  juryOptics(arcaneJury),
			"fashion": juryOptics(fashionJury),
		},
		"loop": map[string]any{
			"arcane":  loopOptics(arcaneStream, arcaneEvalPath(arcaneStream)),
			"fashion": loopOptics(fashionStream, fashionEvalPath(fashionStream)),
		},
		"drift": driftNotes(sockets, hats),
	}
}

func probeGPUs() []map[string]any {
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "nvidia-smi",
		"--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
		"--format=csv,noheader,nounits",
	).Output()
	if err != nil {
		return nil
	}
	gpus := make([]map[string]any, 0)
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if gpu, ok := parseTelemetryLine(line); ok {
			gpus = append(gpus, gpu)
		}
	}
	return gpus
}

func machineLine(gpus []map[string]any) string {
	if len(gpus) == 0 {
		return "nvidia-smi unavailable"
	}
	name, _ := gpus[0]["name"].(string)
	name = strings.TrimSpace(name)
	if name == "" {
		name = "GPU"
	}
	return strings.TrimSpace(strings.Join([]string{
		itoa(len(gpus)) + "× " + shortGPUName(name),
	}, " "))
}

func shortGPUName(name string) string {
	n := strings.ReplaceAll(name, "NVIDIA ", "")
	n = strings.ReplaceAll(n, " Server Edition", "")
	return n
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var b [8]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

type opticSocket struct {
	Port  int      `json:"port"`
	Live  bool     `json:"live"`
	IDs   []string `json:"ids"`
	Kind  string   `json:"kind"`
	Role  string   `json:"role"`
	Model string   `json:"model,omitempty"`
}

func probeSockets() []opticSocket {
	spec := []struct {
		port int
		role string
	}{
		{8000, "research governor (law & intent)"},
		{8001, "FLUX jury — not research governor"},
		{8002, "Qwen volume (forage + packing merge)"},
		{8003, "Gemma 12B drafter"},
		{8004, "Pixtral critic"},
	}
	out := make([]opticSocket, 0, len(spec))
	for _, sp := range spec {
		addr := "127.0.0.1:" + itoa(sp.port)
		ids := listVLLMModelsTimeout(addr, 2*time.Second)
		live := len(ids) > 0 || tcpAlive(addr, 250*time.Millisecond)
		kind := classifyHat(ids)
		if live && kind == "down" {
			kind = "unknown"
		}
		model := ""
		if len(ids) > 0 {
			model = ids[0]
		}
		out = append(out, opticSocket{
			Port: sp.port, Live: live, IDs: ids, Kind: kind, Role: sp.role, Model: model,
		})
	}
	return out
}

func listVLLMModelsTimeout(addr string, d time.Duration) []string {
	client := &http.Client{Timeout: d}
	resp, err := client.Get("http://" + addr + "/v1/models")
	if err != nil {
		return nil
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil
	}
	var payload struct {
		Data []struct {
			ID string `json:"id"`
		} `json:"data"`
	}
	if json.NewDecoder(resp.Body).Decode(&payload) != nil {
		return nil
	}
	out := make([]string, 0, len(payload.Data))
	seen := map[string]bool{}
	for _, m := range payload.Data {
		id := strings.TrimSpace(m.ID)
		if id == "" || seen[id] {
			continue
		}
		seen[id] = true
		out = append(out, id)
	}
	return out
}

func classifyHat(ids []string) string {
	if len(ids) == 0 {
		return "down"
	}
	set := map[string]bool{}
	lower := []string{}
	for _, id := range ids {
		set[id] = true
		lower = append(lower, strings.ToLower(id))
	}
	joined := strings.Join(lower, " ")
	switch {
	case set["gemma-governor"]:
		return "governor_gemma"
	case set["hive-research"] || set["qwen38"] || set["qwen-research"] || set["qwen3.5-27b"]:
		return "qwen"
	case set["jury"] || set["gemma-jury"]:
		return "flux_jury"
	case strings.Contains(joined, "pixtral"):
		return "pixtral"
	case strings.Contains(joined, "drafter") || strings.Contains(joined, "12b"):
		return "drafter"
	case set["governor"] && !set["qwen38"] && !set["hive-research"]:
		return "governor_gemma"
	default:
		return "unknown"
	}
}

func hatsFromSockets(socks []opticSocket) map[string]any {
	hats := map[string]any{}
	pick := func(kind, key string) {
		for _, s := range socks {
			if s.Kind == kind && s.Live {
				hats[key] = map[string]any{"port": s.Port, "model": s.Model, "kind": s.Kind, "ids": s.IDs}
				return
			}
		}
		hats[key] = map[string]any{"port": nil, "kind": "missing"}
	}
	pick("governor_gemma", "governor")
	pick("qwen", "qwen")
	pick("flux_jury", "jury")
	pick("pixtral", "pixtral")
	pick("drafter", "drafter")
	return hats
}

func (s Server) fluxOccupants() []map[string]any {
	out := []map[string]any{}
	if fleet := s.fleetStatusPayload(); fleet != nil {
		if workers, ok := fleet["workers"].([]map[string]any); ok {
			for _, w := range workers {
				out = append(out, w)
			}
		}
	}
	root := s.cfg.Root
	for _, gpu := range []int{0, 3} {
		sock := filepath.Join(root, ".fluxd", "flux-gpu"+itoa(gpu)+".sock")
		_, err := os.Stat(sock)
		found := false
		for _, w := range out {
			if g, ok := w["gpu"].(int); ok && g == gpu {
				found = true
				break
			}
		}
		if !found {
			out = append(out, map[string]any{
				"gpu": gpu, "name": "flux-gpu" + itoa(gpu),
				"up": err == nil, "socket": sock,
			})
		}
	}
	return out
}

func juryOptics(cfg jury.JuryConfig) map[string]any {
	return map[string]any{
		"mode":       cfg.Mode,
		"order":      cfg.Order,
		"weights":    cfg.Weights,
		"strictness": cfg.Strictness,
		"min_judges": cfg.MinJudges,
	}
}

func loopOptics(stream map[string]any, path []string) map[string]any {
	if stream == nil {
		stream = map[string]any{}
	}
	return map[string]any{
		"id":         stream["id"],
		"running":    stream["running"],
		"done":       stream["done"],
		"evaluated":  stream["evaluated"],
		"error":      stream["error"],
		"n":          stream["n"],
		"eval_path":  path,
		"prompt":     clipPrompt(stream["prompt"]),
		"unplugged":  streamErrorUnplugged(stream["error"]),
	}
}

func clipPrompt(v any) string {
	s, _ := v.(string)
	s = strings.TrimSpace(s)
	if len(s) > 140 {
		return s[:137] + "…"
	}
	return s
}

func streamErrorUnplugged(v any) bool {
	s, _ := v.(string)
	return strings.Contains(strings.ToLower(s), "unplugged")
}

func fashionEvalPath(stream map[string]any) []string {
	if p := stringSlice(stream["eval_path"]); len(p) > 0 {
		return p
	}
	return []string{"generate", "uniqueness", "sensory_gates", "witness", "pixtral", "governor", "composite"}
}

func arcaneEvalPath(stream map[string]any) []string {
	if p := stringSlice(stream["eval_path"]); len(p) > 0 {
		return p
	}
	return []string{"generate", "uniqueness", "sensory_gates", "forliche", "witness", "pixtral", "governor", "composite"}
}

func stringSlice(v any) []string {
	switch t := v.(type) {
	case []string:
		return t
	case []any:
		out := make([]string, 0, len(t))
		for _, x := range t {
			if s, ok := x.(string); ok && s != "" {
				out = append(out, s)
			}
		}
		return out
	default:
		return nil
	}
}

func driftNotes(socks []opticSocket, hats map[string]any) []string {
	var notes []string
	for _, s := range socks {
		if s.Port == 8002 && s.Kind == "qwen" {
			notes = append(notes, ":8002 is Qwen hive-research — not Pixtral. Pixtral if present is :8004.")
			break
		}
	}
	for _, s := range socks {
		if s.Port == 8001 && s.Kind == "flux_jury" {
			notes = append(notes, ":8001 jury is the FLUX beauty juror — not the research governor.")
			break
		}
	}
	if g, ok := hats["governor"].(map[string]any); ok {
		if g["kind"] == "missing" {
			notes = append(notes, "no Gemma governor fingerprint on :8000–:8004")
		}
	}
	return notes
}
