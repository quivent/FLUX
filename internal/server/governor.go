package server

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

func governorUpstream() *url.URL {
	raw := strings.TrimSpace(os.Getenv("GOVERNOR_URL"))
	if raw == "" {
		raw = "http://127.0.0.1:8000"
	}
	u, err := url.Parse(raw)
	if err != nil || u.Scheme == "" || u.Host == "" {
		u, _ = url.Parse("http://127.0.0.1:8000")
	}
	return u
}

func (s Server) governorPage(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	if r.URL.Path == "/governor/" {
		http.Redirect(w, r, "/governor", http.StatusPermanentRedirect)
		return
	}
	if strings.TrimSuffix(r.URL.Path, "/") != "/governor" {
		http.NotFound(w, r)
		return
	}
	http.ServeFile(w, r, filepath.Join(s.cfg.Root, "apps", "tea", "public", "governor.html"))
}

func (s Server) governorModels(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		methodNotAllowed(w, http.MethodGet)
		return
	}
	s.proxyGovernor(w, r, "/v1/models")
}

func (s Server) governorChat(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		methodNotAllowed(w, http.MethodPost)
		return
	}
	s.proxyGovernor(w, r, "/v1/chat/completions")
}

func (s Server) proxyGovernor(w http.ResponseWriter, r *http.Request, targetPath string) {
	upstream := governorUpstream()
	proxy := httputil.NewSingleHostReverseProxy(upstream)
	proxy.FlushInterval = 50 * time.Millisecond
	proxy.Transport = &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		ResponseHeaderTimeout: 2 * time.Minute,
		IdleConnTimeout:       90 * time.Second,
		DisableCompression:    true,
	}
	proxy.ErrorHandler = func(rw http.ResponseWriter, _ *http.Request, err error) {
		writeJSON(rw, http.StatusBadGateway, map[string]any{"error": err.Error()})
	}
	proxy.ModifyResponse = func(resp *http.Response) error {
		resp.Header.Set("X-Accel-Buffering", "no")
		resp.Header.Set("Cache-Control", "no-cache")
		return nil
	}
	director := proxy.Director
	proxy.Director = func(req *http.Request) {
		director(req)
		req.URL.Scheme = upstream.Scheme
		req.URL.Host = upstream.Host
		req.URL.Path = targetPath
		req.URL.RawPath = targetPath
		req.Host = upstream.Host
	}
	proxy.ServeHTTP(w, r)
}
