package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestGalleryPageReferer(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/api/recent-images?scope=microgreens", nil)
	req.Header.Set("Referer", "https://tea.geijutsu.work/gallery/")
	if !galleryPageReferer(req) {
		t.Fatal("gallery referer")
	}
	req.Header.Set("Referer", "https://tea.geijutsu.work/collections/microgreens")
	if galleryPageReferer(req) {
		t.Fatal("collection must not rewrite")
	}
}

func TestGalleryLiveScopeRewritesSproutsOffTheWall(t *testing.T) {
	s := Server{}
	req := httptest.NewRequest(http.MethodGet, "/api/recent-images?scope=microgreens", nil)
	req.Header.Set("Referer", "https://tea.geijutsu.work/gallery/")
	if got := s.galleryLiveScope(req, "microgreens"); got == "microgreens" || got == "" {
		t.Fatalf("gallery still scoped to sprouts: %q", got)
	}
	coll := httptest.NewRequest(http.MethodGet, "/api/recent-images?scope=microgreens", nil)
	coll.Header.Set("Referer", "https://tea.geijutsu.work/collections/microgreens")
	if got := s.galleryLiveScope(coll, "microgreens"); got != "microgreens" {
		t.Fatalf("microgreens collection lost its own scope: %q", got)
	}
}
