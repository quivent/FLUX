package main

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"

	"local/flux/internal/config"
	"local/flux/internal/ui"
)

// The beauty studies stack: the FLUX Tea surface, the governor gateway, and
// the aesthetic jury models, as one container.
//
// The stack shipped as an opaque 10.5 GiB tarball in R2 with no code path that
// referenced it. These commands are that path, and deploy/beauty/Dockerfile is
// the build it was missing.
//
// The archived container's real defect is VRAM apportionment, not model ids:
// vLLM reads --gpu-memory-utilization as an absolute fraction of the whole
// card, so co-resident servers need cumulative fractions and staggered starts.
const (
	beautyImageTag    = "h200-beauty-studies:latest"
	beautyR2Archive   = "containers/h200-beauty-studies-latest.tar.zst"
	beautyR2Context   = "containers/h200-beauty-studies/"
	beautyManifestRel = "deploy/beauty/beauty.manifest.json"
	beautyDockerfile  = "deploy/beauty/Dockerfile"
)

type beautyManifest struct {
	Image struct {
		Tag       string `json:"tag"`
		Base      string `json:"base"`
		R2Bucket  string `json:"r2Bucket"`
		R2Archive string `json:"r2Archive"`
	} `json:"image"`
	Deployment struct {
		Host     string  `json:"host"`
		Hostname string  `json:"hostname"`
		Card     string  `json:"card"`
		CardGiB  float64 `json:"cardGiB"`
		Note     string  `json:"note"`
	} `json:"deployment"`
	Ports  map[string]int `json:"ports"`
	Models map[string]struct {
		ID             string `json:"id"`
		Drafter        string `json:"drafter"`
		Role           string `json:"role"`
		ApproxGiB      int    `json:"approxGiB"`
		Required       bool   `json:"required"`
		DisabledReason string `json:"disabledReason"`
	} `json:"models"`
	KnownDefects []string `json:"knownDefects"`
}

func loadBeautyManifest(cfg config.Config) (beautyManifest, error) {
	var m beautyManifest
	path := filepath.Join(cfg.Root, beautyManifestRel)
	raw, err := os.ReadFile(path)
	if err != nil {
		return m, fmt.Errorf("beauty manifest not found at %s: %w", path, err)
	}
	if err := json.Unmarshal(raw, &m); err != nil {
		return m, fmt.Errorf("beauty manifest at %s is not valid JSON: %w", path, err)
	}
	return m, nil
}

func beauty(cfg config.Config, args []string) error {
	if len(args) == 0 || args[0] == "help" || args[0] == "-h" || args[0] == "--help" {
		ui.Header("beauty", "the unified beauty studies stack: Tea surface, governor, and jury")
		ui.Suite("subcommands", ui.Mint, []ui.PairRow{
			{"build", "build the image from deploy/beauty/Dockerfile"},
			{"warm", "build with model weights baked in (needs HF_TOKEN)"},
			{"pull", "fetch the prebuilt archive from R2 and load it"},
			{"up", "run the stack with a persistent model cache"},
			{"doctor", "check the container posture against the governor and the card"},
			{"status", "what each service is doing on the deploy host, right now"},
			{"logs", "follow the stack's logs from the deploy host"},
		})
		ui.KV("dockerfile", filepath.Join(cfg.Root, beautyDockerfile))
		ui.KV("archive", beautyR2Archive)
		return nil
	}

	switch strings.ToLower(args[0]) {
	case "build":
		return beautyBuild(cfg, false)
	case "warm":
		return beautyBuild(cfg, true)
	case "pull":
		return beautyPull(cfg)
	case "up", "run", "serve":
		return beautyUp(cfg, args[1:])
	case "doctor", "check":
		return beautyDoctor(cfg)
	case "status", "ps":
		return beautyStatus(cfg)
	case "logs", "tail", "follow":
		return beautyLogs(cfg, args[1:])
	default:
		return fmt.Errorf("unknown beauty command %q; use build, warm, pull, up, or doctor", args[0])
	}
}

