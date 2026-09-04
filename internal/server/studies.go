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
	studies := make([]map[string]any, 0, len(curated)+90)
	seen := map[string]bool{}
	for _, raw := range curated {
		study := sanitizeTeaStudy(raw, stringValue(raw["source"]), stringValue(raw["status"]))
		studies = append(studies, study)
		seen[stringValue(study["id"])] = true
	}
	for _, study := range loadBeautyQueueStudies(root) {
		id := stringValue(study["id"])
		if seen[id] {
			continue
		}
		studies = append(studies, study)
		seen[id] = true
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
		study := sanitizeTeaStudy(raw, filepath.ToSlash(filepath.Join("atlas_drafts", entry.Name())), "draft")
		if seen[stringValue(study["id"])] {
			continue
		}
		drafts = append(drafts, study)
	}
	sort.SliceStable(drafts, func(i, j int) bool {
		return strings.ToLower(stringValue(drafts[i]["label"])) < strings.ToLower(stringValue(drafts[j]["label"]))
	})
	out := append(studies, drafts...)
	overlayLiveStudyRuns(root, out)
	return out, nil
}

func loadBeautyQueueStudies(root string) []map[string]any {
	path := filepath.Join(root, "chorus", "beauty-queue.json")
	rawBytes, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	var queue struct {
		Name     string           `json:"name"`
		Defaults map[string]any   `json:"defaults"`
		Approval map[string]any   `json:"approval"`
		Jobs     []map[string]any `json:"jobs"`
	}
	if err := json.Unmarshal(rawBytes, &queue); err != nil {
		return nil
	}
	source := "chorus/beauty-queue.json"
	out := make([]map[string]any, 0, len(queue.Jobs)+1)
	parentVars := map[string]any{}
	for k, v := range queue.Defaults {
		parentVars[k] = v
	}
	parentVars["jobs"] = len(queue.Jobs)
	parent := sanitizeTeaStudy(map[string]any{
		"id":        "images-of-beauty-48",
		"label":     valueOr(queue.Name, "Images of Beauty — Forty-Eight Studies"),
		"family":    "beauty",
		"kind":      "beauty queue",
		"featured":  true,
		"subject":   "Forty-eight prepared stills. Each child card is a prompt plus the shared beauty-protocol variables.",
		"prompt":    valueOr(stringValue(queue.Approval["rule"]), "All studies inherit the beauty protocol; parameters remain live-adjustable per generation."),
		"note":      "Prepared, not running. Copy a child study to commission. Do not mix onto the fashion GPU.",
		"url":       "/protocol",
		"variables": parentVars,
	}, source, "prepared")
	out = append(out, parent)
	for _, job := range queue.Jobs {
		name := strings.TrimSpace(stringValue(job["name"]))
		if name == "" {
			continue
		}
		focus := strings.TrimSpace(stringValue(job["focus"]))
		vars := map[string]any{}
		for k, v := range queue.Defaults {
			vars[k] = v
		}
		if seed := job["seed"]; seed != nil {
			vars["seed"] = seed
		}
		if axis := strings.TrimSpace(stringValue(job["axis"])); axis != "" {
			vars["axis"] = axis
		}
		vars["approved"] = job["approved"] == true
		status := "prepared"
		if job["approved"] != true {
			status = "planned"
		}
		out = append(out, sanitizeTeaStudy(map[string]any{
			"id":        "beauty-queue-" + teaStudySlug(name),
			"label":     name,
			"family":    "beauty",
			"kind":      "beauty stills",
			"subject":   focus,
			"prompt":    focus,
			"seed":      job["seed"],
			"note":      "Prepared garden-machine study. Inherits the beauty queue defaults.",
			"variables": vars,
		}, source, status))
	}
	return out
}

