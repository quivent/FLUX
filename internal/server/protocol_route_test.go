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

func TestProtocolRouteAPI(t *testing.T) {
	root := t.TempDir()
	fluxd := filepath.Join(root, ".fluxd")
	_ = os.MkdirAll(fluxd, 0o755)
	_ = os.WriteFile(filepath.Join(fluxd, "jury_route.json"), []byte(`{"station":"pixtral","image":"x.png","ts":1}`), 0o644)
	s := Server{cfg: config.Config{Root: root, OutputDir: t.TempDir()}}
	rec := httptest.NewRecorder()
	s.protocolRouteAPI(rec, httptest.NewRequest(http.MethodGet, "/api/protocol/route?scope=silken-horses", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ok"] != true {
		t.Fatalf("payload %+v", payload)
	}
	if _, ok := payload["stations"]; !ok {
		t.Fatal("missing stations")
	}
}

func TestLatestAuditPrefersNewestTimestamp(t *testing.T) {
	out := t.TempDir()
	col := filepath.Join(out, "collections", "silken-horses")
	if err := os.MkdirAll(col, 0o755); err != nil {
		t.Fatal(err)
	}
	old := `{"ts":10,"image_path":"/x/collections/silken-horses/old.png","curved_score":14.2,"tier":"standard"}` + "\n"
	newer := `{"ts":50,"image_path":"/x/collections/silken-horses/new.png","curved_score":90.6,"tier":"spectacle"}` + "\n"
	if err := os.WriteFile(filepath.Join(out, "audit.jsonl"), []byte(old), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(col, "audit.jsonl"), []byte(newer), 0o644); err != nil {
		t.Fatal(err)
	}
	recs := latestAuditForScope(out, "silken-horses", 12)
	if len(recs) != 2 {
		t.Fatalf("got %d recs", len(recs))
	}
	last := recs[len(recs)-1]
	if last["image"] != "new.png" {
		t.Fatalf("last image %+v", last)
	}
}

func TestGalleryBoardIsOnTheWall(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		`id="board"`, `id="flow"`, `/api/protocol/route`, `write through`, `id="b-adv"`,
		`<h2>Jury</h2>`, `id="boardParams"`, `boardLane()`,
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("gallery board missing %q", tok)
		}
	}
}