// beautyBuild builds from the repo Dockerfile. Preferred over pull: a rebuilt
// layer is a few megabytes, where the archive is a 10.5 GiB all-or-nothing
// transfer.
func beautyBuild(cfg config.Config, warm bool) error {
	target := "runtime"
	if warm {
		target = "warm"
	}
	ui.Header("beauty "+map[bool]string{true: "warm", false: "build"}[warm],
		"building "+beautyImageTag+" from "+beautyDockerfile)

	dockerfile := filepath.Join(cfg.Root, beautyDockerfile)
	if _, err := os.Stat(dockerfile); err != nil {
		return fmt.Errorf("missing %s: %w", dockerfile, err)
	}
	for _, bin := range []string{"flux", "gemstone"} {
		staged := filepath.Join(cfg.Root, "deploy", "beauty", "bin", bin)
		if _, err := os.Stat(staged); err != nil {
			return fmt.Errorf("stage the %s binary at %s first (see deploy/beauty/README.md)", bin, staged)
		}
	}

	buildArgs := []string{"build", "-f", dockerfile, "--target", target, "-t", beautyImageTag}
	if warm {
		if os.Getenv("HF_TOKEN") == "" {
			ui.KV("hf token", ui.Warn("HF_TOKEN unset; gated repos will fail to resolve"))
		} else {
			buildArgs = append(buildArgs, "--secret", "id=hf_token,env=HF_TOKEN")
		}
	}
	buildArgs = append(buildArgs, cfg.Root)

	cmd := exec.Command("docker", buildArgs...)
	cmd.Dir = cfg.Root
	cmd.Env = append(os.Environ(), "DOCKER_BUILDKIT=1")
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

// beautyPull fetches the prebuilt archive. A single zstd stream has no random
// access, so this is all 10.5 GiB every time, with no layer reuse — prefer
// beauty build unless the target cannot build.
func beautyPull(cfg config.Config) error {
	ui.Header("beauty pull", "fetching "+beautyR2Archive+" from R2")
	ui.KV("note", ui.Warn("the archive is one zstd stream: no layer reuse, no partial fetch"))
	ui.KV("prefer", "flux beauty build — a changed layer is megabytes, not 10.5 GiB")

	dest := filepath.Join(cfg.Root, ".beauty", filepath.Base(beautyR2Archive))
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}

	if _, err := exec.LookPath("gemstone"); err != nil {
		return fmt.Errorf("gemstone not on PATH; it holds the R2 credentials for this bucket")
	}
	pull := exec.Command("gemstone", "r2", "pull", beautyR2Archive, dest)
	pull.Stdout = os.Stdout
	pull.Stderr = os.Stderr
	if err := pull.Run(); err != nil {
		return fmt.Errorf("r2 pull failed: %w", err)
	}

	ui.Step("loading into docker")
	load := exec.Command("sh", "-c", fmt.Sprintf("zstd -dc %q | docker load", dest))
	load.Stdout = os.Stdout
	load.Stderr = os.Stderr
	return load.Run()
}

func beautyUp(cfg config.Config, args []string) error {
	ui.Header("beauty up", "starting "+beautyImageTag)

	image := beautyImageTag
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		image = args[0]
	}

	run := []string{
		"run", "--rm", "-it",
		"--gpus", "all",
		"--name", "beauty-studies",
		// A named volume so a cold first boot happens once per host, not once
		// per container. The shipped stack had no cache mount at all.
		"-v", "beauty-hf-cache:/models/hf",
		"-v", filepath.Join(cfg.OutputDir) + ":/root/Models/flux-output",
		"-p", "7861:7861",
		"-p", "8000:8000",
		"-p", "8002:8002",
	}

	// The governor gateway needs its toolkit manifest and Council shards, or it
	// answers every completion with "tool manifest has no structured
	// definitions". The original image baked this state into a layer, which is
	// how it went stale; mount it instead so the host owns it.
	stateDir := os.Getenv("BEAUTY_GOVERNOR_STATE")
	if stateDir == "" {
		stateDir = "/opt/beauty/governor-state"
	}
	for _, sub := range []string{".gemstone", ".council"} {
		run = append(run, "-v", filepath.Join(stateDir, sub)+":/root/"+sub)
	}
	for _, key := range []string{
		"HF_TOKEN",
		"BEAUTY_GOVERNOR_MODEL", "BEAUTY_GOVERNOR_DRAFTER", "BEAUTY_GOVERNOR_UTIL",
		"BEAUTY_CODER_MODEL", "BEAUTY_CODER_UTIL",
		"BEAUTY_VISION_MODEL", "BEAUTY_VISION_UTIL",
		"BEAUTY_GOVERNOR_CTX", "BEAUTY_SPEC_TOKENS", "BEAUTY_MAX_NUM_SEQS",
		// Tea rejects websocket upgrades from an unlisted origin, so serving
		// the stack behind tea.influx.vision needs this set.
		"FLUX_WS_ORIGINS", "BEAUTY_TEA_ADDR",
	} {
		if v := os.Getenv(key); v != "" {
			run = append(run, "-e", key+"="+v)
		}
	}
	run = append(run, image)

	cmd := exec.Command("docker", run...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	cmd.Stdin = os.Stdin
	return cmd.Run()
}

