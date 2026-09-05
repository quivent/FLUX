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
		"Microgreens · GPU 0",
		"data.lanes",
		"renderLanes",
		"This frame's jury",
		"flowSeats",
		"evalPath",
		`class="tea-chrome"`,
		`href="/tea.css"`,
		"--paper",
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("judge page missing %q", tok)
		}
	}
}

func TestProtocolPageShowsThisFramesJury(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "protocol.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		"This frame's jury",
		"flowSeats",
		"location.hash",
		"1024",
		"Anatomy",
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("protocol page missing %q", tok)
		}
	}
}

func TestTeaShellListsProtocol(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "tea-shell.js"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `href="/protocol"`) {
		t.Fatal("tea chrome dropped Protocol, so the jury flow screen is unreachable")
	}
}

func TestTeaShellListsDiscourse(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "tea-shell.js"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(raw), `href="/discourse"`) {
		t.Fatal("tea chrome dropped Discourse, so the gemstone arena is unreachable from Tea")
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

func TestParseHiveRenderClampsHarvest(t *testing.T) {
	got := parseHiveRender(map[string]any{"render": map[string]any{
		"steps": 36, "width": 1280, "height": 768, "guidance": 3.2, "life": 95, "depth": 3,
	}})
	if got["steps"] != 36 || got["width"] != 1280 || got["height"] != 768 {
		t.Fatalf("render %+v", got)
	}
	if parseHiveRender(map[string]any{"render": map[string]any{"steps": 99}})["steps"] != 64 {
		t.Fatal("steps above 64 must clamp to 64")
	}
}

func TestClampStudyRenderParams(t *testing.T) {
	if clampStudySteps(36) != 36 || clampStudySteps(18) != 18 || clampStudySteps(7) != 0 {
		t.Fatalf("steps 36=%d 18=%d 7=%d", clampStudySteps(36), clampStudySteps(18), clampStudySteps(7))
	}
	if clampStudySize(256) != 256 || clampStudySize(1280) != 1280 || clampStudySize(900) != 896 {
		t.Fatalf("size 256=%d 1280=%d 900=%d", clampStudySize(256), clampStudySize(1280), clampStudySize(900))
	}
}

func TestHiveCalibrationBriefNamesTheLane(t *testing.T) {
	horses := hiveCalibrationBrief("silken-horses")
	if !strings.Contains(horses, "silken-horses") || !strings.Contains(horses, "equine") {
		t.Fatalf("horses brief: %s", horses)
	}
	if strings.Contains(horses, "Belarro") || strings.Contains(horses, "soil-grown") {
		t.Fatalf("horses brief still talks like microgreens: %s", horses)
	}
	greens := hiveCalibrationBrief("microgreens")
	if !strings.Contains(greens, "microgreens") || !strings.Contains(greens, "Belarro") {
		t.Fatalf("microgreens brief: %s", greens)
	}
}

func TestBindLiveModelsPutsPixtralOnAestheticSeatNotQwen(t *testing.T) {
	cfg := bindLiveModels(jury.DefaultConfig(), liveModels{
		Governor: []string{"governor"},
		Witness:  []string{"jury"},
		Hive:     []string{"hive-research", "qwen-research"},
		Pixtral:  []string{"pixtral", "pixtral-12b"},
	})
	ep := cfg.Endpoints[jury.ServedPixtral]
	if ep.Model != "pixtral" || ep.BaseURL != "http://127.0.0.1:8004/v1" {
		t.Fatalf("aesthetic seat %+v", ep)
	}
	gov := cfg.Endpoints[jury.ServedGovernor]
	if gov.BaseURL != "http://127.0.0.1:8800/v1" {
		t.Fatalf("governor traffic must go through :8800, got %+v", gov)
	}
	if ep.Vision == nil || !*ep.Vision {
		t.Fatal("pixtral must see images")
	}
	wit := cfg.Endpoints[jury.ServedWitness]
	if wit.BaseURL != "http://127.0.0.1:8004/v1" || wit.Model != "pixtral" {
		t.Fatalf("visual-witness must be Pixtral :8004, got %+v", wit)
	}

	cfg = bindLiveModels(cfg, liveModels{
		Governor: []string{"governor"},
		Witness:  []string{"jury"},
		Hive:     []string{"hive-research"},
	})
	if _, ok := cfg.Endpoints[jury.ServedPixtral]; ok {
		t.Fatalf("qwen must not inherit the aesthetic seat: %+v", cfg.Endpoints[jury.ServedPixtral])
	}
}

func TestJuryDirForMicrogreensIsItsCollection(t *testing.T) {
	output := t.TempDir()
	s := Server{cfg: config.Config{OutputDir: output}}
	if got := s.juryDirForLane("microgreens"); got != filepath.Join(output, "collections", "microgreens") {
		t.Fatalf("microgreens jury dir %s", got)
	}
	if s.juryDirForLane("fashion") != output {
		t.Fatal("fashion jury should stay at the output root")
	}
}

func TestSilkenHorsesIsItsOwnJuryLane(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/jury/config?lane=silken-horses", nil)
	if got := requestJuryLane(req, ""); got != "silken-horses" {
		t.Fatalf("silken-horses collapsed to %q", got)
	}
	output := t.TempDir()
	s := Server{cfg: config.Config{OutputDir: output}}
	want := filepath.Join(output, "collections", "silken-horses")
	if got := s.juryDirForLane("silken-horses"); got != want {
		t.Fatalf("silken-horses jury dir %s want %s", got, want)
	}
	root := t.TempDir()
	branch := filepath.Join(root, ".fluxd", "protocol_stream_branch_silken-horses.json")
	if err := os.MkdirAll(filepath.Dir(branch), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(branch, []byte(`{"status":"running"}`), 0o644); err != nil {
		t.Fatal(err)
	}
	if got := protocolStreamStatePathFor(root, "silken-horses"); got != branch {
		t.Fatalf("stream path %s want %s", got, branch)
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

func TestJuryRootIsBeautyJuryNotMotionStudy(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "study-beauty.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		"Beauty jury",
		"silken horses",
		"/api/jury/config",
		`encodeURIComponent(LANE)`,
		"persistLaw",
		"Ask the hive",
		"Bind live seats",
		"Start the beauty jury",
		"/api/protocol/calibrate",
		`const LANE = "silken-horses"`,
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("beauty jury page missing %q", tok)
		}
	}
	lower := strings.ToLower(src)
	for _, tok := range []string{"motion study", "motion atlas", "arcane", "fortiche", "microgreens beauty jury"} {
		if strings.Contains(lower, tok) {
			t.Errorf("beauty jury page still carries off-lane language %q", tok)
		}
	}
}

func TestJuryRoutesFashionBeautyAtRootAndArcaneAtSubpath(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: t.TempDir()}}
	rec := httptest.NewRecorder()
	s.juryPage(rec, httptest.NewRequest(http.MethodGet, "/jury", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("/jury status %d", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "Beauty jury") || !strings.Contains(body, `const LANE = "silken-horses"`) {
		t.Fatal("/jury did not serve the silken-horses beauty jury")
	}
	if strings.Contains(body, "The court for animation stills") || strings.Contains(body, "Arcane chamber") {
		t.Fatal("/jury still served Arcane copy")
	}
	if strings.Contains(body, "Microgreens beauty jury") {
		t.Fatal("/jury still served the microgreens chamber")
	}

	arcane := httptest.NewRecorder()
	s.juryPage(arcane, httptest.NewRequest(http.MethodGet, "/jury/arcane", nil))
	if arcane.Code != http.StatusOK {
		t.Fatalf("/jury/arcane status %d", arcane.Code)
	}
	if !strings.Contains(arcane.Body.String(), "Beauty jury") {
		t.Fatal("/jury/arcane must stay on the beauty jury")
	}
	if strings.Contains(arcane.Body.String(), "The court for animation stills") {
		t.Fatal("/jury/arcane still served the Arcane chamber")
	}
}
