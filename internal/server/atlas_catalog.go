package server

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type atlasSeedRequest struct {
	Seed        string `json:"seed"`
	Description string `json:"description"`
}

func (s Server) storeAtlasReceipt(jobID string, accepted bool, status string, payload map[string]any) {
	db, err := s.openStudioDB()
	if err != nil {
		return
	}
	raw, _ := json.Marshal(payload)
	_, _ = db.Exec(`INSERT INTO atlas_receipts(job_id,nexus_accepted,status,payload_json,updated_at)
		VALUES(?,?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET nexus_accepted=excluded.nexus_accepted,
		status=excluded.status,payload_json=excluded.payload_json,updated_at=excluded.updated_at`,
		jobID, accepted, status, string(raw), time.Now().Unix())
}

func (s Server) restoreAtlasReceipts() {
	db, err := s.openStudioDB()
	if err != nil {
		return
	}
	rows, err := db.Query(`SELECT job_id,nexus_accepted FROM atlas_receipts`)
	if err != nil {
		return
	}
	defer rows.Close()
	for rows.Next() {
		var jobID string
		var accepted bool
		if rows.Scan(&jobID, &accepted) == nil {
			atlasNexusReceipts.Store(jobID, accepted)
		}
	}
}

func (s Server) atlasCatalog(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	db, err := s.openStudioDB()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	jobs := make([]map[string]any, 0)
	rows, err := db.Query(`SELECT id, kind, status, phase, prompt, seed, backend, progress, total, created_at, updated_at
		FROM atlas_jobs ORDER BY updated_at DESC LIMIT 500`)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	for rows.Next() {
		var id, kind, status, phase, prompt, seed, backend string
		var progress, total, createdAt, updatedAt int64
		if rows.Scan(&id, &kind, &status, &phase, &prompt, &seed, &backend, &progress, &total, &createdAt, &updatedAt) == nil {
			jobs = append(jobs, map[string]any{"id": id, "kind": kind, "status": status, "phase": phase, "prompt": prompt, "seed": seed, "backend": backend, "progress": progress, "total": total, "created_at": createdAt, "updated_at": updatedAt})
		}
	}
	rows.Close()
	assets := make([]map[string]any, 0)
	rows, err = db.Query(`SELECT id, job_id, seed, path, access_url, media_type, cell_index, created_at, updated_at
		FROM atlas_assets ORDER BY updated_at DESC LIMIT 10000`)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	for rows.Next() {
		var id, jobID, seed, path, accessURL, mediaType string
		var cell, createdAt, updatedAt int64
		if rows.Scan(&id, &jobID, &seed, &path, &accessURL, &mediaType, &cell, &createdAt, &updatedAt) == nil {
			assets = append(assets, map[string]any{"id": id, "job_id": jobID, "seed": seed, "path": path, "access_url": accessURL, "media_type": mediaType, "cell_index": cell, "created_at": createdAt, "updated_at": updatedAt})
		}
	}
	rows.Close()
	seeds := make([]map[string]any, 0)
	rows, err = db.Query(`SELECT seed, description, source_job_id, created_at, updated_at
		FROM atlas_seeds ORDER BY updated_at DESC LIMIT 1000`)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	for rows.Next() {
		var seed, description, sourceJobID string
		var createdAt, updatedAt int64
		if rows.Scan(&seed, &description, &sourceJobID, &createdAt, &updatedAt) == nil {
			seeds = append(seeds, map[string]any{"seed": seed, "description": description, "source_job_id": sourceJobID, "created_at": createdAt, "updated_at": updatedAt})
		}
	}
	rows.Close()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "jobs": jobs, "assets": assets, "seeds": seeds})
}

