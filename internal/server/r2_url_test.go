package server

import (
	"net/http/httptest"
	"strings"
	"testing"

	"local/flux/internal/cdn"
)

func TestPublicOutputRelURLUsesR2ForCollections(t *testing.T) {
	req := httptest.NewRequest("GET", "/", nil)
	req.Host = "tea.geijutsu.work"
	got := publicOutputRelURL(req, "collections/silken-horses/protocol-silken-horses-stream-20260904-234316-265.png")
	if !strings.HasPrefix(got, cdn.DefaultPublicBase+"/") {
		t.Fatalf("collection asset not on R2: %s", got)
	}
	if strings.Contains(got, "/outputs/") {
		t.Fatalf("collection asset still local: %s", got)
	}
	local := publicOutputRelURL(req, "protocol-fashion-stream-001.png")
	if !strings.Contains(local, "/outputs/") {
		t.Fatalf("root fashion should stay on /outputs/: %s", local)
	}
}
