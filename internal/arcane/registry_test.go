package arcane

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// The compiled roster is what every command falls back to when the continuum
// file is missing or mid-rewrite, so its invariants are asserted directly
// rather than trusted.
func TestBuiltinRosterHoldsItsInvariants(t *testing.T) {
	for _, profile := range Builtin() {
		t.Run(profile.Name, func(t *testing.T) {
			generators := profile.Seats(RoleGenerator)
			if len(generators) == 0 {
				t.Fatal("no generator seat")
			}
			for _, seat := range generators {
				if seat.Precision != "BF16" {
					t.Errorf("generator precision = %q, want BF16 on every profile", seat.Precision)
				}
				if seat.Model != "black-forest-labs/FLUX.1-dev" {
					t.Errorf("generator model = %q", seat.Model)
				}
			}

			for _, role := range []string{RolePalette, RoleGates} {
				seats := profile.Seats(role)
				if len(seats) == 0 {
					t.Fatalf("%s seat missing", role)
				}
				for _, seat := range seats {
					if !seat.Mandatory || seat.Toggleable || !seat.Enabled {
						t.Errorf("%s must be mandatory, enabled and non-toggleable, got %+v", role, seat)
					}
				}
			}

			for _, seat := range profile.Tenants {
				if seat.Toggleable && seat.Role != RoleKontext {
					t.Errorf("%s is toggleable; kontext is the only toggle", seat.Role)
				}
				if !seat.Dense {
					t.Errorf("%s is not dense; the sm_120 profiles depend on an all-dense roster", seat.Role)
				}
				if IsBanned(seat.Model) {
					t.Errorf("%s serves the retired model %q", seat.Role, seat.Model)
				}
			}

			if fits, over := profile.Fits(); !fits {
				t.Errorf("compiled roster does not fit its own hardware: %s", strings.Join(over, "; "))
			}
		})
	}
}

func TestBuiltinRosterArithmetic(t *testing.T) {
	registry := Registry{Profiles: Builtin()}
	for name, want := range map[string]float64{
		"rtx-pro-6000": 88.6, // 35.0 + 24.6 + 19.0 + 7.0 + 3.0
		"b200":         136.0,
		"b300":         196.0,
	} {
		profile, err := registry.Lookup(name)
		if err != nil {
			t.Fatalf("Lookup(%q): %v", name, err)
		}
		if got := profile.Committed(); got != want {
			t.Errorf("%s committed = %.1f, want %.1f", name, got, want)
		}
		if profile.CardCount() != 1 {
			t.Errorf("%s should be a single card, got %d", name, profile.CardCount())
		}
	}
}

// Four 96 GiB cards are four cards. Capacity is decided per GPU; an aggregate
// that looks roomy while one card overflows does not fit.
func TestX4CapacityIsDecidedPerCard(t *testing.T) {
	for _, layout := range X4LayoutOptions {
		profile := X4Profile(layout)
		t.Run(layout, func(t *testing.T) {
			if profile.CardCount() != 4 {
				t.Fatalf("card count = %d, want 4", profile.CardCount())
			}
			if profile.TotalVRAMGiB() != 384.0 {
				t.Errorf("aggregate = %.1f, want 384.0", profile.TotalVRAMGiB())
			}
			cards := profile.Cards()
			if len(cards) != 4 {
				t.Fatalf("Cards() returned %d", len(cards))
			}
			for _, card := range cards {
				if card.Capacity != 96.0 {
					t.Errorf("gpu %d capacity = %.1f, want 96.0 per card", card.Index, card.Capacity)
				}
				if card.Committed > card.Usable {
					t.Errorf("gpu %d overflows: %.1f placed, %.1f usable", card.Index, card.Committed, card.Usable)
				}
			}
			for _, seat := range profile.Tenants {
				if seat.VRAMGiB > profile.VRAMGiB {
					t.Errorf("%s needs %.1f GiB, more than one %.1f GiB card holds", seat.Role, seat.VRAMGiB, profile.VRAMGiB)
				}
				if seat.GPU < 0 || seat.GPU > 3 {
					t.Errorf("%s placed on gpu %d", seat.Role, seat.GPU)
				}
			}
		})
	}
}

