package server

import (
	"net/http"
	"path/filepath"
	"strings"
)

func (s Server) judgePage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	if r.URL.Path == "/judge/" || r.URL.Path == "/algorithm/" {
		http.Redirect(w, r, "/judge", http.StatusPermanentRedirect)
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/")
	if path != "/judge" && path != "/algorithm" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "judge.html"))
}

func requestJuryLane(r *http.Request, bodyLane string) string {
	lane := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("lane")))
	if lane == "" {
		lane = strings.ToLower(strings.TrimSpace(bodyLane))
	}
	if lane == "arcane" {
		return "arcane"
	}
	return "fashion"
}

func (s Server) juryDirForLane(lane string) string {
	if strings.EqualFold(strings.TrimSpace(lane), "arcane") {
		return s.arcaneOutputDir()
	}
	return s.cfg.OutputDir
}
