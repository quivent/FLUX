package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestDaemonsPageIsTheRoster(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "daemons.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		"Daemons — Tea",
		"/api/tea/daemons",
		"/api/tea/daemons/events",
		"/api/tea/characters",
		"Character · GPU 1",
		"Protected",
		"Sentinel",
		`class="tea-chrome"`,
		`href="/tea/tea.css"`,
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("daemons page missing %q", tok)
		}
	}
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.daemonsPage(rec, httptest.NewRequest(http.MethodGet, "/daemons", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("/daemons status %d", rec.Code)
	}
	rec = httptest.NewRecorder()
	s.daemonsPage(rec, httptest.NewRequest(http.MethodGet, "/daemons/", nil))
	if rec.Code != http.StatusPermanentRedirect {
		t.Errorf("/daemons/ status %d", rec.Code)
	}
}

func TestTeaDaemonsAPIIncludesSentinelWatchingTheRoster(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.teaDaemonsAPI(rec, httptest.NewRequest(http.MethodGet, "/api/tea/daemons", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	var snap teaDaemonsSnapshot
	if err := json.Unmarshal(rec.Body.Bytes(), &snap); err != nil {
		t.Fatal(err)
	}
	if snap.Schema != "tea.daemons.v1" {
		t.Fatalf("schema %s", snap.Schema)
	}
	if len(snap.Daemons) < 8 {
		t.Fatalf("wanted a bunch of daemons, got %d", len(snap.Daemons))
	}
	var sentinel *teaDaemon
	for i := range snap.Daemons {
		if snap.Daemons[i].ID == "sentinel" {
			sentinel = &snap.Daemons[i]
			break
		}
	}
	if sentinel == nil || !sentinel.Live || !sentinel.Watch {
		t.Fatalf("sentinel missing or not watching: %+v", sentinel)
	}
	if snap.Summary["total"] != len(snap.Daemons) {
		t.Fatalf("summary total %v vs %d daemons", snap.Summary["total"], len(snap.Daemons))
	}
}
