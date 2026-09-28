package main

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"local/flux/internal/ui"
)

// The Beauty study loop is served by apps/beauty/study/beauty_prompt.py on
// 127.0.0.1:8096 (systemd beauty-prompt.service). These commands drive it.
const (
	beautyStudyURLEnv         = "FLUX_BEAUTY_STUDY_URL"
	beautyStudyDefaultURL     = "http://127.0.0.1:8096"
	beautyStudyCollectionsDir = "/home/ubuntu/Models/flux-output/collections"
	beautyStudyPicturesDir    = "/home/ubuntu/Models/flux-output/influx-outputs"
	beautyStudyActiveColl     = "/home/ubuntu/FLUX/.fluxd/collection.json"
	beautyStudyScoreMin       = -20
	beautyStudyScoreMax       = 20
	beautyStudyDefaultPreset  = "hero"
	beautyStudyRequestTimeout = 30 * time.Second
)

var beautyAlbumPattern = regexp.MustCompile(`^[a-z0-9][a-z0-9-]*$`)

var beautyStudyConfigKeys = []string{
	"weights", "gain", "min_change", "bold_after", "revision_after", "reseed_after",
	"judge_calls", "size", "steps", "vision", "writer",
}

func beautyStudyBaseURL(flagValue string) string {
	if strings.TrimSpace(flagValue) != "" {
		return strings.TrimRight(flagValue, "/")
	}
	if v := strings.TrimSpace(os.Getenv(beautyStudyURLEnv)); v != "" {
		return strings.TrimRight(v, "/")
	}
	return beautyStudyDefaultURL
}

func beautyStudyURLFlag(fs *flag.FlagSet) *string {
	return fs.String("url", "", "study service base URL (default $"+beautyStudyURLEnv+" or "+beautyStudyDefaultURL+")")
}

// beautyStudyCall sends one request to the study service and decodes the JSON reply.
func beautyStudyCall(base, method, path string, body any) (map[string]any, error) {
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return nil, err
		}
		reader = bytes.NewReader(raw)
	}
	req, err := http.NewRequest(method, base+path, reader)
	if err != nil {
		return nil, err
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	client := &http.Client{Timeout: beautyStudyRequestTimeout}
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("study service %s unreachable: %w", base, err)
	}
	defer resp.Body.Close()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	out := map[string]any{}
	if len(bytes.TrimSpace(raw)) > 0 {
		if err := json.Unmarshal(raw, &out); err != nil {
			return nil, fmt.Errorf("%s %s: HTTP %d, not JSON: %s", method, path, resp.StatusCode, strings.TrimSpace(string(raw)))
		}
	}
	if resp.StatusCode >= 300 {
		if msg, ok := out["error"].(string); ok && msg != "" {
			return out, fmt.Errorf("%s %s: HTTP %d: %s", method, path, resp.StatusCode, msg)
		}
		return out, fmt.Errorf("%s %s: HTTP %d", method, path, resp.StatusCode)
	}
	return out, nil
}

func beautyStudyHelp() {
	ui.Header("beauty study", "the study loop: one subject, one controlled change per frame")
	ui.Suite("subcommands", ui.Rose, []ui.PairRow{
		{Left: "start --name <n> --subject <text> [--title <t>] [--album <a>]", Right: "start (or resume) a study on the loop"},
		{Left: "stop", Right: "stop the active study"},
		{Left: "status", Right: "active study, cycle, last change, frames"},
		{Left: "config [key=value ...]", Right: "show or set the loop's settings (weights.<aspect>=<n> sets one weight)"},
	})
	fmt.Println()
	ui.KV("service", beautyStudyBaseURL("")+" (--url or $"+beautyStudyURLEnv+")")
	ui.KV("doc", "docs/BEAUTY_STUDIES.md")
}

