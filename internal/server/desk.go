package server

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"local/flux/internal/jury"
)

type deskPace struct {
	MovementMS  int `json:"movement_ms"`
	StreamN     int `json:"stream_n"`
	StreamSteps int `json:"stream_steps"`
}

type deskBoard struct {
	MovementMS  int                          `json:"movement_ms"`
	StreamN     int                          `json:"stream_n"`
	StreamSteps int                          `json:"stream_steps"`
	Prompts     map[string]map[string]string `json:"prompts,omitempty"`
}

type deskHive struct {
	Pixtral        float64 `json:"pixtral"`
	Qwen           float64 `json:"qwen"`
	Decoder        float64 `json:"decoder"`
	Governor       float64 `json:"governor"`
	EnablePixtral  bool    `json:"enable_pixtral"`
	EnableWitness  bool    `json:"enable_witness"`
	EnableGovernor bool    `json:"enable_governor"`
}

type deskJury struct {
	Mode          string             `json:"mode"`
	Adversarial   bool               `json:"adversarial"`
	TextFromGates bool               `json:"text_from_gates"`
	Uniqueness    bool               `json:"uniqueness"`
	GateTriage    bool               `json:"gate_triage"`
	MinJudges     int                `json:"min_judges"`
	Gamma         map[string]float64 `json:"gamma"`
}

type deskState struct {
	Lane    string                         `json:"lane"`
	Hive    deskHive                       `json:"hive"`
	Jury    deskJury                       `json:"jury"`
	Pace    deskPace                       `json:"pace"`
	Prompts map[string]map[string]string   `json:"prompts,omitempty"`
}

func (s Server) deskPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	name := "desk.html"
	switch strings.TrimSuffix(r.URL.Path, "/") {
	case "/desk", "/control":
		name = "desk.html"
	case "/desk/hive":
		name = "desk-hive.html"
	case "/desk/jury":
		name = "desk-jury.html"
	case "/desk/pace":
		name = "desk-pace.html"
	default:
		if r.URL.Path == "/desk/" || r.URL.Path == "/control/" {
			http.Redirect(w, r, "/desk", http.StatusPermanentRedirect)
			return
		}
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", name))
}

func (s Server) scoresPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	if r.URL.Path == "/scores/" {
		http.Redirect(w, r, "/scores", http.StatusPermanentRedirect)
		return
	}
	if r.URL.Path != "/scores" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "scores.html"))
}

func deskPacePath(root string) string {
	return filepath.Join(root, ".fluxd", "tea_desk.json")
}

func defaultRatingPrompts(root string) map[string]map[string]string {
	raw, err := os.ReadFile(filepath.Join(root, "apps", "tea", "rating_prompts.json"))
	if err != nil {
		return map[string]map[string]string{}
	}
	var prompts map[string]map[string]string
	if json.Unmarshal(raw, &prompts) != nil || prompts == nil {
		return map[string]map[string]string{}
	}
	return prompts
}

func mergePromptMaps(base, overlay map[string]map[string]string) map[string]map[string]string {
	out := map[string]map[string]string{}
	for role, axes := range base {
		cp := map[string]string{}
		for k, v := range axes {
			if strings.TrimSpace(v) != "" {
				cp[k] = v
			}
		}
		out[role] = cp
	}
	for role, axes := range overlay {
		cp := out[role]
		if cp == nil {
			cp = map[string]string{}
			out[role] = cp
		}
		for k, v := range axes {
			if strings.TrimSpace(v) != "" {
				cp[k] = strings.TrimSpace(v)
			}
		}
	}
	return out
}

func loadDeskBoard(root string) deskBoard {
	b := deskBoard{MovementMS: 83, StreamN: 256, StreamSteps: 18, Prompts: defaultRatingPrompts(root)}
	raw, err := os.ReadFile(deskPacePath(root))
	if err != nil {
		return b
	}
	var stored deskBoard
	if json.Unmarshal(raw, &stored) != nil {
		return b
	}
	if stored.MovementMS != 0 {
		b.MovementMS = stored.MovementMS
	}
	if stored.StreamN != 0 {
		b.StreamN = stored.StreamN
	}
	if stored.StreamSteps != 0 {
		b.StreamSteps = stored.StreamSteps
	}
	if b.MovementMS < 24 {
		b.MovementMS = 24
	}
	if b.MovementMS > 400 {
		b.MovementMS = 400
	}
	if b.StreamN != 512 {
		b.StreamN = 256
	}
	if b.StreamSteps != 28 && b.StreamSteps != 18 {
		b.StreamSteps = 18
	}
	if stored.Prompts != nil {
		b.Prompts = mergePromptMaps(b.Prompts, stored.Prompts)
	}
	return b
}

func loadDeskPace(root string) deskPace {
	b := loadDeskBoard(root)
	return deskPace{MovementMS: b.MovementMS, StreamN: b.StreamN, StreamSteps: b.StreamSteps}
}

