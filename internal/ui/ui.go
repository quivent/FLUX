package ui

import (
	"fmt"
	"os"
	"regexp"
	"strings"

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

func VisibleLen(value string) int {
	return len(ansiRE.ReplaceAllString(value, ""))
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