func beautyStudy(args []string) error {
	if len(args) == 0 || isHelp(args[0]) {
		beautyStudyHelp()
		return nil
	}
	switch strings.ToLower(args[0]) {
	case "start":
		return beautyStudyStart(args[1:])
	case "stop":
		return beautyStudyStop(args[1:])
	case "status":
		return beautyStudyStatus(args[1:])
	case "config", "settings":
		return beautyStudyConfig(args[1:])
	default:
		return fmt.Errorf("unknown beauty study command %q; use start, stop, status, or config", args[0])
	}
}

func beautyStudyStart(args []string) error {
	fs := flag.NewFlagSet("beauty study start", flag.ContinueOnError)
	base := beautyStudyURLFlag(fs)
	name := fs.String("name", "", "collection name (slugged by the service)")
	subject := fs.String("subject", "", "the teacher's subject, sent exactly as written")
	preset := fs.String("preset", beautyStudyDefaultPreset, "render preset")
	title := fs.String("title", "", "display title for the collection and influx.pictures")
	album := fs.String("album", "", "influx.pictures album; set it and the loop stages every new frame")
	collections := fs.String("collections", beautyStudyCollectionsDir, "collections directory (fallback when the service ignores title/album)")
	active := fs.String("active", beautyStudyActiveColl, "active collection file (fallback when the service ignores title/album)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if strings.TrimSpace(*subject) == "" {
		return errors.New("usage: flux beauty study start --name <n> --subject <text> [--title <t>] [--album <a>]")
	}
	if *album != "" && !beautyAlbumPattern.MatchString(*album) {
		return fmt.Errorf("album %q must be lowercase letters, digits, and dashes", *album)
	}
	body := map[string]any{"action": "start", "name": *name, "subject": *subject, "preset": *preset}
	if *title != "" {
		body["title"] = *title
	}
	if *album != "" {
		body["album"] = *album
	}
	svc := beautyStudyBaseURL(*base)
	out, err := beautyStudyCall(svc, http.MethodPost, "/api/collection", body)
	if err != nil {
		return err
	}
	act, _ := out["active"].(map[string]any)
	slug := beautyString(act["name"])
	if slug == "" {
		return errors.New("study service did not return the active collection")
	}
	// A service that predates title/album in the start call leaves them unset: write them into the collection files.
	if (*title != "" && beautyString(act["title"]) != *title) || (*album != "" && beautyString(act["album"]) != *album) {
		fields := map[string]any{}
		if *title != "" {
			fields["title"] = *title
		}
		if *album != "" {
			fields["album"] = *album
		}
		if err := beautyStudySetFields(*collections, *active, slug, fields); err != nil {
			return fmt.Errorf("study started, but title/album not set: %w", err)
		}
		act["title"], act["album"] = fields["title"], fields["album"]
	}
	ui.Header("beauty study", "started")
	ui.KV("service", svc)
	ui.KV("name", slug)
	ui.KV("subject", beautyString(act["subject"]))
	ui.KV("preset", beautyString(act["preset"]))
	if *title != "" {
		ui.KV("title", *title)
	}
	if *album != "" {
		ui.KV("album", *album+" (new frames stage for influx.pictures)")
	}
	ui.KV("seed", beautyString(act["seed"]))
	return nil
}

