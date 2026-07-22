package runner

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"os"
	"os/exec"
	"strings"
)

type Result struct {
	OutputPath string
	Seconds    string
}

func Stream(ctx context.Context, env map[string]string, name string, args ...string) (Result, error) {
	cmd := exec.CommandContext(ctx, name, args...)
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, k+"="+v)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return Result{}, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return Result{}, err
	}
	if err := cmd.Start(); err != nil {
		return Result{}, err
	}

	lines := make(chan string)
	go scan(stdout, lines)
	go scan(stderr, lines)
	go func() {
		_ = cmd.Wait()
		close(lines)
	}()

	var result Result
	for line := range lines {
		fmt.Println(line)
		if v, ok := strings.CutPrefix(line, "saved="); ok {
			result.OutputPath = strings.TrimSpace(v)
		}
		if v, ok := strings.CutPrefix(line, "seconds="); ok {
			result.Seconds = strings.TrimSpace(v)
		}
	}
	if cmd.ProcessState == nil || !cmd.ProcessState.Success() {
		if cmd.ProcessState != nil {
			return result, fmt.Errorf("%s exited with %s", name, cmd.ProcessState.String())
		}
		return result, fmt.Errorf("%s failed", name)
	}
	return result, nil
}

func StreamNoResult(ctx context.Context, env map[string]string, name string, args ...string) error {
	_, err := Stream(ctx, env, name, args...)
	return err
}

func scan(r io.Reader, out chan<- string) {
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		out <- sc.Text()
	}
}
