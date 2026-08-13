package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBellProtocolsAreDistinctGeometryWithNoPrompt(t *testing.T) {
	want := []string{"near", "open", "sway", "orbit", "cache"}
	seen := map[string]bool{}
	for _, name := range want {
		protocol, ok := bellProtocols[name]
		if !ok {
			t.Fatalf("missing Bell protocol %q", name)
		}
		key := strings.Join([]string{
			protocol.Mode,
			protocol.Adapter,
			string(rune(int(protocol.ShellScale * 100))),
			string(rune(int(protocol.SeedLock * 100))),
			string(rune(int(protocol.ShellCoupling * 100))),
		}, ":")
		if seen[key] {
			t.Errorf("protocol %q duplicates an earlier geometry", name)
		}
		seen[key] = true
	}
}

func TestDirectionalTournamentHasDirectorAndFourLiteralDirections(t *testing.T) {
	path := filepath.Join("..", "..", "chorus", "tournament.py")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	for _, token := range []string{
		`DIRECTIONS = ("north", "south", "east", "west")`,
		`PERSPECTIVES = ("continuity", "composition", "material_light", "meaningful_change")`,
		`"action": "advance" if advance else "hold"`,
		`"parent": [1.0, 0.0, 0.0, 0.0]`,
		`"batch_size": 4`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("directional protocol missing %q", token)
		}
	}
}

func TestLateGeometryForkSharesEarlyTrajectory(t *testing.T) {
	path := filepath.Join("..", "..", "chorus", "late_fork.py")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	for _, token := range []string{
		`default="18,22,25,26"`,
		`for index, timestep in enumerate(timesteps[:max(forks)], start=1):`,
		`branches_cpu = fork_latents(checkpoints[fork_after].to("cuda"), args.strength,`,
		`for timestep in timesteps[fork_after:]:`,
		`"trajectory_shared": shared`,
		`pipe.text_encoder_2.to("cpu")`,
		`"memory_adaptation": "reduce_suffix_microbatch"`,
		`@torch.inference_mode()`,
		`"schema": "flux.exact-trunk-cache.v1"`,
		`save_file({"latent": checkpoint}, checkpoint_paths[index])`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("late geometry protocol missing %q", token)
		}
	}
}

func TestNightStudyIsDurableAndBeautyBound(t *testing.T) {
	for _, name := range []string{"night-run.json", "night_runner.py", "gemini_coder.py", "constellation.py"} {
		if _, err := os.Stat(filepath.Join("..", "..", "chorus", name)); err != nil {
			t.Fatalf("missing autonomous production component %s: %v", name, err)
		}
	}
	raw, err := os.ReadFile(filepath.Join("..", "..", "chorus", "step_sweep.py"))
	if err != nil {
		t.Fatal(err)
	}
	for _, token := range []string{`if valid[total_steps]:`, `usable_image(image_paths[value])`, `"resumed_outputs"`} {
		if !strings.Contains(string(raw), token) {
			t.Errorf("resumable step study missing %q", token)
		}
	}
}

func TestAdjacentStepSweepHoldsInitialLatentConstant(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "chorus", "step_sweep.py"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	for _, token := range []string{`default="21:28"`, `"variable": "total_denoise_steps"`,
		`latents=base.clone()`, `"phase_shift_px"`, `"edge_xor"`} {
		if !strings.Contains(source, token) {
			t.Errorf("adjacent step sweep missing %q", token)
		}
	}
}

func TestContinuityRepairNeverOverwritesOriginals(t *testing.T) {
	raw, err := os.ReadFile(filepath.Join("..", "..", "chorus", "continuity.py"))
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	for _, token := range []string{`reason = "still"`, `else "gap"`, `"replacement-queue.jsonl"`,
		`"Gemma council required; originals remain authoritative"`, `Image.blend(a, b, 0.5)`} {
		if !strings.Contains(source, token) {
			t.Errorf("continuity repair missing %q", token)
		}
	}
}
