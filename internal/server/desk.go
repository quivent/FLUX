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
	Lane string   `json:"lane"`
	Hive deskHive `json:"hive"`
	Jury deskJury `json:"jury"`
	Pace deskPace `json:"pace"`
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

func loadDeskPace(root string) deskPace {
	p := deskPace{MovementMS: 83, StreamN: 256, StreamSteps: 18}
	raw, err := os.ReadFile(deskPacePath(root))
	if err != nil {
		return p
	}
	_ = json.Unmarshal(raw, &p)
	if p.MovementMS < 24 {
		p.MovementMS = 24
	}
	if p.MovementMS > 400 {
		p.MovementMS = 400
	}
	if p.StreamN != 512 {
		p.StreamN = 256
	}
	if p.StreamSteps != 28 && p.StreamSteps != 18 {
		p.StreamSteps = 18
	}
	return p
}

func saveDeskPace(root string, p deskPace) error {
	_ = os.MkdirAll(filepath.Join(root, ".fluxd"), 0o755)
	raw, err := json.MarshalIndent(p, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(deskPacePath(root), raw, 0o644)
}

func boolVal(p *bool, fallback bool) bool {
	if p == nil {
		return fallback
	}
	return *p
}

func boolPtr(v bool) *bool { return &v }

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
	state := deskState{
		Lane: lane,
		Hive: hiveFromConfig(cfg),
		Jury: juryFromConfig(cfg),
		Pace: loadDeskPace(s.cfg.Root),
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
		if incoming.Hive.Pixtral+incoming.Hive.Qwen+incoming.Hive.Decoder+incoming.Hive.Governor > 0 {
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
		}
		merged := jury.MergeConfig(cfg, patch)
		if err := jury.SaveConfig(dir, merged); err != nil {
			http.Error(w, err.Error(), http.StatusInternalServerError)
			return
		}
		if incoming.Pace.MovementMS != 0 || incoming.Pace.StreamN != 0 || incoming.Pace.StreamSteps != 0 {
			p := loadDeskPace(s.cfg.Root)
			if incoming.Pace.MovementMS != 0 {
				p.MovementMS = incoming.Pace.MovementMS
			}
			if incoming.Pace.StreamN != 0 {
				p.StreamN = incoming.Pace.StreamN
			}
			if incoming.Pace.StreamSteps != 0 {
				p.StreamSteps = incoming.Pace.StreamSteps
			}
			_ = saveDeskPace(s.cfg.Root, p)
		}
		cfg, _ = jury.GetConfig(dir)
		state = deskState{Lane: lane, Hive: hiveFromConfig(cfg), Jury: juryFromConfig(cfg), Pace: loadDeskPace(s.cfg.Root)}
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "saved": true, "desk": state, "hive": resolveHiveTarget()})
		return
	}
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":    true,
		"desk":  state,
		"hive":  resolveHiveTarget(),
		"audit": digestAudit(dir, 24),
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
