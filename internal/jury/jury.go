package jury

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"time"

	_ "modernc.org/sqlite"
)

type JuryConfig struct {
	ID        string             `json:"id"`
	Mode      string             `json:"mode"` // "parallel" or "sequential"
	Order     []string           `json:"order"`
	Weights   map[string]float64 `json:"weights"`
	UpdatedAt int64              `json:"updated_at"`
	R2Synced  bool               `json:"r2_synced"`
}

type JuryPreset struct {
	Name        string             `json:"name"`
	Description string             `json:"description"`
	Mode        string             `json:"mode"`
	Order       []string           `json:"order"`
	Weights     map[string]float64 `json:"weights"`
	CreatedAt   int64              `json:"created_at"`
}

var (
	dbMu sync.Mutex
	db   *sql.DB
)

func DefaultConfig() JuryConfig {
	return JuryConfig{
		ID:        "active",
		Mode:      "parallel",
		Order:     []string{"pixtral", "qwen", "decoder", "governor"},
		Weights: map[string]float64{
			"pixtral":  0.35,
			"qwen":     0.35,
			"decoder":  0.15,
			"governor": 0.15,
		},
		UpdatedAt: time.Now().Unix(),
		R2Synced:  false,
	}
}

func BuiltinPresets() []JuryPreset {
	return []JuryPreset{
		{
			Name:        "Balanced Harmony (Default)",
			Description: "Equal artistic and structural evaluation with balanced governance.",
			Mode:        "parallel",
			Order:       []string{"pixtral", "qwen", "decoder", "governor"},
			Weights:     map[string]float64{"pixtral": 0.35, "qwen": 0.35, "decoder": 0.15, "governor": 0.15},
			CreatedAt:   time.Now().Unix(),
		},
		{
			Name:        "Anatomical Strict (Qwen Heavy)",
			Description: "Prioritizes zero geometrical deformations, sharp contours, and line stability.",
			Mode:        "parallel",
			Order:       []string{"qwen", "pixtral", "decoder", "governor"},
			Weights:     map[string]float64{"qwen": 0.50, "pixtral": 0.25, "governor": 0.15, "decoder": 0.10},
			CreatedAt:   time.Now().Unix(),
		},
		{
			Name:        "Artistic Colorist (Pixtral Heavy)",
			Description: "Prioritizes palette mood, lighting temperature, and medium authenticity.",
			Mode:        "parallel",
			Order:       []string{"pixtral", "qwen", "decoder", "governor"},
			Weights:     map[string]float64{"pixtral": 0.55, "qwen": 0.20, "governor": 0.15, "decoder": 0.10},
			CreatedAt:   time.Now().Unix(),
		},
		{
			Name:        "Semantic Faithful (Governor Heavy)",
			Description: "Strict prompt item adherence and macro compositional compliance.",
			Mode:        "parallel",
			Order:       []string{"governor", "decoder", "pixtral", "qwen"},
			Weights:     map[string]float64{"governor": 0.50, "pixtral": 0.20, "qwen": 0.20, "decoder": 0.10},
			CreatedAt:   time.Now().Unix(),
		},
		{
			Name:        "Cascade Pipeline (Sequential)",
			Description: "Sequential pipeline: Structural inspection passes evidence to Art Critic, then Synthesizer.",
			Mode:        "sequential",
			Order:       []string{"qwen", "pixtral", "decoder", "governor"},
			Weights:     map[string]float64{"qwen": 0.35, "pixtral": 0.35, "decoder": 0.15, "governor": 0.15},
			CreatedAt:   time.Now().Unix(),
		},
	}
}