// beautyDoctor compares the declared stack against the governor's posture and
// the card actually present. Both drifts it looks for are real: the archive in
// R2 predates the governor's current config, and its GPU fractions do not
// leave room for the FLUX worker.
func beautyDoctor(cfg config.Config) error {
	ui.Header("beauty doctor", "container posture vs governor and card")

	m, err := loadBeautyManifest(cfg)
	if err != nil {
		return err
	}

	ui.KV("image", m.Image.Tag)
	ui.KV("base", m.Image.Base)

	ui.Step("models")
	for name, model := range m.Models {
		switch {
		case model.ID == "" && model.DisabledReason != "":
			ui.KV("  "+name, ui.Warn("disabled")+" — "+model.DisabledReason)
		case model.Required:
			ui.KV("  "+name, ui.State(model.ID))
		default:
			ui.KV("  "+name, model.ID+" "+ui.Soft("(optional)"))
		}
	}

	// The governor node's posture is reference only: it describes the governor's
	// own card, which is a different machine from the beauty deployment target.
	ui.Step("governor node (reference)")
	gov, govErr := readGovernorPosture()
	if govErr != nil {
		ui.KV("  governor.json", ui.Warn(govErr.Error()))
	} else {
		ui.KV("  model", gov["model"])
		ui.KV("  gpu_util", gov["gpu_util"])
		ui.KV("  context", gov["context"])
		ui.KV("  card", ui.Soft("governor's own node — not a beauty target"))
	}

	// Grade against the deployment card, not whatever card is local. The
	// governor runs on its own node; its card is not a beauty target, and
	// grading the beauty budget against it reports a bogus over-subscription.
	ui.Step("card")
	total, card, err := beautyDeploymentCard(m)
	if err != nil {
		ui.KV("  gpu", ui.Warn(err.Error()))
	} else {
		ui.KV("  target", m.Deployment.Host+" "+ui.Soft("("+m.Deployment.Hostname+")"))
		ui.KV("  card", fmt.Sprintf("%s — %.1f GiB", card, total))

		reserve := 35.0
		util := 0.55
		if v := os.Getenv("BEAUTY_GOVERNOR_UTIL"); v != "" {
			if u, perr := strconv.ParseFloat(v, 64); perr == nil {
				util = u
			}
		}
		claimed := util * total
		free := total - claimed
		ui.KV("  vllm claim", fmt.Sprintf("%.2f -> %.1f GiB", util, claimed))
		line := fmt.Sprintf("%.1f GiB free for a worker needing %.1f GiB", free, reserve)
		if free < reserve {
			ui.KV("  budget", ui.Bad("over-subscribed")+" — "+line)
		} else {
			ui.KV("  budget", ui.State(line))
		}
	}

	if len(m.KnownDefects) > 0 {
		ui.Step("known defects in the archived container")
		for _, d := range m.KnownDefects {
			ui.KV("  •", d)
		}
	}
	return nil
}

func readGovernorPosture() (map[string]string, error) {
	home, _ := os.UserHomeDir()
	if home == "" {
		home = os.Getenv("HOME")
	}
	path := filepath.Join(home, ".gemstone", "governor.json")
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("no governor posture at %s", path)
	}
	var parsed map[string]any
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("governor.json is not valid JSON: %w", err)
	}
	out := map[string]string{}
	for _, key := range []string{"model", "drafter", "gpu_util", "context", "spec_tokens", "container"} {
		if v, ok := parsed[key]; ok {
			out[key] = strings.TrimSuffix(fmt.Sprintf("%v", v), ".0")
		}
	}
	return out, nil
}

func gpuTotalGiB() (float64, error) {
	out, err := exec.Command("nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits").Output()
	if err != nil {
		return 0, fmt.Errorf("nvidia-smi unavailable")
	}
	first := strings.TrimSpace(strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0])
	mib, err := strconv.ParseFloat(first, 64)
	if err != nil {
		return 0, fmt.Errorf("could not parse nvidia-smi output %q", first)
	}
	return mib / 1024.0, nil
}

