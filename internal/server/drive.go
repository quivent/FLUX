package server

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// GET /api/drive — dual-seat drive as Governor training loop (state, hats, last ticks, calibration).

func hiveRoot() string {
	if v := strings.TrimSpace(os.Getenv("HIVE_ROOT")); v != "" {
		return v
	}
	return "/home/ubuntu/hive"
}

func (s Server) driveAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	root := hiveRoot()
	research := filepath.Join(root, ".swarm", "research", "dual-seat-drive")
	logPath := os.Getenv("DUAL_SEAT_LOG")
	if logPath == "" {
		logPath = "/home/ubuntu/hive-research/logs/dual-seat-drive.jsonl"
	}
	pidPath := "/home/ubuntu/hive-research/run/dual-seat-drive.pid"
	pid := strings.TrimSpace(readText(pidPath))
	alive := pid != "" && processAlive(pid)

	intel := filepath.Join(root, ".swarm", "intelligence")
	tea := filepath.Join(s.cfg.Root, "apps", "tea", "public")
	wantTick := strings.TrimSpace(r.URL.Query().Get("tick"))
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":          true,
		"purpose":     "Governor trains himself toward 100% reliability, expansion of memory, and performance of reasoning. Qwen is reason. Boards, not teachers.",
		"alive":       alive,
		"pid":         pid,
		"gateway":     "http://127.0.0.1:8800",
		"engine":      "http://127.0.0.1:8000",
		"gpu":         1,
		"state":       readJSONFile(filepath.Join(research, "state.json")),
		"hats":        readJSONFile(filepath.Join(research, "hat-bind.json")),
		"calibration": summarizeCal(filepath.Join(research, "calibrate", "governor_scores.json")),
		"recent":      tailDriveLog(logPath, 400),
		"ticks":       listTickFiles(intel, 12),
		"discourse":   loadDiscourse(research, intel, wantTick),
		"convergence": loadConvergence(research),
		"training":    loadTraining(research, intel, tea),
	})
}

func processAlive(pid string) bool {
	_, err := os.Stat("/proc/" + pid)
	return err == nil
}

func readText(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func readJSONFile(path string) any {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var v any
	if json.Unmarshal(b, &v) != nil {
		return nil
	}
	return v
}

func summarizeCal(path string) map[string]any {
	raw := readJSONFile(path)
	m, _ := raw.(map[string]any)
	if m == nil {
		return map[string]any{"present": false}
	}
	scores, _ := m["scores"].([]any)
	correct := 0
	n := len(scores)
	for _, s := range scores {
		row, _ := s.(map[string]any)
		if row != nil && row["correct"] == true {
			correct++
		}
	}
	return map[string]any{"present": true, "correct": correct, "n": n, "scores": scores}
}

func tailDriveLog(path string, n int) []any {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 256*1024), 2*1024*1024)
	for sc.Scan() {
		t := strings.TrimSpace(sc.Text())
		if t != "" {
			lines = append(lines, t)
		}
	}
	if n > 0 && len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	out := make([]any, 0, 16)
	want := map[string]bool{
		"tick_ok": true, "tick_fail": true, "hat_bind": true, "hat_drift": true,
		"chat_unbound": true, "governor_down": true, "drive_start": true,
		"tick_begin": true, "fanout_done": true,
	}
	for _, line := range lines {
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil {
			continue
		}
		ev, _ := rec["event"].(string)
		if !want[ev] {
			continue
		}
		out = append(out, rec)
	}
	if len(out) > 12 {
		out = out[len(out)-12:]
	}
	return out
}

func listTickFiles(dir string, n int) []map[string]any {
	ents, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var names []string
	for _, e := range ents {
		if e.IsDir() {
			continue
		}
		nm := e.Name()
		if strings.HasPrefix(nm, "tick-") && strings.HasSuffix(nm, ".md") {
			names = append(names, nm)
		}
	}
	sort.Strings(names)
	if len(names) > n {
		names = names[len(names)-n:]
	}
	out := make([]map[string]any, 0, len(names))
	for i := len(names) - 1; i >= 0; i-- {
		out = append(out, map[string]any{"file": names[i], "id": strings.TrimSuffix(strings.TrimPrefix(names[i], "tick-"), ".md")})
	}
	return out
}

