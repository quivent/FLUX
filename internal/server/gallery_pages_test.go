package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"local/flux/internal/config"
)

// The gallery pages through the archive with limit+offset. Every frame must be
// reachable exactly once, newest first, and total must count the whole room.
func TestRecentImagesPagesCoverTheWholeArchive(t *testing.T) {
	output := t.TempDir()
	const n = 73
	base := time.Now().Add(-time.Hour)
	for i := 0; i < n; i++ {
		path := filepath.Join(output, fmt.Sprintf("flux-cuda-seed-%03d.png", i))
		if err := os.WriteFile(path, []byte("x"), 0o644); err != nil {
			t.Fatal(err)
		}
		at := base.Add(time.Duration(i) * time.Second)
		if err := os.Chtimes(path, at, at); err != nil {
			t.Fatal(err)
		}
	}
	s := Server{cfg: config.Config{Root: repoRoot(t), OutputDir: output}}
	type page struct {
		Images []struct {
			Name     string `json:"name"`
			Modified int64  `json:"modified"`
		} `json:"images"`
		Total  int `json:"total"`
		Offset int `json:"offset"`
		Limit  int `json:"limit"`
	}
	get := func(offset int) page {
		rec := httptest.NewRecorder()
		s.recentImages(rec, httptest.NewRequest(http.MethodGet, fmt.Sprintf("/api/recent-images?scope=fashion&limit=30&offset=%d", offset), nil))
		if rec.Code != http.StatusOK {
			t.Fatalf("offset %d status %d: %s", offset, rec.Code, rec.Body.String())
		}
		var p page
		if err := json.Unmarshal(rec.Body.Bytes(), &p); err != nil {
			t.Fatal(err)
		}
		return p
	}

	seen := map[string]bool{}
	var names []string
	for offset := 0; offset < n; offset += 30 {
		p := get(offset)
		if p.Total != n || p.Offset != offset || p.Limit != 30 {
			t.Fatalf("offset %d: total=%d offset=%d limit=%d", offset, p.Total, p.Offset, p.Limit)
		}
		want := 30
		if n-offset < want {
			want = n - offset
		}
		if len(p.Images) != want {
			t.Fatalf("offset %d: %d images, want %d", offset, len(p.Images), want)
		}
		for _, im := range p.Images {
			if seen[im.Name] {
				t.Fatalf("%s appears on two pages", im.Name)
			}
			seen[im.Name] = true
			names = append(names, im.Name)
		}
	}
	if len(seen) != n {
		t.Fatalf("pages reached %d of %d frames", len(seen), n)
	}
	for i, name := range names {
		if want := fmt.Sprintf("flux-cuda-seed-%03d.png", n-1-i); name != want {
			t.Fatalf("position %d is %s, want %s (newest first across pages)", i, name, want)
		}
	}
	if past := get(n + 5); len(past.Images) != 0 || past.Total != n {
		t.Fatalf("offset past the end returned %d images, total %d", len(past.Images), past.Total)
	}
}
