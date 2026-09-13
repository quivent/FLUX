package server

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestBeautySiteUsesItsOwnPresentationBundle(t *testing.T) {
	public := t.TempDir()
	for name, body := range map[string]string{
		"index.html":       "beauty-index",
		"gallery.html":     "beauty-gallery",
		"collections.html": "beauty-collections",
		"protocol.html":    "beauty-protocol",
		"jury.html":        "beauty-jury",
		"control.html":     "beauty-controls",
		"beauty.css":       "beauty-css",
		"beauty-shell.js":  "beauty-shell",
	} {
		if err := os.WriteFile(filepath.Join(public, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}

	s := Server{cfg: config.Config{Root: t.TempDir()}, publicDir: public}
	tests := []struct {
		path string
		want string
		call func(http.ResponseWriter, *http.Request)
	}{
		{"/", "beauty-index", s.home},
		{"/gallery/", "beauty-gallery", s.galleryFlux},
		{"/collections", "beauty-collections", s.teaCollectionsPage},
		{"/protocol", "beauty-protocol", s.protocolPage},
		{"/jury", "beauty-jury", s.juryPage},
		{"/control", "beauty-controls", s.deskPage},
		{"/beauty.css", "beauty-css", s.siteChromeAsset},
		{"/beauty-shell.js", "beauty-shell", s.siteChromeAsset},
	}

	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			recorder := httptest.NewRecorder()
			tt.call(recorder, httptest.NewRequest(http.MethodGet, tt.path, nil))
			if recorder.Code != http.StatusOK {
				t.Fatalf("status = %d, want 200", recorder.Code)
			}
			if !strings.Contains(recorder.Body.String(), tt.want) {
				t.Fatalf("body %q does not contain %q", recorder.Body.String(), tt.want)
			}
		})
	}
}

func TestBeautyBundleStaysReduced(t *testing.T) {
	root := filepath.Join(repoRoot(t), "apps", "beauty", "public")
	shell, err := os.ReadFile(filepath.Join(root, "beauty-shell.js"))
	if err != nil {
		t.Fatal(err)
	}
	text := string(shell)
	for _, want := range []string{"Gallery", "Collections", "Protocol", "Jury", "Controls"} {
		if !strings.Contains(text, want) {
			t.Errorf("shell is missing %q", want)
		}
	}
	for _, removed := range []string{"Evening", "Hive", "Research", "Ledger", "Train", "Daemons", "Studies"} {
		if strings.Contains(text, removed) {
			t.Errorf("shell still exposes removed Tea surface %q", removed)
		}
	}
}

func TestBeautyPresentationStaysEditorial(t *testing.T) {
	root := filepath.Join(repoRoot(t), "apps", "beauty", "public")
	css, err := os.ReadFile(filepath.Join(root, "beauty.css"))
	if err != nil {
		t.Fatal(err)
	}
	for _, generatedTell := range []string{"fonts.googleapis.com", "radial-gradient", "backdrop-filter", "border-radius: 999px"} {
		if strings.Contains(string(css), generatedTell) {
			t.Errorf("presentation restored generated-dashboard tell %q", generatedTell)
		}
	}
	index, err := os.ReadFile(filepath.Join(root, "index.html"))
	if err != nil {
		t.Fatal(err)
	}
	for _, retired := range []string{"Beauty,<br>under control.", "Stillness, judged in motion"} {
		if strings.Contains(string(index), retired) {
			t.Errorf("landing page restored retired slogan %q", retired)
		}
	}
}

func TestBeautyPipelineStatusAndGeometryContract(t *testing.T) {
	s := Server{cfg: config.Config{Root: t.TempDir()}}
	recorder := httptest.NewRecorder()
	s.beautyPipelineAPI(recorder, httptest.NewRequest(http.MethodGet, "/api/beauty/pipeline", nil))
	if recorder.Code != http.StatusOK || !strings.Contains(recorder.Body.String(), `"status":"idle"`) {
		t.Fatalf("idle status = %d %s", recorder.Code, recorder.Body.String())
	}

	recorder = httptest.NewRecorder()
	body := strings.NewReader(`{"action":"start","prompt":"beauty","width":1024,"height":1024}`)
	s.beautyPipelineAPI(recorder, httptest.NewRequest(http.MethodPost, "/api/beauty/pipeline", body))
	if recorder.Code != http.StatusBadRequest || !strings.Contains(recorder.Body.String(), "fixed at 512x512") {
		t.Fatalf("geometry contract = %d %s", recorder.Code, recorder.Body.String())
	}
}
