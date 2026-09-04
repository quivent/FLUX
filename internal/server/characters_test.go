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

func TestGovernorLawTrainingFocus(t *testing.T) {
	law := teaGovernorLaw()
	if law["reliability"] != "100%" {
		t.Fatalf("reliability %v", law["reliability"])
	}
	focus, _ := law["training_focus"].([]string)
	joined := strings.Join(focus, " ")
	if !strings.Contains(joined, "reliability") || !strings.Contains(joined, "memory_expansion") || !strings.Contains(joined, "reasoning_performance") {
		t.Fatalf("training_focus %v", law["training_focus"])
	}
	line, _ := law["line"].(string)
	if !strings.Contains(line, "100% reliability") || !strings.Contains(line, "memory") || !strings.Contains(line, "reasoning") {
		t.Fatalf("law line %q", line)
	}
}

func TestCharacterDaemonsAreSpectralProjections(t *testing.T) {
	items := defaultCharacterDaemons()
	if len(items) < 6 {
		t.Fatalf("wanted a panel of character daemons, got %d", len(items))
	}
	for _, c := range items {
		if c.Kind != "character" || c.GPU != 1 {
			t.Fatalf("%s is not a GPU 1 character daemon: %+v", c.ID, c)
		}
		if len(c.Bands) != 128 {
			t.Fatalf("%s bands %d", c.ID, len(c.Bands))
		}
		if c.System == "" || !strings.Contains(c.System, "spectral projection") {
			t.Fatalf("%s missing character contract", c.ID)
		}
		for _, k := range []string{"expansion", "discipline", "grounding", "focus"} {
			if _, ok := c.Weights[k]; !ok {
				t.Fatalf("%s missing cable %s", c.ID, k)
			}
		}
	}
}

func TestCharactersAPIMountsAndWritesVectors(t *testing.T) {
	root := t.TempDir()
	s := Server{cfg: config.Config{Root: root}}
	rec := httptest.NewRecorder()
	s.teaCharactersAPI(rec, httptest.NewRequest(http.MethodGet, "/api/tea/characters", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("get %d %s", rec.Code, rec.Body.String())
	}
	var st characterState
	if err := json.Unmarshal(rec.Body.Bytes(), &st); err != nil {
		t.Fatal(err)
	}
	if st.Mounted != "apprentice" || st.HiddenSize != 5376 {
		t.Fatalf("defaults %+v", st)
	}

	body := `{"mounted":"surgeon","items":[{"id":"surgeon","weights":{"expansion":0.2,"discipline":0.9,"grounding":0.9,"focus":0.8}}]}`
	req := httptest.NewRequest(http.MethodPost, "/api/tea/characters", strings.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rec = httptest.NewRecorder()
	s.teaCharactersAPI(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("post %d %s", rec.Code, rec.Body.String())
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &st); err != nil {
		t.Fatal(err)
	}
	if st.Mounted != "surgeon" {
		t.Fatalf("mounted %s", st.Mounted)
	}
	var surgeon characterDaemon
	for _, c := range st.Items {
		if c.ID == "surgeon" {
			surgeon = c
		}
	}
	if surgeon.Weights["discipline"] != 0.9 {
		t.Fatalf("discipline %v", surgeon.Weights)
	}
	if _, err := os.Stat(filepath.Join(root, ".fluxd", "tea_characters.json")); err != nil {
		t.Fatal(err)
	}
}

func TestDaemonsSnapshotIncludesCharacterKind(t *testing.T) {
	s := Server{cfg: config.Config{Root: t.TempDir()}}
	snap := s.teaDaemonsSnapshot()
	n := 0
	for _, d := range snap.Daemons {
		if d.Kind == "character" {
			n++
		}
	}
	if n < 6 {
		t.Fatalf("character daemons in roster: %d", n)
	}
}
