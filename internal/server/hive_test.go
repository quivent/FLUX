package server

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"local/flux/internal/config"
)

func TestHivePageServes(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	rec := httptest.NewRecorder()
	s.hivePage(rec, httptest.NewRequest(http.MethodGet, "/hive", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	body := rec.Body.String()
	if !strings.Contains(body, "Hive") || !strings.Contains(body, "Discourse") {
		t.Fatal("hive page missing columns")
	}
}

func TestHiveHostServesHivePage(t *testing.T) {
	s := Server{cfg: config.Config{Root: repoRoot(t)}}
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	req.Host = "hive.apiary.vision"
	rec := httptest.NewRecorder()
	s.home(rec, req)
	if rec.Code != http.StatusOK {
		t.Fatalf("status %d", rec.Code)
	}
	if !strings.Contains(rec.Body.String(), "Colony desk") {
		t.Fatal("hive host did not serve the hive page")
	}
}
