package main

// The Arcane surface: the model roster, the three hardware profiles, and real
// provisioning.
//
// The rule this file is written against: every status line printed here must
// correspond to something that was actually probed. The version of
// provisionArcane this replaces printed "NVIDIA GPU ready" and "BF16 / CUDA
// 13.0 active" unconditionally — on a Mac with no CUDA device at all. Anything
// that cannot be determined prints as `unknown` or `not detected`. Nothing
// prints green because it was expected to be green.

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"time"

	"local/flux/internal/arcane"
	"local/flux/internal/config"
	"local/flux/internal/daemon"
	"local/flux/internal/ui"
)

// arcaneDriftPreview caps how many config/roster disagreements print inline.
// Every one is always in --json; the cap keeps the headline command readable
// while the continuum file is being rewritten around us.
const arcaneDriftPreview = 4

const arcaneSignaturePrompt = "A solitary Arcane vigilante in the rain-soaked alleys of Zaun, mechanical arm glowing with chemtech emerald light, sharp angular jawline, visible gouache brushwork"

// profileColor keys the palette off the protocol spec's anchors: Zaun chemtech
// emerald for the workstation card we actually run on, Piltover hextech cyan
// for the B200, gilded brass for the B300.
func profileColor(name string) ui.Color {
	switch name {
	case "rtx-pro-6000":
		return ui.Mint
	case "rtx-pro-6000-x4":
		return ui.Lilac
	case "b200":
		return ui.Teal
	case "b300":
		return ui.Gold
	}
	return ui.Violet
}

// ---------------------------------------------------------------------------
// Dispatch
// ---------------------------------------------------------------------------

func arcaneDispatch(cfg config.Config, args []string) error {
	if len(args) == 0 {
		return render(cfg, []string{"--preset", "arcane-hero", arcaneSignaturePrompt})
	}

	switch strings.ToLower(args[0]) {
	case "-h", "--help", "help":
		arcaneHelp()
		return nil
	case "models", "model", "roster":
		return arcaneModels(cfg, args[1:])
	case "profiles", "profile", "hardware":
		return arcaneProfiles(cfg, args[1:])
	case "provision", "setup", "install":
		return arcaneProvision(cfg, args[1:])
	case "preflight":
		return arcanePreflight(cfg, args[1:])
	case "surfaces", "surface", "pages", "studio-pages":
		return arcaneSurfaces(cfg, args[1:])
	case arcaneModeCharacter, "turn", "turntable":
		return arcaneMode(cfg, arcaneModeCharacter, args[1:])
	case arcaneModeLatent, "latents":
		return arcaneMode(cfg, arcaneModeLatent, args[1:])
	case arcaneModeScenes, "scene", "world":
		return arcaneMode(cfg, arcaneModeScenes, args[1:])
	case "drafts", "draft":
		return arcaneDrafts(cfg, args[1:])
	case "run":
		return arcanePipelineDelegate(cfg, "run", args[1:])
	case "status":
		return arcanePipelineDelegate(cfg, "status", args[1:])
	case "download", "pull":
		// Retained: `flux arcane download` predates `flux arcane models
		// download` and is in muscle memory.
		return download(cfg, args[1:])
	case "serve", "studio":
		return serve(cfg, []string{"studio", "--addr", "0.0.0.0:7860"})
	case "plan":
		prompt := arcaneSignaturePrompt
		if len(args) > 1 {
			prompt = strings.Join(args[1:], " ")
		}
		return render(cfg, []string{"--preset", "arcane-hero", "--dry-run", "--echo", prompt})
	case "zaun":
		return render(cfg, []string{"--preset", "arcane-zaun", strings.Join(args[1:], " ")})
	case "piltover":
		return render(cfg, []string{"--preset", "arcane-piltover", strings.Join(args[1:], " ")})
	default:
		return render(cfg, []string{"--preset", "arcane-hero", strings.Join(args, " ")})
	}
}

func arcaneHelp() {
	ui.Header("arcane", "Fortiche Arcane world forge · roster, provisioning & character studio")
	ui.Suite("roster", ui.Mint, []ui.PairRow{
		{"arcane models", "every model on every profile (same as `models list`)"},
		{"arcane models --profile b300", "narrow to one hardware profile"},
		{"arcane models --profile x4", "the four-card cluster, grouped per GPU"},
		{"arcane models --layout tp", "placement: balanced, dense, tp (x4 only)"},
		{"arcane models --json", "machine-readable roster"},
		{"arcane models download", "fetch the FLUX.1-dev BF16 Diffusers snapshot"},
		{"arcane models load", "warm the generator into VRAM, report tenant state"},
		{"arcane profiles", "all four hardware profiles side by side"},
	})
	ui.Suite("provisioning", ui.Teal, []ui.PairRow{
		{"arcane provision", "probe silicon, runtime, weights, tenants, surfaces"},
		{"arcane provision --dry-run", "probe and report; change nothing"},
		{"arcane provision --json", "structured provisioning report"},
		{"arcane preflight", "delegate to arcane_pipeline.py preflight"},
		{"arcane surfaces", "provision the studio pages via provision_surfaces.py"},
		{"arcane surfaces --check", "verify the studio pages without writing"},
		{"arcane run / status", "delegate to arcane_pipeline.py"},
	})
	ui.Suite("modes", ui.Lilac, []ui.PairRow{
		{"arcane character --draft <d>", "identity coherence across a rotation orbit"},
		{"arcane latent --draft <d>", "novelty and exploration across the latent shell"},
		{"arcane scenes --draft <d>", "composition and world — Piltover against Zaun"},
		{"arcane drafts", "orbit geometry and which mode each draft is valid for"},
		{"arcane drafts --mode <m>", "only the drafts valid for one mode"},
	})
	ui.Suite("forge", ui.Gold, []ui.PairRow{
		{"arcane", "render the signature Arcane Fortiche visual"},
		{"arcane <prompt>", "render a Fortiche Arcane hero character"},
		{"arcane zaun <prompt>", "Zaun undercity chemtech atmosphere"},
		{"arcane piltover <prompt>", "Piltover gilded architectural grandeur"},
		{"arcane turn <prompt>", "alias for `arcane character`"},
		{"arcane plan <prompt>", "show the render plan without running it"},
		{"arcane serve", "launch the Arcane Production Studio on :7860"},
	})
	fmt.Println()
	fmt.Println(ui.Soft("  --profile rtx-pro-6000|rtx-pro-6000-x4|b200|b300 and --json apply to models, profiles, preflight and provision."))
	fmt.Println(ui.Soft("  --layout balanced|dense|tp applies to rtx-pro-6000-x4. Capacity is always decided per GPU, never in aggregate."))
	fmt.Println()
	fmt.Println(ui.Soft("  character, latent and scenes have OPPOSITE objectives. character INVERTS the novelty gate and depends on the"))
	fmt.Println(ui.Soft("  cross-frame residual cache surviving the orbit, so a draft carrying view_prompts — which flushes that cache once"))
	fmt.Println(ui.Soft("  per view — is refused there and welcome in scenes. `flux arcane drafts` shows which draft suits which mode."))
	fmt.Println(ui.Soft("  `flux studio arcane provision` and `flux provision arcane` reach the same provisioning path."))
	fmt.Println()
}

// ---------------------------------------------------------------------------
// models
// ---------------------------------------------------------------------------

func arcaneModels(cfg config.Config, args []string) error {
	sub := "list"
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		sub = strings.ToLower(args[0])
		args = args[1:]
	}

	switch sub {
	case "download", "pull", "fetch":
		return download(cfg, args)
	case "load", "warm":
		return arcaneModelsLoad(cfg, args)
	case "list", "ls", "show", "roster":
		// fall through
	default:
		return fmt.Errorf("unknown `arcane models` command %q; use list, download, or load", sub)
	}

	fs := flag.NewFlagSet("arcane models list", flag.ExitOnError)
	profileName := fs.String("profile", "", "hardware `profile`: rtx-pro-6000, rtx-pro-6000-x4, b200, b300 (default: all)")
	layout := fs.String("layout", "", "placement `layout` for rtx-pro-6000-x4: balanced, dense, tp")
	prefer := fs.String("prefer", "roster", "model identity on a disagreement: `roster` or config (VRAM is always the config's reservation)")
	noPython := fs.Bool("no-python", false, "skip pipeline_paths.vram_budget() and use local arithmetic")
	asJSON := fs.Bool("json", false, "emit the roster as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	precedence, err := arcane.ParsePrecedence(*prefer)
	if err != nil {
		return err
	}
	registry := arcane.LoadWith(cfg.Root, precedence)
	profiles := registry.Profiles
	switch {
	case *profileName != "":
		profile, err := registry.WithLayout(*profileName, *layout)
		if err != nil {
			return err
		}
		profiles = []arcane.Profile{profile}
	case *layout != "":
		return fmt.Errorf("--layout needs --profile rtx-pro-6000-x4")
	}

	budgets := arcaneBudgets(cfg, profiles, *noPython)
	for i := range profiles {
		arcaneApplyBudget(&profiles[i], budgets[profiles[i].Name])
	}

	if *asJSON {
		return arcaneEmitJSON(map[string]any{
			"prefer":               string(registry.Prefer),
			"vram_precedence":      "config reservation (gpu_memory_utilization x card); roster weights are never used as a budget",
			"vram_drift":           registry.VRAMDrift,
			"budgets":              budgets,
			"source":               registry.Source,
			"source_path":          registry.SourcePath,
			"degraded":             registry.Degraded,
			"notes":                registry.Notes,
			"drift":                registry.Drift,
			"config_only_profiles": registry.Extra,
			"default_profile":      registry.DefaultProfile().Name,
			"banned_models":        arcane.BannedModels,
			"profiles":             arcaneProfilesJSON(profiles),
		})
	}

	subtitle := fmt.Sprintf("ratified roster · %d profile", len(profiles))
	if len(profiles) != 1 {
		subtitle += "s"
	}
	ui.Header("arcane models", subtitle)
	arcaneSourceBlock(registry, profiles)

	for _, profile := range profiles {
		arcaneProfileRoster(profile, budgets[profile.Name], registry.DefaultProfile().Name == profile.Name, len(profiles) == 1)
	}

	ui.Suite("invariants", ui.Violet, []ui.PairRow{
		{"generator", "BF16 always, every profile — docs/BF16_NATIVE_PRECISION_SPEC.md"},
		{"palette + gates", "Pixtral and DINOv2 are mandatory seats, never toggleable"},
		{"kontext", "the only toggle in the roster"},
		{"density", "every seat is a dense model — the sm_120 profile depends on it"},
	})
	fmt.Println()
	return nil
}

// arcaneProfileRoster prints one profile's seats, its capacity, and the
// arithmetic behind the toggles. A multi-card profile is grouped BY GPU: four
// 96 GiB cards are four cards, not a 384 GiB pool, and a flat list would
// misrepresent that.
func arcaneProfileRoster(profile arcane.Profile, budget arcaneBudget, isDefault, detailed bool) {
	color := profileColor(profile.Name)

	detail := ""
	if isDefault {
		detail = "default · "
	}
	if profile.Multi() {
		// The config sometimes labels the card with the cluster's own suffix
		// ("... x4"); the count is already spelled out here.
		board := strings.TrimSpace(strings.TrimSuffix(strings.TrimSpace(profile.GPU), fmt.Sprintf("x%d", profile.CardCount())))
		detail += fmt.Sprintf("%d × %s · %.0f GiB", profile.CardCount(), board, profile.VRAMGiB)
	} else {
		detail += profile.GPU + " · " + fmt.Sprintf("%.0f GiB", profile.VRAMGiB)
	}
	if profile.MemoryKind != "" {
		detail += " " + profile.MemoryKind
	}
	if profile.Multi() {
		detail += " each"
	}
	if profile.Bandwidth != "" {
		detail += " · " + profile.Bandwidth
	}
	detail += " · " + profile.SM
	if profile.Interconnect != "" {
		detail += " · " + profile.Interconnect
	}
	if profile.Layout != "" {
		detail += " · layout " + profile.Layout
	}
	ui.Section(profile.Name, detail, color)

	cards := profile.Cards()
	if !profile.Multi() {
		arcaneSeatTable(profile, cards[0].Tenants)
		fmt.Println()
		arcaneBudgetBlock(profile, budget)
		arcaneToggleNotes(profile, cards[0], budget)
		arcaneDetailTree(profile, color, detailed)
		return
	}

	for _, card := range cards {
		label := card.Label
		if label == "" {
			label = "unassigned"
		}
		fmt.Println()
		fmt.Println("  " + ui.Strong(ui.Accent(fmt.Sprintf("gpu %d", card.Index))) + "  " + ui.Soft(label))
		arcaneSeatTable(profile, card.Tenants)
	}

	fmt.Println()
	arcaneBudgetBlock(profile, budget)

	fits, over := profile.Fits()
	if budget.Available {
		fits = budget.Fits
	}
	if fits {
		ui.Field("placement", "ok", fmt.Sprintf("every one of the %d cards holds what is placed on it", profile.CardCount()))
	} else if len(over) > 0 {
		ui.Field("placement", "fail", strings.Join(over, "; "))
	} else {
		ui.Field("placement", "fail", valueOr(budget.Reason, "a card overflows"))
	}
	aggregate := profile.Committed()
	if budget.Available && budget.Allocated > 0 {
		aggregate = budget.Allocated
	}
	ui.Field("aggregate", "ok", fmt.Sprintf("%.1f GiB across %d cards of %.1f GiB", aggregate, profile.CardCount(), profile.TotalVRAMGiB()))
	ui.Note(fmt.Sprintf("that aggregate is a SUM, not a pool — no tenant may exceed one %.0f GiB card, and `fits` is decided per GPU", profile.VRAMGiB))
	if profile.TPViable {
		ui.Note("tensor parallelism is viable on this fabric, but nothing in the roster needs it for capacity: FLUX BF16 35.0, Qwen3.8-27B BF16 54.0, Gemma-4-31B BF16 62.0 and Pixtral BF16 24.0 each fit one card. TP buys latency and headroom, not feasibility")
	} else {
		ui.Note("tensor parallelism is impractical on this fabric — every tenant runs with tensor_parallel = 1 and work is fanned out as disjoint shards instead")
	}
	if len(profile.LayoutOptions) > 1 {
		ui.Note("other layouts: " + strings.Join(profile.LayoutOptions, ", ") + " — select with --layout")
	}
	if profile.Notes != "" {
		ui.Note(profile.Notes)
	}
	arcaneDetailTree(profile, color, detailed)
}

// arcaneSeatTable shows WEIGHTS and RESERVED as separate columns whenever they
// differ, which is the whole point: one is the checkpoint, the other is what
// vLLM takes off the card. Collapsing them to a single "vram" column is how the
// two got confused in the first place.
func arcaneSeatTable(profile arcane.Profile, seats []arcane.Tenant) {
	split := false
	for _, seat := range seats {
		if seat.WeightsGiB > 0 && arcaneRound1(seat.WeightsGiB) != arcaneRound1(seat.VRAMGiB) {
			split = true
			break
		}
	}

	rows := make([][]string, 0, len(seats))
	for _, seat := range seats {
		row := []string{
			arcaneRoleCell(seat),
			arcaneModelCell(seat),
			ui.Soft(seat.Precision),
		}
		if split {
			row = append(row, ui.Soft(fmt.Sprintf("%.1f", seat.WeightsGiB)))
		}
		row = append(row,
			arcaneVRAMCell(seat),
			arcaneSeatCell(seat),
			ui.Soft(arcaneEndpointCell(seat)),
		)
		rows = append(rows, row)
	}

	columns := []ui.Column{
		{Title: "role"},
		{Title: "model"},
		{Title: "precision"},
	}
	if split {
		columns = append(columns, ui.Column{Title: "weights", Right: true})
	}
	columns = append(columns,
		ui.Column{Title: "reserved", Right: true},
		ui.Column{Title: "seat"},
		ui.Column{Title: "endpoint"},
	)
	ui.Table(columns, rows)
}

