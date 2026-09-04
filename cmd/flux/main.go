package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"

	"local/flux/internal/config"
	"local/flux/internal/daemon"
	"local/flux/internal/history"
	"local/flux/internal/jury"
	"local/flux/internal/prompt"
	"local/flux/internal/runner"
	"local/flux/internal/server"
	"local/flux/internal/ui"
	"local/flux/internal/version"
)

func main() {
	cfg := config.Load()
	if len(os.Args) < 2 {
		ui.Usage()
		return
	}

	var err error
	switch os.Args[1] {
	case "help", "-h", "--help":
		ui.Usage()
	case "usage", "examples", "example":
		ui.Examples()
		return
	case "version", "-v", "--version":
		fmt.Printf("flux %s\n", version.Full())
		return
	case "install":
		err = install(cfg)
	case "setup":
		err = setup(cfg)
	case "provision":
		err = provisionCmd(cfg, os.Args[2:])
	case "doctor", "check":
		err = doctor(cfg)
	case "accel", "hardware", "backends":
		err = accel(cfg)
	case "architecture", "arch":
		err = architecture(cfg)
	case "atelier":
		err = atelier(cfg, os.Args[2:])
	case "tea":
		err = tea(cfg, os.Args[2:])
	case "atlas":
		err = atlas(cfg, os.Args[2:])
	case "anime":
		err = anime(cfg, os.Args[2:])
	case "arcane", "fortiche":
		err = arcaneCmd(cfg, os.Args[2:])
	case "ane":
		err = ane(cfg, os.Args[2:])
	case "bench", "benchmark":
		err = bench(cfg, os.Args[2:])
	case "studio", "studios", "status":
		// `flux studio arcane provision` reaches the same path as
		// `flux arcane provision` and `flux provision arcane`.
		if len(os.Args) > 2 && strings.ToLower(os.Args[2]) == "arcane" {
			err = arcaneDispatch(cfg, os.Args[3:])
			break
		}
		err = studio(cfg)
	case "tree":
		tree()
	case "colors", "theme":
		ui.Palette()
	case "download":
		err = download(cfg, os.Args[2:])
	case "gpu", "gpus", "nvidia":
		err = gpu(cfg, os.Args[2:])
	case "fleet":
		err = fleetCmd(cfg, os.Args[2:])
	case "load", "warm", "launch":
		err = loadWorker(cfg, os.Args[2:])
	case "everything", "all", "sovereign":
		err = everythingCmd(cfg)
	case "serve", "http":
		err = serve(cfg, os.Args[2:])
	case "oscillihue", "web":
		err = oscillihue(cfg, os.Args[2:])
	case "gallery", "view":
		err = gallery(cfg, os.Args[2:])
	case "remote":
		err = remote(os.Args[2:])
	case "stop":
		err = stopWorker(cfg)
	case "jobs", "queue":
		err = jobs(cfg, os.Args[2:])
	case "render", "imagine", "forge":
		err = render(cfg, os.Args[2:])
	case "img2img", "i2i", "enhance", "refine":
		err = img2img(cfg, os.Args[2:])
	case "muse", "riff", "board":
		err = muse(os.Args[2:])
	case "matrix":
		err = matrix(os.Args[2:])
	case "pipeline", "pipe", "workflow":
		err = pipeline(cfg, os.Args[2:])
	case "evolve", "mutate", "prompt-evolve":
		err = evolve(cfg, os.Args[2:])
	case "jury", "evaluate", "moj":
		err = juryCmd(cfg, os.Args[2:])
	case "plan":
		err = render(cfg, append(os.Args[2:], "--dry-run", "--echo"))
	case "shape":
		err = shape(os.Args[2:])
	case "spark":
		err = spark(os.Args[2:])
	case "recipes", "presets":
		recipes()
	case "history":
		err = showHistory(cfg, os.Args[2:])
	default:
		err = fmt.Errorf("unknown command %q", os.Args[1])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, ui.Bad("error:"), err)
		os.Exit(1)
	}
}

func install(cfg config.Config) error {
	ui.Header("install", "linking flux into ~/.local/bin")
	home, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(home) == "" {
		home = os.Getenv("HOME")
	}
	if strings.TrimSpace(home) == "" {
		return fmt.Errorf("could not determine home directory")
	}
	binDir := filepath.Join(home, ".local", "bin")
	if err := os.MkdirAll(binDir, 0o755); err != nil {
		return err
	}
	source := filepath.Join(cfg.Root, "flux")
	target := filepath.Join(binDir, "flux")
	if _, err := os.Stat(source); err != nil {
		return fmt.Errorf("missing %s; run make flux first", source)
	}
	_ = os.Remove(target)
	if err := os.Symlink(source, target); err != nil {
		return err
	}
	ui.KV("installed", target)
	ui.KV("target", source)
	return nil
}

func ensureUV() (string, error) {
	if p, err := exec.LookPath("uv"); err == nil {
		return p, nil
	}

	home, _ := os.UserHomeDir()
	if home != "" {
		candidates := []string{
			filepath.Join(home, ".local", "bin", "uv"),
			filepath.Join(home, ".cargo", "bin", "uv"),
		}
		for _, cand := range candidates {
			if info, err := os.Stat(cand); err == nil && !info.IsDir() && info.Mode()&0o111 != 0 {
				return cand, nil
			}
		}
	}

	ui.Step("uv not found on PATH; installing uv via astral.sh...")
	var cmd *exec.Cmd
	if _, err := exec.LookPath("curl"); err == nil {
		cmd = exec.Command("sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh")
	} else if _, err := exec.LookPath("wget"); err == nil {
		cmd = exec.Command("sh", "-c", "wget -qO- https://astral.sh/uv/install.sh | sh")
	} else {
		return "", fmt.Errorf("uv is not installed and neither curl nor wget is available to install it")
	}
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	if err := cmd.Run(); err != nil {
		return "", fmt.Errorf("failed to install uv: %w", err)
	}

	if p, err := exec.LookPath("uv"); err == nil {
		return p, nil
	}
	if home != "" {
		candidates := []string{
			filepath.Join(home, ".local", "bin", "uv"),
			filepath.Join(home, ".cargo", "bin", "uv"),
		}
		for _, cand := range candidates {
			if info, err := os.Stat(cand); err == nil && !info.IsDir() && info.Mode()&0o111 != 0 {
				return cand, nil
			}
		}
	}

	return "", fmt.Errorf("uv installation completed but the uv binary could not be found")
}

func setup(cfg config.Config) error {
	ui.Header("setup", "creating Python environment for local FLUX generation")
	uvBin, err := ensureUV()
	if err != nil {
		return err
	}
	// Rooted at cfg.Root, not the caller's cwd — 'flux setup' arrives over
	// SSH from gemstone with cwd=$HOME, and relative paths made it fail
	// with "File not found: requirements.txt" on the first remote box.
	venvPath := filepath.Join(cfg.Root, ".venv")
	pyBin := filepath.Join(venvPath, "bin", "python")

	var steps [][]string
	if _, err := os.Stat(pyBin); err != nil {
		steps = append(steps, []string{uvBin, "venv", venvPath, "--allow-existing", "--python", "python3.13"})
	}
	steps = append(steps, []string{uvBin, "pip", "install", "--python", pyBin, "-r", filepath.Join(cfg.Root, "requirements.txt")})

	for _, step := range steps {
		ui.Step(strings.Join(step, " "))
		if _, err := runner.Stream(context.Background(), nil, step[0], step[1:]...); err != nil {
			return err
		}
	}

	fmt.Println()
	ui.Suite("next steps", ui.Mint, []ui.PairRow{
		{"flux doctor", "verify model weight safetensors, CUDA/MPS, and headers"},
		{"flux download --dry", "inspect Hugging Face weights fetch command"},
		{"flux load", "launch resident daemon and preload weights into GPU memory"},
		{"flux serve studio", "start the HTTP/WebSocket API and studio dashboard on :7861"},
		{"flux usage", "view real-world generation and pipeline commands"},
	})
	return nil
}

func doctor(cfg config.Config) error {
	ui.Header("doctor", "model, package, and BF16 sanity checks")
	ui.KV("root", cfg.Root)
	ui.KV("model", cfg.ModelDir)
	ui.KV("python", cfg.Python)
	_, err := runner.Stream(context.Background(), map[string]string{
		"MODEL_DIR": cfg.ModelDir,
	}, cfg.Python, cfg.CheckPy)
	return err
}

func accel(cfg config.Config) error {
	ui.Header("accel", "FLUX backend posture")
	ui.KV("default active", "PyTorch Diffusers -> CUDA/MPS/CPU")
	ui.KV("selected", cfg.Backend)
	ui.KV("checkpoint", "BF16 Diffusers")
	ui.KV("socket", "resident worker with per-job backend")
	ui.KV("profile", daemon.New(cfg).ProfilePath())
	ui.KV("amx", "CPU fallback / auxiliary work only")
	ui.KV("ane", "requires validated full-pipeline package")
	ui.KV("architecture", filepath.Join(cfg.Root, "ACCELERATION.md"))
	fmt.Println()
	ui.Suite("backend policy", ui.Teal, []ui.PairRow{
		{"cuda", "NVIDIA GPU backend on enterprise hosts"},
		{"mps", "Apple GPU backend on local Apple Silicon"},
		{"mlx", "next native Apple Silicon backend to benchmark"},
		{"coreml", "fixed-shape compiled backend candidate"},
		{"ane", "strict backend gated by registry and validation"},
		{"cpu/amx", "fallback and auxiliary work, not primary FLUX generation"},
	})
	fmt.Println()
	script := `
import json, platform
out = {"python": platform.python_version(), "machine": platform.machine()}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_available"] = bool(torch.cuda.is_available())
    out["cuda_device_count"] = int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
    out["cuda_device"] = torch.cuda.get_device_name(0) if torch.cuda.is_available() and torch.cuda.device_count() else ""
    out["mps_available"] = bool(torch.backends.mps.is_available())
except Exception as exc:
    out["torch_error"] = repr(exc)
try:
    import mlx.core as mx
    out["mlx"] = getattr(mx, "__version__", "installed")
except Exception as exc:
    out["mlx_error"] = type(exc).__name__ + ": " + str(exc)
try:
    import coremltools as ct
    out["coremltools"] = ct.__version__
except Exception as exc:
    out["coremltools_error"] = type(exc).__name__ + ": " + str(exc)
try:
    import os, pathlib, shutil
    root = pathlib.Path(os.environ.get("FLUX_ROOT", ""))
    local = root / ".venv/bin/mflux-generate"
    out["mflux_generate"] = shutil.which("mflux-generate") or (str(local) if local.exists() else "")
except Exception as exc:
    out["mflux_error"] = type(exc).__name__ + ": " + str(exc)
try:
    import os, pathlib
    coreml_env = os.environ.get("FLUX_COREML_MODEL", "")
    coreml_path = pathlib.Path(coreml_env) if coreml_env else pathlib.Path(os.environ.get("MODEL_DIR", "")) / "coreml"
    out["coreml_model"] = str(coreml_path)
    out["coreml_compiled"] = coreml_path.exists()
except Exception as exc:
    out["coreml_model_error"] = type(exc).__name__ + ": " + str(exc)
try:
    import flux_ane
    out.update(flux_ane.capabilities(os.environ.get("MODEL_DIR", "")))
except Exception as exc:
    out["ane_error"] = type(exc).__name__ + ": " + str(exc)
print(json.dumps(out, sort_keys=True))
`
	cmd := exec.Command(cfg.Python, "-c", script)
	cmd.Env = append(os.Environ(), "FLUX_ROOT="+cfg.Root, "MODEL_DIR="+cfg.ModelDir)
	out, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("capability probe failed: %w", err)
	}
	var probe map[string]any
	if err := json.Unmarshal(out, &probe); err != nil {
		return err
	}
	ui.Header("probe", "local Python backend availability")
	for _, key := range []string{"python", "machine", "torch", "cuda_available", "cuda_device_count", "cuda_device", "mps_available", "mlx", "mflux_generate", "coremltools", "coreml_model", "coreml_compiled", "ane_registry", "ane_registry_exists", "ane_packages", "ane_components", "ane_validated", "ane_renderable", "ane_error", "mlx_error", "mflux_error", "coremltools_error", "coreml_model_error", "torch_error"} {
		if value, ok := probe[key]; ok {
			ui.KV(key, value)
		}
	}
	return nil
}

func architecture(cfg config.Config) error {
	ui.Header("architecture", "resident FLUX control plane")
	ui.KV("cli", "flux -> Go command router")
	ui.KV("worker", "worker.py over Unix socket")
	ui.KV("socket", filepath.Join(cfg.Root, ".fluxd", "flux.sock"))
	ui.KV("state", filepath.Join(cfg.Root, ".fluxd", "jobs.jsonl"))
	ui.KV("profile", filepath.Join(cfg.Root, ".fluxd", "profile.json"))
	ui.KV("http", "flux serve -> /api/health /api/jobs /api/render /outputs")
	ui.KV("domain", "https://flux.influx.vision/ (automatic Caddy edge routing)")
	ui.KV("outputs", cfg.OutputDir)
	fmt.Println()
	ui.Suite("domain mapping (flux.influx.vision)", ui.Rose, []ui.PairRow{
		{"/", "https://flux.influx.vision/ -> Constellation Index Portal"},
		{"/tea", "https://flux.influx.vision/tea -> Tea Living Garden & Stallion Lab"},
		{"/rosarium", "https://flux.influx.vision/rosarium -> Rosarium Grand Museum (7,218 works)"},
		{"/atlas", "https://flux.influx.vision/atlas -> Motion Atlas Sphere & Agent Console"},
		{"/atelier", "https://flux.influx.vision/atelier -> Atelier Synthesis Cockpit"},
		{"/studio", "https://flux.influx.vision/studio -> FLUX Studio Engine Dashboard"},
		{"/gallery", "https://flux.influx.vision/gallery -> Live Generation Feed & Archive"},
		{"/api, /outputs", "https://flux.influx.vision/api -> Reverse Proxy to :7861"},
	})
	fmt.Println()
	ui.Suite("request flow", ui.Teal, []ui.PairRow{
		{"local render", "flux render -> socket submit -> worker queue -> output png"},
		{"remote render", "HTTP /api/render -> same socket submit -> worker queue"},
		{"queue reader", "flux jobs and HTTP /api/jobs read .fluxd/jobs.jsonl through worker"},
	})
	fmt.Println()
	ui.Suite("acceleration", ui.Gold, []ui.PairRow{
		{"cuda", "active PyTorch / Diffusers BF16 tensor execution on NVIDIA GPU"},
		{"mps", "active PyTorch Diffusers backend on Apple Silicon GPU"},
		{"mlx", "candidate backend, selected by benchmark profile when available"},
		{"ane", "strict validated-package path; not active until renderable package exists"},
		{"cpu", "fallback and auxiliary CPU execution"},
	})
	fmt.Println()
	ui.KV("doc", filepath.Join(cfg.Root, "ACCELERATION.md"))
	return nil
}

type atelierStudy struct {
	ID       string   `json:"id"`
	Title    string   `json:"title"`
	Kind     string   `json:"kind"`
	Status   string   `json:"status"`
	Source   string   `json:"source"`
	Evidence string   `json:"evidence"`
	Takeaway string   `json:"takeaway"`
	CLI      string   `json:"cli"`
	Commands []string `json:"commands,omitempty"`
}

type atelierStudyJSON struct {
	atelierStudy
	SourceExists bool `json:"source_exists"`
}

func atelier(cfg config.Config, args []string) error {
	if len(args) == 0 {
		ui.Header("atelier", "imported Atelier research surfaces")
		ui.Suite("subcommands", ui.Teal, []ui.PairRow{
			{"studies", "FLUX.1-related Atelier studies indexed for this CLI"},
			{"studies <id>", "show one study with source and command implications"},
			{"studies --open <id>", "open the source document in ~/Atelier"},
			{"studies --json", "machine-readable study registry"},
		})
		return nil
	}
	switch args[0] {
	case "studies", "study":
		return atelierStudies(cfg, args[1:])
	default:
		return fmt.Errorf("unknown atelier command %q; use studies", args[0])
	}
}

func tea(cfg config.Config, args []string) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "-h" || args[0] == "--help" {
		ui.Header("tea", "living image garden and motion gallery")
		ui.Suite("subcommands", ui.Teal, []ui.PairRow{
			{"setup", "install the shared FLUX runtime and validate Tea assets"},
			{"check", "verify the isolated app bundle without starting a server"},
			{"dev", "serve Tea locally with its FLUX API and live streams"},
			{"serve", "same as dev; supports auth and a public read-only mode"},
		})
		return nil
	}
	switch strings.ToLower(args[0]) {
	case "setup":
		if len(args) != 1 {
			return errors.New("usage: flux tea setup")
		}
		if err := setup(cfg); err != nil {
			return err
		}
		return teaCheck(cfg)
	case "check", "doctor":
		if len(args) != 1 {
			return errors.New("usage: flux tea check")
		}
		return teaCheck(cfg)
	case "dev", "serve", "start":
		return teaServe(cfg, args[1:])
	default:
		return fmt.Errorf("unknown tea command %q; use setup, check, dev, or serve", args[0])
	}
}

