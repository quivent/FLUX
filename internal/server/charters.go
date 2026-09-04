package server

import (
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

type charterCard struct {
	Rank       int    `json:"rank"`
	ID         string `json:"id"`
	Slug       string `json:"slug"`
	State      string `json:"state"`
	Priority   string `json:"priority"`
	Sequence   int    `json:"sequence,omitempty"`
	Parent     string `json:"parent,omitempty"`
	Title      string `json:"title"`
	Summary    string `json:"summary"`
	OpenedBy   string `json:"opened_by"`
	OpenedAt   string `json:"opened_at"`
	DoneWhen   string `json:"done_when"`
	Verify     string `json:"verify"`
	Path       string `json:"path"`
	Kind       string `json:"kind"`
	OpenScopes int    `json:"open_scopes"`
}

func requestHost(r *http.Request) string {
	host := strings.TrimSpace(r.Header.Get("X-Forwarded-Host"))
	if host == "" {
		host = r.Host
	}
	host = strings.ToLower(strings.TrimSpace(strings.Split(host, ",")[0]))
	host = strings.TrimSpace(strings.Split(host, ":")[0])
	return host
}

func hiveSwarmRoot() string {
	if v := strings.TrimSpace(os.Getenv("HIVE_SWARM")); v != "" {
		return v
	}
	if v := strings.TrimSpace(os.Getenv("HIVE_ROOT")); v != "" {
		return filepath.Join(v, ".swarm")
	}
	return "/home/ubuntu/hive/.swarm"
}

func hiveChartersRoot() string {
	return filepath.Join(hiveSwarmRoot(), "charters")
}

func (s Server) chartersPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/")
	if r.URL.Path == "/charters/" {
		http.Redirect(w, r, "/charters", http.StatusPermanentRedirect)
		return
	}
	if path != "/charters" && r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "charters.html"))
}

func (s Server) chartersAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	root := hiveChartersRoot()
	cards := loadCharterQueue(root)
	counts := map[string]int{}
	for _, c := range cards {
		counts[c.State]++
	}
	openN, quarN := scopeCounts(filepath.Join(hiveSwarmRoot(), "scopes"))
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":           true,
		"generated_at": time.Now().UTC().Format(time.RFC3339),
		"source":       root,
		"tea":          "https://tea.geijutsu.work/",
		"atelier":      "https://atelier.apiary.vision/hive",
		"counts":       counts,
		"n":            len(cards),
		"scopes_open":  openN,
		"scopes_quarantine": quarN,
		"queue":        cards,
	})
}

func loadCharterQueue(root string) []charterCard {
	entries, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	seen := map[string]bool{}
	var cards []charterCard
	for _, ent := range entries {
		name := ent.Name()
		if strings.HasPrefix(name, ".") {
			continue
		}
		full := filepath.Join(root, name)
		if ent.IsDir() {
			for _, cand := range []string{"CHARTER.toml", "charter.toml"} {
				p := filepath.Join(full, cand)
				if raw, err := os.ReadFile(p); err == nil {
					c := parseCharterTOML(p, string(raw))
					if c.ID != "" && !seen[c.ID] {
						seen[c.ID] = true
						cards = append(cards, c)
					}
					break
				}
			}
			continue
		}
		if !strings.HasSuffix(name, ".toml") {
			continue
		}
		raw, err := os.ReadFile(full)
		if err != nil {
			continue
		}
		c := parseCharterTOML(full, string(raw))
		if c.ID == "" {
			continue
		}
		if seen[c.ID] {
			continue
		}
		seen[c.ID] = true
		cards = append(cards, c)
	}
	open := listScopeStems(filepath.Join(hiveSwarmRoot(), "scopes", "open"))
	for i := range cards {
		prefix := cards[i].Slug
		if prefix == "" {
			prefix = cards[i].ID
		}
		n := 0
		for _, stem := range open {
			if stem == prefix || strings.HasPrefix(stem, prefix+"-") {
				n++
			}
		}
		cards[i].OpenScopes = n
	}
	sort.SliceStable(cards, func(i, j int) bool {
		si, sj := stateRank(cards[i].State), stateRank(cards[j].State)
		if si != sj {
			return si < sj
		}
		ai, aj := cards[i].Sequence > 0, cards[j].Sequence > 0
		if ai != aj {
			return ai
		}
		if ai && cards[i].Sequence != cards[j].Sequence {
			return cards[i].Sequence < cards[j].Sequence
		}
		pi, pj := priorityRank(cards[i].Priority), priorityRank(cards[j].Priority)
		if pi != pj {
			return pi < pj
		}
		return cards[i].OpenedAt > cards[j].OpenedAt
	})
	for i := range cards {
		cards[i].Rank = i + 1
	}
	return cards
}

func listScopeStems(dir string) []string {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil
	}
	var out []string
	for _, ent := range entries {
		if ent.IsDir() {
			continue
		}
		name := ent.Name()
		if strings.HasSuffix(name, ".toml") {
			out = append(out, strings.TrimSuffix(name, ".toml"))
		}
	}
	return out
}