func arcaneEndpointCell(seat arcane.Tenant) string {
	endpoint := seat.Endpoint()
	if seat.TensorParallel > 1 {
		endpoint += fmt.Sprintf(" · tp=%d", seat.TensorParallel)
	}
	return endpoint
}

func arcaneSeatKey(profile arcane.Profile, seat arcane.Tenant) string {
	if !profile.Multi() {
		return seat.Role
	}
	return fmt.Sprintf("gpu%d %s", seat.GPU, seat.Role)
}

func arcaneToggleNotes(profile arcane.Profile, card arcane.Card, budget arcaneBudget) {
	committed, usable := card.Committed, card.Usable
	if budget.Available && len(budget.PerGPU) > 0 {
		committed, usable = budget.PerGPU[0].Allocated, budget.PerGPU[0].UsableGiB
	}
	for _, toggle := range profile.Toggles() {
		if toggle.Enabled {
			ui.Note(fmt.Sprintf("%s is ON, reserving %.1f GiB", toggle.Role, toggle.VRAMGiB))
			continue
		}
		projected := arcaneRound1(committed + toggle.VRAMGiB)
		card.Usable = usable
		if projected <= usable {
			ui.Note(fmt.Sprintf("%s is OFF; switching it on reserves a further %.1f GiB → %.1f / %.1f GiB, which fits",
				toggle.Role, toggle.VRAMGiB, projected, profile.VRAMGiB))
			continue
		}
		ui.Note(fmt.Sprintf("%s is OFF; switching it on reserves a further %.1f GiB → %.1f / %.1f GiB, which does NOT fit",
			toggle.Role, toggle.VRAMGiB, projected, profile.VRAMGiB))
		if profile.Name == "rtx-pro-6000" {
			ui.Note("to fit it: drop the generator to city96/FLUX.1-dev-gguf (flux1-dev-Q4_K_S.gguf, 6.81 GiB) — costs the impasto the rubric measures — or push the governor remote")
		}
	}
}

func arcaneDetailTree(profile arcane.Profile, color ui.Color, detailed bool) {
	if !detailed {
		return
	}
	children := make([]ui.PairRow, 0, 8)
	for _, note := range arcane.KernelNotes(profile.SM) {
		children = append(children, ui.PairRow{Left: profile.SM, Right: note})
	}
	groups := []ui.TreeGroup{{Name: "kernel family", Detail: profile.SM, Color: color, Children: children}}
	if !profile.Multi() && profile.Notes != "" {
		groups = append(groups, ui.TreeGroup{
			Name:     "profile notes",
			Detail:   profile.Name,
			Color:    color,
			Children: []ui.PairRow{{Left: "note", Right: profile.Notes}},
		})
	}
	ui.Tree("kernels", "what this compute capability can and cannot run", groups)
}

func arcaneRoleCell(seat arcane.Tenant) string {
	label := seat.Role
	if seat.Shard != "" {
		label += " " + seat.Shard
	}
	if seat.Mandatory {
		return ui.Strong(label)
	}
	return ui.Soft(label)
}

func arcaneModelCell(seat arcane.Tenant) string {
	if arcane.IsBanned(seat.Model) {
		return ui.Bad(seat.Model)
	}
	if seat.Mandatory {
		return seat.Model
	}
	return ui.Soft(seat.Model)
}

func arcaneVRAMCell(seat arcane.Tenant) string {
	text := fmt.Sprintf("%.1f", seat.VRAMGiB)
	if seat.Enabled {
		return ui.Strong(text)
	}
	return ui.Soft(text)
}

func arcaneSeatCell(seat arcane.Tenant) string {
	switch {
	case seat.Mandatory:
		return ui.State("always-on")
	case seat.Enabled:
		return ui.State("toggle · on")
	default:
		return ui.State("toggle · off")
	}
}

// arcaneSourceBlock reports where the roster came from before printing any of
// it, so a degraded read is never mistaken for the live config.
func arcaneSourceBlock(registry arcane.Registry, shown []arcane.Profile) {
	if registry.Degraded {
		ui.KV("source", ui.Warn("fallback")+" "+ui.Soft(registry.Source))
	} else {
		ui.KV("source", ui.Good("config")+" "+ui.Soft(registry.Source))
	}
	ui.KV("path", ui.Soft(registry.SourcePath))
	ui.KV("default", ui.State(registry.DefaultProfile().Name))
	ui.KV("precedence", ui.State(string(registry.Prefer))+" "+ui.Soft("wins on MODEL IDENTITY where "+arcane.ContinuumFile+" and the ratified roster disagree"))

	// VRAM precedence is stated separately and unconditionally, because it is
	// the opposite of the identity policy and it is the one that decides
	// whether a card is reported as comfortable or as one KV growth from an OOM.
	unresolved := 0
	under := 0.0
	for _, drift := range registry.VRAMDrift {
		if !strings.Contains(drift, "UNRESOLVED") || !arcaneConcerns(drift, shown, registry) {
			continue
		}
		unresolved++
	}
	for _, profile := range shown {
		if gap := profile.Committed() - profile.WeightsCommitted(); gap > under {
			under = gap
		}
	}
	vram := ui.State("config reservation") + " " + ui.Soft("gpu_memory_utilization × card — roster weights are never used as a budget")
	ui.KV("vram", vram)
	if under > 0.05 {
		ui.Note(fmt.Sprintf("weights under-report by up to %.1f GiB on the profiles shown; that difference is KV cache and activation arena", under))
	}
	if unresolved > 0 {
		ui.Field("vram unresolved", "warn", fmt.Sprintf("%d seat(s) where the roster model and the config's sized model differ, so no reservation applies — see --json vram_drift", unresolved))
	}
	if len(registry.Extra) > 0 {
		ui.KV("config-only", ui.Soft(strings.Join(registry.Extra, ", ")+" — present in config, not part of the arcane roster"))
	}
	for _, note := range registry.Notes {
		ui.Note(note)
	}
	// Only the drift that concerns the profiles being printed, so narrowing to
	// one profile narrows the noise too. Everything is always in --json.
	relevant := registry.Drift
	if len(shown) > 0 && len(shown) < len(registry.Profiles) {
		relevant = nil
		for _, drift := range registry.Drift {
			for _, profile := range shown {
				if strings.HasPrefix(drift, profile.Name+"/") {
					relevant = append(relevant, drift)
					break
				}
			}
		}
	}
	if len(relevant) > 0 {
		ui.KV("drift", ui.Warn(fmt.Sprintf("%d", len(relevant)))+" "+ui.Soft("disagreement(s) between config and the ratified roster"))
		preview := relevant
		if len(preview) > arcaneDriftPreview {
			preview = preview[:arcaneDriftPreview]
		}
		for _, drift := range preview {
			ui.Note(drift)
		}
		if len(relevant) > len(preview) {
			ui.Note(fmt.Sprintf("… and %d more — `flux arcane models list --json` lists every one, `--prefer config` serves the config's values instead", len(relevant)-len(preview)))
		}
	}
}

// arcaneConcerns reports whether a per-seat note is about one of the profiles
// currently on screen, so narrowing to one profile narrows its notes too.
func arcaneConcerns(note string, shown []arcane.Profile, registry arcane.Registry) bool {
	if len(shown) == 0 || len(shown) == len(registry.Profiles) {
		return true
	}
	for _, profile := range shown {
		if strings.HasPrefix(note, profile.Name+"/") {
			return true
		}
	}
	return false
}

func arcaneProfilesJSON(profiles []arcane.Profile) []map[string]any {
	out := make([]map[string]any, 0, len(profiles))
	for _, profile := range profiles {
		dense, sparse := profile.DenseOnly()
		fits, over := profile.Fits()
		out = append(out, map[string]any{
			"gpus":                     profile.CardCount(),
			"interconnect":             profile.Interconnect,
			"interconnect_verified":    profile.InterconnectVerified,
			"interconnect_detected":    profile.InterconnectDetected,
			"tensor_parallel_viable":   profile.TPViable,
			"layout":                   profile.Layout,
			"layout_options":           profile.LayoutOptions,
			"total_vram_gib":           profile.TotalVRAMGiB(),
			"fits_per_gpu":             fits,
			"overflowing_gpus":         over,
			"cards":                    profile.Cards(),
			"name":                     profile.Name,
			"default":                  profile.Default,
			"gpu":                      profile.GPU,
			"memory_kind":              profile.MemoryKind,
			"bandwidth":                profile.Bandwidth,
			"vram_gib":                 profile.VRAMGiB,
			"reserve_gib":              profile.ReserveGiB,
			"usable_gib":               profile.Usable(),
			"sm":                       profile.SM,
			"vllm_min_version":         profile.VLLMMin,
			"nvfp4_dense":              profile.NVFP4Dense,
			"nvfp4_moe":                profile.NVFP4MoE,
			"prebuilt_wheel_available": profile.WheelInBank,
			"kernel_notes":             arcane.KernelNotes(profile.SM),
			"notes":                    profile.Notes,
			"committed_gib":            profile.Committed(),
			"projected_gib":            profile.Projected(),
			"dense_only":               dense,
			"non_dense_tenants":        sparse,
			"tenants":                  profile.Tenants,
		})
	}
	return out
}

// arcaneModelsLoad warms the generator through the resident worker and reports
// — without claiming — the state of every other seat.
func arcaneModelsLoad(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("arcane models load", flag.ExitOnError)
	profileName := fs.String("profile", "", "hardware `profile`")
	layout := fs.String("layout", "", "placement `layout` for rtx-pro-6000-x4")
	preload := fs.Bool("preload", true, "load the generator weights immediately")
	if err := fs.Parse(args); err != nil {
		return err
	}

	registry := arcane.Load(cfg.Root)
	profile, err := registry.WithLayout(*profileName, *layout)
	if err != nil {
		return err
	}

	ui.Header("arcane models load", "warm the roster into VRAM · "+profile.Name)
	ui.KV("profile", ui.State(profile.Name)+" "+ui.Soft(profile.GPU))
	ui.KV("committed", ui.Soft(fmt.Sprintf("%.1f GiB across %d enabled seats on %d card(s)", profile.Committed(), arcaneEnabledCount(profile), profile.CardCount())))

	ui.Section("generator", "resident UDS worker — this CLI starts it", ui.Mint)
	if err := loadWorker(cfg, []string{fmt.Sprintf("--preload=%t", *preload)}); err != nil {
		return err
	}

	ui.Section("tenants", "vLLM seats are started by provision_jury.sh, not by this command", ui.Teal)
	for _, card := range profile.Cards() {
		for _, seat := range card.Tenants {
			if seat.Role == arcane.RoleGenerator {
				continue
			}
			probe := arcaneTenantProbe(cfg, seat)
			ui.Field(arcaneSeatKey(profile, seat), probe.Status, probe.Detail)
		}
	}
	fmt.Println()
	ui.Suite("to bring the vLLM seats up", ui.Gold, []ui.PairRow{
		{"./provision_jury.sh", "start every vLLM tenant for the active profile"},
		{"flux arcane provision", "verify the whole estate afterwards"},
	})
	fmt.Println()
	return nil
}

func arcaneEnabledCount(profile arcane.Profile) int {
	n := 0
	for _, seat := range profile.Tenants {
		if seat.Enabled {
			n++
		}
	}
	return n
}

// ---------------------------------------------------------------------------
// profiles
// ---------------------------------------------------------------------------

