package server

import (
	"encoding/json"
	"os"
	"path/filepath"
	"sort"
)

// A sphere rendered by the fleet is written by several workers into one
// directory, each keeping its own progress.shardN.json. This file reassembles
// those into the single progress view the UI and gallery already expect, so
// nothing downstream needs to know whether a sphere was rendered by one GPU or
// four.

// readAtlasProgress returns the merged progress for an atlas output directory,
// or nil when it has no progress files at all.
//
// An unsharded render still writes plain progress.json and is returned as-is.
func readAtlasProgress(dir string) map[string]any {
	shards := readShardProgress(dir)
	if len(shards) == 0 {
		var progress map[string]any
		raw, err := os.ReadFile(filepath.Join(dir, "progress.json"))
		if err != nil || json.Unmarshal(raw, &progress) != nil {
			return nil
		}
		return progress
	}
	if len(shards) == 1 && intValue(shards[0]["shard_total"]) <= 1 {
		return shards[0]
	}
	return mergeShardProgress(shards)
}

// readShardProgress loads every progress.shardN.json in a directory, ordered by
// shard id so the merged view is stable across calls.
func readShardProgress(dir string) []map[string]any {
	matches, err := filepath.Glob(filepath.Join(dir, "progress.shard*.json"))
	if err != nil || len(matches) == 0 {
		return nil
	}
	shards := make([]map[string]any, 0, len(matches))
	for _, path := range matches {
		raw, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		var progress map[string]any
		if json.Unmarshal(raw, &progress) != nil {
			continue
		}
		shards = append(shards, progress)
	}
	sort.Slice(shards, func(i, j int) bool {
		return intValue(shards[i]["shard_id"]) < intValue(shards[j]["shard_id"])
	})
	return shards
}

func mergeShardProgress(shards []map[string]any) map[string]any {
	merged := map[string]any{}
	for k, v := range shards[0] {
		merged[k] = v
	}

	var (
		current, total, fullTotal          int
		cellsPerHour, elapsed, eta, lastTS float64
		cacheChecks, cacheHits, cacheMiss  int
		newest                             map[string]any
	)
	for _, shard := range shards {
		current += intValue(shard["current"])
		total += intValue(shard["total"])
		cellsPerHour += floatValue(shard["cells_per_hour"])
		cacheChecks += intValue(shard["cache_checks"])
		cacheHits += intValue(shard["cache_hits"])
		cacheMiss += intValue(shard["cache_misses"])
		// Shards start together but finish apart, so the sphere's elapsed time
		// and ETA are the slowest shard's, not a sum or an average.
		if v := floatValue(shard["elapsed_seconds"]); v > elapsed {
			elapsed = v
		}
		if v := floatValue(shard["eta_seconds"]); v > eta {
			eta = v
		}
		if v := intValue(shard["full_total"]); v > fullTotal {
			fullTotal = v
		}
		if ts := floatValue(shard["ts"]); ts >= lastTS {
			lastTS = ts
			newest = shard
		}
	}
	if fullTotal <= 0 {
		fullTotal = total
	}

	merged["current"] = current
	merged["total"] = fullTotal
	merged["full_total"] = fullTotal
	merged["shard_count"] = len(shards)
	merged["cells_per_hour"] = cellsPerHour
	merged["elapsed_seconds"] = elapsed
	merged["eta_seconds"] = eta
	merged["cache_checks"] = cacheChecks
	merged["cache_hits"] = cacheHits
	merged["cache_misses"] = cacheMiss
	if cacheChecks > 0 {
		merged["cache_hit_rate"] = float64(cacheHits) / float64(cacheChecks)
	} else {
		merged["cache_hit_rate"] = 0.0
	}
	// Per-cell fields describe a single render, so take them from whichever
	// shard reported most recently rather than combining them.
	if newest != nil {
		merged["ts"] = newest["ts"]
		merged["current_index"] = newest["current_index"]
		merged["last_cell_seconds"] = newest["last_cell_seconds"]
		merged["last_cell_role"] = newest["last_cell_role"]
		merged["last_cell_steps"] = newest["last_cell_steps"]
	}
	delete(merged, "shard_id")
	return merged
}

// atlasProgressTotal reports the whole-sphere cell count for an output
// directory, preferring live progress over the manifest and tolerating either
// being absent.
func atlasProgressTotal(baseAbs string) int {
	if progress := readAtlasProgress(baseAbs); progress != nil {
		for _, key := range []string{"full_total", "total", "render_total", "render_count"} {
			if value := intValue(progress[key]); value > 0 {
				return value
			}
		}
	}
	raw, err := os.ReadFile(filepath.Join(baseAbs, "manifest.json"))
	if err != nil {
		return 0
	}
	var manifest map[string]any
	if json.Unmarshal(raw, &manifest) != nil {
		return 0
	}
	for _, key := range []string{"render_total", "total", "render_count"} {
		if value := intValue(manifest[key]); value > 0 {
			return value
		}
	}
	return 0
}
