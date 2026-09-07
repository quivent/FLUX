package server

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
)

func (s Server) relativeBeautyDir() string {
	return filepath.Join(s.cfg.OutputDir, "collections", "relative-beauty")
}

func readJSONObject(path string) map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]any{}
	}
	var value map[string]any
	if json.Unmarshal(data, &value) != nil || value == nil {
		return map[string]any{}
	}
	return value
}

func (s Server) relativeBeautyStateAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	dir := s.relativeBeautyDir()
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"control": readJSONObject(filepath.Join(dir, "control.json")),
		"jury":    readJSONObject(filepath.Join(dir, "jury-runtime.json")),
		"worker":  readJSONObject(filepath.Join(dir, "worker-runtime.json")),
	})
}

func (s Server) relativeBeautyControlAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodGet {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "control": readJSONObject(filepath.Join(s.relativeBeautyDir(), "control.json")),
		})
		return
	}
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
		return
	}
	var next map[string]any
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 64<<10)).Decode(&next); err != nil {
		writeError(w, http.StatusBadRequest, "invalid control document")
		return
	}
	if next == nil {
		writeError(w, http.StatusBadRequest, "control document must be an object")
		return
	}
	dir := s.relativeBeautyDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	data, _ := json.MarshalIndent(next, "", "  ")
	data = append(data, '\n')
	tmp := filepath.Join(dir, ".control.json.tmp")
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := os.Rename(tmp, filepath.Join(dir, "control.json")); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "control": next})
}
