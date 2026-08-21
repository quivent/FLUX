package main

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"local/flux/internal/arcane"
	"local/flux/internal/config"
)

// The three modes have opposite objective functions, and the field that decides
// whether a draft suits `character` is view_prompts: worker.py clears the
// cross-frame residual cache whenever prompt text changes between cells, and
// that cache is the continuity mechanism `character` depends on. These are the
// geometries measured from atlas_drafts/.
func TestDraftClassificationMatchesMeasuredGeometry(t *testing.T) {
	cases := []struct {
		name          string
		viewPrompts   int
		seedLock      float64
		shellCoupling float64
		want          []string
	}{
		{"65k", 0, 0.68, 0.35, []string{"latent"}},
		{"animation_still_24", 8, 0.38, 0.82, []string{"scenes"}},
		{"turntable_64", 0, 0.22, 0.65, []string{"character"}},
		{"turntable_elliptic_64", 0, 0.45, 0.65, []string{"character"}},
		{"wide_space_64", 8, 0.28, 0.92, []string{"latent", "scenes"}},
		{"yaw_buckets_64", 8, 0.50, 0.65, []string{"scenes"}},
		{"yaw_hard_64", 8, 0.48, 0.65, []string{"scenes"}},
		{"rose_princess_hybrid_64", 0, 0.45, 0.72, []string{"character", "scenes"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			draft := arcaneDraft{
				Name:          c.name,
				ViewPrompts:   c.viewPrompts,
				SeedLock:      c.seedLock,
				ShellCoupling: c.shellCoupling,
			}
			arcaneClassifyDraft(&draft)
			if strings.Join(draft.Modes, ",") != strings.Join(c.want, ",") {
				t.Errorf("modes = %v, want %v", draft.Modes, c.want)
			}
			if c.viewPrompts > 0 {
				if arcaneValidFor(draft, arcaneModeCharacter) {
					t.Error("a draft that flushes the residual cache must never be valid for character")
				}
				if draft.Warning == "" {
					t.Error("a draft carrying view_prompts must carry the cache-flush warning")
				}
			}
		})
	}
}

// The classification must agree with the drafts actually on disk, so the rules
// do not silently drift away from the data they were derived from.
func TestRealDraftsClassifyAsExpected(t *testing.T) {
	root := repoRoot(t)
	drafts, err := arcaneLoadDrafts(root)
	if err != nil {
		t.Fatalf("arcaneLoadDrafts: %v", err)
	}
	if len(drafts) == 0 {
		t.Skip("no drafts on disk")
	}

	want := map[string][]string{
		"arcane_italian_princess_65k":                   {"latent"},
		"arcane_italian_princess_animation_still_24":    {"scenes"},
		"arcane_italian_princess_turntable_64":          {"character"},
		"arcane_italian_princess_turntable_elliptic_64": {"character"},
		"arcane_italian_princess_wide_space_64":         {"latent", "scenes"},
		"arcane_italian_princess_yaw_buckets_64":        {"scenes"},
		"arcane_italian_princess_yaw_hard_64":           {"scenes"},
		"arcane_rose_princess_hybrid_64":                {"character", "scenes"},
	}
	seen := map[string]bool{}
	for _, draft := range drafts {
		expected, ok := want[draft.Name]
		if !ok {
			continue
		}
		seen[draft.Name] = true
		if strings.Join(draft.Modes, ",") != strings.Join(expected, ",") {
			t.Errorf("%s: modes = %v, want %v (view_prompts=%d seed_lock=%.2f shell_coupling=%.2f)",
				draft.Name, draft.Modes, expected, draft.ViewPrompts, draft.SeedLock, draft.ShellCoupling)
		}
	}
	for name := range want {
		if !seen[name] {
			t.Errorf("draft %s not found on disk", name)
		}
	}
}

func TestResolveDraftRejectsAmbiguityAndMisses(t *testing.T) {
	root := repoRoot(t)
	if _, err := arcaneResolveDraft(root, "no-such-draft-anywhere"); err == nil {
		t.Error("a miss must be an error, not an arbitrary pick")
	}
	// "turntable" matches both turntable_64 and turntable_elliptic_64.
	if _, err := arcaneResolveDraft(root, "turntable"); err == nil {
		t.Error("an ambiguous fragment must be refused")
	} else if !strings.Contains(err.Error(), "matches") {
		t.Errorf("ambiguity error should name the candidates, got %v", err)
	}
	draft, err := arcaneResolveDraft(root, "turntable_elliptic")
	if err != nil {
		t.Fatalf("unambiguous fragment: %v", err)
	}
	if draft.Name != "arcane_italian_princess_turntable_elliptic_64" {
		t.Errorf("resolved to %q", draft.Name)
	}
	if empty, err := arcaneResolveDraft(root, ""); err != nil || empty.Name != "" {
		t.Errorf("an empty --draft should resolve to nothing, got %+v %v", empty, err)
	}
}