func TestX4LayoutsDifferAsDocumented(t *testing.T) {
	balanced := X4Profile(LayoutBalanced)
	dense := X4Profile(LayoutDense)
	tp := X4Profile(LayoutTP)

	if got := len(balanced.Seats(RoleGenerator)); got != 3 {
		t.Errorf("balanced generators = %d, want 3", got)
	}
	if got := len(dense.Seats(RoleGenerator)); got != 4 {
		t.Errorf("dense generators = %d, want 4", got)
	}
	if got := len(tp.Seats(RoleGenerator)); got != 2 {
		t.Errorf("tp generators = %d, want 2 (the judge stack costs two cards)", got)
	}

	// The dense layout's judge card is the tight one, by design.
	if got := dense.Cards()[3].Committed; got != 89.0 {
		t.Errorf("dense gpu 3 = %.1f, want 89.0", got)
	}
	// The balanced judge card upgrades the critics off the generator cards.
	structure, _ := balanced.Tenant(RoleStructure)
	if structure.Precision != "FP8" {
		t.Errorf("balanced structure precision = %q, want FP8", structure.Precision)
	}
	// Only the tensor-parallel layout shards a tenant across cards.
	for _, seat := range balanced.Tenants {
		if seat.TensorParallel > 1 {
			t.Errorf("balanced should run tensor_parallel = 1, got %d on %s", seat.TensorParallel, seat.Role)
		}
	}
	sharded := 0
	for _, seat := range tp.Tenants {
		if seat.TensorParallel > 1 {
			sharded++
		}
	}
	if sharded != 6 {
		t.Errorf("tp layout shards %d seats, want 6 (three judges × two ranks)", sharded)
	}
}

// A missing or malformed continuum file must degrade to the compiled roster and
// say so, never fail the command and never serve a half-read config.
func TestLoadFallsBackWhenConfigIsUnusable(t *testing.T) {
	t.Run("missing", func(t *testing.T) {
		registry := Load(t.TempDir())
		assertFellBack(t, registry, "unreadable")
	})

	t.Run("malformed", func(t *testing.T) {
		root := t.TempDir()
		write(t, root, "[profiles.rtx-pro-6000\ngpu = \"broken\"\n")
		registry := Load(root)
		assertFellBack(t, registry, "did not parse")
	})

	t.Run("truncated mid-write", func(t *testing.T) {
		root := t.TempDir()
		write(t, root, "[profiles.rtx-pro-6000]\ngpu = \"NVIDIA RTX PRO 6000\"\nnotes = \"cut off here")
		registry := Load(root)
		assertFellBack(t, registry, "did not parse")
	})

	t.Run("no profiles", func(t *testing.T) {
		root := t.TempDir()
		write(t, root, "[continuum]\nname = \"x\"\n")
		registry := Load(root)
		assertFellBack(t, registry, "no [profiles.*]")
	})
}

func assertFellBack(t *testing.T, registry Registry, wantNote string) {
	t.Helper()
	if !registry.Degraded {
		t.Error("registry should report itself degraded")
	}
	if registry.Source != "compiled roster" {
		t.Errorf("source = %q, want the compiled roster", registry.Source)
	}
	if len(registry.Profiles) != len(Builtin()) {
		t.Fatalf("fallback served %d profiles, want %d", len(registry.Profiles), len(Builtin()))
	}
	if len(registry.Notes) == 0 {
		t.Fatal("a degraded read must explain itself")
	}
	joined := strings.Join(registry.Notes, " | ")
	if !strings.Contains(joined, wantNote) {
		t.Errorf("notes = %q, want a mention of %q", joined, wantNote)
	}
	// The fallback must still be a usable roster, not an empty shell.
	profile := registry.DefaultProfile()
	if profile.Name != "rtx-pro-6000" || len(profile.Tenants) == 0 {
		t.Errorf("fallback default profile = %+v", profile)
	}
}