func beautyStudyStop(args []string) error {
	fs := flag.NewFlagSet("beauty study stop", flag.ContinueOnError)
	base := beautyStudyURLFlag(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	svc := beautyStudyBaseURL(*base)
	out, err := beautyStudyCall(svc, http.MethodPost, "/api/collection", map[string]any{"action": "stop"})
	if err != nil {
		return err
	}
	act, _ := out["active"].(map[string]any)
	ui.Header("beauty study", "stopped")
	ui.KV("name", beautyString(act["name"]))
	ui.KV("cycle", beautyString(act["cycle"]))
	return nil
}

func beautyStudyStatus(args []string) error {
	fs := flag.NewFlagSet("beauty study status", flag.ContinueOnError)
	base := beautyStudyURLFlag(fs)
	jsonOut := fs.Bool("json", false, "print the service's JSON")
	if err := fs.Parse(args); err != nil {
		return err
	}
	svc := beautyStudyBaseURL(*base)
	out, err := beautyStudyCall(svc, http.MethodGet, "/api/collection", nil)
	if err != nil {
		return err
	}
	act, _ := out["active"].(map[string]any)
	name := beautyString(act["name"])
	var hist map[string]any
	if name != "" {
		hist, err = beautyStudyCall(svc, http.MethodGet, "/api/collection/history?limit=1&name="+url.QueryEscape(name), nil)
		if err != nil {
			return err
		}
	}
	if *jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(map[string]any{"collection": out, "history": hist})
	}
	ui.Header("beauty study", "status")
	ui.KV("service", svc)
	if name == "" {
		ui.KV("active", "no study")
	} else {
		state := "stopped"
		if b, _ := act["active"].(bool); b {
			state = "running"
		}
		ui.KV("study", name+" · "+state)
		ui.KV("subject", beautyString(act["subject"]))
		if t := beautyString(act["title"]); t != "" {
			ui.KV("title", t)
		}
		if a := beautyString(act["album"]); a != "" {
			ui.KV("album", a)
		}
		ui.KV("cycle", beautyString(act["cycle"]))
		ui.KV("stall", beautyString(act["stall"]))
		ui.KV("last change", beautyString(act["last_change"]))
		if best, ok := act["best"].(map[string]any); ok {
			ui.KV("best", beautyString(best["file"]))
		}
		ui.KV("frames", beautyString(hist["total"])+" in the collection, "+strconv.Itoa(len(beautyList(hist["champions"])))+" accepted or marked up")
		if at, ok := act["updated_at"].(float64); ok && at > 0 {
			ui.KV("updated", time.Unix(int64(at), 0).UTC().Format(time.RFC3339))
		}
	}
	if list := beautyList(out["collections"]); len(list) > 0 {
		names := make([]string, 0, len(list))
		for _, item := range list {
			if c, ok := item.(map[string]any); ok {
				names = append(names, beautyString(c["name"]))
			}
		}
		ui.KV("collections", strings.Join(names, " "))
	}
	return nil
}

func beautyStudyConfig(args []string) error {
	fs := flag.NewFlagSet("beauty study config", flag.ContinueOnError)
	base := beautyStudyURLFlag(fs)
	if err := fs.Parse(args); err != nil {
		return err
	}
	svc := beautyStudyBaseURL(*base)
	cur, err := beautyStudyCall(svc, http.MethodGet, "/api/collection/config", nil)
	if err != nil {
		return err
	}
	current, _ := cur["config"].(map[string]any)
	if current == nil {
		current = map[string]any{}
	}
	if fs.NArg() == 0 {
		beautyStudyPrintConfig(svc, "the loop reads these every frame (unset keys use the loop's defaults)", current)
		return nil
	}
	update, err := beautyStudyConfigUpdate(current, fs.Args())
	if err != nil {
		return err
	}
	out, err := beautyStudyCall(svc, http.MethodPost, "/api/collection/config", update)
	if err != nil {
		return err
	}
	saved, _ := out["config"].(map[string]any)
	beautyStudyPrintConfig(svc, "saved; the loop applies it on its next frame", saved)
	return nil
}