// An interpreter that exits 0 and prints nothing is BROKEN, not ready. This is
// not hypothetical: /opt/homebrew/bin/python3 on this machine is a `#!/bin/sh`
// shim that does exactly that, including for `-m py_compile` on a file with a
// syntax error. Rendering that as a pass is the same failure the old
// provisionArcane had, one level down.
func TestInterpreterValidationRejectsASilentStub(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-script fixtures are POSIX-only")
	}
	dir := t.TempDir()

	stub := filepath.Join(dir, "python3")
	writeScript(t, stub, "#!/bin/sh\nexit 0\n")
	if _, reason := arcaneInterpreterVersion(stub); reason == "" {
		t.Error("a silent zero-exit stub must be rejected")
	} else if !strings.Contains(reason, "BROKEN STUB") {
		t.Errorf("reason = %q, want it to name the stub", reason)
	}

	noisy := filepath.Join(dir, "python-noise")
	writeScript(t, noisy, "#!/bin/sh\necho 'command not found'\n")
	if _, reason := arcaneInterpreterVersion(noisy); reason == "" {
		t.Error("output that is not a version must be rejected")
	}

	failing := filepath.Join(dir, "python-fail")
	writeScript(t, failing, "#!/bin/sh\nexit 3\n")
	if _, reason := arcaneInterpreterVersion(failing); reason == "" {
		t.Error("a non-zero exit must be rejected")
	}

	real := filepath.Join(dir, "python-real")
	writeScript(t, real, "#!/bin/sh\necho 3.14.7\n")
	version, reason := arcaneInterpreterVersion(real)
	if reason != "" {
		t.Fatalf("a real interpreter was rejected: %s", reason)
	}
	if version != "3.14.7" {
		t.Errorf("version = %q", version)
	}
}

// Resolution must walk past a broken candidate to a working one, and must
// report the broken one when there is nothing better.
func TestResolvePythonPrefersAWorkingInterpreter(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("shell-script fixtures are POSIX-only")
	}
	root := t.TempDir()

	// Isolate the search: no PATH interpreters, no home-directory venvs. What
	// is left is exactly the candidates this test puts there.
	empty := filepath.Join(root, "empty-path")
	if err := os.MkdirAll(empty, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", empty)
	t.Setenv("HOME", filepath.Join(root, "nowhere"))

	broken := filepath.Join(root, "broken-python")
	writeScript(t, broken, "#!/bin/sh\nexit 0\n")
	t.Setenv("FLUX_PYTHON", broken)
	interp := arcaneResolvePython(config.Config{Root: root, Python: broken})
	if interp.OK {
		t.Fatalf("a stub must not be accepted just because FLUX_PYTHON names it (resolved %s)", interp.Path)
	}
	if !strings.Contains(interp.Reason, "BROKEN STUB") {
		t.Errorf("reason = %q", interp.Reason)
	}

	// A working venv interpreter is found even when FLUX_PYTHON is a stub.
	venv := filepath.Join(root, ".venv", "bin")
	if err := os.MkdirAll(venv, 0o755); err != nil {
		t.Fatal(err)
	}
	writeScript(t, filepath.Join(venv, "python"), "#!/bin/sh\necho 3.13.2\n")
	interp = arcaneResolvePython(config.Config{Root: root, Python: broken})
	if !interp.OK {
		t.Fatalf("a working venv interpreter should win: %s", interp.Reason)
	}
	if interp.Version != "3.13.2" {
		t.Errorf("version = %q", interp.Version)
	}
}

// The witness is what turns "exited 0 having printed nothing" into a reported
// indeterminate rather than a pass.
func TestWitnessIgnoresWhitespaceOnlyOutput(t *testing.T) {
	witness := &arcaneWitness{sink: io_Discard{}}
	if _, err := witness.Write([]byte("   \n\t \n")); err != nil {
		t.Fatal(err)
	}
	if witness.saw {
		t.Error("whitespace is not output")
	}
	if _, err := witness.Write([]byte("preflight: ok\n")); err != nil {
		t.Fatal(err)
	}
	if !witness.saw {
		t.Error("real output should be witnessed")
	}
}

