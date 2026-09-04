package jury

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	SeatPixtral  = "pixtral"
	SeatQwen     = "qwen"
	SeatDecoder  = "decoder"
	SeatGovernor = "governor"

	ServedWitness = "visual-witness"
	ServedPixtral = "pixtral-critic"
	ServedGovernor = "governor"
)

var legacySeats = []string{SeatPixtral, SeatQwen, SeatDecoder, SeatGovernor}

type algorithmBlob struct {
	Endpoints      map[string]JuryEndpoint `json:"endpoints,omitempty"`
	MinJudges      int                     `json:"min_judges,omitempty"`
	TextFromGates  bool                    `json:"text_from_gates"`
	UniquenessInfl *bool                   `json:"uniqueness_influence,omitempty"`
	GateTriage     *bool                   `json:"gate_triage,omitempty"`
}

func algorithmBlobFrom(cfg JuryConfig) algorithmBlob {
	return algorithmBlob{
		Endpoints:      cfg.Endpoints,
		MinJudges:      cfg.MinJudges,
		TextFromGates:  cfg.TextFromGates,
		UniquenessInfl: cfg.UniquenessInfl,
		GateTriage:     cfg.GateTriage,
	}
}

func applyAlgorithmJSON(cfg *JuryConfig, raw string) {
	var blob algorithmBlob
	if json.Unmarshal([]byte(raw), &blob) != nil {
		return
	}
	if blob.Endpoints != nil {
		cfg.Endpoints = blob.Endpoints
	}
	if blob.MinJudges > 0 {
		cfg.MinJudges = blob.MinJudges
	}
	cfg.TextFromGates = blob.TextFromGates
	cfg.UniquenessInfl = blob.UniquenessInfl
	cfg.GateTriage = blob.GateTriage
}

// MergeConfig overlays incoming operator fields onto the persisted config.
// Empty endpoint maps and zero min_judges keep the stored algorithm so a
// weights-only save from an older UI does not unbind live seats.
func MergeConfig(existing, incoming JuryConfig) JuryConfig {
	out := existing
	if incoming.Mode == "parallel" || incoming.Mode == "sequential" {
		out.Mode = incoming.Mode
	}
	if len(incoming.Order) > 0 {
		out.Order = incoming.Order
	}
	if incoming.Weights != nil {
		out.Weights = incoming.Weights
	}
	if incoming.Strictness != nil {
		out.Strictness = incoming.Strictness
	}
	out.AdversarialMode = incoming.AdversarialMode
	if len(incoming.Endpoints) > 0 {
		if out.Endpoints == nil {
			out.Endpoints = map[string]JuryEndpoint{}
		}
		for key, ep := range incoming.Endpoints {
			out.Endpoints[key] = mergeEndpoint(out.Endpoints[key], ep)
		}
	}
	if incoming.MinJudges > 0 {
		out.MinJudges = incoming.MinJudges
	}
	out.TextFromGates = incoming.TextFromGates
	if incoming.UniquenessInfl != nil {
		out.UniquenessInfl = incoming.UniquenessInfl
	}
	if incoming.GateTriage != nil {
		out.GateTriage = incoming.GateTriage
	}
	return out
}

func mergeEndpoint(prev, next JuryEndpoint) JuryEndpoint {
	out := prev
	if strings.TrimSpace(next.BaseURL) != "" {
		out.BaseURL = strings.TrimSpace(next.BaseURL)
	}
	if strings.TrimSpace(next.Model) != "" {
		out.Model = strings.TrimSpace(next.Model)
	}
	if next.Enabled != nil {
		out.Enabled = next.Enabled
	}
	if next.Vision != nil {
		out.Vision = next.Vision
	}
	return out
}

