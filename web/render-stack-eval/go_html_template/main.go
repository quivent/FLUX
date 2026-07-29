// Command go_html_template serves the real motion-atlas directory exactly
// as the production FLUX server does — everything (pages, CSS, JS) under
// one shared /motion-atlas/ prefix, since they're siblings in the same
// directory and reference each other with relative paths. Not wired into
// FLUX's production server.
//
// /events uses ONE shared 200ms ticker fanning out to every connection via
// a hub, instead of each connection running its own independent
// time.NewTicker — the same "one source, fan out to N" fix applied to
// FLUX's real telemetry SSE endpoints (server.go), which had N independent
// nvidia-smi subprocesses instead of one shared poller.
package main

import (
	"fmt"
	"log"
	"net/http"
	"sync"
	"time"
)

// staticDir is the REAL motion-atlas directory — served directly, nothing
// copied.
const staticDir = "/Users/jay/FLUX/web/motion-atlas"

const listenAddr = "127.0.0.1:9202"

var heartbeatHub = struct {
	sync.Mutex
	clients map[chan string]struct{}
}{clients: make(map[chan string]struct{})}

func runHeartbeatHub() {
	ticker := time.NewTicker(200 * time.Millisecond)
	defer ticker.Stop()
	var seq int64
	for range ticker.C {
		seq++
		payload := fmt.Sprintf(`{"seq":%d,"ts":%d,"event":"heartbeat"}`, seq, time.Now().UnixNano())
		heartbeatHub.Lock()
		for client := range heartbeatHub.clients {
			select {
			case client <- payload:
			default:
			}
		}
		heartbeatHub.Unlock()
	}
}

func main() {
	go runHeartbeatHub()

	mux := http.NewServeMux()

	fileServer := http.FileServer(http.Dir(staticDir))
	mux.Handle("GET /motion-atlas/", http.StripPrefix("/motion-atlas/", noStore(fileServer)))
	mux.HandleFunc("GET /", func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, "/motion-atlas/", http.StatusFound)
	})

	mux.HandleFunc("GET /events", eventsHandler)
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		fmt.Fprint(w, `{"service":"go-html-template-comparison","status":"ok"}`)
	})

	log.Printf("listening on http://%s", listenAddr)
	if err := http.ListenAndServe(listenAddr, mux); err != nil {
		log.Fatal(err)
	}
}

func noStore(h http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		h.ServeHTTP(w, r)
	})
}

// eventsHandler subscribes to the shared heartbeat hub instead of running
// its own ticker.
func eventsHandler(w http.ResponseWriter, r *http.Request) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	w.WriteHeader(http.StatusOK)
	flusher.Flush()

	client := make(chan string, 8)
	heartbeatHub.Lock()
	heartbeatHub.clients[client] = struct{}{}
	heartbeatHub.Unlock()
	defer func() {
		heartbeatHub.Lock()
		delete(heartbeatHub.clients, client)
		heartbeatHub.Unlock()
	}()

	ctx := r.Context()
	for {
		select {
		case <-ctx.Done():
			return
		case payload := <-client:
			fmt.Fprintf(w, "data: %s\n\n", payload)
			flusher.Flush()
		}
	}
}
