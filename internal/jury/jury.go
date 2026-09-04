package jury

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

// JuryEndpoint is one vLLM seat the evaluator actually calls.
// Keys in JuryConfig.Endpoints are served names: visual-witness, pixtral-critic, governor.
type JuryEndpoint struct {
	BaseURL string `json:"base_url,omitempty"`
	Model   string `json:"model,omitempty"`
	Enabled *bool  `json:"enabled,omitempty"`
	Vision  *bool  `json:"vision,omitempty"`
}

type JuryConfig struct {
	ID              string                  `json:"id"`
	Mode            string                  `json:"mode"` // "parallel" or "sequential"
	Order           []string                `json:"order"`
	Weights         map[string]float64      `json:"weights"`
	Strictness      map[string]float64      `json:"strictness"`
	AdversarialMode bool                    `json:"adversarial_mode"`
	UpdatedAt       int64                   `json:"updated_at"`
	R2Synced        bool                    `json:"r2_synced"`
	Endpoints       map[string]JuryEndpoint `json:"endpoints,omitempty"`
	MinJudges       int                     `json:"min_judges,omitempty"`
	TextFromGates   bool                    `json:"text_from_gates"`
	UniquenessInfl  *bool                   `json:"uniqueness_influence,omitempty"`
	GateTriage      *bool                   `json:"gate_triage,omitempty"`
}

type JuryPreset struct {
	Name            string             `json:"name"`
	Description     string             `json:"description"`
	Mode            string             `json:"mode"`
	Order           []string           `json:"order"`
	Weights         map[string]float64 `json:"weights"`
	Strictness      map[string]float64 `json:"strictness"`
	AdversarialMode bool               `json:"adversarial_mode"`
	CreatedAt       int64              `json:"created_at"`
}

var (
	dbMu sync.Mutex
	dbs  = map[string]*sql.DB{}
)

func DefaultConfig() JuryConfig {
	return JuryConfig{
		ID:    "active",
		Mode:  "parallel",
		Order: []string{"pixtral", "qwen", "decoder", "governor"},
		Weights: map[string]float64{
			"pixtral":  0.35,
			"qwen":     0.35,
			"decoder":  0.15,
			"governor": 0.15,
		},
		Strictness: map[string]float64{
			"pixtral":  2.0, // Strict colorist / aesthetic gamma
			"qwen":     1.2, // Naturally critical anatomical scan
			"decoder":  1.5, // Strict representation consensus
			"governor": 2.2, // Rigorous semantic & prompt adherence
		},
		AdversarialMode: true,
		UpdatedAt:       time.Now().Unix(),
		R2Synced:        false,
		MinJudges:       1,
		TextFromGates:   false,
	}
}

func BuiltinPresets() []JuryPreset {
	return []JuryPreset{
		{
			Name:            "Balanced Harmony (Default)",
			Description:     "Equal artistic and structural evaluation with balanced governance.",
			Mode:            "parallel",
			Order:           []string{"pixtral", "qwen", "decoder", "governor"},
			Weights:         map[string]float64{"pixtral": 0.35, "qwen": 0.35, "decoder": 0.15, "governor": 0.15},
			Strictness:      map[string]float64{"pixtral": 1.5, "qwen": 1.2, "decoder": 1.3, "governor": 1.5},
			AdversarialMode: false,
			CreatedAt:       time.Now().Unix(),
		},
		{
			Name:            "Inquisitorial Strict (Museum Grade)",
			Description:     "Unsparing adversarial rubrics; only flawless frames exceed 90.0.",
			Mode:            "parallel",
			Order:           []string{"qwen", "pixtral", "governor", "decoder"},
			Weights:         map[string]float64{"pixtral": 0.35, "qwen": 0.35, "decoder": 0.15, "governor": 0.15},
			Strictness:      map[string]float64{"pixtral": 2.4, "qwen": 2.0, "decoder": 1.8, "governor": 2.5},
			AdversarialMode: true,
			CreatedAt:       time.Now().Unix(),
		},
		{
			Name:            "Anatomical Strict (Qwen Heavy)",
			Description:     "Prioritizes zero geometrical deformations, sharp contours, and line stability.",
			Mode:            "parallel",
			Order:           []string{"qwen", "pixtral", "decoder", "governor"},
			Weights:         map[string]float64{"qwen": 0.50, "pixtral": 0.25, "governor": 0.15, "decoder": 0.10},
			Strictness:      map[string]float64{"pixtral": 1.5, "qwen": 2.2, "decoder": 1.3, "governor": 1.5},
			AdversarialMode: true,
			CreatedAt:       time.Now().Unix(),
		},
		{
			Name:            "Artistic Colorist (Pixtral Heavy)",
			Description:     "Prioritizes palette mood, lighting temperature, and medium authenticity.",
			Mode:            "parallel",
			Order:           []string{"pixtral", "qwen", "decoder", "governor"},
			Weights:         map[string]float64{"pixtral": 0.55, "qwen": 0.20, "governor": 0.15, "decoder": 0.10},
			Strictness:      map[string]float64{"pixtral": 2.5, "qwen": 1.2, "decoder": 1.4, "governor": 1.6},
			AdversarialMode: true,
			CreatedAt:       time.Now().Unix(),
		},
		{
			Name:            "Semantic Faithful (Governor Heavy)",
			Description:     "Strict prompt item adherence and macro compositional compliance.",
			Mode:            "parallel",
			Order:           []string{"governor", "decoder", "pixtral", "qwen"},
			Weights:         map[string]float64{"governor": 0.50, "pixtral": 0.20, "qwen": 0.20, "decoder": 0.10},
			Strictness:      map[string]float64{"pixtral": 1.4, "qwen": 1.2, "decoder": 1.3, "governor": 2.8},
			AdversarialMode: true,
			CreatedAt:       time.Now().Unix(),
		},
	}
}

