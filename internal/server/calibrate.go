package server

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"local/flux/internal/jury"
)

type hiveTarget struct {
	URL       string `json:"url"`
	Model     string `json:"model"`
	Fallback  string `json:"fallback"`
	FallModel string `json:"fallback_model"`
	Live      bool   `json:"live"`
	Source    string `json:"source"`
}

type liveModels struct {
	Governor []string `json:"governor"`
	Witness  []string `json:"witness"`
	Hive     []string `json:"hive"`
	Pixtral  []string `json:"pixtral"`
}

type auditDigest struct {
	N           int              `json:"n"`
	Window      int              `json:"window"`
	Unscored    int              `json:"unscored"`
	Scored      int              `json:"scored"`
	Spectacles  int              `json:"spectacles"`
	Masterpiece int              `json:"masterpiece"`
	Reasons     map[string]int   `json:"reasons,omitempty"`
	Degraded    map[string]int   `json:"degraded,omitempty"`
	MeanRaw     float64          `json:"mean_raw,omitempty"`
	UniqMean    float64          `json:"uniqueness_mean,omitempty"`
	UniqMinDist float64          `json:"uniqueness_min_distance,omitempty"`
	Collapse    int              `json:"mode_collapse,omitempty"`
	UniqCats    map[string]int   `json:"uniqueness_categories,omitempty"`
	NoveltyMean float64          `json:"novelty_mean,omitempty"`
	GateAesMean float64          `json:"gate_aesthetic_mean,omitempty"`
	GatePass    int              `json:"gate_pass,omitempty"`
	GateFail    int              `json:"gate_fail,omitempty"`
	Frames      []map[string]any `json:"frames"`
}

func resolveHiveTarget() hiveTarget {
	t := hiveTarget{
		URL:       "http://127.0.0.1:8002/v1/chat/completions",
		Model:     "hive-research",
		Fallback:  "http://127.0.0.1:8000/v1/chat/completions",
		FallModel: "governor",
		Source:    "default",
	}
	home, _ := os.UserHomeDir()
	raw, err := os.ReadFile(filepath.Join(home, ".hive-wails-config.json"))
	if err == nil {
		var cfg struct {
			QwenEndpoint string `json:"qwenEndpoint"`
			ModelName    string `json:"modelName"`
		}
		if json.Unmarshal(raw, &cfg) == nil {
			if strings.TrimSpace(cfg.QwenEndpoint) != "" {
				t.URL = strings.TrimSpace(cfg.QwenEndpoint)
				t.Source = "hive-wails"
			}
			if strings.TrimSpace(cfg.ModelName) != "" {
				t.Model = strings.TrimSpace(cfg.ModelName)
			}
		}
	}
	if v := strings.TrimSpace(os.Getenv("HIVE_URL")); v != "" {
		t.URL = v
		t.Source = "env"
	}
	if v := strings.TrimSpace(os.Getenv("HIVE_MODEL")); v != "" {
		t.Model = v
	}
	t.Live = tcpAlive(hostPortFromURL(t.URL), 250*time.Millisecond)
	return t
}

func hostPortFromURL(raw string) string {
	u := strings.TrimPrefix(strings.TrimPrefix(raw, "https://"), "http://")
	u = strings.Split(u, "/")[0]
	if u == "" {
		return "127.0.0.1:8002"
	}
	return u
}

