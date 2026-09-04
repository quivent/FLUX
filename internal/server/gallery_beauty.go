package server

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
)

type recentImage struct {
	Name           string
	Path           string
	URL            string
	Kind           string
	Modified       int64
	Composite      *float64
	BeautyDeltaPct *float64
}

func attachBeautyDifferentials(outputDir string, items []recentImage) {
	if len(items) == 0 {
		return
	}
	scores := loadAuditComposites(outputDir)
	if len(scores) == 0 {
		return
	}
	ordered := append([]recentImage(nil), items...)
	sort.Slice(ordered, func(i, j int) bool { return ordered[i].Name < ordered[j].Name })
	var prev float64
	hasPrev := false
	comp := map[string]float64{}
	delta := map[string]float64{}
	hasDelta := map[string]bool{}
	for _, item := range ordered {
		sc, ok := scores[item.Name]
		if !ok {
			continue
		}
		comp[item.Name] = sc
		if hasPrev {
			// Composite is already 0–100 beauty. The overlay is the point
			// change versus the previous scored frame, shown as +0.3%.
			delta[item.Name] = sc - prev
			hasDelta[item.Name] = true
		}
		prev = sc
		hasPrev = true
	}
	for i := range items {
		if sc, ok := comp[items[i].Name]; ok {
			v := sc
			items[i].Composite = &v
		}
		if hasDelta[items[i].Name] {
			d := delta[items[i].Name]
			items[i].BeautyDeltaPct = &d
		}
	}
}

func loadAuditComposites(outputDir string) map[string]float64 {
	out := map[string]float64{}
	paths := []string{filepath.Join(outputDir, "audit.jsonl")}
	entries, _ := os.ReadDir(filepath.Join(outputDir, "collections"))
	for _, entry := range entries {
		if entry.IsDir() {
			paths = append(paths, filepath.Join(outputDir, "collections", entry.Name(), "audit.jsonl"))
		}
	}
	for _, path := range paths {
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
		for sc.Scan() {
			var rec map[string]any
			if json.Unmarshal(sc.Bytes(), &rec) != nil {
				continue
			}
			if unscored, _ := rec["unscored"].(bool); unscored {
				continue
			}
			img, _ := rec["image_path"].(string)
			if img == "" {
				img, _ = rec["filename"].(string)
			}
			if img == "" {
				continue
			}
			score, ok := asFloat(rec["composite"])
			if !ok {
				score, ok = asFloat(rec["raw_composite"])
			}
			if !ok {
				continue
			}
			out[filepath.Base(img)] = score
		}
		_ = f.Close()
	}
	return out
}
