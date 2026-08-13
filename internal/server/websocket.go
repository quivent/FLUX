package server

import (
	"bufio"
	"crypto/sha1"
	"encoding/base64"
	"encoding/binary"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const wsGUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

// wsPingInterval/wsPongTimeout back the dead-connection reaper: the server
// pings every wsPingInterval, and the read deadline (refreshed on ANY
// inbound frame, including the browser's automatic pong reply) is set to
// wsPongTimeout. A silently-dropped link -- cable pulled, VPN blip, no
// FIN/RST ever arrives -- produces zero bytes in either direction, so
// without this the connection and its hub subscription would leak forever.
// Vars, not consts, purely so tests can shorten them -- otherwise covering the
// reaper honestly would mean a 45-second test, and a test that slow is a test
// that eventually gets skipped.
var (
	wsPingInterval = 20 * time.Second
	wsPongTimeout  = 45 * time.Second
	// wsMaxClientFrame caps a claimed client frame length. These endpoints
	// are push-only; the browser never sends anything but automatic
	// pong/close frames (a few bytes). A client claiming a huge length
	// would otherwise just park this connection's read loop indefinitely
	// waiting on bytes that may never arrive.
	wsMaxClientFrame int64 = 64 * 1024
	// wsWriteTimeout bounds a single frame write. Without it, a client that
	// stops READING (suspended laptop, stalled tab, zero receive window)
	// blocks the handler goroutine inside Write indefinitely -- and a read
	// deadline cannot interrupt a blocked write, so wsPongTimeout does NOT
	// rescue this case. The handler would never reach its select again, so
	// `done` closing would go unnoticed and the deferred conn.Close() would
	// never run: goroutine, socket, and hub subscription all leaked for as
	// long as the process lives.
	wsWriteTimeout = 10 * time.Second
)

// wsConn is a minimal RFC 6455 server-side WebSocket connection: handshake,
// unmasked text-frame writes, and ping frames. Every caller in this file
// only ever pushes JSON progress snapshots one-directionally, so a full
// client implementation (masking on write, fragmentation) would be unused
// weight -- this is deliberately the subset that's actually used.
type wsConn struct {
	conn net.Conn
	rw   *bufio.ReadWriter
}

// originAllowed re-establishes the cross-origin read protection that the SSE
// endpoints got for free and that WebSockets do NOT get.
//
// Browsers apply CORS to EventSource but NOT to the WebSocket handshake: a
// page on any origin may open ws:// to any host the user can reach, including
// 127.0.0.1, and read everything the server sends. Without this check, moving
// a feed from SSE to WS silently converts a cross-origin-BLOCKED endpoint into
// a cross-origin-READABLE one -- any site the user visits could siphon render
// jobs, prompts, and output paths off their local dashboard (CSWSH).
//
// Policy deliberately mirrors localOrigin()'s existing SSE behavior:
//   - no Origin header: allow. Non-browser callers (the flux CLI, curl, python)
//     send none, and they carry no ambient credentials to abuse.
//   - localhost family: allow, so a vite dev server on another local port keeps
//     working against the API, exactly as it does over SSE today.
//   - same origin as the request Host: allow, so a real deployment behind a
//     domain still works.
//   - anything else: refuse before hijacking.
func originAllowed(r *http.Request) bool {
	origin := strings.TrimSpace(r.Header.Get("Origin"))
	if origin == "" {
		return true
	}
	parsed, err := url.Parse(origin)
	if err != nil {
		return false
	}
	switch parsed.Hostname() {
	case "localhost", "127.0.0.1", "::1":
		return true
	}
	// Some capability gateways intentionally discard the browser-facing Host
	// before the request reaches the workload. Those deployments must declare
	// their public origins explicitly rather than weakening the check globally.
	for _, allowed := range strings.Split(os.Getenv("FLUX_WS_ORIGINS"), ",") {
		if strings.TrimRight(strings.TrimSpace(allowed), "/") == strings.TrimRight(origin, "/") {
			return true
		}
	}
	if parsed.Host == r.Host {
		return true
	}
	// Reverse proxies terminate TLS and commonly replace Host with the private
	// upstream capability while preserving the browser-facing authority in
	// X-Forwarded-Host. Without honoring that authority, a legitimate gallery
	// opened at tea.influx.vision is rejected as cross-origin even though the
	// browser is connecting back to the page's own origin.
	forwardedHost := strings.TrimSpace(strings.Split(r.Header.Get("X-Forwarded-Host"), ",")[0])
	return forwardedHost != "" && parsed.Host == forwardedHost
}

func upgradeWebSocket(w http.ResponseWriter, r *http.Request) (*wsConn, error) {
	if !strings.EqualFold(r.Header.Get("Upgrade"), "websocket") {
		return nil, fmt.Errorf("expected a websocket upgrade request")
	}
	if !originAllowed(r) {
		return nil, fmt.Errorf("websocket origin %q not allowed", r.Header.Get("Origin"))
	}
	key := r.Header.Get("Sec-WebSocket-Key")
	if key == "" {
		return nil, fmt.Errorf("missing Sec-WebSocket-Key")
	}
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		return nil, fmt.Errorf("connection does not support hijacking")
	}
	conn, rw, err := hijacker.Hijack()
	if err != nil {
		return nil, err
	}
	sum := sha1.Sum([]byte(key + wsGUID))
	accept := base64.StdEncoding.EncodeToString(sum[:])
	resp := "HTTP/1.1 101 Switching Protocols\r\n" +
		"Upgrade: websocket\r\n" +
		"Connection: Upgrade\r\n" +
		"Sec-WebSocket-Accept: " + accept + "\r\n\r\n"
	if _, err := rw.WriteString(resp); err != nil {
		conn.Close()
		return nil, err
	}
	if err := rw.Flush(); err != nil {
		conn.Close()
		return nil, err
	}
	conn.SetReadDeadline(time.Now().Add(wsPongTimeout))
	return &wsConn{conn: conn, rw: rw}, nil
}