func listVLLMModels(addr string) []string {
	client := &http.Client{Timeout: 800 * time.Millisecond}
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

func collectLiveModels() liveModels {
	return liveModels{
		Governor: listVLLMModels("127.0.0.1:8000"),
		Witness:  listVLLMModels("127.0.0.1:8001"),
		Hive:     listVLLMModels("127.0.0.1:8002"),
		Pixtral:  listVLLMModels("127.0.0.1:8004"),
	}
}

func digestAudit(outputDir string, window int) auditDigest {
	if window <= 0 {
		window = 24
	}
	d := auditDigest{Reasons: map[string]int{}, Degraded: map[string]int{}, UniqCats: map[string]int{}, Frames: []map[string]any{}}
	f, err := os.Open(filepath.Join(outputDir, "audit.jsonl"))
	if err != nil {
		return d
	}
	defer f.Close()
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 256*1024), 2*1024*1024)
	for sc.Scan() {
		text := strings.TrimSpace(sc.Text())
		if text != "" {
			lines = append(lines, text)
		}
	}
	d.N = len(lines)
	start := 0
	if len(lines) > window {
		start = len(lines) - window
	}
	slice := lines[start:]
	d.Window = len(slice)
	var sum float64
	var nSum int
	var uniqSum, uniqDistSum, novSum, gateAesSum float64
	var nUniq, nNov, nGate int
	for _, line := range slice {
		var rec map[string]any
		if json.Unmarshal([]byte(line), &rec) != nil {
			continue
		}
		tier, _ := rec["tier"].(string)
		unscored, _ := rec["unscored"].(bool)
		if unscored || tier == "unscored" {
			d.Unscored++
			reason, _ := rec["unscored_reason"].(string)
			if reason == "" {
				reason = "unscored"
			}
			if len(reason) > 120 {
				reason = reason[:120]
			}
			d.Reasons[reason]++
		} else {
			d.Scored++
			if tier == "spectacle" {
				d.Spectacles++
			}
			if tier == "masterpiece" {
				d.Masterpiece++
			}
			if raw, ok := asFloat(rec["raw_composite"]); ok {
				sum += raw
				nSum++
			}
		}
		frame := map[string]any{
			"job_id": rec["job_id"],
			"tier":   tier,
		}
		if uniq, ok := rec["uniqueness"].(map[string]any); ok {
			if sc, ok := asFloat(uniq["score"]); ok {
				uniqSum += sc
				nUniq++
				frame["uniqueness"] = sc
			}
			if dist, ok := asFloat(uniq["min_distance"]); ok {
				uniqDistSum += dist
			}
			if cat, _ := uniq["category"].(string); cat != "" {
				d.UniqCats[cat]++
				frame["uniq_cat"] = cat
			}
			if b, _ := uniq["mode_collapse"].(bool); b {
				d.Collapse++
				frame["collapse"] = true
			}
		}
		if gates, ok := rec["gates"].(map[string]any); ok {
			if nov, ok := asFloat(gates["novelty"]); ok {
				novSum += nov
				nNov++
				frame["novelty"] = nov
			}
			if aes, ok := asFloat(gates["aesthetic"]); ok {
				gateAesSum += aes
				nGate++
			}
			if passed, _ := gates["passed"].(bool); passed {
				d.GatePass++
			} else if gates["passed"] != nil {
				d.GateFail++
			}
		}
		if rec["raw_composite"] != nil {
			frame["raw_composite"] = rec["raw_composite"]
		}
		if judges, ok := rec["judges"].([]any); ok {
			var degr []string
			var scores []map[string]any
			for _, rawJ := range judges {
				j, _ := rawJ.(map[string]any)
				if j == nil {
					continue
				}
				role, _ := j["role"].(string)
				if b, _ := j["degraded"].(bool); b {
					errStr, _ := j["error"].(string)
					if len(errStr) > 80 {
						errStr = errStr[:80]
					}
					key := role
					if errStr != "" {
						key = role + ": " + errStr
					}
					d.Degraded[key]++
					degr = append(degr, key)
				} else if j["score"] != nil {
					card := map[string]any{"role": role, "score": j["score"]}
					if title, _ := j["title"].(string); title != "" {
						card["title"] = title
					}
					if model, _ := j["model"].(string); model != "" {
						card["model"] = model
					}
					if ep, _ := j["endpoint"].(string); ep != "" {
						card["endpoint"] = ep
					} else if ep, _ := j["base_url"].(string); ep != "" {
						card["endpoint"] = ep
					}
					if crit, _ := j["critique"].(string); crit != "" {
						if len(crit) > 160 {
							crit = crit[:160]
						}
						card["critique"] = crit
					}
					if borrowed, _ := j["borrowed_vision"].(string); borrowed != "" {
						card["borrowed_vision"] = borrowed
					}
					if subs, ok := j["subscores"].(map[string]any); ok {
						card["subscores"] = subs
						if a, ok := asFloat(subs["anatomy"]); ok {
							card["anatomy"] = a
						}
					}
					if ratings, ok := j["ratings"].(map[string]any); ok && len(ratings) > 0 {
						card["ratings"] = ratings
					} else if obs, ok := j["observations"].(map[string]any); ok {
						if ratings, ok := obs["ratings"].(map[string]any); ok && len(ratings) > 0 {
							card["ratings"] = ratings
						}
					}
					if card["ratings"] == nil && card["subscores"] != nil {
						if subs, ok := card["subscores"].(map[string]any); ok {
							words := map[string]any{}
							for k, v := range subs {
								if n, ok := asFloat(v); ok {
									words[k] = wordForScore(n)
								}
							}
							if n, ok := asFloat(j["score"]); ok {
								words["overall"] = wordForScore(n)
							}
							if len(words) > 0 {
								card["ratings"] = words
							}
						}
					}
					scores = append(scores, card)
				}
			}
			if len(degr) > 0 {
				frame["degraded"] = degr
			}
			if len(scores) > 0 {
				frame["scores"] = scores
			}
		}
		d.Frames = append(d.Frames, frame)
	}
	if nSum > 0 {
		d.MeanRaw = sum / float64(nSum)
	}
	if nUniq > 0 {
		d.UniqMean = uniqSum / float64(nUniq)
		d.UniqMinDist = uniqDistSum / float64(nUniq)
	}
	if nNov > 0 {
		d.NoveltyMean = novSum / float64(nNov)
	}
	if nGate > 0 {
		d.GateAesMean = gateAesSum / float64(nGate)
	}
	return d
}

