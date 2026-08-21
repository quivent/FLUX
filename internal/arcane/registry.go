package arcane

import (
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

// ContinuumFile is the config the registry reads, relative to the repo root.
const ContinuumFile = "jury_continuum.toml"

// Canonical roles, in the order the roster is always printed. The continuum
// file names the same seats after their implementations (flux, witness,
// pixtral); the CLI names them after their jobs.
const (
	RoleGenerator = "generator"
	RoleStructure = "structure"
	RoleGovernor  = "governor"
	RolePalette   = "palette"
	RoleGates     = "gates"
	RoleKontext   = "kontext"
)

// RoleOrder fixes the display order: what makes the picture, what reads it,
// what judges it, and last the one thing that can be switched off.
var RoleOrder = []string{RoleGenerator, RoleStructure, RoleGovernor, RolePalette, RoleGates, RoleKontext}

// roleFromConfig maps continuum tenant keys onto canonical roles.
var roleFromConfig = map[string]string{
	"flux":      RoleGenerator,
	"generator": RoleGenerator,
	"witness":   RoleStructure,
	"structure": RoleStructure,
	"qwen":      RoleStructure,
	"governor":  RoleGovernor,
	"pixtral":   RolePalette,
	"palette":   RolePalette,
	"gates":     RoleGates,
	"kontext":   RoleKontext,
}

// configFromRole is the reverse: the tenant key each role is stored under.
var configFromRole = map[string]string{
	RoleGenerator: "flux",
	RoleStructure: "witness",
	RoleGovernor:  "governor",
	RolePalette:   "pixtral",
	RoleGates:     "gates",
	RoleKontext:   "kontext",
}

// profileAlias resolves the names an operator might type, plus the historical
// names earlier revisions of the continuum file used for the same silicon.
var profileAlias = map[string]string{
	"rtx-pro-6000":    "rtx-pro-6000",
	"rtxpro6000":      "rtx-pro-6000",
	"rtx6000":         "rtx-pro-6000",
	"blackwell-96":    "rtx-pro-6000",
	"blackwell96":     "rtx-pro-6000",
	"blackwell":       "rtx-pro-6000",
	"pro6000":         "rtx-pro-6000",
	"default":         "rtx-pro-6000",
	"b200":            "b200",
	"b200-192":        "b200",
	"b300":            "b300",
	"b300-288":        "b300",
	"rtx-pro-6000-x4": "rtx-pro-6000-x4",
	"rtx-pro-6000x4":  "rtx-pro-6000-x4",
	"rtxpro6000x4":    "rtx-pro-6000-x4",
	"rtx6000x4":       "rtx-pro-6000-x4",
	"x4":              "rtx-pro-6000-x4",
	"4x":              "rtx-pro-6000-x4",
	"blackwell-96x4":  "rtx-pro-6000-x4",
	"cluster":         "rtx-pro-6000-x4",
}

// BannedModels must never appear in the roster again. Every one of them was
// live in an earlier revision of the continuum file or in provision_jury.sh.
// They are checked as data rather than remembered, so a config that reintroduces
// one is rejected out loud instead of quietly served to the operator.
var BannedModels = []string{
	"black-forest-labs/FLUX.1-schnell",
	"Qwen/Qwen2.5-VL-7B-Instruct",
	"Qwen/Qwen3-VL-8B-Instruct",
	"google/gemma-4-12b-it",
}

// IsBanned reports whether a model id is on the retired list.
func IsBanned(model string) bool {
	needle := strings.ToLower(strings.TrimSpace(model))
	if needle == "" {
		return false
	}
	for _, banned := range BannedModels {
		if strings.ToLower(banned) == needle {
			return true
		}
	}
	return false
}

// Where a displayed VRAM figure came from.
const (
	VRAMFromConfig = "config reservation"
	VRAMFromRoster = "roster weights"
)

// Tenant is one seat on the card.
type Tenant struct {
	Role      string `json:"role"`
	ConfigKey string `json:"config_key"`
	Duty      string `json:"duty"`
	Model     string `json:"model"`
	Precision string `json:"precision"`

	// VRAMGiB is what the tenant RESERVES: gpu_memory_utilization × card ×
	// tensor_parallel for a vLLM seat. This — not WeightsGiB — is the number
	// capacity is decided on, because it is the only one that includes the KV
	// cache and the activation arena.
	//
	// WeightsGiB is just the checkpoint. Summing weights and calling it a VRAM
	// budget is the mistake that made every prior budget in this repo
	// disagree with the hardware: on rtx-pro-6000 it under-reports by ~5.6 GiB,
	// which is the difference between "5.4 GiB free" and "0.32 GiB free".
	VRAMGiB    float64 `json:"vram_gib"`
	WeightsGiB float64 `json:"weights_gib,omitempty"`
	Kind       string  `json:"kind"`
	Port       int     `json:"port,omitempty"`
	Socket     string  `json:"socket,omitempty"`
	Served     string  `json:"served_name,omitempty"`
	GPUMemUtil float64 `json:"gpu_memory_utilization,omitempty"`
	Enabled    bool    `json:"enabled"`
	Mandatory  bool    `json:"mandatory"`
	Toggleable bool    `json:"toggleable"`
	Dense      bool    `json:"dense"`
	Remote     bool    `json:"remote,omitempty"`
	Note       string  `json:"note,omitempty"`

	// GPU is the card index this seat is placed on. Single-card profiles put
	// everything on 0. It is a placement, not a hint: on a cluster without
	// NVLink a seat cannot spill onto its neighbour, so this index is what
	// decides whether the profile fits.
	GPU   int    `json:"gpu"`
	Shard string `json:"shard,omitempty"`

	// TensorParallel is the degree this seat is sharded across cards. It is 1
	// everywhere except a deliberate tensor-parallel layout, and provisioning
	// flags anything above 1 when the detected interconnect cannot carry it.
	TensorParallel int `json:"tensor_parallel,omitempty"`

	// RosterVRAMGiB is the compiled roster's weights figure for this seat, kept
	// so a divergence from the config's reservation is reportable rather than
	// hidden. It is never used as a capacity number when config has one.
	RosterVRAMGiB float64 `json:"roster_weights_gib,omitempty"`

	// VRAMSource records which of the two the displayed VRAMGiB came from.
	VRAMSource string `json:"vram_source,omitempty"`
}

// State is the one-word posture of a seat: always-on, or a toggle and its
// position. The generator, both critics and the gates are never toggleable —
// Kontext is the only seat with an off switch.
func (t Tenant) State() string {
	switch {
	case t.Mandatory:
		return "always-on"
	case t.Enabled:
		return "toggle · on"
	default:
		return "toggle · off"
	}
}

// Endpoint describes where the seat is reached, for provisioning probes.
func (t Tenant) Endpoint() string {
	switch {
	case t.Remote:
		return "remote"
	case t.Port > 0:
		return fmt.Sprintf("127.0.0.1:%d", t.Port)
	case t.Socket != "":
		return ".fluxd/" + t.Socket
	case t.Kind == "inproc":
		return "in-process"
	}
	return ""
}

// Profile is one hardware posture: a card, its kernel family, and the roster
// that fits on it.
type Profile struct {
	Name       string  `json:"name"`
	Default    bool    `json:"default"`
	GPU        string  `json:"gpu"`
	MemoryKind string  `json:"memory_kind,omitempty"`
	Bandwidth  string  `json:"bandwidth,omitempty"`
	VRAMGiB    float64 `json:"vram_gib"`
	ReserveGiB float64 `json:"reserve_gib"`
	SM         string  `json:"sm"`

	// GPUs, Interconnect and TPViable describe the topology. They matter
	// because a multi-card profile is not a memory pool: without NVLink,
	// tensor parallelism is impractical and every model must fit one card.
	GPUs                 int      `json:"gpus"`
	Interconnect         string   `json:"interconnect,omitempty"`
	InterconnectVerified bool     `json:"interconnect_verified"`
	InterconnectDetected string   `json:"interconnect_detected,omitempty"`
	TPViable             bool     `json:"tensor_parallel_viable"`
	Layout               string   `json:"layout,omitempty"`
	LayoutOptions        []string `json:"layout_options,omitempty"`

	VLLMMin     string   `json:"vllm_min_version,omitempty"`
	NVFP4Dense  bool     `json:"nvfp4_dense"`
	NVFP4MoE    bool     `json:"nvfp4_moe"`
	WheelInBank bool     `json:"prebuilt_wheel_available"`
	Notes       string   `json:"notes,omitempty"`
	Tenants     []Tenant `json:"tenants"`
}

// Tenant looks a seat up by canonical role, returning the first placement. On
// a profile that runs several generator shards, use Seats.
func (p Profile) Tenant(role string) (Tenant, bool) {
	for _, tenant := range p.Tenants {
		if tenant.Role == role {
			return tenant, true
		}
	}
	return Tenant{}, false
}

// Seats returns every placement of a role, in card order.
func (p Profile) Seats(role string) []Tenant {
	var out []Tenant
	for _, tenant := range p.Tenants {
		if tenant.Role == role {
			out = append(out, tenant)
		}
	}
	return out
}

// CardCount is the number of physical GPUs, never below one.
func (p Profile) CardCount() int {
	if p.GPUs < 1 {
		return 1
	}
	return p.GPUs
}

// Multi reports whether this profile spans more than one card.
func (p Profile) Multi() bool { return p.CardCount() > 1 }

// TotalVRAMGiB is the aggregate board memory across every card. It is a sum,
// NOT a pool: see Fits.
func (p Profile) TotalVRAMGiB() float64 {
	return round1(p.VRAMGiB * float64(p.CardCount()))
}

// Card is one physical GPU and everything placed on it.
type Card struct {
	Index     int      `json:"gpu"`
	Label     string   `json:"label,omitempty"`
	Tenants   []Tenant `json:"tenants"`
	Committed float64  `json:"committed_gib"`
	Capacity  float64  `json:"capacity_gib"`
	Usable    float64  `json:"usable_gib"`
	Fits      bool     `json:"fits"`
}

// Cards groups the roster by physical GPU, in card order, with each card's own
// arithmetic. Single-card profiles yield exactly one card, so every caller can
// use this shape.
func (p Profile) Cards() []Card {
	cards := make([]Card, p.CardCount())
	for i := range cards {
		cards[i] = Card{Index: i, Capacity: p.VRAMGiB, Usable: p.Usable()}
	}
	for _, role := range RoleOrder {
		for _, seat := range p.Seats(role) {
			index := seat.GPU
			if index < 0 || index >= len(cards) {
				index = 0
			}
			cards[index].Tenants = append(cards[index].Tenants, seat)
			if seat.Enabled && !seat.Remote {
				cards[index].Committed += seat.VRAMGiB
			}
		}
	}
	for i := range cards {
		cards[i].Committed = round1(cards[i].Committed)
		cards[i].Fits = cards[i].Committed <= cards[i].Usable
		cards[i].Label = cardLabel(cards[i])
	}
	return cards
}

// cardLabel names a card by what sits on it. Repeated labels are collapsed with
// a count, so a tensor-parallel rank hosting three tenants reads as
// "tp rank 0 ×3" rather than repeating itself.
func cardLabel(card Card) string {
	var parts []string
	counts := map[string]int{}
	for _, seat := range card.Tenants {
		if !seat.Enabled {
			continue
		}
		label := seat.Role
		if seat.Shard != "" {
			label = seat.Shard
		}
		if counts[label] == 0 {
			parts = append(parts, label)
		}
		counts[label]++
	}
	for i, part := range parts {
		if counts[part] > 1 {
			parts[i] = fmt.Sprintf("%s ×%d", part, counts[part])
		}
	}
	return strings.Join(parts, " + ")
}

// Fits answers the only question that matters on a multi-card profile: does
// every individual card hold what was placed on it. An aggregate that looks
// roomy while one card overflows does not fit, and this never reports it as
// though it did.
func (p Profile) Fits() (bool, []string) {
	var over []string
	for _, card := range p.Cards() {
		if !card.Fits {
			over = append(over, fmt.Sprintf("gpu %d: %.1f GiB placed, %.1f GiB usable", card.Index, card.Committed, card.Usable))
		}
	}
	return len(over) == 0, over
}

// Committed is the VRAM the currently enabled seats claim, summed across every
// card. On a multi-card profile this figure is informational only — capacity is
// decided per card by Fits, because there is no pooling without NVLink.
func (p Profile) Committed() float64 {
	total := 0.0
	for _, tenant := range p.Tenants {
		if tenant.Enabled && !tenant.Remote {
			total += tenant.VRAMGiB
		}
	}
	return round1(total)
}

// WeightsCommitted sums the checkpoint sizes of the enabled seats. It is
// reported alongside Committed so the gap between "what the weights weigh" and
// "what vLLM reserves" is visible instead of being silently chosen between.
func (p Profile) WeightsCommitted() float64 {
	total := 0.0
	for _, tenant := range p.Tenants {
		if !tenant.Enabled || tenant.Remote {
			continue
		}
		if tenant.WeightsGiB > 0 {
			total += tenant.WeightsGiB
			continue
		}
		total += tenant.VRAMGiB
	}
	return round1(total)
}

// ReservationBacked reports whether every enabled seat's figure came from the
// config's reservation model rather than from roster weights. When it is false
// the displayed budget is optimistic and must be labelled as such.
func (p Profile) ReservationBacked() bool {
	for _, tenant := range p.Tenants {
		if tenant.Enabled && !tenant.Remote && tenant.VRAMSource != VRAMFromConfig {
			return false
		}
	}
	return true
}

// Projected is Committed plus every toggle that is currently off — what the
// card would hold with everything switched on.
func (p Profile) Projected() float64 {
	total := p.Committed()
	for _, tenant := range p.Tenants {
		if !tenant.Enabled && !tenant.Remote {
			total += tenant.VRAMGiB
		}
	}
	return round1(total)
}

// Usable is capacity minus the reserve held back for CUDA contexts, allocator
// fragmentation, and driver overhead across several co-tenant processes.
func (p Profile) Usable() float64 {
	if p.ReserveGiB <= 0 {
		return p.VRAMGiB
	}
	return round1(p.VRAMGiB - p.ReserveGiB)
}

// Toggles lists the seats that can be switched, in display order.
func (p Profile) Toggles() []Tenant {
	var out []Tenant
	for _, tenant := range p.Tenants {
		if tenant.Toggleable {
			out = append(out, tenant)
		}
	}
	return out
}

// DenseOnly reports whether every enabled seat is a dense model. It is the
// question sm_120 provisioning turns on: dense NVFP4 GEMM works there, but the
// NVFP4 MoE kernels are gated behind a compute-capability-100 family check and
// crash on a workstation Blackwell card.
func (p Profile) DenseOnly() (bool, []string) {
	var sparse []string
	for _, tenant := range p.Tenants {
		if tenant.Enabled && !tenant.Dense {
			sparse = append(sparse, tenant.Role+" ("+tenant.Model+")")
		}
	}
	return len(sparse) == 0, sparse
}

// KernelNotes are the compiled-in facts about a kernel family. They do not come
// from config because they are properties of the silicon, not of this estate.
func KernelNotes(sm string) []string {
	switch strings.ToLower(strings.TrimSpace(sm)) {
	case "sm_120":
		return []string{
			"SM80-era mma.sync — no tcgen05.mma",
			"GEMM tiles must fit 99 KB shared memory (sm_100 has 228 KB)",
			"SM100-targeted kernels (DeepGEMM, CUTLASS SM100 collectives, WGMMA FlashAttention) fail to compile or crash",
			"dense NVFP4 GEMM works on vLLM >= 0.13.0",
			"NVFP4 MoE kernels are BROKEN here (vllm#33416, vllm#31085, flashinfer#2577)",
			"no sm120 wheel in the R2 artifact bank — sm100 and sm80 only; vLLM must be built for sm_120 first",
		}
	case "sm_100":
		return []string{
			"tcgen05.mma tensor-core path",
			"228 KB shared memory per GEMM tile",
			"dense and MoE NVFP4 kernels both supported",
			"the R2 artifact bank's sm100 wheel applies directly",
		}
	}
	return []string{"kernel family not characterised for " + sm}
}

// Precedence decides who wins when the continuum file and the compiled roster
// disagree about a model, its precision, or its VRAM budget.
//
// The default is PreferRoster. The roster is the ratified answer and the
// continuum file is being rewritten continuously by other hands; a CLI that
// silently adopted whatever the file said this minute would report a different
// roster every few minutes and would have no way to notice a regression. Under
// PreferRoster every divergence is still reported, in full, with both values —
// so nothing is hidden, and `--prefer config` shows the file's version.
type Precedence string

const (
	PreferRoster Precedence = "roster"
	PreferConfig Precedence = "config"
)

// ParsePrecedence turns a flag value into a policy.
func ParsePrecedence(value string) (Precedence, error) {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "roster", "builtin", "ratified":
		return PreferRoster, nil
	case "config", "continuum", "toml", "file":
		return PreferConfig, nil
	}
	return "", fmt.Errorf("unknown --prefer %q; use roster or config", value)
}