func scopeCounts(root string) (open, quarantine int) {
	if entries, err := os.ReadDir(filepath.Join(root, "open")); err == nil {
		for _, e := range entries {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".toml") {
				open++
			}
		}
	}
	if entries, err := os.ReadDir(filepath.Join(root, "quarantine")); err == nil {
		for _, e := range entries {
			if !e.IsDir() && strings.HasSuffix(e.Name(), ".toml") {
				quarantine++
			}
		}
	}
	return
}

func parseCharterTOML(path, src string) charterCard {
	id := tomlField(src, "charter", "id")
	if id == "" {
		id = strings.TrimSuffix(filepath.Base(path), ".toml")
		id = strings.TrimSuffix(id, ".TOML")
		if strings.EqualFold(id, "CHARTER") || strings.EqualFold(id, "charter") {
			id = filepath.Base(filepath.Dir(path))
		}
	}
	slug := tomlField(src, "charter", "slug")
	if slug == "" {
		slug = id
	}
	title := tomlField(src, "charter.goal", "title")
	if title == "" {
		title = tomlField(src, "charter", "goal_inline")
	}
	if title == "" {
		title = slug
	}
	summary := firstNonEmpty(
		tomlField(src, "charter.goal", "summary"),
		tomlField(src, "charter.goal", "statement"),
		tomlField(src, "charter.goal", "prose"),
		tomlField(src, "charter.notes", "strategy"),
	)
	openedBy := firstNonEmpty(
		tomlField(src, "charter", "opened_by"),
		tomlField(src, "charter", "issued_by"),
		tomlField(src, "charter", "proposed_by"),
		tomlField(src, "charter", "approved_by"),
	)
	openedAt := firstNonEmpty(
		tomlField(src, "charter", "opened_at"),
		tomlField(src, "charter", "created_at"),
		tomlField(src, "charter", "last_review"),
	)
	rel, _ := filepath.Rel(hiveChartersRoot(), path)
	if rel == "" || strings.HasPrefix(rel, "..") {
		rel = filepath.Base(path)
	}
	seq := 0
	if n, err := strconv.Atoi(strings.TrimSpace(tomlField(src, "charter", "sequence"))); err == nil {
		seq = n
	}
	return charterCard{
		ID:       id,
		Slug:     slug,
		State:    strings.ToLower(firstNonEmpty(tomlField(src, "charter", "state"), "unset")),
		Priority: strings.ToLower(firstNonEmpty(tomlField(src, "charter", "priority"), "medium")),
		Sequence: seq,
		Parent:   tomlField(src, "charter", "parent"),
		Title:    clip(title, 220),
		Summary:  clip(collapseSpace(summary), 420),
		OpenedBy: openedBy,
		OpenedAt: openedAt,
		DoneWhen: clip(collapseSpace(firstNonEmpty(
			tomlField(src, "charter.contract", "done_when"),
			tomlField(src, "charter.goal", "done_when"),
		)), 280),
		Verify: tomlField(src, "charter.contract", "verify"),
		Path:   filepath.ToSlash(rel),
		Kind:   "toml",
	}
}

func firstNonEmpty(ss ...string) string {
	for _, s := range ss {
		if strings.TrimSpace(s) != "" {
			return strings.TrimSpace(s)
		}
	}
	return ""
}

func collapseSpace(s string) string {
	return strings.TrimSpace(strings.Join(strings.Fields(s), " "))
}

func clip(s string, n int) string {
	s = strings.TrimSpace(s)
	if n <= 0 || len(s) <= n {
		return s
	}
	return strings.TrimSpace(s[:n]) + "…"
}

func stateRank(s string) int {
	switch s {
	case "active":
		return 0
	case "proposed":
		return 1
	case "paused":
		return 2
	case "implemented":
		return 3
	case "satisfied":
		return 4
	case "superseded":
		return 5
	case "parked":
		return 6
	default:
		return 7
	}
}

func priorityRank(s string) int {
	switch s {
	case "critical":
		return 0
	case "high":
		return 1
	case "medium":
		return 2
	case "low":
		return 3
	default:
		return 4
	}
}

var (
	tomlSectionRe = regexp.MustCompile(`(?m)^\[([^\]]+)\]\s*$`)
	tomlTripleRe  = regexp.MustCompile(`(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*"""([\s\S]*?)"""`)
	tomlQuoteRe   = regexp.MustCompile(`(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*"([^"]*)"`)
	tomlBareRe    = regexp.MustCompile(`(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*([^\s#][^#\n]*)`)
)

func tomlField(src, section, key string) string {
	body := tomlSection(src, section)
	if body == "" {
		return ""
	}
	for _, re := range []*regexp.Regexp{tomlTripleRe, tomlQuoteRe, tomlBareRe} {
		ms := re.FindAllStringSubmatch(body, -1)
		for _, m := range ms {
			if len(m) >= 3 && m[1] == key {
				return strings.TrimSpace(strings.Trim(m[2], `"`))
			}
		}
	}
	return ""
}

func tomlSection(src, name string) string {
	idxs := tomlSectionRe.FindAllStringSubmatchIndex(src, -1)
	if len(idxs) == 0 {
		if name == "charter" {
			return src
		}
		return ""
	}
	for i, idx := range idxs {
		sec := src[idx[2]:idx[3]]
		if sec != name {
			continue
		}
		start := idx[1]
		end := len(src)
		if i+1 < len(idxs) {
			end = idxs[i+1][0]
		}
		return src[start:end]
	}
	return ""
}

