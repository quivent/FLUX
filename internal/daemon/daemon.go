package daemon

import (
	"bufio"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"syscall"
	"time"

	"local/flux/internal/config"
)

type Client struct {
	cfg     config.Config
	name    string
	dir     string
	socket  string
	state   string
	log     string
	pid     string
	profile string
	kind    string
	env     map[string]string
}

type Response struct {
	OK       bool             `json:"ok"`
	Error    string           `json:"error,omitempty"`
	Loaded   bool             `json:"loaded,omitempty"`
	Device   string           `json:"device,omitempty"`
	Backend  string           `json:"backend,omitempty"`
	Backends map[string]any   `json:"backends,omitempty"`
	Profile  map[string]any   `json:"profile,omitempty"`
	Jobs     []map[string]any `json:"jobs,omitempty"`
	Job      map[string]any   `json:"job,omitempty"`
	Removed  []string         `json:"removed,omitempty"`
	Raw      map[string]any   `json:"-"`
}

// New returns the client the CLI uses when no worker was named.
//
// The default worker listens on .fluxd/flux.sock, but a fleet runs its workers
// under per-GPU names (flux-gpu0, flux-gpu1, ...) on their own sockets. When a
// fleet worker is already live, the unnamed default would ignore it, start a
// second worker, and load a second full pipeline onto the same GPU — roughly
// 32 GiB of duplicate weights. On a box that also hosts an LLM that is an
// out-of-memory error rather than a slow render, and the CLI's own message
// ("route: resident unix socket") gives no hint that it picked a different
// resident worker than the HTTP server is using.
//
// So adopt a live fleet worker when one is listening. An explicit NewNamed call
// still addresses exactly what it asks for, and with no fleet worker up the
// default socket is used exactly as before.
func New(cfg config.Config) Client {
	if name, ok := liveFleetWorker(cfg); ok {
		return NewNamed(cfg, name)
	}
	return NewNamed(cfg, "flux")
}

// liveFleetWorker reports the lowest-indexed per-GPU worker that is actually
// accepting connections. A socket file left behind by a dead worker is skipped:
// only a successful dial proves the pipeline behind it is resident.
func liveFleetWorker(cfg config.Config) (string, bool) {
	if os.Getenv("FLUX_NO_FLEET_ADOPT") != "" {
		return "", false
	}
	dir := filepath.Join(cfg.Root, ".fluxd")
	for gpu := 0; gpu < 8; gpu++ {
		name := fmt.Sprintf("flux-gpu%d", gpu)
		conn, err := net.DialTimeout("unix", filepath.Join(dir, name+".sock"), 250*time.Millisecond)
		if err != nil {
			continue
		}
		_ = conn.Close()
		return name, true
	}
	return "", false
}

func NewNamed(cfg config.Config, name string) Client {
	var env map[string]string
	if v := os.Getenv("FLUX_CUDA_DEVICES"); v != "" {
		env = map[string]string{"CUDA_VISIBLE_DEVICES": v}
	}
	return NewWorker(cfg, name, name, env)
}

// NewWorker builds a client for a named worker with an explicit worker kind and
// extra environment variables. Name and kind are separate because a fleet runs
// several workers of kind "flux" under distinct names (flux-gpu0, flux-gpu1...),
// each pinned to one GPU via CUDA_VISIBLE_DEVICES. Pinning through the
// environment means every worker process still addresses its device as cuda:0,
// so no device-index plumbing is needed inside worker.py.
func NewWorker(cfg config.Config, name, kind string, env map[string]string) Client {
	dir := filepath.Join(cfg.Root, ".fluxd")
	socketName := "flux.sock"
	stateName := "jobs.jsonl"
	logName := "worker.log"
	pidName := "worker.pid"
	profileName := "profile.json"
	if name != "" && name != "flux" {
		socketName = name + ".sock"
		stateName = name + ".jobs.jsonl"
		logName = name + ".log"
		pidName = name + ".pid"
		profileName = name + ".profile.json"
	}
	return Client{
		cfg:     cfg,
		name:    name,
		dir:     dir,
		socket:  filepath.Join(dir, socketName),
		state:   filepath.Join(dir, stateName),
		log:     filepath.Join(dir, logName),
		pid:     filepath.Join(dir, pidName),
		profile: filepath.Join(dir, profileName),
		kind:    kind,
		env:     env,
	}
}

// Name reports the worker name this client addresses.
func (c Client) Name() string { return c.name }

func (c Client) Paths() (socket, state, log, pid string) {
	return c.socket, c.state, c.log, c.pid
}

func (c Client) ProfilePath() string {
	return c.profile
}

func (c Client) Start(preload bool) error {
	if err := os.MkdirAll(c.dir, 0o755); err != nil {
		return err
	}
	if _, err := c.Request(map[string]any{"op": "ping"}); err == nil {
		return nil
	}
	logf, err := os.OpenFile(c.log, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		return err
	}
	args := []string{
		"-u",
		filepath.Join(c.cfg.Root, "worker.py"),
		"--socket", c.socket,
		"--state", c.state,
		"--profile", c.profile,
		"--model-dir", c.cfg.ModelDir,
		"--out-dir", c.cfg.OutputDir,
		"--backend", c.cfg.Backend,
	}
	if preload {
		args = append(args, "--preload")
	}
	if c.kind != "" && c.kind != "flux" {
		args = append(args, "--kind", c.kind)
	}
	cmd := exec.Command(c.cfg.Python, args...)
	cmd.Stdout = logf
	cmd.Stderr = logf
	if len(c.env) > 0 {
		cmd.Env = os.Environ()
		for k, v := range c.env {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
	}
	cmd.SysProcAttr = &syscall.SysProcAttr{Setsid: true}
	if err := cmd.Start(); err != nil {
		_ = logf.Close()
		return err
	}
	_ = os.WriteFile(c.pid, []byte(fmt.Sprintf("%d\n", cmd.Process.Pid)), 0o644)
	_ = cmd.Process.Release()
	_ = logf.Close()
	wait := 20 * time.Second
	if preload {
		wait = 45 * time.Second
	}
	deadline := time.Now().Add(wait)
	for time.Now().Before(deadline) {
		if _, err := c.Request(map[string]any{"op": "ping"}); err == nil {
			return nil
		}
		time.Sleep(100 * time.Millisecond)
	}
	if preload {
		return nil
	}
	return errors.New("worker did not create socket quickly; see .fluxd/worker.log")
}

func (c Client) Request(req map[string]any) (Response, error) {
	conn, err := net.DialTimeout("unix", c.socket, 500*time.Millisecond)
	if err != nil {
		return Response{}, err
	}
	defer conn.Close()
	if err := json.NewEncoder(conn).Encode(req); err != nil {
		return Response{}, err
	}
	line, err := bufio.NewReader(conn).ReadBytes('\n')
	if err != nil {
		return Response{}, err
	}
	var raw map[string]any
	if err := json.Unmarshal(line, &raw); err != nil {
		return Response{}, err
	}
	var resp Response
	_ = json.Unmarshal(line, &resp)
	resp.Raw = raw
	if !resp.OK {
		return resp, errors.New(resp.Error)
	}
	return resp, nil
}

func (c Client) Stop() error {
	_, err := c.Request(map[string]any{"op": "stop"})
	return err
}
