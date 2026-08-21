package arcane

import (
	"strings"
	"testing"
)

const sampleTOML = `
# A comment, and a blank line above.
[continuum]
name = "influx-vision-moj"
version = "3.0.0"
default_profile = "rtx-pro-6000"
cadence_seconds = 6.3
enabled = true

[ports]
governor = 8000
reserved = [8003]
spread = [
  8100,
  8101,   # trailing comment inside an array
  8102,
]

[profiles.rtx-pro-6000]
gpu = "NVIDIA RTX PRO 6000 Blackwell Server Edition"
sm = "sm_120"
vram_gib = 96.0
notes = "quotes # inside a string are not comments"

[profiles.rtx-pro-6000.tenants.flux]
kind = "uds"
precision = "bf16"
resolution = [1024, 1024]
variants.bf16 = { model = "black-forest-labs/FLUX.1-dev", weights_gib = 35.0, note = "BF16 DiT" }
variants.q4_k_s = { model = "city96/FLUX.1-dev-gguf", weights_gib = 6.81, degrades_generator = true }
`

func TestParseTOMLReadsTheContinuumShape(t *testing.T) {
	doc, err := ParseTOML(sampleTOML)
	if err != nil {
		t.Fatalf("ParseTOML: %v", err)
	}

	continuum, ok := TableAt(doc, "continuum")
	if !ok {
		t.Fatal("missing [continuum]")
	}
	if got := Str(continuum, "default_profile", ""); got != "rtx-pro-6000" {
		t.Errorf("default_profile = %q, want rtx-pro-6000", got)
	}
	if got := Num(continuum, "cadence_seconds", 0); got != 6.3 {
		t.Errorf("cadence_seconds = %v, want 6.3", got)
	}
	if !Flag(continuum, "enabled", false) {
		t.Error("enabled should parse as true")
	}

	ports, _ := TableAt(doc, "ports")
	if got := Whole(ports, "governor", 0); got != 8000 {
		t.Errorf("governor port = %d, want 8000", got)
	}
	spread, ok := ports["spread"].([]any)
	if !ok || len(spread) != 3 {
		t.Fatalf("multi-line array parsed as %#v", ports["spread"])
	}

	profile, ok := TableAt(doc, "profiles", "rtx-pro-6000")
	if !ok {
		t.Fatal("missing [profiles.rtx-pro-6000]")
	}
	if got := Str(profile, "sm", ""); got != "sm_120" {
		t.Errorf("sm = %q", got)
	}
	if got := Num(profile, "vram_gib", 0); got != 96.0 {
		t.Errorf("vram_gib = %v", got)
	}
	if got := Str(profile, "notes", ""); !strings.Contains(got, "#") {
		t.Errorf("a # inside a string must survive, got %q", got)
	}

	flux, ok := TableAt(doc, "profiles", "rtx-pro-6000", "tenants", "flux")
	if !ok {
		t.Fatal("missing dotted sub-table for the flux tenant")
	}
	resolution, ok := flux["resolution"].([]any)
	if !ok || len(resolution) != 2 {
		t.Fatalf("resolution parsed as %#v", flux["resolution"])
	}

	// Dotted keys assigning inline tables: `variants.bf16 = { ... }`.
	variant, ok := TableAt(flux, "variants", "bf16")
	if !ok {
		t.Fatal("missing variants.bf16 inline table")
	}
	if got := Str(variant, "model", ""); got != "black-forest-labs/FLUX.1-dev" {
		t.Errorf("variants.bf16.model = %q", got)
	}
	if got := Num(variant, "weights_gib", 0); got != 35.0 {
		t.Errorf("variants.bf16.weights_gib = %v", got)
	}

	q4, _ := TableAt(flux, "variants", "q4_k_s")
	if !Flag(q4, "degrades_generator", false) {
		t.Error("a bool inside an inline table should parse")
	}
	if got := Num(q4, "weights_gib", 0); got != 6.81 {
		t.Errorf("variants.q4_k_s.weights_gib = %v, want 6.81", got)
	}
}

func TestParseTOMLArrayOfTables(t *testing.T) {
	doc, err := ParseTOML("[[judge]]\nname = \"pixtral\"\n\n[[judge]]\nname = \"qwen\"\n")
	if err != nil {
		t.Fatalf("ParseTOML: %v", err)
	}
	rows, ok := doc["judge"].([]any)
	if !ok || len(rows) != 2 {
		t.Fatalf("array of tables parsed as %#v", doc["judge"])
	}
	second, _ := rows[1].(map[string]any)
	if Str(second, "name", "") != "qwen" {
		t.Errorf("second entry = %#v", second)
	}
}

func TestParseTOMLEscapesAndLiteralStrings(t *testing.T) {
	doc, err := ParseTOML(`a = "line\nbreak"` + "\n" + `b = 'raw\nnot-an-escape'` + "\n" + `c = """block"""` + "\n")
	if err != nil {
		t.Fatalf("ParseTOML: %v", err)
	}
	if doc["a"] != "line\nbreak" {
		t.Errorf("basic string escape = %q", doc["a"])
	}
	if doc["b"] != `raw\nnot-an-escape` {
		t.Errorf("literal string should not unescape, got %q", doc["b"])
	}
	if doc["c"] != "block" {
		t.Errorf("multi-line basic string = %q", doc["c"])
	}
}

// Malformed input must return an error rather than panic: the continuum file is
// rewritten by other processes and may be read mid-write.
func TestParseTOMLRejectsMalformedInputWithoutPanicking(t *testing.T) {
	for name, src := range map[string]string{
		"unterminated table": "[profiles.rtx\nkey = 1\n",
		"no equals":          "[a]\njust a bare line\n",
		"unterminated array": "[a]\nports = [8000, 8001\n",
		"unterminated quote": "[a]\nname = \"unclosed\n",
		"empty key segment":  "[a]\nfoo..bar = 1\n",
		"bad inline table":   "[a]\nv = { model = \"x\" 3 }\n",
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := ParseTOML(src); err == nil {
				t.Errorf("expected an error for %s", name)
			}
		})
	}
}

func TestAccessorsTolerateWrongTypes(t *testing.T) {
	table := map[string]any{"n": "not-a-number", "b": 3, "s": 7}
	if got := Num(table, "n", 1.5); got != 1.5 {
		t.Errorf("Num on a string should fall back, got %v", got)
	}
	if got := Flag(table, "b", true); got != true {
		t.Errorf("Flag on an int should fall back, got %v", got)
	}
	if got := Str(table, "s", "fallback"); got != "fallback" {
		t.Errorf("Str on an int should fall back, got %q", got)
	}
	if got := Whole(table, "missing", 42); got != 42 {
		t.Errorf("Whole on a missing key should fall back, got %d", got)
	}
	if _, ok := TableAt(table, "n"); ok {
		t.Error("TableAt on a scalar should report absence")
	}
}
