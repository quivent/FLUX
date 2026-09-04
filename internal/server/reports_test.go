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

func TestReportsPageIsTheOutputReportSurface(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "reports.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{"Output reports", "/api/reports", "fashion", "arcane", "CONVERGENCE"} {
		if !strings.Contains(src, tok) {
			t.Errorf("reports page missing %q", tok)
		}
	}
}

func TestReportsAPIExposesBothLanes(t *testing.T) {
	output := t.TempDir()
	if err := os.MkdirAll(filepath.Join(output, "arcane"), 0o755); err != nil {
		t.Fatal(err)
	}
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: output}}
	rec := httptest.NewRecorder()
	s.reportsAPI(rec, httptest.NewRequest(http.MethodGet, "/api/reports", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	lanes, _ := payload["lanes"].([]any)
	if len(lanes) != 2 {
		t.Fatalf("lanes = %d, want 2: %s", len(lanes), rec.Body.String())
	}
	ids := []string{lanes[0].(map[string]any)["id"].(string), lanes[1].(map[string]any)["id"].(string)}
	if ids[0] != "fashion" || ids[1] != "arcane" {
		t.Fatalf("lane ids %v", ids)
	}
}

func TestConvergenceHostDoesNotServeTeaReports(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: t.TempDir()}}
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Host = "convergence.apiary.vision"
	rec := httptest.NewRecorder()
	s.home(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if strings.Contains(rec.Body.String(), "Output reports") {
		t.Fatal("Tea home must not hijack convergence.apiary.vision; that host is hive-only")
	}
}