// Registry is the resolved roster plus an honest account of where it came from.
type Registry struct {
	Profiles   []Profile  `json:"profiles"`
	Source     string     `json:"source"`
	SourcePath string     `json:"source_path,omitempty"`
	Prefer     Precedence `json:"prefer"`
	Degraded   bool       `json:"degraded"`
	Notes      []string   `json:"notes,omitempty"`
	Drift      []string   `json:"drift,omitempty"`

	// VRAMDrift is kept apart from Drift because it is the one disagreement
	// that changes a capacity verdict rather than a label. A roster weights
	// figure standing in for a reservation reads as comfortable headroom when
	// the card is actually 0.33% from full.
	VRAMDrift []string `json:"vram_drift,omitempty"`
	Extra     []string `json:"config_only_profiles,omitempty"`

	// raw is the parsed [profiles.*] table, kept so an alternate layout can be
	// overlaid from config on demand rather than re-reading the file.
	raw map[string]any
}

// DefaultProfile is the profile used when none is named.
func (r Registry) DefaultProfile() Profile {
	for _, profile := range r.Profiles {
		if profile.Default {
			return profile
		}
	}
	return r.Profiles[0]
}

// Names lists profile names in registry order.
func (r Registry) Names() []string {
	out := make([]string, 0, len(r.Profiles))
	for _, profile := range r.Profiles {
		out = append(out, profile.Name)
	}
	return out
}