func loadDiscourse(research, intel, want string) map[string]any {
	var path string
	if want != "" {
		base := want
		if !strings.HasPrefix(base, "tick-") {
			base = "tick-" + want
		}
		if !strings.HasSuffix(base, ".md") {
			base += ".md"
		}
		cand := filepath.Join(intel, filepath.Base(base))
		if _, err := os.Stat(cand); err == nil {
			path = cand
		}
	}
	if path == "" {
		ticks := listTickFiles(intel, 1)
		if len(ticks) > 0 {
			path = filepath.Join(intel, ticks[0]["file"].(string))
		}
	}
	body := ""
	if path != "" {
		b, err := os.ReadFile(path)
		if err == nil {
			body = string(b)
			if len(body) > 14000 {
				body = body[:14000] + "\n…truncated…"
			}
		}
	}
	return map[string]any{
		"path":     path,
		"body":     body,
		"merge":    readJSONFile(filepath.Join(research, "distill", "merge.json")),
		"gate":     readJSONFile(filepath.Join(research, "distill", "gate.json")),
		"qwen_A":   readJSONFile(filepath.Join(research, "dialectic", "A-merge.json")),
		"governor": readJSONFile(filepath.Join(research, "dialectic", "B-governor.json")),
		"delta":    readJSONFile(filepath.Join(research, "dialectic", "delta.json")),
	}
}

func loadConvergence(research string) map[string]any {
	st, _ := readJSONFile(filepath.Join(research, "state.json")).(map[string]any)
	var last map[string]any
	if st != nil {
		last, _ = st["last_result"].(map[string]any)
	}
	gate, _ := readJSONFile(filepath.Join(research, "distill", "gate.json")).(map[string]any)
	delta, _ := readJSONFile(filepath.Join(research, "dialectic", "delta.json")).(map[string]any)
	cal := summarizeCal(filepath.Join(research, "calibrate", "governor_scores.json"))
	return map[string]any{
		"ticks_ok":    st["ticks_ok"],
		"ticks_fail":  st["ticks_fail"],
		"phase":       st["phase"],
		"tick":        st["tick"],
		"gate":        gate,
		"delta":       delta,
		"last":        last,
		"calibration": cal,
	}
}

