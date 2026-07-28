// Package fleet runs one FLUX worker per GPU and presents them as a single
// logical renderer.
//
// Each worker is an ordinary daemon.Client launched with CUDA_VISIBLE_DEVICES
// pinned to one ordinal, so a worker process sees exactly one GPU and keeps
// addressing it as cuda:0. That means worker.py needs no device-index plumbing:
// isolation happens at the process boundary, and a crashed or wedged worker
// takes down only its own GPU.
//
// Two kinds of parallelism sit on top of that:
//
//   - Independent jobs are dispatched to the idlest worker, so four different
//     renders occupy four GPUs.
//   - A single atlas sphere is sharded: every worker receives the same job id
//     and payload plus a distinct (shard_id, shard_total), renders an
//     interleaved slice of the cell order, and writes into the same output
//     directory. Cells are content-addressed by index and skipped when already
//     present, so shards never collide and a re-submit resumes.
package fleet

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"local/flux/internal/config"
	"local/flux/internal/daemon"
)

// WorkerPrefix names per-GPU workers: flux-gpu0, flux-gpu1, ...
const WorkerPrefix = "flux-gpu"

// Pool is a set of GPU-pinned workers addressed as one renderer.
type Pool struct {
	cfg     config.Config
	gpus    []int
	workers []daemon.Client
}

// WorkerStatus is one worker's health as reported by its ping, together with
// the jobs it owns.
type WorkerStatus struct {
	Name    string           `json:"name"`
	GPU     int              `json:"gpu"`
	Up      bool             `json:"up"`
	Loaded  bool             `json:"loaded"`
	Device  string           `json:"device,omitempty"`
	Backend string           `json:"backend,omitempty"`
	Jobs    int              `json:"jobs"`
	Active  int              `json:"active"`
	Error   string           `json:"error,omitempty"`
	JobList []map[string]any `json:"-"`
	Raw     map[string]any   `json:"-"`
}

// Snapshot is one consistent pass over the fleet: merged jobs plus aggregate
// health. Callers that need both (the dashboard and its SSE stream) should use
// this rather than calling Jobs and Status separately, which would double the
// round trips per poll.
type Snapshot struct {
	Jobs    []map[string]any
	Workers []WorkerStatus
	Up      int
	Loaded  bool
	Device  string
	Backend string
}

// DetectGPUs reports the GPU ordinals the fleet should span.
//
// FLUX_FLEET_GPUS ("0,2") pins an explicit set; FLUX_FLEET_SIZE caps the count.
// Otherwise the ordinals come from nvidia-smi. A machine with no usable GPU
// yields nil, and callers fall back to the single default worker.
func DetectGPUs() []int {
	if raw := strings.TrimSpace(os.Getenv("FLUX_FLEET_GPUS")); raw != "" {
		var gpus []int
		for _, field := range strings.Split(raw, ",") {
			field = strings.TrimSpace(field)
			if field == "" {
				continue
			}
			n, err := strconv.Atoi(field)
			if err != nil || n < 0 {
				continue
			}
			gpus = append(gpus, n)
		}
		return capGPUs(gpus)
	}
	out, err := exec.Command("nvidia-smi", "--query-gpu=index", "--format=csv,noheader").Output()
	if err != nil {
		return nil
	}
	var gpus []int
	for _, line := range strings.Split(string(out), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		n, err := strconv.Atoi(line)
		if err != nil {
			continue
		}
		gpus = append(gpus, n)
	}
	return capGPUs(gpus)
}

func capGPUs(gpus []int) []int {
	limit := strings.TrimSpace(os.Getenv("FLUX_FLEET_SIZE"))
	if limit == "" {
		return gpus
	}
	n, err := strconv.Atoi(limit)
	if err != nil || n <= 0 || n >= len(gpus) {
		return gpus
	}
	return gpus[:n]
}

// New builds a pool spanning every detected GPU. A pool over zero GPUs is valid
// and simply reports Enabled() == false.
func New(cfg config.Config) Pool {
	return NewForGPUs(cfg, DetectGPUs())
}