// beautyStudyConfigUpdate turns key=value pairs into the POST body. Values are JSON
// when they parse as JSON (numbers, objects), strings otherwise. weights.<aspect>=<n>
// merges one weight into the current weights.
func beautyStudyConfigUpdate(current map[string]any, pairs []string) (map[string]any, error) {
	update := map[string]any{}
	for _, pair := range pairs {
		key, raw, ok := strings.Cut(pair, "=")
		if !ok || strings.TrimSpace(key) == "" {
			return nil, fmt.Errorf("config takes key=value; got %q", pair)
		}
		key = strings.TrimSpace(key)
		var value any
		if err := json.Unmarshal([]byte(raw), &value); err != nil {
			value = raw
		}
		if aspect, isWeight := strings.CutPrefix(key, "weights."); isWeight {
			n, ok := value.(float64)
			if !ok || aspect == "" {
				return nil, fmt.Errorf("weights.<aspect> takes a number; got %q", pair)
			}
			weights, _ := update["weights"].(map[string]any)
			if weights == nil {
				weights = map[string]any{}
				if cur, ok := current["weights"].(map[string]any); ok {
					for k, v := range cur {
						weights[k] = v
					}
				}
			}
			weights[aspect] = n
			update["weights"] = weights
			continue
		}
		known := false
		for _, k := range beautyStudyConfigKeys {
			if k == key {
				known = true
				break
			}
		}
		if !known {
			return nil, fmt.Errorf("unknown study setting %q; use one of: %s", key, strings.Join(beautyStudyConfigKeys, ", "))
		}
		if key == "weights" {
			if _, ok := value.(map[string]any); !ok {
				return nil, errors.New(`weights takes a JSON object, e.g. weights='{"beauty":3,"change":2}'`)
			}
		}
		update[key] = value
	}
	return update, nil
}

func beautyStudyPrintConfig(svc, subtitle string, cfg map[string]any) {
	ui.Header("beauty study config", subtitle)
	ui.KV("service", svc)
	keys := make([]string, 0, len(cfg))
	for k := range cfg {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if k == "weights" {
			if w, ok := cfg[k].(map[string]any); ok {
				aspects := make([]string, 0, len(w))
				for a := range w {
					aspects = append(aspects, a)
				}
				sort.Strings(aspects)
				for _, a := range aspects {
					ui.KV("weights."+a, beautyString(w[a]))
				}
				continue
			}
		}
		ui.KV(k, beautyString(cfg[k]))
	}
	if len(keys) == 0 {
		ui.KV("settings", "none saved; the loop runs on its defaults")
	}
}

func beautyScore(args []string) error {
	fs := flag.NewFlagSet("beauty score", flag.ContinueOnError)
	base := beautyStudyURLFlag(fs)
	study := fs.String("study", "", "collection name")
	cycle := fs.Int("cycle", 0, "the frame's cycle")
	dim := fs.String("dim", "beauty", "beauty, direction, difference, or uniqueness")
	value := fs.Int("value", 0, "score, -20..20 (0 clears it)")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *study == "" || *cycle <= 0 {
		return errors.New("usage: flux beauty score --study <n> --cycle <c> --dim beauty|direction|difference|uniqueness --value <v>")
	}
	if *value < beautyStudyScoreMin || *value > beautyStudyScoreMax {
		return fmt.Errorf("value %d is outside %d..%d", *value, beautyStudyScoreMin, beautyStudyScoreMax)
	}
	svc := beautyStudyBaseURL(*base)
	d := strings.ToLower(*dim)
	var out map[string]any
	var err error
	switch d {
	case "beauty":
		out, err = beautyStudyCall(svc, http.MethodPost, "/api/collection/mark", map[string]any{"name": *study, "cycle": *cycle, "mark": *value})
	case "direction", "difference", "uniqueness":
		out, err = beautyStudyCall(svc, http.MethodPost, "/api/collection/score", map[string]any{"name": *study, "cycle": *cycle, "dim": d, "value": *value})
	default:
		return fmt.Errorf("unknown dim %q; use beauty, direction, difference, or uniqueness", *dim)
	}
	if err != nil {
		return err
	}
	ui.Header("beauty score", "the teacher's score outranks the judge")
	ui.KV("study", *study)
	ui.KV("cycle", *cycle)
	ui.KV(d, *value)
	if s, ok := out["scores"].(map[string]any); ok && len(s) > 0 {
		raw, _ := json.Marshal(s)
		ui.KV("scores", string(raw))
	}
	return nil
}

