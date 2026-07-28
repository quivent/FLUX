package server

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func writeJSONFile(t *testing.T, path string, body map[string]any) {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, raw, 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestReadAtlasProgressMergesShards(t *testing.T) {
	dir := t.TempDir()
	writeJSONFile(t, filepath.Join(dir, "progress.shard0.json"), map[string]any{
		"job_id": "s1", "shard_id": 0, "shard_total": 4, "current": 3, "total": 4, "full_total": 16,
		"cells_per_hour": 100.0, "elapsed_seconds": 50.0, "eta_seconds": 10.0,
		"cache_checks": 10, "cache_hits": 4, "ts": 100.0, "current_index": 8,
	})
	writeJSONFile(t, filepath.Join(dir, "progress.shard1.json"), map[string]any{
		"job_id": "s1", "shard_id": 1, "shard_total": 4, "current": 2, "total": 4, "full_total": 16,
		"cells_per_hour": 80.0, "elapsed_seconds": 60.0, "eta_seconds": 25.0,
		"cache_checks": 10, "cache_hits": 6, "ts": 200.0, "current_index": 9,
	})

	progress := readAtlasProgress(dir)
	if progress == nil {
		t.Fatal("expected merged progress, got nil")
	}
	if got := intValue(progress["current"]); got != 5 {
		t.Errorf("current = %d, want 5 (sum)", got)
	}
	if got := intValue(progress["total"]); got != 16 {
		t.Errorf("total = %d, want 16 (whole sphere, not sum of shard totals)", got)
	}
	if got := floatValue(progress["cells_per_hour"]); got != 180.0 {
		t.Errorf("cells_per_hour = %v, want 180 (sum)", got)
	}
	if got := floatValue(progress["elapsed_seconds"]); got != 60.0 {
		t.Errorf("elapsed_seconds = %v, want 60 (slowest shard)", got)
	}
	if got := floatValue(progress["eta_seconds"]); got != 25.0 {
		t.Errorf("eta_seconds = %v, want 25 (slowest shard)", got)
	}
	if got := floatValue(progress["cache_hit_rate"]); got != 0.5 {
		t.Errorf("cache_hit_rate = %v, want 0.5 (10/20)", got)
	}
	// Per-cell fields come from the shard that reported most recently.
	if got := intValue(progress["current_index"]); got != 9 {
		t.Errorf("current_index = %d, want 9 (newest shard)", got)
	}
	if got := intValue(progress["shard_count"]); got != 2 {
		t.Errorf("shard_count = %d, want 2", got)
	}
}

func TestReadAtlasProgressUnsharded(t *testing.T) {
	dir := t.TempDir()
	writeJSONFile(t, filepath.Join(dir, "progress.json"), map[string]any{
		"job_id": "s1", "current": 7, "total": 16,
	})
	progress := readAtlasProgress(dir)
	if progress == nil {
		t.Fatal("expected progress, got nil")
	}
	if got := intValue(progress["current"]); got != 7 {
		t.Errorf("current = %d, want 7", got)
	}
	if got := intValue(progress["total"]); got != 16 {
		t.Errorf("total = %d, want 16", got)
	}
}

func TestReadAtlasProgressAbsent(t *testing.T) {
	if progress := readAtlasProgress(t.TempDir()); progress != nil {
		t.Errorf("expected nil for a directory with no progress files, got %#v", progress)
	}
}

func TestAtlasProgressTotalFallsBackToManifest(t *testing.T) {
	dir := t.TempDir()
	writeJSONFile(t, filepath.Join(dir, "manifest.json"), map[string]any{
		"render_total": 256, "shard_total": 4,
	})
	if got := atlasProgressTotal(dir); got != 256 {
		t.Errorf("atlasProgressTotal = %d, want 256 from the manifest", got)
	}
}

func TestAtlasProgressTotalPrefersFullTotal(t *testing.T) {
	dir := t.TempDir()
	writeJSONFile(t, filepath.Join(dir, "manifest.json"), map[string]any{"render_total": 256})
	writeJSONFile(t, filepath.Join(dir, "progress.shard0.json"), map[string]any{
		"shard_id": 0, "shard_total": 4, "current": 1, "total": 64, "full_total": 256,
	})
	// The per-shard "total" of 64 must never surface as the sphere's total.
	if got := atlasProgressTotal(dir); got != 256 {
		t.Errorf("atlasProgressTotal = %d, want 256", got)
	}
}
