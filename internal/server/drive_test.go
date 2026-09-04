package server

import (
	"os"
	"testing"
)

func TestLoadTrainingShape(t *testing.T) {
	research := "/home/ubuntu/hive/.swarm/research/dual-seat-drive"
	intel := "/home/ubuntu/hive/.swarm/intelligence"
	tea := "/home/ubuntu/CLIs/flux/apps/tea/public"
	if _, err := os.Stat(research); err != nil {
		t.Skip("hive research not present")
	}
	got := loadTraining(research, intel, tea)
	for _, k := range []string{"curriculum", "means", "rating", "cycles", "evolution"} {
		if got[k] == nil {
			t.Fatalf("missing %s", k)
		}
	}
	cur, _ := got["curriculum"].(map[string]any)
	if cur == nil {
		t.Fatal("curriculum not a map")
	}
	q, _ := cur["question"].(string)
	if q == "" {
		t.Fatal("empty curriculum question")
	}
	cyc, _ := got["cycles"].([]map[string]any)
	if len(cyc) == 0 {
		t.Fatal("no cycle feedback")
	}
	evo, _ := got["evolution"].(map[string]any)
	if evo == nil {
		t.Fatal("evolution missing")
	}
	eras, _ := evo["eras"].([]map[string]any)
	if len(eras) == 0 {
		t.Fatal("no mechanism eras")
	}
}
