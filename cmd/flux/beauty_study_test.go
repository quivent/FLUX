package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"testing"
)

func TestBeautySiteNameMatchesGalleryGrammar(t *testing.T) {
	// 2026-09-27 12:34:56 UTC, cycle 1234 -> suffix 234
	got := beautySiteName("nudity-in-nature", 1790512496, 1234)
	want := "protocol-nudity-in-nature-stream-20260927-123456-234.png"
	if got != want {
		t.Fatalf("beautySiteName = %q; want %q", got, want)
	}
}

func TestBeautyStudyConfigUpdate(t *testing.T) {
	current := map[string]any{"weights": map[string]any{"beauty": 3.0, "change": 2.0}}
	update, err := beautyStudyConfigUpdate(current, []string{"gain=0.7", "writer=off", "weights.beauty=4"})
	if err != nil {
		t.Fatal(err)
	}
	if update["gain"] != 0.7 || update["writer"] != "off" {
		t.Fatalf("unexpected update %v", update)
	}
	w := update["weights"].(map[string]any)
	if w["beauty"] != 4.0 || w["change"] != 2.0 {
		t.Fatalf("weights not merged: %v", w)
	}
	if _, err := beautyStudyConfigUpdate(current, []string{"nope=1"}); err == nil {
		t.Fatal("expected unknown key to be rejected")
	}
	if _, err := beautyStudyConfigUpdate(current, []string{"weights=3"}); err == nil {
		t.Fatal("expected non-object weights to be rejected")
	}
}

func TestBeautyPublishStagesFramesSkipsRemovedAndLinked(t *testing.T) {
	root := t.TempDir()
	collections := filepath.Join(root, "collections")
	pictures := filepath.Join(root, "influx-outputs")
	folder := filepath.Join(collections, "study")
	must(t, os.MkdirAll(filepath.Join(folder, "renders"), 0o755))
	must(t, os.MkdirAll(pictures, 0o755))
	history := ""
	for c := 1; c <= 3; c++ {
		file := "renders/c000" + strconv.Itoa(c) + ".png"
		must(t, os.WriteFile(filepath.Join(folder, file), []byte("frame"+strconv.Itoa(c)), 0o644))
		row, _ := json.Marshal(map[string]any{"cycle": c, "file": file, "at": 1790512496 + c})
		history += string(row) + "\n"
	}
	history += `{"outcome":"teacher mark"}` + "\n"
	must(t, os.WriteFile(filepath.Join(folder, "history.jsonl"), []byte(history), 0o644))
	must(t, os.WriteFile(filepath.Join(folder, "removed.json"), []byte("[2]"), 0o644))
	must(t, os.WriteFile(filepath.Join(folder, "collection.json"), []byte(`{"name":"study","subject":"s"}`), 0o644))
	// cycle 3 was already staged by the loop under another timestamp
	must(t, os.Link(filepath.Join(folder, "renders/c0003.png"), filepath.Join(pictures, "protocol-album-stream-20260101-000000-003.png")))
	active := filepath.Join(root, "active.json")
	must(t, os.WriteFile(active, []byte(`{"name":"study","active":true}`), 0o644))

	err := beautyPublish([]string{"--study", "study", "--album", "album", "--title", "T",
		"--collections", collections, "--pictures", pictures, "--active", active})
	if err != nil {
		t.Fatal(err)
	}
	entries, _ := os.ReadDir(pictures)
	if len(entries) != 2 {
		t.Fatalf("want 2 staged files (cycle 1 new, cycle 3 existing), got %d", len(entries))
	}
	if _, err := os.Stat(filepath.Join(pictures, beautySiteName("album", 1790512497, 1))); err != nil {
		t.Fatalf("cycle 1 not staged: %v", err)
	}
	for _, p := range []string{filepath.Join(folder, "collection.json"), active} {
		var c map[string]any
		raw, _ := os.ReadFile(p)
		must(t, json.Unmarshal(raw, &c))
		if c["album"] != "album" || c["title"] != "T" {
			t.Fatalf("%s: album/title not set: %v", p, c)
		}
	}
}

func TestBeautyScoreRoutesBeautyToMark(t *testing.T) {
	var gotPath string
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	defer srv.Close()
	must(t, beautyScore([]string{"--url", srv.URL, "--study", "s", "--cycle", "7", "--dim", "beauty", "--value", "5"}))
	if gotPath != "/api/collection/mark" || gotBody["mark"] != 5.0 || gotBody["cycle"] != 7.0 {
		t.Fatalf("beauty score went to %s with %v", gotPath, gotBody)
	}
	must(t, beautyScore([]string{"--url", srv.URL, "--study", "s", "--cycle", "7", "--dim", "direction", "--value", "-3"}))
	if gotPath != "/api/collection/score" || gotBody["dim"] != "direction" || gotBody["value"] != -3.0 {
		t.Fatalf("direction score went to %s with %v", gotPath, gotBody)
	}
	if err := beautyScore([]string{"--url", srv.URL, "--study", "s", "--cycle", "7", "--value", "21"}); err == nil {
		t.Fatal("expected out-of-range value to be rejected")
	}
}

func TestBeautyRemoveRestoreSendsRemovedFalse(t *testing.T) {
	var gotBody map[string]any
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&gotBody)
		_, _ = w.Write([]byte(`{"ok":true,"removed":[]}`))
	}))
	defer srv.Close()
	must(t, beautyRemove([]string{"--url", srv.URL, "--study", "s", "--cycle", "4", "--restore"}))
	if gotBody["removed"] != false {
		t.Fatalf("restore sent %v", gotBody)
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}
