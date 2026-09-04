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

func TestNormalizeProtocolBranch(t *testing.T) {
	slug, err := normalizeProtocolBranch(" Silk Road ")
	if err != nil || slug != "silk-road" {
		t.Fatalf("got %q %v", slug, err)
	}
	if _, err := normalizeProtocolBranch("fashion"); err == nil {
		t.Fatal("fashion must stay reserved")
	}
	if _, err := normalizeProtocolBranch(""); err == nil {
		t.Fatal("empty name must fail")
	}
}

func TestPublishCompletedJobAssetsKeepsCollectionPath(t *testing.T) {
	output := t.TempDir()
	rel := filepath.Join("collections", "silk", "protocol-silk-001.png")
	path := filepath.Join(output, rel)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("png"), 0o644); err != nil {
		t.Fatal(err)
	}
	s := Server{cfg: config.Config{OutputDir: output}}
	s.publishCompletedJobAssets([]map[string]any{
		{"id": "job-silk", "status": "done", "output": path, "filename": filepath.ToSlash(rel)},
	})
	want := "/outputs/collections/silk/protocol-silk-001.png"
	motionAssetHub.Lock()
	defer motionAssetHub.Unlock()
	for _, ev := range motionAssetHub.recent {
		asset, _ := ev["asset"].(map[string]any)
		if stringValue(ev["job_id"]) == "job-silk" && stringValue(asset["access_url"]) == want {
			return
		}
	}
	t.Fatalf("asset stream flattened the collection path, recent=%v", motionAssetHub.recent)
}

func TestProtocolBranchesAPIListsCap(t *testing.T) {
	root := t.TempDir()
	output := t.TempDir()
	s := Server{cfg: config.Config{Root: root, OutputDir: output, Python: "/bin/true"}}
	rec := httptest.NewRecorder()
	s.protocolBranchesAPI(rec, httptest.NewRequest(http.MethodGet, "/api/protocol/branches", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d: %s", rec.Code, rec.Body.String())
	}
	var payload struct {
		Cap int  `json:"cap"`
		OK  bool `json:"ok"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if !payload.OK || payload.Cap != protocolBranchCap {
		t.Fatalf("payload %+v", payload)
	}
}

func TestProtocolBranchHarvestDefaultsToQualityNotThumbnails(t *testing.T) {
	n, steps, depth, width, height := clampProtocolBranchHarvest(0, 0, 0, 0, 0)
	if n != 0 || steps != 28 || depth != 1 || width != 1024 || height != 1024 {
		t.Fatalf("defaults n=%d steps=%d depth=%d %dx%d", n, steps, depth, width, height)
	}
	n, steps, _, width, height = clampProtocolBranchHarvest(512, 18, 1, 256, 256)
	if n != 512 || steps != 18 || width != 256 || height != 256 {
		t.Fatalf("explicit thumbnail harvest must still be allowed: n=%d steps=%d %dx%d", n, steps, width, height)
	}
	n, _, _, _, _ = clampProtocolBranchHarvest(256, 28, 1, 1024, 1024)
	if n != 256 {
		t.Fatalf("256 must remain legal, got %d", n)
	}
}

func TestProtocolBranchStartRejectsReservedAndEmptyPrompt(t *testing.T) {
	s := Server{cfg: config.Config{Root: t.TempDir(), OutputDir: t.TempDir(), Python: "/bin/true"}}
	post := func(body string) *httptest.ResponseRecorder {
		rec := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodPost, "/api/protocol/branches", strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		s.protocolBranchesAPI(rec, req)
		return rec
	}
	if rec := post(`{"name":"fashion","prompt":"a dress"}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("fashion reserved: %d %s", rec.Code, rec.Body.String())
	}
	if rec := post(`{"name":"silk"}`); rec.Code != http.StatusBadRequest {
		t.Fatalf("missing prompt: %d %s", rec.Code, rec.Body.String())
	}
}
