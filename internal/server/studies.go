package server

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"unicode"
)

func (s Server) teaStudiesPage(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/studies/" {
		http.Redirect(w, r, "/studies", http.StatusPermanentRedirect)
		return
	}
	if r.URL.Path != "/studies" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "studies.html"))
}

func (s Server) teaStudiesAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	studies, err := loadTeaStudies(s.cfg.Root)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"count":   len(studies),
		"studies": studies,
	})
}

func loadTeaStudies(root string) ([]map[string]any, error) {
	curatedPath := filepath.Join(root, "apps", "tea", "studies.json")
	curatedRaw, err := os.ReadFile(curatedPath)
	if err != nil {
		return nil, fmt.Errorf("read Tea study catalog: %w", err)
	}
	var curated []map[string]any
	if err := json.Unmarshal(curatedRaw, &curated); err != nil {
		return nil, fmt.Errorf("parse Tea study catalog: %w", err)
	}
	studies := make([]map[string]any, 0, len(curated)+40)
	for _, raw := range curated {
		studies = append(studies, sanitizeTeaStudy(raw, stringValue(raw["source"]), stringValue(raw["status"])))
	}

	draftsRoot := filepath.Join(root, "atlas_drafts")
	entries, err := os.ReadDir(draftsRoot)
	if err != nil {
		return nil, fmt.Errorf("read atlas drafts: %w", err)
	}
	drafts := make([]map[string]any, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".json" {
			continue
		}
		rawBytes, readErr := os.ReadFile(filepath.Join(draftsRoot, entry.Name()))
		if readErr != nil {
			return nil, fmt.Errorf("read atlas draft %s: %w", entry.Name(), readErr)
		}
		var raw map[string]any
		if err := json.Unmarshal(rawBytes, &raw); err != nil {
			return nil, fmt.Errorf("parse atlas draft %s: %w", entry.Name(), err)
		}
		drafts = append(drafts, sanitizeTeaStudy(raw, filepath.ToSlash(filepath.Join("atlas_drafts", entry.Name())), "draft"))
	}
	sort.SliceStable(drafts, func(i, j int) bool {
		return strings.ToLower(stringValue(drafts[i]["label"])) < strings.ToLower(stringValue(drafts[j]["label"]))
	})
	return append(studies, drafts...), nil
}

func sanitizeTeaStudy(raw map[string]any, source, status string) map[string]any {
	id := strings.TrimSpace(stringValue(raw["id"]))
	if id == "" {
		id = strings.TrimSuffix(filepath.Base(source), filepath.Ext(source))
	}
	label := strings.TrimSpace(stringValue(raw["label"]))
	if label == "" || label == id {
		label = teaStudyLabel(id)
	}
	kind := teaStudyKind(raw)
	study := map[string]any{
		"id":          id,
		"label":       label,
		"subject":     strings.TrimSpace(stringValue(raw["subject"])),
		"prompt":      strings.TrimSpace(stringValue(raw["prompt"])),
		"note":        strings.TrimSpace(stringValue(raw["note"])),
		"kind":        kind,
		"status":      valueOr(status, "draft"),
		"source":      source,
		"mode":        strings.TrimSpace(stringValue(raw["mode"])),
		"sample_mode": strings.TrimSpace(stringValue(raw["sample_mode"])),
		"url":         strings.TrimSpace(stringValue(raw["url"])),
		"featured":    raw["featured"] == true,
	}
	for _, key := range []string{"size", "steps", "guidance", "batch_size", "render_count", "n_latent", "index_start", "index_end", "seed"} {
		if value, ok := raw[key]; ok && value != nil {
			study[key] = value
		}
	}
	rows, cols := intValue(raw["n_rows"]), intValue(raw["n_cols"])
	if rows > 0 && cols > 0 {
		study["grid"] = fmt.Sprintf("%d × %d", rows, cols)
	}
	return study
}

func teaStudyKind(raw map[string]any) string {
	studyType := strings.ToLower(strings.TrimSpace(stringValue(raw["study_type"])))
	sampleMode := strings.ToLower(strings.TrimSpace(stringValue(raw["sample_mode"])))
	haystack := strings.ToLower(strings.Join([]string{
		stringValue(raw["id"]), stringValue(raw["label"]), stringValue(raw["subject"]), stringValue(raw["kind"]),
	}, " "))
	if explicit := strings.TrimSpace(stringValue(raw["kind"])); explicit != "" && explicit != "latent_sphere_map" {
		return explicit
	}
	switch studyType {
	case "loop":
		return "motion loop"
	case "movement":
		return "motion"
	case "atlas":
		return "atlas"
	}
	if sampleMode == "contiguous" || strings.Contains(haystack, "motion") || strings.Contains(haystack, "turntable") {
		return "motion path"
	}
	if strings.Contains(sampleMode, "sparse") || strings.Contains(haystack, "scout") {
		return "atlas scout"
	}
	return "atlas study"
}

func teaStudyLabel(id string) string {
	label := strings.TrimPrefix(id, "spheremap_atlas_")
	label = strings.NewReplacer("-", " ", "_", " ").Replace(label)
	words := strings.Fields(label)
	for i, word := range words {
		runes := []rune(word)
		if len(runes) > 0 {
			runes[0] = unicode.ToUpper(runes[0])
			words[i] = string(runes)
		}
	}
	return strings.Join(words, " ")
}