func (s Server) atlasSeeds(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	db, err := s.openStudioDB()
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	rows, err := db.Query(`SELECT seed, description, source_job_id, created_at, updated_at
		FROM atlas_seeds ORDER BY updated_at DESC LIMIT 2000`)
	if err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	defer rows.Close()
	seeds := make([]map[string]any, 0)
	for rows.Next() {
		var seed, description, sourceJobID string
		var createdAt, updatedAt int64
		if rows.Scan(&seed, &description, &sourceJobID, &createdAt, &updatedAt) == nil {
			seeds = append(seeds, map[string]any{
				"seed": seed, "description": description, "source_job_id": sourceJobID,
				"created_at": createdAt, "updated_at": updatedAt,
			})
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "seeds": seeds})
}

func (s Server) atlasSeed(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	var req atlasSeedRequest
	if json.NewDecoder(r.Body).Decode(&req) != nil {
		writeError(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	req.Seed = strings.TrimSpace(req.Seed)
	req.Description = strings.TrimSpace(req.Description)
	if req.Seed == "" {
		writeError(w, http.StatusBadRequest, "seed is required")
		return
	}
	if err := s.storeAtlasSeed(req.Seed, req.Description, ""); err != nil {
		writeError(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "seed": req.Seed, "description": req.Description})
}

func (s Server) storeAtlasSeed(seed, description, sourceJobID string) error {
	seed = strings.TrimSpace(seed)
	if seed == "" {
		return nil
	}
	db, err := s.openStudioDB()
	if err != nil {
		return err
	}
	now := time.Now().Unix()
	_, err = db.Exec(`INSERT INTO atlas_seeds(seed, description, source_job_id, created_at, updated_at)
		VALUES(?,?,?,?,?)
		ON CONFLICT(seed) DO UPDATE SET
			description=CASE WHEN excluded.description <> '' THEN excluded.description ELSE atlas_seeds.description END,
			source_job_id=CASE WHEN excluded.source_job_id <> '' THEN excluded.source_job_id ELSE atlas_seeds.source_job_id END,
			updated_at=excluded.updated_at`,
		seed, strings.TrimSpace(description), strings.TrimSpace(sourceJobID), now, now)
	return err
}

func (s Server) storeAtlasJobs(jobs []map[string]any) {
	if len(jobs) == 0 {
		return
	}
	db, err := s.openStudioDB()
	if err != nil {
		slog.Warn("atlas job catalog unavailable", "error", err)
		return
	}
	now := time.Now().Unix()
	tx, err := db.Begin()
	if err != nil {
		return
	}
	for _, job := range jobs {
		id := stringValue(job["id"])
		if id == "" {
			id = stringValue(job["job_id"])
		}
		if id == "" {
			continue
		}
		raw, _ := json.Marshal(job)
		seed := stringValue(job["seed"])
		progress := intValue(job["progress"])
		total := intValue(job["total"])
		if p, ok := job["progress"].(map[string]any); ok {
			progress = intValue(p["current"])
			total = intValue(p["total"])
		}
		_, err = tx.Exec(`INSERT INTO atlas_jobs
			(id, kind, status, phase, prompt, seed, backend, progress, total, payload_json, created_at, updated_at)
			VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
			ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, status=excluded.status,
			phase=excluded.phase, prompt=excluded.prompt, seed=excluded.seed,
			backend=excluded.backend, progress=excluded.progress, total=excluded.total,
			payload_json=excluded.payload_json, updated_at=excluded.updated_at`,
			id, stringValue(job["kind"]), stringValue(job["status"]), stringValue(job["phase"]),
			stringValue(job["prompt"]), seed, stringValue(job["backend"]), progress, total, string(raw), now, now)
		if err != nil {
			_ = tx.Rollback()
			slog.Warn("could not update atlas job catalog", "error", err)
			return
		}
		if seed != "" {
			_, err = tx.Exec(`INSERT INTO atlas_seeds(seed, description, source_job_id, created_at, updated_at)
				VALUES(?,?,?,?,?)
				ON CONFLICT(seed) DO UPDATE SET
				description=CASE WHEN excluded.description <> '' THEN excluded.description ELSE atlas_seeds.description END,
				source_job_id=CASE WHEN excluded.source_job_id <> '' THEN excluded.source_job_id ELSE atlas_seeds.source_job_id END,
				updated_at=excluded.updated_at`,
				seed, stringValue(job["prompt"]), id, now, now)
			if err != nil {
				_ = tx.Rollback()
				slog.Warn("could not update atlas seed catalog", "error", err)
				return
			}
		}
	}
	if err := tx.Commit(); err != nil {
		slog.Warn("could not commit atlas job catalog", "error", err)
	}
}

func (s Server) storeAtlasAsset(event map[string]any) {
	asset, _ := event["asset"].(map[string]any)
	if asset == nil {
		return
	}
	jobID := stringValue(event["job_id"])
	if jobID == "" {
		jobID = stringValue(asset["job_id"])
	}
	assetPath := stringValue(asset["path"])
	accessURL := stringValue(asset["access_url"])
	cell := intValue(asset["cell_index"])
	if _, present := asset["cell_index"]; !present {
		cell = intValue(asset["index"])
	}
	key := jobID + "\x00" + assetPath + "\x00" + accessURL
	if cell >= 0 {
		key = jobID + "\x00cell\x00" + strconv.Itoa(cell)
	}
	sum := sha256.Sum256([]byte(key))
	id := hex.EncodeToString(sum[:16])
	raw, _ := json.Marshal(asset)
	db, err := s.openStudioDB()
	if err != nil {
		slog.Warn("atlas asset catalog unavailable", "error", err)
		return
	}
	now := time.Now().Unix()
	_, err = db.Exec(`INSERT INTO atlas_assets
		(id, job_id, seed, path, access_url, media_type, cell_index, metadata_json, created_at, updated_at)
		VALUES(?,?,?,?,?,?,?,?,?,?)
		ON CONFLICT(id) DO UPDATE SET job_id=excluded.job_id, seed=excluded.seed,
		path=excluded.path, access_url=excluded.access_url, media_type=excluded.media_type,
		cell_index=excluded.cell_index, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at`,
		id, jobID, stringValue(asset["seed"]), assetPath, accessURL,
		stringValue(asset["media_type"]), cell, string(raw), now, now)
	if err != nil {
		slog.Warn("could not update atlas asset catalog", "error", err)
	}
}

func (s Server) reconcileAtlasCatalog() {
	resp, err := s.client.Request(map[string]any{"op": "jobs"})
	if err == nil {
		s.storeAtlasJobs(resp.Jobs)
	}
	atlasRoot := filepath.Join(s.cfg.OutputDir, "atlas")
	_ = filepath.WalkDir(atlasRoot, func(file string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil || entry.IsDir() {
			return nil
		}
		ext := strings.ToLower(filepath.Ext(file))
		if ext != ".png" && ext != ".jpg" && ext != ".jpeg" && ext != ".webp" {
			return nil
		}
		rel, err := filepath.Rel(s.cfg.OutputDir, file)
		if err != nil {
			return nil
		}
		jobID := strings.TrimSuffix(filepath.Base(filepath.Dir(file)), ".sphere")
		cell := -1
		base := strings.TrimSuffix(filepath.Base(file), filepath.Ext(file))
		if strings.HasPrefix(base, "cell_") {
			if parsed, parseErr := strconv.Atoi(strings.TrimPrefix(base, "cell_")); parseErr == nil {
				cell = parsed
			}
		}
		s.storeAtlasAsset(map[string]any{
			"job_id": jobID,
			"asset": map[string]any{
				"path":       filepath.ToSlash(rel),
				"access_url": "/outputs/" + filepath.ToSlash(rel),
				"media_type": "image/" + strings.TrimPrefix(ext, "."),
				"cell_index": cell,
				"source":     "startup-reconciliation",
			},
		})
		return nil
	})
}
