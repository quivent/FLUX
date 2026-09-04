package server

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

type teaDaemon struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Role     string `json:"role"`
	Kind     string `json:"kind"`
	Bind     string `json:"bind"`
	Required  bool   `json:"required"`
	Protected bool   `json:"protected"`
	Live      bool   `json:"live"`
	PID       int    `json:"pid,omitempty"`
	Detail    string `json:"detail,omitempty"`
	Watch     bool   `json:"watch,omitempty"`
}

type teaDaemonEvent struct {
	At     time.Time `json:"at"`
	Daemon string    `json:"daemon"`
	From   string    `json:"from"`
	To     string    `json:"to"`
	Note   string    `json:"note"`
}

type teaDaemonsSnapshot struct {
	Schema    string           `json:"schema"`
	UpdatedAt time.Time        `json:"updated_at"`
	Summary   map[string]int   `json:"summary"`
	Sentinel  map[string]any   `json:"sentinel"`
	Daemons   []teaDaemon      `json:"daemons"`
	Events    []teaDaemonEvent `json:"events"`
	Law       map[string]any   `json:"law"`
}

type daemonSpec struct {
	ID, Name, Role, Kind, Bind, TCP, Sock, Proc string
	Required, Protected                         bool
}

var teaDaemonSpecs = []daemonSpec{
	{ID: "sentinel", Name: "Sentinel", Role: "Watches every other daemon. This is the eye.", Kind: "watch", Bind: "in-process", Required: true},
	{ID: "tea", Name: "Tea", Role: "Parchment HTTP — this site.", Kind: "http", Bind: "0.0.0.0:7861", TCP: "127.0.0.1:7861", Required: true, Proc: "tea serve"},
	{ID: "governor-gateway", Name: "Governor gateway", Role: "Agentic loop. Dual-pass, shards, doctrine. Protected. His tools ride here.", Kind: "gateway", Bind: "127.0.0.1:8800", TCP: "127.0.0.1:8800", Required: true, Protected: true, Proc: "governor gateway serve"},
	{ID: "governor-engine", Name: "Governor engine", Role: "Gemma 4 31B on GPU 1. Protected. Do not break. Do not swap under him.", Kind: "engine", Bind: "127.0.0.1:8000", TCP: "127.0.0.1:8000", Required: true, Protected: true, Proc: "served-model-name governor"},
	{ID: "flux-gpu0", Name: "FLUX GPU 0", Role: "BF16 generator. Arcane / microgreens.", Kind: "worker", Bind: "uds:.fluxd/flux-gpu0.sock", Sock: ".fluxd/flux-gpu0.sock", Required: true, Proc: "flux-gpu0.sock"},
	{ID: "flux-gpu3", Name: "FLUX GPU 3", Role: "FP8 generator. Fashion wall.", Kind: "worker", Bind: "uds:.fluxd/flux-gpu3.sock", Sock: ".fluxd/flux-gpu3.sock", Required: true, Proc: "flux-gpu3.sock"},
	{ID: "protocol-stream", Name: "Protocol stream", Role: "Perpetual stills into the collection.", Kind: "stream", Bind: "gpu0 worker", Required: false, Proc: "protocol_stream.py"},
	{ID: "hive-research", Name: "Qwen", Role: "Reason. GPU 2. Protected sounding board for thought, not a second Governor.", Kind: "engine", Bind: "127.0.0.1:8002", TCP: "127.0.0.1:8002", Required: true, Protected: true, Proc: "served-model-name hive-research"},
	{ID: "pixtral", Name: "Pixtral", Role: "Beauty critic. GPU 3.", Kind: "engine", Bind: "127.0.0.1:8004", TCP: "127.0.0.1:8004", Required: true, Proc: "served-model-name pixtral"},
	{ID: "drafter", Name: "Drafter", Role: "Gemma 12B decoder + MTP.", Kind: "engine", Bind: "127.0.0.1:8003", TCP: "127.0.0.1:8003", Required: false, Proc: "served-model-name drafter"},
	{ID: "visionary", Name: "Visionary", Role: "Sensory witness. Often standby.", Kind: "engine", Bind: "127.0.0.1:8001", TCP: "127.0.0.1:8001", Required: false},
	{ID: "atelier", Name: "Atelier", Role: "Comfort / render UI.", Kind: "http", Bind: "127.0.0.1:9732", TCP: "127.0.0.1:9732", Required: false},
	{ID: "hive", Name: "Quantum Hive", Role: "Hive control surface.", Kind: "http", Bind: "127.0.0.1:7890", TCP: "127.0.0.1:7890", Required: false},
	{ID: "flash", Name: "Flash", Role: "Dispatcher.", Kind: "http", Bind: "127.0.0.1:7840", TCP: "127.0.0.1:7840", Required: false},
	{ID: "waveworkers", Name: "Waveworkers", Role: "Audio workers.", Kind: "http", Bind: "127.0.0.1:7889", TCP: "127.0.0.1:7889", Required: false},
	{ID: "music", Name: "Music Lab", Role: "Music surface.", Kind: "http", Bind: "127.0.0.1:4174", TCP: "127.0.0.1:4174", Required: false},
	{ID: "code", Name: "Governor code", Role: "His tools. Coding daemon with suture reach. Autonomy needs this live.", Kind: "daemon", Bind: "127.0.0.1:47895", TCP: "127.0.0.1:47895", Required: true, Protected: true, Proc: "daemons code"},
}

var teaSentinel = struct {
	sync.Mutex
	running bool
	tick    time.Time
	prev    map[string]bool
	events  []teaDaemonEvent
}{prev: map[string]bool{}}

