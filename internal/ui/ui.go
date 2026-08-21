package ui

import (
	"fmt"
	"os"
	"regexp"
	"strings"
	"unicode/utf8"

	"local/flux/internal/version"
)

type Color string

const (
	Reset Color = "\033[0m"
	Bold  Color = "\033[1m"
	Dim   Color = "\033[2m"

	Violet Color = "\033[38;5;141m"
	Lilac  Color = "\033[38;5;183m"
	Indigo Color = "\033[38;5;99m"
	Teal   Color = "\033[38;5;73m"
	Mint   Color = "\033[38;5;121m"
	Gold   Color = "\033[38;5;220m"
	Rose   Color = "\033[38;5;204m"
	Amber  Color = "\033[38;5;214m"
	Red    Color = "\033[31m"
	Line   Color = "\033[38;5;238m"
	InkDim Color = "\033[38;5;246m"
)

var ansiRE = regexp.MustCompile(`\x1b\[[0-9;]*m`)

func enabled() bool {
	if os.Getenv("FLUX_FORCE_COLOR") != "" || os.Getenv("ATELIER_FORCE_COLOR") != "" || os.Getenv("CLICOLOR_FORCE") != "" {
		return true
	}
	if os.Getenv("FLUX_NO_COLOR") != "" {
		return false
	}
	return true
}

func paint(c Color, s string) string {
	if !enabled() {
		return s
	}
	return string(c) + s + string(Reset)
}

func Accent(s string) string { return paint(Teal, s) }
func Good(s string) string {
	return paint(Mint, "●") + " " + paint(Bold, paint(Mint, strings.ToUpper(s)))
}
func Warn(s string) string {
	return paint(Amber, "●") + " " + paint(Bold, paint(Amber, strings.ToUpper(s)))
}
func Bad(s string) string {
	return paint(Rose, "✕") + " " + paint(Bold, paint(Rose, strings.ToUpper(s)))
}
func Soft(s string) string   { return paint(InkDim, s) }
func Strong(s string) string { return paint(Bold, s) }

