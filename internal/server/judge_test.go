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
	"local/flux/internal/jury"
)

func TestJudgePageIsTheAlgorithmController(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "judge.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		"Judging algorithm",
		"/api/jury/config",
		"/api/protocol/calibrate",
		"lane=' + encodeURIComponent(lane)",
		"Hive calibrate & apply",
		"text from gates",
		"Fashion · GPU 3",
		"Arcane · GPU 0",
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("judge page missing %q", tok)
		}
	}
}

func TestJuryConfigLaneKeepsFashionAndArcaneIndependent(t *testing.T) {
	output := t.TempDir()
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: output}}
	fashion := jury.DefaultConfig()
	fashion.Weights = map[string]float64{"pixtral": 0.4, "qwen": 0.3, "decoder": 0.1, "governor": 0.2}
	if err := jury.SaveConfig(output, fashion); err != nil {
		t.Fatal(err)
	}
	arcane := jury.DefaultConfig()
	arcane.Weights = map[string]float64{"pixtral": 0.45, "qwen": 0.25, "decoder": 0.10, "governor": 0.20}
	if err := jury.SaveConfig(filepath.Join(output, "arcane"), arcane); err != nil {
		t.Fatal(err)
	}

	get := func(lane string) map[string]any {
		rec := httptest.NewRecorder()
		s.juryConfigAPI(rec, httptest.NewRequest(http.MethodGet, "/api/jury/config?lane="+lane, nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("lane %s status %d: %s", lane, rec.Code, rec.Body.String())
		}
		var payload map[string]any
		if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
			t.Fatal(err)
		}
		return payload
	}

	f := get("fashion")
	a := get("arcane")
	fw := f["config"].(map[string]any)["weights"].(map[string]any)
	aw := a["config"].(map[string]any)["weights"].(map[string]any)
	if f["lane"] != "fashion" || a["lane"] != "arcane" {
		t.Fatalf("lane tags fashion=%v arcane=%v", f["lane"], a["lane"])
	}
	if fw["pixtral"] == aw["pixtral"] {
		t.Fatalf("fashion and arcane weights collapsed: fashion=%v arcane=%v", fw, aw)
	}
	if filepath.Base(output) == "arcane" {
		t.Fatal("fashion dir must not be the arcane subdir")
	}
}

func TestJudgeRoutesServeTheController(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: t.TempDir()}}
	for _, path := range []string{"/judge", "/algorithm"} {
		rec := httptest.NewRecorder()
		s.judgePage(rec, httptest.NewRequest(http.MethodGet, path, nil))
		if rec.Code != http.StatusOK {
			t.Errorf("%s status %d", path, rec.Code)
		}
		if !strings.Contains(rec.Body.String(), "Judging algorithm") {
			t.Errorf("%s did not serve the controller", path)
		}
	}
	rec := httptest.NewRecorder()
	s.judgePage(rec, httptest.NewRequest(http.MethodGet, "/judge/", nil))
	if rec.Code != http.StatusPermanentRedirect {
		t.Errorf("/judge/ status %d", rec.Code)
	}
}
