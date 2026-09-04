package server

import (
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

func (s Server) reportsPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/")
	if r.URL.Path == "/reports/" || r.URL.Path == "/output-reports/" {
		http.Redirect(w, r, "/reports", http.StatusPermanentRedirect)
		return
	}
	if path != "/reports" && path != "/output-reports" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "reports.html"))
}

func (s Server) reportsAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	fashionState := readProtocolStreamStateFile(filepath.Join(s.cfg.Root, ".fluxd", "protocol_stream_gpu3.json"))
	arcaneState := readProtocolStreamStateFile(filepath.Join(s.cfg.Root, ".fluxd", "arcane_stream.json"))
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":           true,
		"generated_at": time.Now().Unix(),
		"host":         "convergence.apiary.vision",
		"lanes": []map[string]any{
			s.reportLane("fashion", "GPU 3 · FLUX FP8", "/gallery/", "/collections/fashion", s.cfg.OutputDir, fashionState, "fashion"),
			s.reportLane("arcane", "GPU 0 · FLUX BF16", "/collections/arcane", "/collections/arcane", s.arcaneOutputDir(), arcaneState, "arcane"),
		},
	})
}

func (s Server) reportLane(id, worker, wall, collection, outDir string, stream map[string]any, scope string) map[string]any {
	if stream == nil {
		stream = map[string]any{}
	}
	audit := digestAudit(outDir, 24)
	return map[string]any{
		"id":         id,
		"worker":     worker,
		"wall":       wall,
		"collection": collection,
		"stream": map[string]any{
			"id":             stream["id"],
			"status":         stream["status"],
			"prompt":         stream["prompt"],
			"prompt_version": stream["prompt_version"],
			"submitted":      stream["submitted"],
			"done":           stream["done"],
			"n":              stream["n"],
			"running":        stream["running"],
			"guidance":       stream["guidance"],
			"steps":          stream["steps"],
			"variant":        stream["variant"],
			"error":          stream["error"],
		},
		"audit":  audit,
		"images": s.listReportImages(scope, 8),
	}
}

func (s Server) listReportImages(scope string, limit int) []map[string]any {
	if limit <= 0 {
		limit = 8
	}
	dir := s.cfg.OutputDir
	prefix := ""
	if scope == "arcane" {
		dir = s.arcaneOutputDir()
		prefix = "arcane/"
	}
	list, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	type item struct {
		name string
		path string
		mod  int64
	}
	var items []item
	for _, entry := range list {
		if entry.IsDir() || !isImageName(entry.Name()) {
			continue
		}
		rel := prefix + entry.Name()
		if !recentAssetAllowed(scope, filepath.ToSlash(rel)) {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		items = append(items, item{
			name: entry.Name(),
			path: "/outputs/" + filepath.ToSlash(rel),
			mod:  info.ModTime().Unix(),
		})
	}
	sort.Slice(items, func(i, j int) bool { return items[i].mod > items[j].mod })
	if len(items) > limit {
		items = items[:limit]
	}
	out := make([]map[string]any, 0, len(items))
	for _, it := range items {
		out = append(out, map[string]any{"name": it.name, "path": it.path, "modified": it.mod})
	}
	return out
}