// A retired model in config is refused under every precedence policy: the list
// exists so a regression is caught rather than served.
func TestRetiredModelsAreRefusedEvenWhenConfigWins(t *testing.T) {
	root := t.TempDir()
	write(t, root, `
[continuum]
default_profile = "rtx-pro-6000"

[profiles.rtx-pro-6000]
gpu = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
sm = "sm_120"
vram_gib = 96.0

[profiles.rtx-pro-6000.tenants.flux]
kind = "uds"
model = "black-forest-labs/FLUX.1-schnell"
precision = "bf16"
enabled = true
`)
	for _, prefer := range []Precedence{PreferRoster, PreferConfig} {
		registry := LoadWith(root, prefer)
		profile, err := registry.Lookup("rtx-pro-6000")
		if err != nil {
			t.Fatalf("Lookup: %v", err)
		}
		seat, _ := profile.Tenant(RoleGenerator)
		if seat.Model != "black-forest-labs/FLUX.1-dev" {
			t.Errorf("prefer=%s: generator = %q, want the roster model", prefer, seat.Model)
		}
		if !strings.Contains(strings.Join(registry.Drift, " "), "REFUSED") {
			t.Errorf("prefer=%s: the refusal must be reported, drift = %v", prefer, registry.Drift)
		}
	}
}

func TestPrecedenceDecidesWhoWinsOnDisagreement(t *testing.T) {
	root := t.TempDir()
	write(t, root, `
[profiles.rtx-pro-6000]
gpu = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
sm = "sm_120"
vram_gib = 96.0

[profiles.rtx-pro-6000.tenants.pixtral]
kind = "vllm"
model = "mistralai/Pixtral-12B-2409"
precision = "bf16"
port = 8002
vram_expected_gib = 24.0
enabled = true
`)
	roster := LoadWith(root, PreferRoster)
	profile, _ := roster.Lookup("rtx-pro-6000")
	seat, _ := profile.Tenant(RolePalette)
	if seat.Model != "RedHatAI/pixtral-12b-quantized.w4a16" {
		t.Errorf("prefer=roster kept %q", seat.Model)
	}
	if len(roster.Drift) == 0 {
		t.Error("the disagreement must still be reported under prefer=roster")
	}

	config := LoadWith(root, PreferConfig)
	profile, _ = config.Lookup("rtx-pro-6000")
	seat, _ = profile.Tenant(RolePalette)
	if seat.Model != "mistralai/Pixtral-12B-2409" || seat.VRAMGiB != 24.0 {
		t.Errorf("prefer=config served %q at %.1f GiB", seat.Model, seat.VRAMGiB)
	}
}

// Config supplies the operational fields even when the roster wins on identity.
func TestConfigSuppliesOperationalFields(t *testing.T) {
	root := t.TempDir()
	write(t, root, `
[profiles.rtx-pro-6000]
gpu = "Relabelled Card"
sm = "sm_120"
vram_gib = 96.0
interconnect = "nvlink"
tensor_parallel_viable = true

[profiles.rtx-pro-6000.tenants.witness]
kind = "vllm"
model = "unsloth/Qwen3.8-27B-NVFP4"
precision = "nvfp4"
port = 9101
gpu = 2
enabled = false
`)
	registry := LoadWith(root, PreferRoster)
	profile, _ := registry.Lookup("rtx-pro-6000")
	if profile.GPU != "Relabelled Card" || profile.Interconnect != "nvlink" || !profile.TPViable {
		t.Errorf("profile fields not overlaid: %+v", profile)
	}
	seat, _ := profile.Tenant(RoleStructure)
	if seat.Port != 9101 || seat.GPU != 2 || seat.Enabled {
		t.Errorf("tenant operational fields not overlaid: %+v", seat)
	}
}

func TestLookupResolvesAliases(t *testing.T) {
	registry := Registry{Profiles: Builtin()}
	for alias, want := range map[string]string{
		"":                "rtx-pro-6000",
		"blackwell-96":    "rtx-pro-6000",
		"RTX-PRO-6000":    "rtx-pro-6000",
		"b300-288":        "b300",
		"x4":              "rtx-pro-6000-x4",
		"rtx-pro-6000-x4": "rtx-pro-6000-x4",
	} {
		profile, err := registry.Lookup(alias)
		if err != nil {
			t.Fatalf("Lookup(%q): %v", alias, err)
		}
		if profile.Name != want {
			t.Errorf("Lookup(%q) = %q, want %q", alias, profile.Name, want)
		}
	}
	if _, err := registry.Lookup("h100"); err == nil {
		t.Error("an unknown profile must be an error, not a silent default")
	}
}