// WithLayout resolves a profile and applies a named placement. Only the
// multi-card profile has alternatives; asking for one elsewhere is an error
// rather than a silently ignored flag.
func (r Registry) WithLayout(name, layout string) (Profile, error) {
	profile, err := r.Lookup(name)
	if err != nil {
		return Profile{}, err
	}
	if strings.TrimSpace(layout) == "" {
		return profile, nil
	}
	if len(profile.LayoutOptions) == 0 {
		return Profile{}, fmt.Errorf("profile %s has a single fixed layout; --layout applies only to %s", profile.Name, "rtx-pro-6000-x4")
	}
	wanted := x4LayoutName(layout)
	if !strings.EqualFold(wanted, layout) && !containsFold(profile.LayoutOptions, layout) {
		return Profile{}, fmt.Errorf("unknown layout %q; %s supports: %s", layout, profile.Name, strings.Join(profile.LayoutOptions, ", "))
	}
	// Placement comes from the compiled roster. When the continuum models the
	// alternate layout as its own profile table — `rtx-pro-6000-x4-tp` — its
	// operational fields are overlaid on top of the roster placement.
	relaid := X4Profile(wanted)
	relaid.Default = profile.Default
	relaid.InterconnectDetected = profile.InterconnectDetected
	relaid.InterconnectVerified = profile.InterconnectVerified
	if r.raw != nil {
		if table, ok := findLayoutTable(r.raw, profile.Name, wanted); ok {
			overlay := Registry{Prefer: r.Prefer}
			applyProfile(&overlay, &relaid, table)
			relaid.Layout = wanted
			relaid.LayoutOptions = X4LayoutOptions
		}
	}
	return relaid, nil
}

