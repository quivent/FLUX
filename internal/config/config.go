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
	modelDir := getenv("MODEL_DIR", "/Users/joshkornreich/Models/flux1")
	outputDir := getenv("OUT_DIR", "/Users/joshkornreich/Models/flux-output")
	backend := getenv("FLUX_BACKEND", "auto")
	venvPython := filepath.Join(root, ".venv", "bin", "python")
	python := getenv("FLUX_PYTHON", venvPython)
	if _, err := os.Stat(python); err != nil {
		python = getenv("PYTHON", "python3.13")
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

func hasRunnerFiles(root string) bool {
	if _, err := os.Stat(filepath.Join(root, "worker.py")); err != nil {
		return false
	}
	if _, err := os.Stat(filepath.Join(root, "generate.py")); err != nil {
		return false
	}
	return true
}
