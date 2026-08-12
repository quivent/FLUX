package server

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

// The read-only gate is the only thing between a public listener and an H100,
// so it gets a test that names the exact surface: anything not listed here is
// refused, including routes added later.
func TestReadOnlyGate(t *testing.T) {
	reached := false
	handler := withReadOnly(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		w.WriteHeader(http.StatusOK)
	}), true)

	cases := []struct {
		method string
		path   string
		allow  bool
	}{
		{http.MethodGet, "/", true},
		{http.MethodGet, "/app", true},
		{http.MethodGet, "/gallery/", true},
		{http.MethodGet, "/movement", true},
		{http.MethodGet, "/atelier/", true},
		{http.MethodGet, "/outputs/atlas/x.png", true},
		{http.MethodGet, "/api/health", true},
		{http.MethodGet, "/api/recent-images", true},
		{http.MethodGet, "/api/assets/ws", true},
		{http.MethodGet, "/api/telemetry/events", true},
		{http.MethodGet, "/api/telemetry/ws", true},
		{http.MethodGet, "/api/jobs", true},
		{http.MethodGet, "/api/jobs/ws", true},
		// The gallery is unusable without thumbnails; a full-size wall is
		// hundreds of megabytes.
		{http.MethodGet, "/api/asset/thumbnail?w=384&src=/outputs/a.png", true},

		// Renders, warmups and cancels all cost GPU time or money.
		{http.MethodPost, "/api/render", false},
		{http.MethodPost, "/api/warm", false},
		{http.MethodPost, "/api/atlas", false},
		{http.MethodPost, "/api/governor/chat", false},
		{http.MethodPost, "/api/jobs", false},
		{http.MethodGet, "/api/jobs/abc/cancel", false},
		// A GET is not automatically safe: warm loads the model.
		{http.MethodGet, "/api/warm", false},
		{http.MethodDelete, "/outputs/atlas/x.png", false},
	}

	for _, tc := range cases {
		reached = false
		rec := httptest.NewRecorder()
		handler.ServeHTTP(rec, httptest.NewRequest(tc.method, tc.path, nil))
		if tc.allow && !reached {
			t.Errorf("%s %s: expected to pass the gate, got %d", tc.method, tc.path, rec.Code)
		}
		if !tc.allow {
			if reached {
				t.Errorf("%s %s: reached the handler; the gate must refuse it", tc.method, tc.path)
			}
			if rec.Code != http.StatusForbidden {
				t.Errorf("%s %s: expected 403, got %d", tc.method, tc.path, rec.Code)
			}
		}
	}
}

// Disabled is the default everywhere except a deliberate public bind, so it
// must be a true pass-through rather than a subtly different handler.
func TestReadOnlyDisabledPassesEverything(t *testing.T) {
	reached := false
	handler := withReadOnly(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
	}), false)
	handler.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodPost, "/api/render", nil))
	if !reached {
		t.Fatal("read-only disabled must not block anything")
	}
}
