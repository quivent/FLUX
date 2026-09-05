# KVX decode inject — numbers — 2026-09-05

Live GPU 1 Governor (`hive-gemma`, `:8000`, gateway `:8800`).
`nvidia/Gemma-4-31B-IT-NVFP4`, vLLM MultiConnector:

- `GemstoneVaultKVConnector` (`kv_both`, vault `/kv-vault`)
- `ExampleHiddenStatesConnector` (`kv_producer`, dumps `/residuals`)
- `extract_hidden_states` layer `[17]`
- `--kv-cache-dtype fp8`, `--gpu-memory-utilization 0.62`, `--max-model-len 32768`

Launch: `daemons/kv_connector_level2/hive-gemma-s2.sh`  
Receipt: FLUX `apps/tea/public/train-kvx-inject.json` (`63b5a19`)  
Connector: `gemstone_vault_connector.py` + `vault_prefix.py` (`92cb6f1`)

## What was wrong

Store and load hashed `align(len(prompt)-1)` of the **full** prompt. Any extra
user token changed the hash, so a baked prefix never loaded. External hit rate
on the previous engine was **18 048 / 719 890 = 2.51%**.

Fix: walk block-aligned prefixes longest → shortest; store still writes
`align(len-1)` of the full prompt. Load of an already-aligned prefix must pass
that length as-is (`align(32-1)=16` would drop a block).

## Cold-cache proof (the gate)

GPU prefix cache empty. Vault already on disk. First request was **not** the
baked string; it was `PREFIX` plus extra words.

| Probe | External KV hits | GPU prefix hits |
|---|---|---|
| Longer prompt first (`PREFIX` + extra) | **0 → 32** | **0** |
| Exact baked `PREFIX` next | 32 → 32 | GPU already had the 32 |

32 tokens = **2 blocks** (block size 16).

Greedy exact line still:

```
Please provide the claims you wish to have classified.
```

Dumps still on (`kv_transfer_params.hidden_states_path` under `/residuals`).

Gate: `external_hits > 0` on a **cold GPU cache**, greedy text match, dumps on.

## Same engine, earlier restart (exact PREFIX only)

`external_hits` **5888 → 5920** (Δ **+32**), greedy match, dumps on.

## Counters after the cold EXTRA-first process

Measured live on that engine:

| Metric | Value |
|---|---|
| External prefix queries | 23 128 |
| External prefix hits | 5 920 (**25.6%**) |
| GPU prefix queries | 23 160 |
| GPU prefix hits | 32 (**0.14%**) |

(The 25.6% is this process after the 32-token inject plus later traffic, not
the lifetime of the box.)

## Vault on disk

Host bind: `hive/.swarm/research/spectral-externalization/kv-vault` → `/kv-vault`

| | |
|---|---|
| Prefix folders | 191 |
| Layer `.safetensors` | 11 426 (~60 layers / folder) |
| Bytes | 572 507 785 224 (~533 GiB) |

S1 page (earlier today): slot **128**, pointer **128128**, hash `1073703271`,
dump cosine **1.0**, vault round-trip cosine **0.999842**.

## Not this path

| | |
|---|---|
| `.shard` files | 45 |
| Compiled RADX-KVX | 1 (`hive-protocols.kvx`, 489 696 B, 99 blocks) |
| CUDA mmap vault | 45/45 handles, 128.4 MiB |

This inject is **per-prompt attention KV**, keyed by token-prefix hash, not
splicing all 45 shards into every decode.

## Do not

- Restart a second vLLM on GPU 1
- Steal GPU 0/3 or GPU 2 Qwen
- Treat hive forage `residual_match: false` as wiping these counters
- Commit residual `.safetensors`