// NewForGPUs builds a pool over an explicit set of GPU ordinals.
func NewForGPUs(cfg config.Config, gpus []int) Pool {
	workers := make([]daemon.Client, 0, len(gpus))
	for _, gpu := range gpus {
		name := fmt.Sprintf("%s%d", WorkerPrefix, gpu)
		env := map[string]string{
			"CUDA_VISIBLE_DEVICES": strconv.Itoa(gpu),
			// Four processes sharing a host benefit from an expandable
			// allocator; long atlas runs otherwise fragment their pool.
			"PYTORCH_CUDA_ALLOC_CONF": envOr("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True"),
			"FLUX_WORKER_GPU":         strconv.Itoa(gpu),
		}
		workers = append(workers, daemon.NewWorker(cfg, name, "flux", env))
	}
	return Pool{cfg: cfg, gpus: gpus, workers: workers}
}

func envOr(key, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(key)); v != "" {
		return v
	}
	return fallback
}

// Size reports how many GPUs the pool spans.
func (p Pool) Size() int { return len(p.workers) }

// Enabled reports whether the pool has any worker to dispatch to.
func (p Pool) Enabled() bool { return len(p.workers) > 0 }

// GPUs reports the ordinals the pool spans.
func (p Pool) GPUs() []int { return p.gpus }

// Workers exposes the underlying clients, in GPU order.
func (p Pool) Workers() []daemon.Client { return p.workers }

// Start brings up every worker concurrently. Model load dominates startup, so
// serial starts would cost four times as long for no benefit. An error is
// returned only when no worker at all came up; partial fleets stay usable.
func (p Pool) Start(preload bool) error {
	if !p.Enabled() {
		return errors.New("fleet has no GPUs")
	}
	errs := make([]error, len(p.workers))
	var wg sync.WaitGroup
	for i, worker := range p.workers {
		wg.Add(1)
		go func(i int, worker daemon.Client) {
			defer wg.Done()
			errs[i] = worker.Start(preload)
		}(i, worker)
	}
	wg.Wait()

	var failed []string
	for i, err := range errs {
		if err != nil {
			failed = append(failed, fmt.Sprintf("%s: %v", p.workers[i].Name(), err))
		}
	}
	if len(failed) == len(p.workers) {
		return fmt.Errorf("no fleet worker started (%s)", strings.Join(failed, "; "))
	}
	return nil
}

// Stop asks every worker to exit, returning any failures.
func (p Pool) Stop() []error {
	var errs []error
	for _, worker := range p.workers {
		if err := worker.Stop(); err != nil {
			errs = append(errs, fmt.Errorf("%s: %w", worker.Name(), err))
		}
	}
	return errs
}

// Status pings every worker concurrently so one hung worker cannot stall the
// report for the rest.
func (p Pool) Status() []WorkerStatus {
	out := make([]WorkerStatus, len(p.workers))
	var wg sync.WaitGroup
	for i, worker := range p.workers {
		wg.Add(1)
		go func(i int, worker daemon.Client) {
			defer wg.Done()
			status := WorkerStatus{Name: worker.Name(), GPU: p.gpus[i]}
			resp, err := worker.Request(map[string]any{"op": "ping"})
			if err != nil {
				status.Error = err.Error()
				out[i] = status
				return
			}
			status.Up = true
			status.Loaded = resp.Loaded
			status.Device = resp.Device
			status.Backend = resp.Backend
			status.Raw = resp.Raw
			if jobs, err := worker.Request(map[string]any{"op": "jobs"}); err == nil {
				for _, job := range jobs.Jobs {
					job["worker"] = worker.Name()
					job["gpu"] = p.gpus[i]
				}
				status.JobList = jobs.Jobs
				status.Jobs = len(jobs.Jobs)
				status.Active = countActive(jobs.Jobs)
			}
			out[i] = status
		}(i, worker)
	}
	wg.Wait()
	return out
}

func countActive(jobs []map[string]any) int {
	active := 0
	for _, job := range jobs {
		switch stringValue(job["status"]) {
		case "running", "queued":
			active++
		}
	}
	return active
}

// Snapshot takes one pass over the fleet, returning merged jobs alongside
// aggregate health. A fleet counts as loaded once any worker holds the model,
// since that worker can already render.
func (p Pool) Snapshot() Snapshot {
	statuses := p.Status()
	snap := Snapshot{Workers: statuses}
	var all []map[string]any
	for _, status := range statuses {
		all = append(all, status.JobList...)
		if !status.Up {
			continue
		}
		snap.Up++
		if status.Loaded {
			snap.Loaded = true
		}
		if snap.Device == "" {
			snap.Device = status.Device
		}
		if snap.Backend == "" {
			snap.Backend = status.Backend
		}
	}
	snap.Jobs = MergeShardJobs(all)
	return snap
}

