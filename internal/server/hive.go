package server

import (
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func (s Server) hivePage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	if r.URL.Path == "/hive/" {
		http.Redirect(w, r, "/hive", http.StatusPermanentRedirect)
		return
	}
	if strings.TrimSuffix(r.URL.Path, "/") != "/hive" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "hive.html"))
}

// GET /api/hive — charters, live dual-seat discourse, and gated outcomes.
func (s Server) hiveAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	root := hiveRoot()
	research := filepath.Join(root, ".swarm", "research", "dual-seat-drive")
	intel := filepath.Join(root, ".swarm", "intelligence")
	tea := filepath.Join(s.cfg.Root, "apps", "tea", "public")
	logPath := strings.TrimSpace(os.Getenv("DUAL_SEAT_LOG"))
	if logPath == "" {
		logPath = "/home/ubuntu/hive-research/logs/dual-seat-drive.jsonl"
	}
	// The public Hive display and this Tea surface must read the same swarm.
	// The old dual-seat drive PID is a separate historical experiment and can
	// be absent even while hive_tick.py is actively foraging.
	actualState, actualAlive := liveHiveState(root)
	control, _ := readJSONFile(filepath.Join(root, ".swarm", "run", "hive-tick.json")).(map[string]any)
	st, _ := readJSONFile(filepath.Join(research, "state.json")).(map[string]any)
	if st == nil {
		st = map[string]any{}
	}
	phase, _ := st["phase"].(string)
	kind := kindFromPhase(phase)
	wantTick := strings.TrimSpace(r.URL.Query().Get("tick"))
	if wantTick != "" {
		kind = kindFromPhase(wantTick)
	}
	if k := strings.TrimSpace(r.URL.Query().Get("kind")); k != "" {
		kind = kindFromPhase(k)
	}

	cards := loadCharterQueue(filepath.Join(root, ".swarm", "charters"))
	active := make([]charterCard, 0, 9)
	for _, c := range cards {
		if c.State == "active" {
			active = append(active, c)
		}
	}

	activeCharter := ""
	if c, ok := control["charter"].(map[string]any); ok {
		activeCharter, _ = c["id"].(string)
		if activeCharter == "" {
			activeCharter, _ = c["title"].(string)
		}
	}
	question := ""
	if objective, ok := control["objective"].(map[string]any); ok {
		question, _ = objective["summary"].(string)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":           true,
		"exclusive":    true,
		"gpu":          1,
		"question":     question,
		"alive":        actualAlive,
		"pid":          "hive_tick.py",
		"swarm":        actualState,
		"compute":      hiveComputeTelemetry(),
		"state":        actualState,
		"legacy_state": st,
		"hats":         readJSONFile(filepath.Join(research, "hat-bind.json")),
		"kind":         kind,
		"charter":      activeCharter,
		"charters":     active,
		"discourse":    loadHiveDiscourse(research, intel, kind, wantTick),
		"outcomes":     loadHiveOutcomes(research, intel, tea, st),
		"recent":       tailDriveLog(logPath, 400),
		"ticks":        listTickFiles(intel, 16),
	})
}

func liveHiveState(root string) (map[string]any, bool) {
	state, _ := readJSONFile(filepath.Join(root, ".swarm", "run", "hive-tick-state.json")).(map[string]any)
	if state == nil {
		state = map[string]any{}
	}
	state["tick"] = state["tick_n"]
	phase := "foraging"
	if parked, _ := state["parked"].(bool); parked {
		phase = "parked"
	}
	state["phase"] = phase
	out, err := exec.Command("pgrep", "-f", "hive_tick.py").Output()
	alive := err == nil && strings.TrimSpace(string(out)) != ""
	return state, alive
}

func hiveComputeTelemetry() map[string]any {
	out, err := exec.Command("nvidia-smi",
		"--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
		"--format=csv,noheader,nounits",
	).Output()
	if err != nil {
		return map[string]any{"available": false, "gpus": []map[string]any{}}
	}
	gpus := make([]map[string]any, 0)
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if gpu, ok := parseTelemetryLine(line); ok {
			gpus = append(gpus, gpu)
		}
	}
	return map[string]any{"available": len(gpus) > 0, "gpus": gpus}
}

func charterForKind(kind string) string {
	switch kind {
	case "residual":
		return "residual-capture-gpu1"
	case "vram-shard":
		return "kvx-decode-injection"
	default:
		return "spectral-cartridge-manufacture"
	}
}

func loadHiveDiscourse(research, intel, kind, want string) map[string]any {
	ext := filepath.Join(research, "externalize")
	tick := loadDiscourse(research, intel, want)
	forage := compactForage(readJSONFile(filepath.Join(ext, kind+"-forage.json")))
	merge := readJSONFile(filepath.Join(ext, kind+"-merge.json"))
	verdict := readJSONFile(filepath.Join(ext, kind+"-verdict.json"))
	return map[string]any{
		"kind":     kind,
		"charter":  charterForKind(kind),
		"tick":     tick,
		"qwen":     forage,
		"merge":    merge,
		"governor": verdict,
		"seats": map[string]any{
			"qwen":     "GPU 2 · hive-research :8002 · forage then pack",
			"governor": "GPU 1 · governor :8800 · gate, never forage",
		},
	}
}

func compactForage(v any) []map[string]any {
	var rows []any
	switch t := v.(type) {
	case []any:
		rows = t
	case map[string]any:
		if inner, ok := t["ledger"].([]any); ok {
			rows = inner
		} else if inner, ok := t["keep"].([]any); ok {
			rows = inner
		}
	}
	out := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		m, _ := row.(map[string]any)
		if m == nil {
			continue
		}
		item := map[string]any{
			"id":         m["id"],
			"candidate":  clipAny(m["candidate"], 280),
			"retires":    m["retires"],
			"why_fluent": clipAny(firstNonEmpty(asString(m["why_fluent"]), asString(m["why"])), 320),
			"why_not":    clipAny(m["why_not"], 200),
			"confidence": m["confidence"],
			"pointer":    firstNonEmpty(asString(m["pointer_hint"]), asString(m["pointer"])),
		}
		out = append(out, item)
		if len(out) >= 12 {
			break
		}
	}
	return out
}

func loadHiveOutcomes(research, intel, tea string, st map[string]any) map[string]any {
	last, _ := st["last_result"].(map[string]any)
	var verdict any
	if last != nil {
		verdict = last["verdict"]
	}
	shards, _ := readJSONFile(filepath.Join(tea, "train-shards.json")).(map[string]any)
	return map[string]any{
		"last":    last,
		"verdict": verdict,
		"verdicts": map[string]any{
			"spectral":   readJSONFile(filepath.Join(research, "externalize", "spectral-verdict.json")),
			"residual":   readJSONFile(filepath.Join(research, "externalize", "residual-verdict.json")),
			"vram-shard": readJSONFile(filepath.Join(research, "externalize", "vram-shard-verdict.json")),
		},
		"shards": shards,
		"ticks":  listTickFiles(intel, 16),
	}
}

func asString(v any) string {
	s, _ := v.(string)
	return s
}

func clipAny(v any, n int) any {
	switch t := v.(type) {
	case string:
		return clip(t, n)
	default:
		return v
	}
}