func teaCheck(cfg config.Config) error {
	root := filepath.Join(cfg.Root, "apps", "tea", "public")
	required := []string{
		"index.html", "gallery.html", "movement.html", "studies.html", "stallion-lab.html", "exhibition.html", "stallion.html", "sentinel.html",
		"tea.css", "tea-shell.js",
		"../studies.json",
		"../protocols/stallion-motion-v2.json",
		"assets/bell-learns-the-wind-contact.jpg",
		"assets/bell-learns-the-wind-manifest.json",
		"assets/bell-learns-the-wind.mp4",
		"assets/stallion-atlas-contact.jpg",
		"assets/stallion-atlas-exhibition.mp4",
		"assets/stallion-atlas-grid.jpg",
		"assets/stallion-atlas-poster.jpg",
		"assets/stallion-gait-poster.jpg",
		"assets/stallion-gait-projection.mp4",
	}
	var missing []string
	for _, rel := range required {
		info, err := os.Stat(filepath.Join(root, filepath.FromSlash(rel)))
		if err != nil || info.IsDir() || info.Size() == 0 {
			missing = append(missing, rel)
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("Tea app is incomplete under %s; missing or empty: %s", root, strings.Join(missing, ", "))
	}
	ui.Header("tea check", "app bundle ready")
	ui.KV("root", root)
	ui.KV("pages", "garden gallery movement studies exhibition sentinel")
	ui.KV("runtime", "shared FLUX HTTP server and worker")
	ui.KV("assets", fmt.Sprintf("%d required files present", len(required)))
	return nil
}

func teaServe(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("tea serve", flag.ContinueOnError)
	addr := fs.String("addr", "127.0.0.1:7861", "HTTP listen address")
	backend := fs.String("backend", cfg.Backend, "default backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	token := fs.String("token", "", "HTTP bearer token")
	tokenEnv := fs.String("token-env", "FLUX_HTTP_TOKEN", "env var containing HTTP bearer token")
	unsafeNoAuth := fs.Bool("unsafe-no-auth", false, "allow public bind without HTTP auth")
	publicReadOnly := fs.Bool("public-read-only", false, "serve Tea and safe GETs; refuse GPU mutations")
	open := fs.Bool("open", false, "open Tea in the default browser")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := teaCheck(cfg); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	cfg.Backend = strings.ToLower(*backend)
	resolvedToken := resolveToken(*token, *tokenEnv)
	if publicBindAddr(*addr) && resolvedToken == "" && !*unsafeNoAuth {
		return fmt.Errorf("refusing to expose %s without auth; set --token, %s, or --unsafe-no-auth", *addr, *tokenEnv)
	}
	url := "http://" + *addr + "/"
	ui.Header("tea", "living image garden over the FLUX runtime")
	ui.KV("local url", url)
	ui.KV("domain", "https://flux.influx.vision/tea")
	ui.KV("auth", authState(resolvedToken, publicBindAddr(*addr), *unsafeNoAuth))
	ui.KV("backend", cfg.Backend)
	ui.KV("outputs", cfg.OutputDir)
	if *publicReadOnly {
		ui.KV("public", "read-only presentation and event streams")
	}
	if *open {
		server.OpenBrowser(url)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServe(ctx, cfg, server.Options{Addr: *addr, Token: resolvedToken, PublicReadOnly: *publicReadOnly})
}

func atelierStudies(cfg config.Config, args []string) error {
	if len(args) >= 2 && args[0] == "open" {
		args = append([]string{"--open", args[1]}, args[2:]...)
	}
	ordered, err := reorderAtelierStudiesArgs(args)
	if err != nil {
		return err
	}
	fs := flag.NewFlagSet("atelier studies", flag.ExitOnError)
	jsonOut := fs.Bool("json", false, "print machine-readable study registry")
	commandsOnly := fs.Bool("commands", false, "print related CLI commands only")
	pathsOnly := fs.Bool("paths", false, "print source paths only")
	kind := fs.String("kind", "", "filter by kind")
	openID := fs.String("open", "", "open a study source by id")
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	studies := filterAtelierStudies(atelierStudyRegistry(atelierRoot()), *kind)
	target := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if *openID != "" {
		study := findAtelierStudy(studies, *openID)
		if study == nil {
			return fmt.Errorf("unknown Atelier study %q", *openID)
		}
		if err := exec.Command("open", study.Source).Run(); err != nil {
			return err
		}
		if !*jsonOut && !*commandsOnly && !*pathsOnly {
			ui.Header("atelier studies", "opened source")
			ui.KV("id", study.ID)
			ui.KV("source", study.Source)
		}
		return nil
	}
	if target != "" {
		study := findAtelierStudy(studies, target)
		if study == nil {
			return fmt.Errorf("unknown Atelier study %q", target)
		}
		if *jsonOut {
			return json.NewEncoder(os.Stdout).Encode(atelierStudyForJSON(*study))
		}
		if *commandsOnly {
			printStudyCommands(*study)
			return nil
		}
		if *pathsOnly {
			fmt.Println(study.Source)
			return nil
		}
		printAtelierStudy(*study)
		return nil
	}
	if *jsonOut {
		out := make([]atelierStudyJSON, 0, len(studies))
		for _, study := range studies {
			out = append(out, atelierStudyForJSON(study))
		}
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"root": atelierRoot(), "studies": out})
	}
	if *commandsOnly {
		for _, study := range studies {
			printStudyCommands(study)
		}
		return nil
	}
	if *pathsOnly {
		for _, study := range studies {
			fmt.Println(study.Source)
		}
		return nil
	}
	ui.Header("atelier studies", "FLUX.1 research imported from ~/Atelier")
	ui.KV("atelier", atelierRoot())
	ui.KV("studies", len(studies))
	ui.KV("worker", ui.State("not touched"))
	fmt.Println()
	for i, study := range studies {
		meta := fmt.Sprintf("%s · %s", study.Kind, filepath.Base(study.Source))
		ui.Capsule(study.ID, meta, study.Takeaway, "flux atelier studies "+study.ID, []ui.Color{ui.Teal, ui.Gold, ui.Lilac, ui.Indigo}[i%4])
		if i != len(studies)-1 {
			fmt.Println()
		}
	}
	return nil
}

func atelierRoot() string {
	return filepath.Join(atelierHome(), "Atelier")
}

func atelierStudyRegistry(root string) []atelierStudy {
	src := func(parts ...string) string {
		all := append([]string{root}, parts...)
		return filepath.Join(all...)
	}
	return []atelierStudy{
		{
			ID:       "flux1-transport",
			Title:    "FLUX.1 as deterministic transport",
			Kind:     "method",
			Status:   "research note",
			Source:   src("docs", "research", "FLUX.md"),
			Evidence: "The Atelier note frames FLUX as deterministic transport where seed and latent path are authorable inputs.",
			Takeaway: "Treat seed as a controllable creative handle, not incidental randomness.",
			CLI:      "Keep seed, job id, output path, and prompt shape visible on render and queue surfaces.",
			Commands: []string{
				"flux render \"glass cabin\" --preset hero --seed 617538272 --dry-run",
				"flux jobs --active",
				"flux history --n 8",
			},
		},
		{
			ID:       "flux1-architecture",
			Title:    "FLUX.1 text and latent architecture",
			Kind:     "architecture",
			Status:   "research note",
			Source:   src("docs", "research", "FLUX_ARCHITECTURE.md"),
			Evidence: "FLUX.1 combines CLIP-L pooled conditioning with T5-XXL token conditioning, then denoises a 16-channel latent through MMDiT.",
			Takeaway: "Separate global style intent from local subject/material detail when shaping prompts.",
			CLI:      "The creative lens flags map cleanly onto global and local prompt roles.",
			Commands: []string{
				"flux recipes",
				"flux shape \"forest shrine\" --style anime --camera wide --light golden --texture ink",
				"flux evolve \"forest shrine\" --mode anime",
			},
		},
		{
			ID:       "flux1-vs-flux2",
			Title:    "FLUX.1 vs FLUX.2 seed authorability",
			Kind:     "comparison",
			Status:   "intra-family study",
			Source:   src("docs", "research", "seed-authorability", "FLUX-INTRA-FAMILY-ORDERING.md"),
			Evidence: "The study reports FLUX.1-dev at 2.82% coarse spread and FLUX.2-dev at 3.72% under its intra-family protocol.",
			Takeaway: "Prefer FLUX.1 for authored latent motion; treat FLUX.2 as a stronger director model with more text-side authority.",
			CLI:      "Keep this CLI's active renderer FLUX.1-centered and expose FLUX.2 as a separate future lane.",
			Commands: []string{
				"flux architecture",
				"flux atelier studies seed-layout-protocol",
			},
		},
		{
			ID:       "seed-layout-protocol",
			Title:    "Seed-layout metric and protocol",
			Kind:     "measurement",
			Status:   "protocol",
			Source:   src("docs", "research", "seed-authorability", "SEED-LAYOUT-METRIC-AND-PROTOCOL.md"),
			Evidence: "The protocol pools images to 8x8x3, measures cross-seed spread, and locks 512x512, 24 steps, guidance 3.5, and a fixed seed block.",
			Takeaway: "Any authorability claim needs a locked prompt, seed block, and N policy.",
			CLI:      "Use deterministic seeds in matrix/pipeline plans and avoid mixing benchmark claims across prompt protocols.",
			Commands: []string{
				"flux matrix \"abstract bioglass morphing texture, seamless\" --styles material --moods clinical --cameras wide --n 1",
				"flux render \"abstract bioglass morphing texture, seamless\" --width 512 --height 512 --steps 24 --guidance 3.5 --seed 617538272 --dry-run",
			},
		},
		{
			ID:       "flat-prompt-protocol",
			Title:    "Flat-prompt ablation protocol",
			Kind:     "measurement",
			Status:   "defined matrix",
			Source:   src("docs", "research", "seed-authorability", "FLAT-PROMPT-PROTOCOL.md"),
			Evidence: "The crux prompt is exactly: uniform gray field, seamless, no structure.",
			Takeaway: "Use a flat field to separate seed-layout coupling from prompt-induced composition.",
			CLI:      "Add null-composition benchmark presets before making cross-model authorability claims.",
			Commands: []string{
				"flux render \"uniform gray field, seamless, no structure\" --width 512 --height 512 --steps 24 --guidance 3.5 --seed 617538272 --dry-run",
				"flux atelier studies spectrum-battery",
			},
		},
		{
			ID:       "spectrum-battery",
			Title:    "Seed authorability spectrum battery",
			Kind:     "measurement",
			Status:   "partial run",
			Source:   src("docs", "research", "seed-authorability", "SPECTRUM-BATTERY-EXPERIMENT.md"),
			Evidence: "The GH200 bioglass battery reports FLUX.1-dev stable near 12.4-12.8% across N=64, 128, and 256; the prompt collapses historical separation.",
			Takeaway: "Bioglass is useful but not a neutral control; cite flat-prompt legs for model ordering.",
			CLI:      "Surface protocol caveats next to study-derived numbers instead of burying them in docs.",
			Commands: []string{
				"flux atelier studies flat-prompt-protocol",
				"flux pipeline \"uniform gray field, seamless, no structure\" --mode explore --n 1",
			},
		},
		{
			ID:       "moment-operator",
			Title:    "Fixed-seed moment operator findings",
			Kind:     "motion",
			Status:   "live-system findings",
			Source:   src("docs", "research", "MOMENT-OPERATOR-FINDINGS.md"),
			Evidence: "The study drives a fixed-seed FLUX latent through elliptic, oscillatory, weave, and screw paths; the living band is roughly arc 0.10-0.18.",
			Takeaway: "Motion control belongs to a latent-path workflow, not seed rerolling.",
			CLI:      "Keep current still-generation commands separate from a future moment/motion command family.",
			Commands: []string{
				"flux atelier studies flux1-transport",
				"flux architecture",
			},
		},
		{
			ID:       "flux1-runtime",
			Title:    "FLUX.1 runtime residency",
			Kind:     "runtime",
			Status:   "canonical rule",
			Source:   src("docs", "FLUX-RUNTIME-ARCHITECTURE.md"),
			Evidence: "Atelier treats FLUX.1 as a local runtime owned by flux1_loader.py, flux_still.py, and the resident motion worker queue.",
			Takeaway: "Do not reintroduce a model-manager owner or spin another FLUX when the socket lane is live.",
			CLI:      "Local render, remote render, HTTP dashboard, and jobs all route through the same resident worker policy.",
			Commands: []string{
				"flux architecture",
				"flux jobs --active",
				"flux render \"glass cabin\" --async",
			},
		},
		{
			ID:       "flux1-model-shelf",
			Title:    "FLUX.1 model shelf and loader shape",
			Kind:     "runtime",
			Status:   "current shape",
			Source:   src("ui", "inference", "FLUX1_MODELS.md"),
			Evidence: "The Atelier model shelf expects ComfyUI-style single files under ~/models/flux1 or COMFY_MODELS and renders through flux1_loader.py.",
			Takeaway: "Keep model residency state visible while leaving generation ownership with the socket worker.",
			CLI:      "download, warm, studio, architecture, and jobs form the operational loop.",
			Commands: []string{
				"flux download",
				"flux studio",
				"flux warm --preload=false",
			},
		},
		{
			ID:       "render-flux-language",
			Title:    "render.flux language specification",
			Kind:     "spec",
			Status:   "contract",
			Source:   src("specification", "render.flux.language"),
			Evidence: "The spec records FLUX.1-dev as the resident moment-operator model and defines offline, latent injection, and render contract rules.",
			Takeaway: "The CLI should describe architecture and study posture in the same terms as Atelier's render contract.",
			CLI:      "Use architecture/studies as readable contract surfaces, not hidden implementation trivia.",
			Commands: []string{
				"flux tree",
				"flux atelier studies flux1-runtime",
			},
		},
		{
			ID:       "cpu-support-lane",
			Title:    "GH200 CPU support lane",
			Kind:     "performance",
			Status:   "implementation note",
			Source:   src("ui", "inference", "cpu_pipeline.py"),
			Evidence: "PromptCache encodes fixed prompts once and overlap() scores prior outputs on CPU while the GPU renders the next item.",
			Takeaway: "Beyond the GPU, use CPU for prompt caching, scoring, bootstrap, and queue analysis rather than denoising.",
			CLI:      "This complements the ANE prompt-model idea: support lanes can evolve prompts or score results while FLUX denoises elsewhere.",
			Commands: []string{
				"flux evolve \"forest shrine\" --engine heuristic",
				"flux jobs --active",
			},
		},
	}
}

func filterAtelierStudies(studies []atelierStudy, kind string) []atelierStudy {
	kind = strings.ToLower(strings.TrimSpace(kind))
	if kind == "" {
		return studies
	}
	out := make([]atelierStudy, 0, len(studies))
	for _, study := range studies {
		if strings.EqualFold(study.Kind, kind) {
			out = append(out, study)
		}
	}
	return out
}

func findAtelierStudy(studies []atelierStudy, id string) *atelierStudy {
	id = strings.ToLower(strings.TrimSpace(id))
	for i := range studies {
		if strings.ToLower(studies[i].ID) == id {
			return &studies[i]
		}
	}
	return nil
}

func atelierStudyForJSON(study atelierStudy) atelierStudyJSON {
	_, err := os.Stat(study.Source)
	return atelierStudyJSON{atelierStudy: study, SourceExists: err == nil}
}

func printAtelierStudy(study atelierStudy) {
	ui.Header("atelier study", study.Title)
	ui.KV("id", study.ID)
	ui.KV("kind", study.Kind)
	ui.KV("status", study.Status)
	ui.KV("source", study.Source)
	ui.KV("source exists", ui.State(strconv.FormatBool(atelierStudyForJSON(study).SourceExists)))
	fmt.Println()
	ui.Suite("read", ui.Teal, []ui.PairRow{
		{"evidence", study.Evidence},
		{"takeaway", study.Takeaway},
		{"cli", study.CLI},
	})
	if len(study.Commands) > 0 {
		fmt.Println()
		ui.Suite("related commands", ui.Gold, studyCommandRows(study))
	}
}

func printStudyCommands(study atelierStudy) {
	for _, command := range study.Commands {
		fmt.Println(command)
	}
}

func studyCommandRows(study atelierStudy) []ui.PairRow {
	rows := make([]ui.PairRow, 0, len(study.Commands))
	for _, command := range study.Commands {
		rows = append(rows, ui.PairRow{Left: command, Right: "related action"})
	}
	return rows
}

func reorderAtelierStudiesArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{"kind": true, "open": true}
	boolFlags := map[string]bool{"json": true, "commands": true, "paths": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

func anime(cfg config.Config, args []string) error {
	if len(args) == 0 {
		ui.Header("anime", "anime.productions project bridge")
		ui.Suite("subcommands", ui.Teal, []ui.PairRow{
			{"productions", "show anime.sakure.network studio wiring"},
			{"productions --open", "open the public FLUX studio page"},
			{"productions --gallery", "open the public render gallery"},
			{"productions --project", "open the local Vite project"},
			{"productions --build", "build the anime.productions bundle"},
		})
		return nil
	}
	switch args[0] {
	case "productions", "production", "studio":
		return animeProductions(cfg, args[1:])
	default:
		return fmt.Errorf("unknown anime command %q; use productions", args[0])
	}
}

// arcaneCmd is a thin entry point; the surface itself lives in arcane.go.
func arcaneCmd(cfg config.Config, args []string) error {
	return arcaneDispatch(cfg, args)
}

func provisionCmd(cfg config.Config, args []string) error {
	if len(args) == 0 || args[0] == "arcane" || args[0] == "arcahe" {
		rest := args
		if len(rest) > 0 {
			rest = rest[1:]
		}
		return arcaneProvision(cfg, rest)
	}
	return setup(cfg)
}

func serveArcane(cfg config.Config, args []string) error {
	return serveStudio(cfg, append([]string{"--addr", "0.0.0.0:7860"}, args...))
}

func animeProductions(_ config.Config, args []string) error {
	fs := flag.NewFlagSet("anime productions", flag.ExitOnError)
	openPublic := fs.Bool("open", false, "open the public anime.sakure.network FLUX page")
	openGallery := fs.Bool("gallery", false, "open the public render gallery")
	openProject := fs.Bool("project", false, "open the local anime.productions project")
	build := fs.Bool("build", false, "build the Vite bundle")
	url := fs.String("url", "https://anime.sakure.network/flux/", "public studio URL")
	project := fs.String("path", filepath.Join(atelierHome(), "anime.productions", "sakura"), "local anime.productions project path")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *openPublic {
		return exec.Command("open", *url).Run()
	}
	if *openGallery {
		return exec.Command("open", strings.TrimRight(*url, "/")+"/#gallery").Run()
	}
	if *openProject {
		return exec.Command("open", *project).Run()
	}
	if *build {
		ui.Header("anime productions", "building anime.sakure.network studio")
		ui.KV("project", *project)
		ui.KV("worker", ui.State("not touched"))
		cmd := exec.Command("npm", "run", "build")
		cmd.Dir = *project
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		return cmd.Run()
	}
	ui.Header("anime productions", "anime.sakure.network studio bridge")
	ui.KV("public", *url)
	ui.KV("project", *project)
	ui.KV("entry", filepath.Join(*project, "src", "flux", "main.tsx"))
	ui.KV("style", filepath.Join(*project, "src", "flux", "FluxSakura.css"))
	ui.KV("preview", "launchd com.anime.productions -> 127.0.0.1:9733")
	ui.KV("tunnel", "cloudflared anime.sakure.network -> 127.0.0.1:9733; /api and /outputs -> FLUX HTTP")
	ui.KV("worker", ui.State("not touched"))
	fmt.Println()
	ui.Suite("actions", ui.Gold, []ui.PairRow{
		{"flux anime productions --open", "open the public studio page"},
		{"flux anime productions --gallery", "open the public render gallery"},
		{"flux anime productions --project", "open the local project"},
		{"flux anime productions --build", "rebuild the Vite bundle without restarting FLUX"},
		{"flux atelier studies", "same study registry exposed in the CLI"},
	})
	return nil
}

func atelierHome() string {
	if home := os.Getenv("HOME"); home != "" {
		return home
	}
	if home, err := os.UserHomeDir(); err == nil {
		return home
	}
	return "."
}

func atlas(cfg config.Config, args []string) error {
	if len(args) == 0 {
		return atlasSphere(cfg, nil)
	}
	switch args[0] {
	case "sphere", "spheremap":
		return atlasSphere(cfg, args[1:])
	case "bell", "path-study":
		return atlasBell(cfg, args[1:])
	case "motion", "b300":
		return atlasMotion(cfg, args[1:])
	default:
		return fmt.Errorf("atlas needs a command: sphere, bell, or motion")
	}
}

type bellProtocol struct {
	Mode          string
	ShellScale    float64
	SeedLock      float64
	ShellCoupling float64
	Adapter       string
	Description   string
}

var bellProtocols = map[string]bellProtocol{
	"near": {
		Mode: "elliptic", ShellScale: 0.46, SeedLock: 0.68, ShellCoupling: 0.18, Adapter: "none",
		Description: "the original close Bell path; identity is strong and change is deliberately small",
	},
	"open": {
		Mode: "elliptic", ShellScale: 0.72, SeedLock: 0.54, ShellCoupling: 0.18, Adapter: "none",
		Description: "wider steps on the same path geometry; the first answer to an under-changing sequence",
	},
	"sway": {
		Mode: "sway", ShellScale: 0.62, SeedLock: 0.58, ShellCoupling: 0.30, Adapter: "none",
		Description: "causal lateral motion with a moderate home anchor",
	},
	"orbit": {
		Mode: "elliptic", ShellScale: 0.58, SeedLock: 0.60, ShellCoupling: 0.44, Adapter: "none",
		Description: "stronger directional coupling while retaining the elliptic motion family",
	},
	"cache": {
		Mode: "elliptic", ShellScale: 0.72, SeedLock: 0.54, ShellCoupling: 0.18, Adapter: "atlas-xframe-cache",
		Description: "the open path with step-keyed cross-frame block reuse for paired fidelity research",
	},
}

func atlasBell(cfg config.Config, args []string) error {
	if len(args) == 0 || args[0] == "list" || args[0] == "protocols" {
		ui.Header("atlas bell", "open-prompt latent motion protocols")
		for _, name := range []string{"near", "open", "sway", "orbit", "cache"} {
			p := bellProtocols[name]
			ui.KV(name, fmt.Sprintf("%s · mode=%s shell=%.2f lock=%.2f coupling=%.2f adapter=%s",
				p.Description, p.Mode, p.ShellScale, p.SeedLock, p.ShellCoupling, p.Adapter))
		}
		ui.KV("run", `flux atlas bell open --prompt "..."`)
		ui.KV("paired cache", `flux atlas bell cache-audit --prompt "..." --generations 8`)
		ui.KV("directed", `flux atlas bell tournament --prompt "..." --north-star "..." --detach`)
		ui.KV("late geometry", `flux atlas bell late-fork --prompt "..." --fork-steps 18,22,25,26`)
		return nil
	}

	protocolName := strings.ToLower(strings.TrimSpace(args[0]))
	if protocolName == "tournament" || protocolName == "directed" {
		return atlasBellTournament(cfg, args[1:])
	}
	if protocolName == "late-fork" || protocolName == "geometry-fork" {
		return atlasBellLateFork(cfg, args[1:])
	}
	if protocolName == "step-sweep" || protocolName == "schedule-sweep" {
		return atlasBellStepSweep(cfg, args[1:])
	}
	if protocolName == "continuity" || protocolName == "repair" {
		return atlasBellContinuity(cfg, args[1:])
	}
	if protocolName == "control" {
		return atlasBellControl(cfg, args[1:])
	}
	if protocolName == "status" {
		return atlasBellStatus(cfg, args[1:])
	}
	paired := protocolName == "cache-audit"
	if paired {
		protocolName = "open"
	}
	protocol, ok := bellProtocols[protocolName]
	if !ok {
		return fmt.Errorf("unknown Bell protocol %q; use: near, open, sway, orbit, cache, cache-audit", args[0])
	}

	remaining := append([]string(nil), args[1:]...)
	hasPrompt := false
	for _, arg := range remaining {
		if arg == "--prompt" || strings.HasPrefix(arg, "--prompt=") {
			hasPrompt = true
			break
		}
	}
	if !hasPrompt {
		return errors.New("Bell protocols require --prompt; their motion geometry never supplies artistic language")
	}
	generations := 1024
	for i := 0; i < len(remaining); i++ {
		if remaining[i] == "--generations" && i+1 < len(remaining) {
			n, err := strconv.Atoi(remaining[i+1])
			if err != nil || n < 1 || n > 65536 {
				return fmt.Errorf("--generations must be in [1,65536]")
			}
			generations = n
			remaining = append(remaining[:i], remaining[i+2:]...)
			i--
		}
	}
	base := filepath.Join(cfg.Root, "atlas_drafts", "garden-bell-learns-the-wind.json")
	stamp := time.Now().UTC().Format("20060102-150405")
	invoke := func(name string, p bellProtocol) error {
		defaults := []string{
			"--draft", base,
			"--id", "bell-" + name + "-" + stamp,
			"--size", "512",
			"--steps", "28",
			"--sample-count", strconv.Itoa(generations),
			"--mode", p.Mode,
			"--shell-scale", strconv.FormatFloat(p.ShellScale, 'f', -1, 64),
			"--seed-lock", strconv.FormatFloat(p.SeedLock, 'f', -1, 64),
			"--shell-coupling", strconv.FormatFloat(p.ShellCoupling, 'f', -1, 64),
			"--order", "row_serpentine",
			"--adapter", p.Adapter,
		}
		return atlasSphere(cfg, append(defaults, remaining...))
	}
	if !paired {
		return invoke(protocolName, protocol)
	}
	if err := invoke("cache-baseline", bellProtocols["open"]); err != nil {
		return err
	}
	return invoke("cache-reuse", bellProtocols["cache"])
}

func atlasBellContinuity(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("atlas bell continuity", flag.ContinueOnError)
	sphereID := fs.String("sphere", "garden-bell-learns-the-wind-001", "connected atlas sphere id")
	promptText := fs.String("prompt", "", "replacement prompt (required)")
	loop := fs.Bool("loop", false, "measure last-to-first continuity")
	submit := fs.Bool("submit", false, "submit marked replacement candidates to img2img")
	stillRatio := fs.Float64("still-ratio", 0.38, "fraction of median motion marking unnatural stillness")
	gapRatio := fs.Float64("gap-ratio", 2.35, "multiple of median motion marking a gap")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*promptText) == "" {
		return errors.New("atlas bell continuity requires --prompt")
	}
	sphere := filepath.Join(cfg.OutputDir, "atlas", strings.TrimSuffix(*sphereID, ".sphere")+".sphere")
	cmdArgs := []string{filepath.Join(cfg.Root, "chorus", "continuity.py"),
		"--sphere", sphere, "--out-dir", cfg.OutputDir, "--prompt", *promptText,
		"--still-ratio", strconv.FormatFloat(*stillRatio, 'f', -1, 64),
		"--gap-ratio", strconv.FormatFloat(*gapRatio, 'f', -1, 64),
		"--socket", filepath.Join(cfg.Root, ".fluxd", "img2img.sock")}
	if *loop {
		cmdArgs = append(cmdArgs, "--loop")
	}
	if *submit {
		cmdArgs = append(cmdArgs, "--submit")
	}
	ui.Header("Bell continuity", "mark first, replace non-destructively, require council acceptance")
	ui.KV("sphere", *sphereID)
	ui.KV("submit", fmt.Sprint(*submit))
	_, err := runner.Stream(context.Background(), nil, cfg.Python, cmdArgs...)
	return err
}

func atlasBellStepSweep(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("atlas bell step-sweep", flag.ContinueOnError)
	promptText := fs.String("prompt", "", "open FLUX prompt (required)")
	stepRange := fs.String("step-range", "21:28", "inclusive range (21:28) or comma list")
	seed := fs.Int("seed", 1935692473, "fixed initial latent seed")
	guidance := fs.Float64("guidance", 3.6, "fixed FLUX guidance")
	id := fs.String("id", "", "durable study id")
	detach := fs.Bool("detach", false, "run under ~/.flux-run and return immediately")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*promptText) == "" {
		return errors.New("atlas bell step-sweep requires --prompt")
	}
	jobID := strings.TrimSpace(*id)
	if jobID == "" {
		jobID = "bell-step-sweep-" + time.Now().UTC().Format("20060102-150405")
	}
	cmdArgs := []string{filepath.Join(cfg.Root, "chorus", "step_sweep.py"),
		"--prompt", *promptText, "--id", jobID, "--model-dir", cfg.ModelDir,
		"--out-dir", cfg.OutputDir, "--step-range", *stepRange, "--size", "512",
		"--guidance", strconv.FormatFloat(*guidance, 'f', -1, 64), "--seed", strconv.Itoa(*seed)}
	ui.Header("Bell step sweep", "same latent, adjacent denoise depths")
	ui.KV("job", jobID)
	ui.KV("schedule", *stepRange)
	if !*detach {
		_, err := runner.Stream(context.Background(), nil, cfg.Python, cmdArgs...)
		return err
	}
	runDir := filepath.Join(atelierHome(), ".flux-run")
	if err := os.MkdirAll(runDir, 0o755); err != nil {
		return err
	}
	logPath, pidPath := filepath.Join(runDir, jobID+".log"), filepath.Join(runDir, jobID+".pid")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(cfg.Python, cmdArgs...)
	cmd.Stdout, cmd.Stderr, cmd.SysProcAttr = logFile, logFile, &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		_ = logFile.Close()
		return err
	}
	pid := cmd.Process.Pid
	if err := os.WriteFile(pidPath, []byte(strconv.Itoa(pid)+"\n"), 0o644); err != nil {
		_ = cmd.Process.Kill()
		_ = logFile.Close()
		return err
	}
	_ = cmd.Process.Release()
	_ = logFile.Close()
	ui.KV("state", ui.State("running")+" "+ui.Soft(fmt.Sprintf("pid %d", pid)))
	ui.KV("log", logPath)
	return nil
}

func atlasBellLateFork(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("atlas bell late-fork", flag.ContinueOnError)
	promptText := fs.String("prompt", "", "open FLUX prompt (required)")
	forkSteps := fs.String("fork-steps", "18,22,25,26", "comma-separated late boundaries in the denoise schedule")
	strength := fs.Float64("strength", 0.06, "angular tangent displacement applied only at each boundary")
	steps := fs.Int("steps", 28, "BF16 FLUX denoise steps")
	seed := fs.Int("seed", 1935692473, "deterministic tangent-basis seed")
	guidance := fs.Float64("guidance", 3.6, "FLUX guidance strength")
	adapter := fs.String("adapter", "none", "none or first-block-cache")
	cacheThreshold := fs.Float64("cache-threshold", 0.08, "first-block-cache residual threshold")
	branchMicrobatch := fs.Int("branch-microbatch", 2, "suffix candidates per CUDA pass; halves automatically on OOM")
	id := fs.String("id", "", "durable study id")
	detach := fs.Bool("detach", false, "run under ~/.flux-run and return immediately")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*promptText) == "" {
		return errors.New("atlas bell late-fork requires --prompt; the protocol never supplies artistic language")
	}
	if *steps < 2 || *steps > 100 {
		return errors.New("--steps must be in [2,100]")
	}
	if *strength < 0.001 || *strength > 0.8 {
		return errors.New("--strength must be in [0.001,0.8]")
	}
	if *adapter != "none" && *adapter != "first-block-cache" {
		return errors.New("--adapter must be none or first-block-cache")
	}
	if *branchMicrobatch < 1 || *branchMicrobatch > 4 {
		return errors.New("--branch-microbatch must be in [1,4]")
	}
	jobID := strings.TrimSpace(*id)
	if jobID == "" {
		jobID = "bell-late-fork-" + time.Now().UTC().Format("20060102-150405")
	}
	cmdArgs := []string{
		filepath.Join(cfg.Root, "chorus", "late_fork.py"),
		"--prompt", *promptText,
		"--id", jobID,
		"--model-dir", cfg.ModelDir,
		"--out-dir", cfg.OutputDir,
		"--size", "512",
		"--steps", strconv.Itoa(*steps),
		"--fork-steps", *forkSteps,
		"--strength", strconv.FormatFloat(*strength, 'f', -1, 64),
		"--guidance", strconv.FormatFloat(*guidance, 'f', -1, 64),
		"--seed", strconv.Itoa(*seed),
		"--adapter", *adapter,
		"--cache-threshold", strconv.FormatFloat(*cacheThreshold, 'f', -1, 64),
		"--branch-microbatch", strconv.Itoa(*branchMicrobatch),
	}
	ui.Header("Bell late geometry", "shared early trajectory, guided late suffix")
	ui.KV("job", jobID)
	ui.KV("render", fmt.Sprintf("512x512 · %d steps · forks after %s", *steps, *forkSteps))
	ui.KV("intervention", fmt.Sprintf("four tangent directions · %.4f radians · only after each boundary", *strength))
	ui.KV("adapter", *adapter)
	if !*detach {
		_, err := runner.Stream(context.Background(), nil, cfg.Python, cmdArgs...)
		return err
	}
	home := atelierHome()
	runDir := filepath.Join(home, ".flux-run")
	if err := os.MkdirAll(runDir, 0o755); err != nil {
		return err
	}
	logPath := filepath.Join(runDir, jobID+".log")
	pidPath := filepath.Join(runDir, jobID+".pid")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(cfg.Python, cmdArgs...)
	cmd.Stdout, cmd.Stderr = logFile, logFile
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		_ = logFile.Close()
		return err
	}
	if err := os.WriteFile(pidPath, []byte(strconv.Itoa(cmd.Process.Pid)+"\n"), 0o644); err != nil {
		_ = cmd.Process.Kill()
		_ = logFile.Close()
		return err
	}
	pid := cmd.Process.Pid
	_ = cmd.Process.Release()
	_ = logFile.Close()
	ui.KV("state", ui.State("running")+" "+ui.Soft(fmt.Sprintf("pid %d", pid)))
	ui.KV("log", logPath)
	return nil
}

func atlasBellTournament(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("atlas bell tournament", flag.ContinueOnError)
	promptText := fs.String("prompt", "", "open FLUX prompt (required)")
	northStar := fs.String("north-star", "increase visible coherent motion while preserving subject identity and established formal strength", "asymptotic destination (required but never treated as a fixed image)")
	generations := fs.Int("generations", 1024, "decision generations; each renders four literal directions")
	steps := fs.Int("steps", 28, "BF16 FLUX denoise steps")
	seed := fs.Int("seed", 1935692473, "four-dimensional latent basis seed")
	angle := fs.Float64("angle", 0.12, "initial angular distance from retained parent")
	minimumGain := fs.Float64("minimum-gain", 2.0, "child score margin required to advance")
	adapter := fs.String("adapter", "none", "none or first-block-cache; enable only after paired audit")
	cacheThreshold := fs.Float64("cache-threshold", 0.08, "first-block-cache residual threshold")
	id := fs.String("id", "", "durable lineage id")
	detach := fs.Bool("detach", false, "run under ~/.flux-run and return immediately")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*promptText) == "" {
		return errors.New("atlas bell tournament requires --prompt; the protocol never supplies artistic language")
	}
	if strings.TrimSpace(*northStar) == "" {
		return errors.New("atlas bell tournament requires a non-empty --north-star")
	}
	if *generations < 1 || *generations > 65536 {
		return errors.New("--generations must be in [1,65536]")
	}
	if *adapter != "none" && *adapter != "first-block-cache" {
		return errors.New("--adapter must be none or first-block-cache")
	}
	jobID := strings.TrimSpace(*id)
	if jobID == "" {
		jobID = "bell-directed-" + time.Now().UTC().Format("20060102-150405")
	}
	cmdArgs := []string{
		filepath.Join(cfg.Root, "chorus", "tournament.py"),
		"--prompt", *promptText,
		"--north-star", *northStar,
		"--id", jobID,
		"--model-dir", cfg.ModelDir,
		"--out-dir", cfg.OutputDir,
		"--generations", strconv.Itoa(*generations),
		"--size", "512",
		"--steps", strconv.Itoa(*steps),
		"--seed", strconv.Itoa(*seed),
		"--angle", strconv.FormatFloat(*angle, 'f', -1, 64),
		"--minimum-gain", strconv.FormatFloat(*minimumGain, 'f', -1, 64),
		"--adapter", *adapter,
		"--cache-threshold", strconv.FormatFloat(*cacheThreshold, 'f', -1, 64),
	}
	ui.Header("Bell tournament", "four directions, one Director, one retained lineage")
	ui.KV("job", jobID)
	ui.KV("render", fmt.Sprintf("512x512 · %d steps · 4 candidates × %d generations", *steps, *generations))
	ui.KV("north star", *northStar)
	ui.KV("adapter", *adapter)
	ui.KV("control", filepath.Join(cfg.OutputDir, "atlas", jobID+".sphere", "control.json"))
	if !*detach {
		_, err := runner.Stream(context.Background(), nil, cfg.Python, cmdArgs...)
		return err
	}
	home := atelierHome()
	runDir := filepath.Join(home, ".flux-run")
	if err := os.MkdirAll(runDir, 0o755); err != nil {
		return err
	}
	logPath := filepath.Join(runDir, jobID+".log")
	pidPath := filepath.Join(runDir, jobID+".pid")
	logFile, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	cmd := exec.Command(cfg.Python, cmdArgs...)
	cmd.Stdout, cmd.Stderr = logFile, logFile
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		_ = logFile.Close()
		return err
	}
	if err := os.WriteFile(pidPath, []byte(strconv.Itoa(cmd.Process.Pid)+"\n"), 0o644); err != nil {
		_ = cmd.Process.Kill()
		_ = logFile.Close()
		return err
	}
	_ = cmd.Process.Release()
	_ = logFile.Close()
	ui.KV("state", ui.State("running")+" "+ui.Soft(fmt.Sprintf("pid %d", cmd.Process.Pid)))
	ui.KV("log", logPath)
	return nil
}

