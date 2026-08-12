package server

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"html"
	"image"
	_ "image/jpeg"
	_ "image/png"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"local/flux/internal/config"
	"local/flux/internal/daemon"
	"local/flux/internal/fleet"
	"local/flux/internal/prompt"

	_ "modernc.org/sqlite"
)

type Options struct {
	Addr  string
	Token string
	// PublicReadOnly serves the gallery to the open internet: safe GETs on an
	// allowlist, everything else refused. Set it whenever the listener is
	// reachable without a token, which is the only way a browser can open the
	// page at all.
	PublicReadOnly bool
}

type Server struct {
	cfg    config.Config
	client daemon.Client
	// pool spans one worker per detected GPU. It is empty on hosts with no
	// GPU, in which case every worker helper falls back to client.
	pool fleet.Pool
}

var modelDownloadState struct {
	sync.Mutex
	running bool
	message string
}

var atlasNexusReceipts sync.Map

var studioSchemaState = struct {
	sync.Mutex
	dbs map[string]*sql.DB
}{dbs: make(map[string]*sql.DB)}

var motionAssetHub = struct {
	sync.Mutex
	clients map[chan map[string]any]struct{}
	recent  []map[string]any
}{clients: make(map[chan map[string]any]struct{})}

// motionTelemetryHub fans a single shared nvidia-smi poll out to every
// connected /api/telemetry/events client, instead of each connection
// spawning its own "nvidia-smi --loop=1" subprocess (which is what
// telemetryEvents used to do — fine for one viewer, but N viewers meant N
// redundant GPU-query processes running simultaneously).
var motionTelemetryHub = struct {
	sync.Mutex
	clients map[chan map[string]any]struct{}
	latest  map[string]any
}{clients: make(map[chan map[string]any]struct{})}

// motionProcessHub is the same fan-out fix applied to the sibling
// /api/telemetry/processes/events endpoint, which had the identical bug:
// one "nvidia-smi pmon" subprocess spawned per connected client.
var motionProcessHub = struct {
	sync.Mutex
	clients map[chan map[string]any]struct{}
}{clients: make(map[chan map[string]any]struct{})}

// jobsWorkerResponse is the shared, not-yet-per-client-personalized result
// of one "op":"jobs" worker query — output URLs are added per subscriber
// afterward (see jobsEvents) since they depend on that client's own
// request Host (publicBaseURL(r)), which the shared poller has no access to.
type jobsWorkerResponse struct {
	WorkerRunning bool
	WorkerError   string
	Jobs          []map[string]any
	ModelLoaded   bool
	Backend       string
	Device        string
}

// motionJobsHub fans a single shared worker query out to every connected
// /api/jobs/events client, instead of each connection running its own
// inotify watch on jobs.jsonl AND its own worker IPC round trip on every
// change (N independent watches + N redundant IPC calls for identical data).
var motionJobsHub = struct {
	sync.Mutex
	clients map[chan *jobsWorkerResponse]struct{}
	latest  *jobsWorkerResponse
}{clients: make(map[chan *jobsWorkerResponse]struct{})}

type renderRequest struct {
	Prompt         string  `json:"prompt"`
	Model          string  `json:"model"`
	Backend        string  `json:"backend"`
	Preset         string  `json:"preset"`
	Style          string  `json:"style"`
	Mood           string  `json:"mood"`
	Camera         string  `json:"camera"`
	Light          string  `json:"light"`
	Palette        string  `json:"palette"`
	Texture        string  `json:"texture"`
	Detail         string  `json:"detail"`
	Chaos          string  `json:"chaos"`
	Director       string  `json:"director"`
	Ratio          string  `json:"ratio"`
	Width          int     `json:"width"`
	Height         int     `json:"height"`
	Steps          int     `json:"steps"`
	Guidance       float64 `json:"guidance"`
	LatentDistance float64 `json:"latent_distance"`
	Seed           string  `json:"seed"`
	Filename       string  `json:"filename"`
	Iterations     int     `json:"iterations"`
	Draft          bool    `json:"draft"`
	DryRun         bool    `json:"dry_run"`
}

type renderPlan struct {
	Prompt   string   `json:"prompt"`
	Model    string   `json:"model"`
	Backend  string   `json:"backend"`
	Preset   string   `json:"preset,omitempty"`
	Style    string   `json:"style,omitempty"`
	Mood     string   `json:"mood,omitempty"`
	Camera   string   `json:"camera,omitempty"`
	Light    string   `json:"light,omitempty"`
	Palette  string   `json:"palette,omitempty"`
	Texture  string   `json:"texture,omitempty"`
	Detail   string   `json:"detail,omitempty"`
	Chaos    string   `json:"chaos,omitempty"`
	Director string   `json:"director,omitempty"`
	Ratio    string   `json:"ratio"`
	Width    int      `json:"width"`
	Height   int      `json:"height"`
	Steps    int      `json:"steps"`
	Guidance float64  `json:"guidance"`
	Seed     string   `json:"seed,omitempty"`
	Filename string   `json:"filename,omitempty"`
	Command  []string `json:"command"`
}

type jobActionRequest struct {
	ID string `json:"id"`
}

type img2imgRequest struct {
	Prompt        string  `json:"prompt"`
	Image         string  `json:"image"`
	Image2        string  `json:"image2"`
	IdentityImage string  `json:"identity_image"`
	PostureImage  string  `json:"posture_image"`
	BackdropImage string  `json:"backdrop_image"`
	Backend       string  `json:"backend"`
	Width         int     `json:"width"`
	Height        int     `json:"height"`
	Steps         int     `json:"steps"`
	Guidance      float64 `json:"guidance"`
	Strength      float64 `json:"strength"`
	Seed          string  `json:"seed"`
	Filename      string  `json:"filename"`
	DryRun        bool    `json:"dry_run"`
}

type blendImageInput struct {
	Image  string  `json:"image"`
	Label  string  `json:"label"`
	Weight float64 `json:"weight"`
	Part   string  `json:"part"`
}

type blendRequest struct {
	Images []blendImageInput `json:"images"`
	Width  int               `json:"width"`
	Height int               `json:"height"`
	Name   string            `json:"name"`
	Mode   string            `json:"mode"`
}

type atlasSubmitRequest struct {
	Prompt          string    `json:"prompt"`
	ID              string    `json:"id"`
	Backend         string    `json:"backend"`
	Model           string    `json:"model"`
	Precision       string    `json:"precision"`
	BatchSize       int       `json:"batch_size"`
	DimensionRates  []float64 `json:"dimension_rates"`
	StudyType       string    `json:"study_type"`
	RunType         string    `json:"run_type"`
	IndexStart      int       `json:"index_start"`
	IndexEnd        int       `json:"index_end"`
	SampleMode      string    `json:"sample_mode"`
	Cells           int       `json:"cells"`
	Size            int       `json:"size"`
	Steps           int       `json:"steps"`
	Guidance        float64   `json:"guidance"`
	Seed            string    `json:"seed"`
	SeedB           int64     `json:"seed_b"`
	SeedC           int64     `json:"seed_c"`
	SeedD           int64     `json:"seed_d"`
	ShellScale      float64   `json:"shell_scale"`
	SeedLock        float64   `json:"seed_lock"`
	ShellCoupling   float64   `json:"shell_coupling"`
	Mode            string    `json:"mode"`
	TraversalOrder  string    `json:"traversal_order"`
	Adapter         string    `json:"adapter"`
	CacheThreshold  float64   `json:"cache_threshold"`
	CacheDownsample int       `json:"cache_downsample"`
	CacheWarmup     int       `json:"cache_warmup"`
	DryRun          bool      `json:"dry_run"`
}

func animeCastBatchPlist() string {
	home, err := os.UserHomeDir()
	if err != nil {
		home = "."
	}
	return filepath.Join(home, "Library", "LaunchAgents", "com.flux.anime-cast-batch.plist")
}

func ListenAndServe(ctx context.Context, cfg config.Config, opt Options) error {
	if strings.TrimSpace(opt.Addr) == "" {
		opt.Addr = "127.0.0.1:7861"
	}
	s := Server{cfg: cfg, client: daemon.New(cfg), pool: fleet.New(cfg)}
	if s.fleetOn() {
		slog.Info("flux fleet enabled", "gpus", s.pool.GPUs(), "workers", s.pool.Size())
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/", s.home)
	mux.HandleFunc("/app", s.app)
	mux.HandleFunc("/app/", s.app)
	mux.HandleFunc("/atelier", s.legacyAtelier)
	mux.HandleFunc("/atelier/", s.legacyAtelier)
	mux.HandleFunc("/motion-atlas", s.motionAtlas)
	mux.HandleFunc("/motion-atlas/", s.motionAtlas)
	mux.HandleFunc("/atlas-studio", s.atlasStudio)
	mux.HandleFunc("/flux/atlas-studio", s.atlasStudio)
	mux.HandleFunc("/atlas-watch", s.atlasWatch)
	mux.HandleFunc("/flux/atlas-watch", s.atlasWatch)
	mux.HandleFunc("/api/health", s.health)
	mux.HandleFunc("/api/governor/chat", s.governorChat)
	mux.HandleFunc("/api/visionary/chat", s.visionaryChat)
	mux.HandleFunc("/api/telemetry", s.telemetry)
	mux.HandleFunc("/api/telemetry/events", s.telemetryEvents)
	mux.HandleFunc("/api/telemetry/ws", s.telemetryWS)
	mux.HandleFunc("/api/telemetry/processes/events", s.telemetryProcessEvents)
	mux.HandleFunc("/api/telemetry/processes/ws", s.telemetryProcessWS)
	mux.HandleFunc("/api/assets/events", s.assetEvents)
	mux.HandleFunc("/api/assets/ws", s.assetWS)
	mux.HandleFunc("/api/model/download", s.modelDownload)
	mux.HandleFunc("/api/model/load", s.modelLoad)
	mux.HandleFunc("/api/model/events", s.modelEvents)
	mux.HandleFunc("/api/model/ws", s.modelWS)
	mux.HandleFunc("/api/jobs", s.jobs)
	mux.HandleFunc("/api/jobs/events", s.jobsEvents)
	mux.HandleFunc("/api/jobs/ws", s.jobsWS)
	mux.HandleFunc("/api/job/cancel", s.cancelJob)
	mux.HandleFunc("/api/job/update", s.updateJob)
	mux.HandleFunc("/api/jobs/prune", s.pruneJobs)
	mux.HandleFunc("/api/render", s.render)
	mux.HandleFunc("/api/img2img", s.img2img)
	mux.HandleFunc("/api/img2img/jobs", s.img2imgJobs)
	mux.HandleFunc("/api/img2img/events", s.img2imgEvents)
	mux.HandleFunc("/api/img2img/warm", s.img2imgWarm)
	mux.HandleFunc("/api/img2img/cancel", s.img2imgCancel)
	mux.HandleFunc("/api/blend", s.blendImages)
	mux.HandleFunc("/api/atlas/submit", s.submitAtlas)
	mux.HandleFunc("/api/atlas/preview", s.previewAtlasSeeds)
	mux.HandleFunc("/api/atlas/seeds", s.atlasSeeds)
	mux.HandleFunc("/api/atlas/seed", s.atlasSeed)
	mux.HandleFunc("/api/atlas/catalog", s.atlasCatalog)
	mux.HandleFunc("/api/asset/thumbnail", s.assetThumbnail)
	mux.HandleFunc("/api/upload", s.uploadImage)
	mux.HandleFunc("/api/warm", s.warm)
	mux.HandleFunc("/api/stop", s.stop)
	mux.HandleFunc("/api/batch/pause", s.pauseBatch)
	mux.HandleFunc("/api/batch/resume", s.resumeBatch)
	mux.HandleFunc("/api/collections", s.collections)
	mux.HandleFunc("/api/collection", s.collection)
	mux.HandleFunc("/api/collection/picks", s.collectionPicks)
	mux.HandleFunc("/api/collection/delete", s.deleteCollection)
	mux.HandleFunc("/api/recent-images", s.recentImages)
	mux.HandleFunc("/api/atlas/events/", s.atlasEvents)
	mux.HandleFunc("/api/gallery/events/", s.galleryEvents)
	mux.HandleFunc("/atlas/", s.atlas)
	mux.HandleFunc("/gallery", s.gallery)
	mux.HandleFunc("/gallery/", s.gallery)
	mux.HandleFunc("/movement", s.movement)
	mux.HandleFunc("/movement/", s.movement)
	mux.HandleFunc("/exhibition", s.exhibition)
	mux.HandleFunc("/exhibition/", s.exhibition)
	mux.HandleFunc("/staged/", s.staged)
	mux.HandleFunc("/outputs/", s.output)
	s.restoreAtlasReceipts()
	go s.reconcileAtlasCatalog()
	go s.runPiperAssetHub(ctx)
	go s.runTelemetryHub(ctx)
	go s.runTelemetryProcessHub(ctx)
	go s.runJobsHub(ctx)
	go s.runModelHub(ctx)

	httpServer := &http.Server{
		Addr:              opt.Addr,
		Handler:           withAuth(withReadOnly(withLocalHeaders(mux), opt.PublicReadOnly), opt.Token),
		ReadHeaderTimeout: 5 * time.Second,
	}
	errc := make(chan error, 1)
	go func() {
		slog.Info("flux http server listening", "addr", opt.Addr)
		errc <- httpServer.ListenAndServe()
	}()

	select {
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdownCtx)
		return ctx.Err()
	case err := <-errc:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}

// readOnlyPaths are the only things a public listener answers. An allowlist
// rather than a blocklist of mutating verbs: a GET can still be expensive or
// revealing (/api/warm loads the model, the governor proxies bill someone
// else's key), and a blocklist silently opens every route added later.
//
// Prefixes, matched against the cleaned path.
var readOnlyPaths = []string{
	"/app",
	"/gallery",
	"/movement",
	"/exhibition",
	"/atelier",
	"/outputs/",
	"/api/health",
	"/api/recent-images",
	// Without this the gallery falls back to full-size PNGs -- megabytes per
	// tile, across two proxy hops.
	"/api/asset/thumbnail",
	"/api/assets/events",
	"/api/assets/ws",
	"/api/telemetry/events",
	"/api/telemetry/ws",
	"/api/jobs",
}

func readOnlyAllowed(r *http.Request) bool {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		return false
	}
	// /api/jobs is a listing, but /api/jobs/<id>/cancel would not be; only the
	// exact listing path and the websocket beneath it are safe.
	path := r.URL.Path
	if strings.HasPrefix(path, "/api/jobs") && path != "/api/jobs" && path != "/api/jobs/ws" {
		return false
	}
	for _, p := range readOnlyPaths {
		if path == p || strings.HasPrefix(path, p) {
			return true
		}
	}
	return path == "/" || path == "/favicon.ico"
}

// withReadOnly refuses anything outside the gallery when the listener is
// public. It sits inside withAuth so a token-bearing operator is unaffected
// only when no public flag is set -- the flag is the deliberate choice to
// serve strangers, so it applies to every request on that listener.
func withReadOnly(next http.Handler, enabled bool) http.Handler {
	if !enabled {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !readOnlyAllowed(r) {
			writeError(w, http.StatusForbidden, "this listener is public and read-only")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func withAuth(next http.Handler, token string) http.Handler {
	token = strings.TrimSpace(token)
	if token == "" {
		return next
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodOptions {
			next.ServeHTTP(w, r)
			return
		}
		if !authorized(r, token) {
			w.Header().Set("WWW-Authenticate", `Basic realm="flux", charset="UTF-8"`)
			writeError(w, http.StatusUnauthorized, "missing or invalid FLUX token")
			return
		}
		next.ServeHTTP(w, r)
	})
}

func authorized(r *http.Request, token string) bool {
	if strings.TrimSpace(r.Header.Get("X-Flux-Token")) == token {
		return true
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	lower := strings.ToLower(auth)
	if strings.HasPrefix(lower, "bearer ") {
		return strings.TrimSpace(auth[len("bearer "):]) == token
	}
	if strings.HasPrefix(lower, "basic ") {
		decoded, err := base64.StdEncoding.DecodeString(strings.TrimSpace(auth[len("basic "):]))
		if err != nil {
			return false
		}
		user, pass, ok := strings.Cut(string(decoded), ":")
		return ok && (user == token || pass == token)
	}
	return false
}

func (s Server) home(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/" {
		http.NotFound(w, r)
		return
	}
	if host := strings.ToLower(strings.TrimSpace(strings.Split(r.Host, ":")[0])); host == "flux.influx.vision" {
		http.Redirect(w, r, "/motion-atlas/", http.StatusTemporaryRedirect)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "web", "tea", "index.html"))
}

// app keeps the production console available without asking a public landing
// page to also explain a working machine. The public listener still applies
// its read-only gate, so exposing the shell does not expose GPU mutations.
func (s Server) app(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/app/" {
		http.Redirect(w, r, "/app", http.StatusPermanentRedirect)
		return
	}
	if r.URL.Path != "/app" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(indexHTML(s.cfg)))
}

func (s Server) atlasStudio(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/atlas-studio" && r.URL.Path != "/flux/atlas-studio" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(atlasStudioHTML(s.cfg)))
}

func (s Server) motionAtlas(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/motion-atlas" {
		http.Redirect(w, r, "/motion-atlas/", http.StatusTemporaryRedirect)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/motion-atlas/")
	if name == "" {
		name = "index.html"
	}
	allowed := map[string]bool{
		"index.html": true, "app.css": true, "app.js": true,
		"topbar.css":  true,
		"optics.html": true, "optics.js": true,
		"queue.html": true, "queue.js": true,
		"registry.html": true, "registry.js": true,
		"governor.html": true, "governor.js": true, "governor.css": true,
		"visionary.html": true, "visionary.js": true, "visionary.css": true,
		"processing.html": true, "processing.js": true,
	}
	if !allowed[name] {
		http.NotFound(w, r)
		return
	}
	if strings.HasSuffix(name, ".css") || strings.HasSuffix(name, ".js") {
		w.Header().Set("Cache-Control", "public, max-age=3600")
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "web", "motion-atlas", name))
}

// galleryFlux serves the live body of work, fed by this same server's asset
// and job lanes.
//
// It is mounted here rather than under the static lane on purpose --
// ListenAndServeStatic attaches no API, and the page is nothing without
// /api/assets/ws and /outputs/ coming from the same origin.
func (s Server) galleryFlux(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/gallery" {
		http.Redirect(w, r, "/gallery/", http.StatusPermanentRedirect)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/gallery/")
	if name == "" {
		name = "index.html"
	}
	// The page is deliberately a single self-contained file, so an allowlist of
	// one is the whole surface: anything else is a path we never meant to serve.
	if name != "index.html" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "web", "atelier-flux", name))
}

// movement presents one live authored path. The exhibition is a second,
// collection-level surface that places it beside the Stallion atlas.
func (s Server) movement(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/movement/" {
		http.Redirect(w, r, "/movement", http.StatusPermanentRedirect)
		return
	}
	if r.URL.Path != "/movement" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "web", "atelier-flux", "movement.html"))
}

func (s Server) exhibition(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/exhibition/" {
		http.Redirect(w, r, "/exhibition", http.StatusPermanentRedirect)
		return
	}
	if r.URL.Path == "/exhibition" {
		http.ServeFile(w, r, filepath.Join(s.cfg.Root, "web", "atelier-flux", "exhibition.html"))
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/exhibition/")
	allowed := map[string]bool{
		"stallion-atlas-exhibition.mp4": true,
		"stallion-atlas-poster.jpg":     true,
		"stallion-atlas-contact.jpg":    true,
	}
	if !allowed[name] {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "web", "atelier-flux", "assets", name))
}

// legacyAtelier preserves links shared while the wall still carried its
// working name. The redirect is permanent; no second copy of the gallery is
// allowed to drift behind the canonical one.
func (s Server) legacyAtelier(w http.ResponseWriter, r *http.Request) {
	suffix := strings.TrimPrefix(r.URL.Path, "/atelier")
	http.Redirect(w, r, "/gallery"+suffix, http.StatusPermanentRedirect)
}

func (s Server) governorChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 2<<20))
	if err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, "https://governor.influx.vision/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		http.Error(w, "unable to create Governor request", http.StatusInternalServerError)
		return
	}
	request.Header.Set("Content-Type", "application/json")
	request.Header.Set("Accept", "application/json")
	response, err := (&http.Client{Timeout: 5 * time.Minute}).Do(request)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	defer response.Body.Close()
	w.Header().Set("Content-Type", response.Header.Get("Content-Type"))
	w.WriteHeader(response.StatusCode)
	_, _ = io.Copy(w, response.Body)
}

func (s Server) visionaryChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 32<<20))
	if err != nil {
		http.Error(w, "invalid request body", http.StatusBadRequest)
		return
	}
	request, err := http.NewRequestWithContext(r.Context(), http.MethodPost, "http://127.0.0.1:8001/v1/chat/completions", bytes.NewReader(body))
	if err != nil {
		http.Error(w, "unable to create Visionary request", http.StatusInternalServerError)
		return
	}
	request.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{Timeout: 5 * time.Minute}).Do(request)
	if err != nil {
		writeJSON(w, http.StatusBadGateway, map[string]any{"error": err.Error()})
		return
	}
	defer response.Body.Close()
	w.Header().Set("Content-Type", response.Header.Get("Content-Type"))
	w.WriteHeader(response.StatusCode)
	_, _ = io.Copy(w, response.Body)
}

func (s Server) atlasWatch(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path != "/atlas-watch" && r.URL.Path != "/flux/atlas-watch" {
		http.NotFound(w, r)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	_, _ = w.Write([]byte(atlasWatchHTML(s.cfg)))
}

func (s Server) health(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	resp, err := s.workerPing()
	body := map[string]any{
		"ok":              true,
		"backend":         s.cfg.Backend,
		"model_dir":       s.cfg.ModelDir,
		"output_dir":      s.cfg.OutputDir,
		"nexus_reachable": nexusReachable(),
		"piper_reachable": piperReachable(),
	}
	if fleetStatus := s.fleetStatusPayload(); fleetStatus != nil {
		body["fleet"] = fleetStatus
	}
	if err != nil {
		body["worker_running"] = false
		body["worker_error"] = err.Error()
		writeJSON(w, http.StatusOK, body)
		return
	}
	body["worker_running"] = true
	body["loaded"] = resp.Loaded
	body["device"] = resp.Device
	body["backend"] = resp.Backend
	writeJSON(w, http.StatusOK, body)
}

func (s Server) telemetry(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	out, err := exec.CommandContext(ctx, "nvidia-smi",
		"--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
		"--format=csv,noheader,nounits",
	).Output()
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "available": false})
		return
	}
	gpus := make([]map[string]any, 0)
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		if gpu, ok := parseTelemetryLine(line); ok {
			gpus = append(gpus, gpu)
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "available": len(gpus) > 0, "gpus": gpus})
}

func (s Server) telemetryEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	events := make(chan map[string]any, 8)
	motionTelemetryHub.Lock()
	motionTelemetryHub.clients[events] = struct{}{}
	latest := motionTelemetryHub.latest
	motionTelemetryHub.Unlock()
	defer func() {
		motionTelemetryHub.Lock()
		delete(motionTelemetryHub.clients, events)
		motionTelemetryHub.Unlock()
	}()

	send := func(event map[string]any) bool {
		raw, _ := json.Marshal(event)
		if _, err := fmt.Fprintf(w, "event: gpu\ndata: %s\n\n", raw); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	if latest != nil && !send(latest) {
		return
	}
	for {
		select {
		case <-r.Context().Done():
			return
		case event := <-events:
			if !send(event) {
				return
			}
		}
	}
}

// telemetryWS is the WebSocket twin of telemetryEvents, same motionTelemetryHub
// subscription -- see jobsWS for why this exists (smaller per-message
// framing, faster reconnect-storm recovery; fan-out latency itself is tied
// with SSE since both ride the same broadcast).
func (s Server) telemetryWS(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	conn, err := upgradeWebSocket(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	defer conn.Close()

	done := make(chan struct{})
	go func() { conn.readLoop(); close(done) }()

	events := make(chan map[string]any, 8)
	motionTelemetryHub.Lock()
	motionTelemetryHub.clients[events] = struct{}{}
	latest := motionTelemetryHub.latest
	motionTelemetryHub.Unlock()
	defer func() {
		motionTelemetryHub.Lock()
		delete(motionTelemetryHub.clients, events)
		motionTelemetryHub.Unlock()
	}()

	send := func(event map[string]any) bool {
		raw, err := json.Marshal(event)
		if err != nil {
			return false
		}
		return conn.writeText(raw) == nil
	}
	if latest != nil && !send(latest) {
		return
	}
	ping := time.NewTicker(wsPingInterval)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-done:
			return
		case <-ping.C:
			if conn.writePing() != nil {
				return
			}
		case event := <-events:
			if !send(event) {
				return
			}
		}
	}
}

// runTelemetryHub is the single shared nvidia-smi poller backing
// motionTelemetryHub — started once at server startup, regardless of how
// many /api/telemetry/events viewers connect or disconnect.
func (s Server) runTelemetryHub(ctx context.Context) {
	for ctx.Err() == nil {
		cmd := exec.CommandContext(ctx, "nvidia-smi",
			"--query-gpu=index,name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
			"--format=csv,noheader,nounits", "--loop=1",
		)
		stdout, err := cmd.StdoutPipe()
		if err != nil || cmd.Start() != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Second):
				continue
			}
		}
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			gpu, ok := parseTelemetryLine(scanner.Text())
			if !ok {
				continue
			}
			event := map[string]any{"ok": true, "available": true, "gpu": gpu}
			motionTelemetryHub.Lock()
			motionTelemetryHub.latest = event
			for client := range motionTelemetryHub.clients {
				select {
				case client <- event:
				default:
				}
			}
			motionTelemetryHub.Unlock()
		}
		_ = cmd.Wait()
		if ctx.Err() != nil {
			return
		}
		time.Sleep(time.Second)
	}
}

func (s Server) telemetryProcessEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")

	events := make(chan map[string]any, 32)
	motionProcessHub.Lock()
	motionProcessHub.clients[events] = struct{}{}
	motionProcessHub.Unlock()
	defer func() {
		motionProcessHub.Lock()
		delete(motionProcessHub.clients, events)
		motionProcessHub.Unlock()
	}()

	for {
		select {
		case <-r.Context().Done():
			return
		case event := <-events:
			raw, _ := json.Marshal(event)
			if _, err := fmt.Fprintf(w, "event: process\ndata: %s\n\n", raw); err != nil {
				return
			}
			flusher.Flush()
		}
	}
}

// telemetryProcessWS is the WebSocket twin of telemetryProcessEvents.
func (s Server) telemetryProcessWS(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	conn, err := upgradeWebSocket(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	defer conn.Close()

	done := make(chan struct{})
	go func() { conn.readLoop(); close(done) }()

	events := make(chan map[string]any, 32)
	motionProcessHub.Lock()
	motionProcessHub.clients[events] = struct{}{}
	motionProcessHub.Unlock()
	defer func() {
		motionProcessHub.Lock()
		delete(motionProcessHub.clients, events)
		motionProcessHub.Unlock()
	}()

	ping := time.NewTicker(wsPingInterval)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-done:
			return
		case <-ping.C:
			if conn.writePing() != nil {
				return
			}
		case event := <-events:
			raw, err := json.Marshal(event)
			if err != nil {
				continue
			}
			if conn.writeText(raw) != nil {
				return
			}
		}
	}
}

// runTelemetryProcessHub is the single shared "nvidia-smi pmon" poller
// backing motionProcessHub — started once at server startup.
func (s Server) runTelemetryProcessHub(ctx context.Context) {
	for ctx.Err() == nil {
		cmd := exec.CommandContext(ctx, "nvidia-smi", "pmon", "-s", "um", "-d", "1")
		stdout, err := cmd.StdoutPipe()
		if err != nil || cmd.Start() != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Second):
				continue
			}
		}
		scanner := bufio.NewScanner(stdout)
		for scanner.Scan() {
			fields := strings.Fields(scanner.Text())
			if len(fields) < 12 || strings.HasPrefix(fields[0], "#") || fields[1] == "-" {
				continue
			}
			pid, _ := strconv.Atoi(fields[1])
			command := strings.Join(fields[11:], " ")
			if raw, err := os.ReadFile(fmt.Sprintf("/proc/%d/cmdline", pid)); err == nil {
				if full := strings.TrimSpace(strings.ReplaceAll(string(raw), "\x00", " ")); full != "" {
					command = full
				}
			}
			label := "GPU PROCESS"
			lower := strings.ToLower(command)
			switch {
			case strings.Contains(lower, "vllm"):
				label = "vLLM"
			case strings.Contains(lower, "worker.py") || strings.Contains(lower, "flux"):
				label = "FLUX"
			}
			event := map[string]any{
				"pid": pid, "gpu": fields[0], "sm": telemetryNumber(fields[3]),
				"memory_utilization": telemetryNumber(fields[4]),
				"memory_used":        telemetryNumber(fields[9]),
				"label":              label, "command": command,
			}
			motionProcessHub.Lock()
			for client := range motionProcessHub.clients {
				select {
				case client <- event:
				default:
				}
			}
			motionProcessHub.Unlock()
		}
		_ = cmd.Wait()
		if ctx.Err() != nil {
			return
		}
		time.Sleep(time.Second)
	}
}

func parseTelemetryLine(line string) (map[string]any, bool) {
	parts := strings.Split(line, ",")
	if len(parts) < 8 {
		return nil, false
	}
	for i := range parts {
		parts[i] = strings.TrimSpace(parts[i])
	}
	return map[string]any{
		"index": parts[0], "name": parts[1],
		"utilization":  telemetryNumber(parts[2]),
		"memory_used":  telemetryNumber(parts[3]),
		"memory_total": telemetryNumber(parts[4]),
		"temperature":  telemetryNumber(parts[5]),
		"power_draw":   telemetryNumber(parts[6]),
		"power_limit":  telemetryNumber(parts[7]),
	}, true
}

// assetWS is the WebSocket twin of assetEvents, including the same
// job_id filter and replay-of-recent-history behavior on connect.
func (s Server) assetWS(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	jobFilter := strings.TrimSpace(r.URL.Query().Get("job_id"))
	conn, err := upgradeWebSocket(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	defer conn.Close()

	done := make(chan struct{})
	go func() { conn.readLoop(); close(done) }()

	events := make(chan map[string]any, 32)
	motionAssetHub.Lock()
	motionAssetHub.clients[events] = struct{}{}
	recent := append([]map[string]any(nil), motionAssetHub.recent...)
	motionAssetHub.Unlock()
	defer func() {
		motionAssetHub.Lock()
		delete(motionAssetHub.clients, events)
		motionAssetHub.Unlock()
	}()

	send := func(event map[string]any) bool {
		if jobFilter != "" && stringValue(event["job_id"]) != jobFilter {
			return true
		}
		asset, _ := event["asset"].(map[string]any)
		if asset == nil || !strings.HasPrefix(stringValue(asset["access_url"]), "/outputs/") {
			return true
		}
		raw, err := json.Marshal(event)
		if err != nil {
			return true
		}
		return conn.writeText(raw) == nil
	}
	for _, event := range recent {
		replay := make(map[string]any, len(event)+1)
		for key, value := range event {
			replay[key] = value
		}
		replay["replay"] = true
		if !send(replay) {
			return
		}
	}
	ping := time.NewTicker(wsPingInterval)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-done:
			return
		case <-ping.C:
			if conn.writePing() != nil {
				return
			}
		case event := <-events:
			if !send(event) {
				return
			}
		}
	}
}

func (s Server) assetEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")
	jobFilter := strings.TrimSpace(r.URL.Query().Get("job_id"))
	events := make(chan map[string]any, 32)
	motionAssetHub.Lock()
	motionAssetHub.clients[events] = struct{}{}
	recent := append([]map[string]any(nil), motionAssetHub.recent...)
	motionAssetHub.Unlock()
	defer func() {
		motionAssetHub.Lock()
		delete(motionAssetHub.clients, events)
		motionAssetHub.Unlock()
	}()
	send := func(event map[string]any) bool {
		if jobFilter != "" && stringValue(event["job_id"]) != jobFilter {
			return true
		}
		asset, _ := event["asset"].(map[string]any)
		if asset == nil || !strings.HasPrefix(stringValue(asset["access_url"]), "/outputs/") {
			return true
		}
		raw, _ := json.Marshal(event)
		if _, err := fmt.Fprintf(w, "event: asset\ndata: %s\n\n", raw); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	for _, event := range recent {
		replay := make(map[string]any, len(event)+1)
		for key, value := range event {
			replay[key] = value
		}
		replay["replay"] = true
		if !send(replay) {
			return
		}
	}
	for {
		select {
		case <-r.Context().Done():
			return
		case event := <-events:
			if !send(event) {
				return
			}
		}
	}
}

func (s Server) runPiperAssetHub(ctx context.Context) {
	socketPath := strings.TrimSpace(os.Getenv("PIPER_SOCKET"))
	if socketPath == "" {
		socketPath = "/tmp/piper.sock"
	}
	for ctx.Err() == nil {
		conn, err := net.DialTimeout("unix", socketPath, 2*time.Second)
		if err != nil {
			select {
			case <-ctx.Done():
				return
			case <-time.After(time.Second):
				continue
			}
		}
		_, _ = fmt.Fprintln(conn, `{"type":"asset.subscribe","consumer":"flux-motion-atlas-hub"}`)
		scanner := bufio.NewScanner(conn)
		for scanner.Scan() {
			var event map[string]any
			if json.Unmarshal(scanner.Bytes(), &event) != nil || stringValue(event["event"]) != "ASSET_READY" {
				continue
			}
			asset, _ := event["asset"].(map[string]any)
			if asset == nil || !strings.HasPrefix(stringValue(asset["access_url"]), "/outputs/") {
				continue
			}
			s.storeAtlasAsset(event)
			motionAssetHub.Lock()
			motionAssetHub.recent = append(motionAssetHub.recent, event)
			if len(motionAssetHub.recent) > 64 {
				motionAssetHub.recent = motionAssetHub.recent[len(motionAssetHub.recent)-64:]
			}
			for client := range motionAssetHub.clients {
				select {
				case client <- event:
				default:
				}
			}
			motionAssetHub.Unlock()
		}
		_ = conn.Close()
	}
}