func asFloat(v any) (float64, bool) {
	switch t := v.(type) {
	case float64:
		return t, true
	case int:
		return float64(t), true
	case json.Number:
		f, err := t.Float64()
		return f, err == nil
	default:
		return 0, false
	}
}

func bindLiveModels(cfg jury.JuryConfig, live liveModels) jury.JuryConfig {
	if cfg.Endpoints == nil {
		cfg.Endpoints = map[string]jury.JuryEndpoint{}
	}
	falseV := jury.BoolPtr(false)
	trueV := jury.BoolPtr(true)
	pick := func(ids []string, prefer []string) string {
		for _, want := range prefer {
			for _, have := range ids {
				if have == want {
					return have
				}
			}
		}
		if len(ids) > 0 {
			return ids[0]
		}
		return ""
	}
	if m := pick(live.Witness, []string{"jury", "gemma-jury", "visual-witness"}); m != "" {
		cfg.Endpoints[jury.ServedWitness] = jury.JuryEndpoint{
			BaseURL: "http://127.0.0.1:8001/v1",
			Model:   m,
			Enabled: trueV,
			Vision:  falseV,
		}
	}
	if m := pick(live.Pixtral, []string{"pixtral", "pixtral-12b", "pixtral-jury", "pixtral-critic"}); m != "" {
		cfg.Endpoints[jury.ServedPixtral] = jury.JuryEndpoint{
			BaseURL: "http://127.0.0.1:8004/v1",
			Model:   m,
			Enabled: trueV,
			Vision:  trueV,
		}
	} else {
		delete(cfg.Endpoints, jury.ServedPixtral)
	}
	govModel := pick(live.Governor, []string{"governor", "qwen-governor"})
	if govModel == "" {
		govModel = "governor"
	}
	cfg.Endpoints[jury.ServedGovernor] = jury.JuryEndpoint{
		BaseURL: "http://127.0.0.1:8800/v1",
		Model:   govModel,
		Enabled: trueV,
		Vision:  falseV,
	}
	return cfg
}