// layoutVariantOf recognises a config profile named `<base>-<layout>`, which is
// how the continuum models an alternate placement of a profile the roster
// already carries.
func layoutVariantOf(name string) string {
	lowered := strings.ToLower(name)
	for _, layout := range X4LayoutOptions {
		if suffix := "-" + layout; strings.HasSuffix(lowered, suffix) {
			base := strings.TrimSuffix(lowered, suffix)
			if _, ok := profileAlias[base]; ok {
				return layout
			}
		}
	}
	return ""
}

func findLayoutTable(profiles map[string]any, base, layout string) (map[string]any, bool) {
	for name, value := range profiles {
		table, ok := value.(map[string]any)
		if !ok || layoutVariantOf(name) != layout {
			continue
		}
		lowered := strings.ToLower(name)
		if alias, ok := profileAlias[strings.TrimSuffix(lowered, "-"+layout)]; ok && alias == base {
			return table, true
		}
	}
	return nil, false
}

func containsFold(haystack []string, needle string) bool {
	for _, item := range haystack {
		if strings.EqualFold(item, needle) {
			return true
		}
	}
	return false
}

// Lookup resolves a profile by name or alias. An empty name yields the default.
func (r Registry) Lookup(name string) (Profile, error) {
	if strings.TrimSpace(name) == "" {
		return r.DefaultProfile(), nil
	}
	key := strings.ToLower(strings.TrimSpace(name))
	if canonical, ok := profileAlias[key]; ok {
		key = canonical
	}
	for _, profile := range r.Profiles {
		if strings.ToLower(profile.Name) == key {
			return profile, nil
		}
	}
	return Profile{}, fmt.Errorf("unknown profile %q; known profiles: %s", name, strings.Join(r.Names(), ", "))
}

// Builtin returns the compiled roster: the three ratified hardware profiles.
// This is what `flux arcane models` prints when jury_continuum.toml is missing,
// mid-rewrite, or unparseable. It is a fallback, never a suggestion — every
// model id here is the ratified one.
func Builtin() []Profile {
	return []Profile{
		{
			Name:         "rtx-pro-6000",
			Default:      true,
			GPU:          "NVIDIA RTX PRO 6000 Blackwell Server Edition",
			MemoryKind:   "GDDR7",
			VRAMGiB:      96.0,
			ReserveGiB:   2.0,
			SM:           "sm_120",
			VLLMMin:      "0.13.0",
			NVFP4Dense:   true,
			NVFP4MoE:     false,
			WheelInBank:  false,
			GPUs:         1,
			Interconnect: "n/a (single card)",
			TPViable:     false,
			Notes:        "Workstation Blackwell. Dense NVFP4 only. Kontext does not fit alongside a BF16 generator.",
			Tenants: []Tenant{
				tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false),
				tenant(RoleStructure, "vllm", "unsloth/Qwen3.8-27B-NVFP4", "NVFP4", 24.6, true, false),
				tenant(RoleGovernor, "vllm", "nvidia/Gemma-4-31B-IT-NVFP4", "NVFP4", 19.0, true, false),
				tenant(RolePalette, "vllm", "RedHatAI/pixtral-12b-quantized.w4a16", "INT4 w4a16", 7.0, true, false),
				tenant(RoleGates, "inproc", gatesModel, "fp16", 3.0, true, false),
				tenant(RoleKontext, "uds", "black-forest-labs/FLUX.1-Kontext-dev", "BF16", 24.0, false, false),
			},
		},
		{
			Name:         "b200",
			GPU:          "NVIDIA B200 SXM",
			MemoryKind:   "HBM3e",
			Bandwidth:    "8 TB/s",
			VRAMGiB:      192.0,
			ReserveGiB:   4.0,
			SM:           "sm_100",
			VLLMMin:      "0.13.0",
			NVFP4Dense:   true,
			NVFP4MoE:     true,
			WheelInBank:  true,
			GPUs:         1,
			Interconnect: "nvlink5",
			TPViable:     true,
			Notes:        "Datacenter Blackwell. Kontext runs at full BF16 with room to spare.",
			Tenants: []Tenant{
				tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false),
				tenant(RoleStructure, "vllm", "Qwen/Qwen3.8-27B-FP8", "FP8", 28.0, true, false),
				tenant(RoleGovernor, "vllm", "redhatai/gemma-4-31b-it-fp8-dynamic", "FP8", 56.0, true, false),
				tenant(RolePalette, "vllm", "mistralai/Pixtral-12B-2409", "FP8", 14.0, true, false),
				tenant(RoleGates, "inproc", gatesModel, "fp16", 3.0, true, false),
				tenant(RoleKontext, "uds", "black-forest-labs/FLUX.1-Kontext-dev", "BF16", 24.0, false, false),
			},
		},
		{
			Name:         "b300",
			GPU:          "NVIDIA B300 SXM6 (Blackwell Ultra)",
			MemoryKind:   "HBM3e",
			Bandwidth:    "8 TB/s",
			VRAMGiB:      288.0,
			ReserveGiB:   6.0,
			SM:           "sm_100",
			VLLMMin:      "0.13.0",
			NVFP4Dense:   true,
			NVFP4MoE:     true,
			WheelInBank:  true,
			GPUs:         1,
			Interconnect: "nvlink5",
			TPViable:     true,
			Notes:        "Blackwell Ultra. The ratified full-precision posture: judges run BF16 here.",
			Tenants: []Tenant{
				tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false),
				tenant(RoleStructure, "vllm", "Qwen/Qwen3.8-27B", "BF16", 54.0, true, false),
				tenant(RoleGovernor, "vllm", "redhatai/gemma-4-31b-it-fp8-dynamic", "FP8", 56.0, true, false),
				tenant(RolePalette, "vllm", "mistralai/Pixtral-12B-2409", "BF16", 24.0, true, false),
				tenant(RoleGates, "inproc", gatesModel, "fp16", 3.0, true, false),
				// Kontext defaults ON here: 288 GiB is the one single-card
				// profile with room for it alongside a BF16 generator and
				// BF16 judges.
				enable(tenant(RoleKontext, "uds", "black-forest-labs/FLUX.1-Kontext-dev", "BF16", 24.0, false, false)),
			},
		},
		X4Profile(LayoutBalanced),
	}
}