func telemetryNumber(value string) float64 {
	value = strings.TrimSpace(strings.TrimSuffix(value, " W"))
	n, _ := strconv.ParseFloat(value, 64)
	return n
}

func (s Server) jobs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	resp, err := s.workerSnapshot()
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":             true,
			"worker_running": false,
			"jobs":           []any{},
			"worker_error":   err.Error(),
		})
		return
	}
	jobs := s.jobsWithOutputURLs(r, dashboardJobs(resp.Jobs))
	applyAtlasReceipts(jobs)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":             true,
		"worker_running": true,
		"jobs":           jobs,
	})
}

func (s Server) jobsEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	header := w.Header()
	header.Set("Content-Type", "text/event-stream")
	header.Set("Cache-Control", "no-cache")
	header.Set("Connection", "keep-alive")
	header.Set("X-Accel-Buffering", "no")

	updates := make(chan *jobsWorkerResponse, 4)
	motionJobsHub.Lock()
	motionJobsHub.clients[updates] = struct{}{}
	latest := motionJobsHub.latest
	motionJobsHub.Unlock()
	defer func() {
		motionJobsHub.Lock()
		delete(motionJobsHub.clients, updates)
		motionJobsHub.Unlock()
	}()

	// Output URLs, receipts, and the model-downloaded check are computed
	// per client here (cheap: no IPC, no filesystem watch) since they
	// depend on this request's own Host header — the shared poller above
	// only dedupes the expensive, identical-for-everyone part (the worker
	// query and the jobs.jsonl watch).
	send := func(data *jobsWorkerResponse) bool {
		body := map[string]any{"ok": true, "worker_running": data.WorkerRunning, "jobs": []any{}}
		if data.WorkerRunning {
			jobs := s.jobsWithOutputURLs(r, data.Jobs)
			applyAtlasReceipts(jobs)
			body["jobs"] = jobs
			body["model_loaded"] = data.ModelLoaded
			body["backend"] = data.Backend
			body["device"] = data.Device
		} else {
			body["worker_error"] = data.WorkerError
		}
		body["model_downloaded"] = serverModelReady(s.cfg.ModelDir)
		raw, err := json.Marshal(body)
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "event: jobs\ndata: %s\n\n", raw); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}

	if latest != nil && !send(latest) {
		return
	}
	for {
		select {
		case <-r.Context().Done():
			return
		case data := <-updates:
			if !send(data) {
				return
			}
		}
	}
}

// jobsWS is the WebSocket twin of jobsEvents, for multi-hour render jobs
// where a live process view is the point, not a nice-to-have. Same
// motionJobsHub subscription, same per-client output-URL/receipt handling;
// only the wire format differs (WS text frames instead of SSE "event:"
// framing).
func (s Server) jobsWS(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	conn, err := upgradeWebSocket(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	defer conn.Close()

	done := make(chan struct{})
	go func() {
		conn.readLoop()
		close(done)
	}()

	updates := make(chan *jobsWorkerResponse, 4)
	motionJobsHub.Lock()
	motionJobsHub.clients[updates] = struct{}{}
	latest := motionJobsHub.latest
	motionJobsHub.Unlock()
	defer func() {
		motionJobsHub.Lock()
		delete(motionJobsHub.clients, updates)
		motionJobsHub.Unlock()
	}()

	send := func(data *jobsWorkerResponse) bool {
		body := map[string]any{"ok": true, "worker_running": data.WorkerRunning, "jobs": []any{}}
		if data.WorkerRunning {
			jobs := s.jobsWithOutputURLs(r, data.Jobs)
			applyAtlasReceipts(jobs)
			body["jobs"] = jobs
			body["model_loaded"] = data.ModelLoaded
			body["backend"] = data.Backend
			body["device"] = data.Device
		} else {
			body["worker_error"] = data.WorkerError
		}
		body["model_downloaded"] = serverModelReady(s.cfg.ModelDir)
		raw, err := json.Marshal(body)
		if err != nil {
			return false
		}
		return conn.writeText(raw) == nil
	}

	if latest != nil && !send(latest) {
		return
	}
	ping := time.NewTicker(wsPingInterval)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-done:
			return
		case <-ping.C:
			if conn.writePing() != nil {
				return
			}
		case data := <-updates:
			if !send(data) {
				return
			}
		}
	}
}

// runJobsHub is the single shared worker poller backing motionJobsHub —
// one inotify watch on jobs.jsonl, one "op":"jobs" IPC call per actual
// change, started once at server startup regardless of subscriber count.
func (s Server) runJobsHub(ctx context.Context) {
	fetch := func() *jobsWorkerResponse {
		resp, err := s.workerSnapshot()
		if err != nil {
			return &jobsWorkerResponse{WorkerRunning: false, WorkerError: err.Error()}
		}
		s.storeAtlasJobs(resp.Jobs)
		return &jobsWorkerResponse{
			WorkerRunning: true,
			Jobs:          dashboardJobs(resp.Jobs),
			ModelLoaded:   resp.Loaded,
			Backend:       resp.Backend,
			Device:        resp.Device,
		}
	}
	publish := func(data *jobsWorkerResponse) {
		motionJobsHub.Lock()
		motionJobsHub.latest = data
		for client := range motionJobsHub.clients {
			select {
			case client <- data:
			default:
			}
		}
		motionJobsHub.Unlock()
	}

	publish(fetch())
	jobsFile := filepath.Join(s.cfg.Root, ".fluxd", "jobs.jsonl")
	for ctx.Err() == nil {
		if !waitForPathChange(ctx, jobsFile) {
			return
		}
		publish(fetch())
	}
}

func (s Server) modelDownload(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	modelDownloadState.Lock()
	if modelDownloadState.running {
		modelDownloadState.Unlock()
		writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "status": "already-downloading"})
		return
	}
	modelDownloadState.running = true
	modelDownloadState.message = "Preparing FLUX.1 Dev BF16 download"
	modelDownloadState.Unlock()
	go func() {
		executable, _ := os.Executable()
		cmd := exec.Command(executable, "download", "--workers", "16")
		cmd.Dir = s.cfg.Root
		output, err := cmd.CombinedOutput()
		message := strings.TrimSpace(string(output))
		if len(message) > 500 {
			message = message[len(message)-500:]
		}
		if err != nil {
			message = err.Error() + " · " + message
		}
		modelDownloadState.Lock()
		modelDownloadState.running = false
		modelDownloadState.message = message
		modelDownloadState.Unlock()
		touchModelEvent(s.cfg.Root)
	}()
	touchModelEvent(s.cfg.Root)
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "status": "downloading"})
}

func (s Server) modelLoad(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	if !serverModelReady(s.cfg.ModelDir) {
		writeError(w, http.StatusConflict, "download FLUX.1 Dev before loading it")
		return
	}
	if err := s.workerStart(false); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	go func() {
		_, _ = s.workerBroadcast(map[string]any{"op": "warm"})
		touchModelEvent(s.cfg.Root)
	}()
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "status": "loading"})
}

func (s Server) modelEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Accel-Buffering", "no")

	updates := make(chan []byte, 4)
	motionModelHub.Lock()
	motionModelHub.clients[updates] = struct{}{}
	latest := motionModelHub.latest
	motionModelHub.Unlock()
	defer func() {
		motionModelHub.Lock()
		delete(motionModelHub.clients, updates)
		motionModelHub.Unlock()
	}()

	send := func(raw []byte) bool {
		if _, err := fmt.Fprintf(w, "event: model\ndata: %s\n\n", raw); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	if latest != nil && !send(latest) {
		return
	}
	for {
		select {
		case <-r.Context().Done():
			return
		case raw := <-updates:
			if !send(raw) {
				return
			}
		}
	}
}

// modelWS is the WebSocket twin of modelEvents.
func (s Server) modelWS(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	conn, err := upgradeWebSocket(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	defer conn.Close()

	done := make(chan struct{})
	go func() { conn.readLoop(); close(done) }()

	updates := make(chan []byte, 4)
	motionModelHub.Lock()
	motionModelHub.clients[updates] = struct{}{}
	latest := motionModelHub.latest
	motionModelHub.Unlock()
	defer func() {
		motionModelHub.Lock()
		delete(motionModelHub.clients, updates)
		motionModelHub.Unlock()
	}()

	if latest != nil && conn.writeText(latest) != nil {
		return
	}
	ping := time.NewTicker(wsPingInterval)
	defer ping.Stop()
	for {
		select {
		case <-r.Context().Done():
			return
		case <-done:
			return
		case <-ping.C:
			if conn.writePing() != nil {
				return
			}
		case raw := <-updates:
			if conn.writeText(raw) != nil {
				return
			}
		}
	}
}

// motionModelHub fans a single shared worker ping + model-download-state
// snapshot out to every connected /api/model/events client, instead of each
// connection running its own inotify watch on model.event AND its own
// worker "ping" IPC call on every change. Unlike jobs, this payload has no
// per-client variation (no Host-dependent URLs), so it's broadcast verbatim.
var motionModelHub = struct {
	sync.Mutex
	clients map[chan []byte]struct{}
	latest  []byte
}{clients: make(map[chan []byte]struct{})}

// runModelHub is the single shared poller backing motionModelHub.
func (s Server) runModelHub(ctx context.Context) {
	fetch := func() []byte {
		resp, respErr := s.workerPing()
		modelDownloadState.Lock()
		body := map[string]any{
			"downloaded":  serverModelReady(s.cfg.ModelDir),
			"downloading": modelDownloadState.running,
			"message":     modelDownloadState.message,
			"loaded":      respErr == nil && resp.Loaded,
			"device": func() string {
				if respErr == nil {
					return resp.Device
				}
				return ""
			}(),
		}
		modelDownloadState.Unlock()
		raw, _ := json.Marshal(body)
		return raw
	}
	publish := func(raw []byte) {
		motionModelHub.Lock()
		motionModelHub.latest = raw
		for client := range motionModelHub.clients {
			select {
			case client <- raw:
			default:
			}
		}
		motionModelHub.Unlock()
	}

	publish(fetch())
	statePath := filepath.Join(s.cfg.Root, ".fluxd", "model.event")
	for ctx.Err() == nil {
		if !waitForPathChange(ctx, statePath) {
			return
		}
		publish(fetch())
	}
}

func touchModelEvent(root string) {
	dir := filepath.Join(root, ".fluxd")
	_ = os.MkdirAll(dir, 0o755)
	path := filepath.Join(dir, "model.event")
	_ = os.WriteFile(path, []byte(strconv.FormatInt(time.Now().UnixNano(), 10)), 0o644)
}

func serverModelReady(modelDir string) bool {
	required := []string{
		"model_index.json", "scheduler/scheduler_config.json",
		"text_encoder/model.safetensors",
		"text_encoder_2/model-00001-of-00002.safetensors",
		"text_encoder_2/model-00002-of-00002.safetensors",
		"transformer/diffusion_pytorch_model-00001-of-00003.safetensors",
		"transformer/diffusion_pytorch_model-00002-of-00003.safetensors",
		"transformer/diffusion_pytorch_model-00003-of-00003.safetensors",
		"vae/diffusion_pytorch_model.safetensors",
	}
	for _, rel := range required {
		if _, err := os.Stat(filepath.Join(modelDir, rel)); err != nil {
			return false
		}
	}
	return true
}

func (s Server) pruneJobs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req struct {
		Keep     *int     `json:"keep"`
		Statuses []string `json:"statuses"`
	}
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&req)
	}
	keep := 0
	if req.Keep != nil && *req.Keep >= 0 {
		keep = *req.Keep
	}
	statuses := req.Statuses
	if len(statuses) == 0 {
		statuses = []string{"error", "cancelled"}
	}
	for _, status := range statuses {
		switch status {
		case "error", "cancelled", "done":
		default:
			writeError(w, http.StatusBadRequest, "statuses may only include error, cancelled, or done")
			return
		}
	}
	resp, err := s.workerBroadcast(map[string]any{"op": "prune", "keep": keep, "statuses": statuses})
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "removed": resp.Removed})
}

