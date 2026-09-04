package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestBuiltinStudiosAreDisjoint(t *testing.T) {
	fashion, ok := studioBySlug("fashion")
	if !ok || fashion.GPU != 3 || fashion.Branch {
		t.Fatalf("fashion studio %+v", fashion)
	}
	greens, ok := studioBySlug("microgreens")
	if !ok || greens.GPU != 0 || !greens.Branch || greens.Wall != "/collections/microgreens" {
		t.Fatalf("microgreens studio %+v", greens)
	}
	if fashion.Socket == greens.Socket || fashion.Wall == greens.Wall || fashion.Scope == greens.Scope {
		t.Fatal("fashion and microgreens share a wall or GPU socket")
	}
}

func TestStudiosAPIListsFashionAndMicrogreens(t *testing.T) {
	s := Server{cfg: config.Config{Root: t.TempDir(), OutputDir: t.TempDir()}}
	rec := httptest.NewRecorder()
	s.studiosAPI(rec, httptest.NewRequest(http.MethodGet, "/api/studios", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	var payload struct {
		Studios []struct {
			Slug string `json:"slug"`
			Wall string `json:"wall"`
			GPU  int    `json:"gpu"`
		} `json:"studios"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if len(payload.Studios) != 2 {
		t.Fatalf("studios %+v", payload.Studios)
	}
	got := payload.Studios[0].Slug + payload.Studios[1].Slug
	if !strings.Contains(got, "fashion") || !strings.Contains(got, "microgreens") {
		t.Fatalf("missing studios %s", got)
	}
}

func TestStudioStopWritesPause(t *testing.T) {
	root := t.TempDir()
	s := Server{cfg: config.Config{Root: root, OutputDir: t.TempDir(), Python: "/bin/true"}}
	st, _ := studioBySlug("fashion")
	rec := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/studios/fashion", strings.NewReader(`{"action":"stop"}`))
	s.controlStudio(rec, req, st)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	if !s.studioPaused("fashion") {
		t.Fatal("expected pause file")
	}
}

func TestRecentImagesKeepsMicrogreensOffFashion(t *testing.T) {
	if recentAssetAllowed("fashion", "collections/microgreens/protocol-microgreens-001.png") {
		t.Fatal("fashion wall listed a microgreens frame")
	}
	if !recentAssetAllowed("microgreens", "collections/microgreens/protocol-microgreens-001.png") {
		t.Fatal("microgreens wall missing its own frame")
	}
	if recentAssetAllowed("microgreens", "protocol-fashion-stream-001.png") {
		t.Fatal("microgreens wall listed fashion")
	}
}