func InitDB(outputDir string) (*sql.DB, error) {
	dbMu.Lock()
	defer dbMu.Unlock()

	if db != nil {
		return db, nil
	}

	dbPath := filepath.Join(outputDir, "jury.sqlite3")
	_ = os.MkdirAll(outputDir, 0755)

	var err error
	db, err = sql.Open("sqlite", dbPath)
	if err != nil {
		return nil, fmt.Errorf("open sqlite %s: %w", dbPath, err)
	}

	queries := []string{
		`CREATE TABLE IF NOT EXISTS jury_config (
			id TEXT PRIMARY KEY,
			mode TEXT NOT NULL,
			order_json TEXT NOT NULL,
			weights_json TEXT NOT NULL,
			updated_at INTEGER NOT NULL,
			r2_synced INTEGER DEFAULT 0
		);`,
		`CREATE TABLE IF NOT EXISTS jury_presets (
			name TEXT PRIMARY KEY,
			description TEXT,
			mode TEXT NOT NULL,
			order_json TEXT NOT NULL,
			weights_json TEXT NOT NULL,
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
	}
	for _, q := range queries {
		if _, err := db.Exec(q); err != nil {
			return nil, fmt.Errorf("init sqlite schema: %w", err)
		}
	}

	// Seed default config if empty
	var count int
	_ = db.QueryRow("SELECT COUNT(*) FROM jury_config").Scan(&count)
	if count == 0 {
		def := DefaultConfig()
		ord, _ := json.Marshal(def.Order)
		wei, _ := json.Marshal(def.Weights)
		_, _ = db.Exec("INSERT INTO jury_config (id, mode, order_json, weights_json, updated_at, r2_synced) VALUES (?, ?, ?, ?, ?, ?)",
			def.ID, def.Mode, string(ord), string(wei), def.UpdatedAt, 0)
	}

	// Seed builtin presets if empty
	var pCount int
	_ = db.QueryRow("SELECT COUNT(*) FROM jury_presets").Scan(&pCount)
	if pCount == 0 {
		for _, p := range BuiltinPresets() {
			ord, _ := json.Marshal(p.Order)
			wei, _ := json.Marshal(p.Weights)
			_, _ = db.Exec("INSERT OR IGNORE INTO jury_presets (name, description, mode, order_json, weights_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
				p.Name, p.Description, p.Mode, string(ord), string(wei), p.CreatedAt)
		}
	}

	// Also write JSON fallback for quick daemon reads
	_ = ExportConfigJSON(outputDir)

	return db, nil
}

func GetConfig(outputDir string) (JuryConfig, error) {
	d, err := InitDB(outputDir)
	if err != nil {
		return DefaultConfig(), err
	}

	var cfg JuryConfig
	var ordStr, weiStr string
	var synced int
	err = d.QueryRow("SELECT id, mode, order_json, weights_json, updated_at, r2_synced FROM jury_config WHERE id = 'active'").
		Scan(&cfg.ID, &cfg.Mode, &ordStr, &weiStr, &cfg.UpdatedAt, &synced)
	if err != nil {
		return DefaultConfig(), nil
	}

	_ = json.Unmarshal([]byte(ordStr), &cfg.Order)
	_ = json.Unmarshal([]byte(weiStr), &cfg.Weights)
	cfg.R2Synced = (synced == 1)
	return cfg, nil
}

func SaveConfig(outputDir string, cfg JuryConfig) error {
	d, err := InitDB(outputDir)
	if err != nil {
		return err
	}

	cfg.ID = "active"
	cfg.UpdatedAt = time.Now().Unix()
	cfg.R2Synced = false

	ordStr, _ := json.Marshal(cfg.Order)
	weiStr, _ := json.Marshal(cfg.Weights)

	_, err = d.Exec(`
		INSERT INTO jury_config (id, mode, order_json, weights_json, updated_at, r2_synced)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(id) DO UPDATE SET
			mode = excluded.mode,
			order_json = excluded.order_json,
			weights_json = excluded.weights_json,
			updated_at = excluded.updated_at,
			r2_synced = excluded.r2_synced
	`, cfg.ID, cfg.Mode, string(ordStr), string(weiStr), cfg.UpdatedAt, 0)
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

	rows, err := d.Query("SELECT name, description, mode, order_json, weights_json, created_at FROM jury_presets ORDER BY created_at ASC")
	if err != nil {
		return BuiltinPresets(), nil
	}
	defer rows.Close()

	var presets []JuryPreset
	for rows.Next() {
		var p JuryPreset
		var ordStr, weiStr string
		if err := rows.Scan(&p.Name, &p.Description, &p.Mode, &ordStr, &weiStr, &p.CreatedAt); err == nil {
			_ = json.Unmarshal([]byte(ordStr), &p.Order)
			_ = json.Unmarshal([]byte(weiStr), &p.Weights)
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
	ordStr, _ := json.Marshal(p.Order)
	weiStr, _ := json.Marshal(p.Weights)

	_, err = d.Exec(`
		INSERT INTO jury_presets (name, description, mode, order_json, weights_json, created_at)
		VALUES (?, ?, ?, ?, ?, ?)
		ON CONFLICT(name) DO UPDATE SET
			description = excluded.description,
			mode = excluded.mode,
			order_json = excluded.order_json,
			weights_json = excluded.weights_json,
			created_at = excluded.created_at
	`, p.Name, p.Description, p.Mode, string(ordStr), string(weiStr), p.CreatedAt)
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