func arcaneProfiles(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("arcane profiles", flag.ExitOnError)
	profileName := fs.String("profile", "", "narrow to a single `profile`")
	layout := fs.String("layout", "", "placement `layout` for rtx-pro-6000-x4: balanced, dense, tp")
	prefer := fs.String("prefer", "roster", "model identity on a disagreement: `roster` or config (VRAM is always the config's reservation)")
	noPython := fs.Bool("no-python", false, "skip pipeline_paths.vram_budget() and use local arithmetic")
	asJSON := fs.Bool("json", false, "emit the profiles as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	precedence, err := arcane.ParsePrecedence(*prefer)
	if err != nil {
		return err
	}
	registry := arcane.LoadWith(cfg.Root, precedence)
	profiles := registry.Profiles
	switch {
	case *profileName != "":
		profile, err := registry.WithLayout(*profileName, *layout)
		if err != nil {
			return err
		}
		profiles = []arcane.Profile{profile}
	case *layout != "":
		return fmt.Errorf("--layout needs --profile rtx-pro-6000-x4")
	}

	budgets := arcaneBudgets(cfg, profiles, *noPython)
	for i := range profiles {
		arcaneApplyBudget(&profiles[i], budgets[profiles[i].Name])
	}

	if *asJSON {
		return arcaneEmitJSON(map[string]any{
			"source":          registry.Source,
			"source_path":     registry.SourcePath,
			"degraded":        registry.Degraded,
			"notes":           registry.Notes,
			"vram_drift":      registry.VRAMDrift,
			"budgets":         budgets,
			"default_profile": registry.DefaultProfile().Name,
			"profiles":        arcaneProfilesJSON(profiles),
		})
	}

	ui.Header("arcane profiles", "hardware postures side by side")
	arcaneSourceBlock(registry, profiles)

	columns := []ui.Column{{Title: ""}}
	for _, profile := range profiles {
		title := profile.Name
		if profile.Name == registry.DefaultProfile().Name {
			title += " *"
		}
		columns = append(columns, ui.Column{Title: title})
	}

	ui.Section("silicon", "* marks the default profile", ui.Violet)
	silicon := [][]string{
		arcaneRow(profiles, "gpu", func(p arcane.Profile) string { return arcaneShortGPU(p.GPU) }),
		arcaneRow(profiles, "gpus", func(p arcane.Profile) string {
			if p.Multi() {
				return ui.Strong(fmt.Sprintf("%d", p.CardCount())) + ui.Soft(" cards")
			}
			return ui.Soft("1 card")
		}),
		arcaneRow(profiles, "memory/card", func(p arcane.Profile) string {
			text := fmt.Sprintf("%.0f GiB %s", p.VRAMGiB, p.MemoryKind)
			if p.Bandwidth != "" {
				text += " · " + p.Bandwidth
			}
			return text
		}),
		arcaneRow(profiles, "memory total", func(p arcane.Profile) string {
			if !p.Multi() {
				return ui.Soft(fmt.Sprintf("%.0f GiB", p.TotalVRAMGiB()))
			}
			return ui.Soft(fmt.Sprintf("%.0f GiB (sum, not a pool)", p.TotalVRAMGiB()))
		}),
		arcaneRow(profiles, "interconnect", func(p arcane.Profile) string {
			text := ui.State(valueOr(p.Interconnect, "unknown"))
			if p.InterconnectDetected != "" {
				text += ui.Soft(" · detected " + p.InterconnectDetected)
			} else if p.Multi() {
				text += ui.Soft(" · declared, verify with provision")
			}
			return text
		}),
		arcaneRow(profiles, "tensor parallel", func(p arcane.Profile) string {
			if !p.Multi() {
				return ui.State("n/a") + ui.Soft(" · one card, tp=1")
			}
			if !p.TPViable {
				return ui.State("impractical") + ui.Soft(" · tp=1 only, shard instead")
			}
			return ui.State("viable") + ui.Soft(" · not needed for capacity")
		}),
		arcaneRow(profiles, "compute", func(p arcane.Profile) string { return ui.State(p.SM) }),
		arcaneRow(profiles, "mma path", func(p arcane.Profile) string {
			if p.SM == "sm_120" {
				return ui.Soft("mma.sync (SM80-era)")
			}
			return ui.Soft("tcgen05.mma")
		}),
		arcaneRow(profiles, "smem/tile", func(p arcane.Profile) string {
			if p.SM == "sm_120" {
				return ui.Soft("99 KB")
			}
			return ui.Soft("228 KB")
		}),
		arcaneRow(profiles, "nvfp4 dense", func(p arcane.Profile) string { return arcaneYesNo(p.NVFP4Dense) }),
		arcaneRow(profiles, "nvfp4 moe", func(p arcane.Profile) string { return arcaneYesNo(p.NVFP4MoE) }),
		arcaneRow(profiles, "vllm floor", func(p arcane.Profile) string { return ui.Soft(">= " + p.VLLMMin) }),
		arcaneRow(profiles, "r2 wheel", func(p arcane.Profile) string {
			if p.WheelInBank {
				return ui.State("in bank")
			}
			return ui.State("build required")
		}),
	}
	ui.Table(columns, silicon)

	ui.Section("roster", "model · precision per seat", ui.Lilac)
	var roster [][]string
	for _, role := range arcane.RoleOrder {
		roster = append(roster, arcaneRow(profiles, role, func(p arcane.Profile) string {
			seats := p.Seats(role)
			if len(seats) == 0 {
				return ui.Soft("—")
			}
			cell := arcaneShortModel(seats[0].Model) + " · " + seats[0].Precision
			if len(seats) > 1 {
				cell += fmt.Sprintf(" ×%d", len(seats))
			}
			if !seats[0].Enabled {
				return ui.Soft(cell + " (off)")
			}
			return cell
		}))
	}
	roster = append(roster, arcaneRow(profiles, "committed", func(p arcane.Profile) string {
		return ui.Strong(fmt.Sprintf("%.1f", p.Committed())) + ui.Soft(fmt.Sprintf(" / %.1f GiB", p.TotalVRAMGiB()))
	}))
	roster = append(roster, arcaneRow(profiles, "busiest card", func(p arcane.Profile) string {
		busiest := arcane.Card{}
		for _, card := range p.Cards() {
			if card.Committed >= busiest.Committed {
				busiest = card
			}
		}
		return fmt.Sprintf("gpu %d · ", busiest.Index) + ui.Strong(fmt.Sprintf("%.1f", busiest.Committed)) + ui.Soft(fmt.Sprintf(" / %.1f GiB", p.VRAMGiB))
	}))
	roster = append(roster, arcaneRow(profiles, "fits (per gpu)", func(p arcane.Profile) string {
		if fits, _ := p.Fits(); fits {
			return ui.State("yes")
		}
		return ui.State("no")
	}))
	roster = append(roster, arcaneRow(profiles, "all toggles on", func(p arcane.Profile) string {
		projected := p.Projected()
		if p.Multi() {
			if fits, _ := p.Fits(); fits {
				return ui.Soft(fmt.Sprintf("%.1f GiB aggregate", projected)) + " " + ui.State("fits per gpu")
			}
			return ui.Soft(fmt.Sprintf("%.1f GiB aggregate", projected)) + " " + ui.State("does not fit")
		}
		if projected > p.Usable() {
			return ui.Soft(fmt.Sprintf("%.1f / %.1f GiB", projected, p.VRAMGiB)) + " " + ui.State("does not fit")
		}
		return fmt.Sprintf("%.1f", projected) + ui.Soft(fmt.Sprintf(" / %.1f GiB", p.VRAMGiB)) + " " + ui.State("fits")
	}))
	ui.Table(columns, roster)

	ui.Section("capacity", "reservations per physical card — never aggregated, never weights", ui.Indigo)
	for _, profile := range profiles {
		budget := budgets[profile.Name]
		if budget.Available && len(budget.PerGPU) > 0 {
			for _, card := range budget.PerGPU {
				label := profile.Name
				if len(budget.PerGPU) > 1 {
					label = fmt.Sprintf("%s · gpu %d", arcaneShortProfile(profile.Name), card.GPU)
				}
				ui.Capacity(label, card.Allocated, card.TotalGiB, "GiB")
			}
			continue
		}
		for _, card := range profile.Cards() {
			label := profile.Name
			if profile.Multi() {
				label = fmt.Sprintf("%s · gpu %d", arcaneShortProfile(profile.Name), card.Index)
			}
			ui.Capacity(label, card.Committed, card.Capacity, "GiB")
		}
	}
	arcaneBudgetSummary(profiles, budgets)

	fmt.Println()
	for _, profile := range profiles {
		if profile.Notes != "" {
			ui.Note(profile.Name + ": " + profile.Notes)
		}
	}
	fmt.Println()
	return nil
}

// arcaneShortProfile keeps a per-card label inside the capacity rail's width.
func arcaneShortProfile(name string) string {
	return strings.TrimPrefix(name, "rtx-pro-6000-")
}

func arcaneRow(profiles []arcane.Profile, label string, cell func(arcane.Profile) string) []string {
	row := []string{ui.Soft(strings.ToUpper(label))}
	for _, profile := range profiles {
		row = append(row, cell(profile))
	}
	return row
}

func arcaneYesNo(v bool) string {
	if v {
		return ui.State("yes")
	}
	return ui.State("blocked")
}

// arcaneShortGPU trims the marketing words so three cards fit side by side.
func arcaneShortGPU(name string) string {
	short := strings.TrimPrefix(name, "NVIDIA ")
	short = strings.ReplaceAll(short, "Blackwell Server Edition", "Blackwell SE")
	short = strings.ReplaceAll(short, "(Blackwell Ultra)", "Ultra")
	short = strings.ReplaceAll(short, "'Blackwell Ultra'", "Ultra")
	return strings.TrimSpace(short)
}

// arcaneShortModel drops the org prefix for the comparison grid. The full id is
// always shown by `arcane models`.
func arcaneShortModel(model string) string {
	if strings.Contains(model, "+") {
		return "dinov2-giant + siglip"
	}
	if i := strings.LastIndex(model, "/"); i >= 0 {
		return model[i+1:]
	}
	return model
}

// ---------------------------------------------------------------------------
// provisioning
// ---------------------------------------------------------------------------

type arcaneProbeResult struct {
	Key      string `json:"key"`
	Status   string `json:"status"`
	Detail   string `json:"detail"`
	Blocking bool   `json:"blocking,omitempty"`
}

type arcaneStageResult struct {
	Name   string              `json:"name"`
	Detail string              `json:"detail,omitempty"`
	Color  ui.Color            `json:"-"`
	Probes []arcaneProbeResult `json:"probes"`
}

func (s *arcaneStageResult) add(key, status, detail string) {
	s.Probes = append(s.Probes, arcaneProbeResult{Key: key, Status: status, Detail: detail})
}

func (s *arcaneStageResult) block(key, status, detail string) {
	s.Probes = append(s.Probes, arcaneProbeResult{Key: key, Status: status, Detail: detail, Blocking: true})
}

func (s arcaneStageResult) render() {
	ui.Section(s.Name, s.Detail, s.Color)
	for _, probe := range s.Probes {
		ui.Field(probe.Key, probe.Status, probe.Detail)
	}
}

// arcaneSilicon is what nvidia-smi actually said. Every field is empty until a
// probe fills it; nothing here has a hopeful default.
type arcaneSilicon struct {
	Present  bool
	Name     string
	SM       string
	Cap      string
	VRAMGiB  float64
	Driver   string
	Count    int
	ProbeErr string
}

func arcaneProvision(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("arcane provision", flag.ExitOnError)
	profileName := fs.String("profile", "", "hardware `profile`: rtx-pro-6000, rtx-pro-6000-x4, b200, b300")
	layout := fs.String("layout", "", "placement `layout` for rtx-pro-6000-x4: balanced, dense, tp")
	dryRun := fs.Bool("dry-run", false, "probe and report; make no changes")
	asJSON := fs.Bool("json", false, "emit the provisioning report as JSON")
	skipSurfaces := fs.Bool("no-surfaces", false, "skip the studio surface stage")
	if err := fs.Parse(args); err != nil {
		return err
	}

	registry := arcane.Load(cfg.Root)
	profile, err := registry.WithLayout(*profileName, *layout)
	if err != nil {
		return err
	}

	interp := arcaneResolvePython(cfg)
	silicon := arcaneProbeSilicon()
	link := arcaneProbeInterconnect()
	python := arcaneProbePython(interp)
	budget := arcaneProbeBudget(cfg, interp, profile.Name)
	arcaneApplyBudget(&profile, budget)

	stages := []arcaneStageResult{
		arcaneStageSilicon(silicon),
		arcaneStageInterconnect(profile, link),
		arcaneStageBudget(profile, budget),
		arcaneStageFit(profile, silicon, budget),
		arcaneStageRuntime(cfg, profile, interp, python),
		arcaneStageWeights(cfg, *dryRun),
		arcaneStageTenants(cfg, profile),
		arcaneStagePipeline(cfg, registry),
	}
	if !*skipSurfaces {
		stages = append(stages, arcaneStageSurfaces(cfg, interp, profile.Name, false, *dryRun))
	}

	blocking := 0
	warnings := 0
	for _, stage := range stages {
		for _, probe := range stage.Probes {
			if probe.Blocking {
				blocking++
			} else if probe.Status == "warn" || probe.Status == "unknown" || probe.Status == "unavailable" {
				warnings++
			}
		}
	}

	if *asJSON {
		return arcaneEmitJSON(map[string]any{
			"profile":        profile.Name,
			"dry_run":        *dryRun,
			"source":         registry.Source,
			"degraded":       registry.Degraded,
			"blocking":       blocking,
			"warnings":       warnings,
			"provisionable":  blocking == 0,
			"host":           runtime.GOOS + "/" + runtime.GOARCH,
			"interpreter":    interp,
			"silicon":        silicon,
			"interconnect":   link,
			"budget":         budget,
			"stages":         stages,
			"committed_gib":  profile.Committed(),
			"weights_gib":    profile.WeightsCommitted(),
			"capacity_gib":   profile.VRAMGiB,
			"kernel_notes":   arcane.KernelNotes(profile.SM),
			"registry_notes": registry.Notes,
		})
	}

	title := "arcane provision"
	subtitle := "real probes · " + profile.Name
	if *dryRun {
		subtitle = "dry run · nothing is written · " + profile.Name
	}
	ui.Header(title, subtitle)
	ui.KV("profile", ui.State(profile.Name)+" "+ui.Soft(fmt.Sprintf("%s · %s · %d card(s)", profile.GPU, profile.SM, profile.CardCount())))
	if profile.Layout != "" {
		ui.KV("layout", ui.State(profile.Layout)+" "+ui.Soft("options: "+strings.Join(profile.LayoutOptions, ", ")))
	}
	ui.KV("roster", ui.Soft(fmt.Sprintf("%.1f GiB reserved across %d enabled seats (weights %.1f GiB) · busiest card %.1f / %.1f GiB",
		profile.Committed(), arcaneEnabledCount(profile), profile.WeightsCommitted(), arcaneBusiest(profile).Committed, profile.VRAMGiB)))
	ui.KV("config", ui.Soft(registry.Source))

	for _, stage := range stages {
		stage.render()
	}

	ui.Section("verdict", "counted from the probes above, not assumed", ui.Violet)
	arcaneBudgetBlock(profile, budget)
	switch {
	case blocking == 0 && warnings == 0:
		ui.Field("result", "ok", "every probe passed; this host can serve the "+profile.Name+" roster")
	case blocking == 0:
		ui.Field("result", "warn", fmt.Sprintf("%d non-blocking finding(s); no blocking failure", warnings))
	default:
		ui.Field("result", "fail", fmt.Sprintf("%d blocking failure(s), %d warning(s)", blocking, warnings))
	}
	fmt.Println()

	if *dryRun {
		ui.Note("dry run: no symlink was created, no daemon was started, no file was written")
		if blocking > 0 {
			ui.Note(fmt.Sprintf("a real `flux arcane provision` on this host would exit non-zero on %d blocking failure(s)", blocking))
		}
		fmt.Println()
		return nil
	}
	if blocking > 0 {
		return fmt.Errorf("arcane provision: %d blocking failure(s) on profile %s", blocking, profile.Name)
	}
	fmt.Println()
	return nil
}

func arcaneStageSilicon(silicon arcaneSilicon) arcaneStageResult {
	stage := arcaneStageResult{Name: "silicon", Detail: "what nvidia-smi reports, or nothing", Color: ui.Mint}

	host, _ := os.Hostname()
	stage.add("host", "ok", strings.TrimSpace(host+" · "+runtime.GOOS+"/"+runtime.GOARCH))

	if !silicon.Present {
		detail := "nvidia-smi is not on PATH"
		if silicon.ProbeErr != "" {
			detail = silicon.ProbeErr
		}
		if runtime.GOOS == "darwin" {
			detail += " — this is macOS; no CUDA device is possible here"
		}
		stage.block("nvidia-smi", "not detected", detail)
		stage.add("gpu", "unknown", "no device enumerated")
		stage.add("compute", "unknown", "compute capability undetermined")
		stage.add("vram", "unknown", "board memory undetermined")
		stage.add("driver", "unknown", "driver version undetermined")
		return stage
	}

	stage.add("nvidia-smi", "ok", fmt.Sprintf("%d device(s) enumerated", silicon.Count))
	stage.add("gpu", "ok", silicon.Name)
	if silicon.SM != "" {
		stage.add("compute", "ok", silicon.SM+" (compute capability "+silicon.Cap+")")
	} else {
		stage.add("compute", "unknown", "nvidia-smi did not report compute_cap")
	}
	if silicon.VRAMGiB > 0 {
		stage.add("vram", "ok", fmt.Sprintf("%.1f GiB reported by the driver", silicon.VRAMGiB))
	} else {
		stage.add("vram", "unknown", "memory.total not reported")
	}
	stage.add("driver", "ok", silicon.Driver)
	return stage
}

func arcaneStageFit(profile arcane.Profile, silicon arcaneSilicon, budget arcaneBudget) arcaneStageResult {
	stage := arcaneStageResult{Name: "profile fit", Detail: "does this silicon match the selected profile", Color: ui.Teal}

	switch {
	case silicon.SM == "":
		stage.add("kernel family", "unknown", "cannot compare against "+profile.SM+" without a detected device")
	case silicon.SM == profile.SM:
		stage.add("kernel family", "ok", "detected "+silicon.SM+" matches profile "+profile.SM)
	default:
		stage.block("kernel family", "fail", "detected "+silicon.SM+" but profile "+profile.Name+" targets "+profile.SM+" — different kernel families, not interchangeable")
	}

	switch {
	case silicon.VRAMGiB <= 0:
		stage.add("board memory", "unknown", fmt.Sprintf("profile expects %.1f GiB per card; nothing detected to compare", profile.VRAMGiB))
	case silicon.VRAMGiB >= profile.VRAMGiB*0.95:
		stage.add("board memory", "ok", fmt.Sprintf("%.1f GiB per card detected against %.1f GiB expected", silicon.VRAMGiB, profile.VRAMGiB))
	default:
		stage.block("board memory", "fail", fmt.Sprintf("%.1f GiB detected but profile %s budgets against %.1f GiB per card", silicon.VRAMGiB, profile.Name, profile.VRAMGiB))
	}

	switch {
	case silicon.Count == 0:
		stage.add("card count", "unknown", fmt.Sprintf("profile %s places tenants on %d card(s); none detected", profile.Name, profile.CardCount()))
	case silicon.Count == profile.CardCount():
		stage.add("card count", "ok", fmt.Sprintf("%d card(s) detected, %d placed", silicon.Count, profile.CardCount()))
	case silicon.Count < profile.CardCount():
		stage.block("card count", "fail", fmt.Sprintf("%d card(s) detected but profile %s places tenants on %d — placement cannot be satisfied", silicon.Count, profile.Name, profile.CardCount()))
	default:
		stage.add("card count", "warn", fmt.Sprintf("%d card(s) detected, profile %s only places tenants on %d", silicon.Count, profile.Name, profile.CardCount()))
	}

	// Capacity is decided PER CARD, on RESERVATIONS. A roomy aggregate that
	// overflows one card does not fit, and a weights total that looks roomy
	// while the reservation does not fit is worse than no number at all.
	fits, over := profile.Fits()
	if budget.Available {
		fits = budget.Fits
		if !fits && budget.Reason != "" {
			over = []string{budget.Reason}
		}
	}
	switch {
	case !fits:
		stage.block("roster budget", "fail", "per-GPU overflow — "+strings.Join(over, "; "))
	case budget.Available && budget.Critical():
		stage.add("roster budget", "warn", "CRITICAL — fits by "+budget.BindingPhrase()+", a rounding error rather than a margin; it boots and then OOMs on the first KV cache growth")
	default:
		busiest := arcaneBusiest(profile).Committed
		usable := profile.Usable()
		if budget.Available && len(budget.PerGPU) > 0 {
			busiest, usable = budget.PerGPU[0].Allocated, budget.PerGPU[0].UsableGiB
		}
		detail := fmt.Sprintf("every card holds its placement; busiest reserves %.2f GiB against %.2f GiB usable", busiest, usable)
		if profile.Multi() {
			detail += fmt.Sprintf(" (aggregate %.1f GiB is a sum across %d cards, not a pool)", profile.Committed(), profile.CardCount())
		}
		stage.add("roster budget", "ok", detail)
	}

	dense, sparse := profile.DenseOnly()
	switch {
	case dense:
		stage.add("moe guard", "ok", fmt.Sprintf("all %d enabled seats are dense models", arcaneEnabledCount(profile)))
	case profile.SM == "sm_120":
		stage.block("moe guard", "fail", "sm_120 cannot run NVFP4 MoE kernels (vllm#33416, vllm#31085, flashinfer#2577); non-dense tenants: "+strings.Join(sparse, ", "))
	default:
		stage.add("moe guard", "warn", "non-dense tenants configured: "+strings.Join(sparse, ", "))
	}

	if profile.SM == "sm_120" && !profile.NVFP4MoE {
		stage.add("nvfp4", "ok", "dense NVFP4 GEMM is supported here; the MoE path is gated off and the roster never touches it")
	}

	if profile.WheelInBank {
		stage.add("vllm wheel", "ok", "the R2 artifact bank carries a wheel for "+profile.SM)
	} else {
		stage.add("vllm wheel", "warn", "PREREQUISITE: the R2 wheel bank ships sm100 and sm80 only — there is no sm120 wheel; vLLM >= "+profile.VLLMMin+" must be built for "+profile.SM+" first")
	}
	return stage
}

// ---------------------------------------------------------------------------
// Interpreter resolution
//
// `python3` on PATH cannot be trusted. This machine has a Homebrew shim at
// /opt/homebrew/bin/python3 that prints nothing and exits 0 for every
// invocation, including `-m py_compile` on a file with a syntax error. A
// delegated stage run through it returns success with no output, which is
// exactly the "green without checking" failure this file exists to remove.
//
// So: resolve deliberately, then prove the interpreter runs before trusting it.
// An interpreter that exits 0 with empty stdout is BROKEN, not ready.
// ---------------------------------------------------------------------------

type arcaneInterpreter struct {
	Path    string `json:"path,omitempty"`
	OK      bool   `json:"ok"`
	Version string `json:"version,omitempty"`
	Reason  string `json:"reason,omitempty"`
	Tried   int    `json:"candidates_tried"`
}

const arcaneInterpreterCheck = `import sys;print("%d.%d.%d" % sys.version_info[:3])`

// arcaneResolvePython walks the candidate interpreters in preference order and
// returns the first one that demonstrably executes Python. If every candidate
// is broken it returns the first broken one, with the reason, so provisioning
// can name the offender rather than shrugging.
func arcaneResolvePython(cfg config.Config) arcaneInterpreter {
	var candidates []string
	if explicit := os.Getenv("FLUX_PYTHON"); explicit != "" {
		candidates = append(candidates, explicit)
	}
	candidates = append(candidates,
		filepath.Join(cfg.Root, ".venv", "bin", "python"),
		filepath.Join(cfg.Root, ".venv", "bin", "python3"),
	)
	if home := os.Getenv("HOME"); home != "" {
		candidates = append(candidates,
			filepath.Join(home, ".venv", "bin", "python"),
			filepath.Join(home, ".venvs", "mlx", "bin", "python3"),
		)
	}
	if cfg.Python != "" {
		candidates = append(candidates, cfg.Python)
	}
	candidates = append(candidates, "python3", "python")

	var broken arcaneInterpreter
	seen := map[string]bool{}
	tried := 0
	for _, candidate := range candidates {
		path, ok := arcaneExecutable(candidate)
		if !ok || seen[path] {
			continue
		}
		seen[path] = true
		tried++
		version, reason := arcaneInterpreterVersion(path)
		if reason == "" {
			return arcaneInterpreter{Path: path, OK: true, Version: version, Tried: tried}
		}
		if broken.Path == "" {
			broken = arcaneInterpreter{Path: path, Reason: reason}
		}
	}
	broken.Tried = tried
	if broken.Path == "" {
		broken.Reason = "no python interpreter found on PATH or in the repo venv"
	}
	return broken
}

func arcaneExecutable(candidate string) (string, bool) {
	if strings.ContainsRune(candidate, filepath.Separator) {
		info, err := os.Stat(candidate)
		if err != nil || info.IsDir() {
			return "", false
		}
		return candidate, true
	}
	path, err := exec.LookPath(candidate)
	if err != nil {
		return "", false
	}
	return path, true
}

// arcaneInterpreterVersion returns the interpreter's version, or the reason it
// cannot be trusted. Empty stdout on a zero exit is the stub signature.
func arcaneInterpreterVersion(path string) (string, string) {
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, path, "-c", arcaneInterpreterCheck).Output()
	if err != nil {
		return "", "does not execute: " + err.Error()
	}
	text := strings.TrimSpace(string(out))
	if text == "" {
		return "", "BROKEN STUB — exits 0 and prints nothing; it is not a Python interpreter"
	}
	head := firstLine(text)
	if len(head) == 0 || head[0] < '0' || head[0] > '9' {
		return "", "unrecognised version output " + strconv.Quote(truncateText(head, 40))
	}
	return head, ""
}

