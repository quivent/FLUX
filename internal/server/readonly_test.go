package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"local/flux/internal/config"
)

// The read-only gate is the only thing between a public listener and an H100,
// so it gets a test that names the exact surface: anything not listed here is
// refused, including routes added later.
func TestReadOnlyGate(t *testing.T) {
	reached := false
	handler := withReadOnly(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	}), true)

	cases := []struct {
		method string
		path   string
		allow  bool
	}{
		{http.MethodGet, "/", true},
		{http.MethodGet, "/app", true},
		{http.MethodGet, "/gallery/", true},
		{http.MethodGet, "/gallery/arcane", true},
		{http.MethodGet, "/collections", true},
		{http.MethodGet, "/collections/fashion", true},
		{http.MethodGet, "/collections/arcane", true},
		{http.MethodGet, "/collections/silk", true},
		{http.MethodGet, "/api/protocol/branches", true},
		{http.MethodGet, "/studios", true},
		{http.MethodGet, "/studio/fashion", true},
		{http.MethodGet, "/api/studios", true},
		{http.MethodGet, "/portraits", true},
		{http.MethodGet, "/sentinel", true},
		{http.MethodGet, "/api/sentinel/events", true},
		{http.MethodGet, "/movement", true},
		{http.MethodGet, "/studies", true},
		{http.MethodGet, "/studies/stallion", true},
		{http.MethodGet, "/studies/stallion/results/example/contact-sheet.jpg", true},
		{http.MethodGet, "/api/studies", true},
		{http.MethodGet, "/api/studies/stallion-motion", true},
		{http.MethodGet, "/exhibition", true},
		{http.MethodGet, "/exhibition/stallion-atlas-exhibition.mp4", true},
		{http.MethodGet, "/atelier/", true},
		{http.MethodGet, "/motion-atlas/", true},
		{http.MethodGet, "/motion-atlas/app.js", true},
		{http.MethodGet, "/outputs/atlas/x.png", true},
		{http.MethodGet, "/protocol", true},
		{http.MethodGet, "/judge", true},
		{http.MethodGet, "/desk", true},
		{http.MethodGet, "/desk/hive", true},
		{http.MethodGet, "/scores", true},
		{http.MethodGet, "/api/tea/scores", true},
		{http.MethodGet, "/api/tea/desk", true},
		{http.MethodGet, "/tea.css", true},
		{http.MethodGet, "/tea-shell.js", true},
		{http.MethodGet, "/reports", true},
		{http.MethodGet, "/api/reports", true},
		{http.MethodGet, "/charters", true},
		{http.MethodGet, "/api/charters", true},
		{http.MethodGet, "/algorithm", true},
		{http.MethodGet, "/api/protocol", true},
		{http.MethodGet, "/api/health", true},
		{http.MethodGet, "/api/recent-images", true},
		{http.MethodGet, "/api/assets/ws", true},
		{http.MethodGet, "/api/telemetry/events", true},
		{http.MethodGet, "/api/telemetry/ws", true},
		{http.MethodGet, "/api/jobs", true},
		{http.MethodGet, "/api/jobs/ws", true},
		// The gallery is unusable without thumbnails; a full-size wall is
		// hundreds of megabytes.
		{http.MethodGet, "/api/asset/thumbnail?w=384&src=/outputs/a.png", true},

		// Renders, warmups and cancels all cost GPU time or money.
		{http.MethodPost, "/api/protocol/branches", false},
		{http.MethodPost, "/api/studios/fashion", false},
		{http.MethodPost, "/api/render", false},
		{http.MethodPost, "/api/generate", false},
		{http.MethodPost, "/api/atlas/submit", false},
		{http.MethodPost, "/api/studies", false},
		{http.MethodPost, "/api/studies/stallion-motion", false},
		{http.MethodDelete, "/api/studies/stallion-motion", false},
		{http.MethodPost, "/api/warm", false},
		{http.MethodPost, "/api/atlas", false},
		{http.MethodPost, "/api/governor/chat", false},
		{http.MethodPost, "/api/jobs", false},
		{http.MethodGet, "/api/jobs/abc/cancel", false},
		// A GET is not automatically safe: warm loads the model.
		{http.MethodGet, "/api/warm", false},
		{http.MethodDelete, "/outputs/atlas/x.png", false},
	}

	for _, tc := range cases {
		reached = false
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.path, nil))
		if tc.allow && !reached {
			t.Errorf("%s %s: expected to pass the gate, got %d", tc.method, tc.path, rec.Code)
		}
		if !tc.allow {
			if reached {
				t.Errorf("%s %s: reached the handler; the gate must refuse it", tc.method, tc.path)
			}
			if rec.Code != http.StatusForbidden {
				t.Errorf("%s %s: expected 403, got %d", tc.method, tc.path, rec.Code)
			}
		}
	}
}