func (s Server) daemonsPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	path := strings.TrimSuffix(r.URL.Path, "/")
	if r.URL.Path == "/daemons/" {
		http.Redirect(w, r, "/daemons", http.StatusPermanentRedirect)
		return
	}
	if path != "/daemons" && path != "/daemons/characters" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "daemons.html"))
}

func (s Server) teaDaemonsAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	writeJSON(w, http.StatusOK, s.teaDaemonsSnapshot())
}

func (s Server) teaDaemonsEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "event streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	tick := time.NewTicker(2 * time.Second)
	defer tick.Stop()
	send := func() {
		raw, err := json.Marshal(s.teaDaemonsSnapshot())
		if err != nil {
			return
		}
		_, _ = w.Write([]byte("event: daemons\ndata: "))
		_, _ = w.Write(raw)
		_, _ = w.Write([]byte("\n\n"))
		flusher.Flush()
	}
	send()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-tick.C:
			send()
		}
	}
}

func (s Server) runTeaSentinel(ctx context.Context) {
	teaSentinel.Lock()
	teaSentinel.running = true
	teaSentinel.Unlock()
	s.noteDaemonFlips(s.probeTeaDaemons())
	tick := time.NewTicker(2 * time.Second)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
			s.noteDaemonFlips(s.probeTeaDaemons())
		}
	}
}

func (s Server) teaDaemonsSnapshot() teaDaemonsSnapshot {
	daemons := append(s.probeTeaDaemons(), s.characterDaemons()...)
	up, down, alerts := 0, 0, 0
	for _, d := range daemons {
		if d.Live {
			up++
		} else {
			down++
		}
		if (d.Required || d.Protected) && !d.Live {
			alerts++
		}
	}
	teaSentinel.Lock()
	events := append([]teaDaemonEvent(nil), teaSentinel.events...)
	running := teaSentinel.running
	tick := teaSentinel.tick
	teaSentinel.Unlock()
	if tick.IsZero() {
		tick = time.Now().UTC()
	}
	return teaDaemonsSnapshot{
		Schema:    "tea.daemons.v1",
		UpdatedAt: time.Now().UTC(),
		Summary:   map[string]int{"up": up, "down": down, "total": len(daemons), "alerts": alerts},
		Sentinel: map[string]any{
			"id": "sentinel", "running": running, "watching": len(daemons) - 1,
			"last_tick": tick, "alerts": alerts, "law": teaGovernorLaw()["line"],
		},
		Daemons: daemons,
		Events:  events,
		Law:     teaGovernorLaw(),
	}
}

func (s Server) probeTeaDaemons() []teaDaemon {
	out := make([]teaDaemon, 0, len(teaDaemonSpecs))
	for _, spec := range teaDaemonSpecs {
		d := teaDaemon{
			ID: spec.ID, Name: spec.Name, Role: spec.Role, Kind: spec.Kind,
			Bind: spec.Bind, Required: spec.Required, Protected: spec.Protected,
			Watch: spec.ID == "sentinel",
		}
		switch spec.ID {
		case "sentinel":
			d.Live = true
			d.Detail = "watching the roster"
			d.PID = os.Getpid()
		default:
			if spec.TCP != "" && tcpAlive(spec.TCP, 180*time.Millisecond) {
				d.Live = true
			}
			if spec.Sock != "" {
				sock := spec.Sock
				if !filepath.IsAbs(sock) {
					sock = filepath.Join(s.cfg.Root, sock)
				}
				if unixAlive(sock, 180*time.Millisecond) {
					d.Live = true
					d.Bind = "uds:" + spec.Sock
				}
			}
			if spec.Proc != "" {
				if pid, ok := procHas(spec.Proc); ok {
					d.PID = pid
					if spec.TCP == "" && spec.Sock == "" {
						d.Live = true
					}
				}
			}
			if d.Live && d.Detail == "" {
				d.Detail = "answering"
			}
			if !d.Live {
				d.Detail = "silent"
			}
		}
		out = append(out, d)
	}
	return out
}

func (s Server) noteDaemonFlips(now []teaDaemon) {
	teaSentinel.Lock()
	defer teaSentinel.Unlock()
	teaSentinel.tick = time.Now().UTC()
	for _, d := range now {
		was, seen := teaSentinel.prev[d.ID]
		teaSentinel.prev[d.ID] = d.Live
		if !seen {
			continue
		}
		if was == d.Live {
			continue
		}
		from, to := "up", "down"
		if d.Live {
			from, to = "down", "up"
		}
		note := d.Name + " went " + to
		if d.Required && !d.Live {
			note = "required " + d.Name + " went silent"
		}
		teaSentinel.events = append(teaSentinel.events, teaDaemonEvent{
			At: time.Now().UTC(), Daemon: d.ID, From: from, To: to, Note: note,
		})
		if len(teaSentinel.events) > 48 {
			teaSentinel.events = teaSentinel.events[len(teaSentinel.events)-48:]
		}
	}
}

func unixAlive(path string, timeout time.Duration) bool {
	conn, err := net.DialTimeout("unix", path, timeout)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func procHas(substr string) (int, bool) {
	entries, err := os.ReadDir("/proc")
	if err != nil {
		return 0, false
	}
	for _, e := range entries {
		if !e.IsDir() {
			continue
		}
		pid, err := strconv.Atoi(e.Name())
		if err != nil {
			continue
		}
		raw, err := os.ReadFile("/proc/" + e.Name() + "/cmdline")
		if err != nil || len(raw) == 0 {
			continue
		}
		cmd := strings.ReplaceAll(string(raw), "\x00", " ")
		if strings.Contains(cmd, substr) {
			return pid, true
		}
	}
	return 0, false
}