func NormalizeConfig(cfg *JuryConfig) {
	if cfg.Mode != "sequential" {
		cfg.Mode = "parallel"
	}
	if len(cfg.Order) == 0 {
		cfg.Order = append([]string{}, DefaultConfig().Order...)
	}
	if cfg.Weights == nil {
		cfg.Weights = map[string]float64{}
	}
	if cfg.Strictness == nil {
		cfg.Strictness = map[string]float64{}
	}
	def := DefaultConfig()
	sum := 0.0
	for _, seat := range legacySeats {
		if _, ok := cfg.Weights[seat]; !ok {
			cfg.Weights[seat] = def.Weights[seat]
		}
		if cfg.Weights[seat] < 0 {
			cfg.Weights[seat] = 0
		}
		sum += cfg.Weights[seat]
		g := cfg.Strictness[seat]
		if g == 0 {
			g = def.Strictness[seat]
		}
		if g < 1.0 {
			g = 1.0
		}
		if g > 3.0 {
			g = 3.0
		}
		cfg.Strictness[seat] = g
	}
	if sum <= 0 {
		cfg.Weights = def.Weights
	} else {
		for _, seat := range legacySeats {
			cfg.Weights[seat] = cfg.Weights[seat] / sum
		}
	}
	if cfg.MinJudges < 1 {
		cfg.MinJudges = 1
	}
	if cfg.MinJudges > 3 {
		cfg.MinJudges = 3
	}
}

func BoolPtr(v bool) *bool { return &v }

// CalibrationRecord is one hive (or heuristic) proposal for the judging algorithm.
type CalibrationRecord struct {
	TS         int64      `json:"ts"`
	Source     string     `json:"source"`
	Endpoint   string     `json:"endpoint,omitempty"`
	Model      string     `json:"model,omitempty"`
	Applied    bool       `json:"applied"`
	Note       string     `json:"note,omitempty"`
	Diagnosis  string     `json:"diagnosis,omitempty"`
	Rationale  string     `json:"rationale,omitempty"`
	Proposal   JuryConfig `json:"proposal"`
	Audit      any        `json:"audit,omitempty"`
	Error      string     `json:"error,omitempty"`
	RawSnippet string     `json:"raw_snippet,omitempty"`
}

func calibrationPath(outputDir string) string {
	return filepath.Join(outputDir, "jury_calibration.json")
}

func calibrationLogPath(outputDir string) string {
	return filepath.Join(outputDir, "jury_calibration.jsonl")
}

func SaveCalibration(outputDir string, rec CalibrationRecord) error {
	if rec.TS == 0 {
		rec.TS = time.Now().Unix()
	}
	_ = os.MkdirAll(outputDir, 0755)
	data, err := json.MarshalIndent(rec, "", "  ")
	if err != nil {
		return err
	}
	if err := os.WriteFile(calibrationPath(outputDir), data, 0644); err != nil {
		return err
	}
	line, _ := json.Marshal(rec)
	f, err := os.OpenFile(calibrationLogPath(outputDir), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return nil
	}
	defer f.Close()
	_, _ = f.Write(append(line, '\n'))
	return nil
}

func LatestCalibration(outputDir string) *CalibrationRecord {
	raw, err := os.ReadFile(calibrationPath(outputDir))
	if err != nil {
		return nil
	}
	var rec CalibrationRecord
	if json.Unmarshal(raw, &rec) != nil {
		return nil
	}
	return &rec
}

func RecentCalibrations(outputDir string, limit int) []CalibrationRecord {
	if limit <= 0 {
		limit = 8
	}
	f, err := os.Open(calibrationLogPath(outputDir))
	if err != nil {
		if latest := LatestCalibration(outputDir); latest != nil {
			return []CalibrationRecord{*latest}
		}
		return nil
	}
	defer f.Close()
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		text := strings.TrimSpace(sc.Text())
		if text != "" {
			lines = append(lines, text)
		}
	}
	if len(lines) > limit {
		lines = lines[len(lines)-limit:]
	}
	out := make([]CalibrationRecord, 0, len(lines))
	for _, line := range lines {
		var rec CalibrationRecord
		if json.Unmarshal([]byte(line), &rec) == nil {
			out = append(out, rec)
		}
	}
	return out
}