// Layout names for the four-card profile.
const (
	LayoutBalanced = "balanced"
	LayoutDense    = "dense"
	LayoutTP       = "tp"
)

// X4LayoutOptions are the placements the four-card profile supports.
var X4LayoutOptions = []string{LayoutBalanced, LayoutDense, LayoutTP}

// X4Profile is the four-card RTX PRO 6000 Blackwell cluster.
//
// Four 96 GiB cards are FOUR CARDS, not a 384 GiB pool. Every tenant must fit
// one card on its own; capacity is decided per GPU and never in aggregate. The
// operator reports NVLink on this machine — which makes tensor parallelism
// viable and lets the judges run wider — but nothing in the roster needs TP for
// capacity: FLUX BF16 35.0, Qwen3.8-27B BF16 54.0, Gemma-4-31B BF16 62.0 and
// Pixtral BF16 24.0 each fit a single card. TP buys latency and headroom here,
// not feasibility. Because public specs for this board say it has no NVLink and
// the operator's machine says otherwise, `flux arcane provision` detects the
// interconnect rather than trusting either claim.
func X4Profile(layout string) Profile {
	tenants, notes := x4Layout(layout)
	return Profile{
		Name:          "rtx-pro-6000-x4",
		GPU:           "NVIDIA RTX PRO 6000 Blackwell Server Edition",
		MemoryKind:    "GDDR7",
		VRAMGiB:       96.0,
		ReserveGiB:    2.0,
		SM:            "sm_120",
		VLLMMin:       "0.13.0",
		NVFP4Dense:    true,
		NVFP4MoE:      false,
		WheelInBank:   false,
		GPUs:          4,
		Interconnect:  "nvlink",
		TPViable:      true,
		Layout:        x4LayoutName(layout),
		LayoutOptions: X4LayoutOptions,
		Notes:         notes,
		Tenants:       tenants,
	}
}

func x4LayoutName(layout string) string {
	switch strings.ToLower(strings.TrimSpace(layout)) {
	case LayoutDense:
		return LayoutDense
	case LayoutTP, "tensor-parallel", "tensor_parallel":
		return LayoutTP
	}
	return LayoutBalanced
}

// x4Layout places the roster across four cards. Each layout's arithmetic is
// per-card and is stated in the note so the trade is legible.
func x4Layout(layout string) ([]Tenant, string) {
	switch x4LayoutName(layout) {
	case LayoutDense:
		// Four generator shards. The judge card takes a fourth generator, so
		// the palette critic drops back to INT4 to leave room for it.
		return []Tenant{
			place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 0, "shard 0", "flux-gpu0.sock"),
			place(tenant(RoleGates, "inproc", gatesModel, "fp16", 3.0, true, false), 0, "", ""),
			enable(place(tenant(RoleKontext, "uds", "black-forest-labs/FLUX.1-Kontext-dev", "BF16", 24.0, false, false), 0, "", "flux-kontext.sock")),
			place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 1, "shard 1", "flux-gpu1.sock"),
			place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 2, "shard 2", "flux-gpu2.sock"),
			place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 3, "shard 3", "flux-gpu3.sock"),
			place(tenant(RoleStructure, "vllm", "Qwen/Qwen3.8-27B-FP8", "FP8", 28.0, true, false), 3, "", ""),
			place(tenant(RoleGovernor, "vllm", "nvidia/Gemma-4-31B-IT-NVFP4", "NVFP4", 19.0, true, false), 3, "", ""),
			place(tenant(RolePalette, "vllm", "RedHatAI/pixtral-12b-quantized.w4a16", "INT4 w4a16", 7.0, true, false), 3, "", ""),
		}, "Four generator shards. GPU 3 carries a fourth generator alongside the judges (89.0/96.0), so judge latency contends with generation on that card and the palette critic drops to INT4 to fit."

	case LayoutTP:
		// The judge stack runs BF16 tensor-parallel across GPUs 2 and 3. It
		// costs two generator shards; it buys full-precision critics.
		return []Tenant{
			place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 0, "shard 0", "flux-gpu0.sock"),
			place(tenant(RoleGates, "inproc", gatesModel, "fp16", 3.0, true, false), 0, "", ""),
			enable(place(tenant(RoleKontext, "uds", "black-forest-labs/FLUX.1-Kontext-dev", "BF16", 24.0, false, false), 0, "", "flux-kontext.sock")),
			place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 1, "shard 1", "flux-gpu1.sock"),
			shard(place(tenant(RoleStructure, "vllm", "Qwen/Qwen3.8-27B", "BF16", 27.0, true, false), 2, "tp rank 0", ""), 2),
			shard(place(tenant(RoleGovernor, "vllm", "redhatai/gemma-4-31b-it-fp8-dynamic", "BF16", 31.0, true, false), 2, "tp rank 0", ""), 2),
			shard(place(tenant(RolePalette, "vllm", "mistralai/Pixtral-12B-2409", "BF16", 12.0, true, false), 2, "tp rank 0", ""), 2),
			shard(place(tenant(RoleStructure, "vllm", "Qwen/Qwen3.8-27B", "BF16", 27.0, true, false), 3, "tp rank 1", ""), 2),
			shard(place(tenant(RoleGovernor, "vllm", "redhatai/gemma-4-31b-it-fp8-dynamic", "BF16", 31.0, true, false), 3, "tp rank 1", ""), 2),
			shard(place(tenant(RolePalette, "vllm", "mistralai/Pixtral-12B-2409", "BF16", 12.0, true, false), 3, "tp rank 1", ""), 2),
		}, "Judge stack tensor-parallel at BF16 across GPUs 2 and 3 (70.0/96.0 each). Requires a verified NVLink fabric: over PCIe Gen5 x16 this layout is impractical. Costs two generator shards."
	}

	// balanced: three generator shards and one dedicated judge card.
	return []Tenant{
		place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 0, "shard 0", "flux-gpu0.sock"),
		place(tenant(RoleGates, "inproc", gatesModel, "fp16", 3.0, true, false), 0, "", ""),
		enable(place(tenant(RoleKontext, "uds", "black-forest-labs/FLUX.1-Kontext-dev", "BF16", 24.0, false, false), 0, "", "flux-kontext.sock")),
		place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 1, "shard 1", "flux-gpu1.sock"),
		place(tenant(RoleGenerator, "uds", "black-forest-labs/FLUX.1-dev", "BF16", 35.0, true, false), 2, "shard 2", "flux-gpu2.sock"),
		place(tenant(RoleStructure, "vllm", "Qwen/Qwen3.8-27B-FP8", "FP8", 28.0, true, false), 3, "", ""),
		place(tenant(RoleGovernor, "vllm", "nvidia/Gemma-4-31B-IT-NVFP4", "NVFP4", 19.0, true, false), 3, "", ""),
		place(tenant(RolePalette, "vllm", "mistralai/Pixtral-12B-2409", "FP8", 14.0, true, false), 3, "", ""),
	}, "Three generator shards plus one dedicated judge card. Off the generator cards, the structure and palette critics upgrade from NVFP4/INT4 to FP8 — the interesting difference from the single-card profile. Kontext is ON: GPU 0 has the room."
}

