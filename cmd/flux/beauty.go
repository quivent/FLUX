package main

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	"local/flux/internal/config"
	"local/flux/internal/ui"
)

type beautyArchitecture struct {
	Name    string
	Profile string
	Summary string
}

var beautyArchitectures = []beautyArchitecture{
	{"compact", "h100", "one studio H100: FLUX + Qwen + Pixtral + gates; Gemma remote"},
	{"remote-qwen", "h100-remote-witness", "studio H100 keeps FLUX + Pixtral; Qwen and Gemma are remote"},
	{"distributed", "h100-distributed-atelier", "expanded studio + dedicated Qwen + dedicated Gemma machines"},
	{"resident-tribunal", "h200-resident-tribunal", "H200 143GiB: every judge on-card; self-contained EGRL, Kontext BF16 resident"},
	{"speed-swarm", "h200-speed-swarm", "H200: two resident FLUX workers feeding one tribunal; governor remote"},
	{"multi-critic", "h200-multi-critic", "H200: Pixtral + InternVL dual critics; disagreement is the operator signal"},
	{"deep-context", "h200-deep-context", "H200: 131k windows; the full anchor set + taste log ride in-context"},
	{"adaptive-coexist", "h200-adaptive-coexist", "H200: adapt to resident work — reuse live Gemmas as witness+governor, add only FLUX+Pixtral+gates"},
}

func suitesCmd(cfg config.Config, args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		ui.Header("suites", "specialized FLUX production suites")
		ui.Suite("available", ui.Rose, []ui.PairRow{
			{"beauty", "architecture profiles, EGRL protocol, deployment, and operator eye-gate"},
		})
		fmt.Println()
		fmt.Println(ui.Soft("  flux suites beauty COMMANDLIST"))
		return nil
	}
	if strings.EqualFold(args[0], "beauty") {
		return beautySuite(cfg, args[1:])
	}
	return fmt.Errorf("unknown suite %q; use beauty", args[0])
}

func beautySuite(cfg config.Config, args []string) error {
	if len(args) == 0 || isHelp(args[0]) || strings.EqualFold(args[0], "commandlist") || strings.EqualFold(args[0], "commands") {
		beautyCommandList(cfg)
		return nil
	}
	switch strings.ToLower(args[0]) {
	case "architectures", "architecture", "profiles":
		beautyArchitectureList()
		return nil
	case "protocols", "protocol":
		return beautyProtocols(args[1:])
	case "deploy", "provision":
		return beautyDeploy(cfg, args[1:], false)
	case "status":
		return beautyDeploy(cfg, args[1:], true)
	case "slate":
		return beautyEyeGate(cfg, append([]string{"slate"}, args[1:]...))
	case "record":
		return beautyEyeGate(cfg, append([]string{"record"}, args[1:]...))
	default:
		return fmt.Errorf("unknown beauty command %q; run `flux suites beauty COMMANDLIST`", args[0])
	}
}

func isHelp(value string) bool {
	switch strings.ToLower(value) {
	case "help", "-h", "--help":
		return true
	default:
		return false
	}
}

func beautyCommandList(cfg config.Config) {
	ui.Header("beauty", "architecture × protocol command list")
	beautyArchitectureList()
	fmt.Println()
	beautyProtocolList()
	fmt.Println()
	ui.Suite("commands", ui.Gold, []ui.PairRow{
		{"flux suites beauty architectures", "list deployment variants and continuum profile names"},
		{"flux suites beauty protocols", "list EGRL, machine-audit, and operator-gate views"},
		{"flux suites beauty protocol <name>", "explain one protocol view and its commands"},
		{"flux suites beauty deploy compact --dry-run", "validate the one-H100 studio without changing it"},
		{"flux suites beauty deploy compact", "provision the compact studio"},
		{"flux suites beauty deploy remote-qwen --witness-url <url>", "keep Qwen clean on another machine"},
		{"flux suites beauty deploy distributed --witness-url <url> --governor-url <url>", "provision the three-machine atelier"},
		{"flux suites beauty status <architecture>", "show the selected topology and tenant health"},
		{"flux suites beauty slate", "show every pending candidate in machine-ranked order"},
		{"flux suites beauty record --job-id <id> --verdict crown|kill|note --words <text> --delta <n>", "persist the operator verdict verbatim"},
	})
	fmt.Println()
	ui.KV("invariant", "Pixtral is local on the FLUX studio in every Beauty architecture")
	ui.KV("source", filepath.Join(cfg.Root, "jury_continuum.toml"))
}

func beautyArchitectureList() {
	rows := make([]ui.PairRow, 0, len(beautyArchitectures))
	for _, arch := range beautyArchitectures {
		rows = append(rows, ui.PairRow{arch.Name + " → " + arch.Profile, arch.Summary})
	}
	ui.Suite("architectures", ui.Teal, rows)
}