func InitDB(outputDir string) (*sql.DB, error) {
	dbMu.Lock()
	defer dbMu.Unlock()

	resolvedDir, err := filepath.EvalSymlinks(outputDir)
	if err == nil {
		outputDir = resolvedDir
	}
	dbPath := filepath.Join(outputDir, "jury.sqlite3")
	if existing := dbs[dbPath]; existing != nil {
		return existing, nil
	}
	_ = os.MkdirAll(outputDir, 0755)

	db, err := sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open sqlite %s: %w", dbPath, err)
	}
	db.SetMaxOpenConns(1)
	_, _ = db.Exec("PRAGMA journal_mode=WAL;")
	_, _ = db.Exec("PRAGMA busy_timeout=5000;")
	dbs[dbPath] = db

	queries := []string{
		`CREATE TABLE IF NOT EXISTS jury_config (
			id TEXT PRIMARY KEY,
			mode TEXT NOT NULL,
			order_json TEXT NOT NULL,
			weights_json TEXT NOT NULL,
			strictness_json TEXT,
			adversarial_mode INTEGER DEFAULT 0,
			updated_at INTEGER NOT NULL,
			r2_synced INTEGER DEFAULT 0,
			algorithm_json TEXT
		);`,
		`CREATE TABLE IF NOT EXISTS jury_presets (
			name TEXT PRIMARY KEY,
			description TEXT,
			mode TEXT NOT NULL,
			order_json TEXT NOT NULL,
			weights_json TEXT NOT NULL,
			strictness_json TEXT,
			adversarial_mode INTEGER DEFAULT 0,
			created_at INTEGER NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS jury_verdicts (
			job_id TEXT PRIMARY KEY,
			seed TEXT,
			prompt TEXT,
			composite_score REAL,
			scores_json TEXT,
			critiques_json TEXT,
			mode TEXT,
			masterpiece INTEGER,
			created_at INTEGER NOT NULL
		);`,
		`CREATE TABLE IF NOT EXISTS human_feedback (
			id INTEGER PRIMARY KEY AUTOINCREMENT,
			job_id TEXT NOT NULL,
			seed TEXT,
			prompt TEXT,
			action TEXT NOT NULL,
			curated_rank INTEGER DEFAULT 0,
			rating INTEGER DEFAULT 5,
			operator TEXT DEFAULT 'human',
			created_at INTEGER NOT NULL
		);`,
	}
	for _, q := range queries {
		if _, err := db.Exec(q); err != nil {
			return nil, fmt.Errorf("init sqlite schema: %w", err)
		}
	}

	_, _ = db.Exec(`CREATE TABLE IF NOT EXISTS visual_fingerprints (
			job_id TEXT PRIMARY KEY,
			filepath TEXT,
			vector_blob BLOB,
			uniqueness_score REAL,
			category TEXT,
			created_at INTEGER
		);`)
	_, _ = db.Exec("ALTER TABLE jury_verdicts ADD COLUMN raw_score REAL;")
	_, _ = db.Exec("ALTER TABLE jury_verdicts ADD COLUMN percentile_rank REAL;")

	// Migrations for existing tables
	_, _ = db.Exec("ALTER TABLE jury_config ADD COLUMN strictness_json TEXT;")
	_, _ = db.Exec("ALTER TABLE jury_config ADD COLUMN adversarial_mode INTEGER DEFAULT 0;")
	_, _ = db.Exec("ALTER TABLE jury_config ADD COLUMN algorithm_json TEXT;")
	_, _ = db.Exec("ALTER TABLE jury_presets ADD COLUMN strictness_json TEXT;")
	_, _ = db.Exec("ALTER TABLE jury_presets ADD COLUMN adversarial_mode INTEGER DEFAULT 0;")

	// Seed default config if empty
	var count int
	_ = db.QueryRow("SELECT COUNT(*) FROM jury_config").Scan(&count)
	if count == 0 {
		def := DefaultConfig()
		ord, _ := json.Marshal(def.Order)
		wei, _ := json.Marshal(def.Weights)
		strc, _ := json.Marshal(def.Strictness)
		adv := 0
		if def.AdversarialMode {
			adv = 1
		}
		_, _ = db.Exec("INSERT INTO jury_config (id, mode, order_json, weights_json, strictness_json, adversarial_mode, updated_at, r2_synced) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			def.ID, def.Mode, string(ord), string(wei), string(strc), adv, def.UpdatedAt, 0)
	}

	// Seed builtin presets
	for _, p := range BuiltinPresets() {
		ord, _ := json.Marshal(p.Order)
		wei, _ := json.Marshal(p.Weights)
		strc, _ := json.Marshal(p.Strictness)
		adv := 0
		if p.AdversarialMode {
			adv = 1
		}
		_, _ = db.Exec("INSERT OR REPLACE INTO jury_presets (name, description, mode, order_json, weights_json, strictness_json, adversarial_mode, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			p.Name, p.Description, p.Mode, string(ord), string(wei), string(strc), adv, p.CreatedAt)
	}

	return db, nil
}

func GetConfig(outputDir string) (JuryConfig, error) {
	d, err := InitDB(outputDir)
	if err != nil {
		return DefaultConfig(), err
	}

	var cfg JuryConfig
	var ordStr, weiStr string
	var strcStr, algoStr sql.NullString
	var advInt, synced int
	err = d.QueryRow("SELECT id, mode, order_json, weights_json, strictness_json, adversarial_mode, updated_at, r2_synced, algorithm_json FROM jury_config WHERE id = 'active'").
		Scan(&cfg.ID, &cfg.Mode, &ordStr, &weiStr, &strcStr, &advInt, &cfg.UpdatedAt, &synced, &algoStr)
	if err != nil {
		return DefaultConfig(), nil
	}

	_ = json.Unmarshal([]byte(ordStr), &cfg.Order)
	_ = json.Unmarshal([]byte(weiStr), &cfg.Weights)
	if strcStr.Valid && strcStr.String != "" {
		_ = json.Unmarshal([]byte(strcStr.String), &cfg.Strictness)
	} else {
		cfg.Strictness = DefaultConfig().Strictness
	}
	cfg.AdversarialMode = (advInt == 1)
	cfg.R2Synced = (synced == 1)
	if algoStr.Valid && algoStr.String != "" {
		applyAlgorithmJSON(&cfg, algoStr.String)
	}
	if cfg.MinJudges <= 0 {
		cfg.MinJudges = 1
	}
	return cfg, nil
}

func SaveConfig(outputDir string, cfg JuryConfig) error {
	d, err := InitDB(outputDir)
	if err != nil {
		return err
	}

	existing, _ := GetConfig(outputDir)
	cfg = MergeConfig(existing, cfg)
	NormalizeConfig(&cfg)

	cfg.ID = "active"
	cfg.UpdatedAt = time.Now().Unix()
	cfg.R2Synced = false

	if cfg.Strictness == nil {
		cfg.Strictness = DefaultConfig().Strictness
	}

	ordStr, _ := json.Marshal(cfg.Order)
	weiStr, _ := json.Marshal(cfg.Weights)
	strcStr, _ := json.Marshal(cfg.Strictness)
	algoStr, _ := json.Marshal(algorithmBlobFrom(cfg))
	adv := 0
	if cfg.AdversarialMode {
		adv = 1
	}

	_, err = d.Exec(`
		INSERT INTO jury_config (id, mode, order_json, weights_json, strictness_json, adversarial_mode, updated_at, r2_synced, algorithm_json)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			mode = excluded.mode,
			order_json = excluded.order_json,
			weights_json = excluded.weights_json,
			strictness_json = excluded.strictness_json,
			adversarial_mode = excluded.adversarial_mode,
			updated_at = excluded.updated_at,
			r2_synced = excluded.r2_synced,
			algorithm_json = excluded.algorithm_json
	`, cfg.ID, cfg.Mode, string(ordStr), string(weiStr), string(strcStr), adv, cfg.UpdatedAt, 0, string(algoStr))
	if err != nil {
		return err
	}

	_ = ExportConfigJSON(outputDir)
	return nil
}

func ListPresets(outputDir string) ([]JuryPreset, error) {
	d, err := InitDB(outputDir)
	if err != nil {
		return BuiltinPresets(), err
	}

	rows, err := d.Query("SELECT name, description, mode, order_json, weights_json, strictness_json, adversarial_mode, created_at FROM jury_presets ORDER BY created_at ASC")
	if err != nil {
		return BuiltinPresets(), nil
	}
	defer rows.Close()

	var presets []JuryPreset
	for rows.Next() {
		var p JuryPreset
		var ordStr, weiStr string
		var strcStr sql.NullString
		var advInt int
		if err := rows.Scan(&p.Name, &p.Description, &p.Mode, &ordStr, &weiStr, &strcStr, &advInt, &p.CreatedAt); err == nil {
			_ = json.Unmarshal([]byte(ordStr), &p.Order)
			_ = json.Unmarshal([]byte(weiStr), &p.Weights)
			if strcStr.Valid && strcStr.String != "" {
				_ = json.Unmarshal([]byte(strcStr.String), &p.Strictness)
			} else {
				p.Strictness = DefaultConfig().Strictness
			}
			p.AdversarialMode = (advInt == 1)
			presets = append(presets, p)
		}
	}
	return presets, nil
}

func SavePreset(outputDir string, p JuryPreset) error {
	d, err := InitDB(outputDir)
	if err != nil {
		return err
	}

	p.CreatedAt = time.Now().Unix()
	if p.Strictness == nil {
		p.Strictness = DefaultConfig().Strictness
	}
	ordStr, _ := json.Marshal(p.Order)
	weiStr, _ := json.Marshal(p.Weights)
	strcStr, _ := json.Marshal(p.Strictness)
	adv := 0
	if p.AdversarialMode {
		adv = 1
	}

	_, err = d.Exec(`
		INSERT INTO jury_presets (name, description, mode, order_json, weights_json, strictness_json, adversarial_mode, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(name) DO UPDATE SET
			description = excluded.description,
			mode = excluded.mode,
			order_json = excluded.order_json,
			weights_json = excluded.weights_json,
			strictness_json = excluded.strictness_json,
			adversarial_mode = excluded.adversarial_mode,
			created_at = excluded.created_at
	`, p.Name, p.Description, p.Mode, string(ordStr), string(weiStr), string(strcStr), adv, p.CreatedAt)
	return err
}

func ExportConfigJSON(outputDir string) error {
	cfg, err := GetConfig(outputDir)
	if err != nil {
		cfg = DefaultConfig()
	}
	data, _ := json.MarshalIndent(cfg, "", "  ")
	return os.WriteFile(filepath.Join(outputDir, "jury_config.json"), data, 0644)
}

func SyncToR2(outputDir string) error {
	dbPath := filepath.Join(outputDir, "jury.sqlite3")
	cmd := exec.Command("gemstone", "r2", "push", dbPath, "state/jury.sqlite3")
	out, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("r2 push failed: %s: %w", string(out), err)
	}

	d, _ := InitDB(outputDir)
	if d != nil {
		_, _ = d.Exec("UPDATE jury_config SET r2_synced = 1 WHERE id = 'active'")
	}
	return nil
}