func place(t Tenant, gpu int, shardName, socket string) Tenant {
	t.GPU = gpu
	t.Shard = shardName
	if socket != "" {
		t.Socket = socket
	} else if t.Kind != "uds" {
		t.Socket = ""
	}
	return t
}

func enable(t Tenant) Tenant {
	t.Enabled = true
	return t
}

func shard(t Tenant, degree int) Tenant {
	t.TensorParallel = degree
	return t
}

const gatesModel = "facebook/dinov2-giant + google/siglip-so400m-patch14-384"

var roleDuty = map[string]string{
	RoleGenerator: "Continuous Flow-Matching Candidate Production",
	RoleStructure: "Spatial Grounding, Anatomy, Line Integrity & Defect Scan",
	RoleGovernor:  "Senior Protocol Advisor, Semantic Auditor & Prompt Mutator",
	RolePalette:   "Artistic Theory, Palette Cohesion, Tonal Density & Medium Authenticity",
	RoleGates:     "High-Frequency Micro-Sensory Gates (<10ms)",
	RoleKontext:   "Instruction-Guided Edit & Re-Composition Pass",
}

var rolePort = map[string]int{
	RoleStructure: 8001,
	RoleGovernor:  8000,
	RolePalette:   8002,
}

var roleSocket = map[string]string{
	RoleGenerator: "flux-gpu0.sock",
	RoleKontext:   "flux-kontext.sock",
}

// tenant builds a roster seat. Every model in the roster is dense: that is the
// only reason the sm_120 profile is safe at all, so it is asserted here rather
// than passed in per call site.
func tenant(role, kind, model, precision string, vram float64, mandatory, remote bool) Tenant {
	return Tenant{
		Role:          role,
		ConfigKey:     configFromRole[role],
		Duty:          roleDuty[role],
		Model:         model,
		Precision:     precision,
		VRAMGiB:       vram,
		WeightsGiB:    vram,
		RosterVRAMGiB: vram,
		VRAMSource:    VRAMFromRoster,
		Kind:          kind,
		Port:          rolePort[role],
		Socket:        roleSocket[role],
		Enabled:       mandatory,
		Mandatory:     mandatory,
		Toggleable:    !mandatory,
		Dense:         true,
		Remote:        remote,
	}
}

// Load resolves the registry for a repo root. The continuum file is the live
// source of truth; the compiled roster is the fallback and the identity check.
// A missing, truncated, or malformed config never fails the command — it
// degrades to the roster and says so.
func Load(root string) Registry { return LoadWith(root, PreferRoster) }

// LoadWith resolves the registry under an explicit precedence policy.
func LoadWith(root string, prefer Precedence) Registry {
	if prefer == "" {
		prefer = PreferRoster
	}
	registry := Registry{
		Profiles: Builtin(),
		Source:   "compiled roster",
		Prefer:   prefer,
	}

	path := filepath.Join(root, ContinuumFile)
	registry.SourcePath = path

	raw, err := os.ReadFile(path)
	if err != nil {
		registry.Degraded = true
		registry.Notes = append(registry.Notes, fmt.Sprintf("%s unreadable (%v) — using the compiled roster", ContinuumFile, err))
		return registry
	}

	doc, err := ParseTOML(string(raw))
	if err != nil {
		registry.Degraded = true
		registry.Notes = append(registry.Notes, fmt.Sprintf("%s did not parse (%v) — using the compiled roster", ContinuumFile, err))
		return registry
	}

	profilesTable, ok := TableAt(doc, "profiles")
	if !ok || len(profilesTable) == 0 {
		registry.Degraded = true
		registry.Notes = append(registry.Notes, fmt.Sprintf("%s has no [profiles.*] tables — using the compiled roster", ContinuumFile))
		return registry
	}

	registry.Source = ContinuumFile
	registry.raw = profilesTable
	applied := 0
	for i := range registry.Profiles {
		name := registry.Profiles[i].Name
		table, found := findProfileTable(profilesTable, name)
		if !found {
			registry.Notes = append(registry.Notes, fmt.Sprintf("%s has no profile %q — that profile is served from the compiled roster", ContinuumFile, name))
			continue
		}
		applyProfile(&registry, &registry.Profiles[i], table)
		applied++
	}
	if applied == 0 {
		registry.Degraded = true
		registry.Source = "compiled roster"
		registry.Notes = append(registry.Notes, fmt.Sprintf("%s named none of the roster profiles — using the compiled roster", ContinuumFile))
		return registry
	}
	if applied < len(registry.Profiles) {
		registry.Degraded = true
	}

	// Default profile: honour [continuum].default_profile when it names a
	// profile we actually carry.
	if continuum, ok := TableAt(doc, "continuum"); ok {
		if want := Str(continuum, "default_profile", ""); want != "" {
			if canonical, ok := profileAlias[strings.ToLower(want)]; ok {
				for i := range registry.Profiles {
					registry.Profiles[i].Default = registry.Profiles[i].Name == canonical
				}
			}
		}
	}

	// Profiles the config carries that the roster does not. Reported, never
	// merged: the arcane roster is three profiles and this keeps that honest
	// without hiding that the continuum serves other hardware too.
	known := map[string]bool{}
	for _, profile := range registry.Profiles {
		known[profile.Name] = true
	}
	for name := range profilesTable {
		canonical := name
		if alias, ok := profileAlias[strings.ToLower(name)]; ok {
			canonical = alias
		}
		if known[canonical] || layoutVariantOf(name) != "" {
			continue
		}
		registry.Extra = append(registry.Extra, name)
	}
	sort.Strings(registry.Extra)

	// The retired list is data in the config; fold it into the check so a
	// future addition there is enforced without a Go change.
	if retired, ok := TableAt(doc, "retired"); ok {
		for _, model := range Strings(retired, "models") {
			if !IsBanned(model) {
				BannedModels = append(BannedModels, model)
			}
		}
	}

	return registry
}