func heuristicProposal(current jury.JuryConfig, live liveModels, audit auditDigest) jury.CalibrationRecord {
	cfg := current
	cfg = bindLiveModels(cfg, live)
	rationale := "Heuristic calibration (hive did not return a usable JSON proposal)."
	diagnosis := ""
	if audit.Window > 0 && audit.Unscored == audit.Window {
		cfg.TextFromGates = true
		cfg.MinJudges = 1
		diagnosis = fmt.Sprintf(
			"All %d recent frames are unscored. Live seats are text-only (no image slots). Binding live models and scoring from uniqueness + sensory-gate testimony so the hive and governor can actually sit.",
			audit.Window,
		)
		rationale = "Enable text-from-gates, bind hive-research / jury / governor, keep current weights, gamma unchanged until real scores exist."
	} else if audit.Scored > 0 && audit.MeanRaw >= 88 {
		for k, g := range cfg.Strictness {
			if g < 2.4 {
				cfg.Strictness[k] = g + 0.3
			}
		}
		cfg.AdversarialMode = true
		diagnosis = fmt.Sprintf("Recent scored mean raw composite is %.1f — raise gamma so 90+ stays rare.", audit.MeanRaw)
	} else if audit.Scored > 0 && audit.MeanRaw > 0 && audit.MeanRaw < 55 {
		for k, g := range cfg.Strictness {
			if g > 1.3 {
				cfg.Strictness[k] = g - 0.3
			}
		}
		diagnosis = fmt.Sprintf("Recent scored mean raw composite is %.1f — ease gamma so the sieve still ranks.", audit.MeanRaw)
	} else {
		diagnosis = "Recent mix is usable; bind live model names so seats stop 404ing and keep current weights."
	}
	jury.NormalizeConfig(&cfg)
	return jury.CalibrationRecord{
		TS:        time.Now().Unix(),
		Source:    "heuristic",
		Diagnosis: diagnosis,
		Rationale: rationale,
		Proposal:  cfg,
		Audit:     audit,
	}
}

func extractJSONObject(text string) (map[string]any, string, error) {
	text = strings.TrimSpace(text)
	if i := strings.Index(text, "```"); i >= 0 {
		rest := text[i+3:]
		rest = strings.TrimPrefix(rest, "json")
		rest = strings.TrimPrefix(rest, "JSON")
		if j := strings.Index(rest, "```"); j >= 0 {
			text = rest[:j]
		} else {
			text = rest
		}
		text = strings.TrimSpace(text)
	}
	var best map[string]any
	var snippet string
	score := func(obj map[string]any) int {
		n := 0
		if obj["weights"] != nil {
			n += 4
		}
		if obj["strictness"] != nil {
			n += 3
		}
		if obj["endpoints"] != nil {
			n += 2
		}
		if obj["mode"] != nil || obj["text_from_gates"] != nil {
			n++
		}
		return n
	}
	bestScore := -1
	for i := 0; i < len(text); i++ {
		if text[i] != '{' {
			continue
		}
		dec := json.NewDecoder(strings.NewReader(text[i:]))
		var obj map[string]any
		if err := dec.Decode(&obj); err != nil || obj == nil {
			continue
		}
		raw, _ := json.Marshal(obj)
		s := score(obj)
		if s > bestScore {
			bestScore = s
			best = obj
			snippet = string(raw)
			if s >= 4 {
				return best, snippet, nil
			}
		}
	}
	if best == nil {
		return nil, text, fmt.Errorf("hive returned no JSON object")
	}
	return best, snippet, nil
}

func proposalFromHiveJSON(obj map[string]any, current jury.JuryConfig, live liveModels) (jury.JuryConfig, string, string) {
	cfg := current
	if mode, _ := obj["mode"].(string); mode == "parallel" || mode == "sequential" {
		cfg.Mode = mode
	}
	if adv, ok := obj["adversarial_mode"].(bool); ok {
		cfg.AdversarialMode = adv
	}
	if tg, ok := obj["text_from_gates"].(bool); ok {
		cfg.TextFromGates = tg
	}
	if n, ok := asFloat(obj["min_judges"]); ok && n >= 1 {
		cfg.MinJudges = int(n)
	}
	if w, ok := obj["weights"].(map[string]any); ok {
		if cfg.Weights == nil {
			cfg.Weights = map[string]float64{}
		}
		for k, v := range w {
			if f, ok := asFloat(v); ok {
				cfg.Weights[k] = f
			}
		}
	}
	if s, ok := obj["strictness"].(map[string]any); ok {
		if cfg.Strictness == nil {
			cfg.Strictness = map[string]float64{}
		}
		for k, v := range s {
			if f, ok := asFloat(v); ok {
				cfg.Strictness[k] = f
			}
		}
	}
	if eps, ok := obj["endpoints"].(map[string]any); ok {
		if cfg.Endpoints == nil {
			cfg.Endpoints = map[string]jury.JuryEndpoint{}
		}
		for key, raw := range eps {
			m, _ := raw.(map[string]any)
			if m == nil {
				continue
			}
			ep := cfg.Endpoints[key]
			if v, _ := m["model"].(string); v != "" {
				ep.Model = v
			}
			if v, _ := m["base_url"].(string); v != "" {
				ep.BaseURL = v
			}
			if v, ok := m["enabled"].(bool); ok {
				ep.Enabled = jury.BoolPtr(v)
			}
			if v, ok := m["vision"].(bool); ok {
				ep.Vision = jury.BoolPtr(v)
			}
			cfg.Endpoints[key] = ep
		}
	} else {
		cfg = bindLiveModels(cfg, live)
	}
	diag, _ := obj["diagnosis"].(string)
	rat, _ := obj["rationale"].(string)
	if rat == "" {
		rat, _ = obj["why"].(string)
	}
	diag = strings.TrimSpace(diag)
	rat = strings.TrimSpace(rat)
	if strings.EqualFold(diag, "one sentence") {
		diag = ""
	}
	if strings.EqualFold(rat, "one sentence") {
		rat = ""
	}
	jury.NormalizeConfig(&cfg)
	return cfg, diag, rat
}