func truncateText(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// arcaneWitness records whether anything non-blank was ever written through it,
// without buffering the stream. A delegated stage that exits 0 having printed
// nothing is indeterminate, not successful.
type arcaneWitness struct {
	sink io.Writer
	saw  bool
}

func (w *arcaneWitness) Write(p []byte) (int, error) {
	if !w.saw && strings.TrimSpace(string(p)) != "" {
		w.saw = true
	}
	return w.sink.Write(p)
}

// arcanePython is the single Python probe's result. Absent fields stay empty.
type arcanePython struct {
	Ran        bool   `json:"ran"`
	Version    string `json:"python,omitempty"`
	Torch      string `json:"torch,omitempty"`
	CUDA       string `json:"cuda,omitempty"`
	CUDAOK     bool   `json:"cuda_available"`
	Device     string `json:"cuda_device,omitempty"`
	VLLM       string `json:"vllm,omitempty"`
	VLLMErr    string `json:"vllm_error,omitempty"`
	Diffusers  string `json:"diffusers,omitempty"`
	Transforms string `json:"transformers,omitempty"`
	Err        string `json:"error,omitempty"`
}

const arcanePythonProbe = `
import json
out = {}
try:
    import platform
    out["python"] = platform.python_version()
except Exception as exc:
    out["error"] = repr(exc)
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda"] = getattr(torch.version, "cuda", "") or ""
    out["cuda_available"] = bool(torch.cuda.is_available())
    if out["cuda_available"] and torch.cuda.device_count():
        out["cuda_device"] = torch.cuda.get_device_name(0)
except Exception as exc:
    out["torch_error"] = type(exc).__name__
try:
    import vllm
    out["vllm"] = getattr(vllm, "__version__", "")
except Exception as exc:
    out["vllm_error"] = type(exc).__name__ + ": " + str(exc)[:120]
try:
    import diffusers
    out["diffusers"] = diffusers.__version__
except Exception as exc:
    out["diffusers_error"] = type(exc).__name__
try:
    import transformers
    out["transformers"] = transformers.__version__
except Exception as exc:
    out["transformers_error"] = type(exc).__name__
print(json.dumps(out))
`

func arcaneProbePython(interp arcaneInterpreter) arcanePython {
	result := arcanePython{}
	if !interp.OK {
		result.Err = interp.Reason
		return result
	}
	ctx, cancel := context.WithTimeout(context.Background(), 45*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, interp.Path, "-c", arcanePythonProbe).Output()
	if err != nil {
		result.Err = "probe failed: " + err.Error()
		return result
	}
	if strings.TrimSpace(string(out)) == "" {
		result.Err = "probe exited 0 and printed nothing — this interpreter is not executing code"
		return result
	}
	var raw map[string]any
	if err := json.Unmarshal(out, &raw); err != nil {
		result.Err = "probe returned unparseable output"
		return result
	}
	result.Ran = true
	result.Version, _ = raw["python"].(string)
	result.Torch, _ = raw["torch"].(string)
	result.CUDA, _ = raw["cuda"].(string)
	result.CUDAOK, _ = raw["cuda_available"].(bool)
	result.Device, _ = raw["cuda_device"].(string)
	result.VLLM, _ = raw["vllm"].(string)
	result.VLLMErr, _ = raw["vllm_error"].(string)
	result.Diffusers, _ = raw["diffusers"].(string)
	result.Transforms, _ = raw["transformers"].(string)
	return result
}

// arcaneStageBudget reports where the capacity numbers came from before any of
// them are used. An unavailable authority is stated, not silently replaced.
func arcaneStageBudget(profile arcane.Profile, budget arcaneBudget) arcaneStageResult {
	stage := arcaneStageResult{Name: "budget", Detail: "reservations, not checkpoint weights", Color: ui.Amber}

	if !budget.Available {
		stage.add("authority", "unknown", budget.Source+" did not run: "+valueOr(budget.Reason, "unavailable"))
		if profile.ReservationBacked() {
			stage.add("fallback", "warn", fmt.Sprintf("using this CLI's own total over %s reservations: %.1f GiB reserved, %.1f GiB of weights",
				arcane.ContinuumFile, profile.Committed(), profile.WeightsCommitted()))
		} else {
			stage.block("fallback", "fail", fmt.Sprintf("only ROSTER WEIGHTS are available (%.1f GiB); that omits the KV cache and activation arena and would report more headroom than the card has", profile.WeightsCommitted()))
		}
		return stage
	}

	stage.add("authority", "ok", budget.Source)
	stage.add("reserved", "ok", fmt.Sprintf("%.2f GiB of %.2f GiB usable after a %.2f GiB reserve", budget.Allocated, budget.UsableGiB, budget.TotalGiB-budget.UsableGiB))
	if budget.Weights > 0 {
		status := "ok"
		if budget.Gap() > 0.05 {
			status = "warn"
		}
		stage.add("weights gap", status, fmt.Sprintf("weights %.2f GiB · reserved %.2f GiB · %.2f GiB of KV cache and activation arena", budget.Weights, budget.Allocated, budget.Gap()))
	}
	for i, card := range budget.PerGPU {
		status := "ok"
		switch {
		case !card.Fits:
			status = "fail"
		case card.Headroom < 1.0:
			status = "warn"
		}
		key := "headroom"
		if len(budget.PerGPU) > 1 {
			key = fmt.Sprintf("gpu %d headroom", i)
		}
		detail := fmt.Sprintf("%.2f GiB over the reserve (%.2f%% of the card)", card.Headroom, 100*card.Headroom/max1(card.TotalGiB))
		if status == "fail" {
			stage.block(key, status, detail)
		} else {
			stage.add(key, status, detail)
		}
	}
	for _, warning := range budget.Warnings {
		if strings.HasPrefix(warning, "CRITICAL") {
			stage.add("critical", "warn", warning)
		}
	}
	return stage
}

func arcaneBusiest(profile arcane.Profile) arcane.Card {
	busiest := arcane.Card{}
	for _, card := range profile.Cards() {
		if card.Committed >= busiest.Committed {
			busiest = card
		}
	}
	return busiest
}

func arcaneStageRuntime(cfg config.Config, profile arcane.Profile, interp arcaneInterpreter, python arcanePython) arcaneStageResult {
	stage := arcaneStageResult{Name: "runtime", Detail: "interpreter, venv, torch and the vLLM floor", Color: ui.Indigo}

	venv := filepath.Join(cfg.Root, ".venv")
	if info, err := os.Stat(filepath.Join(venv, "bin", "python")); err == nil && !info.IsDir() {
		stage.add("venv", "ok", venv)
	} else {
		stage.add("venv", "warn", venv+" absent — run `flux setup`")
	}

	// The interpreter is proved before anything is asked of it: a shim that
	// exits 0 with no output would otherwise make every delegated stage green.
	if !interp.OK {
		where := interp.Path
		if where == "" {
			where = "(none)"
		}
		stage.block("interpreter", "fail", where+": "+interp.Reason)
		stage.add("python", "unknown", fmt.Sprintf("%d candidate interpreter(s) tried, none executes Python", interp.Tried))
		stage.add("torch", "unknown", "no working interpreter to ask")
		stage.add("cuda", "unknown", "no working interpreter to ask")
		stage.add("vllm", "unknown", "no working interpreter to ask")
		return stage
	}
	stage.add("interpreter", "ok", interp.Path+" · verified to execute Python "+interp.Version)

	if !python.Ran {
		detail := python.Err
		if detail == "" {
			detail = "interpreter did not respond"
		}
		stage.add("python", "unknown", interp.Path+": "+detail)
		stage.add("torch", "unknown", "capability probe was indeterminate")
		stage.add("cuda", "unknown", "capability probe was indeterminate")
		stage.add("vllm", "unknown", "capability probe was indeterminate")
		return stage
	}

	stage.add("python", "ok", python.Version+" · "+interp.Path)

	if python.Torch != "" {
		detail := "torch " + python.Torch
		if python.CUDA != "" {
			detail += " built against CUDA " + python.CUDA
		}
		stage.add("torch", "ok", detail)
	} else {
		stage.block("torch", "missing", "torch does not import in "+interp.Path)
	}

	switch {
	case python.CUDAOK && python.Device != "":
		stage.add("cuda", "ok", "torch.cuda.is_available() is true · "+python.Device)
	case python.Torch == "":
		stage.add("cuda", "unknown", "torch absent, so CUDA could not be queried")
	default:
		stage.block("cuda", "fail", "torch.cuda.is_available() is false — no usable CUDA device on this host")
	}

	switch {
	case python.VLLM == "":
		detail := "vLLM does not import"
		if python.VLLMErr != "" {
			detail += " (" + python.VLLMErr + ")"
		}
		if profile.VLLMMin != "" {
			detail += "; profile " + profile.Name + " needs >= " + profile.VLLMMin
		}
		stage.block("vllm", "missing", detail)
	case profile.VLLMMin == "":
		stage.add("vllm", "ok", python.VLLM+" (no version floor recorded for this profile)")
	default:
		ok, comparable := arcane.VersionAtLeast(python.VLLM, profile.VLLMMin)
		switch {
		case !comparable:
			stage.add("vllm", "unknown", python.VLLM+" cannot be compared against the >= "+profile.VLLMMin+" floor")
		case ok:
			stage.add("vllm", "ok", python.VLLM+" satisfies the >= "+profile.VLLMMin+" floor for "+profile.SM)
		default:
			stage.block("vllm", "fail", python.VLLM+" is below the >= "+profile.VLLMMin+" floor required for dense NVFP4 GEMM on "+profile.SM)
		}
	}

	if python.Diffusers != "" {
		stage.add("diffusers", "ok", "diffusers "+python.Diffusers+" · transformers "+valueOr(python.Transforms, "?"))
	} else {
		stage.add("diffusers", "warn", "diffusers does not import — the generator cannot run")
	}
	return stage
}

func arcaneStageWeights(cfg config.Config, dryRun bool) arcaneStageResult {
	stage := arcaneStageResult{Name: "weights", Detail: "FLUX.1-dev BF16 snapshot resolution", Color: ui.Gold}
	stage.add("model dir", "ok", cfg.ModelDir)

	if fluxModelReady(cfg.ModelDir) {
		stage.add("flux1-dev", "ok", "every required safetensors shard is present")
	} else {
		stage.block("flux1-dev", "missing", "snapshot incomplete at "+cfg.ModelDir+" — run `flux arcane models download`")
	}

	// The /models/flux1 canonical path. This is the one piece of the old
	// provisionArcane that was doing real work; it is kept, and it is the only
	// thing provisioning mutates.
	if info, err := os.Stat("/models/flux1"); err == nil && info.IsDir() {
		target := "/models/flux1"
		if resolved, err := filepath.EvalSymlinks("/models/flux1"); err == nil {
			target += " -> " + resolved
		}
		stage.add("/models/flux1", "ok", target)
		return stage
	}

	hfSnapshot := filepath.Join(cfg.Root, ".cache", "huggingface", "hub", "models--black-forest-labs--FLUX.1-dev", "snapshots")
	matches, _ := filepath.Glob(filepath.Join(hfSnapshot, "*"))
	if len(matches) == 0 {
		stage.add("/models/flux1", "warn", "absent, and no Hugging Face snapshot found under "+hfSnapshot+" to link it to")
		return stage
	}
	if dryRun {
		stage.add("/models/flux1", "warn", "absent; would symlink to "+matches[0]+" (dry run: not created)")
		return stage
	}
	if err := os.MkdirAll("/models", 0o777); err != nil {
		stage.add("/models/flux1", "warn", "cannot create /models: "+err.Error())
		return stage
	}
	_ = os.Remove("/models/flux1")
	if err := os.Symlink(matches[0], "/models/flux1"); err != nil {
		stage.add("/models/flux1", "warn", "symlink failed: "+err.Error())
		return stage
	}
	stage.add("/models/flux1", "ok", "created -> "+matches[0])
	return stage
}

func arcaneStageTenants(cfg config.Config, profile arcane.Profile) arcaneStageResult {
	stage := arcaneStageResult{Name: "tenants", Detail: "is each seat's endpoint actually answering", Color: ui.Rose}

	socket, _, _, _ := daemon.New(cfg).Paths()
	if conn, err := net.DialTimeout("unix", socket, 400*time.Millisecond); err == nil {
		_ = conn.Close()
		stage.add(".fluxd", "ok", socket+" is accepting connections")
	} else if _, statErr := os.Stat(socket); statErr == nil {
		stage.add(".fluxd", "warn", socket+" exists but does not accept connections — stale socket from a dead worker")
	} else {
		stage.add(".fluxd", "warn", socket+" absent — no resident worker; `flux arcane models load` starts one")
	}

	for _, card := range profile.Cards() {
		for _, seat := range card.Tenants {
			probe := arcaneTenantProbe(cfg, seat)
			stage.add(arcaneSeatKey(profile, seat), probe.Status, probe.Detail)
		}
	}
	return stage
}

func arcaneTenantProbe(cfg config.Config, seat arcane.Tenant) arcaneProbeResult {
	result := arcaneProbeResult{Key: seat.Role}
	switch {
	case !seat.Enabled:
		result.Status = "skip"
		result.Detail = seat.Model + " — seat is switched off in this profile"
	case seat.Remote:
		result.Status = "skip"
		result.Detail = seat.Model + " — served off-card; not probed from here"
	case seat.Kind == "inproc":
		result.Status = "skip"
		result.Detail = seat.Model + " — in-process inside moj_evaluator.py, no endpoint to probe"
	case seat.Port > 0:
		addr := fmt.Sprintf("127.0.0.1:%d", seat.Port)
		conn, err := net.DialTimeout("tcp", addr, 400*time.Millisecond)
		if err != nil {
			result.Status = "warn"
			result.Detail = addr + " not listening — " + seat.Model + " is not up"
			return result
		}
		_ = conn.Close()
		result.Status = "ok"
		result.Detail = addr + " answering · " + seat.Model
	case seat.Socket != "":
		path := filepath.Join(cfg.Root, ".fluxd", seat.Socket)
		conn, err := net.DialTimeout("unix", path, 400*time.Millisecond)
		if err != nil {
			result.Status = "warn"
			result.Detail = path + " not accepting connections — " + seat.Model + " is not resident"
			return result
		}
		_ = conn.Close()
		result.Status = "ok"
		result.Detail = path + " answering · " + seat.Model
	default:
		result.Status = "unknown"
		result.Detail = seat.Model + " — no endpoint declared for this seat"
	}
	return result
}

func arcaneStagePipeline(cfg config.Config, registry arcane.Registry) arcaneStageResult {
	stage := arcaneStageResult{Name: "pipeline", Detail: "the Python half of the estate", Color: ui.Lilac}
	for _, entry := range []struct {
		key      string
		file     string
		blocking bool
	}{
		{"continuum", arcane.ContinuumFile, false},
		{"arcane_pipeline", "arcane_pipeline.py", false},
		{"provision_jury", "provision_jury.sh", false},
		{"moj_evaluator", "moj_evaluator.py", false},
		{"sensory_gates", "sensory_gates.py", false},
		{"pipeline_paths", "pipeline_paths.py", false},
	} {
		path := filepath.Join(cfg.Root, entry.file)
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			stage.add(entry.key, "ok", fmt.Sprintf("%s · %d bytes", path, info.Size()))
		} else {
			stage.add(entry.key, "warn", path+" not present")
		}
	}
	if registry.Degraded {
		stage.add("config read", "warn", "the roster is being served from the compiled fallback, not "+arcane.ContinuumFile)
	} else {
		stage.add("config read", "ok", "roster parsed from "+arcane.ContinuumFile)
	}
	return stage
}