func TestRecentImageRoomsAreDisjoint(t *testing.T) {
	cases := []struct {
		scope, rel string
		want       bool
	}{
		{"", "old-master.png", true},
		{"", "batches/stills", true},
		{"", "atlas/bell.sphere", false},
		{"", "collections/bell-weather", false},
		{"portraits", "collections/bell-weather", true},
		{"portraits", "atlas/bell.sphere", false},
		{"portraits", "old-master.png", false},
		{"arcane", "arcane/still-001.png", true},
		{"arcane", "protocol-arcane-atlas-001.png", false},
		{"arcane", "old-master.png", false},
		{"arcane", "collections/bell-weather", false},
		{"arcane", "protocol-fashion-stream-001.png", false},
		{"arcane", "arcane/_rejected-atlas/protocol-arcane-atlas-001.png", false},
		{"images", "arcane/still-001.png", false},
		{"images", "protocol-arcane-atlas-001.png", false},
		{"images", "protocol-fashion-stream-001.png", true},
		{"images", "princess-rose.png", false},
		{"fashion", "protocol-fashion-stream-001.png", true},
		{"fashion", "protocol-arcane-atlas-001.png", false},
		{"fashion", "collections/silk/protocol-silk-001.png", false},
		{"silk", "collections/silk/protocol-silk-001.png", true},
		{"silk", "collections/silk", true},
		{"silk", "collections/other/protocol-other-001.png", false},
		{"silk", "protocol-fashion-stream-001.png", false},
		{"portraits", "collections/silk/protocol-silk-001.png", false},
		{"movement", "atlas/bell.sphere", true},
		{"movement", "collections/bell-weather", false},
		{"images", "finished-work.png", false},
		{"images", "studies/stallion-motion", false},
		{"images", "projects/round-42/candidate.png", false},
	}
	for _, tc := range cases {
		if got := recentAssetAllowed(tc.scope, tc.rel); got != tc.want {
			t.Errorf("scope=%q rel=%q: got %t want %t", tc.scope, tc.rel, got, tc.want)
		}
	}
}

func TestRecentImagesKeepSameNamedFramesFromDistinctRuns(t *testing.T) {
	output := t.TempDir()
	for _, run := range []string{"run-a", "run-b"} {
		path := filepath.Join(output, "studies", "stallion-motion", "runs", run, "frame_00000.jpg")
		if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, []byte(run), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: output}}
	rec := httptest.NewRecorder()
	s.recentImages(rec, httptest.NewRequest(http.MethodGet, "/api/recent-images?limit=20", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("recent images status = %d: %s", rec.Code, rec.Body.String())
	}
	var payload struct {
		Total  int `json:"total"`
		Images []struct {
			Path string `json:"path"`
		} `json:"images"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload.Total != 2 || len(payload.Images) != 2 {
		t.Fatalf("same-named frames collapsed: total=%d images=%d body=%s", payload.Total, len(payload.Images), rec.Body.String())
	}
	if payload.Images[0].Path == payload.Images[1].Path {
		t.Fatalf("distinct runs returned one path twice: %s", rec.Body.String())
	}
}

// Disabled is the default everywhere except a deliberate public bind, so it
// must be a true pass-through rather than a subtly different handler.
func TestReadOnlyDisabledPassesEverything(t *testing.T) {
	reached := false
	handler := withReadOnly(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
	}), false)
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/api/render", nil))
	if !reached {
		t.Fatal("read-only disabled must not block anything")
	}
}
