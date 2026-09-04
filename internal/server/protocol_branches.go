package server

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

const protocolBranchCap = 3

var protocolBranchSlugPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]{0,31}$`)
var protocolBranchStrip = regexp.MustCompile(`[^a-z0-9-]+`)
var protocolBranchDashes = regexp.MustCompile(`-{2,}`)

var reservedProtocolBranches = map[string]bool{
	"fashion": true, "arcane": true, "portraits": true, "atlas": true,
	"gallery": true, "protocol": true, "images": true, "movement": true,
	"exhibition": true, "studies": true, "stallion": true, "tea": true,
	"index": true, "assets": true, "api": true, "batches": true, "trash": true,
	"garden": true, "engine": true, "judge": true, "jury": true, "sentinel": true,
	"rig": true, "domains": true, "stream": true, "gpu3": true, "fp8": true,
	"desk": true, "scores": true, "control": true, "governor": true, "daemons": true,
	"celadon": true, "still-life": true, "still_life": true,
}

func normalizeProtocolBranch(name string) (string, error) {
	slug := strings.ToLower(strings.TrimSpace(name))
	slug = strings.ReplaceAll(slug, "_", "-")
	slug = protocolBranchStrip.ReplaceAllString(slug, "-")
	slug = protocolBranchDashes.ReplaceAllString(slug, "-")
	slug = strings.Trim(slug, "-")
	if slug == "" || !protocolBranchSlugPattern.MatchString(slug) {
		return "", fmt.Errorf("branch name must be 1-32 letters, numbers, or hyphens")
	}
	if reservedProtocolBranches[slug] {
		return "", fmt.Errorf("branch %q is reserved; pick a new collection name", slug)
	}
	return slug, nil
}

func protocolBranchStatePath(root, slug string) string {
	return filepath.Join(root, ".fluxd", "protocol_stream_branch_"+slug+".json")
}

func protocolBranchPidPath(root, slug string) string {
	return filepath.Join(root, ".fluxd", "protocol_stream_branch_"+slug+".pid")
}

func protocolBranchRel(slug string) string {
	return filepath.ToSlash(filepath.Join("collections", slug))
}

func publicCollectionSlug(path string) (string, bool) {
	rel := strings.Trim(strings.TrimPrefix(path, "/collections/"), "/")
	if rel == "" || strings.Contains(rel, "/") {
		return "", false
	}
	rel = strings.ToLower(rel)
	if !protocolBranchSlugPattern.MatchString(rel) {
		return "", false
	}
	return rel, true
}

func (s Server) listProtocolBranches() []map[string]any {
	fluxd := filepath.Join(s.cfg.Root, ".fluxd")
	entries, err := os.ReadDir(fluxd)
	if err != nil {
		return []map[string]any{}
	}
	out := make([]map[string]any, 0)
	seen := map[string]bool{}
	for _, entry := range entries {
		name := entry.Name()
		if entry.IsDir() || !strings.HasPrefix(name, "protocol_stream_branch_") || !strings.HasSuffix(name, ".json") {
			continue
		}
		slug := strings.TrimSuffix(strings.TrimPrefix(name, "protocol_stream_branch_"), ".json")
		if slug == "" || seen[slug] {
			continue
		}
		seen[slug] = true
		state := readProtocolStreamStateFile(protocolBranchStatePath(s.cfg.Root, slug))
		if state == nil {
			state = map[string]any{}
		}
		status, _ := state["status"].(string)
		prompt, _ := state["prompt"].(string)
		row := map[string]any{
			"slug":       slug,
			"name":       slug,
			"status":     status,
			"prompt":     prompt,
			"n":          state["n"],
			"steps":      state["steps"],
			"submitted":  state["submitted"],
			"done":       state["done"],
			"running":    state["running"],
			"error":      state["error"],
			"collection": protocolBranchRel(slug),
			"wall":       "/collections/" + slug,
			"stream":     state,
		}
		out = append(out, row)
	}
	collRoot := filepath.Join(s.cfg.OutputDir, "collections")
	if dirs, err := os.ReadDir(collRoot); err == nil {
		for _, entry := range dirs {
			if !entry.IsDir() {
				continue
			}
			slug := entry.Name()
			if reservedProtocolBranches[slug] || seen[slug] {
				continue
			}
			if _, err := os.Stat(filepath.Join(collRoot, slug, ".protocol-branch.json")); err != nil {
				continue
			}
			seen[slug] = true
			out = append(out, map[string]any{
				"slug":       slug,
				"name":       slug,
				"status":     "idle",
				"collection": protocolBranchRel(slug),
				"wall":       "/collections/" + slug,
			})
		}
	}
	return out
}

func (s Server) runningProtocolBranchCount(except string) int {
	n := 0
	for _, row := range s.listProtocolBranches() {
		if stringValue(row["status"]) != "running" {
			continue
		}
		if except != "" && stringValue(row["slug"]) == except {
			continue
		}
		n++
	}
	return n
}

func (s Server) protocolBranchesAPI(w http.ResponseWriter, r *http.Request) {
	switch r.Method {
	case http.MethodGet:
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":       true,
			"cap":      protocolBranchCap,
			"running":  s.runningProtocolBranchCount(""),
			"branches": s.listProtocolBranches(),
		})
	case http.MethodPost:
		s.startOrStopProtocolBranch(w, r)
	default:
		methodNotAllowed(w, http.MethodGet, http.MethodPost)
	}
}

func (s Server) startOrStopProtocolBranch(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Name   string `json:"name"`
		Branch string `json:"branch"`
		Prompt string `json:"prompt"`
		N      int    `json:"n"`
		Steps  int    `json:"steps"`
		Width  int    `json:"width"`
		Height int    `json:"height"`
		Depth  int    `json:"depth"`
		Socket string `json:"socket"`
		Stop   bool   `json:"stop"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil && err != io.EOF {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	raw := req.Branch
	if raw == "" {
		raw = req.Name
	}
	slug, err := normalizeProtocolBranch(raw)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if req.Stop {
		s.stopProtocolBranch(w, slug)
		return
	}
	prompt := strings.TrimSpace(req.Prompt)
	if prompt == "" {
		writeError(w, http.StatusBadRequest, "branch prompt is required")
		return
	}
	promptL := strings.ToLower(prompt)
	if strings.Contains(promptL, "celadon tea bowl") || strings.Contains(promptL, "kintsugi seam") {
		writeError(w, http.StatusConflict, "still-life / celadon tea-bowl stream is stopped")
		return
	}
	if current := readProtocolStreamStateFile(protocolBranchStatePath(s.cfg.Root, slug)); current != nil {
		if status, _ := current["status"].(string); status == "running" {
			writeJSON(w, http.StatusOK, map[string]any{
				"ok": true, "started": false, "branch": slug, "wall": "/collections/" + slug, "stream": current,
			})
			return
		}
	}
	if s.runningProtocolBranchCount(slug) >= protocolBranchCap {
		writeError(w, http.StatusConflict, fmt.Sprintf("already running %d protocol branches; stop one first", protocolBranchCap))
		return
	}
	req.N, req.Steps, req.Depth, req.Width, req.Height = clampProtocolBranchHarvest(req.N, req.Steps, req.Depth, req.Width, req.Height)
	collDir := filepath.Join(s.cfg.OutputDir, "collections", slug)
	if err := os.MkdirAll(collDir, 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	marker, _ := json.Marshal(map[string]any{"branch": slug, "wall": "/collections/" + slug})
	_ = os.WriteFile(filepath.Join(collDir, ".protocol-branch.json"), append(marker, '\n'), 0o644)

	statePath := protocolBranchStatePath(s.cfg.Root, slug)
	logPath := filepath.Join(s.cfg.Root, ".fluxd", "protocol_stream_branch_"+slug+".log")
	if err := os.MkdirAll(filepath.Dir(statePath), 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	args := []string{
		filepath.Join(s.cfg.Root, "protocol_stream.py"),
		"--n", strconv.Itoa(req.N),
		"--steps", strconv.Itoa(req.Steps),
		"--depth", strconv.Itoa(req.Depth),
		"--prompt", prompt,
		"--width", strconv.Itoa(req.Width),
		"--height", strconv.Itoa(req.Height),
		"--branch", slug,
		"--state", statePath,
		"--lane", slug,
	}
	sock := strings.TrimSpace(req.Socket)
	if sock == "" {
		sock = filepath.Join(s.cfg.Root, ".fluxd", "flux-gpu3.sock")
	} else if !filepath.IsAbs(sock) {
		if strings.ContainsRune(sock, os.PathSeparator) {
			sock = filepath.Join(s.cfg.Root, sock)
		} else {
			sock = filepath.Join(s.cfg.Root, ".fluxd", sock)
		}
	}
	args = append(args, "--socket", sock)
	logf, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	cmd := exec.Command(s.cfg.Python, args...)
	cmd.Dir = s.cfg.Root
	cmd.Stdout = logf
	cmd.Stderr = logf
	cmd.Env = append(os.Environ(),
		"OUT_DIR="+s.cfg.OutputDir,
		"FLUX_OUTPUT_DIR="+s.cfg.OutputDir,
		"FLUX_HTTP=http://127.0.0.1:7861",
		"PYTHONUNBUFFERED=1",
	)
	if err := cmd.Start(); err != nil {
		_ = logf.Close()
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_ = os.WriteFile(protocolBranchPidPath(s.cfg.Root, slug), []byte(strconv.Itoa(cmd.Process.Pid)+"\n"), 0o644)
	_ = cmd.Process.Release()
	_ = logf.Close()
	time.Sleep(200 * time.Millisecond)
	writeJSON(w, http.StatusAccepted, map[string]any{
		"ok":      true,
		"started": true,
		"branch":  slug,
		"wall":    "/collections/" + slug,
		"n":       req.N,
		"steps":   req.Steps,
		"width":   req.Width,
		"height":  req.Height,
		"stream":  readProtocolStreamStateFile(statePath),
	})
}

func clampProtocolBranchHarvest(n, steps, depth, width, height int) (int, int, int, int, int) {
	if n < 0 {
		n = 0
	}
	if steps != 18 {
		steps = 28
	}
	if depth <= 0 {
		depth = 1
	}
	if depth > 2 {
		depth = 2
	}
	if width <= 0 {
		width = 1024
	}
	if height <= 0 {
		height = width
	}
	if width > 2048 {
		width = 2048
	}
	if height > 2048 {
		height = 2048
	}
	return n, steps, depth, width, height
}

func (s Server) stopProtocolBranch(w http.ResponseWriter, slug string) {
	pidPath := protocolBranchPidPath(s.cfg.Root, slug)
	raw, err := os.ReadFile(pidPath)
	if err == nil {
		pid, convErr := strconv.Atoi(strings.TrimSpace(string(raw)))
		if convErr == nil && pid > 1 {
			proc, findErr := os.FindProcess(pid)
			if findErr == nil {
				_ = proc.Kill()
			}
		}
	}
	_ = os.Remove(pidPath)
	statePath := protocolBranchStatePath(s.cfg.Root, slug)
	state := readProtocolStreamStateFile(statePath)
	if state == nil {
		state = map[string]any{"branch": slug}
	}
	state["status"] = "stopped"
	state["updated_at"] = time.Now().Unix()
	if encoded, err := json.MarshalIndent(state, "", "  "); err == nil {
		_ = os.WriteFile(statePath, append(encoded, '\n'), 0o644)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "stopped": true, "branch": slug, "wall": "/collections/" + slug, "stream": state,
	})
}