type io_Discard struct{}

func (io_Discard) Write(p []byte) (int, error) { return len(p), nil }

// The surface route table is a claim about internal/server/server.go. Every
// route it names must point at a file that exists, or the table has gone stale.
func TestSurfaceRoutesPointAtRealFiles(t *testing.T) {
	root := repoRoot(t)
	rows := arcaneSurfaceRouteRows(config.Config{Root: root})
	if len(rows) != len(arcaneSurfaceRoutes) {
		t.Fatalf("got %d rows for %d routes", len(rows), len(arcaneSurfaceRoutes))
	}
	for _, row := range rows {
		if row.Status != "ok" {
			t.Errorf("%s -> %s is %s", row.Route, row.Path, row.Status)
		}
	}
}

// A missing pipeline script must surface as unavailable — never as a pass.
func TestSurfaceStageReportsAMissingScriptAsUnavailable(t *testing.T) {
	root := t.TempDir()
	stage := arcaneStageSurfaces(config.Config{Root: root}, arcaneInterpreter{OK: true, Path: "/bin/true"}, "rtx-pro-6000", true, true)
	if len(stage.Probes) != 1 {
		t.Fatalf("probes = %+v", stage.Probes)
	}
	if stage.Probes[0].Status != "unavailable" {
		t.Errorf("status = %q, want unavailable", stage.Probes[0].Status)
	}
	if stage.Probes[0].Blocking {
		t.Error("an absent script is not a blocking failure")
	}
}

// Provisioning on a host with no CUDA device must report absence, and must
// count it as blocking rather than printing a hopeful green line.
func TestSiliconStageReportsAbsenceHonestly(t *testing.T) {
	stage := arcaneStageSilicon(arcaneSilicon{ProbeErr: "nvidia-smi is not on PATH"})
	statuses := map[string]string{}
	blocking := 0
	for _, probe := range stage.Probes {
		statuses[probe.Key] = probe.Status
		if probe.Blocking {
			blocking++
		}
	}
	if statuses["nvidia-smi"] != "not detected" {
		t.Errorf("nvidia-smi status = %q", statuses["nvidia-smi"])
	}
	for _, key := range []string{"gpu", "compute", "vram", "driver"} {
		if statuses[key] != "unknown" {
			t.Errorf("%s = %q, want unknown when nothing was detected", key, statuses[key])
		}
	}
	if blocking != 1 {
		t.Errorf("blocking probes = %d, want 1", blocking)
	}
}

// With nothing detected, nothing may be asserted about the fabric either.
func TestInterconnectStageIsHonestWithoutADriver(t *testing.T) {
	profile := arcane.X4Profile(arcane.LayoutTP)
	stage := arcaneStageInterconnect(profile, arcaneInterconnect{Detected: "unknown", Detail: "nvidia-smi not present"})
	got := map[string]string{}
	for _, probe := range stage.Probes {
		got[probe.Key] = probe.Status
	}
	if got["detected"] != "unknown" || got["agreement"] != "unknown" {
		t.Errorf("undetected fabric must read unknown, got %v", got)
	}
	// A tp layout on an unverifiable fabric is unknown, not ok.
	if got["tensor parallel"] != "unknown" {
		t.Errorf("tensor parallel = %q, want unknown", got["tensor parallel"])
	}

	// Detected PCIe under a tp layout is a misconfiguration, and must say so.
	stage = arcaneStageInterconnect(profile, arcaneInterconnect{Probed: true, Detected: "pcie", Detail: "topo -m shows only PCIe paths"})
	for _, probe := range stage.Probes {
		if probe.Key == "tensor parallel" {
			if probe.Status != "warn" || !strings.Contains(probe.Detail, "MISCONFIGURATION") {
				t.Errorf("tp over PCIe must warn loudly, got %+v", probe)
			}
		}
		if probe.Key == "agreement" && probe.Status != "warn" {
			t.Errorf("declared nvlink against detected pcie must warn, got %+v", probe)
		}
	}
}

func TestTopologySummaryDistinguishesNVLinkFromPCIe(t *testing.T) {
	matrix := "\tGPU0\tGPU1\nGPU0\t X \tNV18\nGPU1\tNV18\t X \n"
	if got := arcaneTopologySummary(matrix); !strings.Contains(got, "NVLink") {
		t.Errorf("NV18 should read as NVLink, got %q", got)
	}
	matrix = "\tGPU0\tGPU1\nGPU0\t X \tSYS\nGPU1\tSYS\t X \n"
	got := arcaneTopologySummary(matrix)
	if !strings.Contains(got, "PCIe") || strings.Contains(got, "NVLink") {
		t.Errorf("SYS should read as PCIe only, got %q", got)
	}
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	// Tests run in cmd/flux; the repo root is two levels up.
	return filepath.Dir(filepath.Dir(wd))
}