func findProfileTable(profiles map[string]any, canonical string) (map[string]any, bool) {
	for name, value := range profiles {
		table, ok := value.(map[string]any)
		if !ok {
			continue
		}
		key := strings.ToLower(name)
		if alias, ok := profileAlias[key]; ok {
			key = alias
		}
		if key == canonical {
			return table, true
		}
	}
	return nil, false
}

// applyProfile overlays one config profile onto its roster counterpart. Model
// identity from config wins — the CLI must show what will actually be
// provisioned — but a divergence from the roster is recorded as drift, and a
// retired model is refused outright rather than displayed.
func applyProfile(registry *Registry, profile *Profile, table map[string]any) {
	profile.GPU = Str(table, "gpu", profile.GPU)
	profile.SM = Str(table, "sm", Str(table, "compute_capability", profile.SM))
	profile.VLLMMin = Str(table, "vllm_min_version", profile.VLLMMin)
	profile.Notes = Str(table, "notes", Str(table, "description", profile.Notes))
	profile.ReserveGiB = Num(table, "reserve_gib", profile.ReserveGiB)
	profile.NVFP4Dense = Flag(table, "native_nvfp4_dense", Flag(table, "native_nvfp4", profile.NVFP4Dense))
	profile.NVFP4MoE = Flag(table, "native_nvfp4_moe", profile.NVFP4MoE)
	profile.WheelInBank = Flag(table, "prebuilt_wheel_available", profile.WheelInBank)

	// VRAM here is PER CARD. gpu_count multiplies the aggregate, never the
	// per-card capacity — conflating the two is how a four-card cluster gets
	// mistaken for one 384 GiB pool.
	vram := Num(table, "vram_gib", 0)
	if vram <= 0 {
		vram = Num(table, "vram_per_gpu_gib", 0)
	}
	if vram > 0 {
		profile.VRAMGiB = vram
	}
	profile.GPUs = Whole(table, "gpus", Whole(table, "gpu_count", profile.GPUs))

	profile.Interconnect = Str(table, "interconnect", profile.Interconnect)
	profile.InterconnectVerified = Flag(table, "interconnect_verified", profile.InterconnectVerified)
	profile.InterconnectDetected = Str(table, "interconnect_detected", profile.InterconnectDetected)
	profile.TPViable = Flag(table, "tensor_parallel_viable", profile.TPViable)
	profile.Layout = Str(table, "layout", profile.Layout)

	tenants, ok := TableAt(table, "tenants")
	if !ok {
		registry.Notes = append(registry.Notes, fmt.Sprintf("profile %q has no tenants in %s — roster seats kept", profile.Name, ContinuumFile))
		return
	}

	// A role may hold several seats (generator shards, tensor-parallel ranks).
	// Config entries for a role are matched to roster seats in key order, so
	// `flux0`/`flux1`/`flux2` land on shards 0, 1 and 2. A role the config does
	// not mention keeps its roster seat.
	byRole := groupTenantTables(tenants)
	used := map[string]int{}
	for i := range profile.Tenants {
		seat := &profile.Tenants[i]
		entries := byRole[seat.Role]
		index := used[seat.Role]
		if index >= len(entries) {
			if index == 0 {
				registry.Notes = append(registry.Notes, fmt.Sprintf("profile %q: no %s tenant in %s — roster seat kept", profile.Name, seat.Role, ContinuumFile))
			}
			continue
		}
		used[seat.Role]++
		applyTenant(registry, profile.Name, seat, entries[index])
	}
}

// groupTenantTables buckets the config's tenant tables by canonical role,
// deterministically ordered by key so shard placement is stable across runs.
// A key may carry an index suffix (`flux0`, `flux-1`) for a role with several
// seats; the suffix is stripped before the role lookup.
func groupTenantTables(tenants map[string]any) map[string][]map[string]any {
	keys := make([]string, 0, len(tenants))
	for name := range tenants {
		keys = append(keys, name)
	}
	sort.Strings(keys)

	out := map[string][]map[string]any{}
	for _, name := range keys {
		table, ok := tenants[name].(map[string]any)
		if !ok {
			continue
		}
		role := roleFromConfigKey(name)
		if role == "" {
			continue
		}
		out[role] = append(out[role], table)
	}
	return out
}

func roleFromConfigKey(name string) string {
	key := strings.ToLower(strings.TrimSpace(name))
	if role, ok := roleFromConfig[key]; ok {
		return role
	}
	trimmed := strings.TrimRight(key, "0123456789")
	trimmed = strings.TrimRight(trimmed, "-_")
	return roleFromConfig[trimmed]
}