func loadTraining(research, intel, tea string) map[string]any {
	protocol, _ := readJSONFile(filepath.Join(tea, "train-protocol.json")).(map[string]any)
	catalog, _ := readJSONFile(filepath.Join(tea, "train-curriculum.json")).(map[string]any)
	st, _ := readJSONFile(filepath.Join(research, "state.json")).(map[string]any)
	if st == nil {
		st = map[string]any{}
	}
	hats, _ := readJSONFile(filepath.Join(research, "hat-bind.json")).(map[string]any)
	cal := summarizeCal(filepath.Join(research, "calibrate", "governor_scores.json"))
	gate, _ := readJSONFile(filepath.Join(research, "distill", "gate.json")).(map[string]any)
	govDial, _ := readJSONFile(filepath.Join(research, "dialectic", "B-governor.json")).(map[string]any)
	delta, _ := readJSONFile(filepath.Join(research, "dialectic", "delta.json")).(map[string]any)
	evolveDoc := readJSONFile(filepath.Join(research, "evolve", "mechanism.json"))
	probesDoc := readJSONFile(filepath.Join(research, "evolve", "probes.json"))

	var lessons []any
	if catalog != nil {
		lessons, _ = catalog["lessons"].([]any)
	}
	extraction := make([]map[string]any, 0, 6)
	operating := 0
	for _, l := range lessons {
		m, _ := l.(map[string]any)
		if m == nil {
			continue
		}
		n := asInt(m["n"])
		if n >= 19 {
			extraction = append(extraction, map[string]any{"n": n, "id": m["id"], "title": m["title"]})
		} else {
			operating++
		}
	}

	phase, _ := st["phase"].(string)
	kind := kindFromPhase(phase)
	probes := probesFor(kind, protocol, probesDoc)
	last, _ := st["last_result"].(map[string]any)
	var lastVerdict map[string]any
	if last != nil {
		lastVerdict, _ = last["verdict"].(map[string]any)
	}
	fluency := map[string]any{}
	if lastVerdict != nil {
		if f, ok := lastVerdict["fluency"].(map[string]any); ok {
			fluency = f
		}
	}

	gHat, _ := hats["governor"].(map[string]any)
	qHat, _ := hats["qwen"].(map[string]any)
	if gHat == nil {
		gHat = map[string]any{}
	}
	if qHat == nil {
		qHat = map[string]any{}
	}

	question := "Move Gemma/Qwen out of weights and text context into spectral vectors, residual patterns, and VRAM shards, then reason by pointer-token."
	notSGD := "SGD on the serving Gemma. Prompt stuffing. Stealing GPU 0 or 3."
	if protocol != nil {
		if q, ok := protocol["question"].(string); ok && q != "" {
			question = q
		}
		if n, ok := protocol["not"].(string); ok && n != "" {
			notSGD = n
		}
	}

	verdicts := map[string]any{
		"spectral":   readJSONFile(filepath.Join(research, "externalize", "spectral-verdict.json")),
		"residual":   readJSONFile(filepath.Join(research, "externalize", "residual-verdict.json")),
		"vram-shard": readJSONFile(filepath.Join(research, "externalize", "vram-shard-verdict.json")),
	}

	return map[string]any{
		"curriculum": map[string]any{
			"id":            catalogString(catalog, "id", "governor-core-v1"),
			"title":         catalogString(catalog, "title", "Governor self-training"),
			"question":      question,
			"not":           notSGD,
			"lessons":       len(lessons),
			"operating":     operating,
			"extraction":    extraction,
			"substrates":    protocolField(protocol, "substrates"),
			"current_phase": phase,
			"current_kind":  kind,
			"tick":          st["tick"],
			"probes":        probes,
		},
		"means": map[string]any{
			"method":     "He trains himself. Boards, not teachers. Qwen is reason. Focus: 100% reliability, expansion of memory, performance of reasoning. Not SGD.",
			"beat":       protocolField(protocol, "beat"),
			"seats":      protocolField(protocol, "seats"),
			"invariants": protocolField(protocol, "invariants"),
			"governor":   map[string]any{"gpu": 1, "port": gHat["port"], "model": gHat["model"], "kind": gHat["kind"], "n": 1},
			"qwen":       map[string]any{"gpu": 2, "port": qHat["port"], "model": qHat["model"], "kind": qHat["kind"], "n": 8},
			"forage":     "qwen-wide n=8 on GPU 2 :8002",
			"merge":      "qwen-long packing on the same engine — not a second mind",
			"gate":       "Governor n=1 on GPU 1 gateway :8800",
		},
		"rating": map[string]any{
			"rule":         "Fluency = pointer_ok AND residual_match AND no_prose_restatement. Every accept names retired context bytes.",
			"fluency":      fluency,
			"last_verdict": lastVerdict,
			"calibration":  cal,
			"issuance":     gate,
			"dialectic":    map[string]any{"governor": govDial, "delta": delta},
			"held_out":     "A cartridge that only matches training wording is not fluent. English recap of a shard is failure.",
		},
		"cycles":    loadCycleFeedback(intel, 12),
		"verdicts":  verdicts,
		"evolution": loadEvolution(intel, research, lastVerdict, evolveDoc, probesDoc),
		"history":   tailJSONL(filepath.Join(research, "evolve", "history.jsonl"), 12),
	}
}

func catalogString(cat map[string]any, key, fallback string) string {
	if cat != nil {
		if s, ok := cat[key].(string); ok && s != "" {
			return s
		}
	}
	return fallback
}

func protocolField(p map[string]any, key string) any {
	if p == nil {
		return nil
	}
	return p[key]
}

func kindFromPhase(phase string) string {
	switch {
	case strings.Contains(phase, "residual"):
		return "residual"
	case strings.Contains(phase, "vram"):
		return "vram-shard"
	default:
		return "spectral"
	}
}