// beautyDeploymentCard reports the GPU on the stack's deployment host. It reads
// the card locally when this box is that host, and over ssh otherwise, so the
// budget is always graded against the card the stack will actually run on.
func beautyDeploymentCard(m beautyManifest) (float64, string, error) {
	host := strings.TrimSpace(m.Deployment.Host)
	local, _ := os.Hostname()

	if host == "" || host == "localhost" || strings.HasSuffix(host, "@"+local) || local == m.Deployment.Hostname {
		total, err := gpuTotalGiB()
		if err != nil {
			return 0, "", err
		}
		name, _ := exec.Command("nvidia-smi", "--query-gpu=name", "--format=csv,noheader").Output()
		return total, strings.TrimSpace(strings.SplitN(strings.TrimSpace(string(name)), "\n", 2)[0]), nil
	}

	out, err := exec.Command("ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host,
		"nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits").Output()
	if err != nil {
		// Fall back to the card the manifest declares, so doctor still reports a
		// budget when the deploy host is unreachable.
		if m.Deployment.CardGiB > 0 {
			return m.Deployment.CardGiB, m.Deployment.Card + " (declared; host unreachable)", nil
		}
		return 0, "", fmt.Errorf("could not reach %s and no card declared in the manifest", host)
	}
	line := strings.TrimSpace(strings.SplitN(strings.TrimSpace(string(out)), "\n", 2)[0])
	parts := strings.Split(line, ",")
	if len(parts) != 2 {
		return 0, "", fmt.Errorf("unexpected nvidia-smi output from %s: %q", host, line)
	}
	mib, err := strconv.ParseFloat(strings.TrimSpace(parts[1]), 64)
	if err != nil {
		return 0, "", fmt.Errorf("could not parse memory from %s: %q", host, parts[1])
	}
	return mib / 1024.0, strings.TrimSpace(parts[0]), nil
}

// beautyRemote wraps a command so it runs on the deploy host when that is a
// different machine, and locally when it is this one.
func beautyRemote(m beautyManifest, argv ...string) *exec.Cmd {
	host := strings.TrimSpace(m.Deployment.Host)
	local, _ := os.Hostname()
	if host == "" || host == "localhost" || local == m.Deployment.Hostname {
		return exec.Command(argv[0], argv[1:]...)
	}
	return exec.Command("ssh", append([]string{"-o", "BatchMode=yes", host}, argv...)...)
}

// beautyStatus reports what each supervisor program is doing. Boot is slow the
// first time because the weights are still arriving, so "not up yet" and
// "broken" must be distinguishable at a glance.
func beautyStatus(cfg config.Config) error {
	m, err := loadBeautyManifest(cfg)
	if err != nil {
		return err
	}
	ui.Header("beauty status", m.Deployment.Host+" ("+m.Deployment.Hostname+")")

	out, err := beautyRemote(m, "docker", "ps", "--filter", "name=beauty-studies",
		"--format", "{{.Status}}").Output()
	state := strings.TrimSpace(string(out))
	if err != nil || state == "" {
		ui.KV("container", ui.Warn("not running")+" — start it with flux beauty up")
		return nil
	}
	ui.KV("container", ui.State(state))

	// supervisorctl exits non-zero whenever any program is not RUNNING, which is
	// the normal state during a cold boot. Judge on output, not exit code.
	sup, _ := beautyRemote(m, "docker", "exec", "beauty-studies",
		"supervisorctl", "status").CombinedOutput()
	if len(strings.TrimSpace(string(sup))) > 0 {
		ui.Step("services")
		for _, line := range strings.Split(strings.TrimSpace(string(sup)), "\n") {
			fields := strings.Fields(line)
			if len(fields) < 2 {
				continue
			}
			name, st := fields[0], fields[1]
			switch st {
			case "RUNNING":
				ui.KV("  "+name, ui.State(strings.Join(fields[1:], " ")))
			case "STARTING":
				ui.KV("  "+name, ui.Soft(strings.Join(fields[1:], " ")))
			default:
				ui.KV("  "+name, ui.Bad(st)+" "+ui.Soft(strings.Join(fields[2:], " ")))
			}
		}
	}

	if du, err := beautyRemote(m, "docker", "exec", "beauty-studies",
		"du", "-sh", "/models/hf").Output(); err == nil {
		size := strings.Fields(strings.TrimSpace(string(du)))
		if len(size) > 0 {
			ui.Step("model cache")
			ui.KV("  /models/hf", size[0]+ui.Soft("  (weights arrive here on first boot)"))
		}
	}

	ui.Step("endpoints")
	ui.KV("  tea", fmt.Sprintf("http://%s:%d/", strings.TrimPrefix(m.Deployment.Host, "root@"), m.Ports["tea"]))
	ui.KV("  governor", fmt.Sprintf("http://%s:%d/v1", strings.TrimPrefix(m.Deployment.Host, "root@"), m.Ports["governorGateway"]))
	return nil
}

func beautyLogs(cfg config.Config, args []string) error {
	m, err := loadBeautyManifest(cfg)
	if err != nil {
		return err
	}
	argv := []string{"docker", "logs", "-f", "--tail", "80", "beauty-studies"}
	if len(args) > 0 && args[0] != "-f" {
		// flux beauty logs governor-vllm -> just that program's log
		argv = []string{"docker", "exec", "beauty-studies", "tail", "-F", "/var/log/" + args[0] + ".log"}
	}
	cmd := beautyRemote(m, argv...)
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}