// ---------------------------------------------------------------------------
// surfaces
//
// Surface verification belongs to provision_surfaces.py. This CLI invokes it,
// folds its exit code in, and renders the route -> file mapping the Go server
// already implements. It does not check surfaces itself and it never edits HTML.
// ---------------------------------------------------------------------------

const arcaneSurfaceScript = "provision_surfaces.py"

// arcaneSurfaceRoutes mirrors the handler registrations in
// internal/server/server.go. Reported, not enforced: if the server's routing
// changes, this table is the thing that goes stale, so it is printed alongside
// a live stat of the file it claims to serve.
var arcaneSurfaceRoutes = []struct {
	Route   string
	Aliases string
	File    string
	Public  bool
}{
	{"/arcane", "/arcane/", "arcane.html", true},
	{"/jury", "/moj", "jury.html", false},
	{"/consult", "/consult/", "consult.html", true},
	{"/engine", "/engine-room", "engine.html", false},
	{"/exhibition", "/exhibition/", "exhibition.html", true},
	{"/gallery", "/gallery/", "gallery.html", true},
	{"/studies", "/studies/", "studies.html", true},
	{"/sentinel", "/sentinel/", "sentinel.html", true},
	{"/protocol", "/spec", "protocol.html", true},
}

func arcaneSurfaces(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("arcane surfaces", flag.ExitOnError)
	profileName := fs.String("profile", "", "hardware `profile` passed through to provision_surfaces.py")
	check := fs.Bool("check", false, "verify only; do not write the surface manifest")
	dryRun := fs.Bool("dry-run", false, "show what would be written without writing it")
	asJSON := fs.Bool("json", false, "emit the surface report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	registry := arcane.Load(cfg.Root)
	profile, err := registry.Lookup(*profileName)
	if err != nil {
		return err
	}

	interp := arcaneResolvePython(cfg)
	stage := arcaneStageSurfaces(cfg, interp, profile.Name, *check, *dryRun)
	routes := arcaneSurfaceRouteRows(cfg)

	if *asJSON {
		return arcaneEmitJSON(map[string]any{
			"profile":     profile.Name,
			"check":       *check,
			"dry_run":     *dryRun,
			"interpreter": interp,
			"stage":       stage,
			"routes":      routes,
		})
	}

	mode := "provision"
	if *check {
		mode = "verify only"
	}
	if *dryRun {
		mode += " · dry run"
	}
	ui.Header("arcane surfaces", "studio pages · "+mode)
	ui.KV("served from", ui.Soft(filepath.Join(cfg.Root, "apps", "tea", "public")))
	ui.KV("router", ui.Soft("internal/server/server.go · public listener honours the readOnlyPaths allowlist"))
	if interp.OK {
		ui.KV("python", ui.Soft(interp.Path+" · "+interp.Version))
	} else {
		ui.KV("python", ui.Warn("unusable")+" "+ui.Soft(valueOr(interp.Path, "(none)")+": "+interp.Reason))
	}

	ui.Section("routes", "handler registration -> file on disk", ui.Teal)
	rows := make([][]string, 0, len(routes))
	for _, route := range routes {
		access := ui.State("private")
		if route.Public {
			access = ui.State("public")
		}
		rows = append(rows, []string{
			ui.Strong(route.Route),
			ui.Soft(route.Aliases),
			route.File,
			ui.Verdict(route.Status),
			access,
		})
	}
	ui.Table([]ui.Column{
		{Title: "route"},
		{Title: "alias"},
		{Title: "file"},
		{Title: "on disk"},
		{Title: "read-only listener"},
	}, rows)

	stage.render()

	blocking := 0
	for _, probe := range stage.Probes {
		if probe.Blocking {
			blocking++
		}
	}
	fmt.Println()
	if blocking > 0 && !*dryRun {
		return fmt.Errorf("arcane surfaces: %d blocking failure(s)", blocking)
	}
	return nil
}

type arcaneRouteRow struct {
	Route   string `json:"route"`
	Aliases string `json:"aliases,omitempty"`
	File    string `json:"file"`
	Path    string `json:"path"`
	Status  string `json:"status"`
	Public  bool   `json:"read_only_listener"`
}

func arcaneSurfaceRouteRows(cfg config.Config) []arcaneRouteRow {
	public := filepath.Join(cfg.Root, "apps", "tea", "public")
	rows := make([]arcaneRouteRow, 0, len(arcaneSurfaceRoutes))
	for _, route := range arcaneSurfaceRoutes {
		path := filepath.Join(public, route.File)
		status := "missing"
		if info, err := os.Stat(path); err == nil && !info.IsDir() {
			status = "ok"
		}
		rows = append(rows, arcaneRouteRow{
			Route:   route.Route,
			Aliases: route.Aliases,
			File:    route.File,
			Path:    path,
			Status:  status,
			Public:  route.Public,
		})
	}
	return rows
}

// arcaneStageSurfaces shells out to provision_surfaces.py. A script that is not
// there yet is `unavailable` — not a failure, and emphatically not a success.
func arcaneStageSurfaces(cfg config.Config, interp arcaneInterpreter, profileName string, check, dryRun bool) arcaneStageResult {
	stage := arcaneStageResult{Name: "surfaces", Detail: "studio pages via " + arcaneSurfaceScript, Color: ui.Rose}

	script := filepath.Join(cfg.Root, arcaneSurfaceScript)
	if info, err := os.Stat(script); err != nil || info.IsDir() {
		stage.add(arcaneSurfaceScript, "unavailable", script+" is not present — surfaces were neither provisioned nor verified")
		return stage
	}
	if !interp.OK {
		stage.add(arcaneSurfaceScript, "unknown", "no working Python interpreter — "+arcaneSurfaceScript+" was not run, so surfaces are unverified")
		return stage
	}

	args := []string{script, "--json", "--profile", profileName}
	if check {
		args = append(args, "--check")
	}
	if dryRun {
		args = append(args, "--dry-run")
	}

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, interp.Path, args...)
	cmd.Dir = cfg.Root
	out, runErr := cmd.Output()

	exitCode := 0
	if runErr != nil {
		var exitErr *exec.ExitError
		if ok := asExitError(runErr, &exitErr); ok {
			exitCode = exitErr.ExitCode()
		} else {
			stage.add(arcaneSurfaceScript, "warn", "could not run "+script+": "+runErr.Error())
			return stage
		}
	}

	if strings.TrimSpace(string(out)) == "" {
		if exitCode == 0 {
			stage.add(arcaneSurfaceScript, "unknown", "exited 0 and printed nothing — indeterminate, not a pass; surfaces are unverified")
		} else {
			stage.block(arcaneSurfaceScript, "fail", fmt.Sprintf("exited %d with no output", exitCode))
		}
		return stage
	}

	var report map[string]any
	if err := json.Unmarshal(out, &report); err != nil {
		status := "ok"
		detail := fmt.Sprintf("%s exited %d; output was not JSON", arcaneSurfaceScript, exitCode)
		if exitCode != 0 {
			status = "fail"
		}
		if status == "fail" {
			stage.block(arcaneSurfaceScript, status, detail)
		} else {
			stage.add(arcaneSurfaceScript, status, detail)
		}
		if trimmed := strings.TrimSpace(string(out)); trimmed != "" {
			stage.add("output", "warn", firstLine(trimmed))
		}
		return stage
	}

	// Render what the script reports, defensively: `surfaces` is the agreed key
	// and the rest is summarised rather than dropped, so a schema change shows
	// up as thinner output instead of a silent pass.
	if surfaces, ok := report["surfaces"].([]any); ok {
		for _, item := range surfaces {
			entry, ok := item.(map[string]any)
			if !ok {
				continue
			}
			name := arcaneAnyString(entry, "surface", "route", "name", "file")
			status := arcaneAnyString(entry, "status", "state")
			if status == "" {
				if flag, isBool := entry["ok"].(bool); isBool {
					status = map[bool]string{true: "ok", false: "fail"}[flag]
				} else {
					status = "unknown"
				}
			}
			stage.add(name, status, arcaneSurfaceDetail(entry))
		}
	}

	if counts, ok := report["counts"].(map[string]any); ok {
		stage.add("counts", arcaneCountsStatus(counts), fmt.Sprintf("%d surface(s) · %d ok · %d warn · %d fail · %d orphan(s)",
			int(arcaneNum(counts, "surfaces")), int(arcaneNum(counts, "ok")), int(arcaneNum(counts, "warn")),
			int(arcaneNum(counts, "fail")), int(arcaneNum(counts, "orphans"))))
	}
	if manifest := arcaneAnyString(report, "manifest_written", "manifest_would_write", "manifest_path"); manifest != "" {
		status := "ok"
		if _, written := report["manifest_written"].(string); !written {
			status = "skip"
		}
		stage.add("manifest", status, manifest)
	}

	// The script's own verdict and its exit code must agree; a mismatch is
	// reported rather than resolved in favour of whichever looks better.
	scriptOK, hasVerdict := report["ok"].(bool)
	switch {
	case exitCode != 0:
		stage.block("result", "fail", fmt.Sprintf("%s exited %d", arcaneSurfaceScript, exitCode))
	case hasVerdict && !scriptOK:
		stage.block("result", "fail", arcaneSurfaceScript+" exited 0 but reports ok=false")
	default:
		stage.add("result", "ok", fmt.Sprintf("%s exited 0", arcaneSurfaceScript))
	}
	return stage
}

