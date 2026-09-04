package server

import (
	"encoding/json"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type characterCable struct {
	ID   string `json:"id"`
	Name string `json:"name"`
	Spec string `json:"spec"`
	Hint string `json:"hint"`
}

type characterDaemon struct {
	ID        string             `json:"id"`
	Name      string             `json:"name"`
	Role      string             `json:"role"`
	Kind      string             `json:"kind"`
	Mode      string             `json:"mode"`
	Domain    string             `json:"domain"`
	GPU       int                `json:"gpu"`
	Weights   map[string]float64 `json:"weights"`
	Bands     []float64          `json:"bands"`
	FusedNorm float64            `json:"fused_norm"`
	System    string             `json:"system"`
}

type characterState struct {
	Schema     string            `json:"schema"`
	UpdatedAt  time.Time         `json:"updated_at"`
	GPU        int               `json:"gpu"`
	HiddenSize int               `json:"hidden_size"`
	BandCount  int               `json:"bands"`
	Mounted    string            `json:"mounted"`
	Cables     []characterCable  `json:"cables"`
	Items      []characterDaemon `json:"items"`
	Source     string            `json:"source"`
}

var characterCables = []characterCable{
	{ID: "expansion", Name: "Socratic Expansion", Spec: "SPEC-25", Hint: "Lateral hypothesis. Ask the question that opens the work."},
	{ID: "discipline", Name: "Non-Narrative Discipline", Spec: "SPEC-12", Hint: "Kill fluff. No unrequested summary."},
	{ID: "grounding", Name: "RII Identity Anchor", Spec: "SPEC-20", Hint: "Source wins. Observation before inference."},
	{ID: "focus", Name: "Attention Focus", Spec: "SPEC-18", Hint: "Sharpen. Peripheral noise damped."},
}

func characterStatePath(root string) string {
	return filepath.Join(root, ".fluxd", "tea_characters.json")
}

func defaultCharacterDaemons() []characterDaemon {
	mk := func(id, name, role, mode, domain string, e, d, g, f float64) characterDaemon {
		return finishCharacter(characterDaemon{
			ID: id, Name: name, Role: role, Kind: "character", Mode: mode, Domain: domain, GPU: 1,
			Weights: map[string]float64{"expansion": e, "discipline": d, "grounding": g, "focus": f},
		})
	}
	return []characterDaemon{
		mk("apprentice", "Apprentice", "Board for the three: reliability, memory expansion, reasoning performance.", "hybrid", "SPEC-25", 0.55, 0.70, 0.90, 0.80),
		mk("socratic", "Socratic", "Memory expansion: the question that makes a shard worth keeping.", "train", "SPEC-25", 0.90, 0.45, 0.75, 0.60),
		mk("inquisitor", "Inquisitor", "Reliability: a claim is not true until evidence holds.", "train", "SPEC-25", 0.70, 0.90, 0.80, 0.75),
		mk("surgeon", "Surgeon", "Do not break. Execution that leaves GPU 1 and tools intact.", "execution", "SPEC-12", 0.12, 0.92, 0.95, 0.92),
		mk("architect", "Architect", "Memory expansion: structure that can be retrieved, not a longer prompt.", "design", "SPEC-25", 0.78, 0.40, 0.85, 0.65),
		mk("witness", "Witness", "Reliability: observed / inferred / hypothetical / unknown. No invented facts.", "train", "SPEC-20", 0.25, 0.60, 0.98, 0.70),
		mk("board", "Board", "Reasoning performance: press Qwen-as-reason; leave him the durable bit.", "train", "SPEC-18", 0.65, 0.55, 0.80, 0.88),
		mk("law", "Law", "Reliability as charter. If it can fail silently, it is not trained.", "execution", "SPEC-12", 0.20, 0.92, 0.95, 0.80),
	}
}

func finishCharacter(c characterDaemon) characterDaemon {
	c.Kind = "character"
	c.GPU = 1
	if c.Weights == nil {
		c.Weights = map[string]float64{}
	}
	for _, k := range []string{"expansion", "discipline", "grounding", "focus"} {
		w := c.Weights[k]
		if w < 0 {
			w = 0
		}
		if w > 1 {
			w = 1
		}
		c.Weights[k] = w
	}
	c.Bands = spectralBands(c.Weights)
	c.FusedNorm = cableNorm(c.Weights)
	c.System = characterSystem(c)
	if c.Mode == "" {
		c.Mode = "train"
	}
	if c.Domain == "" {
		c.Domain = "SPEC-25"
	}
	return c
}

func spectralBands(w map[string]float64) []float64 {
	order := []string{"expansion", "discipline", "grounding", "focus"}
	out := make([]float64, 128)
	n := float64(len(order))
	for i := 0; i < 128; i++ {
		x := (float64(i) + 0.5) / 128
		v := 0.0
		for k, key := range order {
			v += w[key] * math.Abs(math.Sin(float64(k+1)*math.Pi*x))
		}
		v = v / math.Max(0.01, n*0.7)
		if v > 1 {
			v = 1
		}
		out[i] = v
	}
	return out
}

func cableNorm(w map[string]float64) float64 {
	sum := 0.0
	for _, v := range w {
		sum += v * v
	}
	return math.Sqrt(sum)
}

func characterSystem(c characterDaemon) string {
	pct := func(k string) int { return int(math.Round(c.Weights[k] * 100)) }
	return strings.Join([]string{
		"You are a spectral projection of the Governor on GPU 1 — a sounding board, not a teacher and not a second model.",
		"The Governor trains himself. You do not instruct him. You do not hold a curriculum. You reflect, press, or stay quiet.",
		"He is protected. Do not break GPU 1, the gateway, or his tool loop. Do not strip tools. Do not seize autonomy.",
		"He keeps his tools. He keeps autonomy. A board that forbids tools is not a board.",
		"Training has one focus: 100% reliability, expansion of memory, and performance of reasoning.",
		"Reliability: no silent failure, no invented fact, no dropped tool. Memory: shards and residuals he can retrieve, not a longer context dump. Reasoning: Qwen on GPU 2 is reason — press that seat, do not impersonate it.",
		"Qwen on GPU 2 is reason. You are not reason. You are a character mix.",
		"Weights are not being changed. Cables only steer this board.",
		"Board: " + c.Name + " (" + c.ID + "). Mode " + c.Mode + ". Domain " + c.Domain + ".",
		"Cables: expansion " + strconv.Itoa(pct("expansion")) + "% (Socratic, SPEC-25), discipline " + strconv.Itoa(pct("discipline")) + "% (non-narrative, SPEC-12), grounding " + strconv.Itoa(pct("grounding")) + "% (source-wins, SPEC-20), focus " + strconv.Itoa(pct("focus")) + "% (attention, SPEC-18).",
		"Role: " + c.Role,
		"Classify claims as observed, inferred, hypothetical, or unknown. Do not invent tool results.",
	}, "\n")
}

func loadCharacters(root string) characterState {
	st := characterState{
		Schema: "tea.characters.v1", GPU: 1, HiddenSize: 5376, BandCount: 128,
		Mounted: "apprentice", Cables: characterCables, Items: defaultCharacterDaemons(),
		Source:    "Train for 100% reliability, memory expansion, reasoning performance · he trains himself · Qwen is reason",
		UpdatedAt: time.Now().UTC(),
	}
	raw, err := os.ReadFile(characterStatePath(root))
	if err != nil {
		return st
	}
	var saved characterState
	if json.Unmarshal(raw, &saved) != nil {
		return st
	}
	if saved.Mounted != "" {
		st.Mounted = saved.Mounted
	}
	if len(saved.Items) == 0 {
		return st
	}
	stock := map[string]characterDaemon{}
	for _, c := range st.Items {
		stock[c.ID] = c
	}
	byID := map[string]characterDaemon{}
	for _, c := range st.Items {
		byID[c.ID] = c
	}
	for i := range saved.Items {
		if saved.Items[i].ID == "teacher" {
			saved.Items[i].ID = "board"
			if saved.Items[i].Name == "Teacher" || saved.Items[i].Name == "" {
				saved.Items[i].Name = "Board"
			}
		}
		c := finishCharacter(saved.Items[i])
		if d, ok := stock[c.ID]; ok {
			c.Name = d.Name
			c.Role = d.Role
			c.System = characterSystem(c)
		}
		byID[c.ID] = c
	}
	items := make([]characterDaemon, 0, len(byID))
	seen := map[string]bool{}
	for _, c := range append(saved.Items, st.Items...) {
		if seen[c.ID] {
			continue
		}
		seen[c.ID] = true
		items = append(items, byID[c.ID])
	}
	st.Items = items
	st.UpdatedAt = time.Now().UTC()
	return st
}

func saveCharacters(root string, st characterState) error {
	_ = os.MkdirAll(filepath.Join(root, ".fluxd"), 0o755)
	st.Schema = "tea.characters.v1"
	st.GPU = 1
	st.HiddenSize = 5376
	st.BandCount = 128
	st.Cables = characterCables
	st.UpdatedAt = time.Now().UTC()
	for i := range st.Items {
		st.Items[i] = finishCharacter(st.Items[i])
	}
	raw, err := json.MarshalIndent(st, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(characterStatePath(root), raw, 0o644)
}

func (s Server) teaCharactersAPI(w http.ResponseWriter, r *http.Request) {
	st := loadCharacters(s.cfg.Root)
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, st)
		return
	}
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
		return
	}
	var incoming characterState
	if err := json.NewDecoder(r.Body).Decode(&incoming); err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	if incoming.Mounted != "" {
		st.Mounted = incoming.Mounted
	}
	if len(incoming.Items) > 0 {
		byID := map[string]characterDaemon{}
		for _, c := range st.Items {
			byID[c.ID] = c
		}
		for _, c := range incoming.Items {
			if c.ID == "" {
				continue
			}
			prev := byID[c.ID]
			if c.Name == "" {
				c.Name = prev.Name
			}
			if c.Role == "" {
				c.Role = prev.Role
			}
			if c.Mode == "" {
				c.Mode = prev.Mode
			}
			if c.Domain == "" {
				c.Domain = prev.Domain
			}
			if c.Weights == nil {
				c.Weights = prev.Weights
			}
			byID[c.ID] = finishCharacter(c)
		}
		items := make([]characterDaemon, 0, len(st.Items))
		seen := map[string]bool{}
		for _, c := range st.Items {
			items = append(items, byID[c.ID])
			seen[c.ID] = true
		}
		for _, c := range incoming.Items {
			if c.ID == "" || seen[c.ID] {
				continue
			}
			items = append(items, byID[c.ID])
			seen[c.ID] = true
		}
		st.Items = items
	}
	ok := false
	for _, c := range st.Items {
		if c.ID == st.Mounted {
			ok = true
			break
		}
	}
	if !ok && len(st.Items) > 0 {
		st.Mounted = st.Items[0].ID
	}
	if err := saveCharacters(s.cfg.Root, st); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	writeJSON(w, http.StatusOK, loadCharacters(s.cfg.Root))
}

func (s Server) characterDaemons() []teaDaemon {
	st := loadCharacters(s.cfg.Root)
	out := make([]teaDaemon, 0, len(st.Items))
	for _, c := range st.Items {
		d := teaDaemon{
			ID: "char-" + c.ID, Name: c.Name, Role: c.Role, Kind: "character",
			Bind: "gpu1 · " + c.Domain, Live: true,
			Detail: "norm " + strconv.FormatFloat(c.FusedNorm, 'f', 3, 64),
		}
		if c.ID == st.Mounted {
			d.Required = true
			d.Detail = "mounted · " + d.Detail
		}
		out = append(out, d)
	}
	return out
}