func applyTenant(registry *Registry, profileName string, seat *Tenant, entry map[string]any) {
	seat.Duty = Str(entry, "role", seat.Duty)
	seat.Kind = Str(entry, "kind", seat.Kind)
	seat.Socket = Str(entry, "socket", seat.Socket)
	seat.Served = Str(entry, "served_name", seat.Served)
	seat.Port = Whole(entry, "port", seat.Port)
	seat.GPUMemUtil = Num(entry, "gpu_memory_utilization", seat.GPUMemUtil)
	seat.Enabled = Flag(entry, "enabled", seat.Enabled)
	seat.Mandatory = Flag(entry, "mandatory", seat.Mandatory)
	seat.Toggleable = Flag(entry, "toggleable", seat.Toggleable)
	seat.Dense = Flag(entry, "dense", seat.Dense)
	seat.Remote = Flag(entry, "remote", seat.Remote)
	seat.Note = Str(entry, "note", seat.Note)
	seat.GPU = Whole(entry, "gpu", seat.GPU)
	seat.Shard = Str(entry, "shard", seat.Shard)
	seat.TensorParallel = Whole(entry, "tensor_parallel", seat.TensorParallel)

	// A tenant may carry `model`/`precision` directly, or select one of its
	// `variants.<precision>` sub-tables. Resolve the variant first so the
	// selected precision's model id is the one considered.
	precision := Str(entry, "precision", seat.Precision)
	source := entry
	if variants, ok := TableAt(entry, "variants"); ok {
		if chosen, ok := variants[strings.ToLower(precision)].(map[string]any); ok {
			source = chosen
			seat.Note = Str(chosen, "note", seat.Note)
			seat.GPUMemUtil = Num(chosen, "gpu_memory_utilization", seat.GPUMemUtil)
		}
	}

	// Two different quantities, kept apart. `vram_expected_gib` is what vLLM
	// RESERVES (gpu_memory_utilization × card × tensor_parallel); `weights_gib`
	// is the checkpoint. Capacity is decided on the reservation.
	configVRAM := round1(Num(source, "vram_expected_gib", Num(entry, "vram_expected_gib", 0)))
	configWeights := round1(Num(source, "weights_gib", Num(entry, "weights_gib", 0)))
	if configVRAM <= 0 {
		configVRAM = configWeights
	}
	// Only adopt the config's weights once the two sides agree on WHICH
	// checkpoint is being sized; otherwise the row would pair one model's
	// weights with another's reservation.
	adoptWeights := func() {
		if configWeights > 0 {
			seat.WeightsGiB = configWeights
		}
	}

	// VRAM precedence is NOT the roster's, under either policy. The roster
	// carries weights; only the config carries a reservation. Preferring the
	// roster here reports a comfortable margin on a card that is one KV cache
	// growth away from an OOM.
	applyVRAM := func() {
		if configVRAM <= 0 {
			return
		}
		if seat.RosterVRAMGiB > 0 && configVRAM != round1(seat.RosterVRAMGiB) {
			registry.VRAMDrift = append(registry.VRAMDrift, fmt.Sprintf(
				"%s/%s reserves %.1f GiB; roster weights say %.1f GiB. The %.1f GiB gap is KV cache and activation arena — the reservation is authoritative",
				profileName, seat.Role, configVRAM, seat.RosterVRAMGiB, round1(configVRAM-seat.RosterVRAMGiB)))
		}
		seat.VRAMGiB = configVRAM
		seat.VRAMSource = VRAMFromConfig
	}

	model := Str(source, "model", Str(entry, "model", ""))
	diverges := model != "" && !strings.EqualFold(model, seat.Model)

	switch {
	case model == "":
		// Config named no model for this seat; the roster's stands.
	case IsBanned(model):
		// A retired model is refused under either policy. This is the one case
		// where config never wins: the retired list exists precisely so a
		// regression here is caught rather than served.
		registry.Degraded = true
		registry.Drift = append(registry.Drift, fmt.Sprintf(
			"%s/%s: %s names the RETIRED model %q — REFUSED; roster model %q served instead",
			profileName, seat.Role, ContinuumFile, model, seat.Model))
		return
	case diverges && registry.Prefer == PreferConfig:
		registry.Drift = append(registry.Drift, fmt.Sprintf(
			"%s/%s: config %q · roster %q — config wins (--prefer config)",
			profileName, seat.Role, model, seat.Model))
		seat.Model = model
		seat.Precision = displayPrecision(precision)
		adoptWeights()
		applyVRAM()
		return
	case diverges:
		// The roster keeps the model identity, but its VRAM figure describes a
		// different checkpoint, so neither side's arithmetic applies to what
		// will actually be loaded. Say so rather than quietly picking one.
		detail := fmt.Sprintf("%s/%s: roster %q %s · config %q %s",
			profileName, seat.Role, seat.Model, seat.Precision, model, displayPrecision(precision))
		if configVRAM > 0 {
			detail += fmt.Sprintf(" reserving %.1f GiB", configVRAM)
		}
		registry.Drift = append(registry.Drift, detail+" — roster wins on identity (--prefer config to swap)")
		// Neither figure describes what will load: keep the roster's own weights
		// on both columns and mark the seat unresolved rather than pairing this
		// model's weights with that model's reservation.
		seat.WeightsGiB = seat.RosterVRAMGiB
		seat.VRAMSource = VRAMFromRoster
		registry.VRAMDrift = append(registry.VRAMDrift, fmt.Sprintf(
			"%s/%s budget UNRESOLVED: the roster serves %q but config sizes %q (reserving %.1f GiB). The %.1f GiB shown is roster WEIGHTS for a different checkpoint — no reservation applies",
			profileName, seat.Role, seat.Model, model, configVRAM, seat.VRAMGiB))
		return
	}

	// Models agree. Take the config's precision spelling and its reservation.
	if precision != "" {
		seat.Precision = displayPrecision(precision)
	}
	adoptWeights()
	applyVRAM()
}

// displayPrecision normalises the config's lowercase precision keys into the
// spelling the roster prints.
func displayPrecision(precision string) string {
	switch strings.ToLower(strings.TrimSpace(precision)) {
	case "bf16":
		return "BF16"
	case "fp8":
		return "FP8"
	case "fp16":
		return "fp16"
	case "nvfp4":
		return "NVFP4"
	case "w4a16":
		return "INT4 w4a16"
	case "q4_k_s":
		return "GGUF Q4_K_S"
	case "awq":
		return "AWQ INT4"
	case "":
		return "—"
	}
	return precision
}

// VersionAtLeast compares dotted version strings, e.g. "0.13.0" against a
// floor of "0.13.0". Non-numeric suffixes ("0.13.0rc1", "0.14.0+cu130") are cut
// at the first non-digit so a release candidate does not read as newer than the
// release it precedes.
func VersionAtLeast(have, floor string) (bool, bool) {
	haveParts, ok := versionParts(have)
	if !ok {
		return false, false
	}
	floorParts, ok := versionParts(floor)
	if !ok {
		return false, false
	}
	for i := 0; i < len(haveParts) || i < len(floorParts); i++ {
		h, f := 0, 0
		if i < len(haveParts) {
			h = haveParts[i]
		}
		if i < len(floorParts) {
			f = floorParts[i]
		}
		if h != f {
			return h > f, true
		}
	}
	return true, true
}

func versionParts(v string) ([]int, bool) {
	v = strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(v), "v"))
	if v == "" {
		return nil, false
	}
	var parts []int
	for _, chunk := range strings.Split(v, ".") {
		digits := chunk
		for i, r := range chunk {
			if r < '0' || r > '9' {
				digits = chunk[:i]
				break
			}
		}
		if digits == "" {
			break
		}
		n, err := strconv.Atoi(digits)
		if err != nil {
			return nil, false
		}
		parts = append(parts, n)
	}
	if len(parts) == 0 {
		return nil, false
	}
	return parts, true
}

// SMFromComputeCapability turns nvidia-smi's "12.0" into "sm_120".
func SMFromComputeCapability(cap string) string {
	cap = strings.TrimSpace(cap)
	if cap == "" {
		return ""
	}
	parts := strings.SplitN(cap, ".", 2)
	major, err := strconv.Atoi(strings.TrimSpace(parts[0]))
	if err != nil {
		return ""
	}
	minor := 0
	if len(parts) == 2 {
		if m, err := strconv.Atoi(strings.TrimSpace(parts[1])); err == nil {
			minor = m
		}
	}
	return fmt.Sprintf("sm_%d%d", major, minor)
}

func round1(v float64) float64 {
	return math.Round(v*10) / 10
}
