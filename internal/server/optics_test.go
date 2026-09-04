package server

import "testing"

func TestClassifyHat(t *testing.T) {
	cases := []struct {
		ids  []string
		kind string
	}{
		{[]string{"governor", "gemma-governor"}, "governor_gemma"},
		{[]string{"hive-research", "qwen38"}, "qwen"},
		{[]string{"jury", "gemma-jury"}, "flux_jury"},
		{[]string{"pixtral"}, "pixtral"},
		{[]string{"governor"}, "governor_gemma"},
		{[]string{"governor", "qwen-governor", "qwen38"}, "qwen"},
		{nil, "down"},
	}
	for _, tc := range cases {
		if got := classifyHat(tc.ids); got != tc.kind {
			t.Errorf("classifyHat(%v) = %q, want %q", tc.ids, got, tc.kind)
		}
	}
}

func TestHatsFromSocketsPrefersGemmaGovernor(t *testing.T) {
	socks := []opticSocket{
		{Port: 8000, Live: true, Kind: "governor_gemma", Model: "governor", IDs: []string{"governor", "gemma-governor"}},
		{Port: 8001, Live: true, Kind: "flux_jury", Model: "jury"},
		{Port: 8002, Live: true, Kind: "qwen", Model: "hive-research"},
	}
	hats := hatsFromSockets(socks)
	g := hats["governor"].(map[string]any)
	if g["port"] != 8000 {
		t.Fatalf("governor port %v", g["port"])
	}
	q := hats["qwen"].(map[string]any)
	if q["port"] != 8002 {
		t.Fatalf("qwen port %v", q["port"])
	}
	j := hats["jury"].(map[string]any)
	if j["port"] != 8001 {
		t.Fatalf("jury port %v", j["port"])
	}
}
