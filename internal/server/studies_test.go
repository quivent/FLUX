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

func TestTeaStudyCatalogIncludesDraftsAndRecoveredStallionWork(t *testing.T) {
	studies, err := loadTeaStudies(repoRoot(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(studies) != 40 {
		t.Fatalf("study count = %d, want 40 (36 atlas drafts + 4 recovered records)", len(studies))
	}
	byID := make(map[string]map[string]any, len(studies))
	kinds := make(map[string]int)
	for _, study := range studies {
		byID[stringValue(study["id"])] = study
		kinds[stringValue(study["kind"])]++
		if strings.HasPrefix(stringValue(study["source"]), "/") {
			t.Errorf("study %q exposes an absolute source path", study["id"])
		}
	}
	for _, kind := range []string{"motion", "motion path", "motion loop", "atlas scout", "continuity"} {
		if kinds[kind] == 0 {
			t.Errorf("catalog has no %q cards", kind)
		}
	}
	stallion := byID["stallion-p3-latent-sphere"]
	if stallion == nil {
		t.Fatal("recovered Stallion P3 study is missing")
	}
	if got := intValue(stallion["render_count"]); got != 65536 {
		t.Errorf("Stallion rendered count = %d, want 65536", got)
	}
	if got := intValue(stallion["n_latent"]); got != 65536 {
		t.Errorf("Stallion sphere size = %d, want 65536", got)
	}
	if got := stringValue(stallion["status"]); got != "complete" {
		t.Errorf("Stallion status = %q, want complete", got)
	}
	for _, id := range []string{"atlas-equine-lateral-motion", "stallion-continuity-graph-01", "stallion-continuity-graph-02"} {
		if byID[id] == nil {
			t.Errorf("study %q is missing", id)
		}
	}
}

func TestStallionMotionProtocolIsNormalized(t *testing.T) {
	raw, err := osReadFileAtRoot(repoRoot(t), "apps/tea/protocols/stallion-motion-v1.json")
	if err != nil {
		t.Fatal(err)
	}
	var protocol struct {
		Schema      string             `json:"schema"`
		EdgeWeights map[string]float64 `json:"edge_weights"`
		Modes       map[string]any     `json:"modes"`
	}
	if err := json.Unmarshal(raw, &protocol); err != nil {
		t.Fatal(err)
	}
	if protocol.Schema != "tea.stallion-motion.v1" {
		t.Errorf("protocol schema = %q", protocol.Schema)
	}
	var total float64
	for _, weight := range protocol.EdgeWeights {
		total += weight
	}
	if total < .999999 || total > 1.000001 {
		t.Errorf("edge weights sum to %v, want 1", total)
	}
	for _, mode := range []string{"spectral_loop", "continuity", "kinetic"} {
		if protocol.Modes[mode] == nil {
			t.Errorf("protocol mode %q is missing", mode)
		}
	}
}

func TestStallionMotionLabAndIdleAPI(t *testing.T) {
	root := repoRoot(t)
	s := Server{cfg: config.Config{Root: root, OutputDir: t.TempDir()}}
	page := httptest.NewRecorder()
	s.stallionMotionLab(page, httptest.NewRequest(http.MethodGet, "/studies/stallion", nil))
	if page.Code != http.StatusOK {
		t.Fatalf("motion lab status = %d", page.Code)
	}
	for _, token := range []string{"Stallion Motion Lab", "/api/studies/stallion-motion", "Run exploration", "Spectral loop", "Continuous until stopped", `continuous:$('continuous').checked`} {
		if !strings.Contains(page.Body.String(), token) {
			t.Errorf("motion lab missing %q", token)
		}
	}

	api := httptest.NewRecorder()
	s.stallionMotionAPI(api, httptest.NewRequest(http.MethodGet, "/api/studies/stallion-motion", nil))
	if api.Code != http.StatusOK || !strings.Contains(api.Body.String(), `"state":"idle"`) {
		t.Fatalf("idle API = %d %s", api.Code, api.Body.String())
	}
}

func TestStallionMotionHistoryBuildsCompactGallery(t *testing.T) {
	output := t.TempDir()
	runID := "stallion-motion-20260812-235016-131"
	runDir := filepath.Join(output, "studies", "stallion-motion", "runs", runID)
	if err := os.MkdirAll(runDir, 0o755); err != nil {
		t.Fatal(err)
	}
	writeJSONFile(t, filepath.Join(runDir, "status.json"), map[string]any{
		"state": "complete", "source_kind": "atlas_grid_proxy", "frames": 32, "fps": 12, "rounds": 1,
		"contact_sheet": "contact-sheet.jpg",
		"results": []any{map[string]any{
			"mode": "spectral_loop", "round": 1, "rank": 1, "family": 2,
			"selection_score": 0.23, "video": "r01-spectral_loop.mp4", "poster": "r01-spectral_loop-frames/frame_00000.jpg",
			"metrics": map[string]any{"frames": 32, "worst_visual_jump": 0.3, "edges": []any{"large", "detail"}},
		}},
	})
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: output}}
	rec := httptest.NewRecorder()
	s.stallionMotionAPI(rec, httptest.NewRequest(http.MethodGet, "/api/studies/stallion-motion?history=1", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("history status = %d: %s", rec.Code, rec.Body.String())
	}
	var history map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &history); err != nil {
		t.Fatal(err)
	}
	if intValue(history["run_count"]) != 1 || intValue(history["result_count"]) != 1 {
		t.Fatalf("unexpected history counts: %s", rec.Body.String())
	}
	if !strings.Contains(rec.Body.String(), "/studies/stallion/results/"+runID+"/r01-spectral_loop.mp4") {
		t.Error("history does not expose the playable result URL")
	}
	if strings.Contains(rec.Body.String(), `"edges"`) {
		t.Error("gallery history must not inline full transition-edge manifests")
	}
}

func TestStudiesPageNamesCatalogContract(t *testing.T) {
	raw, err := osReadFileAtRoot(repoRoot(t), "apps/tea/public/studies.html")
	if err != nil {
		t.Fatal(err)
	}
	page := string(raw)
	for _, token := range []string{`href="/studies"`, `/api/studies`, `id="cards"`, `The Stallion`, `65,536 cells complete`, `Full sphere`, `Study library`, `Studies gallery`, `history=1`, `id="viewer"`} {
		if !strings.Contains(page, token) {
			t.Errorf("Studies page missing %q", token)
		}
	}
}

func osReadFileAtRoot(root string, parts ...string) ([]byte, error) {
	return os.ReadFile(filepath.Join(append([]string{root}, parts...)...))
}