// arcaneSurfaceDetail turns one surface entry into a single readable line:
// where it is routed, and the first thing wrong with it.
func arcaneSurfaceDetail(entry map[string]any) string {
	detail := arcaneAnyString(entry, "file")
	if routes := arcaneStringList(entry, "routes"); len(routes) > 0 {
		detail = strings.Join(routes, " ") + " -> " + detail
	}
	if public, ok := entry["public_readonly"].(bool); ok && public {
		detail += " · public"
	}
	for _, key := range []string{"problems", "broken_refs", "unrouted_refs", "notes"} {
		if items := arcaneStringList(entry, key); len(items) > 0 {
			detail += " · " + key + ": " + items[0]
			if len(items) > 1 {
				detail += fmt.Sprintf(" (+%d)", len(items)-1)
			}
			break
		}
	}
	return detail
}

func arcaneCountsStatus(counts map[string]any) string {
	switch {
	case arcaneNum(counts, "fail") > 0:
		return "fail"
	case arcaneNum(counts, "warn") > 0 || arcaneNum(counts, "orphans") > 0:
		return "warn"
	}
	return "ok"
}

func arcaneNum(table map[string]any, key string) float64 {
	if v, ok := table[key].(float64); ok {
		return v
	}
	return 0
}

func arcaneStringList(table map[string]any, key string) []string {
	items, ok := table[key].([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(items))
	for _, item := range items {
		if text, ok := item.(string); ok && text != "" {
			out = append(out, text)
		}
	}
	return out
}

func arcaneAnyString(table map[string]any, keys ...string) string {
	for _, key := range keys {
		if v, ok := table[key].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

func asExitError(err error, target **exec.ExitError) bool {
	if e, ok := err.(*exec.ExitError); ok {
		*target = e
		return true
	}
	return false
}

func firstLine(s string) string {
	if i := strings.IndexByte(s, '\n'); i >= 0 {
		return s[:i]
	}
	return s
}

// ---------------------------------------------------------------------------
// preflight / pipeline delegation
// ---------------------------------------------------------------------------

const arcanePipelineScript = "arcane_pipeline.py"

func arcanePreflight(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("arcane preflight", flag.ExitOnError)
	profileName := fs.String("profile", "", "hardware `profile`")
	layout := fs.String("layout", "", "placement `layout` for rtx-pro-6000-x4")
	asJSON := fs.Bool("json", false, "emit the preflight report as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	interp := arcaneResolvePython(cfg)
	script := filepath.Join(cfg.Root, arcanePipelineScript)
	if info, err := os.Stat(script); err == nil && !info.IsDir() {
		if !interp.OK {
			ui.Header("arcane preflight", "delegates to "+arcanePipelineScript)
			ui.Field("interpreter", "fail", valueOr(interp.Path, "(none)")+": "+interp.Reason)
			fmt.Println()
			return fmt.Errorf("arcane preflight: no working Python interpreter; %s was not run", arcanePipelineScript)
		}
		forward := []string{script, "preflight"}
		if *profileName != "" {
			forward = append(forward, "--profile", *profileName)
		}
		if *asJSON {
			forward = append(forward, "--json")
		}
		forward = append(forward, fs.Args()...)
		witness := &arcaneWitness{sink: os.Stdout}
		cmd := exec.Command(interp.Path, forward...)
		cmd.Dir = cfg.Root
		cmd.Stdout = witness
		cmd.Stderr = os.Stderr
		cmd.Stdin = os.Stdin
		if err := cmd.Run(); err != nil {
			return err
		}
		if !witness.saw {
			return fmt.Errorf("%s preflight exited 0 without printing anything — indeterminate, not a pass", arcanePipelineScript)
		}
		return nil
	}

	// arcane_pipeline.py is not here yet. Run the read-only half of provisioning
	// rather than pretending the check happened.
	registry := arcane.Load(cfg.Root)
	profile, err := registry.WithLayout(*profileName, *layout)
	if err != nil {
		return err
	}
	silicon := arcaneProbeSilicon()
	link := arcaneProbeInterconnect()
	python := arcaneProbePython(interp)
	budget := arcaneProbeBudget(cfg, interp, profile.Name)
	arcaneApplyBudget(&profile, budget)
	stages := []arcaneStageResult{
		arcaneStageSilicon(silicon),
		arcaneStageInterconnect(profile, link),
		arcaneStageBudget(profile, budget),
		arcaneStageFit(profile, silicon, budget),
		arcaneStageRuntime(cfg, profile, interp, python),
		arcaneStageWeights(cfg, true),
		arcaneStageTenants(cfg, profile),
		arcaneStagePipeline(cfg, registry),
	}

	blocking := 0
	for _, stage := range stages {
		for _, probe := range stage.Probes {
			if probe.Blocking {
				blocking++
			}
		}
	}

	if *asJSON {
		return arcaneEmitJSON(map[string]any{
			"profile":      profile.Name,
			"delegated":    false,
			"reason":       script + " not present",
			"blocking":     blocking,
			"clear":        blocking == 0,
			"interpreter":  interp,
			"interconnect": link,
			"budget":       budget,
			"silicon":      silicon,
			"stages":       stages,
			"host":         runtime.GOOS + "/" + runtime.GOARCH,
			"kernel":       arcane.KernelNotes(profile.SM),
			"source":       registry.Source,
			"source_dir":   cfg.Root,
		})
	}

	ui.Header("arcane preflight", "local probes · "+profile.Name)
	ui.Field(arcanePipelineScript, "unavailable", script+" is not present — running the local read-only preflight instead")
	for _, stage := range stages {
		stage.render()
	}
	ui.Section("verdict", "read-only; nothing was changed", ui.Violet)
	if blocking == 0 {
		ui.Field("result", "ok", "no blocking finding for profile "+profile.Name)
	} else {
		ui.Field("result", "fail", fmt.Sprintf("%d blocking finding(s) for profile %s", blocking, profile.Name))
	}
	fmt.Println()
	if blocking > 0 {
		return fmt.Errorf("arcane preflight: %d blocking finding(s)", blocking)
	}
	return nil
}

func arcanePipelineDelegate(cfg config.Config, verb string, args []string) error {
	script := filepath.Join(cfg.Root, arcanePipelineScript)
	if info, err := os.Stat(script); err != nil || info.IsDir() {
		ui.Header("arcane "+verb, "delegates to "+arcanePipelineScript)
		ui.Field(arcanePipelineScript, "unavailable", script+" is not present")
		fmt.Println()
		return fmt.Errorf("%s not found at %s", arcanePipelineScript, script)
	}
	interp := arcaneResolvePython(cfg)
	if !interp.OK {
		ui.Header("arcane "+verb, "delegates to "+arcanePipelineScript)
		ui.Field("interpreter", "fail", valueOr(interp.Path, "(none)")+": "+interp.Reason)
		fmt.Println()
		return fmt.Errorf("arcane %s: no working Python interpreter; %s was not run", verb, arcanePipelineScript)
	}
	witness := &arcaneWitness{sink: os.Stdout}
	cmd := exec.Command(interp.Path, append([]string{script, verb}, args...)...)
	cmd.Dir = cfg.Root
	cmd.Stdout = witness
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	if err := cmd.Run(); err != nil {
		return err
	}
	if !witness.saw {
		return fmt.Errorf("%s %s exited 0 without printing anything — indeterminate, not a pass", arcanePipelineScript, verb)
	}
	return nil
}

// ---------------------------------------------------------------------------
// probes
// ---------------------------------------------------------------------------

// arcaneProbeSilicon asks nvidia-smi and reports exactly what came back. On a
// host with no nvidia-smi it reports absence — it does not guess.
func arcaneProbeSilicon() arcaneSilicon {
	result := arcaneSilicon{}
	binary, err := exec.LookPath("nvidia-smi")
	if err != nil {
		result.ProbeErr = "nvidia-smi is not on PATH"
		return result
	}

	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, binary,
		"--query-gpu=name,memory.total,compute_cap,driver_version",
		"--format=csv,noheader,nounits").Output()
	if err != nil {
		result.ProbeErr = "nvidia-smi failed: " + err.Error()
		return result
	}

	lines := strings.Split(strings.TrimSpace(string(out)), "\n")
	for _, line := range lines {
		if strings.TrimSpace(line) == "" {
			continue
		}
		fields := strings.Split(line, ",")
		if len(fields) < 4 {
			continue
		}
		result.Count++
		if result.Present {
			continue
		}
		result.Present = true
		result.Name = strings.TrimSpace(fields[0])
		if mib, err := strconv.ParseFloat(strings.TrimSpace(fields[1]), 64); err == nil {
			result.VRAMGiB = mib / 1024
		}
		result.Cap = strings.TrimSpace(fields[2])
		result.SM = arcane.SMFromComputeCapability(result.Cap)
		result.Driver = strings.TrimSpace(fields[3])
	}
	if !result.Present {
		result.ProbeErr = "nvidia-smi returned no device rows"
	}
	return result
}

// arcaneInterconnect is what the driver says about GPU-to-GPU links. Public
// specs for RTX PRO 6000 Blackwell say the board has no NVLink; the operator
// reports NVLink on their machine. Rather than encode either belief, this asks
// the driver and reports declared against detected.
type arcaneInterconnect struct {
	Probed   bool   `json:"probed"`
	Detected string `json:"detected"`
	Detail   string `json:"detail,omitempty"`
	Links    int    `json:"active_links,omitempty"`
	Topology string `json:"topology,omitempty"`
}

// arcaneProbeInterconnect runs `nvidia-smi nvlink --status` and `nvidia-smi
// topo -m`. Either can be absent or unsupported; neither absence is reported
// as a pass.
func arcaneProbeInterconnect() arcaneInterconnect {
	result := arcaneInterconnect{Detected: "unknown"}
	binary, err := exec.LookPath("nvidia-smi")
	if err != nil {
		result.Detail = "nvidia-smi not present — interconnect undetermined"
		return result
	}
	result.Probed = true

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	nvlinkOut, nvlinkErr := exec.CommandContext(ctx, binary, "nvlink", "--status").CombinedOutput()
	nvlinkText := strings.TrimSpace(string(nvlinkOut))
	lowered := strings.ToLower(nvlinkText)
	switch {
	case nvlinkErr != nil && nvlinkText == "":
		result.Detail = "nvidia-smi nvlink --status failed: " + nvlinkErr.Error()
	case strings.Contains(lowered, "not supported"), strings.Contains(lowered, "inactive"):
		result.Detected = "pcie"
		result.Detail = "nvidia-smi reports NVLink not supported or inactive on this board"
	default:
		for _, line := range strings.Split(nvlinkText, "\n") {
			if strings.Contains(strings.ToLower(line), "link ") && strings.Contains(line, "GB/s") {
				result.Links++
			}
		}
		if result.Links > 0 {
			result.Detected = "nvlink"
			result.Detail = fmt.Sprintf("%d active NVLink link(s) reported by nvidia-smi", result.Links)
		}
	}

	topoOut, topoErr := exec.CommandContext(ctx, binary, "topo", "-m").CombinedOutput()
	if topoErr == nil {
		result.Topology = arcaneTopologySummary(string(topoOut))
		if strings.Contains(result.Topology, "NVLink") && result.Detected != "nvlink" {
			result.Detected = "nvlink"
			result.Detail = strings.TrimSpace(result.Detail + " · topo -m shows NV# links between GPUs")
		}
		if result.Detected == "unknown" && result.Topology != "" {
			result.Detected = "pcie"
			result.Detail = "topo -m shows only PCIe paths (" + result.Topology + ")"
		}
	}
	if result.Detected == "unknown" && result.Detail == "" {
		result.Detail = "nvidia-smi ran but reported no usable link information"
	}
	return result
}

// arcaneTopologySummary reduces the `nvidia-smi topo -m` matrix to the distinct
// link classes it contains: NV# is NVLink, PIX/PXB/PHB/NODE/SYS are PCIe paths.
func arcaneTopologySummary(matrix string) string {
	classes := map[string]bool{}
	for _, line := range strings.Split(matrix, "\n") {
		if !strings.HasPrefix(strings.TrimSpace(line), "GPU") {
			continue
		}
		for _, field := range strings.Fields(line) {
			switch {
			case strings.HasPrefix(field, "NV") && len(field) > 2:
				classes["NVLink ("+field+")"] = true
			case field == "PIX", field == "PXB", field == "PHB", field == "NODE", field == "SYS":
				classes["PCIe ("+field+")"] = true
			}
		}
	}
	if len(classes) == 0 {
		return ""
	}
	out := make([]string, 0, len(classes))
	for class := range classes {
		out = append(out, class)
	}
	sort.Strings(out)
	return strings.Join(out, ", ")
}

// arcaneStageInterconnect compares what the profile declares against what the
// driver reports, and refuses to call either one green on its own.
func arcaneStageInterconnect(profile arcane.Profile, link arcaneInterconnect) arcaneStageResult {
	stage := arcaneStageResult{Name: "interconnect", Detail: "declared topology against detected topology", Color: ui.Gold}

	declared := valueOr(profile.Interconnect, "not declared")
	stage.add("declared", "ok", declared+fmt.Sprintf(" · %d card(s) · tensor parallel %s", profile.CardCount(), arcaneTPWord(profile.TPViable)))

	if !link.Probed {
		stage.add("detected", "unknown", link.Detail)
		stage.add("agreement", "unknown", "nothing detected to compare against the declared "+declared)
		arcaneTensorParallelGuard(&stage, profile, "unknown")
		return stage
	}

	detail := link.Detail
	if link.Topology != "" {
		detail = strings.TrimSpace(detail + " · topo: " + link.Topology)
	}
	switch link.Detected {
	case "nvlink", "pcie":
		stage.add("detected", "ok", link.Detected+" · "+detail)
		declaredKind := arcaneLinkKind(profile.Interconnect)
		switch {
		case declaredKind == "":
			stage.add("agreement", "warn", "profile declares no interconnect; the driver reports "+link.Detected)
		case declaredKind == link.Detected:
			stage.add("agreement", "ok", "declared "+declared+" matches detected "+link.Detected)
		default:
			stage.add("agreement", "warn", "MISMATCH: profile declares "+declared+" but the driver reports "+link.Detected+" — the placement was budgeted against the declared fabric")
		}
	default:
		stage.add("detected", "unknown", detail)
		stage.add("agreement", "unknown", "driver gave no usable link information")
	}
	arcaneTensorParallelGuard(&stage, profile, link.Detected)
	return stage
}

func arcaneTPWord(viable bool) string {
	if viable {
		return "viable"
	}
	return "impractical"
}

func arcaneLinkKind(declared string) string {
	lowered := strings.ToLower(declared)
	switch {
	case strings.Contains(lowered, "nvlink"):
		return "nvlink"
	case strings.Contains(lowered, "pcie"):
		return "pcie"
	}
	return ""
}

// arcaneTensorParallelGuard refuses to let a tensor-parallel placement pass
// unremarked on a fabric that cannot carry it. PCIe Gen5 x16 is roughly
// 64 GB/s against NVLink 5's 900 GB/s; TP over that is impractical, so a
// tp > 1 tenant on a detected-PCIe host is a misconfiguration, not a detail.
func arcaneTensorParallelGuard(stage *arcaneStageResult, profile arcane.Profile, detected string) {
	var sharded []string
	for _, seat := range profile.Tenants {
		if seat.TensorParallel > 1 {
			sharded = append(sharded, fmt.Sprintf("%s (tp=%d on gpu %d)", seat.Role, seat.TensorParallel, seat.GPU))
		}
	}
	if len(sharded) == 0 {
		stage.add("tensor parallel", "ok", "every tenant runs tensor_parallel = 1; no tenant depends on the fabric for capacity")
		return
	}
	switch detected {
	case "nvlink":
		stage.add("tensor parallel", "ok", "detected NVLink carries "+strings.Join(sharded, ", "))
	case "pcie":
		stage.add("tensor parallel", "warn", "MISCONFIGURATION: "+strings.Join(sharded, ", ")+" needs a high-bandwidth fabric, but only PCIe was detected (~64 GB/s against NVLink 5's 900 GB/s) — this will run, badly")
	default:
		stage.add("tensor parallel", "unknown", strings.Join(sharded, ", ")+" declared, but the fabric could not be detected to confirm it")
	}
}

func arcaneEmitJSON(payload any) error {
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	return encoder.Encode(payload)
}

// ---------------------------------------------------------------------------
// character / latent / scenes
//
// Three modes with genuinely opposite objective functions, which is why they
// are three commands and not one with a flag:
//
//   character  identity coherence across a rotation orbit. The novelty gate is
//              INVERTED — adjacent frames SHOULD match — and the cross-frame
//              residual cache is load-bearing, because residual persistence is
//              what holds identity constant between views.
//   latent     novelty and exploration. Novelty gate as built; the cache is a
//              pure speedup and nothing depends on it.
//   scenes     composition and world (Piltover against Zaun). Mild novelty
//              gate; the cache is a speedup and prompt variation is expected.
//
// That difference is what makes the draft validator below matter rather than
// being pedantry: worker.py clears the xframe residual cache whenever the
// prompt text changes between cells, so a draft carrying per-view prompts
// flushes the cache once per view. In `scenes` that is the deliberate point. In
// `character` it destroys the exact mechanism the mode depends on.
// ---------------------------------------------------------------------------

const (
	arcaneModeCharacter = "character"
	arcaneModeLatent    = "latent"
	arcaneModeScenes    = "scenes"
)

const arcaneDraftDir = "atlas_drafts"

// arcaneDraft is the subset of a draft JSON that decides which modes it suits.
type arcaneDraft struct {
	Name          string   `json:"name"`
	Path          string   `json:"path"`
	ID            string   `json:"id"`
	Label         string   `json:"label"`
	Subject       string   `json:"subject"`
	ViewPrompts   int      `json:"view_prompts"`
	SeedLock      float64  `json:"seed_lock"`
	ShellCoupling float64  `json:"shell_coupling"`
	ShellScale    float64  `json:"shell_scale"`
	Cells         int      `json:"cells,omitempty"`
	Modes         []string `json:"modes"`
	Warning       string   `json:"warning,omitempty"`
}

// arcaneClassifyDraft decides which modes a draft is valid for, from fields
// that are all present in the draft JSON. The rules are stated here rather than
// hidden, and they are heuristics over measured geometry — not an oracle:
//
//	character  needs an unbroken residual cache (view_prompts == 0) and shells
//	           coupled tightly enough to carry identity between frames.
//	latent     wants loose shells (exploration), or the deliberately wide
//	           prompt-varied sampling of a high-coupling scout sweep.
//	scenes     wants prompt variation, or a locked seed under tight coupling so
//	           the world stays put while the composition moves.
func arcaneClassifyDraft(draft *arcaneDraft) {
	draft.Modes = nil

	if draft.ViewPrompts == 0 && draft.ShellCoupling >= 0.6 {
		draft.Modes = append(draft.Modes, arcaneModeCharacter)
	}
	if draft.ShellCoupling <= 0.4 || (draft.ViewPrompts > 0 && draft.ShellCoupling >= 0.9) {
		draft.Modes = append(draft.Modes, arcaneModeLatent)
	}
	if draft.ViewPrompts > 0 || (draft.SeedLock >= 0.4 && draft.ShellCoupling >= 0.7) {
		draft.Modes = append(draft.Modes, arcaneModeScenes)
	}
	if draft.ViewPrompts > 0 {
		draft.Warning = fmt.Sprintf("%d view_prompts flush the cross-frame residual cache %d times per orbit (worker.py clears it when prompt text changes between cells) — deliberate in scenes, fatal in character",
			draft.ViewPrompts, draft.ViewPrompts)
	}
}

// arcaneValidFor reports whether a draft is usable in a mode.
func arcaneValidFor(draft arcaneDraft, mode string) bool {
	for _, candidate := range draft.Modes {
		if candidate == mode {
			return true
		}
	}
	return false
}

func arcaneLoadDrafts(root string) ([]arcaneDraft, error) {
	dir := filepath.Join(root, arcaneDraftDir)
	entries, err := filepath.Glob(filepath.Join(dir, "*.json"))
	if err != nil {
		return nil, err
	}
	drafts := make([]arcaneDraft, 0, len(entries))
	for _, path := range entries {
		draft, err := arcaneReadDraft(path)
		if err != nil {
			continue
		}
		drafts = append(drafts, draft)
	}
	sort.Slice(drafts, func(i, j int) bool { return drafts[i].Name < drafts[j].Name })
	return drafts, nil
}

func arcaneReadDraft(path string) (arcaneDraft, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return arcaneDraft{}, err
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		return arcaneDraft{}, err
	}
	draft := arcaneDraft{
		Name: strings.TrimSuffix(filepath.Base(path), ".json"),
		Path: path,
	}
	draft.ID, _ = doc["id"].(string)
	draft.Label, _ = doc["label"].(string)
	draft.Subject, _ = doc["subject"].(string)
	if views, ok := doc["view_prompts"].([]any); ok {
		draft.ViewPrompts = len(views)
	}
	draft.SeedLock = arcaneFloat(doc, "seed_lock")
	draft.ShellCoupling = arcaneFloat(doc, "shell_coupling")
	draft.ShellScale = arcaneFloat(doc, "shell_scale")
	draft.Cells = int(arcaneFloat(doc, "n_cells"))
	arcaneClassifyDraft(&draft)
	return draft, nil
}

func arcaneFloat(doc map[string]any, key string) float64 {
	if v, ok := doc[key].(float64); ok {
		return v
	}
	return 0
}

// arcaneResolveDraft accepts a path, a file name, or any distinctive fragment
// of one, and refuses an ambiguous fragment rather than picking for you.
func arcaneResolveDraft(root, wanted string) (arcaneDraft, error) {
	if wanted == "" {
		return arcaneDraft{}, nil
	}
	if strings.ContainsRune(wanted, filepath.Separator) || strings.HasSuffix(wanted, ".json") {
		if _, err := os.Stat(wanted); err == nil {
			return arcaneReadDraft(wanted)
		}
	}
	drafts, err := arcaneLoadDrafts(root)
	if err != nil {
		return arcaneDraft{}, err
	}
	needle := strings.ToLower(wanted)
	var matches []arcaneDraft
	for _, draft := range drafts {
		if strings.EqualFold(draft.Name, wanted) {
			return draft, nil
		}
		if strings.Contains(strings.ToLower(draft.Name), needle) {
			matches = append(matches, draft)
		}
	}
	switch len(matches) {
	case 0:
		return arcaneDraft{}, fmt.Errorf("no draft in %s matches %q; run `flux arcane drafts`", arcaneDraftDir, wanted)
	case 1:
		return matches[0], nil
	}
	names := make([]string, 0, len(matches))
	for _, match := range matches {
		names = append(names, match.Name)
	}
	return arcaneDraft{}, fmt.Errorf("%q matches %d drafts: %s", wanted, len(matches), strings.Join(names, ", "))
}

func arcaneDrafts(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("arcane drafts", flag.ExitOnError)
	mode := fs.String("mode", "", "show only drafts valid for a `mode`: character, latent, scenes")
	asJSON := fs.Bool("json", false, "emit the draft table as JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}

	drafts, err := arcaneLoadDrafts(cfg.Root)
	if err != nil {
		return err
	}
	if *mode != "" {
		filtered := drafts[:0:0]
		for _, draft := range drafts {
			if arcaneValidFor(draft, strings.ToLower(*mode)) {
				filtered = append(filtered, draft)
			}
		}
		drafts = filtered
	}

	if *asJSON {
		return arcaneEmitJSON(map[string]any{
			"dir":    filepath.Join(cfg.Root, arcaneDraftDir),
			"mode":   *mode,
			"count":  len(drafts),
			"drafts": drafts,
		})
	}

	ui.Header("arcane drafts", "orbit geometry and which mode each draft is valid for")
	ui.KV("dir", ui.Soft(filepath.Join(cfg.Root, arcaneDraftDir)))
	ui.KV("count", ui.Soft(fmt.Sprintf("%d draft(s)", len(drafts))))

	ui.Section("geometry", "view_prompts is the field that decides character validity", ui.Teal)
	rows := make([][]string, 0, len(drafts))
	for _, draft := range drafts {
		views := ui.Soft("0")
		if draft.ViewPrompts > 0 {
			views = ui.Warn(fmt.Sprintf("%d", draft.ViewPrompts))
		}
		modes := ui.State("none")
		if len(draft.Modes) > 0 {
			modes = strings.Join(draft.Modes, ", ")
		}
		rows = append(rows, []string{
			draft.Name,
			views,
			ui.Soft(fmt.Sprintf("%.2f", draft.SeedLock)),
			ui.Soft(fmt.Sprintf("%.2f", draft.ShellCoupling)),
			ui.Soft(fmt.Sprintf("%.2f", draft.ShellScale)),
			modes,
		})
	}
	ui.Table([]ui.Column{
		{Title: "draft"},
		{Title: "view_prompts", Right: true},
		{Title: "seed_lock", Right: true},
		{Title: "shell_coupling", Right: true},
		{Title: "shell_scale", Right: true},
		{Title: "valid for"},
	}, rows)

	fmt.Println()
	ui.Tree("objectives", "why the same draft is not valid everywhere", []ui.TreeGroup{
		{Name: arcaneModeCharacter, Detail: "identity coherence across a rotation orbit", Color: ui.Mint, Children: []ui.PairRow{
			{Left: "novelty gate", Right: "INVERTED — adjacent frames should match"},
			{Left: "residual cache", Right: "LOAD-BEARING — residual persistence is what holds identity constant"},
			{Left: "requires", Right: "view_prompts = 0, shell_coupling >= 0.60"},
		}},
		{Name: arcaneModeLatent, Detail: "novelty and exploration", Color: ui.Teal, Children: []ui.PairRow{
			{Left: "novelty gate", Right: "as built"},
			{Left: "residual cache", Right: "pure speedup; nothing depends on it"},
			{Left: "requires", Right: "loose shells (coupling <= 0.40), or a wide prompt-varied scout sweep"},
		}},
		{Name: arcaneModeScenes, Detail: "composition and world, Piltover against Zaun", Color: ui.Gold, Children: []ui.PairRow{
			{Left: "novelty gate", Right: "mild"},
			{Left: "residual cache", Right: "speedup; prompt variation is expected"},
			{Left: "requires", Right: "view_prompts > 0, or a locked seed under tight coupling"},
		}},
	})

	var flushing []arcaneDraft
	for _, draft := range drafts {
		if draft.ViewPrompts > 0 {
			flushing = append(flushing, draft)
		}
	}
	if len(flushing) > 0 {
		fmt.Println()
		ui.Field("cache flush", "warn", fmt.Sprintf("%d draft(s) carry per-view prompts; worker.py clears the cross-frame residual cache whenever prompt text changes between cells", len(flushing)))
		for _, draft := range flushing {
			ui.Note(fmt.Sprintf("%s: %d flushes per orbit — fine in scenes, fatal in character", draft.Name, draft.ViewPrompts))
		}
	}
	fmt.Println()
	return nil
}

// arcaneMode runs one of the three pipeline modes. The draft is validated
// against the mode's objective before anything is launched.
func arcaneMode(cfg config.Config, mode string, args []string) error {
	fs := flag.NewFlagSet("arcane "+mode, flag.ExitOnError)
	draftName := fs.String("draft", "", "atlas `draft` to run: a name, a fragment of one, or a path")
	force := fs.Bool("force", false, "run even when the draft is not valid for this mode")
	dryRun := fs.Bool("dry-run", false, "validate and show the plan; launch nothing")
	asJSON := fs.Bool("json", false, "emit the plan as JSON")

	var views, cells *int
	var selection, character *string
	switch mode {
	case arcaneModeCharacter:
		views = fs.Int("views", 0, "views around the orbit (0 keeps the draft's own count)")
		// Undecided by the operator: exposed as a flag with a documented
		// default, deliberately without machinery behind either branch.
		selection = fs.String("select", "dense", "view selection: `dense` or discrete (undecided; forwarded as-is)")
	case arcaneModeLatent:
		cells = fs.Int("cells", 0, "cells to sample (0 keeps the draft's own count)")
	case arcaneModeScenes:
		// Undecided by the operator: forwarded verbatim.
		character = fs.String("character", "none", "character orbit to compose against: `none` or an orbit id")
	}
	if err := fs.Parse(args); err != nil {
		return err
	}

	draft, err := arcaneResolveDraft(cfg.Root, *draftName)
	if err != nil {
		return err
	}

	ui.Header("arcane "+mode, arcaneModeSubtitle(mode))
	ui.KV("objective", ui.Soft(arcaneModeObjective(mode)))
	ui.KV("novelty gate", ui.Soft(arcaneModeGate(mode)))
	ui.KV("residual cache", ui.Soft(arcaneModeCache(mode)))

	blocked := false
	if draft.Name != "" {
		ui.Section("draft", draft.Path, ui.Lilac)
		ui.Field("name", "ok", draft.Name)
		ui.Field("geometry", "ok", fmt.Sprintf("view_prompts=%d · seed_lock=%.2f · shell_coupling=%.2f · shell_scale=%.2f",
			draft.ViewPrompts, draft.SeedLock, draft.ShellCoupling, draft.ShellScale))
		ui.Field("valid for", "ok", valueOr(strings.Join(draft.Modes, ", "), "no mode"))

		switch {
		case arcaneValidFor(draft, mode):
			ui.Field("validation", "ok", draft.Name+" is valid for "+mode)
		case mode == arcaneModeCharacter && draft.ViewPrompts > 0:
			blocked = true
			ui.Field("validation", "fail", draft.Warning)
			ui.Note("character depends on the residual cache surviving the whole orbit; a draft that flushes it cannot hold identity")
			ui.Note("use a view_prompts=0 draft (`flux arcane drafts --mode character`), or pass --force to run it anyway")
		default:
			blocked = true
			ui.Field("validation", "fail", draft.Name+" is not valid for "+mode+" (valid for: "+valueOr(strings.Join(draft.Modes, ", "), "no mode")+")")
			ui.Note("pass --force to run it anyway")
		}
	} else {
		ui.Section("draft", "none supplied", ui.Lilac)
		ui.Field("draft", "skip", "no --draft; "+arcanePipelineScript+" selects its own default")
	}

	if blocked && !*force {
		fmt.Println()
		return fmt.Errorf("arcane %s: draft %q is not valid for this mode; pass --force to override", mode, draft.Name)
	}
	if blocked {
		ui.Field("override", "warn", "--force given: running a draft this mode's objective does not support")
	}

	forward := []string{mode}
	if draft.Path != "" {
		forward = append(forward, "--draft", draft.Path)
	}
	if views != nil && *views > 0 {
		forward = append(forward, "--views", strconv.Itoa(*views))
	}
	if cells != nil && *cells > 0 {
		forward = append(forward, "--cells", strconv.Itoa(*cells))
	}
	if selection != nil && *selection != "" {
		forward = append(forward, "--select", *selection)
	}
	if character != nil && *character != "" {
		forward = append(forward, "--character", *character)
	}
	if *dryRun {
		forward = append(forward, "--dry-run")
	}
	if *asJSON {
		forward = append(forward, "--json")
	}
	forward = append(forward, fs.Args()...)

	script := filepath.Join(cfg.Root, arcanePipelineScript)
	if info, err := os.Stat(script); err != nil || info.IsDir() {
		ui.Section("delegate", arcanePipelineScript, ui.Rose)
		ui.Field(arcanePipelineScript, "unavailable", script+" is not present")

		// Fall back to the legacy preset for the one mode that had one, so the
		// command that already worked keeps working until the pipeline lands.
		if mode == arcaneModeCharacter && len(fs.Args()) > 0 {
			ui.Note("falling back to the legacy `render --preset arcane-turn` path for this prompt")
			fmt.Println()
			return render(cfg, []string{"--preset", "arcane-turn", strings.Join(fs.Args(), " ")})
		}
		fmt.Println()
		return fmt.Errorf("%s not found at %s", arcanePipelineScript, script)
	}

	ui.Section("delegate", strings.Join(append([]string{arcanePipelineScript}, forward...), " "), ui.Rose)
	if *dryRun {
		ui.Field("dry run", "ok", "validated; "+arcanePipelineScript+" still runs its own --dry-run plan below")
	}
	fmt.Println()
	return arcanePipelineDelegate(cfg, forward[0], forward[1:])
}

func arcaneModeSubtitle(mode string) string {
	switch mode {
	case arcaneModeCharacter:
		return "identity coherence across a rotation orbit"
	case arcaneModeLatent:
		return "novelty and exploration across the latent shell"
	case arcaneModeScenes:
		return "composition and world — Piltover against Zaun"
	}
	return mode
}

func arcaneModeObjective(mode string) string {
	switch mode {
	case arcaneModeCharacter:
		return "hold one character's identity constant while the camera moves"
	case arcaneModeLatent:
		return "maximise distance between samples"
	case arcaneModeScenes:
		return "vary composition and world while the subject stays legible"
	}
	return ""
}

func arcaneModeGate(mode string) string {
	switch mode {
	case arcaneModeCharacter:
		return "INVERTED — adjacent frames should match, not differ"
	case arcaneModeLatent:
		return "as built — novelty is the objective"
	case arcaneModeScenes:
		return "mild — prompt variation is expected"
	}
	return ""
}

func arcaneModeCache(mode string) string {
	switch mode {
	case arcaneModeCharacter:
		return "LOAD-BEARING — residual persistence is the continuity mechanism"
	case arcaneModeLatent:
		return "pure speedup; nothing depends on it"
	case arcaneModeScenes:
		return "speedup; prompt variation flushes it by design"
	}
	return ""
}

// ---------------------------------------------------------------------------
// VRAM budget
//
// pipeline_paths.vram_budget() is the authority on capacity, and this CLI defers
// to it rather than re-deriving the arithmetic.
//
// The distinction it enforces is the one that matters: a tenant's WEIGHTS are
// the checkpoint, its RESERVATION is gpu_memory_utilization × card ×
// tensor_parallel — weights plus the KV cache and the activation arena. Summing
// weights and calling the result a VRAM budget is what made every earlier budget
// in this repo disagree with the hardware. On rtx-pro-6000 the gap is 5.6 GiB:
// weights say 88.1/96 with 5.4 GiB of headroom over the reserve, the reservation
// says 93.7/96 with 0.32 GiB — 0.33% of the card. The first reads comfortable.
// The second is a posture that boots and then OOMs on the first KV growth.
//
// So: config reservations win over roster weights everywhere, and where this
// probe can run, its figures win over ours.
// ---------------------------------------------------------------------------

// arcaneBudget mirrors the subset of pipeline_paths.vram_budget()'s return that
// this CLI renders. Absent fields stay zero and are reported as unavailable.
type arcaneBudget struct {
	Available bool    `json:"available"`
	Source    string  `json:"source"`
	Reason    string  `json:"reason,omitempty"`
	Profile   string  `json:"profile,omitempty"`
	Layout    string  `json:"layout,omitempty"`
	TotalGiB  float64 `json:"total_gib"`
	PerGPUGiB float64 `json:"vram_per_gpu_gib"`
	ReserveG  float64 `json:"reserve_gib"`
	UsableGiB float64 `json:"usable_gib"`
	Allocated float64 `json:"allocated_gib"`
	Weights   float64 `json:"weights_gib"`
	FreeGiB   float64 `json:"free_gib"`
	Headroom  float64 `json:"headroom_gib"`
	Fits      bool    `json:"fits"`
	Reason2   string  `json:"overcommit_reason,omitempty"`
	PerGPU    []struct {
		GPU       int      `json:"gpu"`
		TotalGiB  float64  `json:"total_gib"`
		UsableGiB float64  `json:"usable_gib"`
		Allocated float64  `json:"allocated_gib"`
		FreeGiB   float64  `json:"free_gib"`
		Headroom  float64  `json:"headroom_gib"`
		Fits      bool     `json:"fits"`
		Tenants   []string `json:"tenants"`
	} `json:"per_gpu"`
	Warnings []string `json:"warnings,omitempty"`
	Notes    []string `json:"notes,omitempty"`
}

// Gap is how much more the reservation claims than the weights do. It is the
// KV cache and activation arena, and it is the whole reason weights must not be
// used as a budget.
func (b arcaneBudget) Gap() float64 { return b.Allocated - b.Weights }

// Critical reports a margin so thin it is a rounding error rather than headroom.
// pipeline_paths classifies this as its own tier for a reason: the posture boots
// and then dies on the first KV cache growth.
func (b arcaneBudget) Critical() bool {
	if !b.Available || !b.Fits {
		return false
	}
	for _, card := range b.PerGPU {
		if card.Headroom < 1.0 {
			return true
		}
	}
	return b.Headroom < 1.0
}

// BindingHeadroom is the headroom that actually constrains the deployment: the
// tightest card, never the sum across cards. On a multi-GPU profile the
// aggregate is a SUM, not a pool -- reporting it as headroom reads reassuring
// while one card sits a rounding error from OOM. Returns the value and the GPU
// index it came from; index is -1 on a single-card or per-GPU-less budget.
func (b arcaneBudget) BindingHeadroom() (float64, int) {
	if len(b.PerGPU) == 0 {
		return b.Headroom, -1
	}
	tightest, gpu := b.PerGPU[0].Headroom, b.PerGPU[0].GPU
	for _, card := range b.PerGPU[1:] {
		if card.Headroom < tightest {
			tightest, gpu = card.Headroom, card.GPU
		}
	}
	if len(b.PerGPU) == 1 {
		return tightest, -1
	}
	return tightest, gpu
}

// BindingPhrase renders the binding headroom, naming the card when more than one
// exists so the number can never be mistaken for a pool.
func (b arcaneBudget) BindingPhrase() string {
	headroom, gpu := b.BindingHeadroom()
	if gpu < 0 {
		return fmt.Sprintf("%.2f GiB of headroom over the reserve", headroom)
	}
	return fmt.Sprintf("%.2f GiB of headroom on gpu %d, the tightest card (the %.2f GiB aggregate is a SUM across %d cards, not a pool)",
		headroom, gpu, b.Headroom, len(b.PerGPU))
}

const arcaneBudgetProbe = `
import json, sys
sys.path.insert(0, %s)
import pipeline_paths
out = pipeline_paths.vram_budget(%s)
sys.stdout.write(json.dumps(out, default=str))
`

// arcaneProbeBudget asks pipeline_paths for the authoritative budget. Every
// failure path returns Available=false with the reason — the caller then falls
// back to its own arithmetic and says which one it used.
func arcaneProbeBudget(cfg config.Config, interp arcaneInterpreter, profileName string) arcaneBudget {
	budget := arcaneBudget{Source: "pipeline_paths.vram_budget()"}
	if !interp.OK {
		budget.Reason = "no working Python interpreter: " + interp.Reason
		return budget
	}
	if _, err := os.Stat(filepath.Join(cfg.Root, "pipeline_paths.py")); err != nil {
		budget.Reason = "pipeline_paths.py is not present"
		return budget
	}

	script := fmt.Sprintf(arcaneBudgetProbe, strconv.Quote(cfg.Root), strconv.Quote(profileName))
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	cmd := exec.CommandContext(ctx, interp.Path, "-c", script)
	cmd.Dir = cfg.Root
	out, err := cmd.Output()
	if err != nil {
		budget.Reason = "vram_budget() failed: " + arcaneErrDetail(err)
		return budget
	}
	if strings.TrimSpace(string(out)) == "" {
		budget.Reason = "vram_budget() exited 0 and printed nothing"
		return budget
	}
	if err := json.Unmarshal(out, &budget); err != nil {
		budget.Reason = "vram_budget() returned unparseable output: " + err.Error()
		return budget
	}
	budget.Available = true
	budget.Source = "pipeline_paths.vram_budget()"
	if budget.Reason2 != "" && budget.Reason == "" {
		budget.Reason = budget.Reason2
	}
	return budget
}

func arcaneErrDetail(err error) string {
	var exitErr *exec.ExitError
	if asExitError(err, &exitErr) {
		detail := strings.TrimSpace(string(exitErr.Stderr))
		if detail != "" {
			return firstLine(detail)
		}
	}
	return err.Error()
}

// arcaneApplyBudget folds the authoritative figures onto a profile: capacity,
// reserve, and per-seat reservations matched by role. Anything the probe does
// not cover is left as the config's own arithmetic.
func arcaneApplyBudget(profile *arcane.Profile, budget arcaneBudget) {
	if !budget.Available {
		return
	}
	if budget.PerGPUGiB > 0 {
		profile.VRAMGiB = budget.PerGPUGiB
	}
	if len(budget.PerGPU) > 0 {
		profile.GPUs = len(budget.PerGPU)
		if budget.PerGPU[0].TotalGiB > 0 {
			profile.VRAMGiB = budget.PerGPU[0].TotalGiB
		}
		profile.ReserveGiB = arcaneRound1(budget.PerGPU[0].TotalGiB - budget.PerGPU[0].UsableGiB)
	}
}

func arcaneRound1(v float64) float64 {
	return float64(int(v*10+0.5)) / 10
}

// arcaneBudgetBlock prints the capacity verdict. When the authoritative probe
// ran, its numbers are the ones shown; when it did not, the fallback says so in
// the same breath rather than presenting local arithmetic as measurement.
func arcaneBudgetBlock(profile arcane.Profile, budget arcaneBudget) {
	weights := profile.WeightsCommitted()

	if !budget.Available {
		for _, card := range profile.Cards() {
			label := "committed"
			if profile.Multi() {
				label = fmt.Sprintf("gpu %d", card.Index)
			}
			ui.Capacity(label, card.Committed, card.Capacity, "GiB")
		}
		if profile.ReservationBacked() {
			ui.Note(fmt.Sprintf("reservations from %s; %s did not run (%s), so these totals are this CLI's own arithmetic over the same figures",
				arcane.ContinuumFile, budget.Source, valueOr(budget.Reason, "unavailable")))
		} else {
			ui.Field("budget", "warn", "showing ROSTER WEIGHTS, not reservations — this omits the KV cache and activation arena and reads more comfortable than the card is. "+valueOr(budget.Reason, "the authoritative budget was unavailable"))
		}
		arcaneWeightsGapNote(weights, profile.Committed())
		return
	}

	for _, card := range budget.PerGPU {
		label := "committed"
		if len(budget.PerGPU) > 1 {
			label = fmt.Sprintf("gpu %d", card.GPU)
		}
		ui.Capacity(label, card.Allocated, card.TotalGiB, "GiB")
		ui.Note(fmt.Sprintf("gpu %d: %.2f GiB reserved against %.2f GiB usable after the %.2f GiB reserve — %.2f GiB of headroom (%.2f%% of the card)",
			card.GPU, card.Allocated, card.UsableGiB, arcaneRound1(card.TotalGiB-card.UsableGiB), card.Headroom,
			100*card.Headroom/max1(card.TotalGiB)))
	}

	ui.Field("budget source", "ok", budget.Source+" — reservations, not weights")
	if budget.Weights > 0 && budget.Gap() > 0.05 {
		ui.Field("weights gap", "warn", fmt.Sprintf(
			"weights %.2f GiB · reserved %.2f GiB · the %.2f GiB difference is KV cache and activation arena. Capacity is decided on the reservation",
			budget.Weights, budget.Allocated, budget.Gap()))
	}

	switch {
	case !budget.Fits:
		ui.Field("fits", "fail", valueOr(budget.Reason, "overcommitted"))
	case budget.Critical():
		ui.Field("fits", "warn", "CRITICAL — fits by "+budget.BindingPhrase()+", which is a rounding error and not a margin. It boots, then OOMs on the first KV cache growth")
	default:
		ui.Field("fits", "ok", "per-GPU, with "+budget.BindingPhrase())
	}

	for _, warning := range budget.Warnings {
		ui.Note(warning)
	}
}

func max1(v float64) float64 {
	if v <= 0 {
		return 1
	}
	return v
}

func arcaneWeightsGapNote(weights, reserved float64) {
	if weights <= 0 || reserved-weights <= 0.05 {
		return
	}
	ui.Note(fmt.Sprintf("weights %.1f GiB · reserved %.1f GiB — the %.1f GiB difference is KV cache and activation arena, and it is why weights must not be used as a budget",
		weights, reserved, reserved-weights))
}

// arcaneBudgets resolves the authoritative budget for each profile being shown.
// The interpreter is resolved once, and a profile whose probe fails simply gets
// an unavailable budget with its reason — never a substituted guess.
func arcaneBudgets(cfg config.Config, profiles []arcane.Profile, skip bool) map[string]arcaneBudget {
	out := make(map[string]arcaneBudget, len(profiles))
	if skip {
		for _, profile := range profiles {
			out[profile.Name] = arcaneBudget{Source: "pipeline_paths.vram_budget()", Reason: "skipped with --no-python"}
		}
		return out
	}
	interp := arcaneResolvePython(cfg)
	for _, profile := range profiles {
		out[profile.Name] = arcaneProbeBudget(cfg, interp, profile.Name)
	}
	return out
}

// arcaneBudgetSummary states, once, which numbers the reader has just been
// shown and what the weights-versus-reservation gap is on each profile.
func arcaneBudgetSummary(profiles []arcane.Profile, budgets map[string]arcaneBudget) {
	fmt.Println()
	authoritative, fallback := 0, 0
	for _, profile := range profiles {
		if budgets[profile.Name].Available {
			authoritative++
		} else {
			fallback++
		}
	}
	switch {
	case fallback == 0:
		ui.Field("budget source", "ok", "pipeline_paths.vram_budget() — reservations (gpu_memory_utilization × card), not checkpoint weights")
	case authoritative == 0:
		ui.Field("budget source", "warn", "pipeline_paths.vram_budget() did not run; these are this CLI's own totals over the config's reservations")
	default:
		ui.Field("budget source", "warn", fmt.Sprintf("%d profile(s) from pipeline_paths.vram_budget(), %d from local arithmetic", authoritative, fallback))
	}

	for _, profile := range profiles {
		budget := budgets[profile.Name]
		if !budget.Available {
			if budget.Reason != "" {
				ui.Note(profile.Name + ": " + budget.Reason)
			}
			continue
		}
		if budget.Weights > 0 && budget.Gap() > 0.05 {
			ui.Note(fmt.Sprintf("%s: weights %.2f GiB · reserved %.2f GiB · KV cache and activation arena account for the %.2f GiB between them",
				profile.Name, budget.Weights, budget.Allocated, budget.Gap()))
		}
		if budget.Critical() {
			ui.Note(fmt.Sprintf("%s: CRITICAL — %s is a rounding error, not a margin", profile.Name, budget.BindingPhrase()))
		}
	}
}
