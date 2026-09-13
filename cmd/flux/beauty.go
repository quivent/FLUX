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
		{"moj-audit", "inspect machine evidence and ranking; every result remains pending"},
		{"operator-eye", "crown, kill, or note; append verbatim taste data and recalibrate later generations"},
	})
	fmt.Println(ui.Soft("  Every protocol view works with compact, remote-qwen, and distributed architectures."))
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
	default:
		return fmt.Errorf("unknown Beauty protocol %q; use egrl, moj-audit, or operator-eye", name)
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
	return "", fmt.Errorf("unknown Beauty architecture %q; use compact, remote-qwen, or distributed", name)
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
