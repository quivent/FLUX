package main

import (
	"fmt"
	"strconv"
	"strings"

	"local/flux/internal/config"
	"local/flux/internal/fleet"
	"local/flux/internal/ui"
)

// fleetCmd drives the per-GPU worker pool from the CLI: one worker per GPU,
// each pinned with CUDA_VISIBLE_DEVICES.
func fleetCmd(cfg config.Config, args []string) error {
	action := "status"
	if len(args) > 0 {
		action = strings.ToLower(strings.TrimSpace(args[0]))
	}
	pool := fleet.New(cfg)

	switch action {
	case "status", "":
		return fleetStatus(pool)
	case "up", "start":
		return fleetUp(pool, args[1:])
	case "down", "stop":
		return fleetDown(pool)
	case "jobs":
		return fleetJobs(pool)
	case "update", "tune":
		return fleetUpdate(pool, args[1:])
	default:
		return fmt.Errorf("unknown fleet action %q (use status, up, down, jobs, or update)", action)
	}
}

// fleetUpdate retunes a running job: flux fleet update <id> guidance=3.0 steps=20
func fleetUpdate(pool fleet.Pool, args []string) error {
	if !pool.Enabled() {
		return fmt.Errorf("no CUDA GPUs detected")
	}
	if len(args) < 2 {
		return fmt.Errorf("usage: flux fleet update <job-id> field=value [field=value...]\n" +
			"  live fields: guidance, steps, batch_size")
	}
	id := strings.TrimSpace(args[0])
	fields := map[string]any{}
	for _, arg := range args[1:] {
		key, raw, found := strings.Cut(arg, "=")
		if !found {
			return fmt.Errorf("expected field=value, got %q", arg)
		}
		key = strings.TrimSpace(key)
		raw = strings.TrimSpace(raw)
		// Send numbers as numbers so the worker's cast and range check apply
		// to the real value rather than a string.
		if n, err := strconv.ParseFloat(raw, 64); err == nil {
			fields[key] = n
		} else {
			fields[key] = raw
		}
	}

	result, err := pool.Update(id, fields)
	if err != nil {
		return err
	}
	ui.Header("FLUX fleet update", id)
	ui.KV("shards updated", result["shards_updated"])
	if changed, ok := result["changed"].(map[string]any); ok && len(changed) > 0 {
		for key, delta := range changed {
			if d, ok := delta.(map[string]any); ok {
				ui.Pair(key, fmt.Sprintf("%v %s %v", d["from"], ui.Soft("→"), d["to"]))
			}
		}
	} else {
		fmt.Println(ui.Soft("  No values changed."))
	}
	if rejected, ok := result["rejected"].(map[string]any); ok && len(rejected) > 0 {
		ui.Rule()
		for key, reason := range rejected {
			ui.Pair(ui.Bad(key), ui.Soft(fmt.Sprint(reason)))
		}
	}
	return nil
}

func fleetStatus(pool fleet.Pool) error {
	ui.Header("FLUX fleet", "one worker per GPU")
	if !pool.Enabled() {
		ui.KV("gpus", "none detected")
		fmt.Println(ui.Soft("  No CUDA GPUs visible; flux runs the single default worker."))
		return nil
	}
	ui.KV("gpus", fmt.Sprint(pool.GPUs()))
	ui.KV("workers", pool.Size())
	ui.Rule()

	up := 0
	for _, status := range pool.Status() {
		state := ui.Bad("down")
		detail := status.Error
		if status.Up {
			up++
			state = ui.Good("up")
			detail = status.Device
			if status.Loaded {
				detail += " · model loaded"
			} else {
				detail += " · model cold"
			}
			detail += fmt.Sprintf(" · %d job(s), %d active", status.Jobs, status.Active)
		}
		ui.Pair(fmt.Sprintf("gpu%d %s", status.GPU, status.Name), state+" "+ui.Soft(detail))
	}
	ui.Rule()
	ui.KV("reachable", fmt.Sprintf("%d/%d", up, pool.Size()))
	if up == 0 {
		fmt.Println(ui.Soft("  Start them with: flux fleet up"))
	}
	return nil
}

func fleetUp(pool fleet.Pool, args []string) error {
	if !pool.Enabled() {
		return fmt.Errorf("no CUDA GPUs detected; nothing to start")
	}
	preload := false
	for _, arg := range args {
		if arg == "--preload" || arg == "-p" {
			preload = true
		}
	}
	ui.Header("FLUX fleet up", fmt.Sprintf("%d GPU(s)", pool.Size()))
	if preload {
		fmt.Println(ui.Soft("  Preloading the model on every GPU; this takes a while."))
	}
	if err := pool.Start(preload); err != nil {
		return err
	}
	return fleetStatus(pool)
}

func fleetDown(pool fleet.Pool) error {
	if !pool.Enabled() {
		return fmt.Errorf("no CUDA GPUs detected; nothing to stop")
	}
	ui.Header("FLUX fleet down", fmt.Sprintf("%d GPU(s)", pool.Size()))
	errs := pool.Stop()
	for _, err := range errs {
		fmt.Println(ui.Soft("  " + err.Error()))
	}
	stopped := pool.Size() - len(errs)
	ui.KV("stopped", fmt.Sprintf("%d/%d", stopped, pool.Size()))
	return nil
}

func fleetJobs(pool fleet.Pool) error {
	if !pool.Enabled() {
		return fmt.Errorf("no CUDA GPUs detected")
	}
	snap := pool.Snapshot()
	ui.Header("FLUX fleet jobs", fmt.Sprintf("%d/%d worker(s) up", snap.Up, pool.Size()))
	if len(snap.Jobs) == 0 {
		fmt.Println(ui.Soft("  No jobs."))
		return nil
	}
	for _, job := range snap.Jobs {
		id, _ := job["id"].(string)
		status, _ := job["status"].(string)
		phase, _ := job["phase"].(string)
		line := fmt.Sprintf("%s %s", ui.Badge(status), ui.Soft(phase))
		// A sharded sphere reports where each slice is running, which is the
		// only place the per-GPU split is visible once jobs are merged.
		if shards, ok := job["shards"].([]map[string]any); ok && len(shards) > 0 {
			parts := make([]string, 0, len(shards))
			for _, shard := range shards {
				parts = append(parts, fmt.Sprintf("gpu%v:%v/%v", shard["gpu"], shard["step"], shard["total"]))
			}
			line += " " + ui.Soft("["+strings.Join(parts, " ")+"]")
		} else if worker, ok := job["worker"].(string); ok {
			line += " " + ui.Soft(worker)
		}
		ui.Pair(id, line)
	}
	return nil
}
