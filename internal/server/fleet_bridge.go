package server

import (
	"errors"

	"local/flux/internal/daemon"
)

// The HTTP layer talks to "the worker" in a dozen places. These helpers make
// that phrase mean either the historical single worker or a GPU fleet, so the
// handlers stay unchanged in shape.
//
// Every helper falls back to s.client when no GPU is detected, which keeps
// single-GPU hosts, CPU hosts, and Macs on exactly the path they used before.

// fleetOn reports whether multi-GPU dispatch is active.
func (s Server) fleetOn() bool { return s.pool.Enabled() }

// workerStart brings up every worker the server can use.
func (s Server) workerStart(preload bool) error {
	if s.fleetOn() {
		return s.pool.Start(preload)
	}
	return s.client.Start(preload)
}

// workerStop shuts the fleet or the single worker down.
func (s Server) workerStop() error {
	if !s.fleetOn() {
		return s.client.Stop()
	}
	if errs := s.pool.Stop(); len(errs) > 0 {
		return errs[0]
	}
	return nil
}

// workerPing reports representative health. For a fleet this is the aggregate:
// reachable if any worker answers, loaded if any worker holds the model.
func (s Server) workerPing() (daemon.Response, error) {
	if !s.fleetOn() {
		return s.client.Request(map[string]any{"op": "ping"})
	}
	snap := s.pool.Snapshot()
	if snap.Up == 0 {
		return daemon.Response{}, errors.New("no fleet worker is running")
	}
	return daemon.Response{
		OK:      true,
		Loaded:  snap.Loaded,
		Device:  snap.Device,
		Backend: snap.Backend,
	}, nil
}

// workerSnapshot returns jobs plus health in one pass. Shards of a sharded
// atlas job arrive already merged into a single logical job.
func (s Server) workerSnapshot() (daemon.Response, error) {
	if !s.fleetOn() {
		return s.client.Request(map[string]any{"op": "jobs"})
	}
	snap := s.pool.Snapshot()
	if snap.Up == 0 {
		return daemon.Response{}, errors.New("no fleet worker is running")
	}
	return daemon.Response{
		OK:      true,
		Loaded:  snap.Loaded,
		Device:  snap.Device,
		Backend: snap.Backend,
		Jobs:    snap.Jobs,
	}, nil
}

// workerDispatch sends a one-off request to whichever worker is idlest, so
// independent renders spread across the GPUs.
func (s Server) workerDispatch(req map[string]any) (daemon.Response, error) {
	if !s.fleetOn() {
		return s.client.Request(req)
	}
	resp, _, err := s.pool.Dispatch(req)
	return resp, err
}

// workerBroadcast sends a request to every worker and returns the first
// success. Used for operations that address a job by id without knowing which
// worker owns it, such as cancel and prune.
func (s Server) workerBroadcast(req map[string]any) (daemon.Response, error) {
	if !s.fleetOn() {
		return s.client.Request(req)
	}
	var (
		first   daemon.Response
		lastErr error
		ok      bool
	)
	removed := []string{}
	for _, worker := range s.pool.Workers() {
		resp, err := worker.Request(req)
		if err != nil {
			lastErr = err
			continue
		}
		removed = append(removed, resp.Removed...)
		if !ok {
			first = resp
			ok = true
		}
	}
	if !ok {
		if lastErr != nil {
			return daemon.Response{}, lastErr
		}
		return daemon.Response{}, errors.New("no fleet worker accepted the request")
	}
	first.Removed = removed
	return first, nil
}

// workerUpdate retunes an in-flight job, on every shard when it is sharded.
func (s Server) workerUpdate(id string, fields map[string]any) (map[string]any, error) {
	if !s.fleetOn() {
		resp, err := s.client.Request(map[string]any{"op": "update", "id": id, "fields": fields})
		if err != nil {
			return nil, err
		}
		return resp.Raw, nil
	}
	return s.pool.Update(id, fields)
}

// fleetStatusPayload describes the fleet for the health endpoint. It returns
// nil when no fleet is active so the response shape is unchanged on single-GPU
// hosts.
func (s Server) fleetStatusPayload() map[string]any {
	if !s.fleetOn() {
		return nil
	}
	statuses := s.pool.Status()
	workers := make([]map[string]any, 0, len(statuses))
	up := 0
	for _, status := range statuses {
		if status.Up {
			up++
		}
		workers = append(workers, map[string]any{
			"name":    status.Name,
			"gpu":     status.GPU,
			"up":      status.Up,
			"loaded":  status.Loaded,
			"device":  status.Device,
			"backend": status.Backend,
			"jobs":    status.Jobs,
			"active":  status.Active,
			"error":   status.Error,
		})
	}
	return map[string]any{
		"size":    s.pool.Size(),
		"gpus":    s.pool.GPUs(),
		"up":      up,
		"workers": workers,
	}
}