func chatComplete(url, model, system, user string, timeout time.Duration) (string, error) {
	body, _ := json.Marshal(map[string]any{
		"model": model,
		"messages": []map[string]string{
			{"role": "system", "content": system},
			{"role": "user", "content": user},
		},
		"temperature":     0.2,
		"max_tokens":      900,
		"stream":          false,
		"response_format": map[string]string{"type": "json_object"},
	})
	req, err := http.NewRequest(http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/json")
	client := &http.Client{Timeout: timeout}
	resp, err := client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		snip := raw
		if len(snip) > 240 {
			snip = snip[:240]
		}
		return "", fmt.Errorf("HTTP %d from %s: %s", resp.StatusCode, url, strings.TrimSpace(string(snip)))
	}
	var decoded struct {
		Choices []struct {
			Message struct {
				Content any `json:"content"`
			} `json:"message"`
		} `json:"choices"`
	}
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return "", err
	}
	if len(decoded.Choices) == 0 {
		return "", fmt.Errorf("no choices from %s", model)
	}
	return stringifyContent(decoded.Choices[0].Message.Content), nil
}

func stringifyContent(content any) string {
	switch t := content.(type) {
	case string:
		return t
	case []any:
		var b strings.Builder
		for _, part := range t {
			if m, ok := part.(map[string]any); ok {
				if s, ok := m["text"].(string); ok {
					b.WriteString(s)
				}
			} else if s, ok := part.(string); ok {
				b.WriteString(s)
			}
		}
		return b.String()
	default:
		return fmt.Sprint(t)
	}
}

func hiveCalibrationBrief(lane string) string {
	switch strings.ToLower(strings.TrimSpace(lane)) {
	case "microgreens":
		return "Lane: microgreens (Belarro culinary still photographs of soil-grown microgreens. Not people. Not fashion. Not Arcane.)"
	case "fashion", "":
		return "Lane: fashion (editorial fashion stills on GPU 3. Not horses. Not microgreens. Not Arcane.)"
	case "silken-horses":
		return "Lane: silken-horses (equine beauty stills: true anatomy, coat, motion, a thin glimmer of fantasy. One or a few horses. Not fashion, not microgreens, not Arcane. Uniqueness and anatomical correctness matter more than a pretty sunset.)"
	case "arcane":
		return "Lane: arcane (Arcane Fortiche animation stills. Not fashion. Not horses. Not microgreens.)"
	default:
		return fmt.Sprintf("Lane: %s (independent protocol-branch collection. Not fashion. Not microgreens. Not Arcane unless the slug is arcane.)", lane)
	}
}

