package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestBellProtocolsAreDistinctGeometryWithNoPrompt(t *testing.T) {
	want := []string{"near", "open", "sway", "orbit", "cache"}
	seen := map[string]bool{}
	for _, name := range want {
		protocol, ok := bellProtocols[name]
		if !ok {
			t.Fatalf("missing Bell protocol %q", name)
		}
		key := strings.Join([]string{
			protocol.Mode,
			protocol.Adapter,
			string(rune(int(protocol.ShellScale * 100))),
			string(rune(int(protocol.SeedLock * 100))),
			string(rune(int(protocol.ShellCoupling * 100))),
		}, ":")
		if seen[key] {
			t.Errorf("protocol %q duplicates an earlier geometry", name)
		}
		seen[key] = true
	}
}

func TestDirectionalTournamentHasDirectorAndFourLiteralDirections(t *testing.T) {
	path := filepath.Join("..", "..", "chorus", "tournament.py")
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	source := string(raw)
	for _, token := range []string{
		`DIRECTIONS = ("north", "south", "east", "west")`,
		`PERSPECTIVES = ("continuity", "composition", "material_light", "meaningful_change")`,
		`"action": "advance" if advance else "hold"`,
		`"parent": [1.0, 0.0, 0.0, 0.0]`,
		`"batch_size": 4`,
	} {
		if !strings.Contains(source, token) {
			t.Errorf("directional protocol missing %q", token)
		}
	}
}
