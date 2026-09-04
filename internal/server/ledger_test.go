package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestLedgerPageServes(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.ledgerPage(rec, httptest.NewRequest(http.MethodGet, "/ledger", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "Ledger") || !strings.Contains(body, `class="tea-chrome"`) {
		t.Fatal("ledger page missing Tea parchment chrome")
	}
	if !strings.Contains(body, `href="/gallery/"`) || !strings.Contains(body, `href="/studies"`) {
		t.Fatal("ledger page displaced persistent navigation")
	}
}

func TestLedgerAPIServesJSON(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.ledgerAPI(rec, httptest.NewRequest(http.MethodGet, "/api/tea/ledger", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, `"schema": "tea.ledger/v1"`) {
		t.Fatal("ledger json missing schema")
	}
	if !strings.Contains(body, "The Governor remembers Jay") {
		t.Fatal("ledger json missing first settlement")
	}
}
