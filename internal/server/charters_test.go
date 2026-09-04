package server

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestCharterTOMLParse(t *testing.T) {
	src := `
[charter]
id = "dual-seat-research"
slug = "dual-seat-research"
state = "active"
priority = "critical"
opened_at = "2026-09-04T19:30:00Z"
opened_by = "beekeeper"

[charter.goal]
title = "Run dual-seat research"
summary = "Qwen forages, Governor issues"

[charter.contract]
done_when = "each protocol has a tick"
verify = "ls .swarm/protocols/dual-seat-research-doctrine.md"
`
	c := parseCharterTOML("dual-seat-research.toml", src)
	if c.ID != "dual-seat-research" || c.State != "active" || c.Priority != "critical" {
		t.Fatalf("card %+v", c)
	}
	if c.Title != "Run dual-seat research" {
		t.Fatalf("title %q", c.Title)
	}
	if !strings.Contains(c.Verify, "dual-seat-research-doctrine") {
		t.Fatalf("verify %q", c.Verify)
	}
}

func TestCharterQueueRanksActiveCriticalFirst(t *testing.T) {
	dir := t.TempDir()
	swarm := filepath.Join(dir, ".swarm")
	charters := filepath.Join(swarm, "charters")
	if err := os.MkdirAll(charters, 0o755); err != nil {
		t.Fatal(err)
	}
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(charters, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("old.toml", `[charter]
id="old"
state="satisfied"
priority="high"
opened_at="2026-01-01T00:00:00Z"
[charter.goal]
title="done work"`)
	write("later.toml", `[charter]
id="later"
state="active"
priority="medium"
opened_at="2026-09-01T00:00:00Z"
[charter.goal]
title="medium active"`)
	write("now.toml", `[charter]
id="now"
state="active"
priority="critical"
opened_at="2026-09-04T00:00:00Z"
[charter.goal]
title="critical active"`)
	t.Setenv("HIVE_SWARM", swarm)
	q := loadCharterQueue(charters)
	if len(q) != 3 {
		t.Fatalf("n=%d", len(q))
	}
	if q[0].ID != "now" || q[1].ID != "later" || q[2].ID != "old" {
		t.Fatalf("order %+v %+v %+v", q[0].ID, q[1].ID, q[2].ID)
	}
	if q[0].Rank != 1 {
		t.Fatalf("rank %d", q[0].Rank)
	}
}

func TestCharterQueueHonorsResearchSequence(t *testing.T) {
	dir := t.TempDir()
	swarm := filepath.Join(dir, ".swarm")
	charters := filepath.Join(swarm, "charters")
	if err := os.MkdirAll(charters, 0o755); err != nil {
		t.Fatal(err)
	}
	write := func(name, body string) {
		if err := os.WriteFile(filepath.Join(charters, name), []byte(body), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	write("c.toml", `[charter]
id="parent"
state="active"
priority="critical"
sequence=1
opened_at="2026-09-04T22:00:00Z"
[charter.goal]
title="parent"`)
	write("a.toml", `[charter]
id="later-child"
state="active"
priority="critical"
sequence=3
parent="parent"
opened_at="2026-09-04T22:30:00Z"
[charter.goal]
title="third"`)
	write("b.toml", `[charter]
id="first-child"
state="active"
priority="critical"
sequence=2
parent="parent"
opened_at="2026-09-04T22:10:00Z"
[charter.goal]
title="second"`)
	write("z.toml", `[charter]
id="noise"
state="active"
priority="high"
opened_at="2026-09-04T23:00:00Z"
[charter.goal]
title="unsequenced high"`)
	t.Setenv("HIVE_SWARM", swarm)
	q := loadCharterQueue(charters)
	if len(q) != 4 {
		t.Fatalf("n=%d", len(q))
	}
	if q[0].ID != "parent" || q[1].ID != "first-child" || q[2].ID != "later-child" || q[3].ID != "noise" {
		t.Fatalf("order %s %s %s %s", q[0].ID, q[1].ID, q[2].ID, q[3].ID)
	}
}

func TestChartersHostServesQueuePage(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Host = "charters.apiary.vision"
	rec := httptest.NewRecorder()
	s.home(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "The charter queue") {
		t.Fatal("charters host did not serve the queue page")
	}
	if !strings.Contains(body, "tea-chrome") {
		t.Fatal("charters page lost Tea chrome")
	}
}

func TestChartersAPIListsLiveQueue(t *testing.T) {
	dir := t.TempDir()
	swarm := filepath.Join(dir, ".swarm")
	charters := filepath.Join(swarm, "charters")
	if err := os.MkdirAll(charters, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(charters, "x.toml"), []byte(`[charter]
id="x"
state="active"
priority="high"
[charter.goal]
title="X"`), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("HIVE_SWARM", swarm)
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.chartersAPI(rec, httptest.NewRequest(http.MethodGet, "/api/charters", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	var payload map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["ok"] != true {
		t.Fatalf("%s", rec.Body.String())
	}
	queue, _ := payload["queue"].([]any)
	if len(queue) != 1 {
		t.Fatalf("queue %v", payload["queue"])
	}
}
