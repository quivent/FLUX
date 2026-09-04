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
	case "", "fashion", "celadon", "still-life", "still_life", "gpu3", "fp8":
		return "fashion"
	default:
		if reservedProtocolBranches[lane] {
			return "fashion"
		}
		if protocolBranchSlugPattern.MatchString(lane) {
			return lane
		}
		return "fashion"
	}
}

func (s Server) juryDirForLane(lane string) string {
	switch strings.ToLower(strings.TrimSpace(lane)) {
	case "arcane":
		return s.arcaneOutputDir()
	case "microgreens":
		return filepath.Join(s.cfg.OutputDir, "collections", "microgreens")
	case "fashion", "":
		return s.cfg.OutputDir
	default:
		return filepath.Join(s.cfg.OutputDir, "collections", lane)
	}
}

func (s Server) juryLaneButtons() []map[string]any {
	out := []map[string]any{
		{"id": "fashion", "label": "Fashion · GPU 3 FP8", "kind": "builtin"},
		{"id": "microgreens", "label": "Microgreens · GPU 0", "kind": "builtin"},
		{"id": "arcane", "label": "Arcane · GPU 0", "kind": "builtin"},
	}
	seen := map[string]bool{"fashion": true, "microgreens": true, "arcane": true}
	for _, b := range s.listProtocolBranches() {
		slug := stringValue(b["slug"])
		if slug == "" || seen[slug] {
			continue
		}
		seen[slug] = true
		label := strings.ReplaceAll(slug, "-", " ")
		if status := stringValue(b["status"]); status != "" {
			label = label + " · " + status
		} else {
			label = label + " · collection"
		}
		out = append(out, map[string]any{
			"id":     slug,
			"label":  label,
			"kind":   "collection",
			"status": b["status"],
			"wall":   b["wall"],
		})
	}
	return out
}