func beautyProtocolList() {
	ui.Suite("protocols", ui.Lilac, []ui.PairRow{
		{"egrl", "Qwen observation → independent Pixtral critique → Gemma synthesis → operator verdict"},
		{"egrl-recal", "EGRL as a learning loop: anchor update, critic-delta audit, law promotion, register expansion"},
		{"tribunal", "two structurally-separate critics; their disagreement is the operator's priority signal"},
		{"adaptive", "discover what is already resident on the card and bind the witness/governor seats to it"},
		{"ralpheye", "the three-gate operator eye-gate: verdict overrides the critic, persisted verbatim as taste data"},
		{"moj-audit", "inspect machine evidence and ranking; every result remains pending"},
		{"operator-eye", "crown, kill, or note; append verbatim taste data and recalibrate later generations"},
	})
	fmt.Println(ui.Soft("  Protocol views compose with every architecture; egrl-recal/tribunal/adaptive are H200-native."))
}

func beautyProtocols(args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		beautyProtocolList()
		return nil
	}
	name := strings.ToLower(args[0])
	ui.Header("beauty protocol", name)
	switch name {
	case "egrl":
		ui.KV("flow", "Qwen observation → Pixtral anchored critique → Gemma synthesis → operator")
		ui.KV("command", "flux suites beauty slate")
	case "moj-audit", "moj":
		ui.KV("flow", "DINO/SigLIP → Qwen + Pixtral → Gemma recommendation")
		ui.KV("authority", "diagnostic only; candidates remain pending")
		ui.KV("command", "flux jury --probe")
	case "operator-eye", "eye":
		ui.KV("flow", "ranked full slate → crown / kill / note → append-only taste log")
		ui.KV("command", "flux suites beauty record --job-id <id> --verdict <verdict> --words <text> --delta <n>")
	case "egrl-recal", "recalibration":
		ui.KV("flow", "generation N verdicts → anchor update → critic-delta audit → law promotion → register expansion → generation N+1")
		ui.KV("learns", "the operator's taste function; the asset built is the calibrated critic, not any single frame")
		ui.KV("source", "protocols/EGRL.md")
	case "tribunal", "multi-critic":
		ui.KV("flow", "two independent critics rank every candidate; disagreement is flagged for priority operator attention")
		ui.KV("critics", "Pixtral (palette/medium) + InternVL (composition/narrative)")
		ui.KV("profile", "h200-multi-critic")
	case "adaptive", "coexist":
		ui.KV("flow", "discover resident vLLM servers via /v1/models → bind witness/governor seats to them → add only FLUX + Pixtral + gates")
		ui.KV("why", "co-exist with other work on the card (e.g. surgery Gemmas) instead of evicting it")
		ui.KV("profile", "h200-adaptive-coexist")
	case "ralpheye", "eye-gate":
		ui.KV("gate 1", "builder observation — the witness reads the rendered frame; unviewed output is undefined output")
		ui.KV("gate 2", "independent critic — ranks against calibrated anchors; built nothing itself")
		ui.KV("gate 3", "operator — overrides the critic in either direction; verdict persisted verbatim")
		ui.KV("recalibrate", "verdicts + overrides feed forward as taste data and provisional design laws")
		ui.KV("command", "flux suites beauty slate  ·  flux suites beauty record ...")
		ui.KV("source", "protocols/EGRL.md")
	default:
		return fmt.Errorf("unknown Beauty protocol %q; use egrl, egrl-recal, tribunal, adaptive, ralpheye, moj-audit, or operator-eye", name)
	}
	return nil
}

func beautyProfile(name string) (string, error) {
	if name == "" {
		name = "compact"
	}
	for _, arch := range beautyArchitectures {
		if name == arch.Name || name == arch.Profile {
			return arch.Profile, nil
		}
	}
	names := make([]string, 0, len(beautyArchitectures))
	for _, arch := range beautyArchitectures {
		names = append(names, arch.Name)
	}
	return "", fmt.Errorf("unknown Beauty architecture %q; use one of: %s", name, strings.Join(names, ", "))
}

func beautyDeploy(cfg config.Config, args []string, status bool) error {
	architecture := "compact"
	if len(args) > 0 && !strings.HasPrefix(args[0], "-") {
		architecture, args = strings.ToLower(args[0]), args[1:]
	}
	profile, err := beautyProfile(architecture)
	if err != nil {
		return err
	}
	script := filepath.Join(cfg.Root, "deploy", "deploy_h100_beauty.sh")
	commandArgs := []string{script, "--profile", profile}
	if status {
		commandArgs = append(commandArgs, "--status")
	}
	commandArgs = append(commandArgs, args...)
	cmd := exec.Command("bash", commandArgs...)
	cmd.Dir, cmd.Stdout, cmd.Stderr = cfg.Root, os.Stdout, os.Stderr
	return cmd.Run()
}

func beautyEyeGate(cfg config.Config, args []string) error {
	script := filepath.Join(cfg.Root, "beauty_eye_gate.py")
	cmd := exec.Command(cfg.Python, append([]string{script}, args...)...)
	cmd.Dir, cmd.Stdout, cmd.Stderr = cfg.Root, os.Stdout, os.Stderr
	return cmd.Run()
}
