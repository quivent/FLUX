package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestDeskIsTheControlPanel(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "desk.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{
		`GPU 3 · Beauty jury`,
		`id="seats"`,
		`Uniqueness influence`,
		`Gate triage`,
		`Hive calibrate`,
		`/api/protocol/calibrate`,
		`data-lane="fashion"`,
		`failed`,
		`competent`,
		`flawless`,
		`setInterval(refreshHud, 2000)`,
	} {
		if !strings.Contains(src, tok) {
			t.Errorf("desk is still a directory, missing %q", tok)
		}
	}
	if strings.Contains(src, `class="desk-card"`) {
		t.Error("desk still ships as a card index")
	}
	js, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "desk.js"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(js), `lane = "microgreens"`) {
		t.Error("desk.js still defaults to microgreens")
	}
}
