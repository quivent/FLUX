package server

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestGovernorUpstreamDefaultsToAgenticGateway(t *testing.T) {
	t.Setenv("GOVERNOR_URL", "")
	u := governorUpstream()
	if u.String() != "http://127.0.0.1:8800" {
		t.Fatalf("upstream %s, want agentic gateway :8800", u)
	}
}

func TestGovernorPageServesTeaChat(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "governor.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		"Governor — Tea",
		"/api/governor/chat",
		"/api/governor/models",
		"/api/governor/health",
		`include_metadata: true`,
		`class="tea-chrome"`,
		`href="/tea/tea.css"`,
		"stream: true",
		`aria-current="page"`,
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("governor page missing %q", tok)
		}
	}

	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.governorPage(rec, httptest.NewRequest(http.MethodGet, "/governor", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("/governor status %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "Talk to the Governor") {
		t.Fatal("/governor did not serve the chat")
	}
	rec = httptest.NewRecorder()
	s.governorPage(rec, httptest.NewRequest(http.MethodGet, "/governor/", nil))
	if rec.Code != http.StatusPermanentRedirect {
		t.Errorf("/governor/ status %d", rec.Code)
	}
}

func TestGovernorProxyHitsLocalEngine(t *testing.T) {
	var sawPath string
	var sawBody string
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sawPath = r.URL.Path
		body, _ := io.ReadAll(r.Body)
		sawBody = string(body)
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1/models" {
			_, _ = w.Write([]byte(`{"object":"list","data":[{"id":"governor","max_model_len":131072}]}`))
			return
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"ok"}}]}`))
	}))
	defer engine.Close()
	t.Setenv("GOVERNOR_URL", engine.URL)

	s := Server{}
	rec := httptest.NewRecorder()
	s.governorModels(rec, httptest.NewRequest(http.MethodGet, "/api/governor/models", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("models status %d %s", rec.Code, rec.Body.String())
	}
	if sawPath != "/v1/models" {
		t.Fatalf("models proxied to %q", sawPath)
	}
	rec = httptest.NewRecorder()
	s.governorHealth(rec, httptest.NewRequest(http.MethodGet, "/api/governor/health", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("health status %d %s", rec.Code, rec.Body.String())
	}
	if sawPath != "/health" {
		t.Fatalf("health proxied to %q", sawPath)
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}

	rec = httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodPost, "/api/governor/chat", strings.NewReader(`{"model":"governor","stream":false}`))
	req.Header.Set("Content-Type", "application/json")
	s.governorChat(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("chat status %d %s", rec.Code, rec.Body.String())
	}
	if sawPath != "/v1/chat/completions" {
		t.Fatalf("chat proxied to %q", sawPath)
	}
	if !strings.Contains(sawBody, `"model":"governor"`) {
		t.Fatalf("chat body not forwarded: %s", sawBody)
	}
}
