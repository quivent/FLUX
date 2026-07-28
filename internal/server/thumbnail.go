package server

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"image"
	"image/jpeg"
	_ "image/png"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func (s Server) assetThumbnail(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	source := strings.TrimSpace(r.URL.Query().Get("src"))
	// Clients may send either a root-relative path or a fully qualified URL.
	// Behind a reverse proxy the latter is common, so reduce it to its path.
	if parsed, err := url.Parse(source); err == nil && parsed.Path != "" && (parsed.Scheme == "http" || parsed.Scheme == "https") {
		source = parsed.Path
	}
	if !strings.HasPrefix(source, "/outputs/") {
		writeError(w, http.StatusBadRequest, "thumbnail source must be an output asset")
		return
	}
	relative := filepath.Clean(filepath.FromSlash(strings.TrimPrefix(source, "/outputs/")))
	if relative == "." || relative == "" || relative == ".." || strings.HasPrefix(relative, ".."+string(filepath.Separator)) {
		writeError(w, http.StatusBadRequest, "invalid thumbnail source")
		return
	}
	full := filepath.Join(s.cfg.OutputDir, relative)
	resolved, err := filepath.Rel(s.cfg.OutputDir, full)
	if err != nil || resolved == ".." || strings.HasPrefix(resolved, ".."+string(filepath.Separator)) {
		writeError(w, http.StatusBadRequest, "thumbnail source escapes output directory")
		return
	}
	info, err := os.Stat(full)
	if err != nil || !info.Mode().IsRegular() {
		http.NotFound(w, r)
		return
	}
	width := thumbnailWidth(r.URL.Query().Get("w"))
	cacheKey := fmt.Sprintf("%s\x00%d\x00%d\x00%d", full, info.ModTime().UnixNano(), info.Size(), width)
	sum := sha256.Sum256([]byte(cacheKey))
	cachePath := filepath.Join(s.cfg.Root, ".fluxd", "thumbnails", hex.EncodeToString(sum[:16])+".jpg")
	if _, err := os.Stat(cachePath); err != nil {
		if err := makeThumbnail(full, cachePath, width); err != nil {
			writeError(w, http.StatusInternalServerError, "could not create thumbnail")
			return
		}
	}
	w.Header().Set("Content-Type", "image/jpeg")
	w.Header().Set("Cache-Control", "public, max-age=604800, immutable")
	http.ServeFile(w, r, cachePath)
}

func thumbnailWidth(raw string) int {
	value, err := strconv.Atoi(raw)
	if err != nil || value < 160 {
		return 384
	}
	return min(value, 640)
}

func makeThumbnail(source, destination string, maxWidth int) error {
	input, err := os.Open(source)
	if err != nil {
		return err
	}
	decoded, _, err := image.Decode(input)
	_ = input.Close()
	if err != nil {
		return err
	}
	bounds := decoded.Bounds()
	width, height := bounds.Dx(), bounds.Dy()
	if width <= 0 || height <= 0 {
		return fmt.Errorf("image has empty bounds")
	}
	targetWidth := min(width, maxWidth)
	targetHeight := max(1, height*targetWidth/width)
	target := image.NewRGBA(image.Rect(0, 0, targetWidth, targetHeight))
	for y := range targetHeight {
		sourceY := bounds.Min.Y + y*height/targetHeight
		for x := range targetWidth {
			sourceX := bounds.Min.X + x*width/targetWidth
			target.Set(x, y, decoded.At(sourceX, sourceY))
		}
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o700); err != nil {
		return err
	}
	temporary, err := os.CreateTemp(filepath.Dir(destination), ".thumbnail-*.jpg")
	if err != nil {
		return err
	}
	temporaryName := temporary.Name()
	defer os.Remove(temporaryName)
	if err := jpeg.Encode(temporary, target, &jpeg.Options{Quality: 78}); err != nil {
		_ = temporary.Close()
		return err
	}
	if err := temporary.Close(); err != nil {
		return err
	}
	return os.Rename(temporaryName, destination)
}
