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
	root := repoRoot(t)
	studies, err := loadTeaStudies(root)
	if err != nil {
		t.Fatal(err)
	}
	draftEntries, err := os.ReadDir(filepath.Join(root, "atlas_drafts"))
	if err != nil {
		t.Fatal(err)
	}
	draftCount := 0
	for _, entry := range draftEntries {
		if !entry.IsDir() && filepath.Ext(entry.Name()) == ".json" {
			draftCount++
		}
	}
	curatedRaw, err := os.ReadFile(filepath.Join(root, "apps", "tea", "studies.json"))
	if err != nil {
		t.Fatal(err)
	}
	var curated []map[string]any
	if err := json.Unmarshal(curatedRaw, &curated); err != nil {
		t.Fatal(err)
	}
	beautyJobs := 0
	queueRaw, err := os.ReadFile(filepath.Join(root, "chorus", "beauty-queue.json"))
	if err != nil {
		t.Fatal(err)
	}
	var queue struct {
		Jobs []map[string]any `json:"jobs"`
	}
	if err := json.Unmarshal(queueRaw, &queue); err != nil {
		t.Fatal(err)
	}
	beautyJobs = len(queue.Jobs)
	want := draftCount + len(curated) + beautyJobs + 1
	if len(studies) != want {
		t.Fatalf("study count = %d, want %d (%d atlas drafts + %d curated + %d beauty-queue jobs + parent)", len(studies), want, draftCount, len(curated), beautyJobs)
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
	fashion := byID["fashion-beauty-on-beauty"]
	if fashion == nil || !strings.Contains(stringValue(fashion["prompt"]), "Fashion beauty on beauty") {
		t.Fatal("fashion study is missing its prepared prompt")
	}
	if fashion["variables"] == nil {
		t.Error("fashion study is missing variables")
	}
	bell := byID["beauty-queue-bell-weather"]
	if bell == nil {
		t.Fatal("beauty-queue Bell Weather is missing")
	}
	if got := stringValue(bell["status"]); got != "prepared" {
		t.Errorf("Bell Weather status = %q, want prepared", got)
	}
	if !strings.Contains(stringValue(bell["prompt"]), "temple bell") {
		t.Errorf("Bell Weather prompt = %q", bell["prompt"])
	}
	queueParent := byID["images-of-beauty-48"]
	if queueParent == nil {
		t.Fatal("beauty-queue parent card is missing")
	}
	turntable := byID["spheremap_atlas_arcane_italian_princess_turntable_64_20260713"]
	if turntable == nil {
		t.Fatal("Arcane turntable draft is missing")
	}
	if got := stringValue(turntable["family"]); got != "arcane" {
		t.Errorf("Arcane turntable family = %q, want arcane", got)
	}
	vars, _ := turntable["variables"].(map[string]any)
	if vars == nil || vars["seed_lock"] == nil || vars["shell_scale"] == nil {
		t.Errorf("Arcane turntable variables incomplete: %#v", vars)
	}
}

func TestStallionMotionProtocolIsNormalized(t *testing.T) {
	raw, err := osReadFileAtRoot(repoRoot(t), "apps/tea/protocols/stallion-motion-v2.json")
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
	if protocol.Schema != "tea.stallion-motion.v2" {
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
	if !strings.Contains(api.Body.String(), `"source_ready":false`) {
		t.Fatalf("idle API must expose native-source gate: %s", api.Body.String())
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
		"state": "complete", "source_kind": "native_cells", "frames": 32, "fps": 12, "rounds": 1,
		"contact_sheet": "contact-sheet.jpg",
		"results": []any{map[string]any{
			"mode": "spectral_loop", "round": 1, "rank": 1, "family": 2,
			"selection_score": 0.23, "video": "", "poster": "",
			"metrics": map[string]any{"frames": 32, "worst_visual_jump": 0.3, "edges": []any{"large", "detail"}},
		}},
	})
	writeJSONFile(t, filepath.Join(runDir, "r01-spectral_loop.json"), map[string]any{
		"mode": "spectral_loop", "round": 1, "rank": 1, "family": 2,
		"selection_score": 0.23, "video": "r01-spectral_loop.mp4", "poster": "r01-spectral_loop-native-frames/frame_00000.png",
		"metrics": map[string]any{"frames": 32, "worst_visual_jump": 0.3, "edges": []any{"large", "detail"}},
	})
	writeJSONFile(t, filepath.Join(output, "studies", "stallion-motion", "gpu-reviews.json"), map[string]any{
		"reviews": map[string]any{
			runID + "/r01-spectral_loop": map[string]any{
				"schema": "tea.stallion-motion.gpu-review.v2", "qualified": true,
				"neural_score": 0.12, "models": []any{"raft-small-c-t-v2", "deeplabv3-horse"},
			},
		},
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
	if !strings.Contains(rec.Body.String(), `"neural_score":0.12`) {
		t.Error("gallery history does not merge the GPU optical-flow review")
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
