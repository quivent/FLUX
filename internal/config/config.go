package config

import (
	"os"
	"path/filepath"
)

type Config struct {
	Root       string
	ModelDir   string
	OutputDir  string
	Backend    string
	Python     string
	GeneratePy string
	CheckPy    string
	History    string
}

func Load() Config {
	root, err := os.Getwd()
	if err != nil {
		root = "."
	}
	if !hasRunnerFiles(root) {
		if exe, err := os.Executable(); err == nil {
			candidates := []string{filepath.Dir(exe)}
			if resolved, err := filepath.EvalSymlinks(exe); err == nil {
				candidates = append(candidates, filepath.Dir(resolved))
			}
			for _, candidate := range candidates {
				if hasRunnerFiles(candidate) {
					root = candidate
					break
				}
			}
		}
	}
	home := getenv("HOME", ".")
	modelDir := resolveModelDir(home)
	outputDir := resolveOutputDir(home)
	backend := getenv("FLUX_BACKEND", "auto")
	venvPython := filepath.Join(root, ".venv", "bin", "python")
	python := getenv("FLUX_PYTHON", venvPython)
	if _, err := os.Stat(python); err != nil {
		python = getenv("PYTHON", "python3")
	}
	return Config{
		Root:       root,
		ModelDir:   modelDir,
		OutputDir:  outputDir,
		Backend:    backend,
		Python:     python,
		GeneratePy: filepath.Join(root, "generate.py"),
		CheckPy:    filepath.Join(root, "check_flux.py"),
		History:    filepath.Join(root, "history.jsonl"),
	}
}

func getenv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func resolveModelDir(home string) string {
	envModel := firstEnv("MODEL_DIR", "FLUX_MODEL_DIR")
	candidates := []string{
		envModel,
		"/models/flux1",
		"/models/FLUX.1-dev",
		"/models/flux/FLUX.1-dev",
		filepath.Join(home, "Models", "flux1"),
		filepath.Join(home, "models", "flux1"),
		filepath.Join(home, "models", "FLUX.1-dev"),
	}
	for _, candidate := range candidates {
		if validFluxModelDir(candidate) {
			return candidate
		}
	}
	if envModel != "" {
		return envModel
	}
	if _, err := os.Stat("/models"); err == nil {
		return "/models/flux1"
	}
	return filepath.Join(home, "Models", "flux1")
}

func resolveOutputDir(home string) string {
	if output := firstEnv("OUT_DIR", "FLUX_OUTPUT_DIR"); output != "" {
		return output
	}
	if _, err := os.Stat("/runs"); err == nil {
		return "/runs/flux-output"
	}
	return filepath.Join(home, "Models", "flux-output")
}

func firstEnv(keys ...string) string {
	for _, key := range keys {
		if value := os.Getenv(key); value != "" {
			return value
		}
	}
	return ""
}

func validFluxModelDir(path string) bool {
	if path == "" {
		return false
	}
	if _, err := os.Stat(filepath.Join(path, "model_index.json")); err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(path, "transformer")); err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(path, "vae")); err != nil {
		return false
	}
	return true
}

func hasRunnerFiles(root string) bool {
	if _, err := os.Stat(filepath.Join(root, "worker.py")); err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(root, "generate.py")); err != nil {
		return false
	}
	return true
}
