package server

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

// StaticOptions configures the plain file server behind `flux serve oscillihue`.
type StaticOptions struct {
	Addr string
	Root string
	// Fallback receives requests for "/" when Root has no index.html.
	Fallback string
	Quiet    bool
}

// ListenAndServeStatic serves Root over HTTP with no worker, socket, or API
// attached. It is the deploy-preview lane for the web/ tree.
func ListenAndServeStatic(ctx context.Context, opt StaticOptions) error {
	if strings.TrimSpace(opt.Addr) == "" {
		opt.Addr = "127.0.0.1:7870"
	}
	root, err := filepath.Abs(opt.Root)
	if err != nil {
		return err
	}
	info, err := os.Stat(root)
	if err != nil {
		return fmt.Errorf("static root %s: %w", root, err)
	}
	if !info.IsDir() {
		return fmt.Errorf("static root %s is not a directory", root)
	}

	files := http.FileServer(http.Dir(root))
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if opt.Fallback != "" && r.URL.Path == "/" {
			if _, err := os.Stat(filepath.Join(root, "index.html")); err != nil {
				http.Redirect(w, r, opt.Fallback, http.StatusTemporaryRedirect)
				return
			}
		}
		if strings.HasSuffix(r.URL.Path, ".css") || strings.HasSuffix(r.URL.Path, ".js") {
			w.Header().Set("Cache-Control", "no-cache")
		}
		if !opt.Quiet {
			fmt.Printf("%s  %s %s\n", time.Now().Format("15:04:05"), r.Method, r.URL.Path)
		}
		files.ServeHTTP(w, r)
	})

	srv := &http.Server{Addr: opt.Addr, Handler: handler}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = srv.Shutdown(shutdown)
	}()
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