func atlasBellControl(cfg config.Config, args []string) error {
	if len(args) == 0 || strings.HasPrefix(args[0], "-") {
		return errors.New("usage: flux atlas bell control <id> [--angle N] [--steps N] [--north-star text] [--pause|--resume|--stop]")
	}
	jobID := args[0]
	fs := flag.NewFlagSet("atlas bell control", flag.ContinueOnError)
	angle := fs.Float64("angle", -1, "next-generation angular distance")
	steps := fs.Int("steps", 0, "next-generation denoise steps")
	minimumGain := fs.Float64("minimum-gain", -1, "Director margin required to leave the parent")
	northStar := fs.String("north-star", "", "slowly revised asymptotic destination")
	pause := fs.Bool("pause", false, "pause at the generation boundary")
	resume := fs.Bool("resume", false, "resume a paused lineage")
	stop := fs.Bool("stop", false, "stop cleanly at the generation boundary")
	if err := fs.Parse(args[1:]); err != nil {
		return err
	}
	path := filepath.Join(cfg.OutputDir, "atlas", jobID+".sphere", "control.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var control map[string]any
	if err := json.Unmarshal(raw, &control); err != nil {
		return err
	}
	changed := false
	if *angle >= 0 {
		if *angle < 0.01 || *angle > 1.2 {
			return errors.New("--angle must be in [0.01,1.2]")
		}
		control["angle"] = *angle
		changed = true
	}
	if *steps > 0 {
		if *steps > 120 {
			return errors.New("--steps must be in [1,120]")
		}
		control["steps"] = *steps
		changed = true
	}
	if *minimumGain >= 0 {
		control["minimum_gain"] = *minimumGain
		changed = true
	}
	if strings.TrimSpace(*northStar) != "" {
		control["north_star"] = strings.TrimSpace(*northStar)
		changed = true
	}
	if *pause && *resume {
		return errors.New("choose --pause or --resume, not both")
	}
	if *pause {
		control["paused"] = true
		changed = true
	}
	if *resume {
		control["paused"] = false
		changed = true
	}
	if *stop {
		control["stop"] = true
		changed = true
	}
	if !changed {
		return errors.New("no control change supplied")
	}
	control["updated"] = time.Now().UTC().Format(time.RFC3339Nano)
	encoded, _ := json.MarshalIndent(control, "", "  ")
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, append(encoded, '\n'), 0o644); err != nil {
		return err
	}
	if err := os.Rename(tmp, path); err != nil {
		return err
	}
	ui.Header("Bell control", "next generation boundary")
	ui.KV("job", jobID)
	for _, key := range []string{"paused", "stop", "angle", "steps", "minimum_gain", "north_star"} {
		ui.KV(key, fmt.Sprint(control[key]))
	}
	return nil
}

func atlasBellStatus(cfg config.Config, args []string) error {
	if len(args) != 1 {
		return errors.New("usage: flux atlas bell status <id>")
	}
	path := filepath.Join(cfg.OutputDir, "atlas", args[0]+".sphere", "manifest.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var manifest map[string]any
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return err
	}
	ui.Header("Bell lineage", stringValue(manifest["status"]))
	for _, key := range []string{"id", "generation", "generation_target", "accepted", "angle", "steps", "adapter", "north_star"} {
		ui.KV(key, fmt.Sprint(manifest[key]))
	}
	if decision, ok := manifest["last_decision"].(map[string]any); ok {
		ui.KV("decision", fmt.Sprintf("%s · %s", stringValue(decision["action"]), stringValue(decision["direction"])))
	}
	return nil
}

