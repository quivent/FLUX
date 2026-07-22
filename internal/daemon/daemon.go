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
	Raw      map[string]any   `json:"-"`
}

func New(cfg config.Config) Client {
	return NewNamed(cfg, "flux")
}

func NewNamed(cfg config.Config, name string) Client {
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
	}
}

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
	if c.name != "" && c.name != "flux" {
		args = append(args, "--kind", c.name)
	}
	cmd := exec.Command(c.cfg.Python, args...)
	cmd.Stdout = logf
	cmd.Stderr = logf
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
