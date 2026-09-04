package server

import (
	"net/http"
	"os"
	"path/filepath"
	"strings"
)

// GET /api/train — spectral externalization protocol, shard ledger, GPU 1 stream.
func (s Server) trainAPI(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	tea := filepath.Join(s.cfg.Root, "apps", "tea", "public")
	stream := filepath.Join(s.cfg.Root, ".fluxd", "governor_train_stream.json")
	results := readJSONFile(filepath.Join(tea, "train-results.json"))
	if results == nil {
		results = readJSONFile("/home/ubuntu/hive/.swarm/research/spectral-externalization/results/live_wall.json")
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":        true,
		"gpu":       1,
		"exclusive": true,
		"question":  "Train the Governor for 100% reliability, expansion of memory, and performance of reasoning. He trains himself. Qwen is reason.",
		"protocol":  readJSONFile(filepath.Join(tea, "train-protocol.json")),
		"spectral":  readJSONFile(filepath.Join(tea, "train-spectral.json")),
		"shards":    readJSONFile(filepath.Join(tea, "train-shards.json")),
		"results":   results,
		"stream":    readJSONFile(stream),
		"drive":     trainDriveSnapshot(),
		"progress":  trainProgressSnapshot(readJSONFile(stream)),
	})
}

func trainProgressSnapshot(stream any) map[string]any {
	pic := filepath.Join(hiveRoot(), ".swarm", "pic")
	home, err := os.UserHomeDir()
	if err != nil {
		home = "/home/ubuntu"
	}
	sealed := false
	source := ""
	if m, ok := stream.(map[string]any); ok {
		sealed, _ = m["sealed_session"].(bool)
		source, _ = m["source"].(string)
	}
	return map[string]any{
		"pic":            readJSONFile(filepath.Join(pic, "state.json")),
		"proposal":       readJSONFile(filepath.Join(pic, "proposal.json")),
		"implement":      readJSONFile(filepath.Join(pic, "implement.json")),
		"correct":        readJSONFile(filepath.Join(pic, "correct.json")),
		"commissions":    readJSONFile(filepath.Join(home, ".council", "governor", "commission", "work.json")),
		"results_file":   filepath.Join("apps", "tea", "public", "train-results.json"),
		"stream_source":  source,
		"sealed_session": sealed,
	}
}

func trainDriveSnapshot() map[string]any {
	root := hiveRoot()
	research := filepath.Join(root, ".swarm", "research", "dual-seat-drive")
	logPath := os.Getenv("DUAL_SEAT_LOG")
	if logPath == "" {
		logPath = "/home/ubuntu/hive-research/logs/dual-seat-drive.jsonl"
	}
	pid := strings.TrimSpace(readText("/home/ubuntu/hive-research/run/dual-seat-drive.pid"))
	return map[string]any{
		"alive":  pid != "" && processAlive(pid),
		"pid":    pid,
		"state":  readJSONFile(filepath.Join(research, "state.json")),
		"recent": tailDriveLog(logPath, 400),
	}
}
