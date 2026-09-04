package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The public wall presents art, not the machinery that makes or judges it.
// Operational state belongs in an operator surface even when it is useful.
func TestEveningIsAWalkthrough(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "evening.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		`The house, in motion`,
		`256, and no closer`,
		`grab("silken-horses")`,
		`stallion-gait-projection.mp4`,
		`FP8 against BF16`,
		`hive-research`,
		`/api/train`,
		`stallion-gait-projection.mp4`,
		`Four rooms, one night`,
		`href="/desk"`,
		`href="/gallery/"`,
		`href="/hive"`,
		`href="/train"`,
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("evening missing %q", tok)
		}
	}
}

func TestGalleryHasOperatorJuryAndDeskButtons(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{`href="/jury"`, `href="/desk"`, `Configuration / Desk`, `class="op-btn"`} {
		if !strings.Contains(source, token) {
			t.Errorf("gallery missing operator control %q", token)
		}
	}
}

func TestLiveGalleryIsNotMicrogreens(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{
		`LIVE_GALLERY || MICROGREENS_COLLECTION`,
		`LIVE STREAM · MICROGREENS`,
		`Microgreens beauty`,
	} {
		if strings.Contains(source, token) {
			t.Errorf("live /gallery/ is still aliased to microgreens: %q", token)
		}
	}
	for _, token := range []string{
		`/gallery/ is the GPU 3 beauty wall`,
		`pickLiveBranch`,
		`LIVE_BANNED`,
		`silken-horses`,
		`SPROUT`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("live gallery missing %q", token)
		}
	}
}

func TestPublicGalleryHidesProductionMachinery(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{`Images of Beauty — Tea Gallery`, `Images of Beauty`, `id="grid"`, `id="s-collection"`} {
		if !strings.Contains(source, token) {
			t.Errorf("public gallery presentation missing %q", token)
		}
	}
	for _, token := range []string{
		`Recent works`, `Gemma`, `telemetry`, `worker online`, `worker unreachable`, `Asset feed`,
		`human review`, `Piper`, `Nexus`, `/api/jobs`, `/api/telemetry`,
		`taste-status.json`, `drift-status.json`, `picks.json`, `panel-decisions.json`,
	} {
		if strings.Contains(source, token) {
			t.Errorf("public gallery exposes production machinery %q", token)
		}
	}
}

func TestGalleryCountsOnlyNewPushedAssets(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	required := []string{
		`/api/assets/ws`,
		`es.addEventListener('asset'`,
		`streamStartedAt`,
		`replay never increments the collection`,
		`grows from its top-left corner`,
		`if (!addCell(url, asset.name, true)) return`,
		`total += 1`,
		`'s-collection'`,
	}
	for _, token := range required {
		if !strings.Contains(source, token) {
			t.Errorf("gallery live-increment contract missing %q", token)
		}
	}
	if strings.Contains(source, `poll('/api/`) {
		t.Errorf("gallery must use pushed events, found polling path %q", `poll('/api/`)
	}
	if strings.Contains(source, `setInterval(`) && !strings.Contains(source, `setInterval(refreshBoard`) {
		t.Errorf("gallery wall must not poll; only the jury board may setInterval(refreshBoard)")
	}
}

func TestGalleryPortraitsIsTopRated(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{
		`/api/jury/spectacles`,
		`loadTopRated`,
		`Top Rated`,
		`composite_score`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("portraits top-rated wall missing %q", token)
		}
	}
}

func TestGalleryShowsFourRows(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{`MAX_ROWS = 4`, `function wallCap()`, `function trimWall()`} {
		if !strings.Contains(source, token) {
			t.Errorf("gallery four-row wall missing %q", token)
		}
	}
	if strings.Contains(source, `requestAnimationFrame(fillViewport)`) {
		t.Error("gallery still infinite-scrolls the full archive")
	}
	if strings.Contains(strings.ToLower(source), `load more`) {
		t.Error("manual load-more control returned to the public gallery")
	}
}

func TestMotionWorkAlwaysMovesAndKeepsArrivalGrid(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "movement.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{
		`autoplay muted loop playsinline`,
		`bell-learns-the-wind.mp4`,
		`setInterval(`,
		`/api/tea/movement`,
		`id="start"`,
		`type="range"`,
		`id="arrivals"`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("motion experiment console missing %q", token)
		}
	}
}

func TestPublicNavigationNeverDisplacesAWork(t *testing.T) {
	root := repoRoot(t)
	pages := []string{
		filepath.Join(root, "apps", "tea", "public", "index.html"),
		filepath.Join(root, "apps", "tea", "public", "gallery.html"),
		filepath.Join(root, "apps", "tea", "public", "movement.html"),
		filepath.Join(root, "apps", "tea", "public", "studies.html"),
		filepath.Join(root, "apps", "tea", "public", "stallion-lab.html"),
		filepath.Join(root, "apps", "tea", "public", "exhibition.html"),
		filepath.Join(root, "apps", "tea", "public", "stallion.html"),
		filepath.Join(root, "apps", "tea", "public", "jury.html"),
		filepath.Join(root, "apps", "tea", "public", "judge.html"),
	}
	links := []string{`href="/"`, `href="/gallery/"`, `href="/movement"`, `href="/studies"`, `href="/exhibition"`, `href="/judge"`}
	chrome := []string{`class="tea-chrome"`, `href="/tea.css"`, `class="tea-nav"`}
	for _, page := range pages {
		raw, err := os.ReadFile(page)
		if err != nil {
			t.Fatal(err)
		}
		source := string(raw)
		for _, link := range links {
			if !strings.Contains(source, link) {
				t.Errorf("%s displaced persistent navigation link %s", filepath.Base(page), link)
			}
		}
		for _, token := range chrome {
			if !strings.Contains(source, token) {
				t.Errorf("%s left the shared Tea parchment chrome (%s)", filepath.Base(page), token)
			}
		}
	}
	css, err := os.ReadFile(filepath.Join(root, "apps", "tea", "public", "tea.css"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(css), `--paper: #f7f5f0`) {
		t.Error("tea.css lost the Living Parchment paper color")
	}
	gallery, err := os.ReadFile(filepath.Join(root, "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(gallery), `#05060a`) {
		t.Error("gallery returned to a separate dark surface")
	}
}

func TestStallionIsACompleteSingleExhibition(t *testing.T) {
	root := repoRoot(t)
	raw, err := os.ReadFile(filepath.Join(root, "apps", "tea", "public", "stallion.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	for _, token := range []string{
		`7,584`,
		`stallion-atlas-grid.jpg`,
		`stallion-gait-projection.mp4`,
		`autoplay muted loop playsinline`,
		`140 states`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("single Stallion exhibition missing %q", token)
		}
	}
	index, err := os.ReadFile(filepath.Join(root, "apps", "tea", "public", "exhibition.html"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(index), `href="/exhibition/stallion"`) {
		t.Error("exhibitions index does not open the Stallion single exhibition")
	}
}
