package server

import (
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"

	"local/flux/internal/jury"
)

func (s Server) arcaneOutputDir() string {
	return filepath.Join(s.cfg.OutputDir, "arcane")
}

func (s Server) arcaneStudioPage(w http.ResponseWriter, r *http.Request) {
	http.Redirect(w, r, "/jury", http.StatusFound)
}

func (s Server) arcaneProtocolAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
		return
	}
	state := map[string]any{}
	raw, err := os.ReadFile(filepath.Join(s.cfg.Root, ".fluxd", "arcane_stream.json"))
	if err == nil {
		_ = json.Unmarshal(raw, &state)
	}
	cfg, _ := jury.GetConfig(s.arcaneOutputDir())
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":     true,
		"studio": "arcane-atlas",
		"gpu":    0,
		"wall":   "/collections/arcane",
		"stream": state,
		"jury":   cfg,
		"note":   "Independent of the fashion beauty study on GPU 3.",
	})
}

func (s Server) arcaneJuryConfigAPI(w http.ResponseWriter, r *http.Request) {
	dir := s.arcaneOutputDir()
	_ = os.MkdirAll(dir, 0755)
	if r.Method == http.MethodPost {
		var cfg jury.JuryConfig
		if err := json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&cfg); err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		if err := jury.SaveConfig(dir, cfg); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		saved, _ := jury.GetConfig(dir)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "config": saved})
		return
	}
	cfg, err := jury.GetConfig(dir)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "config": cfg})
}
