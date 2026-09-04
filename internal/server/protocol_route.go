package server

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"local/flux/internal/jury"
)

func (s Server) protocolRouteAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	scope := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("scope")))
	if scope == "" || scope == "gallery" || scope == "images" {
		scope = s.liveBeautyScope()
	}
	stream := readProtocolStreamStateLane(s.cfg.Root, scope)
	if stream == nil {
		stream = readProtocolStreamStateFile(protocolBranchStatePath(s.cfg.Root, scope))
	}
	heartbeat := readJSONObjectFile(filepath.Join(s.cfg.Root, ".fluxd", "jury_route.json"))
	generating := tailMatchingJobs(filepath.Join(s.cfg.Root, ".fluxd", "flux-gpu3.jobs.jsonl"), scope, 80)
	latest := latestAuditForScope(s.cfg.OutputDir, scope, 12)
	dir := s.juryDirForLane(requestJuryLane(r, scope))
	cfg, _ := jury.GetConfig(dir)
	station := "idle"
	if n := len(generating); n > 0 {
		station = "generate"
	}
	if heartbeat != nil {
		if ts, ok := asFloat(heartbeat["ts"]); ok && time.Now().Unix()-int64(ts) < 25 {
			if st, _ := heartbeat["station"].(string); st != "" {
				station = st
			}
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"scope":      scope,
		"station":    station,
		"stations":   []string{"generate", "uniqueness", "sensory_gates", "witness", "pixtral", "governor", "composite"},
		"stream":     stream,
		"heartbeat":  heartbeat,
		"generating": generating,
		"latest":     latest,
		"jury":       cfg,
	})
}

func readJSONObjectFile(path string) map[string]any {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var out map[string]any
	if json.Unmarshal(raw, &out) != nil {
		return nil
	}
	return out
}

func tailMatchingJobs(path, scope string, limit int) []map[string]any {
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 2*1024*1024)
	for sc.Scan() {
		t := strings.TrimSpace(sc.Text())
		if t != "" {
			lines = append(lines, t)
		}
	}
	if len(lines) > 200 {
		lines = lines[len(lines)-200:]
	}
	out := []map[string]any{}
	for i := len(lines) - 1; i >= 0 && len(out) < limit; i-- {
		var rec map[string]any
		if json.Unmarshal([]byte(lines[i]), &rec) != nil {
			continue
		}
		hay := strings.ToLower(stringValue(rec["filename"]) + " " + stringValue(rec["output"]))
		if scope != "" && !strings.Contains(hay, strings.ToLower(scope)) {
			continue
		}
		st := stringValue(rec["status"])
		if st != "running" && st != "queued" {
			continue
		}
		out = append(out, map[string]any{
			"id":       rec["id"],
			"status":   st,
			"filename": rec["filename"],
			"width":    rec["width"],
			"height":   rec["height"],
			"steps":    rec["steps"],
		})
	}
	return out
}

func latestAuditForScope(outputDir, scope string, n int) []map[string]any {
	paths := []string{filepath.Join(outputDir, "audit.jsonl")}
	if scope != "" && scope != "fashion" {
		paths = append([]string{filepath.Join(outputDir, "collections", scope, "audit.jsonl")}, paths...)
	}
	var recs []map[string]any
	for _, path := range paths {
		f, err := os.Open(path)
		if err != nil {
			continue
		}
		sc := bufio.NewScanner(f)
		sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
		for sc.Scan() {
			var rec map[string]any
			if json.Unmarshal(sc.Bytes(), &rec) != nil {
				continue
			}
			img := stringValue(rec["image_path"])
			if img == "" {
				img = stringValue(rec["filename"])
			}
			if scope != "" && !strings.Contains(strings.ToLower(img), strings.ToLower(scope)) {
				continue
			}
			row := map[string]any{
				"image":      filepath.Base(img),
				"path":       img,
				"tier":       rec["tier"],
				"composite":  rec["curved_score"],
				"raw":        rec["raw_composite"],
				"unscored":   rec["unscored"],
				"uniqueness": rec["uniqueness"],
				"gates":      rec["gates_summary"],
				"judges":     rec["judges"],
				"ts":         rec["ts"],
			}
			if rec["curved_score"] == nil {
				row["composite"] = rec["composite"]
			}
			recs = append(recs, row)
		}
		_ = f.Close()
	}
	sort.Slice(recs, func(i, j int) bool {
		ti, _ := asFloat(recs[i]["ts"])
		tj, _ := asFloat(recs[j]["ts"])
		return ti < tj
	})
	if len(recs) > n {
		recs = recs[len(recs)-n:]
	}
	return recs
}
