# DEEPSEEK FLASH 4 (0731) H200 OPTIMIZATION PROFILE SPECIFICATION
**TARGET HARDWARE:** NVIDIA H200 (141GB HBM3e VRAM, 4.8 TB/s Memory Bandwidth)  
**MODEL:** `deepseek-ai/DeepSeek-V3-Flash-0731`  
**ALIAS:** `deepblue`  
**STATUS:** REGISTERED IN GEMSTONE INVENTORY

---

### I. H200 HARDWARE OPTIMIZATION PARAMETERS

```json
{
  "profile": "deepseek-flash4-h200",
  "machine": "deepblue",
  "gpu": "NVIDIA H200 (141GB HBM3e)",
  "model": "deepseek-ai/DeepSeek-V3-Flash-0731",
  "backend": "vllm",
  "gpu_memory_utilization": 0.92,
  "max_model_len": 131072,
  "kv_cache_dtype": "fp8",
  "enable_prefix_caching": true,
  "attention_backend": "FLASH_ATTN_V3",
  "speculative_decoding": {
    "enabled": true,
    "num_speculative_tokens": 8,
    "draft_model": "deepseek-ai/DeepSeek-V3-Flash-Draft"
  },
  "tensor_parallel_size": 1
}
```

---

### II. EXECUTION COMMANDS ON DEEPBLUE
Once SSH key authorization is completed on `deepblue`:

```bash
# 1. Register / Verify deepblue node
gemstone register deepblue --host <IP_ADDRESS> --user ubuntu

# 2. Run the H200 DeepSeek Flash 4 0731 Provisioning Pipeline
gemstone provision deepseek-flash4-h200 -m deepblue

# 3. Direct Governor Serve Command
gemstone governor configure \
  --container deepseek-flash4 \
  --model deepseek-ai/DeepSeek-V3-Flash-0731 \
  --image vllm/vllm-openai:latest \
  --gpu-util 0.92 \
  --context 131072 \
  --kv-cache-dtype fp8 \
  --enable-prefix-caching=true \
  --speculative=true \
  --spec-tokens 8 \
  --tool-calling=true
```

---

### III. EXPECTED PERFORMANCE METRICS ON H200
* **Tokens / Second**: **>180+ tok/s** (single-stream output throughput)
* **Max Active Context**: 131,072 tokens with FP8 KV cache compression
* **VRAM Footprint**: ~24 GB model weights + ~48 GB KV Cache = **~72 GB / 141 GB VRAM used** (leaves 69 GB buffer for high concurrent batch size).