type SpectacleItem struct {
	JobID          string  `json:"job_id"`
	Seed           string  `json:"seed"`
	Prompt         string  `json:"prompt"`
	CompositeScore float64 `json:"composite_score"`
	RawScore       float64 `json:"raw_score"`
	PercentileRank float64 `json:"percentile_rank"`
	Masterpiece    int     `json:"masterpiece"`
	CreatedAt      int64   `json:"created_at"`
	ImageURL       string  `json:"image_url"`
}

func outputPublicURL(outputDir, file string) string {
	if file == "" {
		return ""
	}
	rel, err := filepath.Rel(outputDir, file)
	if err != nil || strings.HasPrefix(rel, "..") {
		return "/outputs/" + filepath.Base(file)
	}
	return "/outputs/" + filepath.ToSlash(rel)
}

func GetSpectacles(outputDir string, limit int) ([]SpectacleItem, error) {
	d, err := InitDB(outputDir)
	if err != nil {
		return nil, err
	}
	if limit <= 0 {
		limit = 36
	}

	rows, err := d.Query(`
		SELECT v.job_id, v.seed, v.prompt,
			COALESCE(v.composite_score, 0),
			COALESCE(v.raw_score, v.composite_score, 0),
			COALESCE(v.percentile_rank, v.composite_score, 0),
			COALESCE(v.masterpiece, 0), v.created_at,
			COALESCE(f.filepath, '')
		FROM jury_verdicts v
		LEFT JOIN visual_fingerprints f ON f.job_id = v.job_id
		WHERE v.composite_score IS NOT NULL
			AND (v.composite_score >= 90.0 OR v.masterpiece > 0)
		ORDER BY v.composite_score DESC, COALESCE(v.raw_score, 0) DESC, v.created_at DESC
		LIMIT ?
	`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	seenImg := make(map[string]bool)
	var items []SpectacleItem
	for rows.Next() {
		var it SpectacleItem
		var fp string
		if err := rows.Scan(&it.JobID, &it.Seed, &it.Prompt, &it.CompositeScore, &it.RawScore, &it.PercentileRank, &it.Masterpiece, &it.CreatedAt, &fp); err != nil {
			continue
		}
		if fp != "" {
			if _, err := os.Stat(fp); err == nil {
				it.ImageURL = outputPublicURL(outputDir, fp)
			}
		}
		if it.ImageURL == "" {
			pattern1 := filepath.Join(outputDir, fmt.Sprintf("*%s*.png", it.JobID))
			pattern2 := filepath.Join(outputDir, fmt.Sprintf("*seed-%s*.png", it.Seed))
			matches, _ := filepath.Glob(pattern1)
			if len(matches) == 0 {
				matches, _ = filepath.Glob(pattern2)
			}
			if len(matches) > 0 {
				it.ImageURL = outputPublicURL(outputDir, matches[0])
			}
		}
		if it.ImageURL == "" || seenImg[it.ImageURL] {
			continue
		}
		seenImg[it.ImageURL] = true
		items = append(items, it)
	}
	return items, nil
}

type HumanFeedbackRequest struct {
	JobID       string `json:"job_id"`
	Seed        string `json:"seed"`
	Prompt      string `json:"prompt"`
	Action      string `json:"action"` // "crown_masterpiece", "promote_spectacle", "demote", "rerank"
	CuratedRank int    `json:"curated_rank"`
	Rating      int    `json:"rating"`
	Operator    string `json:"operator"`
}

func SaveHumanFeedback(outputDir string, fb HumanFeedbackRequest) error {
	d, err := InitDB(outputDir)
	if err != nil {
		return err
	}
	if fb.Operator == "" {
		fb.Operator = "human"
	}
	now := time.Now().Unix()

	_, err = d.Exec(`
		INSERT INTO human_feedback (job_id, seed, prompt, action, curated_rank, rating, operator, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?)
	`, fb.JobID, fb.Seed, fb.Prompt, fb.Action, fb.CuratedRank, fb.Rating, fb.Operator, now)
	if err != nil {
		return err
	}

	// Dynamic override in jury_verdicts based on human feedback
	if fb.Action == "crown_masterpiece" {
		_, _ = d.Exec(`UPDATE jury_verdicts SET masterpiece = 1, composite_score = 99.0, percentile_rank = 99.5 WHERE job_id = ?`, fb.JobID)
	} else if fb.Action == "promote_spectacle" {
		_, _ = d.Exec(`UPDATE jury_verdicts SET masterpiece = 2, composite_score = 94.0, percentile_rank = 94.5 WHERE job_id = ?`, fb.JobID)
	} else if fb.Action == "demote" {
		_, _ = d.Exec(`UPDATE jury_verdicts SET masterpiece = 0, composite_score = 45.0, percentile_rank = 15.0 WHERE job_id = ?`, fb.JobID)
	}

	// Append to JSONL for genetic loop pickup
	genomeFile := filepath.Join(outputDir, "spectacle_genome.jsonl")
	if fb.Action == "crown_masterpiece" || fb.Action == "promote_spectacle" {
		entry := map[string]any{
			"ts":             now,
			"job_id":         fb.JobID,
			"seed":           fb.Seed,
			"prompt":         fb.Prompt,
			"human_crowned":  true,
			"curated_action": fb.Action,
			"target":         "movement_towards_master",
		}
		data, _ := json.Marshal(entry)
		f, err := os.OpenFile(genomeFile, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err == nil {
			_, _ = f.Write(append(data, '\n'))
			_ = f.Close()
		}
	}
	return nil
}
