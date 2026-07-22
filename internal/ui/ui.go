package ui

import (
	"fmt"
	"os"
	"regexp"
	"strings"
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
	fmt.Println(paint(Bold, paint(Violet, "flux")) + paint(Dim, "  BF16 local image forge"))
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
	Suite("kernel", Violet, []PairRow{
		{"install", "symlink ./flux into ~/.local/bin/flux"},
		{"setup", "create .venv and install Python deps"},
		{"doctor", "verify model files, MPS, packages, BF16 headers"},
		{"accel", "inspect active and candidate acceleration backends"},
		{"architecture", "show CLI, socket, HTTP, tunnel, and backend flow"},
		{"atelier studies", "FLUX.1-related Atelier research imported into the CLI"},
		{"anime productions", "anime.sakure.network project bridge"},
		{"ane", "manage strict ANE package registry and component conversion"},
		{"ane direct-capture", "capture direct-ANE denoiser block manifest"},
		{"bench", "benchmark socket backends and update auto-selection profile"},
		{"bench --dry-run", "show benchmark plan without starting worker"},
		{"studio", "runtime posture, model paths, preset lanes"},
		{"download", "print the lean HF CLI BF16 download command"},
		{"tree", "full command topology"},
		{"colors", "palette and state sample"},
	})
	Suite("runtime", Indigo, []PairRow{
		{"warm", "start worker and load FLUX into memory"},
		{"warm --preload=false", "start queue without loading the 32 GB model"},
		{"serve", "local HTTP API and dashboard backed by the worker socket"},
		{"serve --addr 0.0.0.0:7861", "expose HTTP API; requires a bearer token"},
		{"serve --addr 0.0.0.0:7861 --unsafe-no-auth", "expose HTTP API without auth"},
		{"gallery", "start the Atelier gallery server at /gallery"},
		{"gallery --open", "open the live gallery in the default browser"},
		{"remote", "call an exposed FLUX HTTP endpoint"},
		{"stop", "stop the resident worker"},
		{"jobs", "summarize queued/running/done worker jobs"},
		{"jobs cancel <id>", "cancel a queued job or request running-job cancellation"},
		{"jobs open latest", "open newest completed output"},
		{"jobs prune --keep 20", "remove old done/error/cancelled records"},
	})
	Suite("forge", Teal, []PairRow{
		{"render \"prompt\"", "start/use resident socket and wait"},
		{"render --direct", "force one-shot Python generation"},
		{"render --async", "queue a job, starting worker if needed"},
		{"img2img --image file \"prompt\"", "second socket for FLUX image-to-image refinement"},
		{"img2img --image A --image2 B \"prompt\"", "composite two references into one img2img source"},
		{"img2img --warm", "start img2img socket without loading the model"},
		{"muse \"subject\"", "generate a shot board of renderable lanes"},
		{"matrix \"subject\"", "creative style/mood/camera control board"},
		{"pipeline \"subject\"", "safe dry-run multi-generation workflow"},
		{"plan", "show exact render plan without running"},
		{"history", "show recent renders"},
	})
	Suite("prompt", Gold, []PairRow{
		{"recipes", "styles, moods, ratios, presets"},
		{"shape", "compose final prompt with style/mood/camera/light/etc."},
		{"spark", "six creative prompt mutations"},
		{"evolve \"subject\"", "prompt-side candidate generator; ANE slot planned"},
		{"muse --commands", "copy-safe render command board"},
	})
	fmt.Println()
	fmt.Println(paint(Dim, "  Examples"))
	Pair("flux render \"glass cabin\" --preset hero", "starts/uses resident socket")
	Pair("flux img2img --image subject.png --image2 style.png \"single cohesive character\"", "simple image+image to image")
	Pair("flux bench --backends mps,mlx --steps 8", "profile concrete backends through the socket")
	Pair("flux bench --dry-run --backends mps,mlx", "show benchmark plan only")
	Pair("flux atelier studies", "FLUX.1 study index from ~/Atelier")
	Pair("flux atelier studies flat-prompt-protocol", "study details with source path")
	Pair("flux anime productions", "anime.sakure.network project bridge")
	Pair("flux muse \"anime rain station\" --remote-url http://host:7861", "shot board with commands")
	Pair("flux matrix \"forest shrine\" --styles anime,noir --cameras wide,close", "creative lanes")
	Pair("flux pipeline \"forest shrine\" --mode anime", "multi-generation plan")
	Pair("flux pipeline \"forest shrine\" --mode anime --run", "queue the workflow")
	Pair("flux evolve \"forest shrine\" --mode anime", "prompt candidates with word counts")
	Pair("flux render \"shrine\" --camera wide --light lantern --palette sakura", "controlled creativity")
	Pair("flux jobs --active", "active queue with estimates")
	Pair("flux remote render --url http://host:7861 \"glass cabin\" --wait", "generate through exposed HTTP")
	Pair("flux render \"keyboard\" --preset object --direct", "force one-shot process")
	Pair("flux spark \"orange keyboard\"", "prompt exploration")
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
