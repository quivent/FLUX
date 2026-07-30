package server

import (
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"testing"
)

// TestFrontendEndpointsAreRegistered enforces the frontend<->backend contract
// that nothing else in this repo checks.
//
// The Motion Atlas assets are served from DISK, not embedded in the binary, so
// the shipped HTML/JS and the running server version independently. That skew
// is invisible until a browser tries it: a page can call an endpoint the
// binary does not register, and the only symptom is a feed that silently never
// connects. It happened -- the SSE-to-WebSocket migration left seven pages
// pointing at endpoints a stale binary did not serve, and `go build`, `go vet`,
// and every unit test stayed green through all of it.
//
// This test reads the same files the server hands to browsers and asserts that
// every /api path they reference is actually routed.
func TestFrontendEndpointsAreRegistered(t *testing.T) {
	root := repoRoot(t)
	serverGo := filepath.Join(root, "internal", "server", "server.go")

	registered := registeredRoutes(t, serverGo)
	if len(registered) == 0 {
		t.Fatal("parsed zero routes from server.go -- the parser broke, not the routes")
	}

	assets := servedAssets(t, serverGo)
	atlas := filepath.Join(root, "web", "motion-atlas")

	refs := frontendEndpointRefs(t, atlas, assets)
	if len(refs) == 0 {
		t.Fatal("found zero /api references in the front end -- the scanner broke")
	}

	socketPaths := frontendSocketPaths(t, atlas, assets)
	if len(socketPaths) == 0 {
		t.Fatal("found zero WebSocket paths in the front end -- the scanner broke, " +
			"and every socket endpoint just silently dropped out of enforcement")
	}

	for _, path := range sortedKeys(refs) {
		where := strings.Join(refs[path], ", ")

		// WebSocket endpoints must match EXACTLY. A prefix check is worse than
		// useless here: "/api/jobs/" is registered as a subtree, so ServeMux
		// happily routes /api/jobs/ws, /api/jobs/socket, and a typo'd
		// /api/jobs/wsDISABLED to the plain JSON jobs handler. The handshake
		// then fails with a 200 full of JSON and the browser reports only that
		// the socket closed. "Routed" is the wrong question; "routed to the
		// upgrade handler" is the right one.
		//
		// Membership is decided by how the path is USED (fed to a socket URL),
		// not by a "/ws" suffix -- otherwise renaming the front end to
		// /api/jobs/socket silently drops out of enforcement, which is the one
		// mutation an earlier version of this test failed to catch.
		if socketPaths[path] {
			if !registered[path] {
				t.Errorf("front end opens a WebSocket to %s but there is no EXACT route for it\n"+
					"  referenced by: %s\n"+
					"  (a subtree pattern does not count -- it would swallow the upgrade and serve JSON)",
					path, where)
			}
			continue
		}

		if !routedOrSubtree(path, registered) {
			t.Errorf("front end calls %s but no route is registered for it\n  referenced by: %s",
				path, where)
		}
	}
}

// routedOrSubtree mirrors http.ServeMux matching for ordinary REST paths, where
// a subtree pattern genuinely is the intended registration (/api/jobs/{id}).
func routedOrSubtree(path string, registered map[string]bool) bool {
	if registered[path] {
		return true
	}
	for pattern := range registered {
		if strings.HasSuffix(pattern, "/") && strings.HasPrefix(path, pattern) {
			return true
		}
	}
	return false
}

var (
	muxPattern = regexp.MustCompile(`mux\.Handle(?:Func)?\("([^"]+)"`)
	allowEntry = regexp.MustCompile(`"([^"]+)":\s*true`)
)

func registeredRoutes(t *testing.T, serverGo string) map[string]bool {
	t.Helper()
	src, err := os.ReadFile(serverGo)
	if err != nil {
		t.Fatalf("read %s: %v", serverGo, err)
	}
	out := map[string]bool{}
	for _, m := range muxPattern.FindAllStringSubmatch(string(src), -1) {
		out[m[1]] = true
	}
	return out
}

// apiRef matches a quoted /api/... literal. Templated paths (containing ${})
// are skipped: they are assembled at runtime and cannot be checked statically.
var apiRef = regexp.MustCompile(`["' ` + "`" + `](/api/[A-Za-z0-9_\-/]*)["' ` + "`" + `]`)