func (c *wsConn) Close() error {
	return c.conn.Close()
}

// writeText sends one unmasked, unfragmented text frame -- server-to-client
// frames must not be masked per RFC 6455 §5.1.
func (c *wsConn) writeText(data []byte) error {
	c.conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
	var header []byte
	length := len(data)
	switch {
	case length <= 125:
		header = []byte{0x81, byte(length)}
	case length <= 0xFFFF:
		header = make([]byte, 4)
		header[0], header[1] = 0x81, 126
		binary.BigEndian.PutUint16(header[2:], uint16(length))
	default:
		header = make([]byte, 10)
		header[0], header[1] = 0x81, 127
		binary.BigEndian.PutUint64(header[2:], uint64(length))
	}
	if _, err := c.rw.Write(header); err != nil {
		return err
	}
	if _, err := c.rw.Write(data); err != nil {
		return err
	}
	return c.rw.Flush()
}

// writePing sends a zero-length ping frame (opcode 0x9). Browsers answer
// automatically with a pong at the WebSocket protocol layer -- no page JS
// involved -- so this alone is enough to keep wsPongTimeout refreshed on a
// live connection and let it expire on a dead one.
func (c *wsConn) writePing() error {
	c.conn.SetWriteDeadline(time.Now().Add(wsWriteTimeout))
	if _, err := c.rw.Write([]byte{0x89, 0x00}); err != nil {
		return err
	}
	return c.rw.Flush()
}

// readLoop blocks reading and discarding client frames purely to detect
// disconnect (close frame, reset, EOF): these endpoints are push-only, so
// frame payloads carry no meaning, but a hijacked connection's lifetime
// isn't tied to the request context, so this is the only way to notice the
// browser went away.
func (c *wsConn) readLoop() {
	for {
		header := make([]byte, 2)
		if _, err := io.ReadFull(c.rw, header); err != nil {
			return
		}
		// Any successfully read frame -- including the browser's automatic
		// pong reply to our ping -- proves the link is alive; push the
		// deadline back out.
		c.conn.SetReadDeadline(time.Now().Add(wsPongTimeout))
		opcode := header[0] & 0x0F
		masked := header[1]&0x80 != 0
		length := int64(header[1] & 0x7F)
		switch length {
		case 126:
			ext := make([]byte, 2)
			if _, err := io.ReadFull(c.rw, ext); err != nil {
				return
			}
			length = int64(binary.BigEndian.Uint16(ext))
		case 127:
			ext := make([]byte, 8)
			if _, err := io.ReadFull(c.rw, ext); err != nil {
				return
			}
			length = int64(binary.BigEndian.Uint64(ext))
		}
		if length > wsMaxClientFrame {
			return
		}
		if masked {
			var maskKey [4]byte
			if _, err := io.ReadFull(c.rw, maskKey[:]); err != nil {
				return
			}
		}
		if length > 0 {
			if _, err := io.CopyN(io.Discard, c.rw, length); err != nil {
				return
			}
		}
		if opcode == 0x8 { // close
			return
		}
	}
}