func TestWithLayoutOnlyAppliesToTheClusterProfile(t *testing.T) {
	registry := Registry{Profiles: Builtin()}
	if _, err := registry.WithLayout("rtx-pro-6000", "tp"); err == nil {
		t.Error("--layout on a single-card profile should be rejected")
	}
	profile, err := registry.WithLayout("x4", "dense")
	if err != nil {
		t.Fatalf("WithLayout: %v", err)
	}
	if profile.Layout != LayoutDense || len(profile.Seats(RoleGenerator)) != 4 {
		t.Errorf("dense layout not applied: %+v", profile.Layout)
	}
	if _, err := registry.WithLayout("x4", "nonsense"); err == nil {
		t.Error("an unknown layout should be rejected")
	}
}

func TestVersionAtLeast(t *testing.T) {
	cases := []struct {
		have, floor      string
		want, comparable bool
	}{
		{"0.13.0", "0.13.0", true, true},
		{"0.12.9", "0.13.0", false, true},
		{"0.14.0", "0.13.0", true, true},
		{"0.13.1", "0.13.0", true, true},
		{"0.13.0rc1", "0.13.0", true, true}, // suffix cut; not treated as newer
		{"0.14.0+cu130", "0.13.0", true, true},
		{"v0.13.0", "0.13.0", true, true},
		{"", "0.13.0", false, false},
		{"unknown", "0.13.0", false, false},
	}
	for _, c := range cases {
		got, comparable := VersionAtLeast(c.have, c.floor)
		if got != c.want || comparable != c.comparable {
			t.Errorf("VersionAtLeast(%q, %q) = (%v, %v), want (%v, %v)", c.have, c.floor, got, comparable, c.want, c.comparable)
		}
	}
}

func TestSMFromComputeCapability(t *testing.T) {
	for cap, want := range map[string]string{
		"12.0": "sm_120",
		"10.0": "sm_100",
		"9.0":  "sm_90",
		"8.6":  "sm_86",
		"":     "",
		"n/a":  "",
	} {
		if got := SMFromComputeCapability(cap); got != want {
			t.Errorf("SMFromComputeCapability(%q) = %q, want %q", cap, got, want)
		}
	}
}

func TestKernelNotesNameTheRealConstraints(t *testing.T) {
	sm120 := strings.Join(KernelNotes("sm_120"), " ")
	for _, want := range []string{"mma.sync", "99 KB", "MoE", "0.13.0", "sm120 wheel"} {
		if !strings.Contains(sm120, want) {
			t.Errorf("sm_120 notes omit %q", want)
		}
	}
	sm100 := strings.Join(KernelNotes("sm_100"), " ")
	if !strings.Contains(sm100, "tcgen05.mma") || !strings.Contains(sm100, "228 KB") {
		t.Errorf("sm_100 notes = %q", sm100)
	}
}

func write(t *testing.T, root, body string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(root, ContinuumFile), []byte(body), 0o644); err != nil {
		t.Fatalf("write config: %v", err)
	}
}