func Header(title, subtitle string) {
	fmt.Println()
	fmt.Println(paint(Bold, paint(Violet, title)) + paint(Dim, "  "+subtitle))
	fmt.Println(paint(Indigo, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
}

func Wordmark() {
	fmt.Println(paint(Violet, "  ___ _    _   ___  __"))
	fmt.Println(paint(Lilac, " | __| |  | | | \\ \\/ /"))
	fmt.Println(paint(Indigo, " | _|| |__| |_| |>  < "))
	fmt.Println(paint(Teal, " |_| |____|____//_/\\_\\"))
}

func Banner() {
	fmt.Println()
	Wordmark()
	fmt.Println(paint(Bold, paint(Violet, "flux")) + " " + paint(Dim, version.Full()) + paint(Dim, " · BF16 local image forge"))
	fmt.Println(paint(Indigo, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
	fmt.Println(paint(Dim, "  Resident socket · prompt instruments · exposed HTTP · local-only FLUX.1-dev"))
}

func KV(key string, value any) {
	fmt.Printf("  %-18s %v\n", paint(InkDim, strings.ToUpper(key)), value)
}

func Step(label string) {
	fmt.Printf("  %s %s\n", paint(Gold, "⟐"), label)
}

func Progress(label, state string, current, total int, detail string) {
	if total < 1 {
		total = 1
	}
	if current < 0 {
		current = 0
	}
	if current > total {
		current = total
	}
	width := 30
	filled := int(float64(current) / float64(total) * float64(width))
	if filled > width {
		filled = width
	}
	bar := strings.Repeat("█", filled) + strings.Repeat("░", width-filled)
	pct := int(float64(current) / float64(total) * 100)
	stateText := State(state)
	if detail != "" {
		detail = "  " + paint(InkDim, detail)
	}
	fmt.Printf("\r  %s %-10s %s %3d%% %s/%s%s\033[K",
		paint(Gold, "⟐"),
		stateText,
		paint(Teal, bar),
		pct,
		paint(Bold, fmt.Sprintf("%d", current)),
		paint(Dim, fmt.Sprintf("%d", total)),
		detail,
	)
}

func ProgressDone() {
	fmt.Println()
}

func Badge(label string) string {
	return paint(Gold, "⟐ "+label)
}

func Rule() {
	fmt.Println(paint(Indigo, "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"))
}

func Pair(left, right string) {
	fmt.Printf("  %-26s %s\n", paint(Teal, left), paint(Dim, right))
}

func CommandLine(cmd string) {
	fmt.Println("  " + paint(Gold, "$ ") + paint(Bold, paint(Teal, cmd)))
}

func Capsule(title, meta, body, command string, color Color) {
	fmt.Printf("%s %s %s\n", paint(color, "◆"), paint(Bold, paint(color, title)), paint(Dim, meta))
	if body != "" {
		fmt.Println("  " + body)
	}
	if command != "" {
		CommandLine(command)
	}
}

func Suite(name string, color Color, commands []PairRow) {
	fmt.Println(paint(color, "▸ ") + paint(Bold, paint(color, name)))
	for index, row := range commands {
		branch := "├─"
		if index == len(commands)-1 {
			branch = "└─"
		}
		cmd := padVisible(paint(color, row.Left), 30)
		fmt.Printf("  %s %s %s\n", paint(color, branch), cmd, paint(Dim, row.Right))
	}
}

type PairRow struct {
	Left  string
	Right string
}

func State(value string) string {
	state := strings.ToLower(strings.TrimSpace(value))
	switch state {
	case "ok", "ready", "true", "online", "active", "validated", "complete", "done", "present":
		return paint(Bold, paint(Mint, value))
	case "unknown", "pending", "queued", "running", "partial", "warm", "starting", "planned":
		return paint(Bold, paint(Amber, value))
	case "fail", "failed", "false", "missing", "blocked", "stale", "down", "error":
		return paint(Bold, paint(Rose, value))
	case "resident", "loaded", "hot":
		return paint(Bold, paint(Rose, value))
	default:
		return paint(Teal, value)
	}
}

func Code(s string) string {
	return paint(Bold, paint(Gold, s))
}

func Tree(title, subtitle string, groups []TreeGroup) {
	Header(title, subtitle)
	for gi, group := range groups {
		color := group.Color
		if color == "" {
			color = []Color{Violet, Lilac, Indigo, Teal, Gold}[gi%5]
		}
		lastGroup := gi == len(groups)-1
		groupBranch := "├─"
		childPrefix := "│  "
		if lastGroup {
			groupBranch = "└─"
			childPrefix = "   "
		}
		fmt.Printf("%s %s %s\n", paint(color, groupBranch), paint(Bold, paint(color, group.Name)), paint(Dim, group.Detail))
		for ci, child := range group.Children {
			branch := "├─"
			if ci == len(group.Children)-1 {
				branch = "└─"
			}
			fmt.Printf("%s%s %s %s\n", paint(Line, childPrefix), paint(color, branch), paint(color, child.Left), paint(Dim, child.Right))
		}
	}
}

type TreeGroup struct {
	Name     string
	Detail   string
	Color    Color
	Children []PairRow
}

// VisibleLen counts the printable columns a string occupies: ANSI escapes are
// stripped, and what remains is counted in runes rather than bytes. Byte
// counting silently over-measured every cell containing a glyph like · or ×,
// which threw off column alignment wherever the palette's own separators were
// used.
func VisibleLen(value string) int {
	return utf8.RuneCountInString(ansiRE.ReplaceAllString(value, ""))
}

func padVisible(value string, width int) string {
	return value + strings.Repeat(" ", max(0, width-VisibleLen(value)))
}

func Usage() {
	Banner()
	Suite("setup", Mint, []PairRow{
		{"install", "symlink ./flux into ~/.local/bin/flux"},
		{"setup", "auto-install uv, create .venv, install dependencies"},
		{"doctor", "verify model files, CUDA/MPS, packages, BF16 headers"},
		{"accel", "inspect active and candidate acceleration backends"},
		{"bench", "benchmark socket backends and update auto-selection profile"},
		{"bench --dry-run", "show benchmark plan without starting worker"},
	})
	Suite("models", Violet, []PairRow{
		{"download", "fetch the FLUX.1-dev BF16 Diffusers snapshot"},
		{"download --dry", "show the fetch plan without downloading"},
		{"load", "start worker and preload FLUX into GPU memory"},
		{"load --preload=false", "start queue without loading the 32 GB model"},
		{"gpu", "show GPU memory, utilization, and active CUDA processes"},
		{"fleet", "inspect multi-GPU worker pool across detected devices"},
		{"ane", "manage strict ANE package registry and component conversion"},
		{"ane direct-capture", "capture direct-ANE denoiser block manifest"},
	})
	Suite("arcane", Lilac, []PairRow{
		{"arcane models", "every model on every hardware profile"},
		{"arcane profiles", "the hardware profiles side by side"},
		{"arcane provision --dry-run", "probe silicon, runtime, weights, tenants, surfaces"},
		{"arcane preflight", "delegate to arcane_pipeline.py preflight"},
		{"arcane surfaces --check", "verify the studio pages"},
		{"arcane drafts", "orbit geometry and which mode each draft suits"},
		{"arcane character|latent|scenes", "the three pipeline modes"},
	})
	Suite("applications", Rose, []PairRow{
		{"serve studio", "primary HTTP API and studio dashboard on :7861"},
		{"serve tea", "Tea living image garden & Stallion motion lab on :7861"},
		{"serve rosarium", "recovered visual museum (7,218 works) on :7862"},
		{"serve atlas", "Motion Atlas Sphere & agent console on :7870"},
		{"serve atelier", "Koyomi synthesis cockpit & prompt duels on :7860"},
		{"serve portal", "Influx Vision constellation index on :8898"},
		{"serve gallery", "live generation feed and archive on :7861/gallery"},
		{"remote", "call an exposed FLUX HTTP endpoint"},
	})
	Suite("actions", Gold, []PairRow{
		{"render \"prompt\"", "start/use resident socket and wait for image"},
		{"render --direct", "force one-shot Python generation"},
		{"render --async", "queue a job, starting worker if needed"},
		{"render --burst N", "seed fanout"},
		{"img2img --image file \"prompt\"", "second socket for FLUX image-to-image refinement"},
		{"img2img --image A --image2 B \"prompt\"", "composite two references into one img2img source"},
		{"img2img --warm", "start img2img socket without loading the model"},
		{"jobs", "summarize queued/running/done worker jobs"},
		{"jobs cancel <id>", "cancel a queued job or request cancellation"},
		{"jobs open latest", "open newest completed output"},
		{"jobs prune --keep 20", "remove old done/error/cancelled records"},
		{"stop", "stop the resident worker daemons"},
		{"pipeline \"subject\"", "safe dry-run multi-generation workflow"},
		{"muse \"subject\"", "generate a shot board of renderable lanes"},
		{"matrix \"subject\"", "creative style/mood/camera control board"},
		{"shape", "compose final prompt with style/mood/camera/light/etc."},
		{"spark", "six creative prompt mutations"},
		{"evolve \"subject\"", "prompt-side candidate generator"},
		{"recipes", "styles, moods, ratios, presets"},
		{"plan", "show exact render plan without running"},
		{"history", "show recent render ledger"},
	})
	Suite("config", Indigo, []PairRow{
		{"studio", "runtime posture, model paths, preset lanes"},
		{"usage", "real-world command examples & workflow patterns"},
		{"tree", "full command topology in Council-style branches"},
		{"architecture", "show CLI, socket, HTTP, tunnel, and backend flow"},
		{"colors", "palette and state sample"},
		{"anime", "anime.sakure.network project bridge"},
	})
	fmt.Println()
	fmt.Println(paint(Dim, "  Run `flux usage` or `flux examples` for real-world command invocations."))
	fmt.Println()
}

func Examples() {
	Header("usage", "real-world command examples & workflow patterns")
	Suite("rendering & forge", Teal, []PairRow{
		{"flux render \"glass cabin\" --preset hero", "starts/uses resident socket with hero preset"},
		{"flux render \"keyboard\" --preset object --direct", "force one-shot Python generation without socket"},
		{"flux render \"shrine\" --camera wide --light lantern --palette sakura", "fine-grained visual lens control"},
		{"flux img2img --image subject.png --image2 style.png \"single cohesive character\"", "image+image composite refinement"},
		{"flux remote render --url http://host:7861 \"glass cabin\" --wait", "generate through exposed HTTP endpoint"},
	})
	Suite("pipelines & exploration", Gold, []PairRow{
		{"flux pipeline \"forest shrine\" --mode anime", "multi-generation prompt exploration plan"},
		{"flux pipeline \"forest shrine\" --mode anime --run", "queue the multi-generation workflow"},
		{"flux matrix \"forest shrine\" --styles anime,noir --cameras wide,close", "generate creative matrix control board"},
		{"flux muse \"anime rain station\" --remote-url http://host:7861", "generate shot board with copy-safe commands"},
		{"flux evolve \"forest shrine\" --mode anime", "generate prompt candidates with word counts"},
		{"flux spark \"orange keyboard\"", "six creative prompt mutations"},
	})
	Suite("applications & serving", Rose, []PairRow{
		{"flux serve studio", "serve primary HTTP/WebSocket API & studio UI on :7861"},
		{"flux serve tea", "serve Tea living garden & Stallion motion lab on :7861"},
		{"flux serve rosarium", "serve recovered visual museum (7,218 works) on :7862"},
		{"flux serve atlas", "serve Motion Atlas Sphere on :7870"},
		{"flux serve atelier", "serve Atelier synthesis cockpit on :7860"},
		{"flux serve portal", "serve Influx Vision constellation portal on :8898"},
		{"flux serve gallery", "serve live generation archive on :7861/gallery"},
	})
	Suite("benchmarking & telemetry", Mint, []PairRow{
		{"flux bench --backends cuda,cpu --steps 8", "profile concrete backends through the socket"},
		{"flux bench --dry-run --backends cuda,cpu", "show benchmark plan without starting worker"},
		{"flux jobs --active", "inspect active queue with progress estimates"},
		{"flux gpu", "inspect NVIDIA and Torch CUDA compute state"},
		{"flux atelier studies flat-prompt-protocol", "view study details with source path"},
	})
	fmt.Println()
}

func Palette() {
	Header("colors", "Council-derived FLUX terminal palette")
	rows := []struct {
		Name  string
		Color Color
		Use   string
	}{
		{"violet", Violet, "kernel / primary headers"},
		{"lilac", Lilac, "secondary command layers"},
		{"indigo", Indigo, "runtime / rules"},
		{"teal", Teal, "forge / live command text"},
		{"mint", Mint, "ready / complete / present"},
		{"gold", Gold, "prompt / synthesis / highlights"},
		{"amber", Amber, "queued / running / planned"},
		{"rose", Rose, "error / hot resident state"},
		{"ink-dim", InkDim, "descriptions and metadata"},
	}
	for _, row := range rows {
		fmt.Printf("  %-14s %s\n", paint(row.Color, "● "+row.Name), paint(Dim, row.Use))
	}
	fmt.Println()
	Suite("states", Teal, []PairRow{
		{"present", State("present")},
		{"ready", State("ready")},
		{"queued", State("queued")},
		{"running", State("running")},
		{"done", State("done")},
		{"error", State("error")},
	})
}

// ---------------------------------------------------------------------------
// Layout primitives added for the Arcane surface.
//
// These are the Go half of a matched pair: arcane_log.py mirrors this exact
// vocabulary (Section / Table / Meter / Field / Note) with the same glyphs and
// the same alignment so the CLI and the Python daemons read as one system.
// Everything here is ANSI-aware through VisibleLen and honours enabled().
// ---------------------------------------------------------------------------

// Section prints a group heading lighter than Header: a coloured caret, a bold
// name, and a dim trailing detail. Suite uses the same caret for command lists.
func Section(name, detail string, color Color) {
	if color == "" {
		color = Violet
	}
	fmt.Println()
	line := paint(color, "▸ ") + paint(Bold, paint(color, name))
	if detail != "" {
		line += "  " + paint(Dim, detail)
	}
	fmt.Println(line)
}

// Column describes one Table column. Cells may already carry ANSI colour; the
// table never repaints them, it only measures and pads.
type Column struct {
	Title string
	Right bool
}

// Table prints an aligned grid with a dim uppercase header and a rule beneath
// it. Column widths are computed from the widest visible cell, so coloured and
// uncoloured cells line up identically.
func Table(columns []Column, rows [][]string) {
	if len(columns) == 0 {
		return
	}
	widths := make([]int, len(columns))
	for i, column := range columns {
		widths[i] = VisibleLen(column.Title)
	}
	for _, row := range rows {
		for i, cell := range row {
			if i >= len(widths) {
				continue
			}
			if w := VisibleLen(cell); w > widths[i] {
				widths[i] = w
			}
		}
	}

	head := make([]string, len(columns))
	for i, column := range columns {
		head[i] = padCell(paint(InkDim, strings.ToUpper(column.Title)), widths[i], column.Right)
	}
	fmt.Println("  " + strings.TrimRight(strings.Join(head, "  "), " "))

	total := 2 * (len(columns) - 1)
	for _, w := range widths {
		total += w
	}
	fmt.Println("  " + paint(Line, strings.Repeat("─", total)))

	for _, row := range rows {
		cells := make([]string, len(columns))
		for i, column := range columns {
			cell := ""
			if i < len(row) {
				cell = row[i]
			}
			cells[i] = padCell(cell, widths[i], column.Right)
		}
		fmt.Println("  " + strings.TrimRight(strings.Join(cells, "  "), " "))
	}
}

func padCell(value string, width int, right bool) string {
	pad := strings.Repeat(" ", max(0, width-VisibleLen(value)))
	if right {
		return pad + value
	}
	return value + pad
}

// Meter renders a proportional capacity bar. The fill colour is the load
// verdict: mint under 70%, amber under 90%, rose at or above it, and rose for
// anything that overflows the total.
func Meter(value, total float64, width int) string {
	if width < 4 {
		width = 4
	}
	ratio := 0.0
	if total > 0 {
		ratio = value / total
	}
	over := ratio > 1
	if ratio < 0 {
		ratio = 0
	}
	if ratio > 1 {
		ratio = 1
	}
	filled := int(ratio*float64(width) + 0.5)
	if filled > width {
		filled = width
	}
	if filled == 0 && value > 0 {
		filled = 1
	}
	color := Mint
	switch {
	case over || ratio >= 0.9:
		color = Rose
	case ratio >= 0.7:
		color = Amber
	}
	return paint(color, strings.Repeat("█", filled)) + paint(Line, strings.Repeat("░", width-filled))
}

// Capacity prints one labelled capacity row: a meter, the absolute figures, the
// percentage, and the headroom left. It is the house rendering for "this much
// of that card is spoken for".
func Capacity(label string, value, total float64, unit string) {
	pct := 0.0
	if total > 0 {
		pct = value / total * 100
	}
	headroom := total - value
	tail := paint(Dim, fmt.Sprintf("%.1f %s free", headroom, unit))
	if headroom < 0 {
		tail = paint(Rose, fmt.Sprintf("%.1f %s over", -headroom, unit))
	}
	fmt.Printf("  %-18s %s %s %s  %s\n",
		paint(InkDim, strings.ToUpper(label)),
		Meter(value, total, 28),
		paint(Bold, fmt.Sprintf("%6.1f", value))+paint(Dim, fmt.Sprintf(" / %.1f %s", total, unit)),
		paint(Dim, fmt.Sprintf("%5.1f%%", pct)),
		tail,
	)
}

// Field prints a key/value row that carries an explicit status verdict, so a
// reader can scan the left rail for anything that is not green. Status is one
// of ok, warn, fail, unknown, or skip; anything else renders through State.
func Field(key, status, detail string) {
	fmt.Printf("  %-18s %-22s %s\n", paint(InkDim, strings.ToUpper(key)), Verdict(status), paint(Dim, detail))
}

// Verdict paints a status token with the glyph its severity earns.
func Verdict(status string) string {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "ok", "ready", "present", "online", "pass":
		return Good(status)
	case "warn", "unknown", "partial", "degraded", "pending", "unavailable", "planned":
		return Warn(status)
	case "fail", "failed", "missing", "blocked", "not detected", "error":
		return Bad(status)
	case "skip", "skipped", "n/a":
		return paint(InkDim, "· ") + paint(Dim, strings.ToUpper(status))
	default:
		return State(status)
	}
}

// Note prints dim annotation lines under a block, indented past the rail.
func Note(lines ...string) {
	for _, line := range lines {
		if line == "" {
			continue
		}
		fmt.Println("  " + paint(Line, "└ ") + paint(Dim, line))
	}
}
