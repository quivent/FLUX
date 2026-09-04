package jury

import (
	"os"
	"path/filepath"
	"testing"
)

func TestGetSpectaclesJoinsFingerprintAndRanksByScore(t *testing.T) {
	dir := t.TempDir()
	low := filepath.Join(dir, "low.png")
	high := filepath.Join(dir, "high.png")
	if err := os.WriteFile(low, []byte("low"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(high, []byte("high"), 0o644); err != nil {
		t.Fatal(err)
	}
	db, err := InitDB(dir)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO jury_verdicts (job_id, seed, prompt, composite_score, raw_score, percentile_rank, masterpiece, created_at)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?, ?)`,
		"job-low", "1", "low", 91.0, 10.0, 91.0, 0, 100,
		"job-high", "2", "high", 99.5, 40.0, 99.5, 1, 50)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.Exec(`INSERT INTO visual_fingerprints (job_id, filepath, uniqueness_score, category, created_at)
		VALUES (?, ?, 1, 'x', 1), (?, ?, 1, 'x', 1)`,
		"job-low", low, "job-high", high)
	if err != nil {
		t.Fatal(err)
	}

	items, err := GetSpectacles(dir, 12)
	if err != nil {
		t.Fatal(err)
	}
	if len(items) != 2 {
		t.Fatalf("got %d spectacles, want 2", len(items))
	}
	if items[0].JobID != "job-high" || items[0].ImageURL != "/outputs/high.png" {
		t.Fatalf("top rated = %+v, want job-high with /outputs/high.png", items[0])
	}
	if items[1].ImageURL != "/outputs/low.png" {
		t.Fatalf("second = %+v", items[1])
	}
}