func writeScript(t *testing.T, path, body string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(body), 0o755); err != nil {
		t.Fatalf("write %s: %v", path, err)
	}
}

// A margin under 1 GiB is a rounding error, not headroom. pipeline_paths
// classifies it as its own tier because the posture boots and then OOMs.
func TestBudgetCriticalTier(t *testing.T) {
	thin := arcaneBudget{Available: true, Fits: true, Headroom: 0.32}
	thin.PerGPU = append(thin.PerGPU, struct {
		GPU       int      `json:"gpu"`
		TotalGiB  float64  `json:"total_gib"`
		UsableGiB float64  `json:"usable_gib"`
		Allocated float64  `json:"allocated_gib"`
		FreeGiB   float64  `json:"free_gib"`
		Headroom  float64  `json:"headroom_gib"`
		Fits      bool     `json:"fits"`
		Tenants   []string `json:"tenants"`
	}{GPU: 0, TotalGiB: 96, UsableGiB: 94, Allocated: 93.68, Headroom: 0.32, Fits: true})
	if !thin.Critical() {
		t.Error("0.32 GiB of headroom must register as critical")
	}

	roomy := thin
	roomy.Headroom = 12
	roomy.PerGPU[0].Headroom = 12
	if roomy.Critical() {
		t.Error("12 GiB of headroom is a margin, not a critical tier")
	}

	over := thin
	over.Fits = false
	if over.Critical() {
		t.Error("an overcommitted budget is a failure, not a critical-but-fitting tier")
	}

	// The gap is the KV cache and activation arena, and it must be reported.
	gapped := arcaneBudget{Available: true, Allocated: 93.68, Weights: 88.09}
	if got := gapped.Gap(); got < 5.5 || got > 5.7 {
		t.Errorf("Gap() = %.2f, want ~5.59", got)
	}
}

// An unavailable authority must never be silently replaced by local arithmetic
// presented as measurement.
func TestBudgetProbeReportsItsOwnAbsence(t *testing.T) {
	budget := arcaneProbeBudget(config.Config{Root: t.TempDir()}, arcaneInterpreter{OK: false, Reason: "BROKEN STUB"}, "rtx-pro-6000")
	if budget.Available {
		t.Fatal("a budget cannot be available without a working interpreter")
	}
	if !strings.Contains(budget.Reason, "BROKEN STUB") {
		t.Errorf("reason = %q, want the interpreter's own reason", budget.Reason)
	}

	root := t.TempDir()
	budget = arcaneProbeBudget(config.Config{Root: root}, arcaneInterpreter{OK: true, Path: "/bin/echo"}, "rtx-pro-6000")
	if budget.Available || !strings.Contains(budget.Reason, "pipeline_paths.py") {
		t.Errorf("a missing pipeline_paths.py must be named: %+v", budget)
	}
}

// The live budget must agree with what the CLI prints, so the two cannot drift
// apart unnoticed.
func TestLiveBudgetMatchesTheConfigReservation(t *testing.T) {
	root := repoRoot(t)
	if _, err := os.Stat(filepath.Join(root, "pipeline_paths.py")); err != nil {
		t.Skip("pipeline_paths.py not present")
	}
	cfg := config.Config{Root: root}
	interp := arcaneResolvePython(cfg)
	if !interp.OK {
		t.Skipf("no working interpreter: %s", interp.Reason)
	}
	budget := arcaneProbeBudget(cfg, interp, "rtx-pro-6000")
	if !budget.Available {
		t.Skipf("vram_budget() unavailable: %s", budget.Reason)
	}

	registry := arcane.Load(root)
	profile, err := registry.Lookup("rtx-pro-6000")
	if err != nil {
		t.Fatalf("Lookup: %v", err)
	}
	if diff := profile.Committed() - budget.Allocated; diff > 0.15 || diff < -0.15 {
		t.Errorf("CLI totals %.2f GiB but pipeline_paths reserves %.2f GiB — the two budgets have drifted apart",
			profile.Committed(), budget.Allocated)
	}
	if budget.Allocated <= budget.Weights {
		t.Errorf("reserved %.2f should exceed weights %.2f", budget.Allocated, budget.Weights)
	}
}