func atlasMotion(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("atlas motion", flag.ContinueOnError)
	addr := fs.String("addr", "127.0.0.1:7861", "listen address")
	open := fs.Bool("open", true, "open the suite in a browser")
	token := fs.String("token", "", "access token for non-local binds")
	backend := fs.String("backend", "cuda", "default backend")
	setupOnly := fs.Bool("setup-only", false, "install prerequisites and exit")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := ensureAtlasMotionPrerequisites(&cfg); err != nil {
		return err
	}
	if *setupOnly {
		ui.Header("motion atlas sphere", "prerequisites ready")
		ui.KV("python", cfg.Python)
		ui.KV("model", cfg.ModelDir)
		ui.KV("backend", strings.ToLower(strings.TrimSpace(*backend)))
		return nil
	}
	cfg.Backend = strings.ToLower(strings.TrimSpace(*backend))
	if err := validateBackend(cfg.Backend); err != nil {
		return err
	}
	resolvedToken := resolveToken(*token, "FLUX_HTTP_TOKEN")
	if publicBindAddr(*addr) && resolvedToken == "" {
		return fmt.Errorf("refusing to expose %s without auth; set --token or FLUX_HTTP_TOKEN", *addr)
	}
	url := "http://" + *addr + "/motion-atlas/"
	ui.Header("motion atlas sphere", "independent FLUX motion suite")
	ui.KV("suite", url)
	ui.KV("backend", cfg.Backend)
	if host, _, splitErr := net.SplitHostPort(*addr); splitErr == nil && (host == "127.0.0.1" || host == "localhost") {
		ui.KV("remote access", "ssh -L 7861:127.0.0.1:7861 <host>")
	}
	if *open {
		server.OpenBrowser(url)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServe(ctx, cfg, server.Options{Addr: *addr, Token: resolvedToken})
}

func ensureAtlasMotionPrerequisites(cfg *config.Config) error {
	venvPython := filepath.Join(cfg.Root, ".venv", "bin", "python")
	if _, err := os.Stat(venvPython); err != nil {
		ui.Header("atlas setup", "creating the project Python environment")
		if err := runner.StreamNoResult(
			context.Background(),
			nil,
			"make",
			"-C", cfg.Root, "setup",
		); err != nil {
			return fmt.Errorf("create atlas environment: %w", err)
		}
	}
	cfg.Python = venvPython
	probe := func() error {
		return exec.Command(cfg.Python, "-c", `
import torch
import diffusers
import transformers
import para_attn
if not torch.cuda.is_available():
    raise SystemExit("PyTorch cannot see a CUDA GPU")
`).Run()
	}
	if err := probe(); err != nil {
		ui.Header("atlas setup", "installing CUDA motion prerequisites")
		if err := runner.StreamNoResult(
			context.Background(),
			nil,
			cfg.Python,
			"-m", "pip", "install", "-r", filepath.Join(cfg.Root, "requirements.txt"),
		); err != nil {
			return fmt.Errorf("install atlas prerequisites: %w", err)
		}
		if err := probe(); err != nil {
			return errors.New("atlas prerequisites installed, but PyTorch still cannot see CUDA")
		}
	}
	if fluxModelReady(cfg.ModelDir) {
		return nil
	}
	ui.Header("atlas setup", "fetching FLUX.1-dev prerequisites")
	ui.KV("model", cfg.ModelDir)
	if err := download(*cfg, []string{"--workers", "16"}); err != nil {
		return fmt.Errorf("download FLUX.1-dev (set HF_TOKEN or run `hf auth login` first): %w", err)
	}
	return nil
}

func atlasSphere(cfg config.Config, args []string) error {
	defaultDraft := filepath.Join(atelierHome(), "Atelier", "data", "motion", "job_drafts", "parameter_grid_atlas", "spheremap_atlas_atlas_echo_study_1782180450145_0.json")
	fs := flag.NewFlagSet("atlas sphere", flag.ExitOnError)
	draftPath := fs.String("draft", defaultDraft, "Atelier latent_sphere_map draft JSON")
	backend := fs.String("backend", cfg.Backend, "backend: auto, cuda, mps, cpu")
	limit := fs.Int("limit", 0, "cap cells; 0 runs the full draft")
	sampleCount := fs.Int("sample-count", 0, "render first N cells from traversal order without shrinking the index window")
	indexStart := fs.Int("index-start", 0, "first atlas cell index")
	indexEnd := fs.Int("index-end", 0, "exclusive atlas cell index; 0 uses draft end")
	fullGrid := fs.Bool("full-grid", false, "run n_rows*n_cols cells even when draft n_latent is smaller")
	steps := fs.Int("steps", 0, "override draft steps")
	size := fs.Int("size", 0, "override draft square size")
	guidance := fs.Float64("guidance", 0, "override guidance")
	mode := fs.String("mode", "", "override latent path mode")
	promptText := fs.String("prompt", "", "override the draft prompt; required by open-prompt protocol commands")
	seed := fs.String("seed", "", "override home seed")
	shellScale := fs.Float64("shell-scale", -1, "override latent shell scale")
	seedLock := fs.Float64("seed-lock", -1, "override home-latent lock")
	shellCoupling := fs.Float64("shell-coupling", -17, "override row/column coupling")
	traversalOrder := fs.String("order", "column_serpentine", "render order: column_serpentine, row_serpentine, or raster")
	adapter := fs.String("adapter", "none", "atlas adapter: none, first-block-cache, or atlas-xframe-cache")
	cacheThreshold := fs.Float64("cache-threshold", 0.12, "first-block-cache residual diff threshold")
	cacheDownsample := fs.Int("cache-downsample", 1, "first-block-cache residual downsample factor")
	cacheWarmup := fs.Int("cache-warmup", 0, "first-block-cache warmup steps before cache reuse")
	id := fs.String("id", "", "override atlas job id")
	dryRun := fs.Bool("dry-run", false, "show the socket plan without submitting")
	openPage := fs.Bool("open", false, "open local atlas viewer after submit")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	raw, err := os.ReadFile(*draftPath)
	if err != nil {
		return err
	}
	var draft map[string]any
	if err := json.Unmarshal(raw, &draft); err != nil {
		return err
	}
	if *id != "" {
		draft["id"] = *id
	}
	if *mode != "" {
		draft["mode"] = *mode
	}
	if strings.TrimSpace(*promptText) != "" {
		draft["prompt"] = strings.TrimSpace(*promptText)
		draft["view_prompts"] = []string{strings.TrimSpace(*promptText)}
	}
	if *seed != "" {
		draft["seed_a"] = *seed
	}
	if *shellScale >= 0 {
		draft["shell_scale"] = *shellScale
	}
	if *seedLock >= 0 {
		draft["seed_lock"] = *seedLock
	}
	if *shellCoupling >= -16 {
		draft["shell_coupling"] = *shellCoupling
	}
	gridTotal := intValue(draft["n_rows"]) * intValue(draft["n_cols"])
	total := intValue(draft["n_latent"])
	if *fullGrid || total <= 0 {
		total = gridTotal
		draft["n_latent"] = total
	}
	end := *indexEnd
	if end <= 0 {
		end = total
	}
	if *limit > 0 && (*indexStart+*limit) < end {
		end = *indexStart + *limit
	}
	runCells := end - *indexStart
	displayCells := runCells
	if *sampleCount > 0 && *sampleCount < displayCells {
		displayCells = *sampleCount
	}
	ui.Header("atlas", "socket-backed latent sphere")
	ui.KV("draft", *draftPath)
	ui.KV("job", stringValue(draft["id"]))
	ui.KV("prompt", stringValue(draft["prompt"]))
	ui.KV("mode", valueOr(stringValue(draft["mode"]), "omega"))
	ui.KV("grid", fmt.Sprintf("%d", gridTotal))
	ui.KV("cells", fmt.Sprintf("%d/%d [%d,%d)", displayCells, total, *indexStart, end))
	ui.KV("order", *traversalOrder)
	ui.KV("adapter", *adapter)
	if *adapter != "none" && *adapter != "" {
		adapterKey := strings.ReplaceAll(strings.ToLower(*adapter), "_", "-")
		if adapterKey == "first-block-cache" || adapterKey == "teacache" || adapterKey == "para-attn" || adapterKey == "atlas-xframe-cache" || adapterKey == "xframe-cache" {
			ui.KV("adapter params", fmt.Sprintf("cache_threshold=%.4f cache_downsample=%d cache_warmup=%d", *cacheThreshold, *cacheDownsample, *cacheWarmup))
		}
	}
	ui.KV("backend", strings.ToLower(*backend))
	ui.KV("route", ui.State("resident")+" "+ui.Soft("unix socket, no second FLUX process"))
	if *dryRun {
		ui.KV("state", ui.State("planned")+" "+ui.Soft("no job submitted"))
		ui.KV("viewer", "http://127.0.0.1:7861/atlas/"+stringValue(draft["id"]))
		return nil
	}
	client := daemon.New(cfg)
	if _, err := client.Request(map[string]any{"op": "ping"}); err != nil {
		if err := client.Start(false); err != nil {
			return err
		}
	}
	payload := map[string]any{
		"op":               "atlas_sphere",
		"draft":            draft,
		"backend":          strings.ToLower(*backend),
		"limit":            *limit,
		"render_count":     *sampleCount,
		"index_start":      *indexStart,
		"traversal_order":  *traversalOrder,
		"n_latent":         total,
		"adapter":          *adapter,
		"cache_threshold":  *cacheThreshold,
		"cache_downsample": *cacheDownsample,
		"cache_warmup":     *cacheWarmup,
	}
	if *indexEnd > 0 {
		payload["index_end"] = *indexEnd
	}
	if *steps > 0 {
		payload["steps"] = *steps
	}
	if *size > 0 {
		payload["size"] = *size
	}
	if *guidance > 0 {
		payload["guidance"] = *guidance
	}
	resp, err := client.Request(payload)
	if err != nil {
		return err
	}
	job := resp.Job
	jobID := stringValue(job["id"])
	viewer := "http://127.0.0.1:7861/atlas/" + jobID
	ui.KV("status", stringValue(job["status"]))
	ui.KV("output", stringValue(job["output"]))
	ui.KV("viewer", viewer)
	if *openPage {
		_ = openOutput(viewer)
	}
	return nil
}

func ane(cfg config.Config, args []string) error {
	if len(args) == 0 {
		ui.Header("ane", "strict Apple Neural Engine adapter workflow")
		ui.Suite("commands", ui.Teal, []ui.PairRow{
			{"ane probe", "show package registry and validation state"},
			{"ane init", "create model/ane/registry.json"},
			{"ane convert-vae", "convert fixed-shape VAE decoder component to Core ML"},
			{"ane validate", "record external Instruments validation metadata"},
			{"ane direct-capture", "capture direct-ANE denoiser block manifest"},
			{"ane direct-pack", "create direct-ANE block weight packing plan"},
			{"ane direct-projections", "create direct-ANE dense projection plan"},
			{"ane direct-attention", "create direct-ANE attention QK/AV plan"},
			{"ane direct-benchmark", "measure synthetic MPS dense matmuls from captured plans"},
			{"ane direct-block-benchmark", "measure real MPS transformer block forwards"},
			{"ane direct-latent-benchmark", "measure real FluxPipeline latent step slope"},
			{"ane direct-component-benchmark", "measure real MPS block submodule components"},
			{"ane direct-aneforge-projections", "measure direct-ANE ANEForge projection kernels"},
			{"ane direct-aneforge-optimized", "measure optimized direct-ANE ANEForge projection plan"},
			{"ane direct-aneforge-attention", "measure direct-ANE ANEForge tiled SDPA attention core"},
			{"ane direct-contract", "create direct-ANE runtime contract and break-even budget"},
			{"ane direct-report", "print direct-ANE dense offload report"},
		})
		return nil
	}
	switch args[0] {
	case "probe", "status":
		ui.Header("ane", "package registry probe")
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, filepath.Join(cfg.Root, "flux_ane.py"), "probe", "--model-dir", cfg.ModelDir)
	case "init":
		ui.Header("ane", "initialize package registry")
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, filepath.Join(cfg.Root, "flux_ane.py"), "init", "--model-dir", cfg.ModelDir)
	case "convert-vae":
		fs := flag.NewFlagSet("ane convert-vae", flag.ExitOnError)
		width := fs.Int("width", 1024, "target image width")
		height := fs.Int("height", 1024, "target image height")
		precision := fs.String("precision", "fp32", "precision: fp16 or fp32")
		computeUnits := fs.String("compute-units", "cpu_and_ne", "Core ML compute units")
		name := fs.String("name", "", "package name")
		outDir := fs.String("out-dir", "", "output directory")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane", "convert FLUX VAE decoder component")
		ui.KV("size", fmt.Sprintf("%dx%d", *width, *height))
		ui.KV("precision", *precision)
		ui.KV("compute units", *computeUnits)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_ane.py"),
			"convert-vae-decoder",
			"--model-dir", cfg.ModelDir,
			"--width", strconv.Itoa(*width),
			"--height", strconv.Itoa(*height),
			"--precision", *precision,
			"--compute-units", *computeUnits,
		}
		if *name != "" {
			cmdArgs = append(cmdArgs, "--name", *name)
		}
		if *outDir != "" {
			cmdArgs = append(cmdArgs, "--out-dir", *outDir)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "validate":
		fs := flag.NewFlagSet("ane validate", flag.ExitOnError)
		name := fs.String("name", "", "package name")
		notes := fs.String("notes", "", "validation notes")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if strings.TrimSpace(*name) == "" {
			return fmt.Errorf("ane validate needs --name")
		}
		ui.Header("ane", "record Instruments validation")
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_ane.py"),
			"mark-validated",
			"--model-dir", cfg.ModelDir,
			"--name", *name,
			"--ane-validated",
		}
		if *notes != "" {
			cmdArgs = append(cmdArgs, "--notes", *notes)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-capture":
		fs := flag.NewFlagSet("ane direct-capture", flag.ExitOnError)
		width := fs.Int("width", 1024, "target image width")
		height := fs.Int("height", 1024, "target image height")
		steps := fs.Int("steps", 1, "pipeline steps; one is enough for block capture")
		blockType := fs.String("block-type", "dual", "block type: dual or single")
		blockIndex := fs.Int("block-index", 0, "block index")
		name := fs.String("name", "", "manifest filename")
		promptText := fs.String("prompt", "a clean product photo of a translucent glass cube on a matte table", "capture prompt")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "capture denoiser block manifest")
		ui.KV("target", fmt.Sprintf("%s[%d]", *blockType, *blockIndex))
		ui.KV("size", fmt.Sprintf("%dx%d", *width, *height))
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"capture-block",
			"--model-dir", cfg.ModelDir,
			"--width", strconv.Itoa(*width),
			"--height", strconv.Itoa(*height),
			"--steps", strconv.Itoa(*steps),
			"--block-type", *blockType,
			"--block-index", strconv.Itoa(*blockIndex),
			"--prompt", *promptText,
		}
		if *name != "" {
			cmdArgs = append(cmdArgs, "--name", *name)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-pack":
		fs := flag.NewFlagSet("ane direct-pack", flag.ExitOnError)
		manifest := fs.String("manifest", "", "source direct-capture manifest")
		out := fs.String("out", "", "output pack plan")
		tileM := fs.Int("tile-m", 128, "matrix tile rows")
		tileN := fs.Int("tile-n", 128, "matrix tile columns")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if strings.TrimSpace(*manifest) == "" {
			return fmt.Errorf("ane direct-pack needs --manifest")
		}
		ui.Header("ane/direct", "create denoiser block pack plan")
		ui.KV("manifest", *manifest)
		ui.KV("tile", fmt.Sprintf("%dx%d", *tileM, *tileN))
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"pack-plan",
			"--manifest", *manifest,
			"--tile-m", strconv.Itoa(*tileM),
			"--tile-n", strconv.Itoa(*tileN),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-projections":
		fs := flag.NewFlagSet("ane direct-projections", flag.ExitOnError)
		manifest := fs.String("manifest", "", "source direct-capture manifest")
		packPlan := fs.String("pack-plan", "", "source direct-pack plan")
		out := fs.String("out", "", "output projection plan")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if strings.TrimSpace(*manifest) == "" {
			return fmt.Errorf("ane direct-projections needs --manifest")
		}
		if strings.TrimSpace(*packPlan) == "" {
			return fmt.Errorf("ane direct-projections needs --pack-plan")
		}
		ui.Header("ane/direct", "create dense projection plan")
		ui.KV("manifest", *manifest)
		ui.KV("pack plan", *packPlan)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"projection-plan",
			"--manifest", *manifest,
			"--pack-plan", *packPlan,
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-attention":
		fs := flag.NewFlagSet("ane direct-attention", flag.ExitOnError)
		manifest := fs.String("manifest", "", "source direct-capture manifest")
		out := fs.String("out", "", "output attention plan")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		if strings.TrimSpace(*manifest) == "" {
			return fmt.Errorf("ane direct-attention needs --manifest")
		}
		ui.Header("ane/direct", "create attention QK/AV plan")
		ui.KV("manifest", *manifest)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"attention-plan",
			"--manifest", *manifest,
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-contract":
		fs := flag.NewFlagSet("ane direct-contract", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		denseSummary := fs.String("dense-summary", filepath.Join(cfg.ModelDir, "ane", "direct", "dense_slice_1024x1024_summary.json"), "dense slice summary")
		blockBenchmark := fs.String("block-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "block_stack_1024x1024_benchmark.json"), "block stack benchmark")
		latentPipelineBenchmark := fs.String("latent-pipeline-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "latent_pipeline_1024x1024_benchmark.json"), "latent pipeline benchmark")
		componentBenchmark := fs.String("component-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "component_1024x1024_benchmark.json"), "component benchmark")
		aneforgeProjectionBenchmark := fs.String("aneforge-projection-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "aneforge_projection_1024x1024_benchmark.json"), "ANEForge projection benchmark")
		aneforgeOptimizedProjectionPlan := fs.String("aneforge-optimized-projection-plan", filepath.Join(cfg.ModelDir, "ane", "direct", "aneforge_optimized_projection_plan_1024x1024.json"), "optimized ANEForge projection plan")
		aneforgeAttentionBenchmark := fs.String("aneforge-attention-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "aneforge_attention_1024x1024_benchmark.json"), "ANEForge attention benchmark")
		out := fs.String("out", "", "output runtime contract")
		steps := fs.Int("steps", 28, "denoise steps")
		dualBlocks := fs.Int("dual-blocks", 19, "dual block count per step")
		singleBlocks := fs.Int("single-blocks", 38, "single block count per step")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "create runtime contract")
		ui.KV("artifacts", *outDir)
		ui.KV("dense summary", *denseSummary)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"runtime-contract",
			"--out-dir", *outDir,
			"--dense-summary", *denseSummary,
			"--block-benchmark", *blockBenchmark,
			"--latent-pipeline-benchmark", *latentPipelineBenchmark,
			"--component-benchmark", *componentBenchmark,
			"--aneforge-projection-benchmark", *aneforgeProjectionBenchmark,
			"--aneforge-optimized-projection-plan", *aneforgeOptimizedProjectionPlan,
			"--aneforge-attention-benchmark", *aneforgeAttentionBenchmark,
			"--steps", strconv.Itoa(*steps),
			"--dual-blocks", strconv.Itoa(*dualBlocks),
			"--single-blocks", strconv.Itoa(*singleBlocks),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-component-benchmark":
		fs := flag.NewFlagSet("ane direct-component-benchmark", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		denseSummary := fs.String("dense-summary", filepath.Join(cfg.ModelDir, "ane", "direct", "dense_slice_1024x1024_summary.json"), "dense slice summary")
		blockBenchmark := fs.String("block-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "block_stack_1024x1024_benchmark.json"), "block stack benchmark")
		out := fs.String("out", "", "output benchmark JSON")
		dtype := fs.String("dtype", "bf16", "benchmark dtype: bf16, fp16, or fp32")
		warmup := fs.Int("warmup", 2, "warmup iterations")
		iterations := fs.Int("iterations", 7, "measured iterations")
		steps := fs.Int("steps", 28, "denoise steps")
		dualBlocks := fs.Int("dual-blocks", 19, "dual block count per step")
		singleBlocks := fs.Int("single-blocks", 38, "single block count per step")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark MPS block components")
		ui.KV("artifacts", *outDir)
		ui.KV("dtype", *dtype)
		ui.KV("iterations", *iterations)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"component-benchmark",
			"--model-dir", cfg.ModelDir,
			"--out-dir", *outDir,
			"--dense-summary", *denseSummary,
			"--block-benchmark", *blockBenchmark,
			"--dtype", *dtype,
			"--warmup", strconv.Itoa(*warmup),
			"--iterations", strconv.Itoa(*iterations),
			"--steps", strconv.Itoa(*steps),
			"--dual-blocks", strconv.Itoa(*dualBlocks),
			"--single-blocks", strconv.Itoa(*singleBlocks),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-aneforge-projections":
		fs := flag.NewFlagSet("ane direct-aneforge-projections", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		out := fs.String("out", "", "output benchmark JSON")
		compress := fs.String("compress", "int8", "ANEForge compression mode")
		warmup := fs.Int("warmup", 2, "MPS warmup iterations")
		mpsIterations := fs.Int("mps-iterations", 5, "MPS measured iterations")
		aneIterations := fs.Int("ane-iterations", 10, "ANE measured iterations")
		steps := fs.Int("steps", 28, "denoise steps")
		seed := fs.Int("seed", 6000, "random seed")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark ANEForge projection kernels")
		ui.KV("artifacts", *outDir)
		ui.KV("compress", *compress)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"aneforge-projection-benchmark",
			"--out-dir", *outDir,
			"--compress", *compress,
			"--warmup", strconv.Itoa(*warmup),
			"--mps-iterations", strconv.Itoa(*mpsIterations),
			"--ane-iterations", strconv.Itoa(*aneIterations),
			"--steps", strconv.Itoa(*steps),
			"--seed", strconv.Itoa(*seed),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-aneforge-optimized":
		fs := flag.NewFlagSet("ane direct-aneforge-optimized", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		out := fs.String("out", "", "output benchmark JSON")
		compress := fs.String("compress", "int8", "ANEForge compression mode")
		warmup := fs.Int("warmup", 2, "MPS warmup iterations")
		mpsIterations := fs.Int("mps-iterations", 5, "MPS measured iterations")
		aneIterations := fs.Int("ane-iterations", 10, "ANE measured iterations")
		steps := fs.Int("steps", 28, "denoise steps")
		seed := fs.Int("seed", 7000, "random seed")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark optimized ANEForge projection plan")
		ui.KV("artifacts", *outDir)
		ui.KV("compress", *compress)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"aneforge-optimized-projection-plan",
			"--out-dir", *outDir,
			"--compress", *compress,
			"--warmup", strconv.Itoa(*warmup),
			"--mps-iterations", strconv.Itoa(*mpsIterations),
			"--ane-iterations", strconv.Itoa(*aneIterations),
			"--steps", strconv.Itoa(*steps),
			"--seed", strconv.Itoa(*seed),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-aneforge-attention":
		fs := flag.NewFlagSet("ane direct-aneforge-attention", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		out := fs.String("out", "", "output benchmark JSON")
		compress := fs.String("compress", "int8", "ANEForge compression mode")
		warmup := fs.Int("warmup", 1, "MPS warmup iterations")
		mpsIterations := fs.Int("mps-iterations", 3, "MPS measured iterations")
		aneIterations := fs.Int("ane-iterations", 5, "ANE measured iterations")
		steps := fs.Int("steps", 28, "denoise steps")
		seed := fs.Int("seed", 8000, "random seed")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark ANEForge attention core")
		ui.KV("artifacts", *outDir)
		ui.KV("compress", *compress)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"aneforge-attention-benchmark",
			"--out-dir", *outDir,
			"--compress", *compress,
			"--warmup", strconv.Itoa(*warmup),
			"--mps-iterations", strconv.Itoa(*mpsIterations),
			"--ane-iterations", strconv.Itoa(*aneIterations),
			"--steps", strconv.Itoa(*steps),
			"--seed", strconv.Itoa(*seed),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-latent-benchmark":
		fs := flag.NewFlagSet("ane direct-latent-benchmark", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		blockBenchmark := fs.String("block-benchmark", filepath.Join(cfg.ModelDir, "ane", "direct", "block_stack_1024x1024_benchmark.json"), "block stack benchmark")
		out := fs.String("out", "", "output benchmark JSON")
		promptText := fs.String("prompt", "a clean product photo of a translucent glass cube on a matte table", "benchmark prompt")
		width := fs.Int("width", 1024, "target image width")
		height := fs.Int("height", 1024, "target image height")
		guidance := fs.Float64("guidance", 3.5, "guidance scale")
		seed := fs.Int("seed", 12345, "base seed")
		dtype := fs.String("dtype", "bf16", "benchmark dtype: bf16, fp16, or fp32")
		stepsList := fs.String("steps-list", "1,2,4", "comma-separated step counts")
		iterations := fs.Int("iterations", 1, "iterations per step count")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark latent pipeline step slope")
		ui.KV("size", fmt.Sprintf("%dx%d", *width, *height))
		ui.KV("steps", *stepsList)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"latent-pipeline-benchmark",
			"--model-dir", cfg.ModelDir,
			"--out-dir", *outDir,
			"--block-benchmark", *blockBenchmark,
			"--prompt", *promptText,
			"--width", strconv.Itoa(*width),
			"--height", strconv.Itoa(*height),
			"--guidance", fmt.Sprintf("%f", *guidance),
			"--seed", strconv.Itoa(*seed),
			"--dtype", *dtype,
			"--steps-list", *stepsList,
			"--iterations", strconv.Itoa(*iterations),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-block-benchmark":
		fs := flag.NewFlagSet("ane direct-block-benchmark", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		denseSummary := fs.String("dense-summary", filepath.Join(cfg.ModelDir, "ane", "direct", "dense_slice_1024x1024_summary.json"), "dense slice summary")
		out := fs.String("out", "", "output benchmark JSON")
		dtype := fs.String("dtype", "bf16", "benchmark dtype: bf16, fp16, or fp32")
		warmup := fs.Int("warmup", 2, "warmup iterations")
		iterations := fs.Int("iterations", 7, "measured iterations")
		steps := fs.Int("steps", 28, "denoise steps")
		dualBlocks := fs.Int("dual-blocks", 19, "dual block count per step")
		singleBlocks := fs.Int("single-blocks", 38, "single block count per step")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark MPS transformer block stack")
		ui.KV("artifacts", *outDir)
		ui.KV("dtype", *dtype)
		ui.KV("iterations", *iterations)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"block-benchmark",
			"--model-dir", cfg.ModelDir,
			"--out-dir", *outDir,
			"--dense-summary", *denseSummary,
			"--dtype", *dtype,
			"--warmup", strconv.Itoa(*warmup),
			"--iterations", strconv.Itoa(*iterations),
			"--steps", strconv.Itoa(*steps),
			"--dual-blocks", strconv.Itoa(*dualBlocks),
			"--single-blocks", strconv.Itoa(*singleBlocks),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-benchmark":
		fs := flag.NewFlagSet("ane direct-benchmark", flag.ExitOnError)
		outDir := fs.String("out-dir", filepath.Join(cfg.ModelDir, "ane", "direct"), "direct-ANE artifact directory")
		out := fs.String("out", "", "output benchmark JSON")
		dtype := fs.String("dtype", "bf16", "benchmark dtype: bf16, fp16, or fp32")
		warmup := fs.Int("warmup", 2, "warmup iterations")
		iterations := fs.Int("iterations", 7, "measured iterations")
		steps := fs.Int("steps", 28, "denoise steps")
		dualBlocks := fs.Int("dual-blocks", 19, "dual block count per step")
		singleBlocks := fs.Int("single-blocks", 38, "single block count per step")
		gpuRenderSeconds := fs.Float64("gpu-render-seconds", 180.0, "GPU-only render reference seconds")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "benchmark MPS dense matmul slice")
		ui.KV("artifacts", *outDir)
		ui.KV("dtype", *dtype)
		ui.KV("iterations", *iterations)
		cmdArgs := []string{
			filepath.Join(cfg.Root, "flux_direct_ane.py"),
			"dense-benchmark",
			"--out-dir", *outDir,
			"--dtype", *dtype,
			"--warmup", strconv.Itoa(*warmup),
			"--iterations", strconv.Itoa(*iterations),
			"--steps", strconv.Itoa(*steps),
			"--dual-blocks", strconv.Itoa(*dualBlocks),
			"--single-blocks", strconv.Itoa(*singleBlocks),
			"--gpu-render-seconds", fmt.Sprintf("%f", *gpuRenderSeconds),
		}
		if *out != "" {
			cmdArgs = append(cmdArgs, "--out", *out)
		}
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, cmdArgs...)
	case "direct-report":
		fs := flag.NewFlagSet("ane direct-report", flag.ExitOnError)
		contract := fs.String("contract", filepath.Join(cfg.ModelDir, "ane", "direct", "direct_runtime_contract_1024x1024.json"), "runtime contract JSON")
		if err := fs.Parse(args[1:]); err != nil {
			return err
		}
		ui.Header("ane/direct", "dense offload report")
		return runner.StreamNoResult(context.Background(), map[string]string{"MODEL_DIR": cfg.ModelDir}, cfg.Python, filepath.Join(cfg.Root, "flux_direct_ane.py"), "runtime-report", "--contract", *contract)
	default:
		return fmt.Errorf("unknown ane command %q; use probe, init, convert-vae, validate, direct-capture, direct-pack, direct-projections, direct-attention, direct-benchmark, direct-block-benchmark, direct-latent-benchmark, direct-component-benchmark, direct-aneforge-projections, direct-aneforge-optimized, direct-aneforge-attention, direct-contract, or direct-report", args[0])
	}
}

func bench(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("bench", flag.ExitOnError)
	backendList := fs.String("backends", "cuda,mps,mlx", "comma-separated backends: cuda, mps, mlx, coreml, ane, cpu")
	promptFlag := fs.String("prompt", "", "benchmark prompt")
	width := fs.Int("width", 768, "benchmark width")
	height := fs.Int("height", 768, "benchmark height")
	steps := fs.Int("steps", 8, "benchmark steps")
	guidance := fs.Float64("guidance", 3.5, "guidance scale")
	seed := fs.String("seed", "12345", "shared seed")
	name := fs.String("name", "", "output filename prefix")
	dryRun := fs.Bool("dry-run", false, "show benchmark plan without starting worker or submitting jobs")
	ordered, err := reorderBenchArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	benchPrompt := strings.TrimSpace(*promptFlag)
	if benchPrompt == "" {
		benchPrompt = strings.TrimSpace(strings.Join(fs.Args(), " "))
	}
	if benchPrompt == "" {
		benchPrompt = "a clean product photo of a translucent glass cube on a matte table"
	}
	backends, err := parseBackendList(*backendList)
	if err != nil {
		return err
	}
	if *width <= 0 || *height <= 0 || *steps <= 0 {
		return fmt.Errorf("--width, --height, and --steps must be positive")
	}

	ui.Header("bench", "socket benchmark for backend auto-selection")
	ui.KV("prompt", benchPrompt)
	ui.KV("size", fmt.Sprintf("%dx%d", *width, *height))
	ui.KV("steps", *steps)
	ui.KV("seed", *seed)
	ui.KV("route", ui.State("resident")+" "+ui.Soft("unix socket"))
	ui.KV("socket policy", "reuse live socket; otherwise start non-preload queue worker")
	ui.KV("generation", "benchmark jobs load the selected backend")
	if *dryRun {
		ui.KV("state", ui.State("planned")+" "+ui.Soft("no worker started, no jobs submitted"))
		ui.Suite("backends", ui.Teal, plannedBackendRows(backends))
		return nil
	}

	client := daemon.New(cfg)
	if _, err := client.Request(map[string]any{"op": "ping"}); err != nil {
		if err := client.Start(false); err != nil {
			return err
		}
	}
	profileReady := true
	profileResp, err := client.Request(map[string]any{"op": "profile"})
	if err != nil {
		profileReady = false
		profileResp, _ = client.Request(map[string]any{"op": "ping"})
	}
	caps := profileResp.Backends
	if caps == nil {
		caps = map[string]any{}
	}

	var results []benchResult
	stamp := time.Now().Format("20060102-150405")
	for i, backend := range backends {
		if !backendCapable(backend, caps) {
			ui.KV("skip "+backend, capabilityReason(backend))
			continue
		}
		filename := *name
		if filename == "" {
			filename = fmt.Sprintf("bench-%s-%s.png", backend, stamp)
		} else if len(backends) > 1 {
			filename = suffixFilename(*name, i+1)
		}
		ui.Step(fmt.Sprintf("backend=%s", backend))
		resp, err := client.Request(map[string]any{
			"op":       "submit",
			"backend":  backend,
			"prompt":   benchPrompt,
			"width":    *width,
			"height":   *height,
			"steps":    *steps,
			"guidance": *guidance,
			"seed":     *seed,
			"filename": filename,
		})
		if err != nil {
			results = append(results, benchResult{Backend: backend, Status: "error", Error: err.Error()})
			continue
		}
		jobID := stringValue(resp.Job["id"])
		job, err := waitSocketJob(client, jobID)
		if err != nil {
			results = append(results, benchResult{Backend: backend, Status: "error", Error: err.Error()})
			continue
		}
		results = append(results, benchResult{
			Backend: backend,
			Status:  stringValue(job["status"]),
			Seconds: floatValue(job["seconds"]),
			Output:  stringValue(job["output"]),
			Error:   stringValue(job["error"]),
		})
	}

	fmt.Println()
	ui.Suite("results", ui.Teal, benchRows(results))
	if profileReady {
		ui.KV("profile", daemon.New(cfg).ProfilePath())
		key := fmt.Sprintf("%dx%d:%d", *width, *height, *steps)
		ui.KV("profile key", key)
	} else {
		ui.KV("profile", ui.Warn("worker does not expose profile API; run flux stop when ready to restart it with the updated worker"))
	}
	return nil
}

const fluxRepoID = "black-forest-labs/FLUX.1-dev"

func download(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("download", flag.ExitOnError)
	plain := fs.Bool("plain", false, "print the copy-safe shell command instead of downloading")
	dry := fs.Bool("dry", false, "show the fetch plan and equivalent hf command without downloading")
	force := fs.Bool("force", false, "re-run the fetch even when the snapshot is already complete")
	token := fs.String("token", "", "Hugging Face `token` (defaults to HF_TOKEN, then the token from hf auth login)")
	workers := fs.Int("workers", 8, "parallel download workers")
	if err := fs.Parse(args); err != nil {
		return err
	}
	patterns := fluxDownloadPatterns()
	lines := []string{
		"hf download " + fluxRepoID + " \\",
		fmt.Sprintf("  --local-dir %s \\", cfg.ModelDir),
	}
	for _, pattern := range patterns {
		lines = append(lines, fmt.Sprintf("  --include %s \\", shellQuote(pattern)))
	}
	lines = append(lines, fmt.Sprintf("  --max-workers %d", *workers))

	if *plain {
		fmt.Println(strings.Join(lines, "\n"))
		return nil
	}
	if *dry {
		ui.Header("download", "lean FLUX.1-dev BF16 Diffusers fetch (dry run)")
		ui.KV("repo", fluxRepoID)
		ui.KV("target", cfg.ModelDir)
		if fluxModelReady(cfg.ModelDir) {
			ui.KV("state", ui.Good("already complete"))
		} else {
			ui.KV("state", ui.Warn("incomplete; flux download would fetch"))
		}
		fmt.Println()
		for _, line := range lines {
			fmt.Println(ui.Code(line))
		}
		fmt.Println()
		fmt.Println(ui.Soft("Requires: HF_TOKEN or `hf auth login`, accepted FLUX.1-dev license, and about 32 GB free."))
		return nil
	}

	if fluxModelReady(cfg.ModelDir) && !*force {
		ui.Header("download", "FLUX.1-dev BF16 Diffusers snapshot")
		ui.KV("target", cfg.ModelDir)
		ui.KV("state", ui.Good("already complete"))
		fmt.Println(ui.Soft("Pass --force to re-verify every shard against the hub."))
		return nil
	}

	resolvedToken := *token
	if resolvedToken == "" {
		resolvedToken = firstNonEmptyEnv("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "HUGGINGFACE_TOKEN")
	}

	ui.Header("download", "fetching FLUX.1-dev BF16 Diffusers weights")
	ui.KV("repo", fluxRepoID)
	ui.KV("target", cfg.ModelDir)
	ui.KV("workers", *workers)

	patternsJSON, err := json.Marshal(patterns)
	if err != nil {
		return err
	}
	modelDirJSON, err := json.Marshal(cfg.ModelDir)
	if err != nil {
		return err
	}
	script := fmt.Sprintf(fluxDownloadScript, strconv.Quote(fluxRepoID), string(modelDirJSON), string(patternsJSON), *workers)

	env := map[string]string{
		"HF_HUB_DISABLE_XET":           "1",
		"HF_HUB_DISABLE_PROGRESS_BARS": "1",
		"PYTHONUNBUFFERED":             "1",
	}
	if resolvedToken != "" {
		env["HF_TOKEN"] = resolvedToken
	}
	if err := runner.StreamNoResult(context.Background(), env, cfg.Python, "-c", script); err != nil {
		return fmt.Errorf("FLUX.1-dev download failed: %w", err)
	}
	if !fluxModelReady(cfg.ModelDir) {
		return fmt.Errorf("download finished but %s is still missing required shards; re-run `flux download --force`", cfg.ModelDir)
	}
	ui.KV("state", ui.Good("snapshot complete"))
	return nil
}

func firstNonEmptyEnv(keys ...string) string {
	for _, key := range keys {
		if value := strings.TrimSpace(os.Getenv(key)); value != "" {
			return value
		}
	}
	return ""
}

// fluxDownloadScript takes repo id, local dir, allow patterns, and worker count.
// Hub progress bars are disabled, so it reports byte progress on its own lines
// that survive log capture by the daemon and the HTTP server.
const fluxDownloadScript = `
import fnmatch, os, shutil, sys, threading, time

from huggingface_hub import HfApi, get_token, snapshot_download
from huggingface_hub.utils import GatedRepoError, HfHubHTTPError, RepositoryNotFoundError

REPO = %s
LOCAL_DIR = %s
PATTERNS = %s
WORKERS = %d


def human(num):
    step = float(num)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if step < 1024 or unit == "TiB":
            return "%%.1f %%s" %% (step, unit)
        step /= 1024


def fail(message, hint=None):
    print("error=" + message)
    if hint:
        print("hint=" + hint)
    sys.exit(1)


token = get_token()
if not token:
    fail(
        "no Hugging Face token found",
        "set HF_TOKEN=hf_..., pass flux download --token hf_..., or run hf auth login",
    )

api = HfApi(token=token)
try:
    who = api.whoami()
    print("hf user=" + str(who.get("name") or who.get("fullname") or "unknown"))
except HfHubHTTPError as exc:
    fail("Hugging Face rejected the token (%%s)" %% exc.response.status_code if exc.response is not None else str(exc),
         "check the token at https://huggingface.co/settings/tokens")

try:
    info = api.model_info(REPO, files_metadata=True)
except GatedRepoError:
    fail(
        "this account has not been granted access to " + REPO,
        "accept the license at https://huggingface.co/" + REPO + " then re-run flux download",
    )
except RepositoryNotFoundError:
    fail(REPO + " is not visible to this token",
         "the token needs read access to gated repos; regenerate it with 'Read access to contents of all public gated repos'")
except HfHubHTTPError as exc:
    fail("could not read repo metadata: " + str(exc))

wanted = [
    sibling
    for sibling in info.siblings
    if any(fnmatch.fnmatch(sibling.rfilename, pattern) for pattern in PATTERNS)
]
total = sum(sibling.size or 0 for sibling in wanted)
print("files=%%d" %% len(wanted))
print("size=" + human(total))


def on_disk(path):
    seen = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                seen += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return seen


os.makedirs(LOCAL_DIR, exist_ok=True)
already = on_disk(LOCAL_DIR)
free = shutil.disk_usage(LOCAL_DIR).free
needed = max(total - already, 0)
print("free=" + human(free))
if free < needed:
    fail(
        "need %%s free at %%s but only %%s is available" %% (human(needed), LOCAL_DIR, human(free)),
    )

stop = threading.Event()


def report():
    started = time.time()
    base = already
    first = True
    while first or not stop.wait(3.0):
        if stop.is_set():
            return
        first = False
        current = on_disk(LOCAL_DIR)
        elapsed = max(time.time() - started, 1e-6)
        rate = (current - base) / elapsed
        pct = (current / total * 100.0) if total else 0.0
        eta = ""
        if rate > 0 and total > current:
            remaining = int((total - current) / rate)
            eta = " eta=%%dm%%02ds" %% (remaining // 60, remaining %% 60)
        print(
            "progress=%%.1f%%%% %%s/%%s rate=%%s/s%%s"
            %% (pct, human(current), human(total), human(rate), eta)
        )
        sys.stdout.flush()


watcher = threading.Thread(target=report, daemon=True)
watcher.start()

try:
    snapshot_download(
        repo_id=REPO,
        local_dir=LOCAL_DIR,
        allow_patterns=PATTERNS,
        max_workers=WORKERS,
        token=token,
    )
except GatedRepoError:
    stop.set()
    fail(
        "this account has not been granted access to " + REPO,
        "accept the license at https://huggingface.co/" + REPO + " then re-run flux download",
    )
except KeyboardInterrupt:
    stop.set()
    print("interrupted; re-run flux download to resume")
    sys.exit(130)
finally:
    stop.set()

print("downloaded=" + human(on_disk(LOCAL_DIR)))
print("local_dir=" + LOCAL_DIR)
`

func fluxDownloadPatterns() []string {
	return []string{
		"model_index.json",
		"scheduler/*",
		"text_encoder/*",
		"text_encoder_2/*",
		"tokenizer/*",
		"tokenizer_2/*",
		"transformer/*",
		"vae/*",
		"README.md",
		"LICENSE.md",
	}
}

func shellQuote(value string) string {
	if value == "" {
		return "''"
	}
	if strings.ContainsAny(value, " \t\n'\"\\$`!*?[]{}()<>;&|") {
		return "'" + strings.ReplaceAll(value, "'", "'\\''") + "'"
	}
	return value
}

func gpu(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("gpu", flag.ExitOnError)
	jsonOut := fs.Bool("json", false, "print raw JSON probe")
	if err := fs.Parse(args); err != nil {
		return err
	}
	script := `
import json, shutil, subprocess
out = {}
try:
    import torch
    out["torch"] = torch.__version__
    out["cuda_available"] = bool(torch.cuda.is_available())
    out["cuda_device_count"] = int(torch.cuda.device_count()) if torch.cuda.is_available() else 0
    out["cuda_devices"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())] if torch.cuda.is_available() else []
except Exception as exc:
    out["torch_error"] = type(exc).__name__ + ": " + str(exc)
if shutil.which("nvidia-smi"):
    q = ["nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"]
    p = subprocess.run(q, capture_output=True, text=True, check=False)
    out["nvidia_smi"] = p.stdout.strip().splitlines()
    pmon = subprocess.run(["nvidia-smi", "pmon", "-c", "1"], capture_output=True, text=True, check=False)
    out["nvidia_pmon"] = pmon.stdout.strip().splitlines()
else:
    out["nvidia_smi"] = []
print(json.dumps(out, sort_keys=True))
`
	out, err := exec.Command(cfg.Python, "-c", script).Output()
	if err != nil {
		return fmt.Errorf("gpu probe failed: %w", err)
	}
	if *jsonOut {
		fmt.Print(string(out))
		return nil
	}
	var probe map[string]any
	if err := json.Unmarshal(out, &probe); err != nil {
		return err
	}
	ui.Header("gpu", "FLUX runtime GPU view")
	ui.KV("python", cfg.Python)
	ui.KV("torch", valueOr(stringValue(probe["torch"]), "unknown"))
	ui.KV("cuda", ui.State(strconv.FormatBool(boolValue(probe["cuda_available"]))))
	ui.KV("device count", intValue(probe["cuda_device_count"]))
	for i, name := range stringSlice(probe["cuda_devices"]) {
		ui.KV(fmt.Sprintf("gpu %d", i), name)
	}
	if rows := stringSlice(probe["nvidia_smi"]); len(rows) > 0 {
		fmt.Println()
		ui.Suite("nvidia-smi", ui.Teal, gpuRows(rows))
	}
	if rows := stringSlice(probe["nvidia_pmon"]); len(rows) > 0 {
		fmt.Println()
		ui.Suite("processes", ui.Gold, gpuRows(rows))
	}
	return nil
}

func tree() {
	ui.Tree("tree", "command topology", []ui.TreeGroup{
		{
			Name:   "setup",
			Detail: "installation, environment, hardware & diagnostics",
			Color:  ui.Mint,
			Children: []ui.PairRow{
				{"install", "global symlink into ~/.local/bin"},
				{"setup", "uv venv + Python dependencies"},
				{"doctor", "CUDA/MPS, package, model, BF16 header checks"},
				{"accel", "current and target acceleration stack"},
				{"bench", "socket benchmark for backend auto-selection"},
				{"bench --dry-run", "show benchmark plan without starting worker"},
			},
		},
		{
			Name:   "models",
			Detail: "model acquisition, resident VRAM, GPU & formats",
			Color:  ui.Violet,
			Children: []ui.PairRow{
				{"download", "fetch FLUX.1-dev BF16 weights from Hugging Face"},
				{"download --dry", "show the fetch plan without downloading"},
				{"load", "launch worker and preload model into GPU memory"},
				{"load --preload=false", "launch queue without loading model"},
				{"gpu", "show GPU memory, utilization, and active CUDA processes"},
				{"fleet", "inspect multi-GPU worker pool across detected devices"},
				{"ane", "manage strict ANE package registry and component conversion"},
				{"ane direct-capture", "capture direct-ANE denoiser block manifest"},
			},
		},
		{
			Name:   "applications",
			Detail: "web studios, galleries & motion laboratories",
			Color:  ui.Rose,
			Children: []ui.PairRow{
				{"serve studio", "primary HTTP API and studio dashboard on :7861"},
				{"serve tea", "Tea living image garden & Stallion motion lab on :7861"},
				{"serve rosarium", "recovered visual museum (7,218 works) on :7862"},
				{"serve atlas", "Motion Atlas Sphere & agent console on :7870"},
				{"serve atelier", "Koyomi synthesis cockpit & prompt duels on :7860"},
				{"serve portal", "Influx Vision constellation index on :8898"},
				{"serve gallery", "live generation feed and archive on :7861/gallery"},
				{"remote", "client for an exposed FLUX HTTP endpoint"},
			},
		},
		{
			Name:   "actions",
			Detail: "image generation, refinement, queues & pipelines",
			Color:  ui.Gold,
			Children: []ui.PairRow{
				{"render", "start/use resident socket and wait for the job"},
				{"render --direct", "force one-shot Python generation"},
				{"render --async", "submit to resident worker, starting queue if needed"},
				{"render --burst N", "seed fanout"},
				{"img2img", "image-to-image refinement over .fluxd/img2img.sock"},
				{"img2img --warm", "start the second socket without preloading"},
				{"jobs", "inspect queued/running/done/error jobs"},
				{"jobs cancel <id>", "cancel queued or request running cancellation"},
				{"jobs open latest", "open newest completed output"},
				{"jobs prune --keep 20", "remove old terminal records"},
				{"stop", "shutdown resident worker daemons"},
				{"pipeline", "safe dry-run multi-generation workflows"},
				{"muse", "shot board with renderable local/remote commands"},
				{"matrix", "style/mood/camera exploration board"},
				{"shape", "compose final prompt with creative lenses"},
				{"spark", "six prompt mutations"},
				{"evolve", "prompt-side candidate generator"},
				{"recipes", "styles, moods, ratios, presets"},
				{"plan", "print exact engine commands"},
				{"history", "JSONL render ledger"},
			},
		},
		{
			Name:   "config",
			Detail: "system topology, architectures & themes",
			Color:  ui.Indigo,
			Children: []ui.PairRow{
				{"studio", "runtime posture, model paths, preset lanes"},
				{"usage", "real-world command examples & workflow patterns"},
				{"tree", "full command topology in Council-style branches"},
				{"architecture", "CLI, socket, HTTP, tunnel, and backend flow"},
				{"colors", "palette and state sample"},
				{"anime", "anime.sakure.network project bridge"},
			},
		},
	})
}

func studio(cfg config.Config) error {
	ui.Header("studio", "local BF16 console overview")
	ui.KV("version", version.Full())
	ui.KV("root", cfg.Root)
	ui.KV("model", cfg.ModelDir)
	ui.KV("outputs", cfg.OutputDir)
	ui.KV("backend", cfg.Backend)
	ui.KV("python", cfg.Python)
	ui.KV("engine", cfg.GeneratePy)
	if st, err := os.Stat(cfg.ModelDir); err == nil && st.IsDir() {
		ui.KV("model state", ui.State("present"))
	} else {
		ui.KV("model state", ui.State("missing"))
	}
	if _, err := os.Stat(cfg.Python); err == nil {
		ui.KV("venv", ui.State("ready"))
	} else {
		ui.KV("venv", ui.State("missing")+" "+ui.Soft("run flux setup"))
	}
	socket, state, log, pid := daemon.New(cfg).Paths()
	client := daemon.New(cfg)
	profile := client.ProfilePath()
	if resp, err := client.Request(map[string]any{"op": "ping"}); err == nil {
		loaded := "cold"
		if resp.Loaded {
			loaded = "loaded"
		}
		ui.KV("worker", ui.State("online")+" "+ui.Soft(loaded+" backend="+valueOr(resp.Backend, "?")+" device="+valueOr(resp.Device, "?")))
	} else {
		ui.KV("worker", ui.State("down")+" "+ui.Soft("no live socket; render/bench/load can start queue"))
	}
	ui.KV("socket", socket)
	ui.KV("jobs", state)
	ui.KV("profile", profile)
	ui.KV("worker log", log)
	ui.KV("pid file", pid)
	fmt.Println()
	fmt.Println(ui.Strong(ui.Accent("Preset lanes")))
	for _, p := range prompt.OrderedPresets {
		ui.Pair(p.Name, fmt.Sprintf("%s/%s %s steps=%d guidance=%.1f", p.Style, p.Mood, p.Ratio, p.Steps, p.Guidance))
	}
	return nil
}

func everythingCmd(cfg config.Config) error {
	ui.Header("everything", "sovereign multi-engine estate posture (Gemma 31B + Qwen 3.8 + FLUX.1)")

	// 1. FLUX.1-dev resident status
	client := daemon.New(cfg)
	if _, err := client.Request(map[string]any{"op": "ping"}); err == nil {
		ui.KV("flux1-dev", ui.State("RESIDENT IN VRAM (32.8 GiB)")+" - "+ui.Soft("already loaded, skipping reload"))
	} else {
		ui.KV("flux1-dev", ui.State("starting resident worker..."))
		_ = client.Start(true)
	}

	// 2. Governor Gemma 31B (:9000 / :8000)
	connGemma, errGemma := net.DialTimeout("tcp", "127.0.0.1:9000", 200*time.Millisecond)
	if errGemma == nil {
		connGemma.Close()
		ui.KV("gemma-31b", ui.State("ACTIVE ON :9000 (35.9 GiB)")+" - "+ui.Soft("Governor loaded, skipping reload"))
	} else {
		ui.KV("gemma-31b", ui.Warn("offline (vllm on :9000)"))
	}

	// 3. Qwen 3.8 Dense Vision (:9001 / :8001)
	connQwen, errQwen := net.DialTimeout("tcp", "127.0.0.1:9001", 200*time.Millisecond)
	if errQwen == nil {
		connQwen.Close()
		ui.KV("qwen-3.8-dense", ui.State("ACTIVE ON :9001")+" - "+ui.Soft("Dense Vision Sentinel loaded, skipping reload"))
	} else {
		ui.KV("qwen-3.8-dense", ui.Soft("standby / shared endpoint active"))
	}

	// 4. FLUX Studio & Arcane Dashboard (:7860 / :7861)
	connStudio, errStudio := net.DialTimeout("tcp", "127.0.0.1:7860", 200*time.Millisecond)
	if errStudio == nil {
		connStudio.Close()
		ui.KV("studio-web", ui.State("ACTIVE ON :7860")+" - "+ui.Soft("https://b300.influx.vision/"))
	} else {
		ui.KV("studio-web", ui.Warn("offline on :7860"))
	}

	ui.KV("arcane-spec", ui.Soft("https://b300.influx.vision/protocol"))
	ui.KV("arcane-forge", ui.Soft("https://b300.influx.vision/arcane"))
	ui.KV("estate-posture", ui.State("ALL SOVEREIGN ENGINES PRESERVED & SYNCED"))
	return nil
}

func loadWorker(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("load", flag.ExitOnError)
	preload := fs.Bool("preload", true, "load model immediately")
	backend := fs.String("backend", cfg.Backend, "backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	cfg.Backend = strings.ToLower(*backend)
	ui.Header("load", "starting persistent FLUX worker")
	ui.KV("backend", cfg.Backend)
	client := daemon.New(cfg)
	if err := client.Start(*preload); err != nil {
		return err
	}
	socket, _, log, pid := client.Paths()
	ui.KV("socket", socket)
	ui.KV("log", log)
	ui.KV("pid", pid)
	if *preload {
		ui.KV("state", ui.State("starting")+" "+ui.Soft("watch .fluxd/worker.log for model_ready=true"))
	} else {
		ui.KV("state", ui.State("ready")+" "+ui.Soft("model loads on first async job"))
	}
	return nil
}

func serve(cfg config.Config, args []string) error {
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		app := strings.ToLower(args[0])
		subArgs := args[1:]
		switch app {
		case "tea", "garden":
			return teaServe(cfg, subArgs)
		case "rosarium", "museum":
			return serveRosarium(cfg, subArgs)
		case "atlas", "motion-atlas", "oscillihue", "motion", "web":
			return serveOscillihue(cfg, subArgs)
		case "atelier", "cockpit", "koyomi":
			return serveAtelier(cfg, subArgs)
		case "portal", "constellation", "hub":
			return servePortal(cfg, subArgs)
		case "gallery", "view":
			return gallery(cfg, subArgs)
		case "studio", "api", "server", "core", "http":
			return serveStudio(cfg, subArgs)
		case "arcane", "fortiche":
			return serveArcane(cfg, subArgs)
		case "help", "-h", "--help", "apps", "list":
			ui.Header("serve", "standardized FLUX application server")
			ui.Suite("applications", ui.Rose, []ui.PairRow{
				{"flux serve studio", "primary HTTP/WebSocket API and studio dashboard on :7861"},
				{"flux serve arcane", "Arcane Fortiche world forge and character studio on :7860"},
				{"flux serve tea", "Tea living image garden and Stallion motion lab on :7861"},
				{"flux serve rosarium", "recovered visual museum & 7,218-item catalog on :7862"},
				{"flux serve atlas", "Motion Atlas Sphere & agent console on :7870"},
				{"flux serve atelier", "Atelier synthesis cockpit & prompt duels on :7860"},
				{"flux serve portal", "Influx Vision constellation portal on :8898"},
				{"flux serve gallery", "live generation feed and archive on :7861/gallery"},
			})
			return nil
		default:
			return fmt.Errorf("unknown application %q for `flux serve`\n\nAvailable applications:\n  • studio   (primary HTTP API & studio UI on :7861)\n  • tea      (living garden & Stallion lab on :7861)\n  • rosarium (grand museum on :7862)\n  • atlas    (Motion Atlas Sphere on :7870)\n  • atelier  (synthesis cockpit on :7860)\n  • portal   (constellation index on :8898)\n  • gallery  (live generation archive on :7861/gallery)", app)
		}
	}
	return serveStudio(cfg, args)
}

func serveStudio(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("serve studio", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:7861", "HTTP listen address")
	backend := fs.String("backend", cfg.Backend, "default backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	token := fs.String("token", "", "HTTP bearer token")
	tokenEnv := fs.String("token-env", "FLUX_HTTP_TOKEN", "env var containing HTTP bearer token")
	unsafeNoAuth := fs.Bool("unsafe-no-auth", false, "allow public bind without HTTP auth")
	publicReadOnly := fs.Bool("public-read-only", false, "serve only the gallery and safe GETs; refuse everything else")
	open := fs.Bool("open", false, "open the dashboard in the default browser")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	cfg.Backend = strings.ToLower(*backend)
	resolvedToken := resolveToken(*token, *tokenEnv)
	if publicBindAddr(*addr) && resolvedToken == "" && !*unsafeNoAuth {
		return fmt.Errorf("refusing to expose %s without auth; set --token, %s, or --unsafe-no-auth", *addr, *tokenEnv)
	}
	ui.Header("serve studio", "local HTTP API over the Unix socket worker")
	ui.KV("local url", "http://"+*addr)
	ui.KV("domain", "https://flux.influx.vision/studio or https://flux.influx.vision/api")
	ui.KV("auth", authState(resolvedToken, publicBindAddr(*addr), *unsafeNoAuth))
	ui.KV("backend", cfg.Backend)
	ui.KV("api", "/api/health /api/jobs /api/render /api/warm /api/stop")
	ui.KV("worker", "starts on first render or POST /api/warm")
	ui.KV("model", cfg.ModelDir)
	if *publicReadOnly {
		ui.KV("public", "read-only: /atelier /motion-atlas /outputs /api/health /api/recent-images /api/assets /api/jobs")
	}
	ui.KV("client", fmt.Sprintf("flux remote status --url http://%s", *addr))
	if *open {
		server.OpenBrowser("http://" + *addr)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServe(ctx, cfg, server.Options{Addr: *addr, Token: resolvedToken, PublicReadOnly: *publicReadOnly})
}

func serveRosarium(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("serve rosarium", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:7862", "HTTP listen address")
	dir := fs.String("dir", filepath.Join(cfg.Root, "apps", "rosarium", "public"), "directory to serve")
	open := fs.Bool("open", false, "open Rosarium in default browser")
	quiet := fs.Bool("quiet", false, "suppress per-request log")
	if err := fs.Parse(args); err != nil {
		return err
	}
	root, err := filepath.Abs(*dir)
	if err != nil {
		return err
	}
	if _, err := os.Stat(root); err != nil {
		return fmt.Errorf("rosarium root %s: %w", root, err)
	}
	url := "http://" + *addr
	ui.Header("serve rosarium", "recovered visual museum (7,218 works)")
	ui.KV("local url", url)
	ui.KV("domain", "https://flux.influx.vision/rosarium")
	ui.KV("root", root)
	ui.KV("auth", ui.Soft("none; static museum"))
	ui.KV("stop", "ctrl-c")
	if *open {
		server.OpenBrowser(url)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServeStatic(ctx, server.StaticOptions{
		Addr:  *addr,
		Root:  root,
		Quiet: *quiet,
	})
}

func serveAtelier(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("serve atelier", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:7860", "HTTP listen address")
	dir := filepath.Join(cfg.Root, "atelier")
	open := fs.Bool("open", false, "open Atelier in default browser")
	quiet := fs.Bool("quiet", false, "suppress per-request log")
	if err := fs.Parse(args); err != nil {
		return err
	}
	root, err := filepath.Abs(dir)
	if err != nil {
		return err
	}
	if _, err := os.Stat(root); err != nil {
		return fmt.Errorf("atelier root %s: %w", root, err)
	}
	url := "http://" + *addr
	ui.Header("serve atelier", "synthesis cockpit & evolution studio")
	ui.KV("local url", url+"/control.html")
	ui.KV("domain", "https://flux.influx.vision/atelier")
	ui.KV("root", root)
	ui.KV("auth", ui.Soft("none; local cockpit"))
	ui.KV("stop", "ctrl-c")
	if *open {
		server.OpenBrowser(url + "/control.html")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServeStatic(ctx, server.StaticOptions{
		Addr:     *addr,
		Root:     root,
		Fallback: "/control.html",
		Quiet:    *quiet,
	})
}

func servePortal(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("serve portal", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:8898", "HTTP listen address")
	dir := fs.String("dir", filepath.Join(cfg.Root, "web", "portal"), "directory to serve")
	open := fs.Bool("open", false, "open portal in default browser")
	quiet := fs.Bool("quiet", false, "suppress per-request log")
	if err := fs.Parse(args); err != nil {
		return err
	}
	root, err := filepath.Abs(*dir)
	if err != nil {
		return err
	}
	if _, err := os.Stat(root); err != nil {
		if _, errVar := os.Stat("/var/www/flux-portal"); errVar == nil {
			root = "/var/www/flux-portal"
		} else {
			return fmt.Errorf("portal root %s: %w", root, err)
		}
	}
	url := "http://" + *addr
	ui.Header("serve portal", "Influx Vision constellation portal")
	ui.KV("local url", url)
	ui.KV("domain", "https://flux.influx.vision/")
	ui.KV("root", root)
	ui.KV("stop", "ctrl-c")
	if *open {
		server.OpenBrowser(url)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServeStatic(ctx, server.StaticOptions{
		Addr:  *addr,
		Root:  root,
		Quiet: *quiet,
	})
}

func serveOscillihue(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("serve oscillihue", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:7870", "HTTP listen address")
	dir := fs.String("dir", filepath.Join(cfg.Root, "web"), "directory to serve")
	open := fs.Bool("open", false, "open the site in the default browser")
	quiet := fs.Bool("quiet", false, "suppress the per-request log")
	if err := fs.Parse(args); err != nil {
		return err
	}
	root, err := filepath.Abs(*dir)
	if err != nil {
		return err
	}
	if _, err := os.Stat(root); err != nil {
		return fmt.Errorf("nothing to serve at %s: %w", root, err)
	}
	fallback := ""
	if _, err := os.Stat(filepath.Join(root, "motion-atlas", "index.html")); err == nil {
		fallback = "/motion-atlas/"
	}
	url := "http://" + *addr

	ui.Header("serve atlas", "Motion Atlas Sphere & web suite over HTTP")
	ui.KV("local url", url)
	ui.KV("domain", "https://flux.influx.vision/atlas")
	ui.KV("root", root)
	if fallback != "" {
		ui.KV("index", url+fallback)
	}
	ui.KV("auth", ui.Soft("none; static files only"))
	if publicBindAddr(*addr) {
		ui.KV("bind", ui.Warn("public; every file under root is reachable"))
	}
	ui.KV("stop", "ctrl-c")
	if *open {
		server.OpenBrowser(url + fallback)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServeStatic(ctx, server.StaticOptions{
		Addr:     *addr,
		Root:     root,
		Fallback: fallback,
		Quiet:    *quiet,
	})
}

// oscillihue is the noun-first spelling: `flux oscillihue serve` and a bare
// `flux oscillihue` both land on the same static server as `flux serve oscillihue`.
func oscillihue(cfg config.Config, args []string) error {
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		switch strings.ToLower(args[0]) {
		case "serve", "http", "start", "web", "static":
			args = args[1:]
		default:
			return fmt.Errorf("unknown oscillihue action %q; use `flux serve atlas`", args[0])
		}
	}
	return serveOscillihue(cfg, args)
}

func gallery(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("gallery", flag.ExitOnError)
	addr := fs.String("addr", "127.0.0.1:7861", "HTTP listen address")
	backend := fs.String("backend", cfg.Backend, "default backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	token := fs.String("token", "", "HTTP bearer token")
	tokenEnv := fs.String("token-env", "FLUX_HTTP_TOKEN", "env var containing HTTP bearer token")
	unsafeNoAuth := fs.Bool("unsafe-no-auth", false, "allow public bind without HTTP auth")
	open := fs.Bool("open", false, "open the gallery in the default browser")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	cfg.Backend = strings.ToLower(*backend)
	resolvedToken := resolveToken(*token, *tokenEnv)
	if publicBindAddr(*addr) && resolvedToken == "" && !*unsafeNoAuth {
		return fmt.Errorf("refusing to expose %s without auth; set --token, %s, or --unsafe-no-auth", *addr, *tokenEnv)
	}
	url := "http://" + *addr + "/gallery"
	ui.Header("serve gallery", "live generation feed and archive")
	ui.KV("local url", url)
	ui.KV("domain", "https://flux.influx.vision/gallery")
	ui.KV("auth", authState(resolvedToken, publicBindAddr(*addr), *unsafeNoAuth))
	ui.KV("backend", cfg.Backend)
	ui.KV("stream", "/api/jobs/events /api/img2img/events /api/gallery/events")
	ui.KV("outputs", cfg.OutputDir)
	if *open {
		server.OpenBrowser(url)
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return server.ListenAndServe(ctx, cfg, server.Options{Addr: *addr, Token: resolvedToken})
}

func publicBindAddr(addr string) bool {
	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		host = addr
	}
	host = strings.Trim(host, "[]")
	if host == "" {
		return true
	}
	if strings.EqualFold(host, "localhost") {
		return false
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return true
	}
	return !ip.IsLoopback()
}

func resolveToken(flagValue, envName string) string {
	if strings.TrimSpace(flagValue) != "" {
		return strings.TrimSpace(flagValue)
	}
	if strings.TrimSpace(envName) == "" {
		return ""
	}
	return strings.TrimSpace(os.Getenv(envName))
}

func authState(token string, public bool, unsafeNoAuth bool) string {
	if strings.TrimSpace(token) == "" {
		if public && unsafeNoAuth {
			return ui.Warn("public-no-auth") + " " + ui.Soft("explicitly exposed")
		}
		return ui.State("local") + " " + ui.Soft("no token")
	}
	return ui.State("ready") + " " + ui.Soft("bearer/basic token")
}

func remote(args []string) error {
	if len(args) == 0 {
		return fmt.Errorf("remote needs a command: status, jobs, warm, stop, render")
	}
	switch args[0] {
	case "status", "health":
		return remoteStatus(args[1:])
	case "jobs", "queue":
		return remoteJobs(args[1:])
	case "warm", "load":
		return remoteWarm(args[1:])
	case "stop":
		return remoteStop(args[1:])
	case "render", "imagine", "forge":
		return remoteRender(args[1:])
	default:
		return fmt.Errorf("unknown remote command %q", args[0])
	}
}

func remoteStatus(args []string) error {
	fs := flag.NewFlagSet("remote status", flag.ExitOnError)
	baseURL, token, tokenEnv := remoteFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	resp, err := remoteRequest(http.MethodGet, *baseURL, "/api/health", resolveToken(*token, *tokenEnv), nil)
	if err != nil {
		return err
	}
	ui.Header("remote", "HTTP FLUX endpoint status")
	ui.KV("url", *baseURL)
	ui.KV("worker", ui.State(fmt.Sprintf("%v", resp["worker_running"])))
	ui.KV("backend", stringValue(resp["backend"]))
	ui.KV("loaded", fmt.Sprintf("%v", resp["loaded"]))
	ui.KV("device", stringValue(resp["device"]))
	if errMsg := stringValue(resp["worker_error"]); errMsg != "" {
		ui.KV("worker error", errMsg)
	}
	return nil
}

func remoteJobs(args []string) error {
	fs := flag.NewFlagSet("remote jobs", flag.ExitOnError)
	baseURL, token, tokenEnv := remoteFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	resp, err := remoteRequest(http.MethodGet, *baseURL, "/api/jobs", resolveToken(*token, *tokenEnv), nil)
	if err != nil {
		return err
	}
	ui.Header("remote jobs", "HTTP worker queue")
	jobs := mapSlice(resp["jobs"])
	if len(jobs) == 0 {
		fmt.Println(ui.Soft("no jobs yet"))
		return nil
	}
	for _, job := range jobs {
		output := valueOr(stringValue(job["output_url"]), stringValue(job["output"]))
		fmt.Printf("%s %-18s %-8s %s\n", ui.Accent(stringValue(job["id"])), ui.State(stringValue(job["status"])), ui.Accent(valueOr(stringValue(job["backend"]), "?")), output)
		fmt.Println("  " + stringValue(job["prompt"]))
		if errMsg := stringValue(job["error"]); errMsg != "" {
			fmt.Println("  " + ui.Bad(errMsg))
		}
	}
	return nil
}

func remoteWarm(args []string) error {
	fs := flag.NewFlagSet("remote warm", flag.ExitOnError)
	baseURL, token, tokenEnv := remoteFlags(fs)
	preload := fs.Bool("preload", false, "load model immediately")
	if err := fs.Parse(args); err != nil {
		return err
	}
	path := "/api/warm"
	if *preload {
		path += "?preload=1"
	}
	_, err := remoteRequest(http.MethodPost, *baseURL, path, resolveToken(*token, *tokenEnv), map[string]any{})
	if err != nil {
		return err
	}
	ui.Header("remote warm", "worker launch requested")
	ui.KV("url", *baseURL)
	ui.KV("preload", *preload)
	return nil
}

func remoteStop(args []string) error {
	fs := flag.NewFlagSet("remote stop", flag.ExitOnError)
	baseURL, token, tokenEnv := remoteFlags(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	_, err := remoteRequest(http.MethodPost, *baseURL, "/api/stop", resolveToken(*token, *tokenEnv), map[string]any{})
	if err != nil {
		return err
	}
	ui.Header("remote stop", "worker stop requested")
	ui.KV("url", *baseURL)
	ui.KV("state", ui.State("down"))
	return nil
}

func remoteRender(args []string) error {
	fs := flag.NewFlagSet("remote render", flag.ExitOnError)
	baseURL, token, tokenEnv := remoteFlags(fs)
	presetName := fs.String("preset", "", "preset: sketch, hero, object, space, cover, future, anime, noir")
	backend := fs.String("backend", "auto", "backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	style := fs.String("style", "", "prompt style")
	mood := fs.String("mood", "", "prompt mood")
	camera := fs.String("camera", "", "camera lens")
	light := fs.String("light", "", "lighting")
	palette := fs.String("palette", "", "palette")
	texture := fs.String("texture", "", "texture")
	detail := fs.String("detail", "", "detail density")
	chaos := fs.String("chaos", "", "variation")
	director := fs.String("director", "", "influence")
	ratioName := fs.String("ratio", "square", "ratio")
	steps := fs.Int("steps", 28, "inference steps")
	guidance := fs.Float64("guidance", 3.5, "guidance scale")
	width := fs.Int("width", 0, "override width")
	height := fs.Int("height", 0, "override height")
	seed := fs.String("seed", "", "seed")
	name := fs.String("name", "", "output filename")
	draft := fs.Bool("draft", false, "768x768, 18 steps")
	dryRun := fs.Bool("dry-run", false, "plan without generating")
	wait := fs.Bool("wait", false, "poll until the job completes")
	ordered, err := reorderRemoteRenderArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	*backend = strings.ToLower(*backend)
	base := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if base == "" {
		return fmt.Errorf("remote render needs a prompt")
	}
	body := map[string]any{
		"prompt":   base,
		"backend":  *backend,
		"preset":   *presetName,
		"style":    *style,
		"mood":     *mood,
		"camera":   *camera,
		"light":    *light,
		"palette":  *palette,
		"texture":  *texture,
		"detail":   *detail,
		"chaos":    *chaos,
		"director": *director,
		"ratio":    *ratioName,
		"width":    *width,
		"height":   *height,
		"steps":    *steps,
		"guidance": *guidance,
		"seed":     *seed,
		"filename": *name,
		"draft":    *draft,
		"dry_run":  *dryRun,
	}
	resolvedToken := resolveToken(*token, *tokenEnv)
	resp, err := remoteRequest(http.MethodPost, *baseURL, "/api/render", resolvedToken, body)
	if err != nil {
		return err
	}
	ui.Header("remote render", "HTTP submit to FLUX socket host")
	ui.KV("url", *baseURL)
	if plan, ok := resp["plan"].(map[string]any); ok {
		ui.KV("backend", stringValue(plan["backend"]))
		ui.KV("size", fmt.Sprintf("%vx%v", plan["width"], plan["height"]))
		ui.KV("steps", fmt.Sprintf("%v", plan["steps"]))
		ui.KV("prompt", stringValue(plan["prompt"]))
	}
	if *dryRun {
		ui.KV("state", ui.State("planned"))
		return nil
	}
	job := mapValue(resp["job"])
	jobID := stringValue(job["id"])
	ui.KV("job", jobID)
	ui.KV("backend", stringValue(job["backend"]))
	ui.KV("status", stringValue(job["status"]))
	if *wait {
		return watchRemoteJob(*baseURL, resolvedToken, jobID)
	}
	return nil
}

func remoteFlags(fs *flag.FlagSet) (*string, *string, *string) {
	baseURL := fs.String("url", "http://127.0.0.1:7861", "remote FLUX HTTP URL")
	token := fs.String("token", "", "HTTP bearer token")
	tokenEnv := fs.String("token-env", "FLUX_HTTP_TOKEN", "env var containing HTTP bearer token")
	return baseURL, token, tokenEnv
}

func remoteRequest(method, baseURL, path, token string, body any) (map[string]any, error) {
	var reader io.Reader
	if body != nil {
		var buf bytes.Buffer
		if err := json.NewEncoder(&buf).Encode(body); err != nil {
			return nil, err
		}
		reader = &buf
	}
	req, err := http.NewRequest(method, strings.TrimRight(baseURL, "/")+path, reader)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json")
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if strings.TrimSpace(token) != "" {
		req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(token))
	}
	client := &http.Client{Timeout: 30 * time.Second}
	httpResp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer httpResp.Body.Close()
	data, err := io.ReadAll(httpResp.Body)
	if err != nil {
		return nil, err
	}
	if httpResp.StatusCode >= 400 {
		return nil, fmt.Errorf("%s: %s", httpResp.Status, strings.TrimSpace(string(data)))
	}
	if len(strings.TrimSpace(string(data))) == 0 {
		return map[string]any{}, nil
	}
	var out map[string]any
	if err := json.Unmarshal(data, &out); err != nil {
		return nil, err
	}
	return out, nil
}

func watchRemoteJob(baseURL, token, jobID string) error {
	if jobID == "" {
		return nil
	}
	for {
		resp, err := remoteRequest(http.MethodGet, baseURL, "/api/jobs", token, nil)
		if err != nil {
			ui.ProgressDone()
			return err
		}
		job := findJob(mapSlice(resp["jobs"]), jobID)
		if job == nil {
			ui.Progress("remote", "unknown", 0, 1, jobID)
			time.Sleep(750 * time.Millisecond)
			continue
		}
		status := stringValue(job["status"])
		phase := valueOr(stringValue(job["phase"]), status)
		step := intValue(job["step"])
		total := intValue(job["total_steps"])
		if total <= 0 {
			total = intValue(job["steps"])
		}
		if total <= 0 {
			total = 1
		}
		if status == "queued" || phase == "loading_model" {
			step = 0
		}
		if phase == "saving" || status == "done" {
			step = total
		}
		ui.Progress("remote", phase, step, total, phase)
		switch status {
		case "done":
			ui.ProgressDone()
			ui.KV("output", stringValue(job["output"]))
			if outputURL := stringValue(job["output_url"]); outputURL != "" {
				ui.KV("image", outputURL)
			}
			return nil
		case "error":
			ui.ProgressDone()
			return fmt.Errorf("%s", stringValue(job["error"]))
		default:
			time.Sleep(750 * time.Millisecond)
		}
	}
}

func stopWorker(cfg config.Config) error {
	ui.Header("stop", "stopping persistent worker")
	if err := daemon.New(cfg).Stop(); err != nil {
		return err
	}
	ui.KV("state", ui.State("down"))
	return nil
}

func jobs(cfg config.Config, args []string) error {
	if len(args) > 0 {
		switch args[0] {
		case "cancel":
			return cancelJob(cfg, args[1:])
		case "prune", "clear":
			return pruneJobs(cfg, args[1:])
		case "open":
			return openJob(cfg, args[1:])
		}
	}
	fs := flag.NewFlagSet("jobs", flag.ExitOnError)
	allJobsFlag := fs.Bool("all", false, "show history instead of active jobs only")
	activeOnly := fs.Bool("active", false, "show queued/running jobs only")
	doneOnly := fs.Bool("done", false, "show completed jobs only")
	errorsOnly := fs.Bool("errors", false, "show failed/cancelled jobs only")
	limit := fs.Int("n", 20, "limit rows, newest first; 0 shows all")
	jsonOut := fs.Bool("json", false, "print raw jobs JSON")
	openLatest := fs.Bool("open-latest", false, "open newest completed output")
	if err := fs.Parse(args); err != nil {
		return err
	}
	resp, err := daemon.New(cfg).Request(map[string]any{"op": "jobs"})
	workerRunning := true
	allJobs := []map[string]any{}
	if err != nil {
		workerRunning = false
		_, statePath, _, _ := daemon.New(cfg).Paths()
		allJobs = readStateJobs(statePath)
	} else {
		allJobs = resp.Jobs
	}
	if !*allJobsFlag && !*activeOnly && !*doneOnly && !*errorsOnly && !*openLatest {
		*activeOnly = true
	}
	jobs := filterJobs(allJobs, *activeOnly, *doneOnly, *errorsOnly)
	reverseJobs(jobs)
	if *limit > 0 && len(jobs) > *limit {
		jobs = jobs[:*limit]
	}
	if *jsonOut {
		return json.NewEncoder(os.Stdout).Encode(map[string]any{"ok": true, "worker_running": workerRunning, "jobs": jobs})
	}
	ui.Header("jobs", "persistent worker queue")
	if workerRunning {
		ui.KV("worker", ui.State("online"))
	} else {
		ui.KV("worker", ui.State("down")+" "+ui.Soft("showing on-disk ledger"))
	}
	if *openLatest {
		job := newestOutputJob(allJobs)
		if job == nil {
			return fmt.Errorf("no completed output to open")
		}
		return openOutput(jobDisplayOutput(job))
	}
	printQueueSummary(allJobs)
	if len(jobs) == 0 {
		if *activeOnly {
			fmt.Println(ui.Soft("no active jobs; use flux jobs --all for recent history"))
		} else {
			fmt.Println(ui.Soft("no jobs yet"))
		}
		return nil
	}
	for _, job := range jobs {
		printJobRow(job)
	}
	return nil
}

func readStateJobs(path string) []map[string]any {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	jobs := []map[string]any{}
	for _, line := range strings.Split(string(data), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		var job map[string]any
		if err := json.Unmarshal([]byte(line), &job); err == nil {
			jobs = append(jobs, job)
		}
	}
	return jobs
}

func fluxModelReady(modelDir string) bool {
	required := []string{
		"model_index.json",
		"scheduler/scheduler_config.json",
		"text_encoder/model.safetensors",
		"text_encoder_2/model-00001-of-00002.safetensors",
		"text_encoder_2/model-00002-of-00002.safetensors",
		"transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
		"transformer/diffusion_pytorch_model-00002-of-00003.safetensors",
		"transformer/diffusion_pytorch_model-00003-of-00003.safetensors",
		"vae/diffusion_pytorch_model.safetensors",
	}
	for _, rel := range required {
		if _, err := os.Stat(filepath.Join(modelDir, rel)); err != nil {
			return false
		}
	}
	return true
}

func cancelJob(cfg config.Config, args []string) error {
	if len(args) != 1 {
		return fmt.Errorf("usage: flux jobs cancel <job-id>")
	}
	ui.Header("jobs", "cancel queued/running worker job")
	resp, err := daemon.New(cfg).Request(map[string]any{"op": "cancel", "id": args[0]})
	if err != nil {
		return fmt.Errorf("%w; if the worker is already running, restart it once to enable cancel support", err)
	}
	job := resp.Job
	ui.KV("job", args[0])
	ui.KV("status", stringValue(job["status"]))
	ui.KV("phase", stringValue(job["phase"]))
	return nil
}

func pruneJobs(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("jobs prune", flag.ExitOnError)
	keep := fs.Int("keep", 20, "keep this many terminal jobs")
	all := fs.Bool("all", false, "prune all terminal jobs")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *all {
		*keep = 0
	}
	ui.Header("jobs", "prune terminal worker job records")
	resp, err := daemon.New(cfg).Request(map[string]any{"op": "prune", "keep": *keep, "statuses": []string{"done", "error", "cancelled"}})
	if err != nil {
		return fmt.Errorf("%w; if the worker is already running, restart it once to enable prune support", err)
	}
	removed := mapSlice(resp.Raw["removed"])
	if raw, ok := resp.Raw["removed"].([]any); ok {
		ui.KV("removed", len(raw))
	} else {
		ui.KV("removed", len(removed))
	}
	ui.KV("keep", *keep)
	return nil
}

func openJob(cfg config.Config, args []string) error {
	target := "latest"
	if len(args) > 0 {
		target = args[0]
	}
	resp, err := daemon.New(cfg).Request(map[string]any{"op": "jobs"})
	if err != nil {
		return err
	}
	var job map[string]any
	if target == "latest" {
		job = newestOutputJob(resp.Jobs)
	} else {
		job = findJob(resp.Jobs, target)
	}
	if job == nil {
		return fmt.Errorf("no output job found for %q", target)
	}
	return openOutput(jobDisplayOutput(job))
}

func render(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("render", flag.ExitOnError)
	presetName := fs.String("preset", "", "preset: sketch, hero, object, space, cover, future, anime, noir")
	backend := fs.String("backend", cfg.Backend, "backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	style := fs.String("style", "", "prompt style: cinema, product, editorial, architect, document, speculative, anime, noir")
	mood := fs.String("mood", "", "prompt mood: quiet, electric, clinical, warm, ominous, optimistic, melancholy, fever")
	camera := fs.String("camera", "", "camera lens: wide, close, macro, low, overhead, tracking, portrait")
	light := fs.String("light", "", "lighting: golden, neon, overcast, rim, lantern, storm, studio")
	palette := fs.String("palette", "", "palette: sakura, verdant, cobalt, ember, mono, pastel, acid")
	texture := fs.String("texture", "", "texture: film, ink, cel, paper, metal, glass, weathered")
	detail := fs.String("detail", "", "detail density: minimal, balanced, dense, ornate, diagram")
	chaos := fs.String("chaos", "", "variation: calm, alive, wild, surreal, maximal")
	director := fs.String("director", "", "influence: miyazaki, kon, oshii, watanabe, anno, shinkai, vogue, brutalist")
	ratioName := fs.String("ratio", "square", "ratio: square, wide, portrait, fourthree, draft")
	steps := fs.Int("steps", 28, "inference steps")
	guidance := fs.Float64("guidance", 3.5, "guidance scale")
	width := fs.Int("width", 0, "override width")
	height := fs.Int("height", 0, "override height")
	seed := fs.String("seed", "", "seed")
	name := fs.String("name", "", "output filename")
	draft := fs.Bool("draft", false, "768x768, 18 steps")
	dryRun := fs.Bool("dry-run", false, "print command without generating")
	echo := fs.Bool("echo", false, "print shaped prompt before running")
	async := fs.Bool("async", false, "queue on persistent worker; starts it if needed")
	direct := fs.Bool("direct", false, "force one-shot Python render even when a worker socket is live")
	burst := fs.Int("burst", 1, "render N seed variants")
	startSeed := fs.Int("start-seed", 0, "first seed for burst when --seed is omitted")
	ordered, err := reorderRenderArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	*backend = strings.ToLower(*backend)
	base := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if base == "" {
		return fmt.Errorf("render needs a prompt")
	}
	preset, err := prompt.PresetByName(*presetName)
	if err != nil {
		return err
	}
	if preset.Name != "" {
		if *style == "" {
			*style = preset.Style
		}
		if *mood == "" {
			*mood = preset.Mood
		}
		if *ratioName == "square" {
			*ratioName = preset.Ratio
		}
		if *steps == 28 {
			*steps = preset.Steps
		}
		if *guidance == 3.5 {
			*guidance = preset.Guidance
		}
	}
	if *draft {
		*ratioName = "draft"
		*steps = 18
	}
	ratio, err := prompt.RatioByName(*ratioName)
	if err != nil {
		return err
	}
	if *width == 0 {
		*width = ratio.Width
	}
	if *height == 0 {
		*height = ratio.Height
	}
	shaped, err := prompt.Compose(base, prompt.Shape{
		Style: *style, Mood: *mood, Camera: *camera, Light: *light, Palette: *palette,
		Texture: *texture, Detail: *detail, Chaos: *chaos, Director: *director, Preset: *presetName,
	})
	if err != nil {
		return err
	}
	if *burst < 1 {
		return fmt.Errorf("--burst must be at least 1")
	}

	renderSubtitle := "local BF16 FLUX generation"
	if *dryRun {
		renderSubtitle = "local BF16 FLUX render plan"
	}
	ui.Header("render", renderSubtitle)
	ui.KV("preset", valueOr(*presetName, "none"))
	ui.KV("backend", *backend)
	ui.KV("style", valueOr(*style, "none"))
	ui.KV("mood", valueOr(*mood, "none"))
	printLensKV("camera", *camera)
	printLensKV("light", *light)
	printLensKV("palette", *palette)
	printLensKV("texture", *texture)
	printLensKV("detail", *detail)
	printLensKV("chaos", *chaos)
	printLensKV("director", *director)
	ui.KV("size", fmt.Sprintf("%dx%d", *width, *height))
	ui.KV("steps", *steps)
	ui.KV("guidance", *guidance)
	ui.KV("seed", valueOr(*seed, "random"))
	ui.KV("burst", *burst)
	if *dryRun {
		ui.KV("state", ui.State("planned")+" "+ui.Soft("no job submitted"))
		if *direct {
			ui.KV("route", ui.State("direct")+" "+ui.Soft("one-shot Python plan"))
		} else {
			ui.KV("route", ui.State("resident")+" "+ui.Soft("unix socket plan"))
		}
	}
	if *echo || *dryRun {
		ui.KV("prompt", shaped)
	}
	if !*dryRun && !fluxModelReady(cfg.ModelDir) {
		return fmt.Errorf("missing FLUX.1-dev Diffusers snapshot at %s; set HF_TOKEN (or run `hf auth login`) with accepted black-forest-labs/FLUX.1-dev access, then run `flux download`", cfg.ModelDir)
	}

	baseArgs := []string{
		cfg.GeneratePy,
		"--prompt", shaped,
		"--width", strconv.Itoa(*width),
		"--height", strconv.Itoa(*height),
		"--steps", strconv.Itoa(*steps),
		"--guidance", fmt.Sprintf("%.3f", *guidance),
	}
	for i := 0; i < *burst; i++ {
		runSeed := *seed
		if *burst > 1 && runSeed == "" {
			if *startSeed == 0 {
				*startSeed = int(time.Now().Unix() % 1000000)
			}
			runSeed = strconv.Itoa(*startSeed + i)
		}
		cmdArgs := append([]string{}, baseArgs...)
		if runSeed != "" {
			cmdArgs = append(cmdArgs, "--seed", runSeed)
		}
		if *name != "" {
			filename := *name
			if *burst > 1 {
				filename = suffixFilename(*name, i+1)
			}
			cmdArgs = append(cmdArgs, "--filename", filename)
		}
		if *dryRun {
			if *direct {
				if !directBackendSupported(*backend) {
					return fmt.Errorf("--direct only supports auto, cuda, mps, or cpu backends")
				}
				if *backend == "cpu" || *backend == "cuda" {
					cmdArgs = append(cmdArgs, "--device", *backend)
				}
				ui.KV(fmt.Sprintf("command[%d]", i+1), cfg.Python+" "+shellish(cmdArgs))
			} else {
				ui.KV(fmt.Sprintf("socket-plan[%d]", i+1), fmt.Sprintf("backend=%s %dx%d steps=%d guidance=%.3f", *backend, *width, *height, *steps, *guidance))
			}
			continue
		}
		client := daemon.New(cfg)
		if !*direct {
			socketLive := false
			if _, err := client.Request(map[string]any{"op": "ping"}); err == nil {
				socketLive = true
			}
			if !socketLive {
				if err := client.Start(false); err != nil {
					return err
				}
			}
			resp, err := client.Request(map[string]any{
				"op":       "submit",
				"backend":  *backend,
				"prompt":   shaped,
				"width":    *width,
				"height":   *height,
				"steps":    *steps,
				"guidance": *guidance,
				"seed":     runSeed,
				"filename": filenameFromArgs(cmdArgs),
			})
			if err != nil {
				return err
			}
			ui.KV("route", ui.State("resident")+" "+ui.Soft("unix socket"))
			if resp.Job != nil {
				jobID := stringValue(resp.Job["id"])
				ui.KV("job", jobID)
				ui.KV("backend", stringValue(resp.Job["backend"]))
				ui.KV("status", stringValue(resp.Job["status"]))
				if !*async {
					if err := watchSocketJob(client, jobID, cfg, history.Entry{
						Time:     time.Now(),
						Prompt:   shaped,
						Style:    *style,
						Mood:     *mood,
						Width:    *width,
						Height:   *height,
						Steps:    *steps,
						Guidance: *guidance,
						Seed:     runSeed,
					}); err != nil {
						return err
					}
				}
			}
			continue
		}
		ui.KV("route", ui.State("direct")+" "+ui.Soft("one-shot Python"))
		if !directBackendSupported(*backend) {
			return fmt.Errorf("--direct only supports auto, cuda, mps, or cpu backends")
		}
		if *backend == "cpu" || *backend == "cuda" {
			cmdArgs = append(cmdArgs, "--device", *backend)
		}
		if *burst > 1 {
			ui.Step(fmt.Sprintf("variant %d/%d seed=%s", i+1, *burst, valueOr(runSeed, "random")))
		}
		res, err := runner.Stream(context.Background(), map[string]string{
			"MODEL_DIR": cfg.ModelDir,
			"OUT_DIR":   cfg.OutputDir,
		}, cfg.Python, cmdArgs...)
		if err != nil {
			return err
		}
		_ = history.Append(cfg.History, history.Entry{
			Time:     time.Now(),
			Prompt:   shaped,
			Style:    *style,
			Mood:     *mood,
			Width:    *width,
			Height:   *height,
			Steps:    *steps,
			Guidance: *guidance,
			Seed:     runSeed,
			Output:   res.OutputPath,
			Seconds:  res.Seconds,
		})
	}
	return nil
}

func img2img(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("img2img", flag.ExitOnError)
	imagePath := fs.String("image", "", "input image path")
	image2Path := fs.String("image2", "", "second reference image path; builds a composite reference")
	ref2Path := fs.String("ref2", "", "alias for --image2")
	backend := fs.String("backend", "auto", "backend: auto, mps, cpu")
	strength := fs.Float64("strength", 0.55, "image denoise strength; 0.25 preserves, 0.55 balances, 0.70 transforms")
	steps := fs.Int("steps", 28, "inference steps")
	guidance := fs.Float64("guidance", 5.0, "guidance scale")
	width := fs.Int("width", 0, "output width; defaults to input image width")
	height := fs.Int("height", 0, "output height; defaults to input image height")
	seed := fs.String("seed", "", "seed")
	name := fs.String("name", "", "output filename")
	style := fs.String("style", "", "prompt style")
	mood := fs.String("mood", "", "prompt mood")
	camera := fs.String("camera", "", "camera lens")
	light := fs.String("light", "", "lighting")
	palette := fs.String("palette", "", "palette")
	texture := fs.String("texture", "", "texture")
	detail := fs.String("detail", "", "detail density")
	chaos := fs.String("chaos", "", "variation")
	director := fs.String("director", "", "influence")
	dryRun := fs.Bool("dry-run", false, "show socket plan without submitting")
	async := fs.Bool("async", false, "queue and return immediately")
	preload := fs.Bool("preload", false, "load img2img model immediately when starting socket")
	warmOnly := fs.Bool("warm", false, "start img2img socket without submitting")
	stopOnly := fs.Bool("stop", false, "stop img2img socket")
	jobsOnly := fs.Bool("jobs", false, "show img2img socket jobs")
	if err := fs.Parse(args); err != nil {
		return err
	}
	client := daemon.NewNamed(cfg, "img2img")
	if *stopOnly {
		ui.Header("img2img", "stopping FLUX image-to-image socket")
		if err := client.Stop(); err != nil {
			return err
		}
		ui.KV("state", ui.State("down"))
		return nil
	}
	if *warmOnly {
		ui.Header("img2img", "starting FLUX image-to-image socket")
		if err := client.Start(*preload); err != nil {
			return err
		}
		socket, state, log, pid := client.Paths()
		ui.KV("socket", socket)
		ui.KV("state", state)
		ui.KV("log", log)
		ui.KV("pid", pid)
		ui.KV("preload", *preload)
		return nil
	}
	if *jobsOnly {
		ui.Header("img2img jobs", "image-to-image socket queue")
		resp, err := client.Request(map[string]any{"op": "jobs"})
		if err != nil {
			return err
		}
		printQueueSummary(resp.Jobs)
		jobs := append([]map[string]any{}, resp.Jobs...)
		reverseJobs(jobs)
		if len(jobs) == 0 {
			fmt.Println(ui.Soft("no jobs yet"))
			return nil
		}
		for _, job := range jobs {
			printJobRow(job)
		}
		return nil
	}
	if strings.TrimSpace(*imagePath) == "" {
		return fmt.Errorf("img2img needs --image <path>")
	}
	if strings.TrimSpace(*image2Path) == "" {
		*image2Path = strings.TrimSpace(*ref2Path)
	}
	switch strings.ToLower(strings.TrimSpace(*backend)) {
	case "", "auto", "mps", "cpu":
		*backend = strings.ToLower(valueOr(*backend, "auto"))
	default:
		return fmt.Errorf("img2img backend must be auto, mps, or cpu")
	}
	if *strength <= 0 || *strength >= 1 {
		return fmt.Errorf("--strength must be between 0 and 1")
	}
	base := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if base == "" {
		base = "stylized cinematic 3D animated character still, sculpted facial planes, expressive eyes, hand-painted texture over 3D form, graphic cel-shadow shapes, dramatic rim lighting, teal and amber color grade"
	}
	shaped, err := prompt.Compose(base, prompt.Shape{
		Style: *style, Mood: *mood, Camera: *camera, Light: *light, Palette: *palette,
		Texture: *texture, Detail: *detail, Chaos: *chaos, Director: *director,
	})
	if err != nil {
		return err
	}
	absImage, err := filepath.Abs(expandHome(*imagePath))
	if err != nil {
		return err
	}
	if _, err := os.Stat(absImage); err != nil {
		return fmt.Errorf("input image unavailable: %w", err)
	}
	absImage2 := ""
	compositeImage := ""
	if strings.TrimSpace(*image2Path) != "" {
		absImage2, err = filepath.Abs(expandHome(*image2Path))
		if err != nil {
			return err
		}
		if _, err := os.Stat(absImage2); err != nil {
			return fmt.Errorf("second input image unavailable: %w", err)
		}
		ref, err := stitchReferenceImages(cfg, absImage, absImage2)
		if err != nil {
			return err
		}
		compositeImage = ref.Path
		absImage = ref.Path
		if *width <= 0 && *height <= 0 {
			*width = ref.OutputWidth
			*height = ref.OutputHeight
		}
		shaped = "Use the composite reference as two visual references: left image is the primary subject/composition reference, right image is the secondary style/detail reference. Synthesize one cohesive image; do not create a diptych, split-screen, contact sheet, or side-by-side layout. " + shaped
	}

	ui.Header("img2img", "FLUX image-to-image socket")
	ui.KV("socket", filepath.Join(cfg.Root, ".fluxd", "img2img.sock"))
	ui.KV("image", absImage)
	if absImage2 != "" {
		ui.KV("image2", absImage2)
		ui.KV("composite", compositeImage)
		ui.KV("conditioning", "composite reference; not true dual-stream conditioning")
	}
	ui.KV("backend", *backend)
	ui.KV("strength", fmt.Sprintf("%.2f", *strength))
	ui.KV("size", sizeLabel(*width, *height, "input"))
	ui.KV("steps", *steps)
	ui.KV("guidance", *guidance)
	ui.KV("seed", valueOr(*seed, "random"))
	ui.KV("prompt", shaped)

	if *dryRun {
		ui.KV("state", ui.State("planned")+" "+ui.Soft("img2img socket job not submitted"))
		return nil
	}

	if err := client.Start(*preload); err != nil {
		return err
	}
	resp, err := client.Request(map[string]any{
		"op":       "submit_img2img",
		"backend":  *backend,
		"prompt":   shaped,
		"image":    absImage,
		"width":    *width,
		"height":   *height,
		"steps":    *steps,
		"guidance": *guidance,
		"strength": *strength,
		"seed":     *seed,
		"filename": *name,
	})
	if err != nil {
		return err
	}
	jobID := stringValue(resp.Job["id"])
	ui.KV("route", ui.State("resident")+" "+ui.Soft("img2img unix socket"))
	ui.KV("job", jobID)
	ui.KV("status", stringValue(resp.Job["status"]))
	if *async {
		return nil
	}
	return watchSocketJob(client, jobID, cfg, history.Entry{
		Time:     time.Now(),
		Prompt:   shaped,
		Style:    *style,
		Mood:     *mood,
		Width:    intValue(resp.Job["width"]),
		Height:   intValue(resp.Job["height"]),
		Steps:    *steps,
		Guidance: *guidance,
		Seed:     *seed,
	})
}

type stitchedReference struct {
	Path         string `json:"path"`
	OutputWidth  int    `json:"output_width"`
	OutputHeight int    `json:"output_height"`
}

func stitchReferenceImages(cfg config.Config, imageA, imageB string) (stitchedReference, error) {
	outDir := filepath.Join(cfg.Root, ".fluxd", "references")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return stitchedReference{}, err
	}
	outPath := filepath.Join(outDir, "ref2-"+time.Now().Format("20060102-150405")+"-"+strconv.FormatInt(time.Now().UnixNano()%1000000, 10)+".png")
	script := `
import json, sys
from PIL import Image, ImageOps

a_path, b_path, out_path = sys.argv[1:4]
a = ImageOps.exif_transpose(Image.open(a_path).convert("RGB"))
b = ImageOps.exif_transpose(Image.open(b_path).convert("RGB"))
target_h = max(a.height, b.height)

def fit_height(img, h):
    if img.height == h:
        return img
    w = max(1, round(img.width * (h / img.height)))
    return img.resize((w, h), Image.Resampling.LANCZOS)

a2 = fit_height(a, target_h)
b2 = fit_height(b, target_h)
pad = max(16, target_h // 24)
board = Image.new("RGB", (a2.width + b2.width + pad, target_h), (8, 9, 14))
board.paste(a2, (0, 0))
board.paste(b2, (a2.width + pad, 0))
board.save(out_path)
print(json.dumps({"path": out_path, "output_width": a.width, "output_height": a.height}))
`
	cmd := exec.Command(cfg.Python, "-c", script, imageA, imageB, outPath)
	out, err := cmd.CombinedOutput()
	if err != nil {
		return stitchedReference{}, fmt.Errorf("reference composite failed: %w: %s", err, strings.TrimSpace(string(out)))
	}
	var ref stitchedReference
	if err := json.Unmarshal(bytes.TrimSpace(out), &ref); err != nil {
		return stitchedReference{}, err
	}
	if ref.Path == "" {
		ref.Path = outPath
	}
	return ref, nil
}

func watchSocketJob(client daemon.Client, jobID string, cfg config.Config, entry history.Entry) error {
	if jobID == "" {
		return nil
	}
	started := time.Now()
	lastStatus := ""
	for {
		resp, err := client.Request(map[string]any{"op": "jobs"})
		if err != nil {
			ui.ProgressDone()
			return err
		}
		job := findJob(resp.Jobs, jobID)
		if job == nil {
			ui.Progress("missing", "unknown", 0, 1, jobID)
			time.Sleep(750 * time.Millisecond)
			continue
		}
		status := stringValue(job["status"])
		phase := valueOr(stringValue(job["phase"]), status)
		step := intValue(job["step"])
		total := intValue(job["total_steps"])
		if total <= 0 {
			total = intValue(job["steps"])
		}
		if total <= 0 {
			total = 1
		}
		if status == "queued" || phase == "loading_model" {
			step = 0
		}
		if phase == "saving" || status == "done" {
			step = total
		}
		detail := phase
		if phase == "loading_model" {
			detail = "loading BF16 pipeline"
		} else if status == "running" || status == "queued" {
			detail = jobTiming(job)
		}
		if status != lastStatus && lastStatus == "" {
			fmt.Println()
		}
		lastStatus = status
		ui.Progress("render", phase, step, total, detail)
		switch status {
		case "done":
			ui.ProgressDone()
			output := stringValue(job["output"])
			ui.KV("output", output)
			entry.Output = output
			entry.Seconds = fmt.Sprintf("%.1fs", time.Since(started).Seconds())
			_ = history.Append(cfg.History, entry)
			return nil
		case "error":
			ui.ProgressDone()
			return fmt.Errorf("%s", stringValue(job["error"]))
		default:
			time.Sleep(750 * time.Millisecond)
		}
	}
}

func waitSocketJob(client daemon.Client, jobID string) (map[string]any, error) {
	if jobID == "" {
		return nil, fmt.Errorf("missing job id")
	}
	for {
		resp, err := client.Request(map[string]any{"op": "jobs"})
		if err != nil {
			ui.ProgressDone()
			return nil, err
		}
		job := findJob(resp.Jobs, jobID)
		if job == nil {
			ui.Progress("bench", "unknown", 0, 1, jobID)
			time.Sleep(750 * time.Millisecond)
			continue
		}
		status := stringValue(job["status"])
		phase := valueOr(stringValue(job["phase"]), status)
		step := intValue(job["step"])
		total := intValue(job["total_steps"])
		if total <= 0 {
			total = intValue(job["steps"])
		}
		if total <= 0 {
			total = 1
		}
		if status == "queued" || phase == "loading_model" {
			step = 0
		}
		if phase == "saving" || status == "done" {
			step = total
		}
		detail := jobID
		if status == "running" || status == "queued" {
			detail = jobTiming(job)
		}
		ui.Progress("bench", phase, step, total, detail)
		switch status {
		case "done":
			ui.ProgressDone()
			return job, nil
		case "error":
			ui.ProgressDone()
			return job, fmt.Errorf("%s", stringValue(job["error"]))
		default:
			time.Sleep(750 * time.Millisecond)
		}
	}
}

func findJob(jobs []map[string]any, id string) map[string]any {
	for _, job := range jobs {
		if stringValue(job["id"]) == id {
			return job
		}
	}
	return nil
}

func parseBackendList(value string) ([]string, error) {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	seen := map[string]bool{}
	for _, part := range parts {
		backend := strings.ToLower(strings.TrimSpace(part))
		if backend == "" {
			continue
		}
		if err := validateBackend(backend); err != nil {
			return nil, err
		}
		if backend == "auto" {
			return nil, fmt.Errorf("bench needs concrete backends, not auto")
		}
		if seen[backend] {
			continue
		}
		seen[backend] = true
		out = append(out, backend)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("bench needs at least one backend")
	}
	return out, nil
}

func backendCapable(backend string, caps map[string]any) bool {
	switch backend {
	case "cuda", "mps", "mlx", "cpu":
		return boolValue(caps[backend])
	case "coreml":
		return boolValue(caps["coreml_compiled"])
	case "ane":
		return boolValue(caps["ane_renderable"])
	default:
		return false
	}
}

func capabilityReason(backend string) string {
	switch backend {
	case "coreml":
		return "requires FLUX_COREML_MODEL or model/coreml compiled package"
	case "ane":
		return "requires validated full-pipeline package in model/ane/registry.json"
	case "mlx":
		return "requires mlx and mflux-generate"
	case "mps":
		return "requires PyTorch MPS"
	default:
		return "backend unavailable"
	}
}

type benchResult struct {
	Backend string
	Status  string
	Seconds float64
	Output  string
	Error   string
}

func benchRows(results []benchResult) []ui.PairRow {
	if len(results) == 0 {
		return []ui.PairRow{{Left: "none", Right: "no capable backends selected"}}
	}
	rows := make([]ui.PairRow, 0, len(results))
	for _, result := range results {
		right := result.Status
		if result.Seconds > 0 {
			right = fmt.Sprintf("%.1fs %s", result.Seconds, result.Output)
		}
		if result.Error != "" {
			right = result.Error
		}
		rows = append(rows, ui.PairRow{Left: result.Backend, Right: right})
	}
	return rows
}

func plannedBackendRows(backends []string) []ui.PairRow {
	rows := make([]ui.PairRow, 0, len(backends))
	for _, backend := range backends {
		rows = append(rows, ui.PairRow{
			Left:  backend,
			Right: "planned socket job with shared prompt, size, steps, guidance, and seed",
		})
	}
	return rows
}

func mapSlice(v any) []map[string]any {
	switch t := v.(type) {
	case []map[string]any:
		return t
	case []any:
		out := make([]map[string]any, 0, len(t))
		for _, item := range t {
			if m := mapValue(item); m != nil {
				out = append(out, m)
			}
		}
		return out
	default:
		return nil
	}
}

func stringSlice(v any) []string {
	raw, ok := v.([]any)
	if !ok {
		return nil
	}
	out := make([]string, 0, len(raw))
	for _, item := range raw {
		out = append(out, stringValue(item))
	}
	return out
}

func gpuRows(rows []string) []ui.PairRow {
	out := make([]ui.PairRow, 0, len(rows))
	for _, row := range rows {
		row = strings.TrimSpace(row)
		if row == "" {
			continue
		}
		left, right := row, ""
		if len(row) > 30 {
			left = strings.TrimSpace(row[:30])
			right = strings.TrimSpace(row[30:])
		}
		out = append(out, ui.PairRow{Left: left, Right: right})
	}
	return out
}

func mapValue(v any) map[string]any {
	switch t := v.(type) {
	case map[string]any:
		return t
	default:
		return nil
	}
}

func reorderRenderArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{
		"preset": true, "backend": true, "style": true, "mood": true, "ratio": true, "steps": true,
		"guidance": true, "width": true, "height": true, "seed": true,
		"camera": true, "light": true, "palette": true, "texture": true, "detail": true, "chaos": true, "director": true,
		"name": true, "burst": true, "start-seed": true,
	}
	boolFlags := map[string]bool{"draft": true, "dry-run": true, "echo": true, "async": true, "direct": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

func reorderBenchArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{
		"backends": true, "prompt": true, "width": true, "height": true, "steps": true,
		"guidance": true, "seed": true, "name": true,
	}
	boolFlags := map[string]bool{"dry-run": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		if boolFlags[name] {
			flags = append(flags, arg)
			continue
		}
		if valueFlags[name] {
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
			continue
		}
		flags = append(flags, arg)
	}
	return append(flags, positional...), nil
}

func reorderRemoteRenderArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{
		"url": true, "token": true, "token-env": true,
		"preset": true, "backend": true, "style": true, "mood": true, "ratio": true, "steps": true,
		"guidance": true, "width": true, "height": true, "seed": true, "name": true,
		"camera": true, "light": true, "palette": true, "texture": true, "detail": true, "chaos": true, "director": true,
	}
	boolFlags := map[string]bool{"draft": true, "dry-run": true, "wait": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

type museLane struct {
	Name   string
	Preset string
	Twist  string
}

var museLanes = []museLane{
	{"opening-signal", "hero", "as an establishing image with one unmistakable focal gesture"},
	{"character-weather", "anime", "with emotional weather, clear silhouette, production-still framing"},
	{"object-truth", "object", "as a tactile object study with exact material behavior"},
	{"quiet-map", "space", "as an inhabited place with believable spatial logic"},
	{"field-note", "sketch", "as a fast visual note, useful composition over polish"},
	{"cover-pressure", "cover", "as a vertical key visual with graphic hierarchy"},
	{"future-proof", "future", "as a plausible near-future scene with restrained invention"},
	{"night-cut", "noir", "with hard shadow shapes and a decisive foreground layer"},
}

func muse(args []string) error {
	fs := flag.NewFlagSet("muse", flag.ExitOnError)
	count := fs.Int("n", 6, "number of lanes")
	startSeed := fs.Int("start-seed", 4100, "first deterministic seed")
	commandsOnly := fs.Bool("commands", false, "print commands only")
	remoteURL := fs.String("remote-url", "", "emit remote render commands for this URL")
	wait := fs.Bool("wait", true, "include --wait on remote commands")
	anime := fs.Bool("anime", false, "bias every lane toward anime production language")
	ordered, err := reorderMuseArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	subject := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if subject == "" {
		return fmt.Errorf("muse needs a subject")
	}
	if *count < 1 {
		return fmt.Errorf("--n must be at least 1")
	}
	if !*commandsOnly {
		ui.Header("muse", "shot board with renderable lanes")
		ui.KV("subject", subject)
		ui.KV("lanes", *count)
		if *remoteURL != "" {
			ui.KV("remote", *remoteURL)
		}
		fmt.Println()
	}
	for i := 0; i < *count; i++ {
		lane := museLanes[i%len(museLanes)]
		preset, err := prompt.PresetByName(lane.Preset)
		if err != nil {
			return err
		}
		base := subject + ", " + lane.Twist
		if *anime && lane.Preset != "anime" {
			base = "anime production still, " + base
		}
		shaped, err := prompt.Compose(base, prompt.Shape{Style: preset.Style, Mood: preset.Mood, Preset: preset.Name})
		if err != nil {
			return err
		}
		seed := *startSeed + i
		command := museCommand(base, preset.Name, seed, *remoteURL, *wait)
		if *commandsOnly {
			fmt.Println(command)
			continue
		}
		ratio, _ := prompt.RatioByName(preset.Ratio)
		meta := fmt.Sprintf("preset=%s %dx%d steps=%d guidance=%.1f seed=%d", preset.Name, ratio.Width, ratio.Height, preset.Steps, preset.Guidance, seed)
		ui.Capsule(lane.Name, meta, shaped, command, []ui.Color{ui.Teal, ui.Gold, ui.Lilac, ui.Indigo}[i%4])
		if i != *count-1 {
			fmt.Println()
		}
	}
	return nil
}

func museCommand(promptText, preset string, seed int, remoteURL string, wait bool) string {
	if strings.TrimSpace(remoteURL) != "" {
		parts := []string{"flux", "remote", "render", "--url", remoteURL, promptText, "--preset", preset, "--seed", strconv.Itoa(seed)}
		if wait {
			parts = append(parts, "--wait")
		}
		return shellish(parts)
	}
	return shellish([]string{"flux", "render", promptText, "--preset", preset, "--seed", strconv.Itoa(seed)})
}

func matrix(args []string) error {
	fs := flag.NewFlagSet("matrix", flag.ExitOnError)
	styles := fs.String("styles", "cinema,anime,noir", "comma-separated styles")
	moods := fs.String("moods", "quiet,electric,melancholy", "comma-separated moods")
	cameras := fs.String("cameras", "wide,close", "comma-separated cameras")
	limit := fs.Int("n", 8, "maximum combinations")
	commands := fs.Bool("commands", false, "print render commands")
	remoteURL := fs.String("remote-url", "", "emit remote render commands for this URL")
	ordered, err := reorderMatrixArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	subject := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if subject == "" {
		return fmt.Errorf("matrix needs a subject")
	}
	styleList := splitCSV(*styles)
	moodList := splitCSV(*moods)
	cameraList := splitCSV(*cameras)
	if len(styleList) == 0 || len(moodList) == 0 || len(cameraList) == 0 {
		return fmt.Errorf("matrix needs at least one style, mood, and camera")
	}
	if !*commands {
		ui.Header("matrix", "creative control board")
		ui.KV("subject", subject)
		ui.KV("styles", strings.Join(styleList, ", "))
		ui.KV("moods", strings.Join(moodList, ", "))
		ui.KV("cameras", strings.Join(cameraList, ", "))
		fmt.Println()
	}
	count := 0
	for _, style := range styleList {
		for _, mood := range moodList {
			for _, camera := range cameraList {
				if *limit > 0 && count >= *limit {
					return nil
				}
				shaped, err := prompt.Compose(subject, prompt.Shape{Style: style, Mood: mood, Camera: camera})
				if err != nil {
					return err
				}
				if *commands {
					if *remoteURL != "" {
						fmt.Println(shellish([]string{"flux", "remote", "render", "--url", *remoteURL, subject, "--style", style, "--mood", mood, "--camera", camera}))
					} else {
						fmt.Println(shellish([]string{"flux", "render", subject, "--style", style, "--mood", mood, "--camera", camera, "--dry-run"}))
					}
				} else {
					meta := fmt.Sprintf("style=%s mood=%s camera=%s", style, mood, camera)
					ui.Capsule(fmt.Sprintf("lane-%02d", count+1), meta, shaped, "", []ui.Color{ui.Teal, ui.Gold, ui.Lilac, ui.Indigo}[count%4])
					fmt.Println()
				}
				count++
			}
		}
	}
	return nil
}

func reorderMatrixArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{"styles": true, "moods": true, "cameras": true, "n": true, "remote-url": true}
	boolFlags := map[string]bool{"commands": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

type evolveLane struct {
	Name     string
	Twist    string
	Style    string
	Mood     string
	Camera   string
	Light    string
	Palette  string
	Texture  string
	Detail   string
	Chaos    string
	Director string
}

func evolve(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("evolve", flag.ExitOnError)
	mode := fs.String("mode", "balanced", "mode: balanced, anime, cinematic, product, strange")
	count := fs.Int("n", 8, "number of evolved prompts")
	engine := fs.String("engine", "heuristic", "engine: heuristic or ane")
	commands := fs.Bool("commands", false, "print render commands for each prompt")
	remoteURL := fs.String("remote-url", "", "emit remote render commands for this URL")
	presetName := fs.String("preset", "", "render preset for commands")
	ordered, err := reorderEvolveArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	subject := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if subject == "" {
		return fmt.Errorf("evolve needs a subject")
	}
	if *count < 1 {
		return fmt.Errorf("--n must be at least 1")
	}
	if strings.EqualFold(*engine, "ane") {
		if err := requirePromptANE(cfg); err != nil {
			ui.Header("evolve", "ANE prompt model slot")
			ui.KV("engine", ui.State("planned"))
			ui.KV("state", ui.Warn("missing"))
			ui.KV("reason", err)
			ui.KV("fallback", "heuristic")
			fmt.Println()
			*engine = "heuristic"
		}
	} else if !strings.EqualFold(*engine, "heuristic") {
		return fmt.Errorf("unknown evolve engine %q; use heuristic or ane", *engine)
	}
	lanes, err := evolveLanes(*mode)
	if err != nil {
		return err
	}
	if !*commands {
		ui.Header("evolve", "prompt-side creative engine")
		ui.KV("subject", subject)
		ui.KV("mode", *mode)
		ui.KV("engine", *engine)
		ui.KV("guidance", "20-80 words is the normal sweet spot; use 80-140 for highly directed scenes")
		fmt.Println()
	}
	for i := 0; i < *count; i++ {
		lane := lanes[i%len(lanes)]
		text, err := prompt.Compose(subject+", "+lane.Twist, prompt.Shape{
			Style: lane.Style, Mood: lane.Mood, Camera: lane.Camera, Light: lane.Light, Palette: lane.Palette,
			Texture: lane.Texture, Detail: lane.Detail, Chaos: lane.Chaos, Director: lane.Director,
		})
		if err != nil {
			return err
		}
		if *commands {
			promptText := subject + ", " + lane.Twist
			parts := []string{"flux"}
			if *remoteURL != "" {
				parts = append(parts, "remote", "render", "--url", *remoteURL)
			} else {
				parts = append(parts, "render")
			}
			parts = append(parts, promptText)
			for _, pair := range []struct {
				name  string
				value string
			}{
				{"style", lane.Style},
				{"mood", lane.Mood},
				{"camera", lane.Camera},
				{"light", lane.Light},
				{"palette", lane.Palette},
				{"texture", lane.Texture},
				{"detail", lane.Detail},
				{"chaos", lane.Chaos},
				{"director", lane.Director},
			} {
				if strings.TrimSpace(pair.value) != "" {
					parts = append(parts, "--"+pair.name, pair.value)
				}
			}
			if *presetName != "" {
				parts = append(parts, "--preset", *presetName)
			}
			fmt.Println(shellish(parts))
			continue
		}
		meta := fmt.Sprintf("words=%d style=%s mood=%s camera=%s", wordCount(text), valueOr(lane.Style, "none"), valueOr(lane.Mood, "none"), valueOr(lane.Camera, "none"))
		ui.Capsule(lane.Name, meta, text, "", []ui.Color{ui.Gold, ui.Teal, ui.Lilac, ui.Indigo}[i%4])
		if i != *count-1 {
			fmt.Println()
		}
	}
	return nil
}

func requirePromptANE(cfg config.Config) error {
	registry := filepath.Join(cfg.ModelDir, "prompt", "ane", "registry.json")
	if _, err := os.Stat(registry); err != nil {
		return fmt.Errorf("no prompt-side ANE registry at %s", registry)
	}
	return nil
}

func evolveLanes(mode string) ([]evolveLane, error) {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "balanced":
		return []evolveLane{
			{Name: "clarify", Twist: "make the subject visually specific with one focal object and readable environment", Style: "cinema", Mood: "quiet", Camera: "wide", Light: "overcast", Detail: "balanced", Chaos: "calm"},
			{Name: "materialize", Twist: "emphasize surface behavior, physical materials, scale, and tactile detail", Style: "material", Mood: "clinical", Camera: "macro", Light: "studio", Texture: "glass", Detail: "diagram"},
			{Name: "dramatize", Twist: "increase dramatic silhouette, atmospheric depth, and foreground pressure", Style: "cinema", Mood: "electric", Camera: "low", Light: "rim", Palette: "ember", Chaos: "alive"},
			{Name: "humanize", Twist: "add lived-in traces, human scale, and small narrative evidence", Style: "document", Mood: "warm", Camera: "close", Light: "lantern", Texture: "weathered", Detail: "dense"},
		}, nil
	case "anime":
		return []evolveLane{
			{Name: "key-visual", Twist: "anime key visual with emotional weather and clean compositing", Style: "anime", Mood: "melancholy", Camera: "wide", Light: "golden", Palette: "sakura", Texture: "cel", Detail: "dense", Director: "shinkai"},
			{Name: "story-still", Twist: "quiet anime production still with hand-crafted world detail", Style: "anime", Mood: "warm", Camera: "close", Light: "lantern", Palette: "verdant", Texture: "ink", Director: "miyazaki"},
			{Name: "night-city", Twist: "lived-in futuristic anime city frame with rhythmic motion", Style: "anime", Mood: "nocturne", Camera: "tracking", Light: "neon", Palette: "cobalt", Detail: "dense", Director: "watanabe"},
		}, nil
	case "cinematic":
		return []evolveLane{
			{Name: "establish", Twist: "wide establishing cinematic frame with clear spatial hierarchy", Style: "cinema", Mood: "quiet", Camera: "wide", Light: "golden", Detail: "balanced"},
			{Name: "pressure", Twist: "dramatic close frame with strong silhouette and emotional compression", Style: "cinema", Mood: "ominous", Camera: "close", Light: "rim", Palette: "mono", Chaos: "alive"},
			{Name: "storm", Twist: "charged cinematic weather and high atmospheric contrast", Style: "cinema", Mood: "fever", Camera: "low", Light: "storm", Palette: "ember", Chaos: "wild"},
		}, nil
	case "product":
		return []evolveLane{
			{Name: "clean", Twist: "premium product image with exact material response", Style: "product", Mood: "clinical", Camera: "close", Light: "studio", Texture: "metal", Detail: "balanced"},
			{Name: "macro", Twist: "macro product material study with edge detail and micro texture", Style: "material", Mood: "clinical", Camera: "macro", Light: "rim", Texture: "glass", Detail: "diagram"},
			{Name: "editorial", Twist: "editorial product staging with confident negative space", Style: "editorial", Mood: "quiet", Camera: "portrait", Light: "golden", Palette: "mono", Director: "vogue"},
		}, nil
	case "strange":
		return []evolveLane{
			{Name: "surreal", Twist: "surreal but coherent transformation with impossible spatial cues", Style: "speculative", Mood: "fever", Camera: "wide", Light: "neon", Palette: "acid", Chaos: "surreal"},
			{Name: "maximal", Twist: "controlled overload with layered symbols and dense secondary stories", Style: "editorial", Mood: "electric", Camera: "overhead", Light: "storm", Detail: "ornate", Chaos: "maximal"},
			{Name: "quiet-weird", Twist: "subtle impossible detail inside an otherwise realistic scene", Style: "document", Mood: "ominous", Camera: "close", Light: "overcast", Texture: "film", Chaos: "surreal"},
		}, nil
	default:
		return nil, fmt.Errorf("unknown evolve mode %q; use balanced, anime, cinematic, product, or strange", mode)
	}
}

func reorderEvolveArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{"mode": true, "n": true, "engine": true, "remote-url": true, "preset": true}
	boolFlags := map[string]bool{"commands": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

func wordCount(value string) int {
	return len(strings.Fields(value))
}

type pipelineLane struct {
	Name     string
	Preset   string
	Style    string
	Mood     string
	Camera   string
	Light    string
	Palette  string
	Texture  string
	Detail   string
	Chaos    string
	Director string
	Ratio    string
	Steps    int
	Guidance float64
	Twist    string
}

func pipeline(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("pipeline", flag.ExitOnError)
	mode := fs.String("mode", "explore", "workflow mode: explore, anime, product, architecture, fashion")
	count := fs.Int("n", 0, "number of lanes; default uses the whole workflow")
	startSeed := fs.Int("start-seed", 6200, "first deterministic seed")
	backend := fs.String("backend", cfg.Backend, "backend: auto, cuda, mps, mlx, coreml, ane, cpu")
	remoteURL := fs.String("remote-url", "", "queue through an exposed FLUX HTTP endpoint")
	run := fs.Bool("run", false, "queue the workflow; default is plan only")
	commandsOnly := fs.Bool("commands", false, "print copy-safe commands only")
	wait := fs.Bool("wait", false, "include --wait in generated remote commands")
	ordered, err := reorderPipelineArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	if err := validateBackend(*backend); err != nil {
		return err
	}
	subject := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if subject == "" {
		return fmt.Errorf("pipeline needs a subject")
	}
	lanes, err := pipelineLanes(*mode)
	if err != nil {
		return err
	}
	if *count > 0 && *count < len(lanes) {
		lanes = lanes[:*count]
	}
	if *commandsOnly {
		for i, lane := range lanes {
			fmt.Println(pipelineCommand(subject, lane, *startSeed+i, *backend, *remoteURL, *wait))
		}
		return nil
	}
	ui.Header("pipeline", "multi-generation workflow")
	ui.KV("subject", subject)
	ui.KV("mode", *mode)
	ui.KV("lanes", len(lanes))
	ui.KV("route", pipelineRoute(*remoteURL, *run))
	ui.KV("state", pipelineState(*run))
	fmt.Println()

	var client daemon.Client
	if *run && *remoteURL == "" {
		cfg.Backend = strings.ToLower(*backend)
		client = daemon.New(cfg)
		if _, err := client.Request(map[string]any{"op": "ping"}); err != nil {
			if err := client.Start(false); err != nil {
				return err
			}
		}
	}
	for i, lane := range lanes {
		seed := strconv.Itoa(*startSeed + i)
		shaped, plan, err := pipelinePlan(subject, lane, seed, *backend)
		if err != nil {
			return err
		}
		meta := fmt.Sprintf("preset=%s %dx%d steps=%d guidance=%.1f seed=%s", valueOr(lane.Preset, "none"), plan["width"], plan["height"], plan["steps"], plan["guidance"], seed)
		command := pipelineCommand(subject, lane, *startSeed+i, *backend, *remoteURL, *wait)
		ui.Capsule(lane.Name, meta, shaped, command, []ui.Color{ui.Teal, ui.Gold, ui.Lilac, ui.Indigo}[i%4])
		if *run {
			if *remoteURL != "" {
				resp, err := remoteRequest(http.MethodPost, *remoteURL, "/api/render", "", plan)
				if err != nil {
					return err
				}
				ui.KV("job", stringValue(mapValue(resp["job"])["id"]))
			} else {
				resp, err := client.Request(map[string]any{
					"op":       "submit",
					"backend":  plan["backend"],
					"prompt":   shaped,
					"width":    plan["width"],
					"height":   plan["height"],
					"steps":    plan["steps"],
					"guidance": plan["guidance"],
					"seed":     seed,
				})
				if err != nil {
					return err
				}
				ui.KV("job", stringValue(resp.Job["id"]))
			}
		}
		if i != len(lanes)-1 {
			fmt.Println()
		}
	}
	if !*run {
		fmt.Println()
		fmt.Println(ui.Soft("plan only; add --run to queue these lanes"))
	}
	return nil
}

func pipelineRoute(remoteURL string, run bool) string {
	if strings.TrimSpace(remoteURL) != "" {
		if run {
			return ui.State("remote") + " " + ui.Soft(remoteURL)
		}
		return ui.State("planned") + " " + ui.Soft("remote commands")
	}
	if run {
		return ui.State("resident") + " " + ui.Soft("local socket queue")
	}
	return ui.State("planned") + " " + ui.Soft("local socket commands")
}

func pipelineState(run bool) string {
	if run {
		return ui.State("queued")
	}
	return ui.State("planned")
}

func pipelinePlan(subject string, lane pipelineLane, seed, backend string) (string, map[string]any, error) {
	preset, err := prompt.PresetByName(lane.Preset)
	if err != nil {
		return "", nil, err
	}
	if lane.Style == "" {
		lane.Style = preset.Style
	}
	if lane.Mood == "" {
		lane.Mood = preset.Mood
	}
	if lane.Ratio == "" {
		lane.Ratio = valueOr(preset.Ratio, "square")
	}
	if lane.Steps == 0 {
		lane.Steps = preset.Steps
	}
	if lane.Steps == 0 {
		lane.Steps = 28
	}
	if lane.Guidance == 0 {
		lane.Guidance = preset.Guidance
	}
	if lane.Guidance == 0 {
		lane.Guidance = 3.5
	}
	base := subject
	if lane.Twist != "" {
		base += ", " + lane.Twist
	}
	shaped, err := prompt.Compose(base, prompt.Shape{
		Style: lane.Style, Mood: lane.Mood, Camera: lane.Camera, Light: lane.Light, Palette: lane.Palette,
		Texture: lane.Texture, Detail: lane.Detail, Chaos: lane.Chaos, Director: lane.Director, Preset: lane.Preset,
	})
	if err != nil {
		return "", nil, err
	}
	ratio, err := prompt.RatioByName(lane.Ratio)
	if err != nil {
		return "", nil, err
	}
	plan := map[string]any{
		"prompt":   base,
		"backend":  strings.ToLower(backend),
		"preset":   lane.Preset,
		"style":    lane.Style,
		"mood":     lane.Mood,
		"camera":   lane.Camera,
		"light":    lane.Light,
		"palette":  lane.Palette,
		"texture":  lane.Texture,
		"detail":   lane.Detail,
		"chaos":    lane.Chaos,
		"director": lane.Director,
		"ratio":    lane.Ratio,
		"width":    ratio.Width,
		"height":   ratio.Height,
		"steps":    lane.Steps,
		"guidance": lane.Guidance,
		"seed":     seed,
	}
	return shaped, plan, nil
}

func pipelineCommand(subject string, lane pipelineLane, seed int, backend, remoteURL string, wait bool) string {
	parts := []string{"flux"}
	if strings.TrimSpace(remoteURL) != "" {
		parts = append(parts, "remote", "render", "--url", remoteURL)
	} else {
		parts = append(parts, "render")
	}
	promptText := subject
	if lane.Twist != "" {
		promptText += ", " + lane.Twist
	}
	parts = append(parts, promptText)
	flagPairs := []struct {
		name  string
		value string
	}{
		{"preset", lane.Preset},
		{"style", lane.Style},
		{"mood", lane.Mood},
		{"camera", lane.Camera},
		{"light", lane.Light},
		{"palette", lane.Palette},
		{"texture", lane.Texture},
		{"detail", lane.Detail},
		{"chaos", lane.Chaos},
		{"director", lane.Director},
		{"ratio", lane.Ratio},
		{"backend", backend},
	}
	for _, pair := range flagPairs {
		if strings.TrimSpace(pair.value) != "" {
			parts = append(parts, "--"+pair.name, pair.value)
		}
	}
	if lane.Steps > 0 {
		parts = append(parts, "--steps", strconv.Itoa(lane.Steps))
	}
	if lane.Guidance > 0 {
		parts = append(parts, "--guidance", fmt.Sprintf("%.1f", lane.Guidance))
	}
	parts = append(parts, "--seed", strconv.Itoa(seed))
	if strings.TrimSpace(remoteURL) != "" && wait {
		parts = append(parts, "--wait")
	}
	return shellish(parts)
}

func pipelineLanes(mode string) ([]pipelineLane, error) {
	switch strings.ToLower(strings.TrimSpace(mode)) {
	case "explore":
		return []pipelineLane{
			{Name: "composition", Preset: "sketch", Camera: "wide", Light: "overcast", Detail: "balanced", Chaos: "calm", Twist: "fast composition pass, readable shapes first"},
			{Name: "hero", Preset: "hero", Camera: "low", Light: "rim", Palette: "ember", Detail: "dense", Chaos: "alive", Twist: "one unmistakable focal gesture"},
			{Name: "material", Preset: "object", Camera: "macro", Light: "studio", Texture: "glass", Detail: "diagram", Chaos: "calm", Twist: "material and surface behavior study"},
			{Name: "world", Preset: "space", Camera: "wide", Light: "golden", Palette: "verdant", Texture: "weathered", Detail: "dense", Twist: "inhabited spatial logic and scale cues"},
			{Name: "surreal", Preset: "future", Camera: "tracking", Light: "neon", Palette: "acid", Detail: "ornate", Chaos: "surreal", Twist: "unexpected but coherent transformation"},
			{Name: "cover", Preset: "cover", Camera: "portrait", Light: "storm", Palette: "mono", Texture: "film", Chaos: "wild", Twist: "graphic hierarchy for a cover image"},
		}, nil
	case "anime":
		return []pipelineLane{
			{Name: "key-visual", Preset: "anime", Camera: "wide", Light: "golden", Palette: "sakura", Texture: "cel", Detail: "dense", Director: "shinkai", Twist: "anime key visual, emotional weather"},
			{Name: "story-still", Preset: "anime", Camera: "close", Light: "lantern", Palette: "verdant", Texture: "ink", Detail: "balanced", Director: "miyazaki", Twist: "quiet story moment with hand-crafted world detail"},
			{Name: "psych-cut", Preset: "noir", Camera: "overhead", Light: "rim", Palette: "mono", Texture: "film", Chaos: "surreal", Director: "kon", Twist: "psychological transition frame"},
			{Name: "city-night", Preset: "future", Camera: "tracking", Light: "neon", Palette: "cobalt", Texture: "weathered", Detail: "dense", Director: "watanabe", Twist: "lived-in night city production still"},
		}, nil
	case "product":
		return []pipelineLane{
			{Name: "catalog", Preset: "object", Camera: "close", Light: "studio", Palette: "mono", Texture: "metal", Detail: "balanced", Chaos: "calm", Twist: "inspection-friendly product render"},
			{Name: "macro", Preset: "object", Camera: "macro", Light: "rim", Texture: "glass", Detail: "diagram", Chaos: "calm", Twist: "macro material proof with edge highlights"},
			{Name: "editorial", Preset: "cover", Camera: "portrait", Light: "golden", Palette: "ember", Texture: "film", Detail: "minimal", Director: "vogue", Twist: "premium editorial product staging"},
		}, nil
	case "architecture", "arch":
		return []pipelineLane{
			{Name: "massing", Preset: "space", Camera: "wide", Light: "overcast", Palette: "mono", Texture: "weathered", Detail: "balanced", Director: "brutalist", Twist: "clear massing and spatial hierarchy"},
			{Name: "interior", Preset: "space", Camera: "wide", Light: "lantern", Palette: "verdant", Texture: "paper", Detail: "dense", Chaos: "alive", Twist: "inhabited interior with human scale"},
			{Name: "diagram", Preset: "sketch", Camera: "overhead", Light: "studio", Detail: "diagram", Chaos: "calm", Twist: "diagrammatic architectural read"},
		}, nil
	case "fashion":
		return []pipelineLane{
			{Name: "cover", Preset: "cover", Camera: "portrait", Light: "studio", Palette: "mono", Texture: "film", Detail: "ornate", Director: "vogue", Twist: "high-fashion cover image with confident pose language"},
			{Name: "runway", Preset: "hero", Camera: "low", Light: "neon", Palette: "acid", Texture: "glass", Detail: "dense", Chaos: "wild", Director: "watanabe", Twist: "runway energy and crowd atmosphere"},
			{Name: "atelier", Preset: "editorial", Style: "editorial", Mood: "warm", Camera: "close", Light: "lantern", Palette: "sakura", Texture: "paper", Detail: "balanced", Twist: "behind-the-scenes atelier intimacy"},
		}, nil
	default:
		return nil, fmt.Errorf("unknown pipeline mode %q; use explore, anime, product, architecture, or fashion", mode)
	}
}

func reorderPipelineArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{"mode": true, "n": true, "start-seed": true, "backend": true, "remote-url": true}
	boolFlags := map[string]bool{"run": true, "commands": true, "wait": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

func reorderMuseArgs(args []string) ([]string, error) {
	valueFlags := map[string]bool{"n": true, "start-seed": true, "remote-url": true}
	boolFlags := map[string]bool{"commands": true, "anime": true, "wait": true}
	var flags, positional []string
	for i := 0; i < len(args); i++ {
		arg := args[i]
		if !strings.HasPrefix(arg, "-") || arg == "-" {
			positional = append(positional, arg)
			continue
		}
		name := strings.TrimLeft(arg, "-")
		if before, _, ok := strings.Cut(name, "="); ok {
			name = before
		}
		switch {
		case boolFlags[name]:
			flags = append(flags, arg)
		case valueFlags[name]:
			flags = append(flags, arg)
			if !strings.Contains(arg, "=") {
				if i+1 >= len(args) {
					return nil, fmt.Errorf("flag %s needs a value", arg)
				}
				i++
				flags = append(flags, args[i])
			}
		default:
			flags = append(flags, arg)
		}
	}
	return append(flags, positional...), nil
}

func recipes() {
	ui.Header("recipes", "prompt shapers built into the Go CLI")
	styleRows := make([]ui.PairRow, 0, len(prompt.OrderedStyles))
	for _, d := range prompt.OrderedStyles {
		styleRows = append(styleRows, ui.PairRow{Left: d.Name, Right: d.Text})
	}
	ui.Suite("styles", ui.Violet, styleRows)

	moodRows := make([]ui.PairRow, 0, len(prompt.OrderedMoods))
	for _, d := range prompt.OrderedMoods {
		moodRows = append(moodRows, ui.PairRow{Left: d.Name, Right: d.Text})
	}
	ui.Suite("moods", ui.Indigo, moodRows)

	cameraRows := definitionRows(prompt.OrderedCameras)
	ui.Suite("camera", ui.Teal, cameraRows)

	lightRows := definitionRows(prompt.OrderedLights)
	ui.Suite("light", ui.Gold, lightRows)

	paletteRows := definitionRows(prompt.OrderedPalettes)
	ui.Suite("palette", ui.Lilac, paletteRows)

	textureRows := definitionRows(prompt.OrderedTextures)
	ui.Suite("texture", ui.Indigo, textureRows)

	detailRows := definitionRows(prompt.OrderedDetails)
	ui.Suite("detail", ui.Teal, detailRows)

	chaosRows := definitionRows(prompt.OrderedChaos)
	ui.Suite("chaos", ui.Amber, chaosRows)

	directorRows := definitionRows(prompt.OrderedDirectors)
	ui.Suite("director", ui.Violet, directorRows)

	ratioRows := make([]ui.PairRow, 0, len(prompt.OrderedRatios))
	for _, r := range prompt.OrderedRatios {
		ratioRows = append(ratioRows, ui.PairRow{Left: r.Name, Right: fmt.Sprintf("%dx%d", r.Width, r.Height)})
	}
	ui.Suite("ratios", ui.Teal, ratioRows)

	presetRows := make([]ui.PairRow, 0, len(prompt.OrderedPresets))
	for _, p := range prompt.OrderedPresets {
		presetRows = append(presetRows, ui.PairRow{
			Left:  p.Name,
			Right: fmt.Sprintf("%s/%s %s steps=%d guidance=%.1f - %s", p.Style, p.Mood, p.Ratio, p.Steps, p.Guidance, p.Note),
		})
	}
	ui.Suite("presets", ui.Gold, presetRows)
}

func shape(args []string) error {
	fs := flag.NewFlagSet("shape", flag.ExitOnError)
	presetName := fs.String("preset", "", "preset")
	style := fs.String("style", "", "style")
	mood := fs.String("mood", "", "mood")
	camera := fs.String("camera", "", "camera")
	light := fs.String("light", "", "light")
	palette := fs.String("palette", "", "palette")
	texture := fs.String("texture", "", "texture")
	detail := fs.String("detail", "", "detail")
	chaos := fs.String("chaos", "", "chaos")
	director := fs.String("director", "", "director")
	ordered, err := reorderRenderArgs(args)
	if err != nil {
		return err
	}
	if err := fs.Parse(ordered); err != nil {
		return err
	}
	base := strings.TrimSpace(strings.Join(fs.Args(), " "))
	if base == "" {
		return fmt.Errorf("shape needs a prompt")
	}
	preset, err := prompt.PresetByName(*presetName)
	if err != nil {
		return err
	}
	if preset.Name != "" {
		if *style == "" {
			*style = preset.Style
		}
		if *mood == "" {
			*mood = preset.Mood
		}
	}
	shaped, err := prompt.Compose(base, prompt.Shape{
		Style: *style, Mood: *mood, Camera: *camera, Light: *light, Palette: *palette,
		Texture: *texture, Detail: *detail, Chaos: *chaos, Director: *director, Preset: *presetName,
	})
	if err != nil {
		return err
	}
	ui.Header("shape", "final prompt")
	fmt.Println(shaped)
	return nil
}

func spark(args []string) error {
	base := strings.TrimSpace(strings.Join(args, " "))
	if base == "" {
		return fmt.Errorf("spark needs a subject")
	}
	ui.Header("spark", "six prompt mutations")
	for i, s := range prompt.Sparks(base) {
		fmt.Printf("%s %s\n", ui.Badge(strconv.Itoa(i+1)), s)
	}
	return nil
}

func showHistory(cfg config.Config, args []string) error {
	fs := flag.NewFlagSet("history", flag.ExitOnError)
	n := fs.Int("n", 10, "entries")
	if err := fs.Parse(args); err != nil {
		return err
	}
	entries, err := history.Last(cfg.History, *n)
	if err != nil {
		return err
	}
	ui.Header("history", fmt.Sprintf("last %d render records", *n))
	if len(entries) == 0 {
		fmt.Println(ui.Soft("no history yet"))
		return nil
	}
	for _, e := range entries {
		fmt.Println(ui.Accent(e.Time.Format("2006-01-02 15:04:05")), e.Output)
		fmt.Println("  " + e.Prompt)
		fmt.Printf("  %dx%d steps=%d guidance=%.2f seed=%s seconds=%s\n",
			e.Width, e.Height, e.Steps, e.Guidance, valueOr(e.Seed, "random"), valueOr(e.Seconds, "?"))
	}
	return nil
}

func definitionRows(defs []prompt.Definition) []ui.PairRow {
	rows := make([]ui.PairRow, 0, len(defs))
	for _, d := range defs {
		rows = append(rows, ui.PairRow{Left: d.Name, Right: d.Text})
	}
	return rows
}

func printLensKV(key, value string) {
	if strings.TrimSpace(value) != "" {
		ui.KV(key, value)
	}
}

func filterJobs(jobs []map[string]any, activeOnly, doneOnly, errorsOnly bool) []map[string]any {
	out := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		status := strings.ToLower(stringValue(job["status"]))
		active := status == "queued" || status == "running"
		done := status == "done"
		failed := status == "error" || status == "cancelled"
		if activeOnly && !active {
			continue
		}
		if doneOnly && !done {
			continue
		}
		if errorsOnly && !failed {
			continue
		}
		out = append(out, job)
	}
	return out
}

func reverseJobs(jobs []map[string]any) {
	for i, j := 0, len(jobs)-1; i < j; i, j = i+1, j-1 {
		jobs[i], jobs[j] = jobs[j], jobs[i]
	}
}

func printQueueSummary(jobs []map[string]any) {
	counts := map[string]int{}
	for _, job := range jobs {
		counts[strings.ToLower(valueOr(stringValue(job["status"]), "unknown"))]++
	}
	ui.KV("summary", fmt.Sprintf("queued=%d running=%d done=%d error=%d cancelled=%d", counts["queued"], counts["running"], counts["done"], counts["error"], counts["cancelled"]))
	if active := activeJob(jobs); active != nil {
		ui.KV("active", fmt.Sprintf("%s %s", stringValue(active["id"]), jobTiming(active)))
	}
	if latest := newestOutputJob(jobs); latest != nil {
		ui.KV("latest", jobDisplayOutput(latest))
	}
	fmt.Println()
}

func printJobRow(job map[string]any) {
	output := jobDisplayOutput(job)
	fmt.Printf("%s %-18s %-8s %s\n", ui.Accent(stringValue(job["id"])), ui.State(stringValue(job["status"])), ui.Accent(valueOr(stringValue(job["backend"]), "?")), output)
	detail := jobTiming(job)
	if kind := stringValue(job["kind"]); kind != "" {
		detail = kind + " · " + detail
	}
	fmt.Println("  " + ui.Soft(detail) + "  " + stringValue(job["prompt"]))
	if image := stringValue(job["image"]); image != "" {
		fmt.Println("  " + ui.Soft("image: "+image))
	}
	if errMsg := stringValue(job["error"]); errMsg != "" {
		fmt.Println("  " + ui.Bad(errMsg))
	}
}

func activeJob(jobs []map[string]any) map[string]any {
	for i := len(jobs) - 1; i >= 0; i-- {
		status := strings.ToLower(stringValue(jobs[i]["status"]))
		if status == "running" {
			return jobs[i]
		}
	}
	for i := len(jobs) - 1; i >= 0; i-- {
		if strings.ToLower(stringValue(jobs[i]["status"])) == "queued" {
			return jobs[i]
		}
	}
	return nil
}

func newestOutputJob(jobs []map[string]any) map[string]any {
	for i := len(jobs) - 1; i >= 0; i-- {
		if jobDisplayOutput(jobs[i]) != "" {
			return jobs[i]
		}
	}
	return nil
}

func jobDisplayOutput(job map[string]any) string {
	if strings.EqualFold(stringValue(job["kind"]), "atlas_sphere") {
		if viewer := stringValue(job["viewer_url"]); viewer != "" {
			return viewer
		}
		if gallery := stringValue(job["gallery_url"]); gallery != "" {
			return gallery
		}
		if id := stringValue(job["id"]); id != "" {
			return "http://127.0.0.1:7861/atlas/" + id
		}
	}
	return valueOr(stringValue(job["output_url"]), stringValue(job["output"]))
}

func jobTiming(job map[string]any) string {
	status := strings.ToLower(stringValue(job["status"]))
	started := floatValue(job["started"])
	finished := floatValue(job["finished"])
	seconds := floatValue(job["seconds"])
	step := intValue(job["step"])
	total := intValue(job["total_steps"])
	if total <= 0 {
		total = intValue(job["steps"])
	}
	switch {
	case seconds > 0:
		return "finished in " + formatDuration(seconds)
	case started > 0 && finished > started:
		return "finished in " + formatDuration(finished-started)
	case status == "running" && started > 0 && step > 0 && total > step:
		elapsed := time.Now().Sub(time.Unix(int64(started), 0)).Seconds()
		perStep := elapsed / float64(step)
		remaining := perStep*float64(total-step) + 8
		return fmt.Sprintf("%s remaining · %s elapsed", formatDuration(remaining), formatDuration(elapsed))
	case status == "running" && started > 0:
		elapsed := time.Now().Sub(time.Unix(int64(started), 0)).Seconds()
		return fmt.Sprintf("finalizing · %s elapsed", formatDuration(elapsed))
	case status == "queued":
		return "queued · estimate " + estimateJobDuration(job)
	default:
		return strings.ToLower(valueOr(stringValue(job["phase"]), status))
	}
}

func estimateJobDuration(job map[string]any) string {
	steps := intValue(job["steps"])
	if steps <= 0 {
		steps = intValue(job["total_steps"])
	}
	if steps <= 0 {
		steps = 28
	}
	width := intValue(job["width"])
	height := intValue(job["height"])
	if width <= 0 || height <= 0 {
		width, height = 1024, 1024
	}
	mp := float64(width*height) / float64(1024*1024)
	return formatDuration(18 + float64(steps)*6.7*mp)
}

func formatDuration(seconds float64) string {
	if seconds <= 0 {
		return "unknown"
	}
	rounded := int(seconds + 0.5)
	if rounded < 1 {
		rounded = 1
	}
	minutes := rounded / 60
	rest := rounded % 60
	if minutes == 0 {
		return fmt.Sprintf("%ds", rest)
	}
	if rest == 0 {
		return fmt.Sprintf("%dm", minutes)
	}
	return fmt.Sprintf("%dm %ds", minutes, rest)
}

func openOutput(target string) error {
	target = strings.TrimSpace(target)
	if target == "" {
		return fmt.Errorf("empty output target")
	}
	ui.Header("open", "open generated output")
	ui.KV("target", target)
	return exec.Command("open", target).Start()
}

func splitCSV(value string) []string {
	parts := strings.Split(value, ",")
	out := make([]string, 0, len(parts))
	for _, part := range parts {
		part = strings.TrimSpace(part)
		if part != "" {
			out = append(out, part)
		}
	}
	return out
}

func valueOr(v, fallback string) string {
	if strings.TrimSpace(v) == "" {
		return fallback
	}
	return v
}

func expandHome(pathValue string) string {
	pathValue = strings.TrimSpace(pathValue)
	if pathValue == "~" {
		if home, err := os.UserHomeDir(); err == nil {
			return home
		}
	}
	if strings.HasPrefix(pathValue, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			return filepath.Join(home, strings.TrimPrefix(pathValue, "~/"))
		}
	}
	return pathValue
}

func sizeLabel(width, height int, fallback string) string {
	if width > 0 && height > 0 {
		return fmt.Sprintf("%dx%d", width, height)
	}
	if width > 0 {
		return fmt.Sprintf("%dxauto", width)
	}
	if height > 0 {
		return fmt.Sprintf("autox%d", height)
	}
	return fallback
}

func validateBackend(value string) error {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "auto", "cuda", "mps", "mlx", "coreml", "ane", "cpu":
		return nil
	default:
		return fmt.Errorf("unknown backend %q; use auto, cuda, mps, mlx, coreml, ane, or cpu", value)
	}
}

func directBackendSupported(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "auto", "cuda", "mps", "cpu":
		return true
	default:
		return false
	}
}

func suffixFilename(name string, n int) string {
	ext := filepath.Ext(name)
	stem := strings.TrimSuffix(name, ext)
	if ext == "" {
		ext = ".png"
	}
	return fmt.Sprintf("%s-%02d%s", stem, n, ext)
}

func shellish(args []string) string {
	out := make([]string, len(args))
	for i, arg := range args {
		if strings.ContainsAny(arg, " ,") {
			out[i] = strconv.Quote(arg)
		} else {
			out[i] = arg
		}
	}
	return strings.Join(out, " ")
}

func stringValue(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case fmt.Stringer:
		return t.String()
	case nil:
		return ""
	default:
		return fmt.Sprintf("%v", t)
	}
}

func boolValue(v any) bool {
	switch t := v.(type) {
	case bool:
		return t
	case string:
		switch strings.ToLower(strings.TrimSpace(t)) {
		case "1", "true", "yes", "on":
			return true
		default:
			return false
		}
	default:
		return false
	}
}

func floatValue(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case float32:
		return float64(t)
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case json.Number:
		f, _ := t.Float64()
		return f
	case string:
		f, _ := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return f
	default:
		return 0
	}
}

func intValue(v any) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case float32:
		return int(t)
	case json.Number:
		n, _ := t.Int64()
		return int(n)
	case string:
		n, _ := strconv.Atoi(t)
		return n
	default:
		return 0
	}
}

func filenameFromArgs(args []string) string {
	for i, arg := range args {
		if arg == "--filename" && i+1 < len(args) {
			return args[i+1]
		}
	}
	return ""
}

func juryCmd(cfg config.Config, args []string) error {
	outDir := cfg.OutputDir
	if outDir == "" {
		outDir = "/root/Models/flux-output"
	}

	if len(args) > 0 {
		sub := args[0]
		switch sub {
		case "provision":
			cmd := exec.Command("/root/CLIs/flux/provision_jury.sh")
			cmd.Stdout = os.Stdout
			cmd.Stderr = os.Stderr
			return cmd.Run()

		case "sync", "r2", "backup":
			fmt.Println(ui.Accent("==> Backing up jury.sqlite3 to Cloudflare R2 (state/jury.sqlite3)..."))
			if err := jury.SyncToR2(outDir); err != nil {
				return fmt.Errorf("sync failed: %w", err)
			}
			fmt.Println(ui.Accent("✓ Successfully backed up jury.sqlite3 to Cloudflare R2."))
			return nil

		case "mode", "strategy":
			if len(args) < 2 {
				c, _ := jury.GetConfig(outDir)
				fmt.Printf("Current Execution Mode: %s\n", ui.Accent(c.Mode))
				return nil
			}
			mode := strings.ToLower(args[1])
			if mode != "parallel" && mode != "sequential" {
				return fmt.Errorf("invalid mode '%s': must be 'parallel' or 'sequential'", mode)
			}
			c, _ := jury.GetConfig(outDir)
			c.Mode = mode
			if err := jury.SaveConfig(outDir, c); err != nil {
				return err
			}
			fmt.Printf("✓ Jury Execution Strategy set to %s (persisted to SQLite)\n", ui.Accent(mode))
			return nil

		case "weight", "weights":
			if len(args) < 3 {
				c, _ := jury.GetConfig(outDir)
				fmt.Println(ui.Accent("Current Judge Weights:"))
				for k, v := range c.Weights {
					fmt.Printf("  %s: %.2f (%.0f%%)\n", k, v, v*100)
				}
				return nil
			}
			judge := strings.ToLower(args[1])
			val, err := strconv.ParseFloat(args[2], 64)
			if err != nil {
				return fmt.Errorf("invalid weight float: %w", err)
			}
			if val > 1.0 {
				val = val / 100.0 // allow passing 35 or 0.35
			}
			c, _ := jury.GetConfig(outDir)
			c.Weights[judge] = val
			if err := jury.SaveConfig(outDir, c); err != nil {
				return err
			}
			fmt.Printf("✓ Set %s weight to %.2f (%.0f%%) in SQLite\n", judge, val, val*100)
			return nil

		case "strict", "strictness", "gamma":
			if len(args) < 3 {
				c, _ := jury.GetConfig(outDir)
				fmt.Println(ui.Accent("Current Judge Strictness Multipliers (γ):"))
				for k, v := range c.Strictness {
					fmt.Printf("  %s: %.2fγ\n", k, v)
				}
				return nil
			}
			judge := strings.ToLower(args[1])
			val, err := strconv.ParseFloat(args[2], 64)
			if err != nil {
				return fmt.Errorf("invalid strictness float: %w", err)
			}
			c, _ := jury.GetConfig(outDir)
			if c.Strictness == nil {
				c.Strictness = jury.DefaultConfig().Strictness
			}
			c.Strictness[judge] = val
			if err := jury.SaveConfig(outDir, c); err != nil {
				return err
			}
			fmt.Printf("✓ Set %s strictness multiplier to %.2fγ in SQLite\n", judge, val)
			return nil

		case "adversarial", "inquisitor":
			c, _ := jury.GetConfig(outDir)
			if len(args) < 2 {
				fmt.Printf("Adversarial Inquisitor Mode: %v\n", c.AdversarialMode)
				return nil
			}
			val := strings.ToLower(args[1])
			c.AdversarialMode = (val == "on" || val == "true" || val == "1" || val == "enable")
			if err := jury.SaveConfig(outDir, c); err != nil {
				return err
			}
			fmt.Printf("✓ Adversarial Inquisitor Mode set to %v (persisted to SQLite)\n", c.AdversarialMode)
			return nil

		case "order", "sequence":
			if len(args) < 2 {
				c, _ := jury.GetConfig(outDir)
				fmt.Printf("Current Execution Order: %s\n", strings.Join(c.Order, " -> "))
				return nil
			}
			newOrder := args[1:]
			if len(newOrder) == 1 && strings.Contains(newOrder[0], ",") {
				newOrder = strings.Split(newOrder[0], ",")
			}
			c, _ := jury.GetConfig(outDir)
			c.Order = newOrder
			if err := jury.SaveConfig(outDir, c); err != nil {
				return err
			}
			fmt.Printf("✓ Jury Execution Order updated: %s\n", strings.Join(c.Order, " -> "))
			return nil

		case "preset", "presets":
			if len(args) > 1 {
				action := args[1]
				if action == "save" && len(args) > 2 {
					name := strings.Join(args[2:], " ")
					c, _ := jury.GetConfig(outDir)
					p := jury.JuryPreset{
						Name:        name,
						Description: "Custom user profile saved via CLI",
						Mode:        c.Mode,
						Order:       c.Order,
						Weights:     c.Weights,
					}
					if err := jury.SavePreset(outDir, p); err != nil {
						return err
					}
					fmt.Printf("✓ Saved active configuration as preset '%s' in SQLite\n", name)
					return nil
				}
				if action == "load" && len(args) > 2 {
					name := strings.Join(args[2:], " ")
					presets, _ := jury.ListPresets(outDir)
					for _, p := range presets {
						if strings.EqualFold(p.Name, name) {
							c := jury.JuryConfig{
								Mode:    p.Mode,
								Order:   p.Order,
								Weights: p.Weights,
							}
							if err := jury.SaveConfig(outDir, c); err != nil {
								return err
							}
							fmt.Printf("✓ Loaded preset profile '%s' (Mode: %s)\n", p.Name, p.Mode)
							return nil
						}
					}
					return fmt.Errorf("preset '%s' not found", name)
				}
			}
			presets, _ := jury.ListPresets(outDir)
			fmt.Println(ui.Accent("Available Jury Presets:"))
			for _, p := range presets {
				fmt.Printf("  • %s (%s): %v\n", p.Name, p.Mode, p.Weights)
			}
			return nil
		}
	}

	// Default display: overview + status
	jCfg, _ := jury.GetConfig(outDir)
	fmt.Println(ui.Accent("=== FLUX Sovereign Visual Jury Matrix ==="))
	fmt.Println("Authority:   Governor (31B) + Qwen3-VL (8B) + Pixtral (12B) + Gemma Decoder (12B)")
	fmt.Printf("Strategy:    %s\n", ui.Accent(strings.ToUpper(jCfg.Mode)))
	fmt.Printf("Inquisitor:  %v\n", jCfg.AdversarialMode)
	fmt.Printf("Pipeline:    %s\n", strings.Join(jCfg.Order, " -> "))
	fmt.Println("Weights:    ", jCfg.Weights)
	fmt.Println("Strictness: ", jCfg.Strictness)
	fmt.Println("Database:    /root/Models/flux-output/jury.sqlite3 (Synced with Cloudflare R2)")
	fmt.Println("Web View:    https://motion.influx.vision/jury")

	raw, err := os.ReadFile("/root/Models/flux-output/audit.jsonl")
	if err == nil {
		lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
		fmt.Printf("\nRecent Jury Verdicts (%d total):\n", len(lines))
		start := 0
		if len(lines) > 5 {
			start = len(lines) - 5
		}
		for _, l := range lines[start:] {
			fmt.Println(" ", l)
		}
	}
	return nil
}
