package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The public wall presents art, not the machinery that makes or judges it.
// Operational state belongs in an operator surface even when it is useful.
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
	for _, token := range []string{`setInterval(`, `poll('/api/`} {
		if strings.Contains(source, token) {
			t.Errorf("gallery must use pushed events, found polling path %q", token)
		}
	}
}

func TestGalleryLoadsContinuouslyOnScroll(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "gallery.html"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(page)
	for _, token := range []string{`class="scroll-sentinel"`, `new IntersectionObserver`, `rootMargin: '800px 0px'`, `requestAnimationFrame(fillViewport)`} {
		if !strings.Contains(source, token) {
			t.Errorf("gallery infinite-scroll contract missing %q", token)
		}
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
		`id="sequence-grid"`,
		`Chronological · no selection or rearrangement`,
		`sort((a,b)=>a.index-b.index)`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("motion work contract missing %q", token)
		}
	}
	for _, token := range []string{`type="range"`, `onclick=`} {
		if strings.Contains(source, token) {
			t.Errorf("motion work returned an interactive slider/control: %q", token)
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
	}
	links := []string{`href="/"`, `href="/gallery/"`, `href="/movement"`, `href="/studies"`, `href="/exhibition"`}
	for _, page := range pages {
		raw, err := os.ReadFile(page)
		if err != nil {
			t.Fatal(err)
		}
		for _, link := range links {
			if !strings.Contains(string(raw), link) {
				t.Errorf("%s displaced persistent navigation link %s", filepath.Base(page), link)
			}
		}
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