// updateJob retunes a queued or running job in place. The worker decides which
// fields are safe to change mid-render and reports the rest as rejected.
func (s Server) updateJob(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req struct {
		ID     string         `json:"id"`
		Fields map[string]any `json:"fields"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	req.ID = strings.TrimSpace(req.ID)
	if req.ID == "" {
		writeError(w, http.StatusBadRequest, "job id is required")
		return
	}
	if len(req.Fields) == 0 {
		writeError(w, http.StatusBadRequest, "fields is required")
		return
	}
	result, err := s.workerUpdate(req.ID, req.Fields)
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, result)
}

func (s Server) cancelJob(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req jobActionRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	req.ID = strings.TrimSpace(req.ID)
	if req.ID == "" {
		writeError(w, http.StatusBadRequest, "job id is required")
		return
	}
	resp, err := s.workerBroadcast(map[string]any{"op": "cancel", "id": req.ID})
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "job": s.jobWithOutputURL(r, resp.Job)})
}

func (s Server) render(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req renderRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	iterations := req.Iterations
	if iterations <= 0 {
		iterations = 1
	}
	if iterations > 64 {
		iterations = 64
	}
	plans := make([]renderPlan, 0, iterations)
	for i := 0; i < iterations; i++ {
		runReq := req
		runReq.Iterations = 1
		runReq.Seed = iterationSeed(req.Seed, i)
		runReq.Filename = iterationFilename(req.Filename, i, iterations)
		plan, err := s.plan(runReq)
		if err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		plans = append(plans, plan)
	}
	if req.DryRun || truthy(r.URL.Query().Get("dry_run")) {
		body := map[string]any{"ok": true, "dry_run": true, "plan": plans[0], "plans": plans, "iterations": iterations}
		writeJSON(w, http.StatusOK, body)
		return
	}
	if err := s.workerStart(false); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	jobs := make([]map[string]any, 0, len(plans))
	for _, plan := range plans {
		// Dispatched per plan rather than per batch: each submit re-measures
		// which worker is idlest, so a multi-iteration render fans out across
		// the GPUs instead of queueing behind one.
		resp, err := s.workerDispatch(map[string]any{
			"op":           "submit",
			"model_family": plan.Model,
			"backend":      plan.Backend,
			"prompt":       plan.Prompt,
			"width":        plan.Width,
			"height":       plan.Height,
			"steps":        plan.Steps,
			"guidance":     plan.Guidance,
			"seed":         plan.Seed,
			"filename":     plan.Filename,
		})
		if err != nil {
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
		jobs = append(jobs, s.jobWithOutputURL(r, resp.Job))
		s.storeAtlasJobs([]map[string]any{resp.Job})
		s.storeAtlasSeed(plan.Seed, plan.Prompt, stringValue(resp.Job["id"]))
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "job": jobs[0], "jobs": jobs, "plan": plans[0], "plans": plans, "iterations": iterations})
}

func (s Server) previewAtlasSeeds(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req renderRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	plan, err := s.plan(req)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if err := s.workerStart(false); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	resp, err := s.workerDispatch(map[string]any{
		"op": "submit_seed_preview", "model_family": plan.Model, "backend": plan.Backend,
		"prompt": plan.Prompt, "width": plan.Width, "height": plan.Height,
		"steps": plan.Steps, "guidance": plan.Guidance, "seed": plan.Seed, "filename": plan.Filename,
		"latent_distance": req.LatentDistance,
	})
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	jobID := stringValue(resp.Job["id"])
	previewDraft := map[string]any{
		"id": jobID, "kind": "flux.motion_atlas.preview", "prompt": plan.Prompt,
		"latent_distance": req.LatentDistance, "batch_plan": []int{32},
	}
	previewPlan := map[string]any{
		"backend": plan.Backend, "width": plan.Width, "height": plan.Height,
		"steps": plan.Steps, "guidance": plan.Guidance, "images_total": 32,
	}
	nexus := submitNexusReceipt(jobID, previewDraft, previewPlan)
	nexusAccepted, _ := nexus["ok"].(bool)
	atlasNexusReceipts.Store(jobID, nexusAccepted)
	s.storeAtlasReceipt(jobID, nexusAccepted, stringValue(nexus["status"]), nexus)
	s.storeAtlasJobs([]map[string]any{resp.Job})
	baseSeed, _ := strconv.ParseInt(plan.Seed, 10, 64)
	for i := 0; i < 32; i++ {
		s.storeAtlasSeed(strconv.FormatInt(baseSeed+int64(i), 10), plan.Prompt, stringValue(resp.Job["id"]))
	}
	writeJSON(w, http.StatusAccepted, map[string]any{
		"ok": true, "job": s.jobWithOutputURL(r, resp.Job),
		"batch_plan": []int{32}, "images_total": 32, "nexus": nexus,
	})
}

func (s Server) submitAtlas(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req atlasSubmitRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	req.Prompt = strings.TrimSpace(req.Prompt)
	if req.Prompt == "" {
		writeError(w, http.StatusBadRequest, "atlas prompt is required")
		return
	}
	renderCount := clampInt(req.Cells, 1, 65536, 64)
	batchSize := clampInt(req.BatchSize, 1, 64, 1)
	rows, cols := 1024, 64
	latentCells := rows * cols
	studyType := atlasChoice(req.StudyType, []string{"loop", "atlas"}, "")
	if studyType == "" {
		writeError(w, http.StatusBadRequest, "study_type is required: choose loop or atlas")
		return
	}
	runType := atlasChoice(req.RunType, []string{"spot", "fill", "path"}, "spot")
	sampleMode := atlasChoice(req.SampleMode, []string{"loop", "nested_sparse", "sparse", "contiguous", "stride", "even", "smooth_even"}, "nested_sparse")
	if sampleMode == "sparse" {
		sampleMode = "nested_sparse"
	}
	// Preserve compatibility with older clients without preserving the old
	// temporally discontinuous ordering. Uniform sphere sampling is always smooth.
	if sampleMode == "even" || sampleMode == "stride" {
		sampleMode = "smooth_even"
	}
	indexStart := 0
	indexEnd := latentCells
	if runType == "path" {
		sampleMode = "contiguous"
		indexStart = clampInt(req.IndexStart, 0, latentCells-1, 0)
		indexEnd = clampInt(req.IndexEnd, indexStart+1, latentCells, indexStart+renderCount)
		if indexEnd <= indexStart {
			indexEnd = clampInt(indexStart+renderCount, indexStart+1, latentCells, latentCells)
		}
	} else {
		req.IndexStart = 0
		req.IndexEnd = latentCells
	}
	if studyType == "loop" {
		sampleMode = "loop"
		indexStart = 0
		indexEnd = latentCells
	}
	size := clampInt(req.Size, 256, 1024, 512)
	steps := clampInt(req.Steps, 1, 120, 36)
	guidance := req.Guidance
	if guidance <= 0 {
		guidance = 4.4
	}
	if guidance > 20 {
		guidance = 20
	}
	shellScale := req.ShellScale
	if shellScale <= 0 {
		shellScale = 1.12
	}
	shellScale = clampFloat(shellScale, 0.01, 4.0)
	seedLock := req.SeedLock
	if seedLock <= 0 {
		seedLock = 0.28
	}
	seedLock = clampFloat(seedLock, 0.0, 0.95)
	shellCoupling := req.ShellCoupling
	if shellCoupling == 0 {
		shellCoupling = 0.92
	}
	shellCoupling = clampFloat(shellCoupling, -16.0, 16.0)
	mode := atlasChoice(req.Mode, []string{"elliptic", "omega", "sway", "oscillatory"}, "elliptic")
	backend := atlasChoice(req.Backend, []string{"auto", "cuda", "mps", "cpu"}, s.cfg.Backend)
	if backend == "" {
		backend = "auto"
	}
	order := atlasChoice(req.TraversalOrder, []string{"row_serpentine", "column_serpentine", "raster"}, "row_serpentine")
	adapter := atlasChoice(req.Adapter, []string{"none", "first-block-cache", "atlas-xframe-cache"}, "none")
	cacheThreshold := req.CacheThreshold
	if cacheThreshold <= 0 {
		cacheThreshold = 0.12
	}
	cacheThreshold = clampFloat(cacheThreshold, 0.0, 1.0)
	cacheDownsample := clampInt(req.CacheDownsample, 1, 64, 1)
	cacheWarmup := clampInt(req.CacheWarmup, 0, steps, 0)
	dimensionRates := []float64{0.32, 0.11, -0.09, 0.08, -0.06, 0.04}
	if len(req.DimensionRates) == 6 {
		for i, rate := range req.DimensionRates {
			dimensionRates[i] = clampFloat(rate, -2, 2)
		}
	}

	now := time.Now()
	id := safeAtlasID(req.ID)
	if id == "" {
		id = "spheremap_atlas_studio_" + now.Format("20060102-150405")
	}
	seedA := strings.TrimSpace(req.Seed)
	if seedA == "" || strings.EqualFold(seedA, "random") {
		seedA = strconv.FormatInt(now.UnixNano()%900000000+10000000, 10)
	}
	seedB := req.SeedB
	if seedB == 0 {
		seedB = now.UnixNano()%700000000 + 211
	}
	seedC := req.SeedC
	if seedC == 0 {
		seedC = (now.UnixNano()/7)%700000000 + 509
	}
	seedD := req.SeedD
	if seedD == 0 {
		seedD = (now.UnixNano()/13)%700000000 + 887
	}

	draft := map[string]any{
		"kind":            "latent_sphere_map",
		"id":              id,
		"instance_id":     id,
		"experiment_id":   "flux:studio-atlas",
		"subject":         "studio latent atlas",
		"label":           strings.ReplaceAll(strings.TrimPrefix(id, "spheremap_atlas_"), "_", " "),
		"prompt":          req.Prompt,
		"view_prompts":    []string{req.Prompt},
		"mode":            mode,
		"study_type":      studyType,
		"run_type":        runType,
		"sample_mode":     sampleMode,
		"traversal":       "spherical_outward",
		"traversal_order": order,
		"n_rows":          rows,
		"n_cols":          cols,
		"n_latent":        latentCells,
		"index_start":     indexStart,
		"index_end":       indexEnd,
		"render_count":    renderCount,
		"batch_size":      batchSize,
		"model":           "FLUX.1-dev",
		"precision":       "bf16",
		"size":            size,
		"steps":           steps,
		"guidance":        guidance,
		"seed_a":          seedA,
		"seed_b":          seedB,
		"seed_c":          seedC,
		"seed_d":          seedD,
		"shell_scale":     shellScale,
		"seed_lock":       seedLock,
		"shell_coupling":  shellCoupling,
		"rates":           dimensionRates,
		"offsets":         []float64{0.18, -0.12, 0.10, -0.08, 0.06, -0.04},
		"notes":           "Submitted from studio latent atlas controls. Prompt is passed as entered; no image-to-image path is used.",
	}
	plan := map[string]any{
		"id":               id,
		"model":            "FLUX.1 dev",
		"backend":          backend,
		"width":            size,
		"height":           size,
		"steps":            steps,
		"guidance":         guidance,
		"cells":            renderCount,
		"batch_size":       batchSize,
		"precision":        "BF16",
		"dimension_rates":  dimensionRates,
		"latent_cells":     latentCells,
		"grid":             fmt.Sprintf("%dx%d", rows, cols),
		"mode":             mode,
		"study_type":       studyType,
		"run_type":         runType,
		"sample_mode":      sampleMode,
		"traversal_order":  order,
		"index_start":      indexStart,
		"index_end":        indexEnd,
		"adapter":          adapter,
		"cache_threshold":  cacheThreshold,
		"cache_downsample": cacheDownsample,
		"cache_warmup":     cacheWarmup,
		"prompt":           req.Prompt,
	}
	viewer := publicBaseURL(r) + "/atlas/" + url.PathEscape(id)
	gallery := publicBaseURL(r) + "/gallery/atlas/" + url.PathEscape(id+".sphere")
	if req.DryRun || truthy(r.URL.Query().Get("dry_run")) {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":      true,
			"dry_run": true,
			"plan":    plan,
			"draft":   draft,
			"viewer":  viewer,
			"gallery": gallery,
		})
		return
	}
	if err := os.MkdirAll(filepath.Join(s.cfg.Root, "atlas_drafts"), 0o755); err == nil {
		if raw, marshalErr := json.MarshalIndent(draft, "", "  "); marshalErr == nil {
			_ = os.WriteFile(filepath.Join(s.cfg.Root, "atlas_drafts", id+".json"), append(raw, '\n'), 0o644)
		}
	}
	nexus := submitNexusReceipt(id, draft, plan)
	nexusAccepted := nexusReceiptVerified(id, nexus)
	atlasNexusReceipts.Store(id, nexusAccepted)
	s.storeAtlasReceipt(id, nexusAccepted, stringValue(nexus["status"]), nexus)
	if !nexusAccepted {
		slog.Warn("nexus receipt not verified, proceeding anyway", "job", id, "status", stringValue(nexus["status"]))
	}
	s.writeQueuedAtlasPlaceholder(id, draft, plan)
	if err := s.workerStart(false); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	atlasReq := map[string]any{
		"op":               "atlas_sphere",
		"id":               id,
		"draft":            draft,
		"backend":          backend,
		"render_count":     renderCount,
		"batch_size":       batchSize,
		"n_latent":         latentCells,
		"index_start":      indexStart,
		"index_end":        indexEnd,
		"sample_mode":      sampleMode,
		"steps":            steps,
		"size":             size,
		"guidance":         guidance,
		"traversal_order":  order,
		"adapter":          adapter,
		"cache_threshold":  cacheThreshold,
		"cache_downsample": cacheDownsample,
		"cache_warmup":     cacheWarmup,
	}

	// A sphere is the one job worth splitting: its cells are independent and
	// it is the long pole for the atlas UI. Every GPU renders an interleaved
	// slice into the same output directory.
	var atlasJob map[string]any
	if s.fleetOn() {
		merged, err := s.pool.SubmitAtlas(atlasReq)
		if err != nil {
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
		atlasJob = merged
	} else {
		resp, err := s.client.Request(atlasReq)
		if err != nil {
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
		atlasJob = resp.Job
	}

	job := s.jobWithOutputURL(r, atlasJob)
	if job != nil {
		job["socket_kind"] = "flux"
		job["viewer_url"] = viewer
		job["gallery_url"] = gallery
	}
	s.storeAtlasJobs([]map[string]any{atlasJob})
	s.storeAtlasSeed(seedA, req.Prompt, stringValue(atlasJob["id"]))
	writeJSON(w, http.StatusAccepted, map[string]any{
		"ok":      true,
		"job":     job,
		"plan":    plan,
		"draft":   draft,
		"viewer":  viewer,
		"gallery": gallery,
		"nexus":   nexus,
	})
}

func applyAtlasReceipts(jobs []map[string]any) {
	for _, job := range jobs {
		if accepted, ok := atlasNexusReceipts.Load(stringValue(job["id"])); ok {
			job["nexus_accepted"] = accepted
		}
	}
}

// nexusReachable dials NEXUS_ADDR without sending anything, for a cheap
// health-check signal distinct from actually submitting a job.
func nexusReachable() bool {
	address := strings.TrimSpace(os.Getenv("NEXUS_ADDR"))
	if address == "" {
		address = "127.0.0.1:9999"
	}
	conn, err := net.DialTimeout("tcp", address, 1*time.Second)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

// piperReachable dials the Piper asset-hub Unix socket without subscribing.
func piperReachable() bool {
	socketPath := strings.TrimSpace(os.Getenv("PIPER_SOCKET"))
	if socketPath == "" {
		socketPath = "/tmp/piper.sock"
	}
	conn, err := net.DialTimeout("unix", socketPath, 1*time.Second)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func submitNexusReceipt(id string, draft, plan map[string]any) map[string]any {
	address := strings.TrimSpace(os.Getenv("NEXUS_ADDR"))
	if address == "" {
		address = "127.0.0.1:9999"
	}
	conn, err := net.DialTimeout("tcp", address, 2*time.Second)
	if err != nil {
		return map[string]any{"ok": false, "status": "unavailable", "error": err.Error()}
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(3 * time.Second))
	payload := map[string]any{"type": "submit", "job": map[string]any{
		"id": id, "job_id": id, "kind": "flux.motion_atlas",
		"status": "queued", "execution_owner": "flux", "draft": draft, "plan": plan,
	}}
	raw, _ := json.Marshal(payload)
	if _, err := fmt.Fprintf(conn, "%s\n", raw); err != nil {
		return map[string]any{"ok": false, "status": "send-failed", "error": err.Error()}
	}
	var receipt map[string]any
	if err := json.NewDecoder(conn).Decode(&receipt); err != nil {
		return map[string]any{"ok": false, "status": "invalid-receipt", "error": err.Error()}
	}
	return receipt
}

func nexusReceiptVerified(jobID string, receipt map[string]any) bool {
	ok, _ := receipt["ok"].(bool)
	accepted, _ := receipt["accepted"].(bool)
	verified, _ := receipt["verified"].(bool)
	return ok && accepted && verified &&
		stringValue(receipt["status"]) == "accepted" &&
		stringValue(receipt["job_id"]) == jobID &&
		strings.TrimSpace(stringValue(receipt["receipt_id"])) != ""
}

func (s Server) writeQueuedAtlasPlaceholder(id string, draft, plan map[string]any) {
	if id == "" {
		return
	}
	outDir := filepath.Join(s.cfg.OutputDir, "atlas", id+".sphere")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		slog.Warn("atlas placeholder directory unavailable", "id", id, "error", err)
		return
	}
	manifestPath := filepath.Join(outDir, "manifest.json")
	if _, err := os.Stat(manifestPath); err == nil {
		return
	}
	now := time.Now()
	manifest := map[string]any{
		"kind":            "atlas_sphere",
		"job_id":          id,
		"subject":         draft["subject"],
		"prompt":          draft["prompt"],
		"status":          "queued",
		"queued":          now.Unix(),
		"n_rows":          draft["n_rows"],
		"n_cols":          draft["n_cols"],
		"n_latent":        draft["n_latent"],
		"index_start":     draft["index_start"],
		"index_end":       draft["index_end"],
		"render_count":    draft["render_count"],
		"render_total":    plan["cells"],
		"rendered":        0,
		"size":            draft["size"],
		"steps":           draft["steps"],
		"guidance":        draft["guidance"],
		"mode":            draft["mode"],
		"run_type":        draft["run_type"],
		"sample_mode":     draft["sample_mode"],
		"traversal":       draft["traversal"],
		"traversal_order": draft["traversal_order"],
		"shell_scale":     draft["shell_scale"],
		"shell_coupling":  draft["shell_coupling"],
		"seed_lock":       draft["seed_lock"],
	}
	if raw, err := json.MarshalIndent(manifest, "", "  "); err == nil {
		_ = os.WriteFile(manifestPath, append(raw, '\n'), 0o644)
	}
	progress := map[string]any{
		"job_id":  id,
		"current": 0,
		"total":   plan["cells"],
		"status":  "queued",
		"ts":      now.Unix(),
	}
	if raw, err := json.MarshalIndent(progress, "", "  "); err == nil {
		_ = os.WriteFile(filepath.Join(outDir, "progress.json"), append(raw, '\n'), 0o644)
	}
}

func (s Server) img2img(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req img2imgRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	promptText := strings.TrimSpace(req.Prompt)
	if promptText == "" {
		writeError(w, http.StatusBadRequest, "prompt is required")
		return
	}
	refs, err := s.resolveImg2ImgReferences(req)
	if err != nil {
		writeError(w, http.StatusBadRequest, err.Error())
		return
	}
	if len(refs) == 0 {
		writeError(w, http.StatusBadRequest, "add at least one identity, posture, backdrop, or source image")
		return
	}
	conditioningRef := chooseConditioningReference(refs)
	imagePath := conditioningRef.Path
	primaryImage := conditioningRef.Path
	identityImage := referencePath(refs, "identity")
	postureImage := referencePath(refs, "posture")
	backdropImage := referencePath(refs, "backdrop")
	image2Path := referencePath(refs, "style")
	conditioning := "single conditioning image: " + conditioningRef.Label
	blendImage := ""
	if identityImage != "" && postureImage != "" {
		blend, err := s.buildFacePlacementBlend(identityImage, postureImage)
		if err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		blendImage = blend.Path
		imagePath = blend.Path
		primaryImage = postureImage
		conditioning = "face placement blend: identity face over posture"
		if req.Width <= 0 || req.Height <= 0 {
			req.Width = blend.OutputWidth
			req.Height = blend.OutputHeight
		}
	}
	if req.Width <= 0 || req.Height <= 0 {
		if width, height, ok := imageDimensions(imagePath); ok {
			req.Width = width
			req.Height = height
		}
	}
	backend := strings.ToLower(strings.TrimSpace(req.Backend))
	if backend == "" {
		backend = "auto"
	}
	if backend != "auto" && backend != "mps" && backend != "cpu" {
		writeError(w, http.StatusBadRequest, "img2img backend must be auto, mps, or cpu")
		return
	}
	steps := req.Steps
	if steps <= 0 {
		steps = 28
	}
	if steps > 120 {
		steps = 120
	}
	guidance := req.Guidance
	if guidance <= 0 {
		guidance = 3.5
	}
	strength := req.Strength
	if strength <= 0 {
		strength = 0.55
	}
	if strength >= 1 {
		strength = 0.99
	}
	effectiveSteps := int(float64(steps)*strength + 0.999999)
	if effectiveSteps < 1 {
		effectiveSteps = 1
	}
	if effectiveSteps > steps {
		effectiveSteps = steps
	}
	plan := map[string]any{
		"prompt":          promptText,
		"image":           imagePath,
		"primary":         primaryImage,
		"image2":          image2Path,
		"identity":        identityImage,
		"posture":         postureImage,
		"backdrop":        backdropImage,
		"blend":           blendImage,
		"conditioning":    conditioning,
		"backend":         backend,
		"width":           req.Width,
		"height":          req.Height,
		"steps":           steps,
		"effective_steps": effectiveSteps,
		"guidance":        guidance,
		"strength":        strength,
		"seed":            req.Seed,
		"filename":        req.Filename,
		"socket":          filepath.Join(s.cfg.Root, ".fluxd", "img2img.sock"),
	}
	if req.DryRun || truthy(r.URL.Query().Get("dry_run")) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "dry_run": true, "plan": plan})
		return
	}
	client := daemon.NewNamed(s.cfg, "img2img")
	if err := client.Start(false); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	resp, err := client.Request(map[string]any{
		"op":             "submit_img2img",
		"backend":        backend,
		"prompt":         promptText,
		"image":          imagePath,
		"primary_image":  primaryImage,
		"image2":         image2Path,
		"identity_image": identityImage,
		"posture_image":  postureImage,
		"backdrop_image": backdropImage,
		"blend_image":    blendImage,
		"conditioning":   conditioning,
		"width":          req.Width,
		"height":         req.Height,
		"steps":          steps,
		"guidance":       guidance,
		"strength":       strength,
		"seed":           req.Seed,
		"filename":       req.Filename,
	})
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	job := s.jobWithOutputURL(r, resp.Job)
	job["socket_kind"] = "img2img"
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "job": job, "plan": plan})
}

func (s Server) img2imgJobs(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	resp, err := daemon.NewNamed(s.cfg, "img2img").Request(map[string]any{"op": "jobs"})
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "worker_running": false, "jobs": []any{}, "worker_error": err.Error()})
		return
	}
	jobs := s.jobsWithOutputURLs(r, dashboardJobs(resp.Jobs))
	for _, job := range jobs {
		job["socket_kind"] = "img2img"
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "worker_running": true, "jobs": jobs})
}

func (s Server) img2imgEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	header := w.Header()
	header.Set("Content-Type", "text/event-stream")
	header.Set("Cache-Control", "no-cache")
	header.Set("Connection", "keep-alive")
	header.Set("X-Accel-Buffering", "no")

	client := daemon.NewNamed(s.cfg, "img2img")
	_, statePath, _, _ := client.Paths()
	send := func() bool {
		resp, err := client.Request(map[string]any{"op": "jobs"})
		body := map[string]any{"ok": true, "worker_running": true, "jobs": []any{}}
		if err != nil {
			body["worker_running"] = false
			body["worker_error"] = err.Error()
		} else {
			jobs := s.jobsWithOutputURLs(r, dashboardJobs(resp.Jobs))
			for _, job := range jobs {
				job["socket_kind"] = "img2img"
			}
			body["jobs"] = jobs
		}
		data, err := json.Marshal(body)
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "event: jobs\ndata: %s\n\n", data); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}
	if !send() {
		return
	}
	for {
		if !waitForPathChange(r.Context(), statePath) {
			return
		}
		if !send() {
			return
		}
	}
}

func (s Server) img2imgWarm(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	preload := truthy(r.URL.Query().Get("preload"))
	if err := daemon.NewNamed(s.cfg, "img2img").Start(preload); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "preload": preload, "socket": filepath.Join(s.cfg.Root, ".fluxd", "img2img.sock")})
}

func (s Server) img2imgCancel(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req jobActionRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	req.ID = strings.TrimSpace(req.ID)
	if req.ID == "" {
		writeError(w, http.StatusBadRequest, "job id is required")
		return
	}
	resp, err := daemon.NewNamed(s.cfg, "img2img").Request(map[string]any{"op": "cancel", "id": req.ID})
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	job := s.jobWithOutputURL(r, resp.Job)
	job["socket_kind"] = "img2img"
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "job": job})
}

func (s Server) blendImages(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req blendRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	items := make([]blendImageInput, 0, len(req.Images))
	for _, item := range req.Images {
		if strings.TrimSpace(item.Image) == "" {
			continue
		}
		resolved, ok := s.resolveImageInput(item.Image)
		if !ok {
			writeError(w, http.StatusBadRequest, "blend images must be local paths or /outputs URLs")
			return
		}
		item.Image = resolved
		if item.Weight <= 0 {
			item.Weight = 1
		}
		if item.Weight > 1 {
			item.Weight = 1
		}
		item.Part = strings.ToLower(strings.TrimSpace(item.Part))
		if item.Part == "" {
			item.Part = "full"
		}
		if strings.TrimSpace(item.Label) == "" {
			item.Label = item.Part
		}
		items = append(items, item)
	}
	if len(items) < 2 {
		writeError(w, http.StatusBadRequest, "blend needs at least two images")
		return
	}
	mode := strings.ToLower(strings.TrimSpace(req.Mode))
	if mode == "" {
		mode = "normal"
	}
	ref, err := s.buildWeightedImageBlend(items, req.Width, req.Height, req.Name, mode)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":            true,
		"path":          ref.Path,
		"url":           s.imagePreviewURL(r, ref.Path),
		"output_width":  ref.OutputWidth,
		"output_height": ref.OutputHeight,
		"mode":          mode,
		"items":         items,
	})
}

func (s Server) uploadImage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, 80<<20)
	if err := r.ParseMultipartForm(80 << 20); err != nil {
		writeError(w, http.StatusBadRequest, "invalid image upload")
		return
	}
	file, header, err := r.FormFile("image")
	if err != nil {
		writeError(w, http.StatusBadRequest, "image file is required")
		return
	}
	defer file.Close()
	name := safeUploadName(header.Filename)
	if !isImageName(name) {
		writeError(w, http.StatusBadRequest, "upload must be png, jpg, jpeg, or webp")
		return
	}
	dir := filepath.Join(s.cfg.Root, ".fluxd", "uploads")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	target := filepath.Join(dir, time.Now().Format("20060102-150405")+"-"+name)
	out, err := os.OpenFile(target, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	defer out.Close()
	if _, err := io.Copy(out, file); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":   true,
		"path": target,
		"name": name,
		"size": header.Size,
	})
}

func safeUploadName(name string) string {
	name = filepath.Base(strings.TrimSpace(name))
	if name == "" || name == "." {
		name = "image.png"
	}
	var b strings.Builder
	for _, r := range name {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '.', r == '-', r == '_':
			b.WriteRune(r)
		default:
			b.WriteByte('-')
		}
	}
	cleaned := strings.Trim(b.String(), ".-")
	if cleaned == "" {
		return "image.png"
	}
	return cleaned
}

func safeAtlasID(value string) string {
	value = strings.TrimSpace(value)
	if value == "" {
		return ""
	}
	value = strings.ReplaceAll(value, ".", "-")
	cleaned := strings.Trim(safeUploadName(value), ".-")
	cleaned = strings.TrimSuffix(cleaned, "-json")
	if cleaned == "" || cleaned == "image-png" {
		return ""
	}
	return cleaned
}

func atlasGrid(cells int) (int, int) {
	switch {
	case cells <= 16:
		return 4, 4
	case cells <= 64:
		return 8, 8
	case cells <= 128:
		return 8, 16
	default:
		return 16, 16
	}
}

func atlasChoice(value string, allowed []string, fallback string) string {
	value = strings.ToLower(strings.TrimSpace(value))
	value = strings.ReplaceAll(value, "_", "-")
	for _, item := range allowed {
		normalized := strings.ReplaceAll(item, "_", "-")
		if value == normalized {
			return item
		}
	}
	return fallback
}

func clampInt(value, min, max, fallback int) int {
	if value == 0 {
		value = fallback
	}
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func clampFloat(value, min, max float64) float64 {
	if value < min {
		return min
	}
	if value > max {
		return max
	}
	return value
}

func (s Server) resolveImageInput(value string) (string, bool) {
	value = strings.TrimSpace(value)
	if value == "" {
		return "", false
	}
	if parsed, err := url.Parse(value); err == nil {
		if parsed.Scheme == "http" || parsed.Scheme == "https" {
			value = parsed.Path
		}
	}
	if strings.HasPrefix(value, "/outputs/") {
		rel := strings.TrimPrefix(value, "/outputs/")
		rel, err := url.PathUnescape(rel)
		if err != nil {
			return "", false
		}
		return s.resolveOutputImageRel(rel)
	}
	if strings.HasPrefix(value, "outputs/") {
		return s.resolveOutputImageRel(strings.TrimPrefix(value, "outputs/"))
	}
	if strings.HasPrefix(value, "~/") {
		if home, err := os.UserHomeDir(); err == nil {
			value = filepath.Join(home, strings.TrimPrefix(value, "~/"))
		}
	}
	abs, err := filepath.Abs(value)
	if err != nil {
		return "", false
	}
	if info, err := os.Stat(abs); err == nil && !info.IsDir() && isImageName(abs) {
		return abs, true
	}
	return "", false
}

func (s Server) resolveOutputImageRel(rel string) (string, bool) {
	rel = strings.TrimPrefix(path.Clean("/"+filepath.ToSlash(rel)), "/")
	if rel == "" || rel == "." || strings.HasPrefix(rel, "../") || !isImageName(rel) {
		return "", false
	}
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err != nil {
		return "", false
	}
	abs, err := filepath.Abs(filepath.Join(outputDir, filepath.FromSlash(rel)))
	if err != nil || !pathInside(outputDir, abs) {
		return "", false
	}
	if info, err := os.Stat(abs); err == nil && !info.IsDir() {
		return abs, true
	}
	return "", false
}

type stitchedReference struct {
	Path         string `json:"path"`
	OutputWidth  int    `json:"output_width"`
	OutputHeight int    `json:"output_height"`
}

type imageReference struct {
	Role  string `json:"role"`
	Label string `json:"label"`
	Path  string `json:"path"`
}

func (s Server) resolveImg2ImgReferences(req img2imgRequest) ([]imageReference, error) {
	type candidate struct {
		role  string
		label string
		value string
	}
	candidates := []candidate{
		{"identity", "identity face", req.IdentityImage},
		{"posture", "posture", req.PostureImage},
		{"backdrop", "backdrop", req.BackdropImage},
	}
	if strings.TrimSpace(req.Image) != "" {
		candidates = append(candidates, candidate{"source", "source", req.Image})
	}
	if strings.TrimSpace(req.Image2) != "" {
		candidates = append(candidates, candidate{"style", "style", req.Image2})
	}
	refs := make([]imageReference, 0, len(candidates))
	seen := make(map[string]bool)
	for _, item := range candidates {
		if strings.TrimSpace(item.value) == "" {
			continue
		}
		resolved, ok := s.resolveImageInput(item.value)
		if !ok {
			return nil, fmt.Errorf("%s image must be a local path or /outputs URL", item.label)
		}
		if seen[resolved] {
			continue
		}
		seen[resolved] = true
		refs = append(refs, imageReference{Role: item.role, Label: item.label, Path: resolved})
	}
	return refs, nil
}

func referencePath(refs []imageReference, role string) string {
	for _, ref := range refs {
		if ref.Role == role {
			return ref.Path
		}
	}
	return ""
}

func chooseConditioningReference(refs []imageReference) imageReference {
	for _, role := range []string{"posture", "source", "identity", "backdrop", "style"} {
		for _, ref := range refs {
			if ref.Role == role {
				return ref
			}
		}
	}
	return refs[0]
}

func imageDimensions(imagePath string) (int, int, bool) {
	f, err := os.Open(imagePath)
	if err != nil {
		return 0, 0, false
	}
	defer f.Close()
	cfg, _, err := image.DecodeConfig(f)
	if err != nil || cfg.Width <= 0 || cfg.Height <= 0 {
		return 0, 0, false
	}
	return cfg.Width, cfg.Height, true
}

func (s Server) buildFacePlacementBlend(identityPath, posturePath string) (stitchedReference, error) {
	outDir := filepath.Join(s.cfg.Root, ".fluxd", "blends")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return stitchedReference{}, err
	}
	outPath := filepath.Join(outDir, "face-blend-"+time.Now().Format("20060102-150405")+"-"+strconv.FormatInt(time.Now().UnixNano()%1000000, 10)+".png")
	script := `
import json, sys
from PIL import Image, ImageOps
import cv2
import numpy as np

identity_path, posture_path, out_path = sys.argv[1:4]
identity = ImageOps.exif_transpose(Image.open(identity_path).convert("RGB"))
posture = ImageOps.exif_transpose(Image.open(posture_path).convert("RGB"))
identity_np = np.array(identity)
posture_np = np.array(posture)

cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

def largest_face(img_np):
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
    faces = cascade.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=4, minSize=(32, 32))
    if len(faces) == 0:
        return None
    return max(faces, key=lambda f: int(f[2]) * int(f[3]))

src_face = largest_face(identity_np)
if src_face is None:
    sx, sy, sw, sh = 0, 0, identity_np.shape[1], identity_np.shape[0]
else:
    sx, sy, sw, sh = [int(v) for v in src_face]

margin_x = int(sw * 0.34)
margin_y_top = int(sh * 0.46)
margin_y_bottom = int(sh * 0.34)
x0 = max(0, sx - margin_x)
y0 = max(0, sy - margin_y_top)
x1 = min(identity_np.shape[1], sx + sw + margin_x)
y1 = min(identity_np.shape[0], sy + sh + margin_y_bottom)
face_crop = identity_np[y0:y1, x0:x1]

dst_face = largest_face(posture_np)
if dst_face is None:
    # Fallback assumes portrait framing: upper-center head region.
    pw, ph = posture_np.shape[1], posture_np.shape[0]
    tw = max(48, int(pw * 0.22))
    th = max(48, int(tw * 1.18))
    tx = int((pw - tw) / 2)
    ty = int(ph * 0.16)
else:
    tx, ty, tw, th = [int(v) for v in dst_face]
    tx -= int(tw * 0.28)
    ty -= int(th * 0.42)
    tw = int(tw * 1.56)
    th = int(th * 1.52)

tx = max(0, min(tx, posture_np.shape[1] - 1))
ty = max(0, min(ty, posture_np.shape[0] - 1))
tw = max(1, min(tw, posture_np.shape[1] - tx))
th = max(1, min(th, posture_np.shape[0] - ty))
resized = cv2.resize(face_crop, (tw, th), interpolation=cv2.INTER_LANCZOS4)
target = posture_np[ty:ty+th, tx:tx+tw].astype(np.float32)
src = resized.astype(np.float32)

# Match rough color/contrast to the target face area.
for c in range(3):
    s_mean, s_std = src[:, :, c].mean(), src[:, :, c].std()
    t_mean, t_std = target[:, :, c].mean(), target[:, :, c].std()
    if s_std > 1:
        src[:, :, c] = (src[:, :, c] - s_mean) * (t_std / s_std) + t_mean
src = np.clip(src, 0, 255).astype(np.uint8)

mask = np.zeros((th, tw), dtype=np.float32)
cv2.ellipse(mask, (tw // 2, th // 2), (max(1, int(tw * 0.46)), max(1, int(th * 0.48))), 0, 0, 360, 1.0, -1)
blur = max(11, (min(tw, th) // 6) | 1)
mask = cv2.GaussianBlur(mask, (blur, blur), 0)
mask = np.clip(mask[:, :, None], 0, 1)

out = posture_np.copy().astype(np.float32)
out_region = out[ty:ty+th, tx:tx+tw]
out[ty:ty+th, tx:tx+tw] = src.astype(np.float32) * mask + out_region * (1 - mask)
out = np.clip(out, 0, 255).astype(np.uint8)
Image.fromarray(out).save(out_path)
print(json.dumps({"path": out_path, "output_width": posture_np.shape[1], "output_height": posture_np.shape[0], "target": [tx, ty, tw, th], "detected": dst_face is not None}))
`
	out, err := exec.Command(s.cfg.Python, "-c", script, identityPath, posturePath, outPath).CombinedOutput()
	if err != nil {
		return stitchedReference{}, fmt.Errorf("face placement blend failed: %w: %s", err, strings.TrimSpace(string(out)))
	}
	var ref stitchedReference
	if err := json.Unmarshal(bytes.TrimSpace(out), &ref); err != nil {
		return stitchedReference{}, err
	}
	if ref.Path == "" {
		ref.Path = outPath
	}
	return ref, nil
}

func (s Server) buildWeightedImageBlend(items []blendImageInput, width, height int, name, mode string) (stitchedReference, error) {
	outDir := filepath.Join(s.cfg.Root, ".fluxd", "blends")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return stitchedReference{}, err
	}
	stem := "weighted-blend"
	if strings.TrimSpace(name) != "" {
		stem = strings.TrimSuffix(safeUploadName(name), filepath.Ext(name))
	}
	outPath := filepath.Join(outDir, stem+"-"+time.Now().Format("20060102-150405")+"-"+strconv.FormatInt(time.Now().UnixNano()%1000000, 10)+".png")
	script := `
import json, sys
from PIL import Image, ImageOps, ImageFilter
import numpy as np

out_path = sys.argv[1]
items = json.loads(sys.argv[2])
width = int(sys.argv[3])
height = int(sys.argv[4])
mode = (sys.argv[5] or "normal").lower()

loaded = []
for item in items:
    img = ImageOps.exif_transpose(Image.open(item["image"]).convert("RGB"))
    loaded.append((item, img))

if width <= 0 or height <= 0:
    width, height = loaded[0][1].size

def cover(img, w, h):
    scale = max(w / img.width, h / img.height)
    rw, rh = max(1, round(img.width * scale)), max(1, round(img.height * scale))
    img = img.resize((rw, rh), Image.Resampling.LANCZOS)
    x = max(0, (rw - w) // 2)
    y = max(0, (rh - h) // 2)
    return img.crop((x, y, x + w, y + h))

def region_mask(part, w, h):
    part = (part or "full").lower()
    y, x = np.mgrid[0:h, 0:w]
    mask = np.ones((h, w), dtype=np.float32)
    if part == "center":
        cx, cy = w * .5, h * .5
        rx, ry = max(1, w * .38), max(1, h * .38)
        mask = np.clip(1 - (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2), 0, 1)
    elif part in ("face", "portrait"):
        cx, cy = w * .5, h * .34
        rx, ry = max(1, w * .24), max(1, h * .24)
        mask = np.clip(1 - (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2), 0, 1)
    elif part == "subject":
        cx, cy = w * .5, h * .48
        rx, ry = max(1, w * .30), max(1, h * .48)
        mask = np.clip(1 - (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2), 0, 1)
    elif part == "top":
        mask = np.clip(1 - y / max(1, h * .62), 0, 1)
    elif part == "bottom":
        mask = np.clip(y / max(1, h * .62) - .35, 0, 1)
    elif part == "left":
        mask = np.clip(1 - x / max(1, w * .62), 0, 1)
    elif part == "right":
        mask = np.clip(x / max(1, w * .62) - .35, 0, 1)
    elif part == "edges":
        cx, cy = w * .5, h * .5
        rx, ry = max(1, w * .36), max(1, h * .36)
        center = np.clip(1 - (((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2), 0, 1)
        mask = 1 - center
    mask_img = Image.fromarray(np.uint8(np.clip(mask, 0, 1) * 255), "L").filter(ImageFilter.GaussianBlur(max(8, min(w, h) // 18)))
    return np.asarray(mask_img).astype(np.float32) / 255.0

def apply_mode(base, src, mode):
    if mode == "multiply":
        return base * src
    if mode == "screen":
        return 1 - (1 - base) * (1 - src)
    if mode == "overlay":
        return np.where(base < .5, 2 * base * src, 1 - 2 * (1 - base) * (1 - src))
    if mode in ("soft", "soft-light", "soft_light"):
        return (1 - 2 * src) * base * base + 2 * src * base
    if mode in ("luminosity", "value"):
        luma = (src[:, :, 0] * .2126 + src[:, :, 1] * .7152 + src[:, :, 2] * .0722)[:, :, None]
        mean = np.maximum(.08, base.mean(axis=2, keepdims=True))
        return np.clip(base * (luma / mean), 0, 1)
    return src

base = np.asarray(cover(loaded[0][1], width, height)).astype(np.float32) / 255.0
for item, img in loaded[1:]:
    src = np.asarray(cover(img, width, height)).astype(np.float32) / 255.0
    weight = float(item.get("weight") or 1)
    weight = max(0.0, min(1.0, weight))
    mask = region_mask(item.get("part") or "full", width, height)[:, :, None] * weight
    blended = apply_mode(base, src, mode)
    base = np.clip(blended * mask + base * (1 - mask), 0, 1)

out = Image.fromarray(np.uint8(np.clip(base, 0, 1) * 255))
out.save(out_path)
print(json.dumps({"path": out_path, "output_width": width, "output_height": height}))
`
	payload, err := json.Marshal(items)
	if err != nil {
		return stitchedReference{}, err
	}
	if width < 0 {
		width = 0
	}
	if height < 0 {
		height = 0
	}
	out, err := exec.Command(s.cfg.Python, "-c", script, outPath, string(payload), strconv.Itoa(width), strconv.Itoa(height), mode).CombinedOutput()
	if err != nil {
		return stitchedReference{}, fmt.Errorf("weighted blend failed: %w: %s", err, strings.TrimSpace(string(out)))
	}
	var ref stitchedReference
	if err := json.Unmarshal(bytes.TrimSpace(out), &ref); err != nil {
		return stitchedReference{}, err
	}
	if ref.Path == "" {
		ref.Path = outPath
	}
	return ref, nil
}

func referenceLabels(refs []imageReference) []string {
	labels := make([]string, 0, len(refs))
	for _, ref := range refs {
		labels = append(labels, ref.Label)
	}
	return labels
}

func (s Server) buildReferenceBoard(refs []imageReference) (stitchedReference, error) {
	outDir := filepath.Join(s.cfg.Root, ".fluxd", "references")
	if err := os.MkdirAll(outDir, 0o755); err != nil {
		return stitchedReference{}, err
	}
	outPath := filepath.Join(outDir, "lanes-"+time.Now().Format("20060102-150405")+"-"+strconv.FormatInt(time.Now().UnixNano()%1000000, 10)+".png")
	script := `
import json, sys
from PIL import Image, ImageOps

out_path = sys.argv[1]
items = json.loads(sys.argv[2])
images = []
for item in items:
    img = ImageOps.exif_transpose(Image.open(item["path"]).convert("RGB"))
    images.append((item, img))
target_h = max(img.height for _, img in images)

def fit_height(img, h):
    if img.height == h:
        return img
    w = max(1, round(img.width * (h / img.height)))
    return img.resize((w, h), Image.Resampling.LANCZOS)

fitted = [(item, fit_height(img, target_h)) for item, img in images]
pad = max(16, target_h // 24)
board_w = sum(img.width for _, img in fitted) + pad * (len(fitted) - 1)
board = Image.new("RGB", (board_w, target_h), (8, 9, 14))
x = 0
for _, img in fitted:
    board.paste(img, (x, 0))
    x += img.width + pad
board.save(out_path)
dim_source = next((img for item, img in images if item["role"] == "posture"), images[0][1])
print(json.dumps({"path": out_path, "output_width": dim_source.width, "output_height": dim_source.height}))
`
	payload, err := json.Marshal(refs)
	if err != nil {
		return stitchedReference{}, err
	}
	out, err := exec.Command(s.cfg.Python, "-c", script, outPath, string(payload)).CombinedOutput()
	if err != nil {
		return stitchedReference{}, fmt.Errorf("reference board failed: %w: %s", err, strings.TrimSpace(string(out)))
	}
	var ref stitchedReference
	if err := json.Unmarshal(bytes.TrimSpace(out), &ref); err != nil {
		return stitchedReference{}, err
	}
	if ref.Path == "" {
		ref.Path = outPath
	}
	return ref, nil
}

func (s Server) output(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet, http.MethodHead)
		return
	}
	name := strings.TrimPrefix(r.URL.Path, "/outputs/")
	name = path.Clean("/" + name)
	name = strings.TrimPrefix(name, "/")
	if name == "" || name == "." || strings.HasPrefix(name, "../") {
		writeError(w, http.StatusBadRequest, "invalid output path")
		return
	}
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	filePath := filepath.Join(outputDir, name)
	fileAbs, err := filepath.Abs(filePath)
	if err != nil || !pathInside(outputDir, fileAbs) {
		writeError(w, http.StatusBadRequest, "invalid output path")
		return
	}
	http.ServeFile(w, r, fileAbs)
}

func (s Server) staged(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet, http.MethodHead)
		return
	}
	rel := strings.TrimPrefix(r.URL.Path, "/staged/")
	rel = strings.TrimPrefix(path.Clean("/"+rel), "/")
	if rel == "" || rel == "." || strings.HasPrefix(rel, "../") || !isImageName(rel) {
		writeError(w, http.StatusBadRequest, "invalid staged path")
		return
	}
	first := strings.Split(rel, "/")[0]
	if first != "uploads" && first != "references" && first != "blends" {
		writeError(w, http.StatusBadRequest, "invalid staged path")
		return
	}
	stateRoot, err := filepath.Abs(filepath.Join(s.cfg.Root, ".fluxd"))
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	fileAbs, err := filepath.Abs(filepath.Join(stateRoot, filepath.FromSlash(rel)))
	if err != nil || !pathInside(stateRoot, fileAbs) {
		writeError(w, http.StatusBadRequest, "invalid staged path")
		return
	}
	http.ServeFile(w, r, fileAbs)
}

func (s Server) collections(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	s.syncOneOffRenders()
	items := make([]map[string]any, 0)
	for _, root := range []string{"batches", "atlas"} {
		dir := filepath.Join(s.cfg.OutputDir, root)
		entries, err := os.ReadDir(dir)
		if err != nil {
			continue
		}
		for _, entry := range entries {
			if !entry.IsDir() {
				continue
			}
			rel := filepath.ToSlash(filepath.Join(root, entry.Name()))
			if item := s.collectionSummary(r, rel); item != nil {
				items = append(items, item)
			}
		}
	}
	s.applyCollectionDB(items)
	sort.SliceStable(items, func(i, j int) bool {
		ip := intValue(items[i]["priority"])
		jp := intValue(items[j]["priority"])
		if ip != jp {
			return ip < jp
		}
		return floatValue(items[i]["updated"]) > floatValue(items[j]["updated"])
	})
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "collections": items})
}

func collectionSortPriority(rel string) int {
	base := path.Base(filepath.ToSlash(rel))
	if strings.HasPrefix(base, "one-off-renders") {
		return 0
	}
	return 10
}

func (s Server) openStudioDB() (*sql.DB, error) {
	stateDir := filepath.Join(s.cfg.Root, ".fluxd")
	if err := os.MkdirAll(stateDir, 0700); err != nil {
		return nil, err
	}
	dbPath := filepath.Join(stateDir, "studio.sqlite")
	studioSchemaState.Lock()
	defer studioSchemaState.Unlock()
	if db := studioSchemaState.dbs[dbPath]; db != nil {
		return db, nil
	}
	dsn := "file:" + filepath.ToSlash(dbPath) + "?_pragma=busy_timeout(10000)&_pragma=journal_mode(DELETE)&_pragma=synchronous(FULL)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, err
	}
	db.SetMaxIdleConns(1)
	db.SetMaxOpenConns(1)
	if _, err := db.Exec(`CREATE TABLE IF NOT EXISTS collections (
	path TEXT PRIMARY KEY,
	name TEXT NOT NULL,
	kind TEXT NOT NULL,
	priority INTEGER NOT NULL DEFAULT 10,
	pinned INTEGER NOT NULL DEFAULT 0,
	created_at INTEGER NOT NULL DEFAULT 0,
	updated_at INTEGER NOT NULL DEFAULT 0,
	last_seen_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS collection_picks (
	collection_path TEXT NOT NULL,
	image_name TEXT NOT NULL,
	image_url TEXT NOT NULL DEFAULT '',
	note TEXT NOT NULL DEFAULT '',
	created_at INTEGER NOT NULL DEFAULT 0,
	updated_at INTEGER NOT NULL DEFAULT 0,
	PRIMARY KEY(collection_path, image_name)
);
CREATE TABLE IF NOT EXISTS atlas_seeds (
	seed TEXT PRIMARY KEY,
	description TEXT NOT NULL DEFAULT '',
	source_job_id TEXT NOT NULL DEFAULT '',
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS atlas_jobs (
	id TEXT PRIMARY KEY,
	kind TEXT NOT NULL DEFAULT '',
	status TEXT NOT NULL DEFAULT '',
	phase TEXT NOT NULL DEFAULT '',
	prompt TEXT NOT NULL DEFAULT '',
	seed TEXT NOT NULL DEFAULT '',
	backend TEXT NOT NULL DEFAULT '',
	progress INTEGER NOT NULL DEFAULT 0,
	total INTEGER NOT NULL DEFAULT 0,
	payload_json TEXT NOT NULL DEFAULT '{}',
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS atlas_assets (
	id TEXT PRIMARY KEY,
	job_id TEXT NOT NULL DEFAULT '',
	seed TEXT NOT NULL DEFAULT '',
	path TEXT NOT NULL DEFAULT '',
	access_url TEXT NOT NULL DEFAULT '',
	media_type TEXT NOT NULL DEFAULT '',
	cell_index INTEGER NOT NULL DEFAULT -1,
	metadata_json TEXT NOT NULL DEFAULT '{}',
	created_at INTEGER NOT NULL,
	updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS atlas_receipts (
	job_id TEXT PRIMARY KEY,
	nexus_accepted INTEGER NOT NULL DEFAULT 0,
	status TEXT NOT NULL DEFAULT '',
	payload_json TEXT NOT NULL DEFAULT '{}',
	updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS atlas_jobs_status_idx ON atlas_jobs(status, updated_at);
CREATE INDEX IF NOT EXISTS atlas_assets_job_idx ON atlas_assets(job_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS atlas_assets_job_cell_idx ON atlas_assets(job_id, cell_index) WHERE cell_index >= 0;
CREATE INDEX IF NOT EXISTS atlas_seeds_updated_idx ON atlas_seeds(updated_at);
`); err != nil {
		_ = db.Close()
		return nil, err
	}
	studioSchemaState.dbs[dbPath] = db
	return db, nil
}

func (s Server) applyCollectionDB(items []map[string]any) {
	if len(items) == 0 {
		return
	}
	db, err := s.openStudioDB()
	if err != nil {
		slog.Warn("collection database unavailable", "error", err)
		return
	}

	now := time.Now().Unix()
	for _, item := range items {
		rel := stringValue(item["path"])
		if rel == "" {
			continue
		}
		name := stringValue(item["name"])
		kind := stringValue(item["kind"])
		updated := int64(floatValue(item["updated"]))
		priority := collectionSortPriority(rel)
		pinned := 0
		if priority == 0 {
			pinned = 1
		}
		_, err := db.Exec(`INSERT INTO collections(path, name, kind, priority, pinned, created_at, updated_at, last_seen_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(path) DO UPDATE SET
	name=excluded.name,
	kind=excluded.kind,
	updated_at=excluded.updated_at,
	last_seen_at=excluded.last_seen_at`,
			rel, name, kind, priority, pinned, updated, updated, now)
		if err != nil {
			slog.Warn("collection database upsert failed", "path", rel, "error", err)
			continue
		}
		if priority == 0 {
			_, _ = db.Exec(`UPDATE collections SET priority=0, pinned=1, name=? WHERE path=?`, name, rel)
		}

		var dbName, dbKind string
		var dbPriority, dbPinned int
		var dbCreated, dbUpdated, dbSeen int64
		err = db.QueryRow(`SELECT name, kind, priority, pinned, created_at, updated_at, last_seen_at FROM collections WHERE path=?`, rel).
			Scan(&dbName, &dbKind, &dbPriority, &dbPinned, &dbCreated, &dbUpdated, &dbSeen)
		if err != nil {
			slog.Warn("collection database read failed", "path", rel, "error", err)
			continue
		}
		if dbName != "" {
			item["name"] = dbName
		}
		if dbKind != "" {
			item["kind"] = dbKind
		}
		item["priority"] = dbPriority
		item["pinned"] = dbPinned == 1
		item["db_created"] = dbCreated
		item["db_updated"] = dbUpdated
		item["db_seen"] = dbSeen
	}
}

func (s Server) collection(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodDelete {
		s.deleteCollection(w, r)
		return
	}
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet, http.MethodDelete)
		return
	}
	rel, ok := s.cleanOutputRel(r.URL.Query().Get("path"))
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid collection path")
		return
	}
	if isOneOffCollectionRel(rel) {
		s.syncOneOffRenders()
	}
	payload, status, errMsg := s.collectionSnapshot(r, rel)
	if errMsg != "" {
		writeError(w, status, errMsg)
		return
	}
	writeJSON(w, status, payload)
}

func (s Server) collectionSnapshot(r *http.Request, rel string) (map[string]any, int, string) {
	base := filepath.Join(s.cfg.OutputDir, rel)
	baseAbs, err := filepath.Abs(base)
	if err != nil {
		return nil, http.StatusInternalServerError, err.Error()
	}
	info, err := os.Stat(baseAbs)
	if err != nil || !info.IsDir() {
		return nil, http.StatusNotFound, "collection not found"
	}
	rejects := loadCollectionRejects(baseAbs)
	picks := s.loadCollectionPicks(rel)
	images := make([]map[string]any, 0)
	_ = filepath.WalkDir(baseAbs, func(file string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if file != baseAbs && strings.HasPrefix(entry.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if !isMediaName(entry.Name()) {
			return nil
		}
		shortRel, err := filepath.Rel(baseAbs, file)
		if err != nil {
			return nil
		}
		shortName := filepath.ToSlash(shortRel)
		if rejects[shortName] {
			return nil
		}
		fileRel, err := filepath.Rel(s.cfg.OutputDir, file)
		if err != nil {
			return nil
		}
		info, _ := entry.Info()
		images = append(images, map[string]any{
			"name":     shortName,
			"type":     mediaType(shortName),
			"url":      publicOutputRelURL(r, fileRel),
			"size":     int64Value(info),
			"modified": unixValue(info),
			"picked":   picks[shortName],
		})
		return nil
	})
	sort.SliceStable(images, func(i, j int) bool {
		if strings.HasPrefix(path.Base(filepath.ToSlash(rel)), "one-off-renders") {
			return floatValue(images[i]["modified"]) < floatValue(images[j]["modified"])
		}
		return stringValue(images[i]["name"]) < stringValue(images[j]["name"])
	})
	summary := s.collectionSummary(r, rel)
	if summary == nil {
		summary = s.emptyCollectionSummary(r, rel, baseAbs, len(images))
	}
	if summary != nil {
		s.applyCollectionDB([]map[string]any{summary})
	}
	return map[string]any{
		"ok":         true,
		"collection": summary,
		"images":     images,
		"picks":      mapKeys(picks),
	}, http.StatusOK, ""
}

func (s Server) emptyCollectionSummary(r *http.Request, rel string, baseAbs string, count int) map[string]any {
	if info, err := os.Stat(baseAbs); err != nil || !info.IsDir() {
		return nil
	}
	total := count
	manifestPath := filepath.Join(baseAbs, "manifest.json")
	if raw, err := os.ReadFile(manifestPath); err == nil {
		var manifest map[string]any
		if json.Unmarshal(raw, &manifest) == nil {
			for _, key := range []string{"render_total", "render_count", "total"} {
				if value := intValue(manifest[key]); value > total {
					total = value
					break
				}
			}
		}
	}
	progressPath := filepath.Join(baseAbs, "progress.json")
	if raw, err := os.ReadFile(progressPath); err == nil {
		var progress map[string]any
		if json.Unmarshal(raw, &progress) == nil {
			if value := intValue(progress["total"]); value > total {
				total = value
			}
		}
	}
	info, _ := os.Stat(baseAbs)
	updated := time.Now()
	if info != nil {
		updated = info.ModTime()
	}
	kind := "collection"
	if strings.HasPrefix(rel, "batches/") {
		kind = "batch"
	}
	if strings.HasPrefix(rel, "atlas/") {
		kind = "atlas"
	}
	return map[string]any{
		"name":         humanCollectionName(rel),
		"raw_name":     path.Base(filepath.ToSlash(rel)),
		"path":         filepath.ToSlash(rel),
		"priority":     collectionSortPriority(rel),
		"kind":         kind,
		"count":        count,
		"total":        total,
		"updated":      updated.Unix(),
		"updated_text": updated.Format("Jan 2 15:04"),
		"thumbnail":    "",
		"url":          publicGalleryRelURL(r, rel),
		"raw_url":      publicOutputRelURL(r, rel),
	}
}

func cleanCollectionImageRel(value string) (string, bool) {
	value = filepath.ToSlash(strings.TrimSpace(value))
	if value == "" || strings.HasPrefix(value, "/") {
		return "", false
	}
	cleaned := path.Clean(value)
	if cleaned == "." || strings.HasPrefix(cleaned, "../") || strings.Contains(cleaned, "/../") {
		return "", false
	}
	if !isImageName(path.Base(cleaned)) {
		return "", false
	}
	return cleaned, true
}

func mapKeys(values map[string]bool) []string {
	keys := make([]string, 0, len(values))
	for key, enabled := range values {
		if enabled {
			keys = append(keys, key)
		}
	}
	sort.Strings(keys)
	return keys
}

func (s Server) loadCollectionPicks(rel string) map[string]bool {
	picks := make(map[string]bool)
	db, err := s.openStudioDB()
	if err != nil {
		slog.Warn("collection picks database unavailable", "error", err)
		return picks
	}
	rows, err := db.Query(`SELECT image_name FROM collection_picks WHERE collection_path=? ORDER BY created_at, image_name`, rel)
	if err != nil {
		slog.Warn("collection picks read failed", "path", rel, "error", err)
		return picks
	}
	defer rows.Close()
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err == nil {
			if cleaned, ok := cleanCollectionImageRel(name); ok {
				picks[cleaned] = true
			}
		}
	}
	return picks
}

func (s Server) collectionPicks(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req struct {
		Path  string   `json:"path"`
		Picks []string `json:"picks"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	rel, ok := s.cleanOutputRel(req.Path)
	if !ok || !(strings.HasPrefix(rel, "batches/") || strings.HasPrefix(rel, "atlas/")) {
		writeError(w, http.StatusBadRequest, "invalid collection path")
		return
	}
	baseAbs, err := filepath.Abs(filepath.Join(s.cfg.OutputDir, rel))
	if err != nil || !pathInside(s.cfg.OutputDir, baseAbs) {
		writeError(w, http.StatusBadRequest, "invalid collection path")
		return
	}
	info, err := os.Stat(baseAbs)
	if err != nil || !info.IsDir() {
		writeError(w, http.StatusNotFound, "collection not found")
		return
	}

	now := time.Now().Unix()
	cleaned := make([]string, 0, len(req.Picks))
	seen := make(map[string]bool)
	for _, raw := range req.Picks {
		name, ok := cleanCollectionImageRel(raw)
		if !ok || seen[name] {
			continue
		}
		imagePath := filepath.Join(baseAbs, filepath.FromSlash(name))
		imageAbs, err := filepath.Abs(imagePath)
		if err != nil || !pathInside(baseAbs, imageAbs) {
			continue
		}
		if info, err := os.Stat(imageAbs); err != nil || info.IsDir() {
			continue
		}
		seen[name] = true
		cleaned = append(cleaned, name)
	}

	db, err := s.openStudioDB()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	tx, err := db.Begin()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	_, err = tx.Exec(`DELETE FROM collection_picks WHERE collection_path=?`, rel)
	if err == nil {
		for _, name := range cleaned {
			fileRel := filepath.ToSlash(filepath.Join(rel, name))
			_, err = tx.Exec(`INSERT INTO collection_picks(collection_path, image_name, image_url, created_at, updated_at)
VALUES (?, ?, ?, ?, ?)`, rel, name, publicOutputRelURL(r, fileRel), now, now)
			if err != nil {
				break
			}
		}
	}
	if err != nil {
		_ = tx.Rollback()
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	if err := tx.Commit(); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":    true,
		"path":  rel,
		"picks": cleaned,
		"count": len(cleaned),
	})
}

func (s Server) recentImages(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	limit := intValue(r.URL.Query().Get("limit"))
	if limit <= 0 || limit > 200 {
		limit = 80
	}
	// Without an offset the gallery can only ever show the newest page, so a
	// long run buries almost all of its own work: 1,088 of 1,184 frames were
	// unreachable from the wall.
	offset := intValue(r.URL.Query().Get("offset"))
	if offset < 0 {
		offset = 0
	}
	scope := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("scope")))
	movementOnly := scope == "movement"
	type recentImage struct {
		Name     string
		Path     string
		URL      string
		Kind     string
		Modified int64
	}
	items := make([]recentImage, 0, limit)
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err == nil {
		_ = filepath.WalkDir(outputDir, func(file string, entry os.DirEntry, err error) error {
			if err != nil {
				return nil
			}
			if entry.IsDir() {
				name := entry.Name()
				rel, _ := filepath.Rel(outputDir, file)
				inAtlas := rel == "atlas" || strings.HasPrefix(filepath.ToSlash(rel), "atlas/")
				// Atlas cells are sequential states, not independent still works.
				// Keep them off the Images of Beauty wall; the Movement room asks
				// for them explicitly and preserves their order there.
				if movementOnly && file != outputDir && !inAtlas {
					return filepath.SkipDir
				}
				if !movementOnly && inAtlas {
					return filepath.SkipDir
				}
				// "_" marks a working directory, not a collection: contact
				// sheets and other instruments live there. Listing them puts a
				// grid of thumbnails on the wall as though it were a work.
				if file != outputDir && (strings.HasPrefix(name, ".") || strings.HasPrefix(name, "_") || name == "node_modules") {
					return filepath.SkipDir
				}
				return nil
			}
			if !isImageName(entry.Name()) || strings.HasPrefix(entry.Name(), "_") {
				return nil
			}
			if movementOnly {
				rel, relErr := filepath.Rel(outputDir, file)
				if relErr != nil || !strings.HasPrefix(filepath.ToSlash(rel), "atlas/") {
					return nil
				}
			}
			info, _ := entry.Info()
			rel, err := filepath.Rel(outputDir, file)
			if err != nil {
				return nil
			}
			items = append(items, recentImage{
				Name:     entry.Name(),
				Path:     "/outputs/" + filepath.ToSlash(rel),
				URL:      publicOutputRelURL(r, rel),
				Kind:     "output",
				Modified: unixValue(info),
			})
			return nil
		})
	}
	stateRoot, err := filepath.Abs(filepath.Join(s.cfg.Root, ".fluxd"))
	if err == nil && !movementOnly {
		for _, root := range []string{"uploads", "references", "blends"} {
			dir := filepath.Join(stateRoot, root)
			_ = filepath.WalkDir(dir, func(file string, entry os.DirEntry, err error) error {
				if err != nil {
					return nil
				}
				if entry.IsDir() {
					if file != dir && strings.HasPrefix(entry.Name(), ".") {
						return filepath.SkipDir
					}
					return nil
				}
				if !isImageName(entry.Name()) {
					return nil
				}
				info, _ := entry.Info()
				rel, err := filepath.Rel(stateRoot, file)
				if err != nil {
					return nil
				}
				parts := strings.Split(filepath.ToSlash(rel), "/")
				for i, part := range parts {
					parts[i] = url.PathEscape(part)
				}
				items = append(items, recentImage{
					Name:     entry.Name(),
					Path:     file,
					URL:      publicBaseURL(r) + "/staged/" + strings.Join(parts, "/"),
					Kind:     root,
					Modified: unixValue(info),
				})
				return nil
			})
		}
	}
	sort.SliceStable(items, func(i, j int) bool {
		return items[i].Modified > items[j].Modified
	})
	seen := make(map[string]bool)
	unique := items[:0]
	for _, item := range items {
		key := item.Name
		if seen[key] {
			continue
		}
		seen[key] = true
		unique = append(unique, item)
	}
	items = unique
	total := len(items)
	if offset >= len(items) {
		items = nil
	} else {
		items = items[offset:]
	}
	if len(items) > limit {
		items = items[:limit]
	}
	out := make([]map[string]any, 0, len(items))
	for _, item := range items {
		out = append(out, map[string]any{
			"name":     item.Name,
			"path":     item.Path,
			"url":      item.URL,
			"kind":     item.Kind,
			"modified": item.Modified,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "images": out, "total": total, "offset": offset, "limit": limit,
	})
}

func (s Server) deleteCollection(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	rel, ok := s.cleanOutputRel(r.URL.Query().Get("path"))
	if !ok || !(strings.HasPrefix(rel, "batches/") || strings.HasPrefix(rel, "atlas/")) {
		writeError(w, http.StatusBadRequest, "invalid collection path")
		return
	}
	source := filepath.Join(s.cfg.OutputDir, rel)
	sourceAbs, err := filepath.Abs(source)
	if err != nil || !pathInside(s.cfg.OutputDir, sourceAbs) {
		writeError(w, http.StatusBadRequest, "invalid collection path")
		return
	}
	info, err := os.Stat(sourceAbs)
	if err != nil || !info.IsDir() {
		writeError(w, http.StatusNotFound, "collection not found")
		return
	}
	trashRoot := filepath.Join(s.cfg.OutputDir, ".trash")
	if err := os.MkdirAll(trashRoot, 0o755); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	stamp := time.Now().Format("20060102-150405")
	dest := filepath.Join(trashRoot, stamp+"-"+strings.ReplaceAll(filepath.ToSlash(rel), "/", "__"))
	if err := os.Rename(sourceAbs, dest); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	destRel, _ := filepath.Rel(s.cfg.OutputDir, dest)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":         true,
		"deleted":    rel,
		"trash_path": filepath.ToSlash(destRel),
		"trash_url":  publicOutputRelURL(r, destRel),
	})
}

func (s Server) gallery(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	if r.URL.Path == "/gallery" || r.URL.Path == "/gallery/" {
		s.galleryFlux(w, r)
		return
	}
	rel, ok := s.cleanOutputRel(strings.TrimPrefix(r.URL.Path, "/gallery/"))
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid gallery path")
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	page := galleryHTML()
	page = strings.ReplaceAll(page, "{{GALLERY_PATH_JSON}}", strconv.Quote(rel))
	page = strings.ReplaceAll(page, "{{STUDIO_URL}}", html.EscapeString(publicStudioURL(r)))
	_, _ = w.Write([]byte(page))
}

func (s Server) galleryEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	rel, ok := s.cleanOutputRel(strings.TrimPrefix(r.URL.Path, "/api/gallery/events/"))
	if !ok {
		writeError(w, http.StatusBadRequest, "invalid gallery path")
		return
	}
	if isOneOffCollectionRel(rel) {
		s.syncOneOffRenders()
	}
	baseAbs, err := filepath.Abs(filepath.Join(s.cfg.OutputDir, rel))
	if err != nil || !pathInside(s.cfg.OutputDir, baseAbs) {
		writeError(w, http.StatusBadRequest, "invalid gallery path")
		return
	}
	info, err := os.Stat(baseAbs)
	if err != nil || !info.IsDir() {
		writeError(w, http.StatusNotFound, "collection not found")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	w.Header().Set("X-Accel-Buffering", "no")

	send := func(event string) bool {
		payload, status, errMsg := s.collectionSnapshot(r, rel)
		if errMsg != "" {
			payload = map[string]any{"ok": false, "error": errMsg, "status": status}
		}
		raw, err := json.Marshal(payload)
		if err != nil {
			return false
		}
		if _, err := fmt.Fprintf(w, "event: %s\ndata: %s\n\n", event, raw); err != nil {
			return false
		}
		flusher.Flush()
		return true
	}

	if !send("gallery") {
		return
	}
	for {
		if !waitForTreeChange(r.Context(), baseAbs) {
			return
		}
		if !send("gallery") {
			return
		}
	}
}

func (s Server) collectionSummary(r *http.Request, rel string) map[string]any {
	rel, ok := s.cleanOutputRel(rel)
	if !ok {
		return nil
	}
	base := filepath.Join(s.cfg.OutputDir, rel)
	baseAbs, err := filepath.Abs(base)
	if err != nil || !pathInside(s.cfg.OutputDir, baseAbs) {
		return nil
	}
	info, err := os.Stat(baseAbs)
	if err != nil || !info.IsDir() {
		return nil
	}
	count := 0
	total := 0
	var thumb string
	var latest time.Time
	samples := make([]string, 0, 40)
	allFrames := make([]string, 0, 256)
	rejects := loadCollectionRejects(baseAbs)
	_ = filepath.WalkDir(baseAbs, func(file string, entry os.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if entry.IsDir() {
			if file != baseAbs && strings.HasPrefix(entry.Name(), ".") {
				return filepath.SkipDir
			}
			return nil
		}
		if entry.Name() == "manifest.jsonl" {
			total = max(total, countJSONLLines(file))
		}
		if !isMediaName(entry.Name()) {
			return nil
		}
		shortRel, err := filepath.Rel(baseAbs, file)
		if err != nil || rejects[filepath.ToSlash(shortRel)] {
			return nil
		}
		count++
		if len(allFrames) < 4000 {
			fileRel, relErr := filepath.Rel(s.cfg.OutputDir, file)
			if relErr == nil {
				allFrames = append(allFrames, publicOutputRelURL(r, fileRel))
			}
		}
		info, _ := entry.Info()
		if info != nil && info.ModTime().After(latest) {
			latest = info.ModTime()
			fileRel, err := filepath.Rel(s.cfg.OutputDir, file)
			if err == nil {
				thumb = publicOutputRelURL(r, fileRel)
			}
		}
		return nil
	})
	if total <= 0 {
		total = count
	}
	if progressTotal := collectionProgressTotal(baseAbs); progressTotal > total {
		total = progressTotal
	}
	if strings.HasPrefix(rel, "atlas/") {
		if atlasTotal := atlasManifestTotal(baseAbs); atlasTotal > total {
			total = atlasTotal
		}
	}
	if count <= 0 {
		return nil
	}
	name := humanCollectionName(rel)
	kind := "collection"
	if strings.HasPrefix(rel, "batches/") {
		kind = "batch"
	}
	if strings.HasPrefix(rel, "atlas/") {
		kind = "atlas"
	}
	sort.Strings(allFrames)
	if n := len(allFrames); n > 0 {
		want := 40
		if n < want {
			want = n
		}
		for i := 0; i < want; i++ {
			samples = append(samples, allFrames[i*n/want])
		}
	}
	return map[string]any{
		"name":         name,
		"raw_name":     path.Base(filepath.ToSlash(rel)),
		"path":         filepath.ToSlash(rel),
		"priority":     collectionSortPriority(rel),
		"kind":         kind,
		"count":        count,
		"total":        total,
		"updated":      latest.Unix(),
		"updated_text": latest.Format("Jan 2 15:04"),
		"thumbnail":    thumb,
		"samples":      samples,
		"url":          publicGalleryRelURL(r, rel),
		"raw_url":      publicOutputRelURL(r, rel),
	}
}

func collectionProgressTotal(baseAbs string) int {
	return atlasProgressTotal(baseAbs)
}

func atlasManifestTotal(baseAbs string) int {
	return atlasProgressTotal(baseAbs)
}

func (s Server) syncOneOffRenders() {
	rel := s.oneOffCollectionRel()
	if rel == "" {
		return
	}
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err != nil {
		return
	}
	destDir := filepath.Join(outputDir, filepath.FromSlash(rel))
	if err := os.MkdirAll(destDir, 0o755); err != nil {
		slog.Warn("one-off gallery sync failed", "error", err)
		return
	}
	entries, err := os.ReadDir(outputDir)
	if err != nil {
		return
	}
	for _, entry := range entries {
		if entry.IsDir() || !isImageName(entry.Name()) {
			continue
		}
		src := filepath.Join(outputDir, entry.Name())
		dst := filepath.Join(destDir, entry.Name())
		if _, err := os.Stat(dst); err == nil {
			continue
		}
		if err := os.Link(src, dst); err != nil {
			if err := copyRegularFile(src, dst); err != nil {
				slog.Warn("one-off image sync failed", "source", src, "error", err)
				continue
			}
			if info, err := os.Stat(src); err == nil {
				_ = os.Chtimes(dst, info.ModTime(), info.ModTime())
			}
		}
	}
	s.writeOneOffManifest(destDir)
}

func (s Server) oneOffCollectionRel() string {
	batchesDir := filepath.Join(s.cfg.OutputDir, "batches")
	entries, err := os.ReadDir(batchesDir)
	if err == nil {
		names := make([]string, 0)
		for _, entry := range entries {
			if entry.IsDir() && strings.HasPrefix(entry.Name(), "one-off-renders") {
				names = append(names, entry.Name())
			}
		}
		sort.Strings(names)
		if len(names) > 0 {
			return filepath.ToSlash(filepath.Join("batches", names[0]))
		}
	}
	return filepath.ToSlash(filepath.Join("batches", "one-off-renders-"+time.Now().Format("20060102")))
}

func isOneOffCollectionRel(rel string) bool {
	return strings.HasPrefix(path.Base(filepath.ToSlash(rel)), "one-off-renders")
}

func (s Server) writeOneOffManifest(dir string) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return
	}
	tmp := filepath.Join(dir, "manifest.jsonl.tmp")
	f, err := os.Create(tmp)
	if err != nil {
		return
	}
	enc := json.NewEncoder(f)
	for _, entry := range entries {
		if entry.IsDir() || !isImageName(entry.Name()) {
			continue
		}
		_ = enc.Encode(map[string]any{
			"filename": entry.Name(),
			"kind":     "one-off",
		})
	}
	if err := f.Close(); err != nil {
		_ = os.Remove(tmp)
		return
	}
	_ = os.Rename(tmp, filepath.Join(dir, "manifest.jsonl"))
}

func copyRegularFile(src, dst string) error {
	in, err := os.Open(src)
	if err != nil {
		return err
	}
	defer in.Close()
	out, err := os.OpenFile(dst, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0o644)
	if err != nil {
		return err
	}
	_, copyErr := io.Copy(out, in)
	closeErr := out.Close()
	if copyErr != nil {
		_ = os.Remove(dst)
		return copyErr
	}
	if closeErr != nil {
		_ = os.Remove(dst)
		return closeErr
	}
	return nil
}

func loadCollectionRejects(baseAbs string) map[string]bool {
	rejects := make(map[string]bool)
	data, err := os.ReadFile(filepath.Join(baseAbs, ".flux-rejects.json"))
	if err != nil {
		return rejects
	}
	var body struct {
		Rejected []string `json:"rejected"`
	}
	if err := json.Unmarshal(data, &body); err != nil {
		return rejects
	}
	for _, item := range body.Rejected {
		item = strings.TrimSpace(filepath.ToSlash(item))
		item = strings.TrimPrefix(path.Clean("/"+item), "/")
		if item == "" || item == "." || strings.HasPrefix(item, "../") {
			continue
		}
		rejects[item] = true
	}
	return rejects
}

func (s Server) cleanOutputRel(value string) (string, bool) {
	value = strings.TrimSpace(value)
	value = strings.TrimPrefix(value, "/")
	value = path.Clean("/" + filepath.ToSlash(value))
	value = strings.TrimPrefix(value, "/")
	if value == "" || value == "." || strings.HasPrefix(value, "../") {
		return "", false
	}
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err != nil {
		return "", false
	}
	candidate, err := filepath.Abs(filepath.Join(outputDir, value))
	if err != nil || !pathInside(outputDir, candidate) {
		return "", false
	}
	return value, true
}

func (s Server) atlas(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	jobID := path.Base(strings.TrimPrefix(r.URL.Path, "/atlas/"))
	if jobID == "." || jobID == "/" || strings.Contains(jobID, "/") {
		writeError(w, http.StatusBadRequest, "invalid atlas id")
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	page := atlasHTML()
	page = strings.ReplaceAll(page, "{{ATLAS_TITLE}}", html.EscapeString(jobID))
	page = strings.ReplaceAll(page, "{{ATLAS_SUBTITLE}}", html.EscapeString(jobID))
	page = strings.ReplaceAll(page, "{{ATLAS_ID_JSON}}", strconv.Quote(jobID))
	_, _ = w.Write([]byte(page))
}

func (s Server) atlasEvents(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		writeError(w, http.StatusInternalServerError, "streaming unsupported")
		return
	}
	jobID := path.Base(strings.TrimPrefix(r.URL.Path, "/api/atlas/events/"))
	if jobID == "." || jobID == "/" || strings.Contains(jobID, "/") {
		writeError(w, http.StatusBadRequest, "invalid atlas id")
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Connection", "keep-alive")
	atlasDir := filepath.Join(s.cfg.OutputDir, "atlas", jobID+".sphere")
	for {
		snap := s.atlasSnapshot(r, jobID)
		raw, _ := json.Marshal(snap)
		_, _ = fmt.Fprintf(w, "event: atlas\ndata: %s\n\n", raw)
		flusher.Flush()
		if !waitForPathChange(r.Context(), atlasDir) {
			return
		}
	}
}

func (s Server) atlasSnapshot(r *http.Request, jobID string) map[string]any {
	out := map[string]any{"ok": true, "id": jobID, "cells": []string{}, "rendered": 0, "total": 0}
	dir := filepath.Join(s.cfg.OutputDir, "atlas", jobID+".sphere")
	manifestPath := filepath.Join(dir, "manifest.json")
	nCols := 64
	if raw, err := os.ReadFile(manifestPath); err == nil {
		var manifest map[string]any
		if json.Unmarshal(raw, &manifest) == nil {
			out["manifest"] = manifest
			if cols := intValue(manifest["n_cols"]); cols > 0 {
				nCols = cols
			}
			total := intValue(manifest["render_total"])
			if total <= 0 {
				total = intValue(manifest["index_end"]) - intValue(manifest["index_start"])
			}
			if total <= 0 {
				total = intValue(manifest["n_latent"])
			}
			out["total"] = total
		}
	}
	if progress := readAtlasProgress(dir); progress != nil {
		out["progress"] = progress
		out["rendered"] = intValue(progress["current"])
		// Guard the total: a shard that has not written progress yet would
		// otherwise zero out the count the manifest already established.
		if total := intValue(progress["total"]); total > 0 {
			out["total"] = total
		}
	}
	if entries, err := os.ReadDir(dir); err == nil {
		type atlasFrame struct {
			index int
			name  string
			url   string
		}
		frames := make([]atlasFrame, 0, len(entries))
		for _, entry := range entries {
			name := entry.Name()
			if entry.IsDir() || !strings.HasPrefix(name, "cell_") || !strings.HasSuffix(name, ".png") {
				continue
			}
			index, _ := strconv.Atoi(strings.TrimSuffix(strings.TrimPrefix(name, "cell_"), ".png"))
			frames = append(frames, atlasFrame{
				index: index,
				name:  name,
				url:   publicOutputRelURL(r, path.Join("atlas", jobID+".sphere", name)),
			})
		}
		sort.Slice(frames, func(i, j int) bool {
			return frames[i].index < frames[j].index
		})
		cells := make([]string, 0, len(frames))
		frameObjects := make([]map[string]any, 0, len(frames))
		for _, frame := range frames {
			cells = append(cells, frame.url)
			frameObjects = append(frameObjects, map[string]any{
				"index": frame.index,
				"row":   frame.index / nCols,
				"col":   frame.index % nCols,
				"name":  frame.name,
				"src":   frame.url,
			})
		}
		out["cells"] = cells
		out["frames"] = frameObjects
		out["rendered"] = len(cells)
	}
	if resp, err := s.workerSnapshot(); err == nil {
		if job := findJob(resp.Jobs, jobID); job != nil {
			out["status"] = stringValue(job["status"])
			out["phase"] = stringValue(job["phase"])
			out["job"] = job
		}
	}
	return out
}

func (s Server) jobsWithOutputURLs(r *http.Request, jobs []map[string]any) []map[string]any {
	out := make([]map[string]any, 0, len(jobs))
	for _, job := range jobs {
		out = append(out, s.jobWithOutputURL(r, job))
	}
	return out
}

func dashboardJobs(jobs []map[string]any) []map[string]any {
	sorted := append([]map[string]any(nil), jobs...)
	sort.SliceStable(sorted, func(i, j int) bool {
		iActive := activeJobStatus(stringValue(sorted[i]["status"]))
		jActive := activeJobStatus(stringValue(sorted[j]["status"]))
		if iActive != jActive {
			return iActive
		}
		return floatValue(sorted[i]["created"]) > floatValue(sorted[j]["created"])
	})
	if len(sorted) > 100 {
		sorted = sorted[:100]
	}
	return sorted
}

func activeJobStatus(status string) bool {
	switch strings.ToLower(strings.TrimSpace(status)) {
	case "queued", "running", "cancelling":
		return true
	default:
		return false
	}
}

func (s Server) jobWithOutputURL(r *http.Request, job map[string]any) map[string]any {
	if job == nil {
		return nil
	}
	out := make(map[string]any, len(job)+1)
	for k, v := range job {
		out[k] = v
	}
	if outputURL := s.outputURL(r, stringValue(job["output"])); outputURL != "" {
		out["output_url"] = outputURL
	}
	if strings.EqualFold(stringValue(job["kind"]), "atlas_sphere") {
		if id := strings.TrimSpace(stringValue(job["id"])); id != "" {
			viewerURL := publicBaseURL(r) + "/atlas/" + url.PathEscape(id)
			out["viewer_url"] = viewerURL
			out["gallery_url"] = publicBaseURL(r) + "/gallery/atlas/" + url.PathEscape(id+".sphere")
			out["output_url"] = viewerURL
		}
	}
	for _, item := range []struct {
		key string
		url string
	}{
		{"image", "image_url"},
		{"primary_image", "primary_image_url"},
		{"image2", "image2_url"},
		{"identity_image", "identity_image_url"},
		{"posture_image", "posture_image_url"},
		{"backdrop_image", "backdrop_image_url"},
		{"blend_image", "blend_image_url"},
	} {
		if url := s.imagePreviewURL(r, stringValue(job[item.key])); url != "" {
			out[item.url] = url
		}
	}
	s.attachBatchProgress(out)
	return out
}

func (s Server) imagePreviewURL(r *http.Request, imagePath string) string {
	if url := s.outputURL(r, imagePath); url != "" {
		return url
	}
	stateRoot, err := filepath.Abs(filepath.Join(s.cfg.Root, ".fluxd"))
	if err != nil {
		return ""
	}
	imageAbs, err := filepath.Abs(imagePath)
	if err != nil || !pathInside(stateRoot, imageAbs) || !isImageName(imageAbs) {
		return ""
	}
	rel, err := filepath.Rel(stateRoot, imageAbs)
	if err != nil {
		return ""
	}
	rel = filepath.ToSlash(rel)
	first := strings.Split(rel, "/")[0]
	if first != "uploads" && first != "references" && first != "blends" {
		return ""
	}
	parts := strings.Split(rel, "/")
	for i, part := range parts {
		parts[i] = url.PathEscape(part)
	}
	return publicBaseURL(r) + "/staged/" + strings.Join(parts, "/")
}

func (s Server) attachBatchProgress(job map[string]any) {
	rel := strings.TrimSpace(filepath.ToSlash(stringValue(job["filename"])))
	if rel == "" {
		rel = s.outputRel(stringValue(job["output"]))
	}
	if !strings.HasPrefix(rel, "batches/") {
		return
	}
	parts := strings.Split(rel, "/")
	if len(parts) < 2 {
		return
	}
	batchRel := strings.Join(parts[:2], "/")
	batchRoot := filepath.Join(s.cfg.OutputDir, filepath.FromSlash(batchRel))
	total := countJSONLLines(filepath.Join(batchRoot, "manifest.jsonl"))
	done := countJSONLLines(filepath.Join(batchRoot, "completed.jsonl"))
	submitted := countJSONLLines(filepath.Join(batchRoot, "submitted.jsonl"))
	if total > 0 {
		job["batch_total"] = total
	}
	if done > 0 {
		job["batch_done"] = done
	}
	if submitted > 0 {
		job["batch_submitted"] = submitted
	}
	job["batch_path"] = batchRel
	job["batch_name"] = humanCollectionName(batchRel)

	if row := readBatchStatus(filepath.Join(batchRoot, "status.json"), stringValue(job["id"]), rel); row != nil {
		applyBatchRow(job, row)
		return
	}
	if row := findBatchRow(filepath.Join(batchRoot, "submitted.jsonl"), stringValue(job["id"]), rel); row != nil {
		applyBatchRow(job, row)
	}
}

func (s Server) outputRel(outputPath string) string {
	outputPath = strings.TrimSpace(outputPath)
	if outputPath == "" {
		return ""
	}
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err != nil {
		return ""
	}
	outputAbs, err := filepath.Abs(outputPath)
	if err != nil || !pathInside(outputDir, outputAbs) {
		return ""
	}
	rel, err := filepath.Rel(outputDir, outputAbs)
	if err != nil || rel == "." || strings.HasPrefix(rel, "..") {
		return ""
	}
	return filepath.ToSlash(rel)
}

func readBatchStatus(file, workerID, filename string) map[string]any {
	raw, err := os.ReadFile(file)
	if err != nil {
		return nil
	}
	var body struct {
		Batch     map[string]any `json:"batch"`
		WorkerJob map[string]any `json:"worker_job"`
	}
	if json.Unmarshal(raw, &body) != nil || body.Batch == nil {
		return nil
	}
	if stringValue(body.WorkerJob["id"]) == workerID || filepath.ToSlash(stringValue(body.Batch["filename"])) == filename {
		return body.Batch
	}
	return nil
}

func findBatchRow(file, workerID, filename string) map[string]any {
	raw, err := os.ReadFile(file)
	if err != nil {
		return nil
	}
	var match map[string]any
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(line) == "" {
			continue
		}
		var row map[string]any
		if json.Unmarshal([]byte(line), &row) != nil {
			continue
		}
		if stringValue(row["worker_job_id"]) == workerID || filepath.ToSlash(stringValue(row["filename"])) == filename {
			match = row
		}
	}
	return match
}

func applyBatchRow(job, row map[string]any) {
	for _, key := range []string{"batch_index", "total", "prompt_index", "variant_index", "group_id", "group_label"} {
		if value, ok := row[key]; ok {
			if key == "total" {
				job["batch_total"] = value
			} else {
				job[key] = value
			}
		}
	}
}

func (s Server) outputURL(r *http.Request, outputPath string) string {
	outputPath = strings.TrimSpace(outputPath)
	if outputPath == "" {
		return ""
	}
	outputDir, err := filepath.Abs(s.cfg.OutputDir)
	if err != nil {
		return ""
	}
	outputAbs, err := filepath.Abs(outputPath)
	if err != nil || !pathInside(outputDir, outputAbs) {
		return ""
	}
	rel, err := filepath.Rel(outputDir, outputAbs)
	if err != nil || rel == "." || strings.HasPrefix(rel, "..") {
		return ""
	}
	return publicOutputRelURL(r, filepath.ToSlash(rel))
}

func pathInside(root, candidate string) bool {
	rel, err := filepath.Rel(root, candidate)
	return err == nil && rel != "." && !strings.HasPrefix(rel, "..") && !filepath.IsAbs(rel)
}

func publicOutputRelURL(r *http.Request, rel string) string {
	parts := strings.Split(filepath.ToSlash(rel), "/")
	for i, part := range parts {
		parts[i] = url.PathEscape(part)
	}
	return publicBaseURL(r) + "/outputs/" + strings.Join(parts, "/")
}

func publicGalleryRelURL(r *http.Request, rel string) string {
	parts := strings.Split(filepath.ToSlash(rel), "/")
	for i, part := range parts {
		parts[i] = url.PathEscape(part)
	}
	return publicBaseURL(r) + "/gallery/" + strings.Join(parts, "/")
}

func publicStudioURL(r *http.Request) string {
	host := strings.TrimSpace(r.Header.Get("X-Forwarded-Host"))
	if host == "" {
		host = r.Host
	}
	if strings.EqualFold(host, "anime.sakure.network") {
		return "/flux/"
	}
	return "/"
}

func publicBaseURL(r *http.Request) string {
	proto := strings.TrimSpace(r.Header.Get("X-Forwarded-Proto"))
	if proto == "" {
		proto = "http"
		if r.TLS != nil {
			proto = "https"
		}
	}
	host := strings.TrimSpace(r.Header.Get("X-Forwarded-Host"))
	if host == "" {
		host = r.Host
	}
	return proto + "://" + host
}

func (s Server) warm(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	preload := truthy(r.URL.Query().Get("preload"))
	if err := s.workerStart(preload); err != nil {
		writeError(w, http.StatusServiceUnavailable, err.Error())
		return
	}
	writeJSON(w, http.StatusAccepted, map[string]any{"ok": true, "preload": preload})
}

func (s Server) stop(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	if err := s.workerStop(); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "worker_running": false, "worker_error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "worker_running": false})
}

func (s Server) pauseBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	output, err := launchctl("bootout", "gui/"+strconv.Itoa(os.Getuid()), animeCastBatchPlist())
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"paused":  true,
		"message": launchctlMessage(output, err, "batch submissions paused"),
	})
}

func (s Server) resumeBatch(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	output, err := launchctl("bootstrap", "gui/"+strconv.Itoa(os.Getuid()), animeCastBatchPlist())
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"resumed": true,
		"message": launchctlMessage(output, err, "batch submissions resumed"),
	})
}

func launchctl(args ...string) (string, error) {
	out, err := exec.Command("launchctl", args...).CombinedOutput()
	return strings.TrimSpace(string(out)), err
}

func launchctlMessage(output string, err error, fallback string) string {
	if output != "" {
		return output
	}
	if err != nil {
		return err.Error()
	}
	return fallback
}

func (s Server) plan(req renderRequest) (renderPlan, error) {
	base := strings.TrimSpace(req.Prompt)
	if base == "" {
		return renderPlan{}, errors.New("prompt is required")
	}
	model := strings.ToLower(strings.TrimSpace(req.Model))
	if model == "" {
		model = "dev"
	}
	switch model {
	case "dev", "flux.1-dev", "flux1-dev":
		model = "dev"
	case "schnell", "flux.1-schnell", "flux1-schnell":
		model = "schnell"
	default:
		return renderPlan{}, fmt.Errorf("unknown model %q", model)
	}
	backend := strings.ToLower(valueOr(req.Backend, "auto"))
	if !validBackend(backend) {
		return renderPlan{}, fmt.Errorf("unknown backend %q", backend)
	}
	if model == "schnell" {
		backend = "mlx"
	}
	ratioName := valueOr(req.Ratio, "square")
	steps := req.Steps
	if steps == 0 {
		if model == "schnell" {
			steps = 4
		} else {
			steps = 28
		}
	}
	guidance := req.Guidance
	if guidance == 0 {
		if model == "schnell" {
			guidance = 0
		} else {
			guidance = 3.5
		}
	}
	preset, err := prompt.PresetByName(req.Preset)
	if err != nil {
		return renderPlan{}, err
	}
	style := req.Style
	mood := req.Mood
	if preset.Name != "" {
		if style == "" {
			style = preset.Style
		}
		if mood == "" {
			mood = preset.Mood
		}
		if ratioName == "square" {
			ratioName = preset.Ratio
		}
		if steps == 28 {
			steps = preset.Steps
		}
		if guidance == 3.5 {
			guidance = preset.Guidance
		}
	}
	if req.Draft {
		ratioName = "draft"
		steps = 18
	}
	ratio, err := prompt.RatioByName(ratioName)
	if err != nil {
		return renderPlan{}, err
	}
	width := req.Width
	height := req.Height
	if width == 0 {
		width = ratio.Width
	}
	if height == 0 {
		height = ratio.Height
	}
	shaped, err := prompt.Compose(base, prompt.Shape{
		Style: style, Mood: mood, Camera: req.Camera, Light: req.Light, Palette: req.Palette,
		Texture: req.Texture, Detail: req.Detail, Chaos: req.Chaos, Director: req.Director, Preset: req.Preset,
	})
	if err != nil {
		return renderPlan{}, err
	}
	cmd := []string{
		s.cfg.Python,
		s.cfg.GeneratePy,
		"--prompt", shaped,
		"--width", strconv.Itoa(width),
		"--height", strconv.Itoa(height),
		"--steps", strconv.Itoa(steps),
		"--guidance", fmt.Sprintf("%.3f", guidance),
	}
	if req.Seed != "" {
		cmd = append(cmd, "--seed", req.Seed)
	}
	if req.Filename != "" {
		cmd = append(cmd, "--filename", req.Filename)
	}
	return renderPlan{
		Prompt:   shaped,
		Model:    model,
		Backend:  backend,
		Preset:   req.Preset,
		Style:    style,
		Mood:     mood,
		Camera:   req.Camera,
		Light:    req.Light,
		Palette:  req.Palette,
		Texture:  req.Texture,
		Detail:   req.Detail,
		Chaos:    req.Chaos,
		Director: req.Director,
		Ratio:    ratioName,
		Width:    width,
		Height:   height,
		Steps:    steps,
		Guidance: guidance,
		Seed:     req.Seed,
		Filename: req.Filename,
		Command:  cmd,
	}, nil
}

func withLocalHeaders(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-Content-Type-Options", "nosniff")
		w.Header().Set("Access-Control-Allow-Origin", localOrigin(r))
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Flux-Token")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func localOrigin(r *http.Request) string {
	origin := r.Header.Get("Origin")
	if origin == "" {
		return "*"
	}
	host := strings.TrimPrefix(strings.TrimPrefix(origin, "http://"), "https://")
	host, _, _ = net.SplitHostPort(host)
	if host == "localhost" || host == "127.0.0.1" || host == "::1" {
		return origin
	}
	return "http://127.0.0.1"
}

func methodNotAllowed(w http.ResponseWriter, methods ...string) {
	w.Header().Set("Allow", strings.Join(methods, ", "))
	writeError(w, http.StatusMethodNotAllowed, "method not allowed")
}

func writeError(w http.ResponseWriter, status int, message string) {
	writeJSON(w, status, map[string]any{"ok": false, "error": message})
}

func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}

func valueOr(value, fallback string) string {
	if strings.TrimSpace(value) == "" {
		return fallback
	}
	return value
}

func stringValue(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case fmt.Stringer:
		return t.String()
	case nil:
		return ""
	default:
		return fmt.Sprintf("%v", t)
	}
}

func intValue(v any) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case json.Number:
		n, _ := t.Int64()
		return int(n)
	case string:
		n, _ := strconv.Atoi(strings.TrimSpace(t))
		return n
	default:
		return 0
	}
}

func floatValue(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case float32:
		return float64(t)
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case json.Number:
		n, _ := t.Float64()
		return n
	default:
		return 0
	}
}

func int64Value(info os.FileInfo) int64 {
	if info == nil {
		return 0
	}
	return info.Size()
}

func unixValue(info os.FileInfo) int64 {
	if info == nil {
		return 0
	}
	return info.ModTime().Unix()
}

func isImageName(name string) bool {
	switch strings.ToLower(path.Ext(name)) {
	case ".png", ".jpg", ".jpeg", ".webp":
		return true
	default:
		return false
	}
}

func isVideoName(name string) bool {
	switch strings.ToLower(path.Ext(name)) {
	case ".mp4", ".webm", ".mov":
		return true
	default:
		return false
	}
}

func isMediaName(name string) bool {
	return isImageName(name) || isVideoName(name)
}

func mediaType(name string) string {
	if isVideoName(name) {
		return "video"
	}
	return "image"
}

func countJSONLLines(file string) int {
	raw, err := os.ReadFile(file)
	if err != nil {
		return 0
	}
	n := 0
	for _, line := range strings.Split(string(raw), "\n") {
		if strings.TrimSpace(line) != "" {
			n++
		}
	}
	return n
}

func humanCollectionName(rel string) string {
	base := path.Base(filepath.ToSlash(rel))
	if strings.HasPrefix(base, "dark-black-sheep-princess-425") {
		return "Midnight Outcast Princess 425"
	}
	if strings.HasPrefix(base, "dev-bf16-mflux-vs-socket") {
		return "Dev BF16 Benchmark: mflux vs Socket"
	}
	if strings.HasPrefix(base, "one-off-renders") {
		return "One-Off Renders"
	}
	base = strings.TrimSuffix(base, ".sphere")
	base = strings.TrimPrefix(base, "spheremap_atlas_")
	base = strings.TrimPrefix(base, "xfc-")
	base = strings.TrimPrefix(base, "fbc-")
	base = strings.TrimSuffix(base, "_20260713")
	base = strings.TrimSuffix(base, "-20260713")
	replacer := strings.NewReplacer("_", " ", "-", " ")
	words := strings.Fields(replacer.Replace(base))
	if len(words) == 0 {
		return "Collection"
	}
	small := map[string]bool{"and": true, "or": true, "the": true, "of": true, "in": true, "with": true}
	for i, word := range words {
		lower := strings.ToLower(word)
		if i > 0 && small[lower] {
			words[i] = lower
			continue
		}
		if len(lower) <= 3 && strings.ToUpper(lower) == word {
			words[i] = word
			continue
		}
		words[i] = strings.ToUpper(lower[:1]) + lower[1:]
	}
	return strings.Join(words, " ")
}

func findJob(jobs []map[string]any, id string) map[string]any {
	for _, job := range jobs {
		if stringValue(job["id"]) == id {
			return job
		}
	}
	return nil
}

func atlasHTML() string {
	return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flux atlas {{ATLAS_TITLE}}</title>
<style>
:root{color-scheme:dark;--bg:#101114;--panel:#17191f;--line:#2b3040;--text:#f0edf8;--muted:#9aa3b2;--violet:#b693ff;--teal:#64d7c4;--gold:#ffd866;--rose:#ff6f9c;--green:#8ef6b0}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1320px;margin:0 auto;padding:28px 18px 42px}.top{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;border-bottom:1px solid var(--line);padding-bottom:18px}
.mark{font:700 28px/1 ui-monospace,SFMono-Regular,Menlo,monospace;color:var(--violet)}.sub{color:var(--muted);margin-top:8px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:14px;margin-top:18px}
.stats{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px}.stat{border:1px solid var(--line);border-radius:6px;padding:10px;background:#11141a;min-width:0}.stat b{display:block;color:var(--gold);font-size:18px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.stat span{color:var(--muted)}
.bar{height:8px;border-radius:999px;background:#0d0f14;border:1px solid var(--line);overflow:hidden;margin-top:12px}.bar i{display:block;height:100%;width:0;background:var(--teal)}
.tune{margin-bottom:12px}.tuneHead{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:9px}.tuneHead span{color:var(--muted);font-size:12px}
.tuneRow{display:flex;flex-wrap:wrap;gap:10px;align-items:end}.tuneRow label{display:grid;gap:4px;color:var(--muted);font-size:12px}
.tuneRow input{width:92px;border:1px solid var(--line);border-radius:6px;background:#0d0f14;color:var(--text);padding:7px 8px;font:13px ui-monospace,SFMono-Regular,Menlo,monospace}
.tuneRow button{border:1px solid var(--line);border-radius:6px;background:#11141a;color:var(--text);padding:8px 14px;cursor:pointer}.tuneRow button:hover{border-color:var(--teal)}.tuneRow button:disabled{opacity:.5;cursor:default}
.tuneRow #tStatus{color:var(--muted);font-size:12px}
.tuneLog{margin-top:9px;color:var(--muted);font:11px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap}
.cells{display:grid;grid-template-columns:repeat(auto-fill,minmax(118px,1fr));gap:10px}.cell{appearance:none;border:1px solid var(--line);border-radius:6px;background:#0d0f14;aspect-ratio:1;overflow:hidden;display:block;padding:0;position:relative;cursor:zoom-in}.cell img{width:100%;height:100%;object-fit:cover;display:block}.cell i{position:absolute;left:6px;bottom:6px;background:rgba(13,15,20,.76);border:1px solid rgba(255,255,255,.16);border-radius:4px;color:var(--text);font:11px/1 ui-monospace,SFMono-Regular,Menlo,monospace;padding:4px 5px;font-style:normal}.cell.loading{display:grid;place-items:center;color:var(--muted);font:12px/1 ui-monospace,SFMono-Regular,Menlo,monospace}.empty{color:var(--muted)}
.preview{position:fixed;inset:0;background:rgba(0,0,0,.86);display:none;grid-template-columns:72px minmax(0,1fr) 72px;grid-template-rows:minmax(0,1fr) auto;align-items:center;gap:12px;padding:22px;z-index:10}.preview.open{display:grid}.preview img{justify-self:center;max-width:100%;max-height:calc(100vh - 110px);border-radius:6px;border:1px solid var(--line);background:#0d0f14}.preview button{appearance:none;border:1px solid var(--line);border-radius:6px;background:rgba(17,20,26,.86);color:var(--text);height:64px;font-size:32px;cursor:pointer}.preview button:hover{border-color:var(--teal)}.preview .meta{grid-column:1/-1;color:var(--muted);font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;text-align:center}
@media(max-width:980px){.top{display:block}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.cells{grid-template-columns:repeat(auto-fill,minmax(96px,1fr))}}
</style>
</head>
<body>
<main>
<div class="top"><div><div class="mark">flux atlas</div><div class="sub">{{ATLAS_SUBTITLE}}</div></div><div class="sub" id="status">connecting</div></div>
<section class="panel">
<div class="stats">
<div class="stat"><b id="rendered">0</b><span>rendered</span></div>
<div class="stat"><b id="total">0</b><span>total</span></div>
<div class="stat"><b id="phase">queued</b><span>phase</span></div>
<div class="stat"><b id="mode">atlas</b><span>mode</span></div>
<div class="stat"><b id="rate">0/h</b><span>rate</span></div>
<div class="stat"><b id="cache">0%</b><span>cache hits</span></div>
</div>
<div class="bar"><i id="bar"></i></div>
</section>
<section class="panel tune">
<div class="tuneHead"><b>Live tuning</b><span id="tuneHint">applies to the next batch on every shard</span></div>
<div class="tuneRow">
<label>guidance<input id="tGuidance" type="number" step="0.1" min="0" max="20"></label>
<label>steps<input id="tSteps" type="number" step="1" min="1" max="120"></label>
<label>batch<input id="tBatch" type="number" step="1" min="1" max="64"></label>
<button id="tApply" type="button">Apply</button>
<span id="tStatus"></span>
</div>
<div id="tLog" class="tuneLog"></div>
</section>
<section class="panel"><div id="cells" class="cells"><div class="empty">waiting for cells</div></div></section>
</main>
<div id="preview" class="preview" role="dialog" aria-modal="true"><button id="prevCell" type="button" aria-label="Previous cell">&lsaquo;</button><img alt=""><button id="nextCell" type="button" aria-label="Next cell">&rsaquo;</button><div id="previewMeta" class="meta"></div></div>
<script>
const id={{ATLAS_ID_JSON}}, seen=new Set(), frames=[], cells=document.getElementById('cells');
const preview=document.getElementById('preview'), previewImg=preview.querySelector('img'), previewMeta=document.getElementById('previewMeta');
let previewIndex=-1;
function text(k,v){document.getElementById(k).textContent=String(v ?? '')}
function fmtRate(v){const n=Number(v||0);return n>=100?String(Math.round(n))+'/h':n.toFixed(1)+'/h'}
function fmtPct(v){const n=Number(v||0);return Math.round(n*1000)/10+'%'}
function frameKey(frame){return String(frame?.src||frame)}
function add(frame){const src=frameKey(frame);if(!src||seen.has(src))return;seen.add(src);frames.push(frame);frames.sort((a,b)=>Number(a.index??0)-Number(b.index??0));if(cells.querySelector('.empty'))cells.textContent='';const button=document.createElement('button');button.type='button';button.className='cell loading';button.textContent='loading';const img=document.createElement('img');img.alt='atlas cell '+String(frame.index??'');img.loading='eager';img.decoding='async';img.onload=()=>{button.classList.remove('loading');button.textContent='';button.appendChild(img);const label=document.createElement('i');label.textContent='i '+String(frame.index??seen.size-1).padStart(5,'0')+' · r '+String(frame.row??'')+' c '+String(frame.col??'');button.appendChild(label)};img.onerror=()=>{setTimeout(()=>{img.src=src+(src.includes('?')?'&':'?')+'retry='+Date.now()},700)};button.onclick=()=>openFrame(frames.findIndex(x=>frameKey(x)===src));img.src=src;cells.appendChild(button)}
function openFrame(i){if(!frames.length)return;previewIndex=(i+frames.length)%frames.length;const f=frames[previewIndex],src=frameKey(f);previewImg.src=src;previewMeta.textContent='cell '+String(f.index??'').padStart(5,'0')+' · row '+String(f.row??'?')+' · col '+String(f.col??'?')+' · '+(previewIndex+1)+'/'+frames.length;preview.classList.add('open')}
function closePreview(){preview.classList.remove('open');previewImg.src=''}
document.getElementById('prevCell').onclick=e=>{e.stopPropagation();openFrame(previewIndex-1)}
document.getElementById('nextCell').onclick=e=>{e.stopPropagation();openFrame(previewIndex+1)}
preview.onclick=e=>{if(e.target===preview)closePreview()}
document.addEventListener('keydown',e=>{if(!preview.classList.contains('open'))return;if(e.key==='Escape')closePreview();if(e.key==='ArrowLeft')openFrame(previewIndex-1);if(e.key==='ArrowRight')openFrame(previewIndex+1)})
const es=new EventSource('/api/atlas/events/'+encodeURIComponent(id));
es.addEventListener('atlas',ev=>{const d=JSON.parse(ev.data);const p=d.progress||{}, total=Number(d.total||p.total||0), rendered=Number(d.rendered||p.current||0);text('rendered',rendered);text('total',total);text('phase',d.phase||d.status||'active');text('mode',d.manifest?.mode||'atlas');text('rate',fmtRate(p.cells_per_hour||d.job?.cells_per_hour));text('cache',fmtPct(p.cache_hit_rate||d.job?.cache_hit_rate));document.getElementById('bar').style.width=total?Math.min(100,(rendered/total)*100)+'%':'0';document.getElementById('status').textContent=(d.status||'watching')+' '+rendered+'/'+total;syncTuning(d.job);(d.frames||d.cells||[]).forEach(add)});
// Live tuning. The worker owns the whitelist, so anything it refuses comes
// back as "rejected" and is reported verbatim rather than guessed at here.
const tG=document.getElementById('tGuidance'),tS=document.getElementById('tSteps'),tB=document.getElementById('tBatch'),
      tApply=document.getElementById('tApply'),tStatus=document.getElementById('tStatus'),tLog=document.getElementById('tLog');
let tTouched=false, tRunning=false;
[tG,tS,tB].forEach(el=>el.addEventListener('input',()=>{tTouched=true}));
function syncTuning(job){
  if(!job)return;
  tRunning=['queued','running'].includes(String(job.status||''));
  tApply.disabled=!tRunning;
  document.getElementById('tuneHint').textContent=tRunning?'applies to the next batch on every shard':'job is '+(job.status||'idle')+'; tuning is closed';
  // Never clobber a value mid-edit; only seed from the job until first touch.
  if(!tTouched){
    if(job.guidance!=null)tG.value=job.guidance;
    if(job.steps!=null)tS.value=job.steps;
    const b=job.batch_size_requested!=null?job.batch_size_requested:job.batch_size;
    if(b!=null)tB.value=b;
  }
  const log=job.parameter_changes||[];
  if(log.length)tLog.textContent=log.slice(-6).map(c=>'cell '+c.at_cell+'  '+Object.entries(c.changes||{}).map(([k,v])=>k+' '+v.from+'->'+v.to).join('  ')).join('\n');
}
tApply.onclick=async()=>{
  const fields={};
  if(tG.value!=='')fields.guidance=Number(tG.value);
  if(tS.value!=='')fields.steps=Number(tS.value);
  if(tB.value!=='')fields.batch_size=Number(tB.value);
  if(!Object.keys(fields).length){tStatus.textContent='nothing to apply';return}
  tApply.disabled=true;tStatus.textContent='applying...';
  try{
    const r=await fetch('/api/job/update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id,fields})});
    const j=await r.json();
    if(!r.ok||j.ok===false){tStatus.textContent=j.error||('HTTP '+r.status)}
    else{
      const ch=Object.keys(j.changed||{}), rj=Object.entries(j.rejected||{});
      tStatus.textContent=(ch.length?ch.length+' applied across '+(j.shards_updated||0)+' shard(s)':'no change')+
        (rj.length?' · rejected: '+rj.map(([k,v])=>k+' ('+v+')').join(', '):'');
      tTouched=false;
    }
  }catch(err){tStatus.textContent=String(err&&err.message||err)}
  tApply.disabled=!tRunning;
};
es.onerror=()=>{document.getElementById('status').textContent='stream reconnecting'};
</script>
</body>
</html>`
}

func galleryIndexHTML() string {
	return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>atelier gallery</title>
<style>
:root{color-scheme:dark;--bg:#07080d;--panel:#10131c;--panel2:#171b28;--line:rgba(235,240,255,.14);--text:#f3f5fb;--muted:#9aa5b8;--pink:#ffb7c5;--blue:#72d9ff;--green:#61f0b5;--gold:#ffd985}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(180deg,#07080d,#10131c 58%,#0b0d14);color:var(--text);font:14px/1.45 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:0}
main{max-width:1460px;margin:0 auto;padding:24px clamp(14px,3vw,34px) 44px}.top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:end;border:1px solid var(--line);border-radius:8px;padding:24px;background:linear-gradient(110deg,rgba(16,19,28,.98),rgba(32,20,35,.72) 48%,rgba(14,36,48,.62));box-shadow:0 24px 80px rgba(0,0,0,.34)}
.mark{color:var(--pink);font-size:12px;font-weight:800;letter-spacing:.18em;text-transform:uppercase}.title{margin-top:10px;font-size:clamp(36px,5vw,66px);line-height:.95;font-weight:820}.sub{margin-top:10px;color:var(--muted);max-width:820px}.actions{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end}.btn,button{appearance:none;border:1px solid rgba(235,240,255,.17);border-radius:7px;background:rgba(7,8,13,.62);color:var(--text);min-height:39px;padding:9px 12px;text-decoration:none;font-weight:760;cursor:pointer}.btn:hover,button:hover{border-color:rgba(255,183,197,.38);background:rgba(23,27,40,.86)}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:16px}.stat{border:1px solid var(--line);border-radius:8px;background:rgba(16,19,28,.76);padding:14px;min-width:0}.stat b{display:block;font-size:26px;line-height:1;color:var(--blue);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.stat:nth-child(2) b{color:var(--green)}.stat:nth-child(3) b{color:var(--pink)}.stat:nth-child(4) b{color:var(--gold)}.stat span{display:block;margin-top:6px;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em}
.toolbar{display:grid;grid-template-columns:minmax(220px,1fr) auto auto;gap:10px;margin:18px 0}.search,select{border:1px solid rgba(235,240,255,.14);border-radius:7px;background:rgba(7,8,13,.72);color:var(--text);min-height:42px;padding:10px 12px;font:inherit}.search:focus,select:focus{outline:0;border-color:rgba(114,217,255,.48)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:13px}.card{display:grid;grid-template-rows:160px auto;min-width:0;overflow:hidden;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.025),transparent),rgba(16,19,28,.78);text-decoration:none;color:var(--text);box-shadow:0 16px 46px rgba(0,0,0,.25)}.card:hover{border-color:rgba(255,183,197,.35);transform:translateY(-1px)}.thumb{background:#080a10;display:grid;place-items:center;color:var(--muted);overflow:hidden}.thumb img,.thumb video{width:100%;height:100%;object-fit:cover;display:block}.body{padding:12px}.body b{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:15px}.meta{display:flex;justify-content:space-between;gap:10px;color:var(--muted);margin-top:7px;font-size:12px}.bar{height:8px;border-radius:99px;border:1px solid rgba(235,240,255,.1);background:rgba(235,240,255,.07);overflow:hidden;margin-top:10px}.bar i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--pink),var(--blue),var(--green))}.empty{border:1px dashed var(--line);border-radius:8px;padding:28px;color:var(--muted);text-align:center;background:rgba(16,19,28,.54)}.status{color:var(--muted);font-size:12px;text-align:right}.dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#ff6b86;margin-right:6px;box-shadow:0 0 12px rgba(255,107,134,.28)}.dot.on{background:var(--green);box-shadow:0 0 12px rgba(97,240,181,.3)}
@media(max-width:800px){.top{grid-template-columns:1fr}.actions{justify-content:flex-start}.stats{grid-template-columns:repeat(2,minmax(0,1fr))}.toolbar{grid-template-columns:1fr}.grid{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}.card{grid-template-rows:128px auto}.status{text-align:left}}
</style>
</head>
<body>
<main>
<section class="top">
<div><div class="mark">anime.productions archive</div><div class="title">Atelier gallery</div><div class="sub">Live collection index for FLUX batches, atlas runs, and generated media. Open any card for the broadcast-backed collection view.</div></div>
<div class="actions"><a class="btn" href="{{STUDIO_URL}}">Studio</a><a class="btn" href="/atlas-watch">Atlas watch</a><button id="refresh" type="button">Refresh</button></div>
</section>
<section class="stats">
<div class="stat"><b id="collections">0</b><span>collections</span></div>
<div class="stat"><b id="media">0</b><span>media</span></div>
<div class="stat"><b id="active">0</b><span>active jobs</span></div>
<div class="stat"><b id="planned">0</b><span>planned</span></div>
</section>
<div class="toolbar"><input id="search" class="search" placeholder="filter collections"><select id="kind"><option value="">all types</option><option value="batch">batch</option><option value="atlas">atlas</option><option value="collection">collection</option></select><div class="status"><span id="dot" class="dot"></span><span id="status">loading</span></div></div>
<section id="grid" class="grid"><div class="empty">Loading gallery</div></section>
</main>
<script>
const $=id=>document.getElementById(id), grid=$('grid'), search=$('search'), kind=$('kind');
let collections=[], jobs=[];
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function escAttr(v){return esc(v)}
function text(id,v){$(id).textContent=String(v??'')}
function pct(x){const total=Number(x.total||0), count=Number(x.count||0);return total?Math.max(0,Math.min(100,(count/total)*100)):100}
function mediaTag(x){return x.thumbnail?'<img loading="lazy" decoding="async" src="'+escAttr(x.thumbnail)+'" alt="">':'<div>No preview</div>'}
function card(x){const p=pct(x), label=Number(x.count||0)+'/'+Number(x.total||x.count||0);return '<a class="card" href="'+escAttr(x.url)+'"><div class="thumb">'+mediaTag(x)+'</div><div class="body"><b>'+esc(x.name||x.path)+'</b><div class="meta"><span>'+esc(x.kind||'collection')+' · '+label+'</span><span>'+esc(x.updated_text||'')+'</span></div><div class="bar"><i style="width:'+p+'%"></i></div></div></a>'}
function render(){const q=search.value.trim().toLowerCase(), k=kind.value;let items=[...collections].sort((a,b)=>(Number(a.priority??10)-Number(b.priority??10))||(Number(b.updated||0)-Number(a.updated||0)));if(k)items=items.filter(x=>String(x.kind||'')===k);if(q)items=items.filter(x=>String((x.name||'')+' '+(x.path||'')).toLowerCase().includes(q));grid.innerHTML=items.map(card).join('')||'<div class="empty">No collections match.</div>';const active=jobs.filter(x=>['queued','running','cancelling'].includes(String(x.status||'').toLowerCase())).length;text('collections',collections.length);text('media',collections.reduce((n,x)=>n+Number(x.count||0),0));text('planned',collections.reduce((n,x)=>n+Number(x.total||x.count||0),0));text('active',active)}
async function requestJSON(path){const r=await fetch(path);return await r.json()}
async function load(){try{const [c,j,i]=await Promise.all([requestJSON('/api/collections'),requestJSON('/api/jobs'),requestJSON('/api/img2img/jobs')]);collections=c.collections||[];jobs=[...(j.jobs||[]),...(i.jobs||[])];$('dot').classList.toggle('on',true);text('status','broadcast ready');render()}catch(err){$('dot').classList.toggle('on',false);text('status',err?.message||'load failed');grid.innerHTML='<div class="empty">'+esc(err?.message||'Gallery unavailable')+'</div>'}}
function connect(){if(!window.EventSource){load();return}const reload=()=>load();const jobStream=new EventSource('/api/jobs/events');jobStream.addEventListener('jobs',ev=>{try{const j=JSON.parse(ev.data);jobs=[...(j.jobs||[]),...jobs.filter(x=>String(x.socket_kind||'flux')==='img2img')];text('status','job broadcast');render();load()}catch(_){}});jobStream.onerror=()=>{text('status','job stream reconnecting')};const imgStream=new EventSource('/api/img2img/events');imgStream.addEventListener('jobs',ev=>{try{const j=JSON.parse(ev.data);jobs=[...jobs.filter(x=>String(x.socket_kind||'flux')!=='img2img'),...(j.jobs||[])];text('status','img2img broadcast');render();load()}catch(_){}});imgStream.onerror=()=>{text('status','img2img stream reconnecting')};setTimeout(reload,1200)}
search.oninput=render;kind.onchange=render;$('refresh').onclick=load;load();connect();
</script>
</body>
</html>`
}

func galleryHTML() string {
	return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flux gallery</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Noto+Serif+JP:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
:root{color-scheme:dark;--surface-0:#060810;--surface-1:#0c0e1a;--surface-2:#121629;--surface-3:#191e32;--text:#f1edf6;--muted:#9aa3bd;--soft:#cfd5e7;--ivory:#e8dce8;--sakura:#ffb7c5;--wisteria:#b48eff;--ocean:#64c8ff;--ember:#ff5638;--line:rgba(237,230,216,.11);--line2:rgba(237,230,216,.18);--glow-sakura:0 0 28px rgba(255,183,197,.18),0 0 6px rgba(255,183,197,.12);--glow-ocean:0 0 28px rgba(100,200,255,.16),0 0 6px rgba(100,200,255,.10)}
*{box-sizing:border-box}body{margin:0;background:var(--surface-0);color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:0;-webkit-font-smoothing:antialiased}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(90deg,rgba(212,69,53,.07),transparent 28%,rgba(100,200,255,.055) 72%,transparent),linear-gradient(180deg,rgba(255,183,197,.035),transparent 34%),radial-gradient(circle at 12% 8%,rgba(180,142,255,.13),transparent 32%),radial-gradient(circle at 86% 14%,rgba(100,200,255,.105),transparent 36%),linear-gradient(180deg,#060810 0%,#0c0e1a 46%,#111324 100%)}body:after{content:"";position:fixed;inset:0;pointer-events:none;opacity:.18;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)' opacity='0.22'/%3E%3C/svg%3E")}
main{position:relative;z-index:1;max-width:1500px;margin:0 auto;padding:24px clamp(16px,3vw,36px) 48px}.top{position:relative;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:22px;align-items:end;min-height:230px;overflow:hidden;border:1px solid rgba(237,230,216,.10);border-radius:8px;padding:28px;background:linear-gradient(105deg,rgba(6,8,16,.96) 0%,rgba(14,15,30,.84) 46%,rgba(48,19,38,.48) 100%),linear-gradient(180deg,rgba(255,183,197,.045),transparent 42%),linear-gradient(90deg,rgba(212,69,53,.18),transparent 34%,rgba(100,200,255,.16));box-shadow:0 28px 90px rgba(0,0,0,.35)}
.top:before{content:"";position:absolute;inset:auto 0 0;height:42%;pointer-events:none;background:linear-gradient(180deg,transparent,rgba(6,8,16,.72))}.top:after{content:"";position:absolute;inset:0;pointer-events:none;border-top:1px solid rgba(255,183,197,.20);background:linear-gradient(90deg,transparent,rgba(180,142,255,.08),rgba(100,200,255,.06),transparent);mix-blend-mode:screen}.top>*{position:relative;z-index:1}
.mark{display:inline-flex;align-items:center;gap:10px;color:var(--sakura);font-size:12px;letter-spacing:.22em;text-transform:uppercase}.mark:before{content:"";width:28px;height:28px;border:2px solid var(--ember);border-left-color:transparent;border-radius:50%;box-shadow:0 0 20px rgba(255,86,56,.28)}.title{max-width:940px;margin-top:14px;font-family:"Noto Serif JP",Georgia,serif;font-size:clamp(42px,5.2vw,76px);font-weight:400;line-height:.92;text-shadow:0 0 34px rgba(255,183,197,.14)}.sub{max-width:900px;color:var(--soft);margin-top:13px}.actions{display:flex;gap:10px;flex-wrap:wrap;justify-content:flex-end;align-self:start}
a.btn,button{appearance:none;min-height:40px;border:1px solid rgba(237,230,216,.16);background:rgba(6,8,16,.66);color:var(--text);border-radius:7px;padding:9px 13px;text-decoration:none;font-weight:750;cursor:pointer;transition:border-color .16s ease,background .16s ease,transform .16s ease,box-shadow .16s ease}a.btn:hover,button:hover{border-color:rgba(255,183,197,.35);background:rgba(18,22,41,.82);transform:translateY(-1px)}.danger{border-color:rgba(255,86,56,.36);color:#ffb0bd;background:rgba(80,22,30,.28)}
.summary{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-top:18px}.stat{position:relative;overflow:hidden;background:linear-gradient(180deg,rgba(255,255,255,.028),transparent 42%),rgba(12,14,26,.78);border:1px solid var(--line);border-radius:8px;padding:14px;min-width:0;box-shadow:0 18px 64px rgba(0,0,0,.28);backdrop-filter:blur(16px)}.stat:before{content:"";position:absolute;inset:0 0 auto;height:1px;background:linear-gradient(90deg,transparent,rgba(255,183,197,.30),rgba(100,200,255,.25),transparent)}.stat b{display:block;color:var(--ivory);font-size:24px;line-height:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:0 0 22px rgba(180,142,255,.16)}.stat:nth-child(1) b{color:var(--sakura);text-shadow:var(--glow-sakura)}.stat:nth-child(2) b{color:var(--ocean);text-shadow:var(--glow-ocean)}.stat:nth-child(3) b{color:var(--wisteria)}.stat:nth-child(5) b{color:var(--gold);text-shadow:var(--glow-sakura)}.stat span{display:block;color:var(--muted);font-size:12px;letter-spacing:.1em;text-transform:uppercase;margin-top:7px}.livebar{margin-top:12px;border:1px solid rgba(237,230,216,.11);border-radius:8px;background:rgba(12,14,26,.58);padding:10px}.livebar .rail{height:9px;border-radius:999px;background:rgba(237,230,216,.08);overflow:hidden;border:1px solid rgba(237,230,216,.08)}.livebar i{display:block;height:100%;width:0;border-radius:999px;background:linear-gradient(90deg,var(--ember),var(--sakura),var(--ocean));box-shadow:var(--glow-sakura);transition:width .22s ease}.livebar .copy{display:flex;justify-content:space-between;gap:12px;margin-top:8px;color:var(--muted);font-size:12px}.livebar strong{color:var(--soft);font-weight:700}.stream-dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--ember);box-shadow:0 0 12px rgba(255,86,56,.22);margin-right:7px}.stream-dot.on{background:#55ffc8;box-shadow:0 0 14px rgba(85,255,200,.32)}
.toolbar{display:grid;grid-template-columns:minmax(220px,1fr) auto auto auto auto auto;align-items:center;gap:10px;margin:18px 0}.search{width:100%;border:1px solid rgba(237,230,216,.13);background:rgba(6,8,16,.78);color:var(--text);border-radius:7px;padding:12px 13px;font:inherit;outline:none}.search:focus{border-color:rgba(255,183,197,.44);box-shadow:0 0 0 3px rgba(255,183,197,.08)}.mode-toggle[data-active="true"]{border-color:rgba(255,213,128,.48);background:rgba(255,213,128,.10);box-shadow:0 0 24px rgba(255,183,197,.14);color:var(--gold)}.pick-status{color:var(--muted);font-size:12px;white-space:nowrap}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(172px,1fr));gap:12px}.cell{appearance:none;display:block;padding:0;border:1px solid var(--line);border-radius:8px;background:rgba(12,14,26,.74);aspect-ratio:1;overflow:hidden;position:relative;cursor:pointer;box-shadow:0 12px 36px rgba(0,0,0,.22);transition:transform .16s ease,border-color .16s ease,box-shadow .16s ease,filter .16s ease}.cell:before{content:"";position:absolute;inset:0;z-index:1;pointer-events:none;background:linear-gradient(180deg,transparent 55%,rgba(6,8,16,.8));opacity:.9}.cell:hover{transform:translateY(-2px);border-color:rgba(255,183,197,.35);box-shadow:0 22px 56px rgba(0,0,0,.34),var(--glow-sakura)}.cell.picked{border-color:rgba(255,213,128,.72);box-shadow:0 22px 62px rgba(0,0,0,.38),0 0 0 2px rgba(255,213,128,.16),0 0 34px rgba(255,183,197,.30);filter:saturate(1.1)}.cell.picked:after{content:"";position:absolute;inset:0;z-index:2;pointer-events:none;border-radius:7px;background:linear-gradient(135deg,rgba(255,213,128,.18),transparent 34%,rgba(255,183,197,.16));mix-blend-mode:screen}.cell img,.cell video{width:100%;height:100%;object-fit:cover;display:block;transition:transform .28s ease}.cell:hover img,.cell:hover video{transform:scale(1.035)}.cell i{position:absolute;z-index:3;left:8px;right:8px;bottom:8px;background:rgba(6,8,16,.74);border:1px solid rgba(255,255,255,.13);border-radius:6px;color:var(--soft);font:11px/1.25 ui-monospace,SFMono-Regular,Menlo,monospace;padding:6px 7px;font-style:normal;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell.pending{opacity:0;pointer-events:none}
.cell.arrive{animation:cellArrive .5s cubic-bezier(.2,.75,.2,1) both}
@keyframes cellArrive{from{opacity:0;transform:translateY(7px) scale(.984)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.cell.arrive{animation:none}.cell.pending{opacity:1}.cell,.cell img,.cell video{transition:none}}
.empty{border:1px dashed rgba(237,230,216,.16);border-radius:8px;padding:28px;color:var(--muted);background:rgba(12,14,26,.58);text-align:center}
.preview{position:fixed;inset:0;background:rgba(2,4,9,.9);display:none;grid-template-columns:minmax(0,1fr) 340px;gap:18px;padding:20px;z-index:30}.preview.open{display:grid}.preview .image{display:grid;place-items:center;min-width:0;position:relative}.preview img,.preview video{max-width:100%;max-height:calc(100vh - 40px);border-radius:8px;border:1px solid rgba(237,230,216,.14);background:rgba(6,8,16,.82);box-shadow:0 28px 100px rgba(0,0,0,.55)}.preview video{display:none}.nav{position:absolute;top:50%;transform:translateY(-50%);width:46px;height:64px;padding:0;border-radius:8px;background:rgba(12,14,26,.78);border-color:rgba(255,255,255,.18);font-size:34px;line-height:1;color:var(--text);backdrop-filter:blur(10px)}.nav:hover{background:rgba(25,30,50,.9);box-shadow:var(--glow-sakura)}.nav.prev{left:12px}.nav.next{right:12px}.close-preview{position:absolute;top:12px;right:12px;width:40px;height:40px;padding:0;border-radius:8px;background:rgba(12,14,26,.78);border-color:rgba(255,255,255,.18);font-size:20px;line-height:1}.meta{background:linear-gradient(180deg,rgba(255,183,197,.035),transparent 34%),rgba(12,14,26,.84);border:1px solid var(--line);border-radius:8px;padding:16px;overflow:auto;box-shadow:0 18px 64px rgba(0,0,0,.32)}.meta h2{font-size:12px;margin:0 0 12px;color:var(--sakura);letter-spacing:.18em;text-transform:uppercase}.meta code{display:block;white-space:pre-wrap;word-break:break-word;color:var(--soft);font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace}
.confirm{position:fixed;inset:0;z-index:40;display:none;place-items:center;background:rgba(4,6,10,.74);padding:18px;backdrop-filter:blur(10px)}.confirm.open{display:grid}.confirm-card{width:min(520px,100%);background:linear-gradient(180deg,rgba(255,183,197,.035),transparent 38%),rgba(12,14,26,.94);border:1px solid rgba(255,183,197,.18);border-radius:8px;box-shadow:0 24px 80px rgba(0,0,0,.48);padding:17px}.confirm-card h2{margin:0 0 8px;color:var(--sakura);font-size:13px;letter-spacing:.16em;text-transform:uppercase}.confirm-card p{margin:0 0 12px;color:var(--soft)}.confirm-card code{display:block;margin:10px 0;padding:10px;border:1px solid var(--line);border-radius:6px;background:rgba(6,8,16,.72);color:var(--ivory);font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}.confirm-card input{width:100%;border:1px solid rgba(237,230,216,.13);background:rgba(6,8,16,.86);color:var(--text);border-radius:6px;padding:10px;font:inherit}.confirm-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:12px}.confirm-error{min-height:20px;margin-top:8px;color:#ff9cb1;font-size:13px}
@media(max-width:860px){main{padding:18px 14px 34px}.top{grid-template-columns:1fr}.actions{justify-content:flex-start}.summary{grid-template-columns:repeat(2,minmax(0,1fr))}.toolbar{grid-template-columns:1fr}.toolbar button{width:100%}.pick-status{text-align:center}.grid{grid-template-columns:repeat(auto-fill,minmax(126px,1fr));gap:9px}.preview{grid-template-columns:1fr;padding:12px}.meta{display:none}.title{font-size:34px}}
</style>
</head>
<body>
<main>
<div class="top">
<div><div class="mark">anime.productions archive</div><div class="title" id="title">collection</div><div class="sub" id="path"></div></div>
<div class="actions"><a class="btn" href="{{STUDIO_URL}}">Studio</a><a class="btn" id="raw" href="#">Raw files</a><button id="delete" class="danger" type="button">Delete</button></div>
</div>
<section class="summary">
<div class="stat"><b id="count">0</b><span>images</span></div>
<div class="stat"><b id="total">0</b><span>planned</span></div>
<div class="stat"><b id="kind">collection</b><span>type</span></div>
<div class="stat"><b id="updated">-</b><span>updated</span></div>
<div class="stat"><b id="picked">0</b><span>picks</span></div>
</section>
<section class="livebar" aria-label="production progress"><div class="rail"><i id="progressBar"></i></div><div class="copy"><span><span id="streamDot" class="stream-dot"></span><strong id="streamState">connecting broadcast</strong></span><span id="progressText">0 / 0 · 0%</span></div></section>
<div class="toolbar"><input id="search" class="search" placeholder="filter by folder, seed, or filename"><button id="clear" type="button">Clear filter</button><button id="pickMode" class="mode-toggle" type="button" data-active="false">Pick mode</button><button id="clearPicks" type="button">Clear picks</button><button id="savePicks" type="button">Save picks</button><span id="pickStatus" class="pick-status">browse mode</span></div>
<section id="grid" class="grid"><div class="empty">Loading collection</div></section>
</main>
<div id="preview" class="preview" role="dialog" aria-modal="true">
<div class="image"><button id="prevImage" class="nav prev" type="button" aria-label="Previous image">&lsaquo;</button><img alt=""><video controls loop playsinline></video><button id="nextImage" class="nav next" type="button" aria-label="Next image">&rsaquo;</button><button id="closePreview" class="close-preview" type="button" aria-label="Close preview">&times;</button></div><div class="meta"><h2>media</h2><code id="meta"></code></div>
</div>
<div id="confirm" class="confirm" role="dialog" aria-modal="true" aria-labelledby="confirmTitle">
<div class="confirm-card">
<h2 id="confirmTitle">Move collection to trash</h2>
<p>This hides the collection from studio and moves the folder to <code>.trash</code>. The files are not permanently deleted.</p>
<code id="confirmPhrase"></code>
<input id="confirmInput" autocomplete="off" spellcheck="false" placeholder="type the confirmation phrase">
<div id="confirmError" class="confirm-error"></div>
<div class="confirm-actions"><button id="cancelDelete" type="button">Cancel</button><button id="confirmDelete" class="danger" type="button">Move to .trash</button></div>
</div>
</div>
<script>
const collectionPath={{GALLERY_PATH_JSON}}, grid=document.getElementById('grid'), search=document.getElementById('search'), preview=document.getElementById('preview'), previewImg=preview.querySelector('img'), previewVideo=preview.querySelector('video'), meta=document.getElementById('meta'), confirmDialog=document.getElementById('confirm'), confirmInput=document.getElementById('confirmInput'), confirmError=document.getElementById('confirmError'), confirmDelete=document.getElementById('confirmDelete'), pickModeButton=document.getElementById('pickMode'), savePicks=document.getElementById('savePicks'), clearPicks=document.getElementById('clearPicks'), pickStatus=document.getElementById('pickStatus');
let images=[], visibleImages=[], previewIndex=-1, currentCollection=null, picked=new Set(), dirtyPicks=false, pickMode=false;
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function text(id,v){document.getElementById(id).textContent=String(v??'')}
function progressPct(c){const count=Number(c?.count??images.length??0),total=Number(c?.total??count??0);return total?Math.max(0,Math.min(100,(count/total)*100)):0}
function updateProgress(c,state){const count=Number(c?.count??images.length??0),total=Number(c?.total??count??0),pct=progressPct(c);document.getElementById('progressBar').style.width=pct+'%';document.getElementById('progressText').textContent=count+' / '+total+' · '+Math.round(pct)+'%';document.getElementById('streamState').textContent=state||'broadcast live';document.getElementById('streamDot').classList.toggle('on',state!=='broadcast reconnecting'&&state!=='broadcast unavailable')}
function applySnapshot(j,state){if(!j.ok){grid.innerHTML='<div class="empty">'+esc(j.error||'Collection unavailable')+'</div>';updateProgress(currentCollection,state||'broadcast unavailable');return}const c=j.collection||{};currentCollection=c;images=j.images||[];if(!dirtyPicks)picked=new Set(j.picks||images.filter(x=>x.picked).map(x=>x.name));text('title',c.name||'collection');text('path',c.path||collectionPath);text('count',c.count??images.length);text('total',c.total??images.length);text('kind',c.kind||'collection');text('updated',c.updated_text||'-');document.getElementById('raw').href=c.raw_url||('/outputs/'+collectionPath);updateProgress(c,state||'broadcast live');render()}
function mediaTag(x){return x.type==='video'?'<video muted loop autoplay playsinline preload="metadata" src="'+esc(x.url)+'"></video>':'<img loading="lazy" decoding="async" src="'+esc(x.url)+'" alt="">'}
// The grid is reconciled by cell name rather than rebuilt. A collection that
// is still rendering broadcasts on every batch, and rewriting innerHTML each
// time tore down every tile: images refetched (the cancelled-request storm in
// the proxy log) and every reveal animation restarted, so the wall never
// settled. Patching in place means a tile animates exactly once, when it first
// arrives.
const tiles=new Map(), seen=new Set(), paint={queue:[],timer:null};
let firstPaint=true;
const reduceMotion=!!(window.matchMedia&&window.matchMedia('(prefers-reduced-motion: reduce)').matches);
const videoWatcher=window.IntersectionObserver?new IntersectionObserver(entries=>{for(const e of entries){const v=e.target.querySelector('video');if(!v)continue;if(e.isIntersecting){v.play().catch(()=>{})}else{v.pause()}}},{rootMargin:'150px'}):null;
function cellIndex(name){const m=/(\d+)\.[a-z0-9]+$/i.exec(String(name));return m?parseInt(m[1],10):-1}
function makeTile(x){const el=document.createElement('button');el.type='button';el.className='cell';el.dataset.name=x.name;el.innerHTML=mediaTag(x)+'<i>'+esc(x.name)+'</i>';if(videoWatcher&&x.type==='video')videoWatcher.observe(el);return el}
function patchTile(el,x,i){el.dataset.i=i;const on=picked.has(x.name);el.classList.toggle('picked',on);el.setAttribute('aria-pressed',on?'true':'false')}
// Release nearest-first from the centre of the arriving run. Block sharding
// delivers contiguous runs of cells, so this reads as a wavefront spreading
// along the traversal rather than cells popping in at random.
function releaseOrder(names){const idx=names.map(cellIndex);const mid=idx.reduce((a,b)=>a+b,0)/Math.max(1,idx.length);return names.map((n,k)=>[n,Math.abs(idx[k]-mid)]).sort((a,b)=>a[1]-b[1]).map(p=>p[0])}
// Drain over a window rather than at a fixed rate: a burst of batch_size cells
// spreads out, and a backlog drains faster so it is clear before the next
// batch lands. Same idea as the stage filmstrip's queueFrames pacing.
function pumpDelay(){const n=paint.queue.length;return n?Math.max(16,Math.min(220,1600/n)):0}
function pump(){const name=paint.queue.shift();if(name===undefined){paint.timer=null;return}reveal(name);paint.timer=setTimeout(pump,pumpDelay())}
function reveal(name){const el=tiles.get(name);if(!el)return;el.classList.remove('pending');if(reduceMotion)return;const r=el.getBoundingClientRect();
  // Animate only what someone can actually see; offscreen tiles just appear.
  if(r.bottom>-200&&r.top<window.innerHeight+200){el.classList.add('arrive');el.addEventListener('animationend',()=>el.classList.remove('arrive'),{once:true})}}
function enqueue(names){if(!names.length)return;paint.queue.push(...releaseOrder(names));if(!paint.timer&&!document.hidden)pump()}
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(paint.timer){clearTimeout(paint.timer);paint.timer=null}}else if(paint.queue.length&&!paint.timer){pump()}});
function render(){const q=search.value.trim().toLowerCase();visibleImages=q?images.filter(x=>String(x.name).toLowerCase().includes(q)):images;
  if(!visibleImages.length){grid.innerHTML='<div class="empty">No media match this filter</div>';tiles.clear();firstPaint=true;updatePickStatus();return}
  if(grid.firstElementChild&&grid.firstElementChild.classList.contains('empty')){grid.innerHTML='';tiles.clear();firstPaint=true}
  const wanted=new Set(visibleImages.map(x=>x.name));
  for(const entry of [...tiles]){if(!wanted.has(entry[0])){entry[1].remove();tiles.delete(entry[0])}}
  const fresh=[];let prev=null;
  for(let i=0;i<visibleImages.length;i++){const x=visibleImages[i];let el=tiles.get(x.name);
    if(!el){el=makeTile(x);tiles.set(x.name,el);
      // Pace only genuinely new cells. Re-showing a tile that a search filter
      // had hidden must be instant, never queued behind a reveal.
      if(!firstPaint&&!seen.has(x.name)){el.classList.add('pending');fresh.push(x.name)}}
    seen.add(x.name);patchTile(el,x,i);
    const want=prev?prev.nextSibling:grid.firstChild;
    if(el!==want)grid.insertBefore(el,want);
    prev=el}
  firstPaint=false;if(fresh.length)enqueue(fresh);updatePickStatus()}
function updatePickStatus(){const count=picked.size;text('picked',count);pickModeButton.dataset.active=pickMode?'true':'false';pickModeButton.textContent=pickMode?'Picking on':'Pick mode';pickStatus.textContent=pickMode?(count+' selected'+(dirtyPicks?' · unsaved':'')):(count?count+' saved pick'+(count===1?'':'s'):'browse mode');savePicks.disabled=!dirtyPicks;clearPicks.disabled=count===0}
function togglePick(i){const image=visibleImages[i];if(!image)return;if(picked.has(image.name)){picked.delete(image.name)}else{picked.add(image.name)}dirtyPicks=true;render()}
function openPreview(i){if(!visibleImages.length)return;previewIndex=(i+visibleImages.length)%visibleImages.length;const image=visibleImages[previewIndex];previewImg.style.display=image.type==='video'?'none':'';previewVideo.style.display=image.type==='video'?'':'none';if(image.type==='video'){previewImg.src='';previewVideo.src=image.url;previewVideo.play().catch(()=>{})}else{previewVideo.pause();previewVideo.removeAttribute('src');previewVideo.load();previewImg.src=image.url}meta.textContent=(previewIndex+1)+' / '+visibleImages.length+'\n'+image.name+'\n'+image.url;preview.classList.add('open')}
function closePreview(){preview.classList.remove('open');previewImg.src='';previewVideo.pause();previewVideo.removeAttribute('src');previewVideo.load();previewIndex=-1}
function stepPreview(delta){if(previewIndex<0)return;openPreview(previewIndex+delta)}
grid.onclick=ev=>{const cell=ev.target.closest('.cell');if(!cell)return;const i=Number(cell.dataset.i);if(pickMode){togglePick(i);return}openPreview(i)}
grid.ondblclick=ev=>{const cell=ev.target.closest('.cell');if(!cell)return;openPreview(Number(cell.dataset.i))}
preview.onclick=ev=>{if(ev.target===preview)closePreview()}
document.getElementById('closePreview').onclick=closePreview;
document.getElementById('prevImage').onclick=ev=>{ev.stopPropagation();stepPreview(-1)}
document.getElementById('nextImage').onclick=ev=>{ev.stopPropagation();stepPreview(1)}
search.oninput=render;document.getElementById('clear').onclick=()=>{search.value='';render()}
pickModeButton.onclick=()=>{pickMode=!pickMode;updatePickStatus()}
clearPicks.onclick=()=>{picked.clear();dirtyPicks=true;render()}
savePicks.onclick=async()=>{savePicks.disabled=true;savePicks.textContent='Saving...';pickStatus.textContent=picked.size+' selected · saving';try{const r=await fetch('/api/collection/picks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:collectionPath,picks:[...picked]})});const j=await r.json();if(!j.ok){pickStatus.textContent=j.error||'Save failed';savePicks.disabled=false;savePicks.textContent='Save picks';return}picked=new Set(j.picks||[...picked]);dirtyPicks=false;savePicks.textContent='Saved';render();setTimeout(()=>{savePicks.textContent='Save picks';updatePickStatus()},900)}catch(err){pickStatus.textContent=err?.message||'Save failed';savePicks.disabled=false;savePicks.textContent='Save picks'}}
async function load(){try{const r=await fetch('/api/collection?path='+encodeURIComponent(collectionPath));applySnapshot(await r.json(),'snapshot fallback')}catch(err){grid.innerHTML='<div class="empty">'+esc(err?.message||'Collection unavailable')+'</div>';updateProgress(currentCollection,'broadcast unavailable')}}
function startBroadcast(){if(!window.EventSource){load();return}const es=new EventSource('/api/gallery/events/'+collectionPath.split('/').map(encodeURIComponent).join('/'));es.addEventListener('gallery',ev=>{try{applySnapshot(JSON.parse(ev.data),'broadcast live')}catch(err){updateProgress(currentCollection,'broadcast parse error')}});es.onerror=()=>{updateProgress(currentCollection,'broadcast reconnecting')};setTimeout(()=>{if(!currentCollection)load()},1200)}
function deletePhrase(){const c=currentCollection||{};return 'delete '+(c.name||collectionPath)}
function openDeleteDialog(){confirmError.textContent='';confirmInput.value='';confirmDelete.disabled=false;confirmDelete.textContent='Move to .trash';document.getElementById('confirmPhrase').textContent=deletePhrase();confirmDialog.classList.add('open');setTimeout(()=>confirmInput.focus(),0)}
function closeDeleteDialog(){confirmDialog.classList.remove('open')}
document.getElementById('delete').onclick=openDeleteDialog;
document.getElementById('cancelDelete').onclick=closeDeleteDialog;
confirmDialog.onclick=ev=>{if(ev.target===confirmDialog)closeDeleteDialog()}
confirmInput.oninput=()=>{confirmError.textContent=''}
confirmDelete.onclick=async()=>{if(confirmInput.value!==deletePhrase()){confirmError.textContent='Confirmation phrase does not match.';confirmInput.focus();return}confirmDelete.disabled=true;confirmDelete.textContent='Moving...';confirmError.textContent='';try{const r=await fetch('/api/collection?path='+encodeURIComponent(collectionPath),{method:'DELETE'});const j=await r.json();if(!j.ok){confirmError.textContent=j.error||'Delete failed.';confirmDelete.disabled=false;confirmDelete.textContent='Move to .trash';return}location.href='/'}catch(err){confirmError.textContent=err?.message||'Delete failed.';confirmDelete.disabled=false;confirmDelete.textContent='Move to .trash'}}
document.addEventListener('keydown',ev=>{if(confirmDialog.classList.contains('open')){if(ev.key==='Escape')closeDeleteDialog();return}if(!preview.classList.contains('open'))return;if(ev.key==='Escape')closePreview();if(ev.key==='ArrowLeft')stepPreview(-1);if(ev.key==='ArrowRight')stepPreview(1)})
startBroadcast();
</script>
</body>
</html>`
}

func validBackend(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "", "auto", "cuda", "mps", "mlx", "coreml", "ane", "cpu":
		return true
	default:
		return false
	}
}

func iterationSeed(seed string, index int) string {
	seed = strings.TrimSpace(seed)
	if seed == "" || index == 0 {
		return seed
	}
	n, err := strconv.ParseInt(seed, 10, 64)
	if err != nil {
		return seed
	}
	return strconv.FormatInt(n+int64(index), 10)
}

func iterationFilename(filename string, index, total int) string {
	filename = strings.TrimSpace(filename)
	if filename == "" || total <= 1 {
		return filename
	}
	ext := path.Ext(filename)
	stem := strings.TrimSuffix(filename, ext)
	if ext == "" {
		ext = ".png"
	}
	return fmt.Sprintf("%s-%02d%s", stem, index+1, ext)
}

func truthy(value string) bool {
	switch strings.ToLower(strings.TrimSpace(value)) {
	case "1", "true", "yes", "on":
		return true
	default:
		return false
	}
}

func atlasStudioHTML(cfg config.Config) string {
	return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flux atlas studio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Noto+Serif+JP:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
:root{color-scheme:dark;--bg:#050711;--panel:#0c0f1d;--panel2:#12172b;--text:#f1edf6;--muted:#9fa6be;--soft:#cfd5e7;--line:rgba(237,230,216,.12);--line2:rgba(237,230,216,.20);--gold:#ffd580;--sakura:#ffb7c5;--ocean:#64c8ff;--ember:#d44535;--green:#00ffc8;--wisteria:#b48eff;--sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--serif:"Noto Serif JP",Georgia,serif}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font:14px/1.45 var(--sans);letter-spacing:0;-webkit-font-smoothing:antialiased}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(90deg,rgba(212,69,53,.08),transparent 30%,rgba(100,200,255,.06) 76%,transparent),radial-gradient(circle at 14% 6%,rgba(180,142,255,.16),transparent 34%),radial-gradient(circle at 90% 8%,rgba(255,183,197,.12),transparent 36%),linear-gradient(180deg,#050711,#0b0e1b 52%,#111426)}
a{color:inherit;text-decoration:none}main{position:relative;z-index:1;max-width:1580px;margin:0 auto;padding:16px clamp(14px,3vw,34px) 36px}.top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:18px;align-items:center;border-bottom:1px solid rgba(237,230,216,.09);padding-bottom:14px}.mark{display:inline-flex;align-items:center;gap:10px;color:var(--muted);font-size:12px}.mark:before{content:"";width:22px;height:22px;border:2px solid var(--ember);border-left-color:transparent;border-radius:50%;box-shadow:0 0 20px rgba(212,69,53,.35)}.title{font-family:var(--serif);font-size:clamp(30px,3.8vw,52px);font-weight:400;line-height:.98;margin-top:8px}.sub{color:var(--muted);max-width:940px;margin-top:6px;font-size:13px}.nav{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end}.layout{display:grid;grid-template-columns:390px minmax(0,1fr);gap:18px;margin-top:18px}.stack{display:grid;gap:18px}section{position:relative;overflow:hidden;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.026),transparent 38%),rgba(12,15,29,.82);box-shadow:0 22px 70px rgba(0,0,0,.30);padding:18px;backdrop-filter:blur(18px)}section:before{content:"";position:absolute;inset:0 0 auto;height:1px;background:linear-gradient(90deg,transparent,rgba(255,213,128,.34),rgba(100,200,255,.25),transparent)}h2{font-size:12px;text-transform:uppercase;letter-spacing:.18em;color:var(--sakura);margin:0 0 14px}.head{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:center;border:1px solid rgba(255,213,128,.14);border-radius:8px;background:rgba(5,7,17,.44);padding:12px;margin-bottom:6px}.head b{display:block;color:var(--gold);font-size:16px;text-shadow:0 0 24px rgba(255,213,128,.14)}.head span{display:block;color:var(--muted);font-size:12px;margin-top:3px}.head>span:last-child{margin:0;border:1px solid rgba(255,183,197,.20);border-radius:999px;color:var(--sakura);padding:6px 9px;background:rgba(255,183,197,.045);white-space:nowrap}
label{display:block;color:var(--muted);font-size:12px;letter-spacing:.08em;text-transform:uppercase;margin:11px 0 6px}textarea,input,select{width:100%;border:1px solid rgba(237,230,216,.13);border-radius:7px;background:rgba(5,7,17,.82);color:var(--text);padding:11px 12px;font:inherit;outline:none}textarea:focus,input:focus,select:focus{border-color:rgba(255,213,128,.48);box-shadow:0 0 0 3px rgba(255,213,128,.08)}textarea{min-height:142px;resize:vertical}.row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.two{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}button,.button{min-height:40px;border:1px solid rgba(255,213,128,.20);border-radius:7px;background:rgba(5,7,17,.64);color:var(--text);padding:10px 13px;font-weight:750;cursor:pointer;text-decoration:none;transition:border-color .16s ease,background .16s ease,box-shadow .16s ease,transform .16s ease}button:hover,.button:hover{border-color:rgba(255,183,197,.34);background:rgba(18,23,43,.86);transform:translateY(-1px)}button.primary,.button.primary{border-color:transparent;background:linear-gradient(135deg,var(--ember),#ff6b3d);box-shadow:0 0 26px rgba(212,69,53,.16)}button.warn{color:var(--gold)}.note{color:var(--muted);font-size:12px;margin-top:9px}.message{border:1px solid rgba(255,213,128,.13);border-radius:8px;background:rgba(5,7,17,.50);padding:13px;color:var(--muted);min-height:72px}.message b{color:var(--gold)}
.summary{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.stat{border:1px solid var(--line);border-radius:8px;background:rgba(5,7,17,.50);padding:12px}.stat b{display:block;font-size:24px;line-height:1;color:var(--gold);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.stat span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.1em;margin-top:7px}.jobs{display:grid;gap:12px}.job{border:1px solid rgba(237,230,216,.12);border-radius:8px;background:rgba(5,7,17,.52);padding:14px;display:grid;gap:12px}.job-top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:start}.job b{color:var(--gold);font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.state{display:inline-flex;align-items:center;gap:6px;border:1px solid rgba(255,183,197,.18);border-radius:999px;color:var(--sakura);padding:5px 9px;background:rgba(255,183,197,.045);font-size:12px;white-space:nowrap}.dot{width:8px;height:8px;border-radius:50%;background:var(--ember)}.dot.on{background:var(--green);box-shadow:0 0 16px rgba(0,255,200,.38)}.prompt{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.bars{display:grid;gap:8px}.barline{display:grid;grid-template-columns:112px minmax(0,1fr) 64px;gap:10px;align-items:center;color:var(--muted);font-size:12px}.bar{height:8px;border:1px solid rgba(237,230,216,.07);border-radius:999px;overflow:hidden;background:rgba(237,230,216,.08)}.bar i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--ember),var(--gold),var(--ocean));box-shadow:0 0 24px rgba(255,213,128,.18);transition:width .22s ease}.metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}.metric{border:1px solid rgba(237,230,216,.10);border-radius:7px;background:rgba(12,15,29,.58);padding:8px;min-width:0}.metric b{display:block;color:var(--soft);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.metric span{display:block;color:var(--muted);font-size:9px;text-transform:uppercase;letter-spacing:.08em;margin-top:4px}.job-actions{display:flex;gap:8px;flex-wrap:wrap}.collections{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px}.collection{border:1px solid rgba(237,230,216,.11);border-radius:8px;overflow:hidden;background:rgba(5,7,17,.48);display:grid;grid-template-rows:118px auto}.collection img{width:100%;height:100%;object-fit:cover;background:#050711}.collection div{padding:10px}.collection b{display:block;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--text)}.collection span{display:block;color:var(--muted);font-size:11px;margin-top:4px}.empty{border:1px dashed rgba(237,230,216,.15);border-radius:8px;padding:18px;color:var(--muted);background:rgba(5,7,17,.42)}
@media(max-width:980px){main{padding:14px 12px 32px}.top,.layout{grid-template-columns:1fr}.nav{justify-content:flex-start}.row,.two,.summary,.metrics{grid-template-columns:1fr}.barline{grid-template-columns:1fr}.title{font-size:34px}textarea{min-height:132px}}
</style>
</head>
<body>
<main>
<div class="top">
<div><div class="mark">anime.productions · flux atlas</div><div class="title">Latent sphere console</div><div class="sub">Dedicated text-to-latent atlas workspace. Socket-backed FLUX.1 dev on MPS. Model: ` + html.EscapeString(cfg.ModelDir) + `</div></div>
<div class="nav"><a class="button" href="./">Studio</a><a class="button" href="/gallery/atlas">Atlas gallery</a></div>
</div>
<div class="layout">
<div class="stack">
<section>
<h2>launch atlas</h2>
<div class="head"><div><b>Sphere atlas lane</b><span>No image-to-image source. Samples a 1024x64 latent sphere map like the earlier working atlas.</span></div><span>dev · MPS · quality</span></div>
<label>prompt</label><textarea id="atlasPrompt">Surreal cinematic architectural city scene in Samara, Russia, old carved wooden houses in the historic center, Soviet apartment blocks, noble stone buildings, private homes and Dubai-like glass towers, wet streets, ember light, dimensional urban depth, high contrast animated film still, visible camera motion and parallax, buildings and streets only, no people, no cloth, no fabric</textarea>
<div class="two"><div><label>study type</label><select id="atlasStudyType" onchange="updateAtlasMode()"><option value="">choose loop or atlas</option><option value="loop">loop study</option><option value="atlas">atlas study</option></select></div><div><label>atlas id</label><input id="atlasID" placeholder="auto"></div></div>
<div><label>run type</label><select id="atlasRunType" onchange="updateAtlasMode()"><option value="spot" selected>sparse scout</option><option value="fill">fill same atlas</option><option value="path">local path</option></select></div>
<div class="row"><div><label>samples</label><select id="atlasCells" onchange="updateAtlasNote()"><option value="1">1 cell test</option><option value="4">4 cell scout</option><option value="16">16 cells</option><option value="64" selected>64 cells</option><option value="128">128 cells</option><option value="256">256 cells</option></select></div><div><label>path start index</label><input id="atlasIndexStart" type="number" min="0" max="65535" step="1" value="0" oninput="updateAtlasNote()"></div><div><label>path end index</label><input id="atlasIndexEnd" type="number" min="1" max="65536" step="1" placeholder="auto" oninput="updateAtlasNote()"></div></div>
<div class="row"><div><label>resolution</label><select id="atlasSize" onchange="updateAtlasNote()"><option value="384">384 square</option><option value="512" selected>512 square</option><option value="640">640 square</option><option value="768">768 square</option></select></div><div><label>steps</label><input id="atlasSteps" type="number" min="1" max="120" value="36" oninput="updateAtlasNote()"></div><div><label>guidance</label><input id="atlasGuidance" type="number" min="0" max="20" step="0.1" value="4.4"></div></div>
<div class="row"><div><label>shell scale</label><input id="atlasShellScale" type="number" min="0.01" max="4" step="0.01" value="1.12"></div><div><label>seed lock</label><input id="atlasSeedLock" type="number" min="0" max="0.95" step="0.01" value="0.28"></div><div><label>coupling</label><input id="atlasShellCoupling" type="number" min="-16" max="16" step="0.01" value="0.92"></div></div>
<div class="two"><div><label>mode</label><select id="atlasMode"><option value="elliptic" selected>elliptic</option><option value="omega">omega</option><option value="sway">sway</option><option value="oscillatory">oscillatory</option></select></div><div><label>order</label><select id="atlasOrder"><option value="row_serpentine" selected>row serpentine</option><option value="column_serpentine">column serpentine</option><option value="raster">raster</option></select></div></div>
<div class="two"><div><label>adapter</label><select id="atlasAdapter"><option value="none" selected>none</option><option value="first-block-cache">first block cache</option><option value="atlas-xframe-cache">atlas x-frame cache</option></select></div><div><label>seed</label><input id="atlasSeed" placeholder="random"></div></div>
<div class="row"><div><label>cache threshold</label><input id="atlasCacheThreshold" type="number" min="0" max="1" step="0.01" value="0.12"></div><div><label>cache downsample</label><input id="atlasCacheDownsample" type="number" min="1" max="64" step="1" value="1"></div><div><label>cache warmup</label><input id="atlasCacheWarmup" type="number" min="0" max="120" step="1" value="0"></div></div>
<div id="atlasNote" class="note">64 nested sparse samples from 1024x64 · 36 steps · 512 square · socket MPS</div>
<div class="actions"><button class="primary" onclick="submitAtlas(false)">Queue atlas run</button><button onclick="submitAtlas(true)">Plan atlas</button></div>
</section>
<section><h2>activity</h2><div id="out" class="message">Ready.</div></section>
</div>
<div class="stack">
<section>
<h2>atlas progress</h2>
<div class="summary">
<div class="stat"><b id="runningCount">0</b><span>active</span></div>
<div class="stat"><b id="cellCount">0/0</b><span>cells</span></div>
<div class="stat"><b id="rateCount">0/h</b><span>rate</span></div>
<div class="stat"><b id="etaCount">-</b><span>eta</span></div>
</div>
<div id="jobs" class="jobs" style="margin-top:14px"><div class="empty">Connecting to atlas job stream.</div></div>
</section>
<section>
<h2>atlas collections</h2>
<div id="collections" class="collections"><div class="empty">Loading atlas collections.</div></div>
</section>
</div>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function escAttr(v){return esc(v)}
function n(id){const v=Number($(id).value);return Number.isFinite(v)?v:0}
function pct(done,total){return total?Math.max(0,Math.min(100,(Number(done||0)/Number(total||0))*100)):0}
function fmtPct(v){return Math.round(Number(v||0)*10)/10+'%'}
function fmtRate(v){const n=Number(v||0);return n>=100?Math.round(n)+'/h':n.toFixed(1)+'/h'}
function fmtTime(sec){sec=Number(sec||0);if(!sec)return '-';if(sec<60)return Math.round(sec)+'s';if(sec<3600)return Math.round(sec/60)+'m';return (sec/3600).toFixed(1)+'h'}
function atlasBody(dryRun){const runType=$('atlasRunType').value,studyType=$('atlasStudyType').value;return {prompt:$('atlasPrompt').value,id:$('atlasID').value,study_type:studyType,run_type:runType,cells:n('atlasCells')||64,index_start:n('atlasIndexStart')||0,index_end:n('atlasIndexEnd')||0,sample_mode:studyType==='loop'?'loop':runType==='path'?'contiguous':'nested_sparse',size:n('atlasSize')||512,steps:n('atlasSteps')||36,guidance:n('atlasGuidance')||4.4,seed:$('atlasSeed').value,shell_scale:n('atlasShellScale')||1.12,seed_lock:n('atlasSeedLock')||0.28,shell_coupling:n('atlasShellCoupling')||0.92,mode:$('atlasMode').value,traversal_order:$('atlasOrder').value,adapter:$('atlasAdapter').value,cache_threshold:n('atlasCacheThreshold')||0.12,cache_downsample:n('atlasCacheDownsample')||1,cache_warmup:n('atlasCacheWarmup')||0,dry_run:dryRun}}
function updateAtlasMode(){const path=$('atlasRunType').value==='path';$('atlasIndexStart').disabled=!path;$('atlasIndexEnd').disabled=!path;updateAtlasNote()}
function updateAtlasNote(){const cells=n('atlasCells')||64,steps=n('atlasSteps')||36,size=n('atlasSize')||512,runType=$('atlasRunType').value,start=n('atlasIndexStart')||0,end=n('atlasIndexEnd')||0;if(runType==='path'){$('atlasNote').textContent=cells+' contiguous path samples · index '+start+' to '+(end||start+cells)+' · '+steps+' steps · '+size+' square · socket MPS';return}const verb=runType==='fill'?'nested fill':'nested sparse';$('atlasNote').textContent=cells+' '+verb+' samples from 1024x64 · '+steps+' steps · '+size+' square · socket MPS'}
function say(html){$('out').innerHTML=html}
async function requestJSON(path,opts={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opts});return await r.json()}
async function submitAtlas(dryRun){updateAtlasNote();const j=await requestJSON('/api/atlas/submit'+(dryRun?'?dry_run=1':''),{method:'POST',body:JSON.stringify(atlasBody(dryRun))});if(!j.ok){say('<b>Request failed</b><br>'+esc(j.error||'Unknown error'));return}const p=j.plan||{},range=' · '+esc(p.sample_mode||'')+' · index '+esc(p.index_start??0)+'-'+esc(p.index_end??'');if(j.dry_run){say('<b>Atlas plan ready</b><br>'+esc(p.cells)+' sampled cell(s) · '+esc(p.grid)+' latent grid'+range+' · '+esc(p.width)+'x'+esc(p.height)+' · '+esc(p.steps)+' steps<br><a class="button" href="'+escAttr(j.viewer)+'" target="_blank" rel="noreferrer">Live viewer</a> <a class="button" href="'+escAttr(j.gallery)+'" target="_blank" rel="noreferrer">Gallery</a>');return}say('<b>Atlas queued</b><br>'+esc(j.job?.id||p.id||'atlas')+' · '+esc(p.cells)+' sampled cell(s)'+range+' · '+esc(p.steps)+' steps<br><a class="button" href="'+escAttr(j.viewer)+'" target="_blank" rel="noreferrer">Live viewer</a> <a class="button" href="'+escAttr(j.gallery)+'" target="_blank" rel="noreferrer">Gallery</a>');renderAtlasJobs([j.job]);loadCollections()}
async function cancelJob(id){const j=await requestJSON('/api/job/cancel',{method:'POST',body:JSON.stringify({id})});if(!j.ok)say('<b>Cancel failed</b><br>'+esc(j.error||'Unknown error'))}
function renderAtlasJobs(jobs){jobs=(jobs||[]).filter(x=>String(x?.kind||'')==='atlas_sphere');const active=jobs.filter(x=>['queued','running','cancelling'].includes(String(x.status||'').toLowerCase()));let done=0,total=0,rate=0,eta=0;active.forEach(x=>{done+=Number(x.step||x.atlas_done||0);total+=Number(x.total_steps||x.atlas_total||0);rate=Math.max(rate,Number(x.cells_per_hour||0));eta=Math.max(eta,Number(x.eta_seconds||0))});$('runningCount').textContent=active.length;$('cellCount').textContent=done+'/'+total;$('rateCount').textContent=fmtRate(rate);$('etaCount').textContent=fmtTime(eta);$('jobs').innerHTML=jobs.map(jobHTML).join('')||'<div class="empty">No atlas jobs are active. Queue one from the launch panel.</div>'}
function metric(label,value){return '<div class="metric"><b>'+esc(value)+'</b><span>'+esc(label)+'</span></div>'}
function jobHTML(x){const total=Number(x.total_steps||x.atlas_total||0),step=Number(x.step||x.atlas_done||0),cellStep=Number(x.cell_step||0),cellTotal=Number(x.cell_total_steps||x.steps||0),cellPct=pct(cellStep,cellTotal),overall=pct(step,total);const status=String(x.status||'queued');const cache=Number(x.cache_hit_rate||0);const phase=x.phase||'';let actions='<div class="job-actions">';if(['queued','running','cancelling'].includes(status.toLowerCase()))actions+='<button class="warn" data-job="'+escAttr(x.id||'')+'" onclick="cancelJob(this.dataset.job)">Cancel</button>';if(x.viewer_url)actions+='<a class="button" href="'+escAttr(x.viewer_url)+'" target="_blank" rel="noreferrer">Live viewer</a>';if(x.gallery_url)actions+='<a class="button" href="'+escAttr(x.gallery_url)+'" target="_blank" rel="noreferrer">Gallery</a>';actions+='</div>';return '<div class="job"><div class="job-top"><div><b>'+esc(x.id||'atlas')+'</b><div class="prompt">'+esc(x.prompt||'')+'</div></div><span class="state"><i class="dot '+(status==='running'?'on':'')+'"></i>'+esc(status)+' · '+esc(phase)+'</span></div><div class="bars"><div class="barline"><span>cells '+esc(step)+'/'+esc(total)+'</span><div class="bar"><i style="width:'+overall+'%"></i></div><span>'+fmtPct(overall)+'</span></div><div class="barline"><span>current cell '+esc(cellStep)+'/'+esc(cellTotal)+'</span><div class="bar"><i style="width:'+cellPct+'%"></i></div><span>'+fmtPct(cellPct)+'</span></div></div><div class="metrics">'+metric('eta',fmtTime(x.eta_seconds))+metric('rate',fmtRate(x.cells_per_hour))+metric('last cell',fmtTime(x.last_cell_seconds))+metric('sample',x.sample_mode||'contiguous')+metric('range',(x.index_start??0)+'-'+(x.index_end??''))+metric('grid',(x.n_rows||'?')+'x'+(x.n_cols||'?'))+metric('steps',x.steps||'')+metric('guidance',x.guidance||'')+metric('mode',x.mode||'')+metric('order',x.traversal_order||'')+metric('adapter',x.adapter||'none')+metric('shell',x.shell_scale||'')+metric('seed lock',x.seed_lock||'')+metric('cache hit',fmtPct(cache*100))+'</div>'+actions+'</div>'}
async function loadJobs(){const j=await requestJSON('/api/jobs');renderAtlasJobs(j.jobs||[])}
async function loadCollections(){const j=await requestJSON('/api/collections');const items=(j.collections||[]).filter(x=>x.kind==='atlas');$('collections').innerHTML=items.map(x=>'<a class="collection" href="'+escAttr(x.url)+'">'+(x.thumbnail?'<img src="'+escAttr(x.thumbnail)+'" alt="">':'<div class="empty">No preview</div>')+'<div><b>'+esc(x.name||x.path)+'</b><span>'+esc(x.count)+'/'+esc(x.total)+' · '+esc(x.updated_text||'')+'</span></div></a>').join('')||'<div class="empty">No atlas collections yet.</div>'}
function connect(){if(!window.EventSource){loadJobs();return}const es=new EventSource('/api/jobs/events');es.addEventListener('jobs',ev=>{try{const j=JSON.parse(ev.data);renderAtlasJobs(j.jobs||[])}catch(_){}});es.onerror=()=>say('<b>Job stream reconnecting</b><br>No polling is running.');}
updateAtlasMode();loadJobs();loadCollections();connect();
</script>
</body>
</html>`
}

func atlasWatchHTML(cfg config.Config) string {
	return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flux atlas watch</title>
<style>
:root{color-scheme:dark;--bg:#060810;--panel:#0c0f1d;--line:rgba(237,230,216,.13);--text:#f1edf6;--muted:#9fa6be;--gold:#ffd580;--sakura:#ffb7c5;--ocean:#64c8ff;--green:#00ffc8;--ember:#d44535}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:linear-gradient(135deg,#060810,#101426 58%,#170c18);color:var(--text);font:14px/1.45 Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1780px;margin:0 auto;padding:18px clamp(14px,2.4vw,30px) 36px}.top{display:flex;align-items:end;justify-content:space-between;gap:18px;border-bottom:1px solid rgba(237,230,216,.10);padding-bottom:16px}.mark{color:var(--sakura);font-size:12px;letter-spacing:.18em;text-transform:uppercase}.title{font:400 clamp(34px,4vw,58px)/.96 Georgia,serif;margin-top:8px}.sub{color:var(--muted);margin-top:8px;max-width:820px}.nav{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end}a,button{color:inherit}.button,button{min-height:38px;border:1px solid rgba(255,213,128,.20);border-radius:7px;background:rgba(6,8,16,.64);padding:9px 12px;text-decoration:none;font-weight:750;cursor:pointer}.button:hover,button:hover{border-color:rgba(255,183,197,.34);background:rgba(18,23,43,.86)}.layout{display:grid;grid-template-columns:320px minmax(0,1fr) 420px;gap:14px;margin-top:16px}.stage{display:grid;gap:14px}section{border:1px solid var(--line);border-radius:8px;background:rgba(12,15,29,.82);padding:14px;min-width:0;box-shadow:0 18px 60px rgba(0,0,0,.24)}h2{margin:0 0 12px;color:var(--sakura);font-size:12px;letter-spacing:.16em;text-transform:uppercase}.jobs,.collections{display:grid;gap:10px}.job{border:1px solid rgba(237,230,216,.11);border-radius:8px;background:rgba(5,7,17,.52);padding:10px;text-align:left;color:var(--text);cursor:pointer}.job.active{border-color:rgba(255,213,128,.50);box-shadow:0 0 0 2px rgba(255,213,128,.10)}.job b{display:block;color:var(--gold);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.job span{display:block;color:var(--muted);font-size:12px;margin-top:4px}.bar{height:7px;border-radius:999px;background:rgba(237,230,216,.08);overflow:hidden;margin-top:8px}.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--ember),var(--gold),var(--ocean));width:0}.stage-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.stage-head b{color:var(--gold)}.cells,.recent{display:grid;grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:9px}.cell,.recent a{border:1px solid rgba(237,230,216,.11);border-radius:7px;overflow:hidden;aspect-ratio:1;background:#050711;position:relative;text-decoration:none}.cell img,.recent img{width:100%;height:100%;object-fit:cover;display:block;transition:transform .18s ease}.cell:hover img,.recent a:hover img,.collection:hover img{transform:scale(1.035)}.cell i,.recent i{position:absolute;left:6px;bottom:6px;right:6px;border-radius:5px;background:rgba(5,7,17,.74);color:var(--muted);font:11px/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;padding:5px;font-style:normal;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.collection{display:grid;grid-template-columns:108px minmax(0,1fr);gap:10px;align-items:center;border:1px solid rgba(237,230,216,.11);border-radius:8px;background:rgba(5,7,17,.52);padding:7px;text-decoration:none;color:var(--text)}.collection img{width:108px;aspect-ratio:1;border-radius:6px;object-fit:cover;background:#050711}.collection b{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:var(--text);font-size:13px}.collection span{display:block;color:var(--muted);font-size:12px;margin-top:5px}.empty{border:1px dashed rgba(237,230,216,.16);border-radius:8px;color:var(--muted);padding:18px;text-align:center;background:rgba(5,7,17,.42)}
@media(max-width:1120px){.layout{grid-template-columns:1fr}.top{display:block}.nav{justify-content:flex-start;margin-top:12px}.cells{grid-template-columns:repeat(auto-fill,minmax(106px,1fr))}}
</style>
</head>
<body>
<main>
<div class="top"><div><div class="mark">anime.productions · atlas watch</div><div class="title">Atlas viewing room</div><div class="sub">Watch active FLUX atlas paths, inspect cells as they arrive, and jump into recent collections. Socket-backed, event-streamed, no polling loop. Model: ` + html.EscapeString(cfg.ModelDir) + `</div></div><div class="nav"><a class="button" href="/flux/atlas-studio">Atlas studio</a><a class="button" href="/gallery/atlas">Atlas gallery</a></div></div>
<div class="layout">
<section><h2>queue</h2><div id="jobs" class="jobs"><div class="empty">Connecting to jobs.</div></div></section>
<div class="stage">
<section><div class="stage-head"><div><b id="activeTitle">Select an atlas</b><div class="sub" id="activeMeta">Cells stream here as they render.</div></div><a id="openGallery" class="button" href="/gallery/atlas">Gallery</a></div><div id="cells" class="cells"><div class="empty">Waiting for an active atlas.</div></div></section>
<section><div class="stage-head"><div><b>Recent atlas cells</b><div class="sub">Newest rendered assets across all atlas collections.</div></div><a class="button" href="/gallery/atlas">All galleries</a></div><div id="recent" class="recent"><div class="empty">Loading recent cells.</div></div></section>
<section><div class="stage-head"><div><b>Horse motion lab</b><div class="sub">CPU-assembled preview, contact sheet, and motion scores from the active horse atlas.</div></div><a class="button" href="/outputs/atlas/spheremap_atlas_horse_gallop_volga_motion_path_1024c_20260715.sphere/_motion/scores.json" target="_blank" rel="noreferrer">Scores</a></div><video id="horseVideo" controls muted loop playsinline style="width:100%;border-radius:8px;border:1px solid rgba(237,230,216,.12);background:#050711"></video><a href="/outputs/atlas/spheremap_atlas_horse_gallop_volga_motion_path_1024c_20260715.sphere/_motion/contact_sheet_latest.jpg" target="_blank" rel="noreferrer"><img id="horseSheet" alt="" style="display:block;width:100%;margin-top:10px;border-radius:8px;border:1px solid rgba(237,230,216,.12);background:#050711"></a></section>
</div>
<section><h2>collections</h2><div id="collections" class="collections"><div class="empty">Loading collections.</div></div></section>
</div>
</main>
<script>
const $=id=>document.getElementById(id);let selected='',atlasES=null,seen=new Set();
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function pct(a,b){return b?Math.max(0,Math.min(100,(Number(a||0)/Number(b||0))*100)):0}
function choose(id,viewer,gallery){selected=id;seen=new Set();$('activeTitle').textContent=id;$('openGallery').href=gallery||('/gallery/atlas/'+encodeURIComponent(id+'.sphere'));$('cells').innerHTML='<div class="empty">Waiting for cells.</div>';if(atlasES)atlasES.close();atlasES=new EventSource('/api/atlas/events/'+encodeURIComponent(id));atlasES.addEventListener('atlas',ev=>{const d=JSON.parse(ev.data),p=d.progress||{};$('activeMeta').textContent=(d.status||'watching')+' · '+(d.rendered||p.current||0)+'/'+(d.total||p.total||0);(d.frames||[]).forEach(addCell)});atlasES.onerror=()=>{$('activeMeta').textContent='stream reconnecting'}}
function addCell(f){const src=String(f.src||'');if(!src||seen.has(src))return;seen.add(src);if($('cells').querySelector('.empty'))$('cells').textContent='';$('cells').insertAdjacentHTML('beforeend','<a class="cell" href="'+esc(src)+'" target="_blank" rel="noreferrer"><img src="'+esc(src)+'" alt=""><i>'+esc(String(f.index??'').padStart(5,'0'))+' · r '+esc(f.row??'')+' c '+esc(f.col??'')+'</i></a>')}
function renderJobs(jobs){jobs=(jobs||[]).filter(x=>String(x.kind||'')==='atlas_sphere');$('jobs').innerHTML=jobs.map(x=>{const step=Number(x.step||0),total=Number(x.total_steps||x.atlas_total||0),active=String(x.id||'')===selected;return '<button class="job '+(active?'active':'')+'" type="button" data-id="'+esc(x.id||'')+'" data-viewer="'+esc(x.viewer_url||'')+'" data-gallery="'+esc(x.gallery_url||'')+'"><b>'+esc(x.id||'atlas')+'</b><span>'+esc(x.status||'')+' · '+esc(x.phase||'')+' · '+step+'/'+total+'</span><div class="bar"><i style="width:'+pct(step,total)+'%"></i></div></button>'}).join('')||'<div class="empty">No active atlas jobs.</div>';if(!selected&&jobs[0])choose(jobs[0].id,jobs[0].viewer_url,jobs[0].gallery_url)}
$('jobs').addEventListener('click',ev=>{const card=ev.target.closest('.job');if(card)choose(card.dataset.id,card.dataset.viewer,card.dataset.gallery)});
async function loadCollections(){const r=await fetch('/api/collections'),j=await r.json();const items=(j.collections||[]).filter(x=>x.kind==='atlas').slice(0,14);$('collections').innerHTML=items.map(x=>'<a class="collection" href="'+esc(x.url)+'">'+(x.thumbnail?'<img src="'+esc(x.thumbnail)+'" alt="">':'<div class="empty">No preview</div>')+'<div><b>'+esc(x.name||x.path)+'</b><span>'+esc(x.count)+'/'+esc(x.total)+' · '+esc(x.updated_text||'')+'</span></div></a>').join('')||'<div class="empty">No atlas collections yet.</div>'}
async function loadRecent(){const r=await fetch('/api/recent-images?limit=96'),j=await r.json();const items=(j.images||[]).filter(x=>String(x.url||'').includes('/outputs/atlas/')).slice(0,48);$('recent').innerHTML=items.map(x=>'<a href="'+esc(x.url)+'" target="_blank" rel="noreferrer"><img src="'+esc(x.url)+'" alt=""><i>'+esc(x.name||'cell')+'</i></a>').join('')||'<div class="empty">No atlas cells yet.</div>'}
function refreshHorseMotion(){const b='/outputs/atlas/spheremap_atlas_horse_gallop_volga_motion_path_1024c_20260715.sphere/_motion/',q='?t='+Date.now();$('horseVideo').src=b+'preview.mp4'+q;$('horseSheet').src=b+'contact_sheet_latest.jpg'+q}
fetch('/api/jobs').then(r=>r.json()).then(j=>renderJobs(j.jobs||[])).catch(()=>{$('jobs').innerHTML='<div class="empty">Jobs unavailable.</div>'});
let recentRefreshAt=0;
if(window.EventSource){const es=new EventSource('/api/jobs/events');es.addEventListener('jobs',ev=>{const j=JSON.parse(ev.data);renderJobs(j.jobs||[]);const now=Date.now();if(now-recentRefreshAt>10000){recentRefreshAt=now;loadRecent().catch(()=>{});refreshHorseMotion()}});es.onerror=()=>{$('jobs').insertAdjacentHTML('beforeend','')}}
loadCollections().catch(()=>{$('collections').innerHTML='<div class="empty">Collections unavailable.</div>'});
loadRecent().catch(()=>{$('recent').innerHTML='<div class="empty">Recent cells unavailable.</div>'});
refreshHorseMotion();
</script>
</body>
</html>`
}

func indexHTML(cfg config.Config) string {
	return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>flux studio</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=Noto+Serif+JP:wght@300;400;500;700&display=swap" rel="stylesheet">
<style>
:root{color-scheme:dark;--surface-0:#060810;--surface-1:#0c0e1a;--surface-2:#121428;--surface-3:#1a1d36;--surface-4:#222644;--text:#ede6d8;--muted:#a8a0bf;--quiet:#5d5878;--gold:#ffd580;--sakura:#ffb7c5;--ocean:#64c8ff;--neon:#00ffc8;--wisteria:#b48eff;--ember:#ff6b3d;--accent:#d44535;--line:rgba(237,230,216,.11);--line-strong:rgba(237,230,216,.18);--glow-sakura:0 0 30px rgba(255,183,197,.20),0 0 8px rgba(255,183,197,.10);--glow-gold:0 0 30px rgba(255,213,128,.20),0 0 8px rgba(255,213,128,.10);--glow-ocean:0 0 30px rgba(100,200,255,.20),0 0 8px rgba(100,200,255,.10);--font-sans:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--font-serif:"Noto Serif JP",Georgia,serif;--font-mono:"JetBrains Mono","SF Mono",monospace}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--surface-0);color:var(--text);font:14px/1.45 var(--font-sans);letter-spacing:0;-webkit-font-smoothing:antialiased}body:before{content:"";position:fixed;inset:0;pointer-events:none;background:linear-gradient(90deg,rgba(212,69,53,.07),transparent 28%,rgba(100,200,255,.055) 72%,transparent),linear-gradient(180deg,rgba(255,213,128,.045),transparent 34%),radial-gradient(circle at 11% 8%,rgba(180,142,255,.13),transparent 32%),radial-gradient(circle at 88% 10%,rgba(100,200,255,.10),transparent 36%),linear-gradient(180deg,#060810 0%,#0c0e1a 46%,#111324 100%)}body:after{content:"";position:fixed;inset:0;pointer-events:none;opacity:.15;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='180' height='180'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='180' height='180' filter='url(%23n)' opacity='0.24'/%3E%3C/svg%3E")}
main{position:relative;z-index:1;max-width:1420px;margin:0 auto;padding:22px clamp(16px,3vw,36px) 48px}.top{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:end;min-height:150px;border-bottom:1px solid rgba(237,230,216,.08);padding-bottom:18px}.mark{display:inline-flex;align-items:center;gap:10px;color:var(--muted);font-size:14px}.mark:before{content:"";width:25px;height:25px;border:2px solid var(--accent);border-left-color:transparent;border-radius:50%;box-shadow:0 0 18px rgba(212,69,53,.36)}.title{max-width:900px;margin-top:14px;font-family:var(--font-serif);font-size:clamp(34px,4.8vw,66px);font-weight:400;line-height:.96}.sub{max-width:850px;color:var(--muted);margin-top:12px}.statusbar{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end}.pill{display:inline-flex;gap:8px;align-items:center;border:1px solid rgba(237,230,216,.12);border-radius:999px;padding:8px 12px;color:var(--muted);background:rgba(6,8,16,.62);backdrop-filter:blur(14px)}.dot{width:9px;height:9px;border-radius:50%;background:var(--accent)}.dot.on{background:var(--neon);box-shadow:0 0 16px rgba(0,255,200,.38)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 390px;gap:18px;margin-top:18px}.stack{display:grid;gap:18px}section{position:relative;min-width:0;overflow:hidden;border:1px solid var(--line);border-radius:8px;background:linear-gradient(180deg,rgba(255,255,255,.025),transparent 38%),rgba(12,14,26,.78);box-shadow:0 18px 64px rgba(0,0,0,.28);padding:18px;backdrop-filter:blur(18px)}section:before{content:"";position:absolute;inset:0 0 auto;height:1px;background:linear-gradient(90deg,transparent,rgba(255,213,128,.34),rgba(100,200,255,.22),transparent)}h2{font-size:12px;text-transform:uppercase;color:var(--sakura);margin:0 0 14px;letter-spacing:.18em}.ops-panel{margin-top:18px}.ops-head{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-bottom:12px}.ops-actions{display:flex;gap:9px;flex-wrap:wrap;justify-content:flex-end}
.collections{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:12px}.collection{position:relative;display:grid;grid-template-rows:156px auto;min-width:0;border:1px solid rgba(237,230,216,.11);border-radius:8px;overflow:hidden;background:rgba(12,14,26,.74);text-decoration:none;color:var(--text);box-shadow:0 12px 36px rgba(0,0,0,.22);transition:transform .18s ease,border-color .18s ease,box-shadow .18s ease}.collection:hover{transform:translateY(-3px);border-color:rgba(255,183,197,.34);box-shadow:0 24px 60px rgba(0,0,0,.36),var(--glow-sakura)}.collection:after{content:"";position:absolute;inset:0;pointer-events:none;background:linear-gradient(180deg,transparent 42%,rgba(6,8,16,.34))}.collection img{width:100%;height:100%;object-fit:cover;background:#060810;transition:transform .28s ease}.collection:hover img{transform:scale(1.035)}.collection .empty-thumb{display:grid;place-items:center;color:var(--quiet);background:radial-gradient(circle at 50% 40%,rgba(255,183,197,.08),transparent 45%),rgba(6,8,16,.72)}.collection div:last-child{position:relative;z-index:1;padding:12px}.collection b{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;color:var(--text);font-size:15px}.collection span{display:block;color:var(--muted);font-size:12px;margin-top:5px}
.jobs{display:grid;gap:7px}.job{border:1px solid rgba(237,230,216,.10);border-radius:8px;padding:8px 10px;background:rgba(6,8,16,.48);display:grid;grid-template-columns:minmax(170px,250px) minmax(0,1fr) 160px auto;align-items:center;gap:10px}.job-head{display:flex;align-items:center;gap:8px;min-width:0}.job b{color:var(--gold);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;text-shadow:var(--glow-gold);font-size:12px}.state{color:var(--muted);font-size:11px;border:1px solid rgba(255,213,128,.15);border-radius:999px;padding:4px 7px;background:rgba(255,213,128,.045);white-space:nowrap}.progress{height:6px;border-radius:999px;background:rgba(237,230,216,.08);overflow:hidden;border:1px solid rgba(237,230,216,.06)}.progress i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent),var(--gold),var(--ocean));box-shadow:var(--glow-gold);transition:width .25s ease}.job-prompt{color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-size:12px}.job-actions{display:flex;gap:7px;justify-content:flex-end}.job-actions button,.job-actions a{min-height:30px;padding:6px 9px;font-size:12px;white-space:nowrap}.compare{grid-column:1/-1;display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-top:2px}.compare figure{margin:0;min-width:0;border:1px solid rgba(237,230,216,.10);border-radius:7px;overflow:hidden;background:rgba(12,14,26,.68)}.compare img{display:block;width:100%;height:132px;object-fit:cover;background:#060810}.compare figcaption{padding:6px 8px;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.08em}.thumb{display:block;width:100%;max-height:240px;object-fit:cover;border:1px solid var(--line);border-radius:7px;background:#060810}
label{display:block;color:var(--muted);font-size:12px;letter-spacing:.08em;text-transform:uppercase;margin:11px 0 6px}textarea,input,select{width:100%;border:1px solid rgba(237,230,216,.13);border-radius:7px;background:rgba(6,8,16,.82);color:var(--text);padding:11px 12px;font:inherit;outline:none}textarea:focus,input:focus,select:focus{border-color:rgba(255,213,128,.48);box-shadow:0 0 0 3px rgba(255,213,128,.08)}textarea{min-height:128px;resize:vertical}.row{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.two{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.actions{display:flex;gap:9px;flex-wrap:wrap;margin-top:14px}
.dropzone{display:grid;grid-template-columns:74px minmax(0,1fr) auto;align-items:center;gap:12px;min-height:82px;border:1px dashed rgba(255,183,197,.28);border-radius:8px;background:rgba(6,8,16,.54);padding:9px;margin-top:8px;transition:border-color .16s ease,background .16s ease,box-shadow .16s ease}.dropzone.drag{border-color:rgba(100,200,255,.62);background:rgba(100,200,255,.07);box-shadow:var(--glow-ocean)}.drop-preview{width:64px;height:64px;border-radius:7px;border:1px solid rgba(237,230,216,.13);background:#060810;object-fit:cover}.drop-main{min-width:0}.drop-main b{display:block;color:var(--text);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.drop-main span{display:block;color:var(--muted);font-size:12px;margin-top:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.dropzone button{min-height:34px;padding:7px 10px}
.lane-tabs{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:10px}.lane-tabs button{min-height:34px;padding:7px 8px;font-size:12px}.lane-tabs button.active{border-color:rgba(100,200,255,.48);box-shadow:var(--glow-ocean);color:var(--ocean)}.i2i-modes{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin-top:10px}.i2i-modes button{min-height:34px;padding:7px 8px;font-size:12px}.i2i-modes button.active{border-color:rgba(255,213,128,.48);box-shadow:var(--glow-gold);color:var(--gold)}.i2i-note{margin-top:8px;color:var(--muted);font-size:12px}.prompt-tools{display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:7px;align-items:end}.prompt-tools button{min-height:38px;padding:8px 10px}.image-shelf{display:grid;grid-template-columns:repeat(auto-fill,minmax(62px,1fr));gap:7px;max-height:238px;overflow:auto;border:1px solid rgba(237,230,216,.10);border-radius:8px;background:rgba(6,8,16,.36);padding:8px;margin-top:8px}.image-tile{appearance:none;border:1px solid rgba(237,230,216,.10);border-radius:7px;overflow:hidden;background:#060810;aspect-ratio:1;padding:0;cursor:grab;position:relative}.image-tile:active{cursor:grabbing}.image-tile:hover{border-color:rgba(255,183,197,.38);box-shadow:var(--glow-sakura)}.image-tile img{display:block;width:100%;height:100%;object-fit:cover}.image-tile span{position:absolute;left:4px;right:4px;bottom:4px;border-radius:4px;background:rgba(6,8,16,.74);color:var(--muted);font-size:9px;line-height:1;padding:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.render-head{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px;border:1px solid rgba(255,213,128,.13);border-radius:8px;background:rgba(6,8,16,.42);padding:12px;margin-bottom:4px}.render-head b{display:block;color:var(--gold);font-size:16px;text-shadow:var(--glow-gold)}.render-head span{display:block;color:var(--muted);font-size:12px;margin-top:3px}.render-head>span:last-child{margin-top:0;border:1px solid rgba(255,183,197,.20);border-radius:999px;color:var(--sakura);padding:6px 9px;background:rgba(255,183,197,.045);white-space:nowrap}.render-actions button{flex:1 1 auto}
button,a.button{min-height:40px;border:1px solid rgba(255,213,128,.20);border-radius:7px;background:rgba(6,8,16,.64);color:var(--text);padding:10px 13px;font-weight:750;cursor:pointer;text-decoration:none;transition:border-color .16s ease,background .16s ease,box-shadow .16s ease,transform .16s ease}button:hover,a.button:hover{border-color:rgba(255,183,197,.34);background:rgba(18,22,41,.86);transform:translateY(-1px)}button.primary{border-color:transparent;background:linear-gradient(135deg,var(--accent),var(--ember));box-shadow:0 0 24px rgba(212,69,53,.14)}button.warn{color:var(--gold)}.message{border:1px solid rgba(255,213,128,.13);border-radius:8px;background:rgba(6,8,16,.50);padding:13px;color:var(--muted);min-height:78px}.message b{color:var(--gold);text-shadow:var(--glow-gold)}
@media(max-width:980px){main{padding:18px 14px 38px}.top,.layout{grid-template-columns:1fr}.top{min-height:auto}.statusbar{justify-content:flex-start}.row,.two{grid-template-columns:1fr}.collections{grid-template-columns:repeat(auto-fill,minmax(160px,1fr))}.job{grid-template-columns:1fr}.job-actions{justify-content:flex-start}.title{font-size:38px}}
</style>
</head>
<body>
<main>
<div class="top">
<div><div class="mark">anime.productions · flux studio</div><div class="title">Sakura render atelier</div><div class="sub">Socket-backed FLUX workspace for anime.sakure.network. Model: ` + html.EscapeString(cfg.ModelDir) + `</div></div>
<div class="statusbar"><div class="pill"><span id="dot" class="dot"></span><span id="status">checking</span></div><div class="pill" id="outputDir">outputs</div></div>
</div>
<section class="ops-panel">
<div class="ops-head"><h2>jobs</h2><div class="ops-actions"><button onclick="refresh()">Refresh</button><button class="warn" onclick="pauseBatch()">Pause batch</button><button onclick="resumeBatch()">Resume batch</button><button onclick="stopWorker()">Stop worker</button></div></div>
<div id="jobs" class="jobs"><span class="sub">Connecting to job stream.</span></div>
</section>
<div class="layout">
<div class="stack">
<section>
<h2>collections</h2>
<div id="collections" class="collections"><div class="sub">Loading collections</div></div>
</section>
</div>
<div class="stack">
<section>
<h2>render</h2>
<div class="render-head"><div><b>Image lane</b><span>Choose model, engine, and run count before queueing.</span></div><span id="laneHint">dev · socket</span></div>
<label>prompt</label><textarea id="prompt">tasteful anime editorial portrait, moonlit room, elegant cinematic styling, refined character design</textarea>
<div class="two">
<div><label>model</label><select id="model" onchange="modelChanged()"><option value="dev">FLUX.1 dev</option><option value="schnell">FLUX.1 schnell</option></select></div>
<div><label>engine</label><select id="backend"><option value="auto">socket auto</option><option value="mps">socket MPS</option><option value="mlx">mflux / MLX</option></select></div>
</div>
<div class="row">
<div><label>preset</label><select id="preset"><option value="">none</option><option>sketch</option><option>hero</option><option>object</option><option>space</option><option>cover</option><option>future</option><option>anime</option><option>noir</option></select></div>
<div><label>ratio</label><select id="ratio"><option>square</option><option>wide</option><option>portrait</option><option>fourthree</option><option>draft</option><option>poster</option><option>banner</option></select></div>
<div><label>iterations</label><input id="iterations" type="number" min="1" max="50" value="1"></div>
</div>
<div class="two">
<div><label>resolution</label><select id="resolution"><option value="384x384">384 square</option><option value="512x512">512 square</option><option value="384x512">384 portrait</option><option value="512x768">512 portrait</option><option value="512x384">512 landscape</option><option value="768x512">768 landscape</option><option value="1024x1024">1024 square</option></select></div>
<div><label>seed</label><input id="seed" placeholder="random"></div>
</div>
<div class="two">
<div><label>steps</label><input id="steps" type="number" min="1" max="120" value="28"></div>
<div><label>guidance</label><input id="guidance" type="number" step="0.1" value="3.5"></div>
</div>
<div class="actions render-actions"><button class="primary" onclick="submitRender(false)">Queue run set</button><button onclick="submitRender(true)">Plan only</button><button onclick="warm(false)">Warm queue</button><button class="warn" onclick="warm(true)">Preload model</button></div>
</section>
<section>
<h2>latent atlas</h2>
<div class="render-head"><div><b>Sphere atlas workspace</b><span>Open the dedicated atlas console for cell grids, latent motion parameters, dry runs, and live viewer links.</span></div><span>separate surface</span></div>
<div class="actions render-actions"><a class="button primary" href="atlas-studio">Open atlas workspace</a></div>
</section>
<section>
<h2>image to image</h2>
<div class="render-head"><div><b>Face placement blend</b><span>Use the top two lanes: identity face is blended onto the posture image, then sent as one source.</span></div><span>img2img · blend</span></div>
<label>identity face</label><input id="i2iIdentity" placeholder="/outputs/... face crop or local file path">
<div id="i2iIdentityDrop" class="dropzone"><img id="i2iIdentityPreview" class="drop-preview" alt=""><div class="drop-main"><b id="i2iIdentityTitle">Drop identity face here</b><span id="i2iIdentityMeta">Best as a clean face crop. It will be placed into the posture image.</span></div><button id="i2iIdentityBrowse" type="button">Browse</button><input id="i2iIdentityFile" type="file" accept="image/png,image/jpeg,image/webp" hidden></div>
<label>posture / composition</label><input id="i2iPosture" placeholder="/outputs/... pose reference or local file path">
<div id="i2iPostureDrop" class="dropzone"><img id="i2iPosturePreview" class="drop-preview" alt=""><div class="drop-main"><b id="i2iPostureTitle">Drop posture reference here</b><span id="i2iPostureMeta">This controls framing and default output dimensions.</span></div><button id="i2iPostureBrowse" type="button">Browse</button><input id="i2iPostureFile" type="file" accept="image/png,image/jpeg,image/webp" hidden></div>
<label>backdrop / style</label><input id="i2iBackdrop" placeholder="/outputs/... backdrop reference or local file path">
<div id="i2iBackdropDrop" class="dropzone"><img id="i2iBackdropPreview" class="drop-preview" alt=""><div class="drop-main"><b id="i2iBackdropTitle">Drop backdrop reference here</b><span id="i2iBackdropMeta">Optional environment, lighting, palette, or style reference.</span></div><button id="i2iBackdropBrowse" type="button">Browse</button><input id="i2iBackdropFile" type="file" accept="image/png,image/jpeg,image/webp" hidden></div>
<div class="lane-tabs"><button id="laneIdentity" type="button" onclick="setActiveI2ISlot('identity')">Identity</button><button id="lanePosture" type="button" onclick="setActiveI2ISlot('posture')">Posture</button><button id="laneBackdrop" type="button" onclick="setActiveI2ISlot('backdrop')">Backdrop</button></div>
<label>weighted blend</label>
<div class="two">
<div><label>mode</label><select id="blendMode"><option value="normal">normal</option><option value="soft-light" selected>soft light</option><option value="overlay">overlay</option><option value="screen">screen</option><option value="multiply">multiply</option><option value="luminosity">luminosity</option></select></div>
<div><label>output</label><select id="blendResolution"><option value="input" selected>base size</option><option value="384x384">384 square</option><option value="512x512">512 square</option><option value="384x512">384 portrait</option><option value="512x768">512 portrait</option><option value="512x384">512 landscape</option><option value="768x512">768 landscape</option></select></div>
</div>
<div class="row">
<div><label>identity weight</label><input id="blendIdentityWeight" type="number" min="0" max="1" step="0.05" value="0.35"></div>
<div><label>identity part</label><select id="blendIdentityPart"><option value="face" selected>face</option><option value="subject">subject</option><option value="center">center</option><option value="full">full</option><option value="top">top</option><option value="bottom">bottom</option><option value="left">left</option><option value="right">right</option><option value="edges">edges</option></select></div>
<div><label>backdrop weight</label><input id="blendBackdropWeight" type="number" min="0" max="1" step="0.05" value="0.45"></div>
</div>
<div class="two">
<div><label>backdrop part</label><select id="blendBackdropPart"><option value="edges" selected>edges</option><option value="full">full</option><option value="center">center</option><option value="top">top</option><option value="bottom">bottom</option><option value="left">left</option><option value="right">right</option><option value="subject">subject</option></select></div>
<div><label>base part</label><select id="blendPosturePart"><option value="full" selected>full</option><option value="subject">subject</option><option value="center">center</option><option value="top">top</option><option value="bottom">bottom</option><option value="left">left</option><option value="right">right</option><option value="edges">edges</option></select></div>
</div>
<div class="actions render-actions"><button type="button" onclick="createLaneBlend()">Create weighted blend</button></div>
<label>reusable image shelf</label><div id="imageShelf" class="image-shelf"><span class="sub">Loading recent images</span></div>
<label>prompt</label><textarea id="i2iPrompt">Harmonize the blended source into one cohesive fully clothed fantasy princess portrait. Preserve the woman's facial likeness from the placed face area while keeping the posture, body framing, and composition from the source. Royal couture gown, delicate crown, refined jewelry, graceful palace atmosphere, high-end animated film style.</textarea>
<div class="prompt-tools"><div><label>saved prompts</label><select id="i2iPromptHistory" onchange="loadI2IPromptFromHistory()"><option value="">Current prompt</option></select></div><button type="button" onclick="saveI2IPrompt()">Save</button><button type="button" onclick="clearI2IPromptHistory()">Clear</button></div>
<div class="two">
<div><label>resolution</label><select id="i2iResolution"><option value="input" selected>source size</option><option value="384x384">384 square</option><option value="512x512">512 square</option><option value="384x512">384 portrait</option><option value="512x768">512 portrait</option><option value="512x384">512 landscape</option><option value="768x512">768 landscape</option></select></div>
<div><label>backend</label><select id="i2iBackend"><option value="auto">img2img auto</option><option value="mps">img2img MPS</option><option value="cpu">CPU</option></select></div>
</div>
<div class="i2i-modes"><button id="i2iModeFaithful" type="button" onclick="setI2IMode('faithful')">Faithful</button><button id="i2iModeBalanced" type="button" onclick="setI2IMode('balanced')">Balanced</button><button id="i2iModeTransform" type="button" onclick="setI2IMode('transform')">Transform</button><button id="i2iModeProfound" type="button" onclick="setI2IMode('profound')">Profound</button></div>
<div class="row">
<div><label>strength</label><input id="i2iStrength" type="number" min="0.01" max="0.99" step="0.01" value="0.55"></div>
<div><label>steps</label><input id="i2iSteps" type="number" min="1" max="120" value="28"></div>
<div><label>guidance</label><input id="i2iGuidance" type="number" step="0.1" value="5.0"></div>
</div>
<div id="i2iDenoiseNote" class="i2i-note">Prompt influence: balanced</div>
<div class="two">
<div><label>seed</label><input id="i2iSeed" placeholder="random"></div>
<div><label>filename</label><input id="i2iFilename" placeholder="optional output filename"></div>
</div>
<div class="actions render-actions"><button class="primary" onclick="submitImg2Img(false)">Queue refinement</button><button onclick="submitImg2Img(true)">Plan only</button><button onclick="warmImg2Img(false)">Warm img2img</button><button class="warn" onclick="showImg2ImgJobs()">Img2img jobs</button></div>
</section>
<section>
<h2>activity</h2>
<div id="out" class="message">Ready.</div>
<div class="actions"><button onclick="refresh()">Refresh</button><button onclick="stopWorker()">Stop worker</button></div>
</section>
</div>
</div>
</main>
<script>
const $=id=>document.getElementById(id);
function numberOrZero(id){const v=Number($(id).value);return Number.isFinite(v)?v:0}
let activeI2ISlot='identity';
const i2iPromptKey='flux.studio.i2i.prompts.v1';
const i2iLastPromptKey='flux.studio.i2i.lastPrompt.v1';
function selectedResolution(){const parts=String($('resolution').value||'384x384').split('x').map(Number);return {width:parts[0]||384,height:parts[1]||384}}
function body(dryRun){const size=selectedResolution();return {prompt:$('prompt').value,model:$('model').value,backend:$('backend').value,preset:$('preset').value,ratio:$('ratio').value,width:size.width,height:size.height,seed:$('seed').value,steps:numberOrZero('steps'),guidance:numberOrZero('guidance'),iterations:Math.max(1,Math.min(50,numberOrZero('iterations')||1)),dry_run:dryRun}}
function selectedI2IResolution(){const value=String($('i2iResolution').value||'input');if(value==='input')return {width:0,height:0};const parts=value.split('x').map(Number);return {width:parts[0]||384,height:parts[1]||384}}
function img2imgBody(dryRun){const size=selectedI2IResolution();return {identity_image:$('i2iIdentity').value,posture_image:$('i2iPosture').value,backdrop_image:$('i2iBackdrop').value,prompt:$('i2iPrompt').value,backend:$('i2iBackend').value,width:size.width,height:size.height,strength:numberOrZero('i2iStrength')||0.55,steps:numberOrZero('i2iSteps')||28,guidance:numberOrZero('i2iGuidance')||5,seed:$('i2iSeed').value,filename:$('i2iFilename').value,dry_run:dryRun}}
function setI2IMode(mode){const values={faithful:{strength:.32,steps:24,guidance:3.5},balanced:{strength:.55,steps:28,guidance:5},transform:{strength:.72,steps:32,guidance:6.5},profound:{strength:.86,steps:36,guidance:7.5}}[mode]||{strength:.55,steps:28,guidance:5};$('i2iStrength').value=values.strength;$('i2iSteps').value=values.steps;$('i2iGuidance').value=values.guidance;updateI2INote();['Faithful','Balanced','Transform','Profound'].forEach(name=>{const b=$('i2iMode'+name);if(b)b.classList.toggle('active',name.toLowerCase()===mode)})}
function updateI2INote(){const strength=numberOrZero('i2iStrength')||0,steps=numberOrZero('i2iSteps')||0,effective=Math.max(1,Math.ceil(strength*steps));let tone='faithful';if(strength>=.82)tone='profound';else if(strength>=.65)tone='transformative';else if(strength>=.45)tone='balanced';$('i2iDenoiseNote').textContent='Prompt influence: '+tone+' · about '+effective+' denoise steps from '+steps+' scheduled'}
function i2iSlot(slot){const slots={identity:{input:'i2iIdentity',preview:'i2iIdentityPreview',title:'i2iIdentityTitle',meta:'i2iIdentityMeta',drop:'i2iIdentityDrop',browse:'i2iIdentityBrowse',file:'i2iIdentityFile',selected:'Identity face selected'},posture:{input:'i2iPosture',preview:'i2iPosturePreview',title:'i2iPostureTitle',meta:'i2iPostureMeta',drop:'i2iPostureDrop',browse:'i2iPostureBrowse',file:'i2iPostureFile',selected:'Posture reference selected'},backdrop:{input:'i2iBackdrop',preview:'i2iBackdropPreview',title:'i2iBackdropTitle',meta:'i2iBackdropMeta',drop:'i2iBackdropDrop',browse:'i2iBackdropBrowse',file:'i2iBackdropFile',selected:'Backdrop reference selected'}};return slots[slot]||slots.identity}
function setActiveI2ISlot(slot){activeI2ISlot=slot;['identity','posture','backdrop'].forEach(x=>{const b=$('lane'+x.charAt(0).toUpperCase()+x.slice(1));if(b)b.classList.toggle('active',x===slot)})}
function setI2IImageSlot(slot,value,label,preview){const s=i2iSlot(slot);$(s.input).value=value||'';$(s.title).textContent=label||s.selected;$(s.meta).textContent=value||'Ready';if(preview){$(s.preview).src=preview}else if(String(value||'').startsWith('/outputs/')){$(s.preview).src=value}else{$(s.preview).removeAttribute('src')}}
function clearI2ISlot(slot){const s=i2iSlot(slot);$(s.input).value='';$(s.preview).removeAttribute('src');$(s.title).textContent=slot==='identity'?'Drop identity face here':slot==='posture'?'Drop posture reference here':'Drop backdrop reference here';$(s.meta).textContent=slot==='identity'?'Best as a clean face crop. It will be placed into the posture image.':slot==='posture'?'This controls framing and default output dimensions.':'Optional environment, lighting, palette, or style reference.'}
function selectedBlendResolution(){const value=String($('blendResolution').value||'input');if(value==='input')return {width:0,height:0};const parts=value.split('x').map(Number);return {width:parts[0]||384,height:parts[1]||384}}
async function createLaneBlend(){const posture=$('i2iPosture').value,identity=$('i2iIdentity').value,backdrop=$('i2iBackdrop').value;const images=[];if(posture)images.push({image:posture,label:'posture base',weight:1,part:$('blendPosturePart').value});if(!posture&&identity)images.push({image:identity,label:'identity base',weight:1,part:'full'});if(identity&&posture)images.push({image:identity,label:'identity',weight:numberOrZero('blendIdentityWeight')||0.35,part:$('blendIdentityPart').value});if(backdrop)images.push({image:backdrop,label:'backdrop',weight:numberOrZero('blendBackdropWeight')||0.45,part:$('blendBackdropPart').value});if(images.length<2){say('<b>Blend needs at least two lanes</b><br>Add a posture/source plus identity or backdrop.');return}const size=selectedBlendResolution();const j=await api('/api/blend',{method:'POST',body:JSON.stringify({images,mode:$('blendMode').value,width:size.width,height:size.height})});if(j.ok&&j.path){clearI2ISlot('identity');clearI2ISlot('backdrop');setI2IImageSlot('posture',j.path,'Weighted blend',j.url);setActiveI2ISlot('posture');say('<b>Blend ready</b><br>'+esc(j.mode||'blend')+' · '+esc(j.output_width)+'x'+esc(j.output_height)+'<br>'+esc(j.path));await refresh()}}
async function uploadI2IFile(file,slot){if(!file)return;const s=i2iSlot(slot);const form=new FormData();form.append('image',file,file.name||'image.png');$(s.title).textContent='Uploading image';$(s.meta).textContent=file.name||'image';const localPreview=URL.createObjectURL(file);$(s.preview).src=localPreview;const r=await fetch('/api/upload',{method:'POST',body:form});const j=await r.json();if(!j.ok){$(s.title).textContent='Upload failed';$(s.meta).textContent=j.error||'Could not stage image';say('<b>Upload failed</b><br>'+esc(j.error||'Could not stage image'));return}setI2IImageSlot(slot,j.path,j.name||file.name,localPreview);say('<b>Image staged</b><br>'+esc(j.path))}
function setupOneI2IDrop(slot){const s=i2iSlot(slot), dz=$(s.drop), fileInput=$(s.file);if(!dz||!fileInput)return;dz.addEventListener('click',()=>setActiveI2ISlot(slot));$(s.input).addEventListener('focus',()=>setActiveI2ISlot(slot));$(s.browse).onclick=()=>{setActiveI2ISlot(slot);fileInput.click()};fileInput.onchange=()=>uploadI2IFile(fileInput.files&&fileInput.files[0],slot);['dragenter','dragover'].forEach(type=>dz.addEventListener(type,ev=>{ev.preventDefault();setActiveI2ISlot(slot);dz.classList.add('drag')}));['dragleave','drop'].forEach(type=>dz.addEventListener(type,ev=>{ev.preventDefault();if(type==='dragleave')dz.classList.remove('drag')}));dz.addEventListener('drop',ev=>{dz.classList.remove('drag');const file=ev.dataTransfer&&ev.dataTransfer.files&&ev.dataTransfer.files[0];if(file){uploadI2IFile(file,slot);return}const custom=ev.dataTransfer&&ev.dataTransfer.getData('application/x-flux-image-path');const uri=custom||(ev.dataTransfer&&((ev.dataTransfer.getData('text/uri-list')||'').split('\n')[0]||ev.dataTransfer.getData('text/plain')))||'';if(uri){try{const u=new URL(uri,location.href);setI2IImageSlot(slot,u.pathname.startsWith('/outputs/')?u.pathname:uri,u.pathname.split('/').pop()||'Dropped image',u.href)}catch(_){setI2IImageSlot(slot,uri,'Dropped image','')}}})}
function setupI2IDrop(){['identity','posture','backdrop'].forEach(setupOneI2IDrop);setActiveI2ISlot(activeI2ISlot);setupPromptCache();['i2iStrength','i2iSteps','i2iGuidance'].forEach(id=>$(id).addEventListener('input',updateI2INote));setI2IMode('balanced');document.addEventListener('dragover',ev=>{if(ev.dataTransfer&&ev.dataTransfer.types&&Array.from(ev.dataTransfer.types).includes('Files'))ev.preventDefault()});document.addEventListener('drop',ev=>{const zones=['i2iIdentityDrop','i2iPostureDrop','i2iBackdropDrop'].map($);if(zones.some(z=>z&&z.contains(ev.target)))return;if(ev.dataTransfer&&ev.dataTransfer.types&&Array.from(ev.dataTransfer.types).includes('Files'))ev.preventDefault()})}
function promptList(){try{return JSON.parse(localStorage.getItem(i2iPromptKey)||'[]')}catch(_){return []}}
function writePromptList(list){localStorage.setItem(i2iPromptKey,JSON.stringify(list.slice(0,30)))}
function renderPromptHistory(){const sel=$('i2iPromptHistory');if(!sel)return;const list=promptList();sel.innerHTML='<option value="">Current prompt</option>'+list.map((p,i)=>'<option value="'+i+'">'+esc(p.slice(0,90))+'</option>').join('')}
function saveI2IPrompt(){const p=String($('i2iPrompt').value||'').trim();if(!p)return;let list=promptList().filter(x=>x!==p);list.unshift(p);writePromptList(list);localStorage.setItem(i2iLastPromptKey,p);renderPromptHistory();say('<b>Prompt saved</b>')}
function loadI2IPromptFromHistory(){const idx=Number($('i2iPromptHistory').value);const list=promptList();if(Number.isFinite(idx)&&list[idx]){$('i2iPrompt').value=list[idx];localStorage.setItem(i2iLastPromptKey,list[idx])}}
function clearI2IPromptHistory(){localStorage.removeItem(i2iPromptKey);renderPromptHistory();say('<b>Prompt history cleared</b>')}
function setupPromptCache(){const last=localStorage.getItem(i2iLastPromptKey);if(last&&!$('i2iPrompt').dataset.loaded){$('i2iPrompt').value=last;$('i2iPrompt').dataset.loaded='1'}$('i2iPrompt').addEventListener('input',()=>localStorage.setItem(i2iLastPromptKey,$('i2iPrompt').value));renderPromptHistory()}
function renderImageShelf(images){const shelf=$('imageShelf');if(!shelf)return;if(!images||!images.length){shelf.innerHTML='<span class="sub">No recent images yet</span>';return}shelf.innerHTML=images.map((img,i)=>'<button class="image-tile" draggable="true" data-i="'+i+'" title="'+escAttr(img.name||img.path||'image')+'"><img src="'+escAttr(img.url)+'" alt=""><span>'+esc(img.kind||'image')+'</span></button>').join('');shelf.querySelectorAll('.image-tile').forEach(btn=>{const img=images[Number(btn.dataset.i)];btn.onclick=()=>setI2IImageSlot(activeI2ISlot,img.path,img.name,img.url);btn.ondragstart=ev=>{ev.dataTransfer.setData('application/x-flux-image-path',img.path);ev.dataTransfer.setData('text/plain',img.path);ev.dataTransfer.setData('text/uri-list',img.url)}})}
function say(html){$('out').innerHTML=html}
async function api(path,opts={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opts});const j=await r.json();if(!j.ok){say('<b>Request failed</b><br>'+esc(j.error||j.worker_error||'Unknown error'));return j}if(j.dry_run&&String(path).includes('/api/atlas/submit')){const p=j.plan||{};say('<b>Atlas plan ready</b><br>'+esc(p.cells||0)+' cells · '+esc(p.grid||'')+' · '+esc(p.backend||'mps')+' · '+esc(p.width)+'x'+esc(p.height)+' · '+esc(p.steps)+' steps · '+esc(p.mode||'')+'<br><a class="button" href="'+escAttr(j.viewer||'#')+'" target="_blank" rel="noreferrer">Live viewer</a> <a class="button" href="'+escAttr(j.gallery||'#')+'" target="_blank" rel="noreferrer">Gallery</a>');return j}if(j.dry_run){const p=j.plan||{},i2i=String(path).includes('/api/img2img'),effective=i2i?Math.max(1,Math.round(Number(p.strength||0)*Number(p.steps||0))):0;say('<b>Plan ready</b><br>'+esc(j.iterations||1)+' iteration(s) · '+esc(p.model||'dev')+' · '+esc(p.backend||'auto')+' · '+esc(p.width)+'x'+esc(p.height)+' · '+esc(p.steps)+' steps'+(i2i?' · '+esc(p.strength)+' strength · ~'+esc(effective)+' prompt-active steps':'')+'<br>'+esc(p.prompt||''));return j}if(j.viewer){if(j.job)renderJobs([j.job]);say('<b>Atlas queued</b><br>'+esc(j.job?.id||j.plan?.id||'atlas')+' · '+esc(j.plan?.cells||'')+' cells · '+esc(j.plan?.steps||'')+' steps<br><a class="button" href="'+escAttr(j.viewer)+'" target="_blank" rel="noreferrer">Live viewer</a> <a class="button" href="'+escAttr(j.gallery||j.viewer)+'" target="_blank" rel="noreferrer">Gallery</a>');return j}if(j.jobs){renderJobs(j.jobs);say('<b>Queued run set</b><br>'+esc(j.jobs.length)+' job(s) · '+esc(j.plan?.model||'dev')+' · '+esc(j.plan?.backend||'auto')+' · '+esc(j.plan?.width)+'x'+esc(j.plan?.height));return j}if(j.job){renderJobs([j.job]);say('<b>Queued</b><br>'+esc(j.job.id||'job')+' · '+esc(j.job.status||'queued'));return j}say('<b>'+esc(j.message||'Updated')+'</b>');return j}
async function requestJSON(path,opts={}){const r=await fetch(path,{headers:{'Content-Type':'application/json'},...opts});return await r.json()}
let lastJobsKey='';
let jobFeeds={flux:[],img2img:[]};
function setJobFeed(kind,jobs){jobFeeds[kind]=jobs||[];renderJobs([...jobFeeds.flux,...jobFeeds.img2img])}
async function health(){const j=await requestJSON('/api/health');$('dot').className='dot '+(j.worker_running?'on':'');$('status').textContent=j.worker_running?(j.loaded?'worker loaded':'worker online'):'worker down';$('outputDir').textContent=(j.output_dir||'outputs').split('/').slice(-2).join('/');return j}
async function refresh(){await health();const [j,i,c,imgs]=await Promise.all([requestJSON('/api/jobs'),requestJSON('/api/img2img/jobs'),requestJSON('/api/collections'),requestJSON('/api/recent-images?limit=80')]);jobFeeds.flux=j.jobs||[];jobFeeds.img2img=i.jobs||[];renderJobs([...jobFeeds.flux,...jobFeeds.img2img]);renderCollections(c.collections||[]);renderImageShelf(imgs.images||[])}
async function submitRender(dryRun){await api('/api/render'+(dryRun?'?dry_run=1':''),{method:'POST',body:JSON.stringify(body(dryRun))});await refresh()}
async function warm(preload){await api('/api/warm?preload='+(preload?'1':'0'),{method:'POST'});await refresh()}
async function stopWorker(){await api('/api/stop',{method:'POST'});await refresh()}
async function pauseBatch(){await api('/api/batch/pause',{method:'POST'});await refresh()}
async function resumeBatch(){await api('/api/batch/resume',{method:'POST'});await refresh()}
async function cancelJob(id,socketKind='flux'){await api(socketKind==='img2img'?'/api/img2img/cancel':'/api/job/cancel',{method:'POST',body:JSON.stringify({id})});await refresh()}
async function submitImg2Img(dryRun){saveI2IPrompt();await api('/api/img2img'+(dryRun?'?dry_run=1':''),{method:'POST',body:JSON.stringify(img2imgBody(dryRun))});await refresh()}
async function warmImg2Img(preload){await api('/api/img2img/warm?preload='+(preload?'1':'0'),{method:'POST'});await refresh()}
async function showImg2ImgJobs(){const j=await requestJSON('/api/img2img/jobs');setJobFeed('img2img',j.jobs||[]);say('<b>Img2img socket</b><br>'+esc((j.jobs||[]).length)+' visible job(s)'+(j.worker_error?'<br>'+esc(j.worker_error):''))}
function modelChanged(){const model=$('model').value;if(model==='schnell'){$('backend').value='mlx';$('steps').value='4';$('guidance').value='0';$('laneHint').textContent='schnell · mflux'}else{if($('backend').value==='mlx')$('backend').value='auto';$('steps').value='28';$('guidance').value='3.5';$('laneHint').textContent='dev · socket'}}
$('backend').onchange=()=>{$('laneHint').textContent=($('model').value==='schnell'?'schnell':'dev')+' · '+($('backend').value==='mlx'?'mflux':'socket')}
function renderJobs(jobs){const key=JSON.stringify(jobs.map(x=>({id:x.id,kind:x.kind,status:x.status,phase:x.phase,step:x.step,total_steps:x.total_steps,output_url:x.output_url,viewer_url:x.viewer_url,gallery_url:x.gallery_url,error:x.error,cancel_requested:x.cancel_requested,socket_kind:x.socket_kind,strength:x.strength,guidance:x.guidance,steps:x.steps})));if(key!==lastJobsKey){$('jobs').innerHTML=jobs.map(jobHTML).join('')||'<span class="sub">No active jobs. Collections remain available.</span>';lastJobsKey=key}}
function connectJobs(){if(!window.EventSource)return;const es=new EventSource('/api/jobs/events');es.addEventListener('jobs',ev=>{try{const j=JSON.parse(ev.data);setJobFeed('flux',j.jobs||[]);if(j.worker_running!==undefined){$('dot').className='dot '+(j.worker_running?'on':'');$('status').textContent=j.worker_running?'worker online':'worker down'}}catch(_){}});es.onerror=()=>{$('status').textContent='job stream reconnecting'};const i2i=new EventSource('/api/img2img/events');i2i.addEventListener('jobs',ev=>{try{const j=JSON.parse(ev.data);setJobFeed('img2img',j.jobs||[])}catch(_){}})}
function renderCollections(items){items=[...(items||[])].sort((a,b)=>(Number(a.priority??10)-Number(b.priority??10))||(Number(b.updated||0)-Number(a.updated||0)));$('collections').innerHTML=items.map(collectionHTML).join('')||'<span class="sub">No batch or atlas collections yet.</span>'}
function collectionHTML(x){const thumb=x.thumbnail?'<img src="'+escAttr(x.thumbnail)+'" alt="">':'<div class="empty-thumb">No preview</div>';const progress=Number(x.total||0)?Math.round((Number(x.count||0)/Number(x.total))*100)+'%':'ready';return '<a class="collection" href="'+escAttr(x.url)+'">'+thumb+'<div><b>'+esc(x.name||x.path)+'</b><span>'+esc(x.kind)+' · '+esc(x.count)+'/'+esc(x.total)+' · '+progress+'</span><span>'+esc(x.updated_text||'')+'</span></div></a>'}
function jobHTML(x){const total=Number(x.total_steps||0);const step=Number(x.step||0);const batchTotal=Number(x.batch_total||0);const batchNow=Number(x.batch_index||x.batch_submitted||x.batch_done||0);const imagePct=total?Math.max(0,Math.min(100,Math.round((step/total)*100))):0;const batchPct=batchTotal?Math.max(0,Math.min(100,Math.round((batchNow/batchTotal)*100))):0;const pct=batchTotal?batchPct:imagePct;const batchText=batchTotal?' · batch '+esc(batchNow)+'/'+esc(batchTotal)+' · '+pct+'%':'';const active=['queued','running','cancelling'].includes(String(x.status||'').toLowerCase());const socketKind=String(x.socket_kind||'flux');const isAtlas=String(x.kind||'')==='atlas_sphere';const imageText=total?' · '+(isAtlas?'cell ':'image ')+esc(step)+'/'+esc(total):'';const tune=socketKind==='img2img'?' · strength '+esc(x.strength||'')+' · guidance '+esc(x.guidance||''):(isAtlas?' · '+esc(x.steps||'')+' steps':'');let actions='<div class="job-actions">';if(active)actions+='<button class="warn" data-job="'+escAttr(x.id||'')+'" data-socket="'+escAttr(socketKind)+'" onclick="cancelJob(this.dataset.job,this.dataset.socket)">Cancel</button>';if(x.viewer_url)actions+='<a class="button" href="'+escAttr(x.viewer_url)+'" target="_blank" rel="noreferrer">Viewer</a>';if(x.gallery_url)actions+='<a class="button" href="'+escAttr(x.gallery_url)+'" target="_blank" rel="noreferrer">Gallery</a>';if(x.output_url&&!x.viewer_url)actions+='<a class="button" href="'+escAttr(x.output_url)+'" target="_blank" rel="noreferrer">Open</a>';actions+='</div>';const source=x.blend_image_url||x.posture_image_url||x.primary_image_url||x.image_url||x.identity_image_url||'';const compare=(socketKind==='img2img'&&source&&x.output_url)?'<div class="compare"><figure><img src="'+escAttr(source)+'" alt=""><figcaption>source</figcaption></figure><figure><img src="'+escAttr(x.output_url)+'" alt=""><figcaption>enhanced</figcaption></figure></div>':'';return '<div class="job"><div class="job-head"><b>'+esc(x.id||'job')+'</b><span class="state">'+esc(socketKind)+' · '+esc(x.status||'')+batchText+imageText+tune+'</span></div><div class="job-prompt">'+esc(x.conditioning||x.batch_name||x.phase||'')+' · '+esc(x.prompt||'')+'</div><div class="progress"><i style="width:'+pct+'%"></i></div>'+actions+compare+'</div>'}
function esc(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function escAttr(v){return esc(v)}
setupI2IDrop();refresh();connectJobs();
</script>
</body>
</html>`
}

func OpenBrowser(url string) {
	for _, opener := range []string{"open", "xdg-open"} {
		if _, err := exec.LookPath(opener); err != nil {
			continue
		}
		if err := exec.Command(opener, url).Start(); err == nil {
			return
		}
	}
}