// servedAssets returns the filenames motionAtlas will actually hand to a
// browser, parsed from its own allow-list. Scoping to this set matters in both
// directions: files the server refuses to serve (web/motion-atlas/js/jobs.js,
// templates/base.html -- both 404) are dead and must not fail the build, and
// any file ADDED to the allow-list later comes under enforcement automatically
// without anyone remembering to update this test.
func servedAssets(t *testing.T, serverGo string) map[string]bool {
	t.Helper()
	src, err := os.ReadFile(serverGo)
	if err != nil {
		t.Fatalf("read %s: %v", serverGo, err)
	}
	start := strings.Index(string(src), "allowed := map[string]bool{")
	if start < 0 {
		t.Fatal("could not find the motionAtlas allow-list in server.go -- if it moved, fix this parser rather than deleting the check")
	}
	block := string(src)[start:]
	if end := strings.Index(block, "\n\t}"); end > 0 {
		block = block[:end]
	}
	out := map[string]bool{}
	for _, entry := range allowEntry.FindAllStringSubmatch(block, -1) {
		out[entry[1]] = true
	}
	if len(out) == 0 {
		t.Fatal("parsed zero served assets from the allow-list -- the parser broke")
	}
	return out
}

// socketPathUse matches the two ways this front end builds a socket URL:
// passing the path to the connectSocket helper, or concatenating it onto
// location.host inline. Deriving the set from real usage means a path stays
// under exact-match enforcement even if it is renamed away from "/ws".
var socketPathUse = regexp.MustCompile(
	`(?:connectSocket\(|location\.host\s*\+\s*)["'` + "`" + `](/api/[A-Za-z0-9_\-/]*)["'` + "`" + `]`)

func frontendSocketPaths(t *testing.T, dir string, served map[string]bool) map[string]bool {
	t.Helper()
	out := map[string]bool{}
	for name := range served {
		if ext := filepath.Ext(name); ext != ".js" && ext != ".html" {
			continue
		}
		body, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("the server serves %s but it could not be read: %v", name, err)
		}
		for _, m := range socketPathUse.FindAllStringSubmatch(string(body), -1) {
			out[m[1]] = true
		}
	}
	return out
}

func frontendEndpointRefs(t *testing.T, dir string, served map[string]bool) map[string][]string {
	t.Helper()
	refs := map[string][]string{}
	for name := range served {
		if ext := filepath.Ext(name); ext != ".js" && ext != ".html" {
			continue
		}
		body, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("the server serves %s but it could not be read: %v", name, err)
		}
		for _, m := range apiRef.FindAllStringSubmatch(string(body), -1) {
			endpoint := m[1]
			if strings.Contains(endpoint, "${") {
				continue
			}
			if !contains(refs[endpoint], name) {
				refs[endpoint] = append(refs[endpoint], name)
			}
		}
	}
	return refs
}

// TestNoStaleEventSourceReferences pins the migration itself. The sigil snippet
// was duplicated byte-for-byte across eight pages, and a sweep that only looked
// at *.js missed every one of them. If someone reintroduces an EventSource for
// a feed that now has a WebSocket, this fails loudly rather than leaving one
// page quietly on the old transport.
func TestNoStaleEventSourceReferences(t *testing.T) {
	root := repoRoot(t)
	dir := filepath.Join(root, "web", "motion-atlas")
	served := servedAssets(t, filepath.Join(root, "internal", "server", "server.go"))

	var offenders []string
	for name := range served {
		if ext := filepath.Ext(name); ext != ".js" && ext != ".html" {
			continue
		}
		body, err := os.ReadFile(filepath.Join(dir, name))
		if err != nil {
			t.Fatalf("the server serves %s but it could not be read: %v", name, err)
		}
		if strings.Contains(string(body), "/api/jobs/events") {
			offenders = append(offenders, name)
		}
	}
	if len(offenders) > 0 {
		sort.Strings(offenders)
		t.Errorf("these front-end files still use the old SSE jobs feed instead of /api/jobs/ws:\n  %s",
			strings.Join(offenders, "\n  "))
	}
}

func repoRoot(t *testing.T) string {
	t.Helper()
	// internal/server -> repo root
	root, err := filepath.Abs(filepath.Join("..", ".."))
	if err != nil {
		t.Fatalf("resolve repo root: %v", err)
	}
	return root
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}

func sortedKeys(m map[string][]string) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