func beautyRemove(args []string) error {
	fs := flag.NewFlagSet("beauty remove", flag.ContinueOnError)
	base := beautyStudyURLFlag(fs)
	study := fs.String("study", "", "collection name")
	cycle := fs.Int("cycle", 0, "the frame's cycle")
	restore := fs.Bool("restore", false, "put the frame back into the collection")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *study == "" || *cycle <= 0 {
		return errors.New("usage: flux beauty remove --study <n> --cycle <c> [--restore]")
	}
	svc := beautyStudyBaseURL(*base)
	out, err := beautyStudyCall(svc, http.MethodPost, "/api/collection/remove", map[string]any{"name": *study, "cycle": *cycle, "removed": !*restore})
	if err != nil {
		return err
	}
	action := "removed (kept on disk; unstaged from influx.pictures)"
	if *restore {
		action = "restored (run flux beauty publish to stage it again)"
	}
	ui.Header("beauty remove", action)
	ui.KV("study", *study)
	ui.KV("cycle", *cycle)
	ui.KV("removed now", beautyString(out["removed"]))
	return nil
}

func beautyPublish(args []string) error {
	fs := flag.NewFlagSet("beauty publish", flag.ContinueOnError)
	study := fs.String("study", "", "collection name")
	album := fs.String("album", "", "influx.pictures album (declared in gallery lib/r2.mjs GALLERIES)")
	title := fs.String("title", "", "collection title to record (optional)")
	collections := fs.String("collections", beautyStudyCollectionsDir, "collections directory")
	pictures := fs.String("pictures", beautyStudyPicturesDir, "influx-outputs directory (synced to s3://governor/outputs)")
	active := fs.String("active", beautyStudyActiveColl, "active collection file")
	dryRun := fs.Bool("dry-run", false, "list what would be staged; change nothing")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *study == "" || *album == "" {
		return errors.New("usage: flux beauty publish --study <n> --album <a> [--title <t>] [--dry-run]")
	}
	if !beautyAlbumPattern.MatchString(*album) {
		return fmt.Errorf("album %q must be lowercase letters, digits, and dashes", *album)
	}
	folder := filepath.Join(*collections, *study)
	frames, err := beautyStudyFrames(folder)
	if err != nil {
		return err
	}
	staged, err := beautyStagedForAlbum(*pictures, *album)
	if err != nil && !os.IsNotExist(err) {
		return err
	}
	linked, already := 0, 0
	for _, f := range frames {
		src := filepath.Join(folder, f.File)
		info, err := os.Stat(src)
		if err != nil {
			continue
		}
		if beautyAlreadyStaged(staged, info) {
			already++
			continue
		}
		name := beautySiteName(*album, f.At, f.Cycle)
		dst := filepath.Join(*pictures, name)
		if _, err := os.Stat(dst); err == nil {
			already++
			continue
		}
		if *dryRun {
			fmt.Println("  " + name + "  ←  " + f.File)
			linked++
			continue
		}
		if err := os.MkdirAll(*pictures, 0o755); err != nil {
			return err
		}
		if err := os.Link(src, dst); err != nil {
			return err
		}
		linked++
	}
	if !*dryRun {
		fields := map[string]any{"album": *album}
		if *title != "" {
			fields["title"] = *title
		}
		if err := beautyStudySetFields(*collections, *active, *study, fields); err != nil {
			return err
		}
	}
	verb := "staged"
	if *dryRun {
		verb = "would stage"
	}
	ui.Header("beauty publish", "stage a study's frames for influx.pictures")
	ui.KV("study", folder)
	ui.KV("album", *album)
	ui.KV(verb, fmt.Sprintf("%d frames as hardlinks in %s", linked, *pictures))
	ui.KV("already", fmt.Sprintf("%d frames staged before", already))
	ui.KV("sync", "sessions-sync uploads influx-outputs to s3://governor/outputs within 300 s")
	ui.KV("site", "gallery: node tools/build.mjs, then vercel deploy --prod (docs/BEAUTY_STUDIES.md)")
	return nil
}

type beautyFrame struct {
	Cycle int
	File  string
	At    float64
}

