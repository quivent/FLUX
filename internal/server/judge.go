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
	switch lane {
	case "arcane":
		return "arcane"
	case "microgreens":
		return "microgreens"
	default:
		return "fashion"
	}
}

func (s Server) juryDirForLane(lane string) string {
	switch strings.ToLower(strings.TrimSpace(lane)) {
	case "arcane":
		return s.arcaneOutputDir()
	case "microgreens":
		return filepath.Join(s.cfg.OutputDir, "collections", "microgreens")
	default:
		return s.cfg.OutputDir
	}
}