// The correction that matters most: a tenant's WEIGHTS are the checkpoint, its
// RESERVATION is gpu_memory_utilization × card. Capacity must be decided on the
// reservation under BOTH precedence policies — preferring the roster's weights
// here reports comfortable headroom on a card that is one KV cache growth from
// an OOM.
func TestConfigReservationAlwaysBeatsRosterWeights(t *testing.T) {
	root := t.TempDir()
	write(t, root, `
[profiles.rtx-pro-6000]
gpu = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
sm = "sm_120"
vram_gib = 96.0
reserve_gib = 2.0

[profiles.rtx-pro-6000.tenants.witness]
kind = "vllm"
model = "unsloth/Qwen3.8-27B-NVFP4"
precision = "nvfp4"
port = 8001
gpu_memory_utilization = 0.28
weights_gib = 24.6
vram_expected_gib = 26.88
enabled = true
`)
	for _, prefer := range []Precedence{PreferRoster, PreferConfig} {
		registry := LoadWith(root, prefer)
		profile, err := registry.Lookup("rtx-pro-6000")
		if err != nil {
			t.Fatalf("Lookup: %v", err)
		}
		seat, _ := profile.Tenant(RoleStructure)

		if seat.VRAMGiB != 26.9 {
			t.Errorf("prefer=%s: reserved = %.2f, want the config's 26.9 reservation, not the roster's weights", prefer, seat.VRAMGiB)
		}
		if seat.WeightsGiB != 24.6 {
			t.Errorf("prefer=%s: weights = %.2f, want 24.6", prefer, seat.WeightsGiB)
		}
		if seat.VRAMSource != VRAMFromConfig {
			t.Errorf("prefer=%s: source = %q, want %q", prefer, seat.VRAMSource, VRAMFromConfig)
		}
		// Only the witness seat is described by this config, so the profile as a
		// whole is NOT reservation-backed and must not claim to be.
		if profile.ReservationBacked() {
			t.Errorf("prefer=%s: seats still on roster weights must keep the profile from claiming to be reservation-backed", prefer)
		}
		if len(registry.VRAMDrift) == 0 {
			t.Errorf("prefer=%s: the weights/reservation gap must be reported", prefer)
		}
		if profile.Committed() <= profile.WeightsCommitted() {
			t.Errorf("prefer=%s: committed %.2f should exceed weights %.2f by the KV cache and activation arena",
				prefer, profile.Committed(), profile.WeightsCommitted())
		}
	}
}

// When the roster and the config name different checkpoints, neither figure
// describes what will load. The seat must not pair one model's weights with
// another's reservation.
func TestUnresolvedCheckpointDoesNotMixFigures(t *testing.T) {
	root := t.TempDir()
	write(t, root, `
[profiles.rtx-pro-6000]
gpu = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
sm = "sm_120"
vram_gib = 96.0

[profiles.rtx-pro-6000.tenants.kontext]
kind = "uds"
model = "city96/FLUX.1-Kontext-dev-gguf"
precision = "q4_k_s"
weights_gib = 6.8
vram_expected_gib = 9.0
enabled = false
toggleable = true
`)
	registry := LoadWith(root, PreferRoster)
	profile, _ := registry.Lookup("rtx-pro-6000")
	seat, _ := profile.Tenant(RoleKontext)

	if seat.Model != "black-forest-labs/FLUX.1-Kontext-dev" {
		t.Fatalf("roster should keep identity, got %q", seat.Model)
	}
	if seat.WeightsGiB != seat.VRAMGiB {
		t.Errorf("an unresolved seat must not pair %q weights %.1f with a %.1f reservation for a different checkpoint",
			seat.Model, seat.WeightsGiB, seat.VRAMGiB)
	}
	if seat.WeightsGiB == 6.8 || seat.VRAMGiB == 9.0 {
		t.Errorf("the GGUF checkpoint's figures leaked onto the BF16 roster seat: weights=%.1f reserved=%.1f", seat.WeightsGiB, seat.VRAMGiB)
	}
	if seat.VRAMSource != VRAMFromRoster {
		t.Errorf("source = %q, want %q", seat.VRAMSource, VRAMFromRoster)
	}
	found := false
	for _, drift := range registry.VRAMDrift {
		if strings.Contains(drift, "UNRESOLVED") {
			found = true
		}
	}
	if !found {
		t.Errorf("an unresolved budget must say so, drift = %v", registry.VRAMDrift)
	}
}

// The compiled fallback carries weights only. It must admit that rather than
// presenting a weights total as a reservation.
func TestCompiledFallbackIsNotReservationBacked(t *testing.T) {
	registry := Load(t.TempDir())
	profile := registry.DefaultProfile()
	if profile.ReservationBacked() {
		t.Error("the compiled roster carries weights, not reservations; it must not claim otherwise")
	}
	if profile.Committed() != profile.WeightsCommitted() {
		t.Errorf("with no config, committed %.1f and weights %.1f are the same figure", profile.Committed(), profile.WeightsCommitted())
	}
}
