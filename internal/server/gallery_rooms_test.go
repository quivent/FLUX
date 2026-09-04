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

func TestFashionGalleryRejectsArcanePrincessRose(t *testing.T) {
	output := t.TempDir()
	files := map[string]string{
		"protocol-fashion-stream-001.png":      "fashion",
		"protocol-arcane-atlas-001.png":        "arcane-root",
		"arcane/protocol-arcane-atlas-002.png": "arcane-dir",
		"princess-rose.png":                    "vanity",
		"celadon-bowl.png":                     "bowl",
		"finished-work.png":                    "other",
	}
	for rel, body := range files {
		path := filepath.Join(output, rel)
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: output}}
	get := func(scope string) []string {
		rec := httptest.NewRecorder()
		s.recentImages(rec, httptest.NewRequest(http.MethodGet, "/api/recent-images?scope="+scope+"&limit=50", nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("scope %s status %d: %s", scope, rec.Code, rec.Body.String())
		}
		var payload struct {
			Images []struct {
				Name string `json:"name"`
				Path string `json:"path"`
			} `json:"images"`
		}
		if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		names := make([]string, 0, len(payload.Images))
		for _, im := range payload.Images {
			names = append(names, im.Name)
		}
		return names
	}

	fashion := strings.Join(get("fashion"), " ")
	if !strings.Contains(fashion, "protocol-fashion-stream-001.png") {
		t.Fatalf("fashion wall missing the stream: %s", fashion)
	}
	for _, banned := range []string{"protocol-arcane-atlas-001.png", "protocol-arcane-atlas-002.png", "princess-rose.png", "celadon-bowl.png", "finished-work.png"} {
		if strings.Contains(fashion, banned) {
			t.Errorf("fashion wall leaked %s: %s", banned, fashion)
		}
	}

	images := strings.Join(get("images"), " ")
	if strings.Contains(images, "arcane") || strings.Contains(images, "princess") {
		t.Errorf("images scope leaked vanity/arcane: %s", images)
	}

	arcane := strings.Join(get("arcane"), " ")
	if !strings.Contains(arcane, "protocol-arcane-atlas-002.png") {
		t.Fatalf("arcane wall missing Fortiche frames: %s", arcane)
	}
	if strings.Contains(arcane, "protocol-arcane-atlas-001.png") {
		t.Errorf("arcane wall listed a root-level dump: %s", arcane)
	}
	if strings.Contains(arcane, "fashion") || strings.Contains(arcane, "princess") {
		t.Errorf("arcane wall leaked fashion/vanity: %s", arcane)
	}
}

func TestCollectionsPagesArePublicRooms(t *testing.T) {
	root := repoRoot(t)
	s := Server{cfg: config.Config{Root: root, OutputDir: t.TempDir()}}
	cases := []struct {
		path string
		want int
		tok  string
	}{
		{"/collections", http.StatusOK, "Named rooms"},
		{"/collections/fashion", http.StatusOK, "id=\"grid\""},
		{"/collections/arcane", http.StatusOK, "id=\"grid\""},
		{"/gallery/arcane", http.StatusFound, "/collections/arcane"},
	}
	for _, tc := range cases {
		rec := httptest.NewRecorder()
		s.muxForTest().ServeHTTP(rec, httptest.NewRequest(http.MethodGet, tc.path, nil))
		if rec.Code != tc.want {
			t.Errorf("%s status %d want %d", tc.path, rec.Code, tc.want)
		}
		body := rec.Body.String() + rec.Header().Get("Location")
		if !strings.Contains(body, tc.tok) {
			t.Errorf("%s missing %q", tc.path, tc.tok)
		}
	}
}

func (s Server) muxForTest() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("/gallery", s.gallery)
	mux.HandleFunc("/gallery/", s.gallery)
	mux.HandleFunc("/collections", s.teaCollectionsPage)
	mux.HandleFunc("/collections/", s.teaCollectionsPage)
	return mux
}
