//go:build !darwin

package server

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"time"
)

type fileSnapshot struct {
	count int
	sum   int64
}

func waitForPathChange(ctx context.Context, target string) bool {
	dir := filepath.Dir(target)
	before := snapshotTree(dir, false)
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return false
		case <-ticker.C:
			if snapshotTree(dir, false) != before {
				return true
			}
		}
	}
}

func waitForTreeChange(ctx context.Context, root string) bool {
	before := snapshotTree(root, true)
	ticker := time.NewTicker(750 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return false
		case <-ticker.C:
			if snapshotTree(root, true) != before {
				return true
			}
		}
	}
}

func snapshotTree(root string, recursive bool) fileSnapshot {
	var snap fileSnapshot
	rootAbs, err := filepath.Abs(root)
	if err != nil {
		return snap
	}
	visit := func(file string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if file != rootAbs && strings.HasPrefix(entry.Name(), ".") {
				return filepath.SkipDir
			}
			if !recursive && file != rootAbs {
				return filepath.SkipDir
			}
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return nil
		}
		snap.count++
		snap.sum += info.ModTime().UnixNano()
		snap.sum += info.Size()
		return nil
	}
	if recursive {
		_ = filepath.WalkDir(rootAbs, visit)
		return snap
	}
	entries, err := os.ReadDir(rootAbs)
	if err != nil {
		return snap
	}
	for _, entry := range entries {
		_ = visit(filepath.Join(rootAbs, entry.Name()), entry, nil)
	}
	return snap
}
