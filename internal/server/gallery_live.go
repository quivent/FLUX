package server

import (
	"net/http"
	"net/url"
	"strings"
)

func galleryPageReferer(r *http.Request) bool {
	if r == nil {
		return false
	}
	raw := r.Referer()
	if raw == "" {
		return false
	}
	u, err := url.Parse(raw)
	if err != nil {
		return false
	}
	p := strings.TrimSuffix(u.Path, "/")
	return p == "/gallery"
}

func (s Server) liveBeautyScope() string {
	best := "silken-horses"
	bestTs := 0.0
	for _, row := range s.listProtocolBranches() {
		slug := stringValue(row["slug"])
		if slug == "" || slug == "microgreens" || slug == "fashion" || slug == "arcane" || slug == "portraits" {
			continue
		}
		if stringValue(row["status"]) == "running" {
			return slug
		}
		st, _ := row["stream"].(map[string]any)
		var ts float64
		ok := false
		if st != nil {
			ts, ok = asFloat(st["updated_at"])
		}
		if ok && ts > bestTs {
			bestTs = ts
			best = slug
		}
	}
	return best
}

func (s Server) galleryLiveScope(r *http.Request, scope string) string {
	scope = strings.ToLower(strings.TrimSpace(scope))
	if !galleryPageReferer(r) {
		return scope
	}
	if scope == "" || scope == "microgreens" || scope == "images" || scope == "fashion" {
		return s.liveBeautyScope()
	}
	if scope == "microgreens" {
		return s.liveBeautyScope()
	}
	return scope
}