func askHiveForCalibration(current jury.JuryConfig, live liveModels, audit auditDigest, note, lane string, study map[string]any) (jury.CalibrationRecord, map[string]any) {
	hive := resolveHiveTarget()
	currentJSON, _ := json.MarshalIndent(current, "", "  ")
	liveJSON, _ := json.Marshal(live)
	auditJSON, _ := json.Marshal(audit)
	studyJSON, _ := json.Marshal(study)
	system := "You are the Hive of a stills beauty jury. You design the next harvest parameters from uniqueness and novelty. You also calibrate judging weights. Reply with ONE JSON object and nothing else. No motion. No Arcane."
	user := fmt.Sprintf(`%s

Current jury law:
%s

Current harvest parameters (operator defaults unless designed_by=hive):
%s

Live engines:
- aesthetic / pixtral-critic is Pixtral on :8004 (vision ON)
- structure / visual-witness is Gemma jury on :8001 (text)
- governor through the agentic gateway on :8800 (text; never raw :8000)
- hive-research Qwen on :8002 is YOU, not a critic
%s

Recent audit (uniqueness + sensory-gate novelty are the harvest health):
%s

Operator note: %s

The stills have been collapsing. If uniqueness_categories is REDUNDANT_CLUSTER or mode_collapse is high, change the harvest: steps, size, guidance, life. Do not leave the same 28-step 1024 square if novelty is low.

Reply ONLY as JSON with:
mode, adversarial_mode, text_from_gates, min_judges, weights, strictness,
render: { steps (8-64), width and height (256-1280, multiples of 64), guidance (1.5-6.0), life (0-100), depth (1-3) },
diagnosis, rationale.
diagnosis must mention uniqueness and novelty numbers from THIS audit.`,
		hiveCalibrationBrief(lane), string(currentJSON), string(studyJSON), string(liveJSON), string(auditJSON), strings.TrimSpace(note+""))
	if strings.TrimSpace(note) == "" {
		user = strings.Replace(user, "Operator note: \n\n", "", 1)
	}

	try := func(url, model, source string) (jury.CalibrationRecord, map[string]any, error) {
		text, err := chatComplete(url, model, system, user, 90*time.Second)
		if err != nil {
			return jury.CalibrationRecord{}, nil, err
		}
		obj, snippet, err := extractJSONObject(text)
		if err != nil {
			return jury.CalibrationRecord{RawSnippet: snippet}, nil, err
		}
		cfg, diag, rat := proposalFromHiveJSON(obj, current, live)
		render := parseHiveRender(obj)
		return jury.CalibrationRecord{
			TS:         time.Now().Unix(),
			Source:     source,
			Endpoint:   url,
			Model:      model,
			Diagnosis:  diag,
			Rationale:  rat,
			Proposal:   cfg,
			Audit:      audit,
			RawSnippet: snippet,
		}, render, nil
	}

	rec, render, err := try(hive.URL, hive.Model, "hive")
	if err != nil {
		rec2, render2, err2 := try(hive.Fallback, hive.FallModel, "governor-fallback")
		if err2 != nil {
			h := heuristicProposal(current, live, audit)
			h.Error = fmt.Sprintf("hive: %v; fallback: %v", err, err2)
			if rec.RawSnippet != "" {
				h.RawSnippet = rec.RawSnippet
			}
			h.Endpoint = hive.URL
			h.Model = hive.Model
			return h, nil
		}
		rec2.Error = "hive primary failed: " + err.Error()
		return rec2, render2
	}
	return rec, render
}

func parseHiveRender(obj map[string]any) map[string]any {
	if obj == nil {
		return nil
	}
	raw, _ := obj["render"].(map[string]any)
	if raw == nil {
		return nil
	}
	out := map[string]any{}
	if steps := clampStudySteps(jsonInt(raw["steps"], 0)); steps != 0 {
		out["steps"] = steps
	}
	if w := jsonInt(raw["width"], 0); w > 0 {
		out["width"] = clampStudySize(w)
	}
	if h := jsonInt(raw["height"], 0); h > 0 {
		out["height"] = clampStudySize(h)
	}
	if g, ok := asFloat(raw["guidance"]); ok && g >= 1.5 && g <= 6 {
		out["guidance"] = g
	}
	if life := jsonInt(raw["life"], -1); life >= 0 && life <= 100 {
		out["life"] = life
	}
	if depth := jsonInt(raw["depth"], 0); depth >= 1 && depth <= 3 {
		out["depth"] = depth
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func (s Server) protocolCalibrateAPI(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		s.getProtocolCalibration(w, r)
	case http.MethodPost:
		s.postProtocolCalibration(w, r)
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
	}
}

func (s Server) getProtocolCalibration(w http.ResponseWriter, r *http.Request) {
	lane := requestJuryLane(r, "")
	dir := s.juryDirForLane(lane)
	live := collectLiveModels()
	hive := resolveHiveTarget()
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":          true,
		"lane":        lane,
		"lanes":       s.juryLaneButtons(),
		"hive":        hive,
		"live_models": live,
		"config":      mustJuryConfig(dir),
		"latest":      jury.LatestCalibration(dir),
		"history":     jury.RecentCalibrations(dir, 6),
		"audit":       digestAudit(dir, 24),
		"presets":     mustPresets(dir),
	})
}

