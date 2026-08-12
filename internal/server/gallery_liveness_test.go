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
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "web", "atelier-flux", "index.html"))
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
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "web", "atelier-flux", "index.html"))
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
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "web", "atelier-flux", "index.html"))
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
