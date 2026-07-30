package server

import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// TestWebSocketAcceptKey pins the handshake against RFC 6455's own worked
// example (§1.3) -- if the accept-key computation ever regresses, every
// browser WebSocket() call fails the handshake silently (the browser just
// never fires onopen), which is a bad thing to discover live.
func TestWebSocketAcceptKey(t *testing.T) {
	const key = "dGhlIHNhbXBsZSBub25jZQ=="
	const want = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
	sum := sha1.Sum([]byte(key + wsGUID))
	got := base64.StdEncoding.EncodeToString(sum[:])
	if got != want {
		t.Fatalf("accept key = %q, want %q", got, want)
	}
}

func TestWriteTextFrameHeader(t *testing.T) {
	cases := []struct {
		name       string
		size       int
		wantHeader []byte
	}{
		{"small", 10, []byte{0x81, 10}},
		{"boundary125", 125, []byte{0x81, 125}},
		{"medium126", 126, append([]byte{0x81, 126}, encodeUint16(126)...)},
		{"medium65535", 65535, append([]byte{0x81, 126}, encodeUint16(65535)...)},
		{"large65536", 65536, append([]byte{0x81, 127}, encodeUint64(65536)...)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server, client := net.Pipe()
			defer server.Close()
			defer client.Close()
			conn := &wsConn{conn: server, rw: bufio.NewReadWriter(bufio.NewReader(server), bufio.NewWriter(server))}
			data := make([]byte, tc.size)
			done := make(chan error, 1)
			go func() { done <- conn.writeText(data) }()
			got := make([]byte, len(tc.wantHeader))
			if _, err := readFull(client, got); err != nil {
				t.Fatalf("read header: %v", err)
			}
			if string(got) != string(tc.wantHeader) {
				t.Fatalf("header = %v, want %v", got, tc.wantHeader)
			}
			payload := make([]byte, tc.size)
			if _, err := readFull(client, payload); err != nil {
				t.Fatalf("read payload: %v", err)
			}
			if err := <-done; err != nil {
				t.Fatalf("writeText: %v", err)
			}
		})
	}
}

func TestWritePingFrame(t *testing.T) {
	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	conn := &wsConn{conn: server, rw: bufio.NewReadWriter(bufio.NewReader(server), bufio.NewWriter(server))}
	done := make(chan error, 1)
	go func() { done <- conn.writePing() }()
	got := make([]byte, 2)
	if _, err := readFull(client, got); err != nil {
		t.Fatalf("read ping: %v", err)
	}
	if got[0] != 0x89 || got[1] != 0x00 {
		t.Fatalf("ping frame = %v, want [0x89 0x00]", got)
	}
	if err := <-done; err != nil {
		t.Fatalf("writePing: %v", err)
	}
}

// TestUpgradeWebSocketLiveHandshake exercises the real integration path --
// an actual net/http server, an actual TCP handshake -- rather than just
// unit-testing the accept-key math in isolation.
func TestUpgradeWebSocketLiveHandshake(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgradeWebSocket(w, r)
		if err != nil {
			t.Errorf("upgradeWebSocket: %v", err)
			return
		}
		defer conn.Close()
		conn.writeText([]byte("hello"))
	}))
	defer srv.Close()

	addr := strings.TrimPrefix(srv.URL, "http://")
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()

	req := "GET / HTTP/1.1\r\n" +
		"Host: " + addr + "\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
		"Sec-WebSocket-Version: 13\r\n\r\n"
	if _, err := conn.Write([]byte(req)); err != nil {
		t.Fatalf("write request: %v", err)
	}

	// Read until BOTH the handshake and the frame have arrived. They are not
	// guaranteed to land in one Read: TCP is a stream, and whether the 101
	// response and the following frame coalesce into a single segment is a
	// kernel/timing detail. Asserting on one Read passes by luck and fails
	// intermittently -- which is exactly what it did.
	conn.SetReadDeadline(time.Now().Add(5 * time.Second))
	var resp string
	buf := make([]byte, 4096)
	for !strings.Contains(resp, "hello") {
		n, err := conn.Read(buf)
		if n > 0 {
			resp += string(buf[:n])
		}
		if err != nil {
			t.Fatalf("read response (got so far %q): %v", resp, err)
		}
	}
	if !strings.Contains(resp, "101 Switching Protocols") {
		t.Fatalf("expected 101 response, got: %q", resp)
	}
	if !strings.Contains(resp, "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=") {
		t.Fatalf("unexpected/missing accept key in response: %q", resp)
	}
}