func saveDeskBoard(root string, patch deskBoard) error {
	cur := loadDeskBoard(root)
	if patch.MovementMS != 0 {
		cur.MovementMS = patch.MovementMS
	}
	if patch.StreamN != 0 {
		cur.StreamN = patch.StreamN
	}
	if patch.StreamSteps != 0 {
		cur.StreamSteps = patch.StreamSteps
	}
	if patch.Prompts != nil {
		cur.Prompts = mergePromptMaps(cur.Prompts, patch.Prompts)
	}
	_ = os.MkdirAll(filepath.Join(root, ".fluxd"), 0o755)
	raw, err := json.MarshalIndent(cur, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(deskPacePath(root), raw, 0o644)
}

func saveDeskPace(root string, p deskPace) error {
	return saveDeskBoard(root, deskBoard{MovementMS: p.MovementMS, StreamN: p.StreamN, StreamSteps: p.StreamSteps})
}

func boolVal(p *bool, fallback bool) bool {
	if p == nil {
		return fallback
	}
	return *p
}

func boolPtr(v bool) *bool { return &v }

var deskRatingScale = []map[string]any{
	{"word": "failed", "hint": "failed render"},
	{"word": "broken", "hint": "cannot be cropped out"},
	{"word": "obvious", "hint": "a casual viewer notices immediately"},
	{"word": "competent", "hint": "a careful viewer finds a real fault in ten seconds"},
	{"word": "specialist", "hint": "one trivial fault a specialist would hunt for"},
	{"word": "flawless", "hint": "no fault findable at 100% zoom"},
}

func wordForScore(n float64) string {
	switch {
	case n <= 19:
		return "failed"
	case n <= 44:
		return "broken"
	case n <= 69:
		return "obvious"
	case n <= 85:
		return "competent"
	case n <= 95:
		return "specialist"
	default:
		return "flawless"
	}
}

func latestDeskRatings(frames []map[string]any) map[string]any {
	out := map[string]any{}
	for i := len(frames) - 1; i >= 0; i-- {
		scores, _ := frames[i]["scores"].([]map[string]any)
		if scores == nil {
			if raw, ok := frames[i]["scores"].([]any); ok {
				for _, item := range raw {
					if m, ok := item.(map[string]any); ok {
						scores = append(scores, m)
					}
				}
			}
		}
		if len(scores) == 0 {
			continue
		}
		for _, card := range scores {
			role, _ := card["role"].(string)
			if role == "" {
				continue
			}
			if ratings, ok := card["ratings"].(map[string]any); ok && len(ratings) > 0 {
				out[role] = ratings
				continue
			}
			words := map[string]any{}
			if subs, ok := card["subscores"].(map[string]any); ok {
				for k, v := range subs {
					if n, ok := asFloat(v); ok {
						words[k] = wordForScore(n)
					}
				}
			}
			if n, ok := asFloat(card["score"]); ok {
				words["overall"] = wordForScore(n)
			}
			if len(words) > 0 {
				out[role] = words
			}
		}
		if len(out) > 0 {
			return out
		}
	}
	return out
}

func hiveFromConfig(cfg jury.JuryConfig) deskHive {
	w := cfg.Weights
	if w == nil {
		w = map[string]float64{}
	}
	h := deskHive{
		Pixtral:        w["pixtral"],
		Qwen:           w["qwen"],
		Decoder:        w["decoder"],
		Governor:       w["governor"],
		EnablePixtral:  true,
		EnableWitness:  true,
		EnableGovernor: true,
	}
	if cfg.Endpoints != nil {
		if ep, ok := cfg.Endpoints[jury.ServedPixtral]; ok {
			h.EnablePixtral = boolVal(ep.Enabled, true)
		}
		if ep, ok := cfg.Endpoints[jury.ServedWitness]; ok {
			h.EnableWitness = boolVal(ep.Enabled, true)
		}
		if ep, ok := cfg.Endpoints[jury.ServedGovernor]; ok {
			h.EnableGovernor = boolVal(ep.Enabled, true)
		}
	}
	return h
}

func juryFromConfig(cfg jury.JuryConfig) deskJury {
	g := map[string]float64{}
	for k, v := range cfg.Strictness {
		g[k] = v
	}
	return deskJury{
		Mode:          cfg.Mode,
		Adversarial:   cfg.AdversarialMode,
		TextFromGates: cfg.TextFromGates,
		Uniqueness:    boolVal(cfg.UniquenessInfl, true),
		GateTriage:    boolVal(cfg.GateTriage, false),
		MinJudges:     cfg.MinJudges,
		Gamma:         g,
	}
}

func (s Server) teaDeskAPI(w http.ResponseWriter, r *http.Request) {
	lane := requestJuryLane(r, "")
	dir := s.juryDirForLane(lane)
	cfg, err := jury.GetConfig(dir)
	if err != nil {
		cfg = jury.DefaultConfig()
	}
	board := loadDeskBoard(s.cfg.Root)
	state := deskState{
		Lane:    lane,
		Hive:    hiveFromConfig(cfg),
		Jury:    juryFromConfig(cfg),
		Pace:    deskPace{MovementMS: board.MovementMS, StreamN: board.StreamN, StreamSteps: board.StreamSteps},
		Prompts: board.Prompts,
	}

	if r.Method == http.MethodPost {
		var incoming deskState
		if err := json.NewDecoder(r.Body).Decode(&incoming); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
		if incoming.Lane != "" {
			lane = requestJuryLane(r, incoming.Lane)
			dir = s.juryDirForLane(lane)
			cfg, _ = jury.GetConfig(dir)
		}
		patch := cfg
		h := incoming.Hive
		if h.Pixtral+h.Qwen+h.Decoder+h.Governor > 0 {
			patch.Weights = map[string]float64{
				"pixtral": h.Pixtral, "qwen": h.Qwen, "decoder": h.Decoder, "governor": h.Governor,
			}
		}
		if incoming.Jury.Mode != "" || incoming.Jury.Gamma != nil || incoming.Jury.MinJudges != 0 {
			j := incoming.Jury
			if j.Mode != "" {
				patch.Mode = j.Mode
			}
			patch.AdversarialMode = j.Adversarial
			patch.TextFromGates = j.TextFromGates
			patch.UniquenessInfl = boolPtr(j.Uniqueness)
			patch.GateTriage = boolPtr(j.GateTriage)
			if j.MinJudges > 0 {
				patch.MinJudges = j.MinJudges
			}
			if j.Gamma != nil {
				patch.Strictness = j.Gamma
			}
		}
		if patch.Endpoints == nil {
			patch.Endpoints = map[string]jury.JuryEndpoint{}
		}
		px := patch.Endpoints[jury.ServedPixtral]
		px.Enabled = boolPtr(incoming.Hive.EnablePixtral)
		patch.Endpoints[jury.ServedPixtral] = px
		ws := patch.Endpoints[jury.ServedWitness]
		ws.Enabled = boolPtr(incoming.Hive.EnableWitness)
		patch.Endpoints[jury.ServedWitness] = ws
		gv := patch.Endpoints[jury.ServedGovernor]
		gv.Enabled = boolPtr(incoming.Hive.EnableGovernor)
		patch.Endpoints[jury.ServedGovernor] = gv
		merged := jury.MergeConfig(cfg, patch)
		if err := jury.SaveConfig(dir, merged); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		boardPatch := deskBoard{Prompts: incoming.Prompts}
		if incoming.Pace.MovementMS != 0 || incoming.Pace.StreamN != 0 || incoming.Pace.StreamSteps != 0 {
			boardPatch.MovementMS = incoming.Pace.MovementMS
			boardPatch.StreamN = incoming.Pace.StreamN
			boardPatch.StreamSteps = incoming.Pace.StreamSteps
		}
		_ = saveDeskBoard(s.cfg.Root, boardPatch)
		cfg, _ = jury.GetConfig(dir)
		board = loadDeskBoard(s.cfg.Root)
		state = deskState{
			Lane:    lane,
			Hive:    hiveFromConfig(cfg),
			Jury:    juryFromConfig(cfg),
			Pace:    deskPace{MovementMS: board.MovementMS, StreamN: board.StreamN, StreamSteps: board.StreamSteps},
			Prompts: board.Prompts,
		}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "saved": true, "desk": state, "hive": resolveHiveTarget()})
		return
	}
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	specs, _ := jury.GetSpectacles(dir, 8)
	stream := readProtocolStreamStateFile(protocolBranchStatePath(s.cfg.Root, "silken-horses"))
	if stream == nil {
		stream = readProtocolStreamStateLane(s.cfg.Root, "fashion")
	}
	audit := digestAudit(dir, 24)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":           true,
		"desk":         state,
		"hive":         resolveHiveTarget(),
		"audit":        audit,
		"rating_scale": deskRatingScale,
		"ratings":      latestDeskRatings(audit.Frames),
		"spectacles":   specs,
		"live_models":  collectLiveModels(),
		"presets":      mustPresets(dir),
		"stream":       stream,
	})
}

func (s Server) teaScoresAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	fashion := digestAudit(s.cfg.OutputDir, 96)
	arcane := digestAudit(s.arcaneOutputDir(), 96)
	greens := digestAudit(s.juryDirForLane("microgreens"), 96)
	fs, _ := jury.GetSpectacles(s.cfg.OutputDir, 36)
	as, _ := jury.GetSpectacles(s.arcaneOutputDir(), 36)
	gs, _ := jury.GetSpectacles(s.juryDirForLane("microgreens"), 36)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":          true,
		"fashion":     map[string]any{"audit": fashion, "spectacles": fs},
		"arcane":      map[string]any{"audit": arcane, "spectacles": as},
		"microgreens": map[string]any{"audit": greens, "spectacles": gs},
	})
}