// beautyStudyFrames reads history.jsonl: every filed frame, minus removed.json.
func beautyStudyFrames(folder string) ([]beautyFrame, error) {
	removed := map[int]bool{}
	if raw, err := os.ReadFile(filepath.Join(folder, "removed.json")); err == nil {
		var cycles []int
		if err := json.Unmarshal(raw, &cycles); err != nil {
			return nil, fmt.Errorf("removed.json: %w", err)
		}
		for _, c := range cycles {
			removed[c] = true
		}
	}
	fh, err := os.Open(filepath.Join(folder, "history.jsonl"))
	if err != nil {
		return nil, err
	}
	defer fh.Close()
	var frames []beautyFrame
	scanner := bufio.NewScanner(fh)
	scanner.Buffer(make([]byte, 0, 1<<20), 64<<20)
	for scanner.Scan() {
		var e struct {
			Cycle *float64 `json:"cycle"`
			File  string   `json:"file"`
			At    float64  `json:"at"`
		}
		if json.Unmarshal(scanner.Bytes(), &e) != nil || e.Cycle == nil || e.File == "" {
			continue
		}
		c := int(*e.Cycle)
		if removed[c] {
			continue
		}
		frames = append(frames, beautyFrame{Cycle: c, File: e.File, At: e.At})
	}
	return frames, scanner.Err()
}

// beautySiteName is influx.pictures' name grammar (gallery lib/r2.mjs readProtocol),
// the same as beauty_jury.stage_for_site and backfill_site.py.
func beautySiteName(album string, at float64, cycle int) string {
	stamp := time.Unix(int64(at), 0).UTC().Format("20060102-150405")
	return fmt.Sprintf("protocol-%s-stream-%s-%03d.png", album, stamp, cycle%1000)
}

func beautyStagedForAlbum(dir, album string) (map[int64][]os.FileInfo, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	prefix := "protocol-" + album + "-stream-"
	bySize := map[int64][]os.FileInfo{}
	for _, entry := range entries {
		if !strings.HasPrefix(entry.Name(), prefix) {
			continue
		}
		info, err := os.Stat(filepath.Join(dir, entry.Name()))
		if err != nil {
			continue
		}
		bySize[info.Size()] = append(bySize[info.Size()], info)
	}
	return bySize, nil
}

// beautyAlreadyStaged: the loop may have staged this frame under a slightly different timestamp; a hardlink is the same file.
func beautyAlreadyStaged(staged map[int64][]os.FileInfo, info os.FileInfo) bool {
	for _, other := range staged[info.Size()] {
		if os.SameFile(info, other) {
			return true
		}
	}
	return false
}

// beautyStudySetFields merges fields into the study's collection.json, and into the
// active collection file when that study is the active one.
func beautyStudySetFields(collections, active, name string, fields map[string]any) error {
	paths := []string{filepath.Join(collections, name, "collection.json")}
	if raw, err := os.ReadFile(active); err == nil {
		var cur map[string]any
		if json.Unmarshal(raw, &cur) == nil && beautyString(cur["name"]) == name {
			paths = append(paths, active)
		}
	}
	for _, p := range paths {
		raw, err := os.ReadFile(p)
		if err != nil {
			return err
		}
		cur := map[string]any{}
		if err := json.Unmarshal(raw, &cur); err != nil {
			return fmt.Errorf("%s: %w", p, err)
		}
		for k, v := range fields {
			cur[k] = v
		}
		out, err := json.MarshalIndent(cur, "", " ")
		if err != nil {
			return err
		}
		tmp := p + ".tmp"
		if err := os.WriteFile(tmp, out, 0o644); err != nil {
			return err
		}
		if err := os.Rename(tmp, p); err != nil {
			return err
		}
	}
	return nil
}

func beautyString(v any) string {
	switch x := v.(type) {
	case nil:
		return ""
	case string:
		return x
	case float64:
		if x == float64(int64(x)) {
			return strconv.FormatInt(int64(x), 10)
		}
		return strconv.FormatFloat(x, 'f', -1, 64)
	case bool:
		return strconv.FormatBool(x)
	default:
		raw, err := json.Marshal(x)
		if err != nil {
			return fmt.Sprint(x)
		}
		return string(raw)
	}
}

func beautyList(v any) []any {
	list, _ := v.([]any)
	return list
}
