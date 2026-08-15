package daemon

import (
	"net"
	"os"
	"path/filepath"
	"testing"

	"local/flux/internal/config"
)

func listenAt(t *testing.T, path string) {
	t.Helper()
	ln, err := net.Listen("unix", path)
	if err != nil {
		t.Fatalf("listen %s: %v", path, err)
	}
	t.Cleanup(func() { _ = ln.Close() })
}

func fluxdRoot(t *testing.T) config.Config {
	t.Helper()
	// Unix socket paths are capped near 104 bytes on darwin, well under what
	// t.TempDir() produces, so keep the root short.
	root, err := os.MkdirTemp("/tmp", "fx")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.RemoveAll(root) })
	if err := os.MkdirAll(filepath.Join(root, ".fluxd"), 0o755); err != nil {
		t.Fatal(err)
	}
	return config.Config{Root: root}
}

// With no fleet worker listening the CLI keeps its historical default, so a
// plain single-worker install is unaffected.
func TestNewFallsBackToDefaultSocket(t *testing.T) {
	cfg := fluxdRoot(t)
	if got := New(cfg).name; got != "flux" {
		t.Fatalf("worker name = %q, want flux", got)
	}
}

// A live per-GPU worker is adopted, so `flux render` reuses the pipeline the
// HTTP server already loaded instead of starting a second one.
func TestNewAdoptsLiveFleetWorker(t *testing.T) {
	cfg := fluxdRoot(t)
	listenAt(t, filepath.Join(cfg.Root, ".fluxd", "flux-gpu0.sock"))
	client := New(cfg)
	if client.name != "flux-gpu0" {
		t.Fatalf("worker name = %q, want flux-gpu0", client.name)
	}
	if filepath.Base(client.socket) != "flux-gpu0.sock" {
		t.Fatalf("socket = %q, want flux-gpu0.sock", client.socket)
	}
}

// A stale socket file with nothing behind it must not be adopted: dialing is
// the only proof the pipeline is resident.
func TestNewIgnoresStaleFleetSocket(t *testing.T) {
	cfg := fluxdRoot(t)
	stale := filepath.Join(cfg.Root, ".fluxd", "flux-gpu0.sock")
	listener, err := net.Listen("unix", stale)
	if err != nil {
		t.Fatal(err)
	}
	_ = listener.Close() // leaves the file, kills the listener
	if got := New(cfg).name; got != "flux" {
		t.Fatalf("stale socket adopted as %q", got)
	}
}

// The lowest live index wins, so the CLI is deterministic on a multi-GPU fleet.
func TestNewPrefersLowestLiveGPU(t *testing.T) {
	cfg := fluxdRoot(t)
	listenAt(t, filepath.Join(cfg.Root, ".fluxd", "flux-gpu2.sock"))
	listenAt(t, filepath.Join(cfg.Root, ".fluxd", "flux-gpu1.sock"))
	if got := New(cfg).name; got != "flux-gpu1" {
		t.Fatalf("worker name = %q, want flux-gpu1", got)
	}
}

// An explicit name always wins; adoption only fills in the unnamed default.
func TestNewNamedIsNeverOverridden(t *testing.T) {
	cfg := fluxdRoot(t)
	listenAt(t, filepath.Join(cfg.Root, ".fluxd", "flux-gpu0.sock"))
	if got := NewNamed(cfg, "img2img").name; got != "img2img" {
		t.Fatalf("explicit name became %q", got)
	}
}

func TestFleetAdoptionCanBeDisabled(t *testing.T) {
	cfg := fluxdRoot(t)
	listenAt(t, filepath.Join(cfg.Root, ".fluxd", "flux-gpu0.sock"))
	t.Setenv("FLUX_NO_FLEET_ADOPT", "1")
	if got := New(cfg).name; got != "flux" {
		t.Fatalf("opt-out ignored, got %q", got)
	}
}
