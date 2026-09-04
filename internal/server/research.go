package server

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

func researchRoot(s Server) string {
	return filepath.Join(s.cfg.Root, "apps", "tea", "public", "research")
}

func (s Server) researchPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	public := filepath.Join(s.cfg.Root, "apps", "tea", "public")
	rel := strings.TrimPrefix(r.URL.Path, "/research")
	rel = strings.TrimPrefix(rel, "/")
	if rel == "" || rel == "index.html" {
		http.ServeFile(w, r, filepath.Join(public, "research.html"))
		return
	}
	clean := filepath.Clean("/" + rel)
	if strings.Contains(clean, "..") {
		http.NotFound(w, r)
		return
	}
	file := filepath.Join(public, "research", filepath.FromSlash(strings.TrimPrefix(clean, "/")))
	if !strings.HasPrefix(file, filepath.Join(public, "research")+string(os.PathSeparator)) && file != filepath.Join(public, "research") {
		http.NotFound(w, r)
		return
	}
	if info, err := os.Stat(file); err == nil && !info.IsDir() {
		http.ServeFile(w, r, file)
		return
	}
	http.ServeFile(w, r, filepath.Join(public, "research.html"))
}

func (s Server) researchAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	indexPath := filepath.Join(researchRoot(s), "index.json")
	raw, err := os.ReadFile(indexPath)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":      true,
			"papers":  []any{},
			"message": "collection not assembled yet",
		})
		return
	}
	var payload any
	if json.Unmarshal(raw, &payload) != nil {
		writeError(w, http.StatusInternalServerError, "research index unreadable")
		return
	}
	writeJSON(w, http.StatusOK, payload)
}