func probesFor(kind string, protocol map[string]any, probesDoc any) []map[string]any {
	if m, ok := probesDoc.(map[string]any); ok {
		block, _ := m["probes"].(map[string]any)
		if block == nil {
			block = m
		}
		if rows, ok := block[kind].([]any); ok && len(rows) > 0 {
			out := make([]map[string]any, 0, len(rows))
			for _, r := range rows {
				if rm, ok := r.(map[string]any); ok {
					out = append(out, rm)
				}
			}
			if len(out) > 0 {
				return out
			}
		}
	}
	if protocol != nil {
		if pm, ok := protocol["probes"].(map[string]any); ok {
			if rows, ok := pm[kind].([]any); ok {
				out := make([]map[string]any, 0, len(rows))
				for _, r := range rows {
					if rm, ok := r.(map[string]any); ok {
						out = append(out, rm)
					}
				}
				return out
			}
		}
	}
	return nil
}

func loadCycleFeedback(intel string, n int) []map[string]any {
	ticks := listTickFiles(intel, n)
	out := make([]map[string]any, 0, len(ticks))
	for _, t := range ticks {
		file, _ := t["file"].(string)
		id, _ := t["id"].(string)
		body := readText(filepath.Join(intel, file))
		phase := id
		if i := strings.Index(id, "-"); i >= 0 {
			phase = id[i+1:]
		}
		row := map[string]any{
			"id":    id,
			"phase": phase,
			"file":  file,
		}
		if ts := extractTickTS(body); ts != "" {
			row["ts"] = ts
		}
		if v := extractJSONAfter(body, "governor verdict"); v != nil {
			row["accept"] = v["accept"]
			row["rework"] = v["rework"]
			row["reject"] = v["reject"]
			row["reason"] = v["reason"]
			row["fluency"] = v["fluency"]
			row["retire"] = v["retire"]
			row["next_pointer"] = v["next_pointer"]
			row["kind"] = v["kind"]
			row["voice"] = v["reason"]
		} else if v := extractJSONAfter(body, "gate"); v != nil {
			row["voice"] = v["reason"]
			row["decision"] = v["decision"]
		} else {
			row["excerpt"] = firstNonEmptyLines(body, 8)
		}
		out = append(out, row)
	}
	return out
}

func extractTickTS(body string) string {
	for _, line := range strings.Split(body, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "- ts:") {
			return strings.TrimSpace(strings.TrimPrefix(line, "- ts:"))
		}
	}
	return ""
}

func extractJSONAfter(body, heading string) map[string]any {
	lower := strings.ToLower(body)
	h := strings.ToLower(heading)
	i := strings.Index(lower, h)
	if i < 0 {
		return nil
	}
	rest := body[i:]
	start := strings.Index(rest, "{")
	if start < 0 {
		return nil
	}
	depth := 0
	for j := start; j < len(rest); j++ {
		switch rest[j] {
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				var v map[string]any
				if json.Unmarshal([]byte(rest[start:j+1]), &v) == nil {
					return v
				}
				return nil
			}
		}
	}
	return nil
}

func firstNonEmptyLines(body string, n int) string {
	var b strings.Builder
	c := 0
	for _, line := range strings.Split(body, "\n") {
		t := strings.TrimSpace(line)
		if t == "" || strings.HasPrefix(t, "#") || strings.HasPrefix(t, "```") {
			continue
		}
		if b.Len() > 0 {
			b.WriteByte('\n')
		}
		b.WriteString(t)
		c++
		if c >= n {
			break
		}
	}
	s := b.String()
	if len(s) > 900 {
		return s[:900] + "…"
	}
	return s
}

