package fleet

import "testing"

func TestMergeShardJobsSumsProgress(t *testing.T) {
	shards := []map[string]any{
		{"id": "sphere1", "shard_id": 0, "shard_total": 4, "gpu": 0, "worker": "flux-gpu0",
			"status": "running", "phase": "batch 3-3", "step": 2, "total_steps": 4,
			"atlas_done": 2, "atlas_total": 4, "atlas_full_total": 16, "cells_per_hour": 100.0, "eta_seconds": 30.0},
		{"id": "sphere1", "shard_id": 1, "shard_total": 4, "gpu": 1, "worker": "flux-gpu1",
			"status": "done", "phase": "done", "step": 4, "total_steps": 4,
			"atlas_done": 4, "atlas_total": 4, "atlas_full_total": 16, "cells_per_hour": 120.0, "eta_seconds": 0.0},
		{"id": "sphere1", "shard_id": 2, "shard_total": 4, "gpu": 2, "worker": "flux-gpu2",
			"status": "running", "phase": "batch 2-2", "step": 1, "total_steps": 4,
			"atlas_done": 1, "atlas_total": 4, "atlas_full_total": 16, "cells_per_hour": 90.0, "eta_seconds": 45.0},
		{"id": "sphere1", "shard_id": 3, "shard_total": 4, "gpu": 3, "worker": "flux-gpu3",
			"status": "queued", "phase": "queued", "step": 0, "total_steps": 4,
			"atlas_done": 0, "atlas_total": 4, "atlas_full_total": 16, "cells_per_hour": 0.0, "eta_seconds": 0.0},
	}

	merged := MergeShardJobs(shards)
	if len(merged) != 1 {
		t.Fatalf("expected shards to collapse into 1 job, got %d", len(merged))
	}
	job := merged[0]

	if got := intValue(job["step"]); got != 7 {
		t.Errorf("step = %d, want 7 (sum across shards)", got)
	}
	// total_steps must be the whole sphere, not the sum of per-shard totals,
	// or the progress bar would read 7/16 as 7/16 only by coincidence.
	if got := intValue(job["total_steps"]); got != 16 {
		t.Errorf("total_steps = %d, want 16 (whole sphere)", got)
	}
	if got := intValue(job["atlas_done"]); got != 7 {
		t.Errorf("atlas_done = %d, want 7", got)
	}
	if got := floatValue(job["cells_per_hour"]); got != 310.0 {
		t.Errorf("cells_per_hour = %v, want 310 (sum)", got)
	}
	if got := floatValue(job["eta_seconds"]); got != 45.0 {
		t.Errorf("eta_seconds = %v, want 45 (slowest shard)", got)
	}
	if got := stringValue(job["phase"]); got != "batch 3-3" {
		t.Errorf("phase = %q, want a running shard's phase", got)
	}
	if got := intValue(job["shard_count"]); got != 4 {
		t.Errorf("shard_count = %d, want 4", got)
	}
	for _, key := range []string{"shard_id", "worker", "gpu"} {
		if _, present := job[key]; present {
			t.Errorf("merged job should not carry per-shard key %q", key)
		}
	}
	shardList, ok := job["shards"].([]map[string]any)
	if !ok || len(shardList) != 4 {
		t.Fatalf("expected 4 shard entries, got %#v", job["shards"])
	}
}

func TestMergeStatusPrecedence(t *testing.T) {
	cases := []struct {
		name     string
		statuses []string
		want     string
	}{
		{"any running wins", []string{"done", "running", "error"}, "running"},
		{"queued outranks error", []string{"done", "queued", "error"}, "queued"},
		{"error once nothing progresses", []string{"done", "error", "done"}, "error"},
		{"cancelled after error", []string{"done", "cancelled"}, "cancelled"},
		{"all done", []string{"done", "done", "done", "done"}, "done"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := mergeStatus(tc.statuses); got != tc.want {
				t.Errorf("mergeStatus(%v) = %q, want %q", tc.statuses, got, tc.want)
			}
		})
	}
}

func TestMergeShardJobsLeavesSoloJobsAlone(t *testing.T) {
	jobs := []map[string]any{
		{"id": "a", "status": "running", "step": 3, "total_steps": 10},
		{"id": "b", "status": "done", "step": 5, "total_steps": 5},
	}
	merged := MergeShardJobs(jobs)
	if len(merged) != 2 {
		t.Fatalf("expected 2 jobs, got %d", len(merged))
	}
	for _, job := range merged {
		if _, present := job["shards"]; present {
			t.Errorf("unsharded job %v should not gain a shards key", job["id"])
		}
	}
}

func TestCapGPUs(t *testing.T) {
	t.Setenv("FLUX_FLEET_SIZE", "2")
	if got := capGPUs([]int{0, 1, 2, 3}); len(got) != 2 {
		t.Errorf("capGPUs with FLUX_FLEET_SIZE=2 returned %v, want 2 entries", got)
	}
}