// Jobs returns every worker's jobs, tagged with their owning worker and GPU.
// Atlas shards that share a job id are merged into one logical job.
func (p Pool) Jobs() []map[string]any {
	return p.Snapshot().Jobs
}

// Cancel asks every worker to cancel the id, since an atlas job is sharded
// across all of them. It reports how many workers accepted the cancel.
func (p Pool) Cancel(id string) (int, error) {
	if strings.TrimSpace(id) == "" {
		return 0, errors.New("cancel requires a job id")
	}
	var (
		mu       sync.Mutex
		accepted int
		lastErr  error
		wg       sync.WaitGroup
	)
	for _, worker := range p.workers {
		wg.Add(1)
		go func(worker daemon.Client) {
			defer wg.Done()
			_, err := worker.Request(map[string]any{"op": "cancel", "id": id})
			mu.Lock()
			defer mu.Unlock()
			if err != nil {
				lastErr = err
				return
			}
			accepted++
		}(worker)
	}
	wg.Wait()
	if accepted == 0 {
		if lastErr != nil {
			return 0, lastErr
		}
		return 0, fmt.Errorf("no fleet worker knows job %s", id)
	}
	return accepted, nil
}

// Idlest picks the worker with the fewest running or queued jobs, starting the
// fleet if nothing is up yet. Ties break toward the lowest GPU ordinal, which
// keeps single-job workloads on a predictable device.
func (p Pool) Idlest() (daemon.Client, error) {
	if !p.Enabled() {
		return daemon.Client{}, errors.New("fleet has no GPUs")
	}
	statuses := p.Status()
	best := -1
	bestActive := 0
	for i, status := range statuses {
		if !status.Up {
			continue
		}
		if best < 0 || status.Active < bestActive {
			best = i
			bestActive = status.Active
		}
	}
	if best >= 0 {
		return p.workers[best], nil
	}
	if err := p.Start(false); err != nil {
		return daemon.Client{}, err
	}
	for i, status := range p.Status() {
		if status.Up {
			return p.workers[i], nil
		}
	}
	return daemon.Client{}, errors.New("no fleet worker is reachable")
}

// Dispatch sends a request to the idlest worker.
func (p Pool) Dispatch(req map[string]any) (daemon.Response, string, error) {
	worker, err := p.Idlest()
	if err != nil {
		return daemon.Response{}, "", err
	}
	resp, err := worker.Request(req)
	return resp, worker.Name(), err
}

// SubmitAtlas shards one atlas sphere across every worker.
//
// All shards share a job id and output directory and differ only in
// (shard_id, shard_total); each renders every shard_total-th cell of the
// traversal order. Interleaving rather than splitting into contiguous blocks
// matters for two reasons: the watch UI streams cells as they land, so an
// interleaved order fills the sphere evenly instead of one quadrant at a time,
// and it self-balances when cell cost varies across the traversal.
func (p Pool) SubmitAtlas(payload map[string]any) (map[string]any, error) {
	if !p.Enabled() {
		return nil, errors.New("fleet has no GPUs")
	}
	id := stringValue(payload["id"])
	if strings.TrimSpace(id) == "" {
		id = "spheremap_fleet_" + time.Now().Format("20060102-150405")
	}

	// If this sphere is already in flight, hand back what is running instead of
	// laying a second sharding over the top. shard_total is fixed at submit
	// time, so re-submitting while the fleet size differs would leave two
	// overlapping partitions of the same cells racing each other: the output
	// stays correct because cells are skipped once present, but the extra
	// shards burn GPU time re-deriving work the first set already covers.
	// A sphere whose shards have all finished or failed is resubmitted
	// normally, which is what makes a partial render resumable.
	if existing := p.activeAtlasJob(id); existing != nil {
		existing["already"] = true
		return existing, nil
	}

	shardTotal := len(p.workers)

	type shardResult struct {
		job  map[string]any
		name string
		err  error
	}
	results := make([]shardResult, shardTotal)
	var wg sync.WaitGroup
	for i, worker := range p.workers {
		wg.Add(1)
		go func(i int, worker daemon.Client) {
			defer wg.Done()
			req := make(map[string]any, len(payload)+4)
			for k, v := range payload {
				req[k] = v
			}
			req["op"] = "atlas_sphere"
			req["id"] = id
			req["shard_id"] = i
			req["shard_total"] = shardTotal
			if _, set := req["shard_block"]; !set {
				req["shard_block"] = ShardBlock()
			}
			resp, err := worker.Request(req)
			if err != nil {
				results[i] = shardResult{name: worker.Name(), err: err}
				return
			}
			job := resp.Job
			if job == nil {
				job = map[string]any{"id": id}
			}
			job["worker"] = worker.Name()
			job["gpu"] = p.gpus[i]
			results[i] = shardResult{job: job, name: worker.Name()}
		}(i, worker)
	}
	wg.Wait()

	var (
		shards []map[string]any
		failed []string
	)
	for _, r := range results {
		if r.err != nil {
			failed = append(failed, fmt.Sprintf("%s: %v", r.name, r.err))
			continue
		}
		shards = append(shards, r.job)
	}
	if len(shards) == 0 {
		return nil, fmt.Errorf("every atlas shard failed (%s)", strings.Join(failed, "; "))
	}
	merged := MergeShardJobs(shards)
	if len(merged) == 0 {
		return nil, errors.New("atlas shards produced no job")
	}
	job := merged[0]
	if len(failed) > 0 {
		// A partial fleet still renders the whole sphere, just slower: the
		// surviving shards' cells are disjoint, and the missing shard's cells
		// are picked up by a later resubmit.
		job["shard_errors"] = failed
	}
	return job, nil
}