func loadEvolution(intel, research string, lastVerdict map[string]any, evolveDoc, probesDoc any) map[string]any {
	type bucket struct {
		first, last, n int
		phases         map[string]int
	}
	five := bucket{first: 1 << 30, phases: map[string]int{}}
	ext := bucket{first: 1 << 30, phases: map[string]int{}}
	ents, _ := os.ReadDir(intel)
	for _, e := range ents {
		nm := e.Name()
		if e.IsDir() || !strings.HasPrefix(nm, "tick-") || !strings.HasSuffix(nm, ".md") {
			continue
		}
		rest := strings.TrimSuffix(strings.TrimPrefix(nm, "tick-"), ".md")
		i := strings.Index(rest, "-")
		if i < 0 {
			continue
		}
		num, err := strconv.Atoi(rest[:i])
		if err != nil {
			continue
		}
		phase := rest[i+1:]
		b := &five
		if strings.Contains(phase, "externalize") {
			b = &ext
		}
		if num < b.first {
			b.first = num
		}
		if num > b.last {
			b.last = num
		}
		b.n++
		b.phases[phase]++
	}
	eras := make([]map[string]any, 0, 2)
	if five.n > 0 {
		eras = append(eras, map[string]any{
			"id":     "five-phase",
			"from":   five.first,
			"to":     five.last,
			"n":      five.n,
			"ticks":  []string{"roofline", "distill", "lattice", "calibrate", "dialectic"},
			"counts": five.phases,
			"why":    "Issuance loop: Qwen foraged, distilled, latticed; Governor calibrated and argued. Distill later reject-all on already-issued pipelines.",
		})
	}
	if ext.n > 0 {
		eras = append(eras, map[string]any{
			"id":     "externalize",
			"from":   ext.first,
			"to":     ext.last,
			"n":      ext.n,
			"ticks":  []string{"externalize-spectral", "externalize-residual", "externalize-vram"},
			"counts": ext.phases,
			"why":    "Hive retired the five-phase issuance beat. Exclusive question: manufacture 128-D cartridges, 5376-D residual bands, KVX shards. Governor grades forage; serving Gemma is not SGD'd.",
		})
	}

	fromFeedback := make([]map[string]any, 0, 4)
	if lastVerdict != nil {
		reason, _ := lastVerdict["reason"].(string)
		if reason != "" {
			fromFeedback = append(fromFeedback, map[string]any{
				"source": "governor last verdict",
				"reason": reason,
				"accept": lastVerdict["accept"],
				"rework": lastVerdict["rework"],
				"reject": lastVerdict["reject"],
				"change": "Rejected and reworked probe ids are rewritten from this reason before the next forage of that substrate.",
			})
		}
	}
	cal := summarizeCal(filepath.Join(research, "calibrate", "governor_scores.json"))
	if scores, ok := cal["scores"].([]any); ok {
		for _, s := range scores {
			row, _ := s.(map[string]any)
			if row != nil && row["correct"] != true {
				fromFeedback = append(fromFeedback, map[string]any{
					"source": "calibration",
					"item":   row["item_id"],
					"note":   row["note"],
					"change": "Keep this doctrine item in the next calibrate beat; do not treat the apprenticeship as finished.",
				})
			}
		}
	}
	gate, _ := readJSONFile(filepath.Join(research, "distill", "gate.json")).(map[string]any)
	if gate != nil {
		if d, _ := gate["decision"].(string); d == "reject-all" {
			fromFeedback = append(fromFeedback, map[string]any{
				"source": "issuance gate",
				"reason": gate["reason"],
				"change": "Issuance ticks stay retired. Forage manufactures shards, it does not re-issue the same pipeline slugs.",
			})
		}
	}

	gen := 1
	if m, ok := probesDoc.(map[string]any); ok {
		gen = asInt(m["generation"])
		if gen == 0 {
			gen = 1
		}
	}

	return map[string]any{
		"current":       "three-substrate externalize",
		"generation":    gen,
		"rule":          "Governor's accept/rework/reject rewrites the next forage probes. Calibration failures stay on the board. reject-all issuance does not revive the old five-phase beat.",
		"eras":          eras,
		"from_feedback": fromFeedback,
		"snapshot":      evolveDoc,
		"probes_file":   filepath.Join(research, "evolve", "probes.json"),
	}
}

func tailJSONL(path string, n int) []any {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 256*1024), 2*1024*1024)
	for sc.Scan() {
		t := strings.TrimSpace(sc.Text())
		if t != "" {
			lines = append(lines, t)
		}
	}
	if n > 0 && len(lines) > n {
		lines = lines[len(lines)-n:]
	}
	out := make([]any, 0, len(lines))
	for _, line := range lines {
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil {
			continue
		}
		out = append(out, rec)
	}
	return out
}

func asInt(v any) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case json.Number:
		n, _ := t.Int64()
		return int(n)
	case string:
		n, _ := strconv.Atoi(t)
		return n
	}
	return 0
}