// TestWriteDeadlineUnblocksStalledClient proves a client that stops reading
// cannot pin the handler goroutine forever. net.Pipe is unbuffered, so a write
// blocks until someone reads -- an exact stand-in for a peer whose receive
// window has filled (suspended laptop, stalled tab).
//
// Before the write deadline existed this blocked indefinitely: the read
// deadline does not interrupt a blocked write, so the handler never returned
// to its select, never saw `done` close, and never ran its deferred
// conn.Close() -- leaking the goroutine, the socket, and the hub subscription.
func TestWriteDeadlineUnblocksStalledClient(t *testing.T) {
	restore := wsWriteTimeout
	wsWriteTimeout = 200 * time.Millisecond
	defer func() { wsWriteTimeout = restore }()

	server, client := net.Pipe()
	defer server.Close()
	defer client.Close()
	// client deliberately never reads.

	conn := &wsConn{conn: server, rw: bufio.NewReadWriter(bufio.NewReader(server), bufio.NewWriter(server))}

	done := make(chan error, 1)
	go func() { done <- conn.writeText([]byte(`{"jobs":[]}`)) }()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("expected a write error against a non-reading client, got nil")
		}
		var netErr net.Error
		if !errors.As(err, &netErr) || !netErr.Timeout() {
			t.Fatalf("expected a timeout error, got %v", err)
		}
	case <-time.After(wsWriteTimeout + 5*time.Second):
		t.Fatal("writeText never returned: a stalled client can pin the handler goroutine forever")
	}
}

// TestOriginAllowed guards the cross-origin protection that WebSockets do NOT
// inherit from CORS. If this regresses, any page the user visits can read
// their render jobs off localhost -- and nothing else in the stack catches it,
// because the browser never blocks a WS handshake the way it blocks EventSource.
func TestOriginAllowed(t *testing.T) {
	cases := []struct {
		name    string
		origin  string
		host    string
		allowed bool
	}{
		{"no origin (CLI/curl/python)", "", "127.0.0.1:7861", true},
		{"same origin", "http://127.0.0.1:7861", "127.0.0.1:7861", true},
		{"localhost alias", "http://localhost:7861", "127.0.0.1:7861", true},
		{"vite dev server, other local port", "http://localhost:5173", "127.0.0.1:7861", true},
		{"ipv6 loopback", "http://[::1]:5173", "127.0.0.1:7861", true},
		{"same origin behind a domain", "https://code.influx.vision", "code.influx.vision", true},
		{"foreign origin", "https://evil.example", "127.0.0.1:7861", false},
		{"foreign origin with port", "http://attacker.test:8080", "127.0.0.1:7861", false},
		{"sandboxed iframe null origin", "null", "127.0.0.1:7861", false},
		{"lookalike subdomain", "https://127.0.0.1.evil.example", "127.0.0.1:7861", false},
		{"different domain than host", "https://other.example", "code.influx.vision", false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest(http.MethodGet, "/api/jobs/ws", nil)
			r.Host = tc.host
			if tc.origin != "" {
				r.Header.Set("Origin", tc.origin)
			}
			if got := originAllowed(r); got != tc.allowed {
				t.Fatalf("originAllowed(origin=%q, host=%q) = %v, want %v",
					tc.origin, tc.host, got, tc.allowed)
			}
		})
	}
}

// TestUpgradeRefusesForeignOrigin proves the check actually gates the upgrade
// end to end, not just the predicate in isolation.
func TestUpgradeRefusesForeignOrigin(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := upgradeWebSocket(w, r)
		if err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		defer conn.Close()
		conn.writeText([]byte(`{"secret":"render-job-data"}`))
	}))
	defer srv.Close()

	addr := strings.TrimPrefix(srv.URL, "http://")
	conn, err := net.Dial("tcp", addr)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	defer conn.Close()
	req := "GET / HTTP/1.1\r\nHost: " + addr + "\r\n" +
		"Upgrade: websocket\r\nConnection: Upgrade\r\n" +
		"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n" +
		"Sec-WebSocket-Version: 13\r\n" +
		"Origin: https://evil.example\r\n\r\n"
	if _, err := conn.Write([]byte(req)); err != nil {
		t.Fatalf("write: %v", err)
	}
	conn.SetReadDeadline(time.Now().Add(2 * time.Second))
	buf := make([]byte, 4096)
	n, _ := conn.Read(buf)
	resp := string(buf[:n])
	if strings.Contains(resp, "101") {
		t.Fatalf("foreign origin was upgraded, expected refusal: %q", resp)
	}
	if strings.Contains(resp, "render-job-data") {
		t.Fatalf("payload leaked to foreign origin: %q", resp)
	}
}

func encodeUint16(v uint16) []byte {
	b := make([]byte, 2)
	binary.BigEndian.PutUint16(b, v)
	return b
}

func encodeUint64(v uint64) []byte {
	b := make([]byte, 8)
	binary.BigEndian.PutUint64(b, v)
	return b
}

func readFull(conn net.Conn, buf []byte) (int, error) {
	total := 0
	for total < len(buf) {
		n, err := conn.Read(buf[total:])
		total += n
		if err != nil {
			return total, err
		}
	}
	return total, nil
}
