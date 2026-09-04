package server

import (
	"net/http"
	"path/filepath"
	"strings"
)

func (s Server) ledgerPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	if r.URL.Path == "/ledger/" {
		http.Redirect(w, r, "/ledger", http.StatusPermanentRedirect)
		return
	}
	if strings.TrimSuffix(r.URL.Path, "/") != "/ledger" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "ledger.html"))
}

func (s Server) ledgerAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	path := filepath.Join(s.cfg.Root, "apps", "tea", "public", "ledger.json")
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	http.ServeFile(w, r, path)
}