func mustJuryConfig(outputDir string) jury.JuryConfig {
	cfg, err := jury.GetConfig(outputDir)
	if err != nil {
		return jury.DefaultConfig()
	}
	return cfg
}

func mustPresets(outputDir string) []jury.JuryPreset {
	p, err := jury.ListPresets(outputDir)
	if err != nil {
		return jury.BuiltinPresets()
	}
	return p
}

func (s Server) postProtocolCalibration(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Apply       bool             `json:"apply"`
		ApplyRender bool             `json:"apply_render"`
		BindLive    bool             `json:"bind_live"`
		Note        string           `json:"note"`
		Lane        string           `json:"lane"`
		Proposal    *jury.JuryConfig `json:"proposal"`
	}
	if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&req); err != nil && err != io.EOF {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	lane := requestJuryLane(r, req.Lane)
	dir := s.juryDirForLane(lane)
	current := mustJuryConfig(dir)
	live := collectLiveModels()
	audit := digestAudit(dir, 24)

	if req.BindLive && req.Proposal == nil {
		cfg := bindLiveModels(current, live)
		pix, ok := cfg.Endpoints[jury.ServedPixtral]
		sees := ok && pix.Vision != nil && *pix.Vision
		cfg.TextFromGates = !sees
		cfg.MinJudges = 1
		jury.NormalizeConfig(&cfg)
		diag := "Bound Pixtral to the aesthetic seat with vision. Qwen stays on :8002 as hive, not as a critic."
		rationale := "Aesthetic is pixtral-critic on :8004. Structure and governor remain text seats."
		if !sees {
			diag = "Pixtral is not live on :8004. Did not bind Qwen into the aesthetic seat."
			rationale = "Leave pixtral-critic unbound until /models/pixtral is serving with image slots."
		}
		rec := jury.CalibrationRecord{
			TS:        time.Now().Unix(),
			Source:    "bind-live",
			Diagnosis: diag,
			Rationale: rationale,
			Proposal:  cfg,
			Audit:     audit,
		}
		applied := false
		if req.Apply {
			if err := jury.SaveConfig(dir, cfg); err != nil {
				writeError(w, http.StatusInternalServerError, err.Error())
				return
			}
			rec.Applied = true
			applied = true
		}
		_ = jury.SaveCalibration(dir, rec)
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":          true,
			"lane":        lane,
			"applied":     applied,
			"calibration": rec,
			"config":      mustJuryConfig(dir),
			"live_models": live,
			"hive":        resolveHiveTarget(),
			"audit":       audit,
		})
		return
	}

	var rec jury.CalibrationRecord
	var render map[string]any
	if req.Proposal != nil {
		cfg := jury.MergeConfig(current, *req.Proposal)
		jury.NormalizeConfig(&cfg)
		rec = jury.CalibrationRecord{
			TS:        time.Now().Unix(),
			Source:    "operator",
			Note:      req.Note,
			Diagnosis: "Operator-supplied proposal",
			Proposal:  cfg,
			Audit:     audit,
		}
	} else {
		study := map[string]any{}
		if lane == "microgreens" {
			study = s.loadMicrogreensStudy()
		}
		rec, render = askHiveForCalibration(current, live, audit, req.Note, lane, study)
		rec.Note = req.Note
	}

	applied := false
	if req.Apply {
		if err := jury.SaveConfig(dir, rec.Proposal); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		rec.Applied = true
		applied = true
	}
	if req.ApplyRender && lane == "microgreens" && render != nil {
		s.applyHiveRender(render, rec.Rationale)
	}
	_ = jury.SaveCalibration(dir, rec)
	payload := map[string]any{
		"ok":          true,
		"lane":        lane,
		"applied":     applied,
		"calibration": rec,
		"config":      mustJuryConfig(dir),
		"hive":        resolveHiveTarget(),
		"live_models": live,
		"audit":       audit,
	}
	if lane == "microgreens" {
		payload["render"] = render
		payload["study"] = s.loadMicrogreensStudy()
	}
	writeJSON(w, http.StatusOK, payload)
}