// DefaultShardBlock is how many consecutive cells a worker takes before the
// next block goes to the next worker. Blocks keep neighbouring cells on one
// GPU, which is what the cross-frame cache needs, while still spreading work
// across the sphere. The worker clamps this down when a sphere is too small to
// give every shard a block.
const DefaultShardBlock = 32

// ShardBlock reports the configured block size, overridable with
// FLUX_SHARD_BLOCK. A value of 1 restores a stride-1 interleave.
func ShardBlock() int {
	if raw := strings.TrimSpace(os.Getenv("FLUX_SHARD_BLOCK")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil && n >= 1 {
			return n
		}
	}
	return DefaultShardBlock
}

// Update retunes an in-flight job on every worker that owns a piece of it. A
// sharded sphere must receive the change on all shards, or the cells rendered
// after it would disagree between GPUs.
func (p Pool) Update(id string, fields map[string]any) (map[string]any, error) {
	if strings.TrimSpace(id) == "" {
		return nil, errors.New("update requires a job id")
	}
	if len(fields) == 0 {
		return nil, errors.New("update requires at least one field")
	}
	req := map[string]any{"op": "update", "id": id, "fields": fields}

	type result struct {
		name string
		raw  map[string]any
		err  error
	}
	results := make([]result, len(p.workers))
	var wg sync.WaitGroup
	for i, worker := range p.workers {
		wg.Add(1)
		go func(i int, worker daemon.Client) {
			defer wg.Done()
			resp, err := worker.Request(req)
			results[i] = result{name: worker.Name(), raw: resp.Raw, err: err}
		}(i, worker)
	}
	wg.Wait()

	changed := map[string]any{}
	rejected := map[string]any{}
	applied := 0
	var failures []string
	for _, r := range results {
		if r.err != nil {
			// Workers that do not own this job answer with an error; that is
			// expected for anything but a fully sharded sphere.
			failures = append(failures, r.name+": "+r.err.Error())
			continue
		}
		applied++
		if c, ok := r.raw["changed"].(map[string]any); ok {
			for k, v := range c {
				changed[k] = v
			}
		}
		if rj, ok := r.raw["rejected"].(map[string]any); ok {
			for k, v := range rj {
				rejected[k] = v
			}
		}
	}
	if applied == 0 {
		return nil, fmt.Errorf("no fleet worker accepted the update (%s)", strings.Join(failures, "; "))
	}
	out := map[string]any{"ok": true, "id": id, "shards_updated": applied, "changed": changed}
	if len(rejected) > 0 {
		out["rejected"] = rejected
	}
	return out, nil
}

// activeAtlasJob returns the merged view of a job that still has at least one
// shard queued or running, or nil when the id is unknown or fully settled.
func (p Pool) activeAtlasJob(id string) map[string]any {
	var shards []map[string]any
	active := false
	for _, status := range p.Status() {
		for _, job := range status.JobList {
			if stringValue(job["id"]) != id {
				continue
			}
			shards = append(shards, job)
			switch stringValue(job["status"]) {
			case "running", "queued":
				active = true
			}
		}
	}
	if !active || len(shards) == 0 {
		return nil
	}
	merged := MergeShardJobs(shards)
	if len(merged) == 0 {
		return nil
	}
	return merged[0]
}