func overlayLiveStudyRuns(root string, studies []map[string]any) {
	type live struct {
		id   string
		file string
	}
	for _, item := range []live{
		{id: "fashion-beauty-on-beauty", file: filepath.Join(root, ".fluxd", "protocol_stream_gpu3.json")},
		{id: "arcane-atlas-mine", file: filepath.Join(root, ".fluxd", "arcane_stream.json")},
	} {
		raw, err := os.ReadFile(item.file)
		if err != nil {
			continue
		}
		var state map[string]any
		if json.Unmarshal(raw, &state) != nil {
			continue
		}
		for _, study := range studies {
			if stringValue(study["id"]) != item.id {
				continue
			}
			if v := stringValue(state["status"]); v != "" {
				study["status"] = v
			}
			study["live"] = map[string]any{
				"submitted": state["submitted"],
				"done":      state["done"],
				"running":   state["running"],
				"variant":   state["variant"],
				"n":         state["n"],
			}
		}
	}
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
	note := strings.TrimSpace(stringValue(raw["note"]))
	if note == "" {
		note = strings.TrimSpace(stringValue(raw["notes"]))
	}
	study := map[string]any{
		"id":          id,
		"label":       label,
		"subject":     strings.TrimSpace(stringValue(raw["subject"])),
		"prompt":      strings.TrimSpace(stringValue(raw["prompt"])),
		"note":        note,
		"kind":        kind,
		"status":      valueOr(status, "draft"),
		"source":      source,
		"mode":        strings.TrimSpace(stringValue(raw["mode"])),
		"sample_mode": strings.TrimSpace(stringValue(raw["sample_mode"])),
		"url":         strings.TrimSpace(stringValue(raw["url"])),
		"wall":        strings.TrimSpace(stringValue(raw["wall"])),
		"family":      teaStudyFamily(raw, kind, id, label),
		"gpu":         raw["gpu"],
		"extra":       strings.TrimSpace(stringValue(raw["extra"])),
		"featured":    raw["featured"] == true,
		"locked":      raw["locked"] == true,
	}
	for _, key := range []string{"size", "steps", "guidance", "batch_size", "render_count", "n_latent", "index_start", "index_end", "seed"} {
		if value, ok := raw[key]; ok && value != nil {
			study[key] = value
		}
	}
	if vars := teaStudyVariables(raw); len(vars) > 0 {
		study["variables"] = vars
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

func teaStudyFamily(raw map[string]any, kind, id, label string) string {
	if explicit := strings.TrimSpace(stringValue(raw["family"])); explicit != "" {
		return explicit
	}
	hay := strings.ToLower(strings.Join([]string{
		id, label, kind, stringValue(raw["subject"]), stringValue(raw["experiment_id"]),
	}, " "))
	switch {
	case strings.Contains(hay, "arcane"):
		return "arcane"
	case strings.Contains(hay, "fashion"), strings.Contains(hay, "celadon"), strings.Contains(hay, "beauty"):
		return "beauty"
	case strings.Contains(hay, "stallion"), strings.Contains(hay, "horse"), strings.Contains(hay, "equine"), strings.Contains(hay, "gallop"):
		return "motion"
	case strings.Contains(hay, "motion"), strings.Contains(hay, "turntable"), strings.Contains(kind, "motion"):
		return "motion"
	default:
		return "atlas"
	}
}

func teaStudyVariables(raw map[string]any) map[string]any {
	vars := map[string]any{}
	if explicit, ok := raw["variables"].(map[string]any); ok {
		for k, v := range explicit {
			if v != nil {
				vars[k] = v
			}
		}
	}
	for _, key := range []string{
		"steps", "guidance", "size", "seed", "seed_a", "seed_b", "seed_c", "seed_d",
		"seed_lock", "shell_scale", "shell_coupling", "n_cols", "n_rows", "n_latent",
		"traversal", "sample_mode", "model", "precision", "batch_size", "render_count",
		"extra", "index_start", "index_end", "run_type", "study_type", "n", "resolution",
		"queue_depth", "worker", "wall", "lock",
	} {
		if _, exists := vars[key]; exists {
			continue
		}
		if value, ok := raw[key]; ok && value != nil && strings.TrimSpace(stringValue(value)) != "" {
			vars[key] = value
		}
	}
	if len(vars) == 0 {
		return nil
	}
	return vars
}

func teaStudySlug(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	var b strings.Builder
	lastDash := false
	for _, r := range s {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			b.WriteRune(r)
			lastDash = false
			continue
		}
		if !lastDash {
			b.WriteByte('-')
			lastDash = true
		}
	}
	return strings.Trim(b.String(), "-")
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
