package server

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestParseRigModelFluxBF16(t *testing.T) {
	cmd := "/home/ubuntu/CLIs/flux/.venv/bin/python -u /home/ubuntu/CLIs/flux/worker.py --socket /home/ubuntu/CLIs/flux/.fluxd/flux-gpu0.sock --model-dir /home/ubuntu/models/FLUX.1-dev --out-dir /home/ubuntu/models/flux-output --backend cuda --preload"
	model, precision, path := parseRigModel(cmd, "FLUX renderer", "GPU 0 BF16 worker")
	if model != "FLUX.1-dev" {
		t.Fatalf("model %q", model)
	}
	if precision != "BF16" {
		t.Fatalf("precision %q", precision)
	}
	if path != "/home/ubuntu/models/FLUX.1-dev" {
		t.Fatalf("path %q", path)
	}
}

func TestParseRigModelFluxFP8(t *testing.T) {
	cmd := "/home/ubuntu/CLIs/flux/.venv/bin/python -u /home/ubuntu/CLIs/flux/worker.py --socket /home/ubuntu/CLIs/flux/.fluxd/flux-gpu3.sock --model-dir /home/ubuntu/models/FLUX.1-dev --fp8-transformer /home/ubuntu/models/FLUX.1-dev-fp8/flux1-dev-fp8.safetensors --backend cuda"
	model, precision, path := parseRigModel(cmd, "FLUX renderer", "GPU 3 FP8 worker")
	if model != "FLUX.1-dev" {
		t.Fatalf("model %q", model)
	}
	if precision != "FP8" {
		t.Fatalf("precision %q", precision)
	}
	if !strings.Contains(path, "flux1-dev-fp8") {
		t.Fatalf("path %q", path)
	}
}

func TestParseRigModelVLLM(t *testing.T) {
	cmd := "/usr/bin/python3 /usr/local/bin/vllm serve --host 0.0.0.0 --port 8000 --model /models/gemma --served-model-name jury gemma-jury --quantization fp8"
	model, precision, path := parseRigModel(cmd, "Inference", "jury")
	if model != "jury · gemma" {
		t.Fatalf("model %q", model)
	}
	if precision != "FP8" {
		t.Fatalf("precision %q", precision)
	}
	if path != "/models/gemma" {
		t.Fatalf("path %q", path)
	}
}

func TestClassifyMojEvaluatorUsesOutputDir(t *testing.T) {
	suite, _ := classifyRigProcess("python moj_evaluator.py --serve", "python", 1, 0)
	if suite == "Fashion jury" {
		t.Fatal("gpu0 moj labeled fashion")
	}
	if suite != "MoJ jury" {
		t.Fatalf("suite %q", suite)
	}
}

func TestDecorateRigGPUMarksFluxSeat(t *testing.T) {
	g := rigGPU{Index: 3, Processes: []rigProcess{{
		PID: 131552, MemoryMiB: 35706, Suite: "FLUX renderer", Task: "GPU 3 FP8 worker",
		Command: "python worker.py --socket /x/flux-gpu3.sock --model-dir /home/ubuntu/models/FLUX.1-dev --fp8-transformer /home/ubuntu/models/FLUX.1-dev-fp8/flux1-dev-fp8.safetensors",
	}}}
	decorateRigGPU(&g)
	if g.Flux == nil || g.Flux["precision"] != "FP8" {
		t.Fatalf("flux %+v", g.Flux)
	}
	if len(g.Occupants) != 1 || g.Occupants[0]["suite"] != "FLUX renderer" {
		t.Fatalf("occupants %+v", g.Occupants)
	}
}

func TestRigPageShowsOccupancy(t *testing.T) {
	page, err := os.ReadFile(filepath.Join(repoRoot(t), "apps", "tea", "public", "rig.html"))
	if err != nil {
		t.Fatal(err)
	}
	src := string(page)
	for _, tok := range []string{"Suite", "VRAM", "FLUX", "occupants", "memory_mib"} {
		if !strings.Contains(src, tok) {
			t.Errorf("rig page missing %q", tok)
		}
	}
}