// MergeShardJobs collapses jobs sharing an id into one logical job, summing
// per-shard progress. Jobs with a unique id pass through untouched.
func MergeShardJobs(jobs []map[string]any) []map[string]any {
	order := make([]string, 0, len(jobs))
	groups := make(map[string][]map[string]any, len(jobs))
	for _, job := range jobs {
		id := stringValue(job["id"])
		if _, seen := groups[id]; !seen {
			order = append(order, id)
		}
		groups[id] = append(groups[id], job)
	}

	out := make([]map[string]any, 0, len(order))
	for _, id := range order {
		group := groups[id]
		if len(group) == 1 {
			out = append(out, group[0])
			continue
		}
		out = append(out, mergeGroup(group))
	}
	return out
}

func mergeGroup(group []map[string]any) map[string]any {
	sort.Slice(group, func(i, j int) bool {
		return intValue(group[i]["shard_id"]) < intValue(group[j]["shard_id"])
	})

	merged := make(map[string]any, len(group[0])+6)
	for k, v := range group[0] {
		merged[k] = v
	}

	var (
		step, atlasDone, atlasTotal, totalSteps int
		cellsPerHour, etaSeconds                float64
		fullTotal                               int
		shards                                  []map[string]any
		statuses                                []string
		running                                 map[string]any
	)
	for _, job := range group {
		step += intValue(job["step"])
		atlasDone += intValue(job["atlas_done"])
		atlasTotal += intValue(job["atlas_total"])
		totalSteps += intValue(job["total_steps"])
		cellsPerHour += floatValue(job["cells_per_hour"])
		if eta := floatValue(job["eta_seconds"]); eta > etaSeconds {
			etaSeconds = eta
		}
		if full := intValue(job["atlas_full_total"]); full > fullTotal {
			fullTotal = full
		}
		status := stringValue(job["status"])
		statuses = append(statuses, status)
		if status == "running" && running == nil {
			running = job
		}
		shards = append(shards, map[string]any{
			"shard_id": intValue(job["shard_id"]),
			"worker":   job["worker"],
			"gpu":      job["gpu"],
			"status":   status,
			"phase":    job["phase"],
			"step":     intValue(job["step"]),
			"total":    intValue(job["total_steps"]),
			"error":    stringValue(job["error"]),
		})
	}

	if fullTotal <= 0 {
		fullTotal = totalSteps
	}
	merged["step"] = step
	merged["atlas_done"] = atlasDone
	merged["atlas_total"] = atlasTotal
	merged["total_steps"] = fullTotal
	merged["cells_per_hour"] = cellsPerHour
	merged["eta_seconds"] = etaSeconds
	merged["status"] = mergeStatus(statuses)
	merged["shards"] = shards
	merged["shard_count"] = len(group)
	delete(merged, "shard_id")
	delete(merged, "worker")
	delete(merged, "gpu")

	// Surface the phase of a shard that is actually rendering; a done or queued
	// shard's phase would misreport the sphere as finished or not yet started.
	if running != nil {
		merged["phase"] = running["phase"]
	}
	if firstErr := firstError(group); firstErr != "" {
		merged["error"] = firstErr
	}
	return merged
}

// mergeStatus reduces shard statuses to one. Order matters: any shard still
// working keeps the sphere active, and an error only surfaces once no shard can
// still make progress.
func mergeStatus(statuses []string) string {
	has := func(want string) bool {
		for _, status := range statuses {
			if status == want {
				return true
			}
		}
		return false
	}
	switch {
	case has("running"):
		return "running"
	case has("queued"):
		return "queued"
	case has("error"):
		return "error"
	case has("cancelled"):
		return "cancelled"
	case has("done"):
		return "done"
	}
	if len(statuses) > 0 {
		return statuses[0]
	}
	return ""
}

func firstError(group []map[string]any) string {
	for _, job := range group {
		if msg := stringValue(job["error"]); msg != "" {
			return msg
		}
	}
	return ""
}

func stringValue(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case nil:
		return ""
	default:
		return fmt.Sprint(t)
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
	}
	return 0
}

func floatValue(v any) float64 {
	switch t := v.(type) {
	case float64:
		return t
	case int:
		return float64(t)
	case int64:
		return float64(t)
	case json.Number:
		f, _ := t.Float64()
		return f
	case string:
		f, _ := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return f
	}
	return 0
}
