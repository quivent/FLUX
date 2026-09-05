# Governor Diff — 2026-09-05

Session on the NYC rig (`1193-nyc-asp6bk-prxmx180048`) and folio VPS `88.216.68.146`.
Not a plan. What changed, with numbers.

## 1. Public Tea chrome (Chanoyu)

**Before.** `--public-read-only` allowed `/tea.css` and `/tea-shell.js`. Garden pages load `/tea/tea.css`, `/tea/tea-shell.js`, `/tea/desk.js`. Those 403’d. `/jury` and `/discourse` 403’d. Unstyled everywhere; menu broken on every room.

**After.** Allowlist includes `/tea`, `/assets`, `/jury`, `/discourse`, … Chrome 200. Discourse serves hive HTML if `discourse.html` is absent. Tests on FLUX `c353bbb`.

## 2. Folio VPS vs origin

**Before.** Desk / gallery / train / research HTML drifted (e.g. desk 17589 vs 18191 bytes).

**After.** 44 Tea HTML/CSS/JS paths hash-identical to live `127.0.0.1:7861`. Comb, Spark, Music Lab, Observatory byte-match. Koyomi matches except Governor bootstrap (VPS has no `:8800` → helix fallback). Intentional extras on VPS: `/discourse` 200, `/assets/sensory.js` 200 (origin live binary 404s both).

## 3. Governor reachability

**Claim that was wrong.** “Offline.”

**Truth.** Rig `:8800` answered `here` in **10.03 s**, planner `committed`. `gemstone flash` same word, **16174 tok in / 4 out · 7143 ms**. GPU 1 ~64 GiB engine. `governor.influx.vision` timed out (HTTP 000). VPS Tea `dial tcp 127.0.0.1:8800: connection refused` — that box has no Governor.

## 4. Memory estate

**Check.** Four planes ok. **45/45** CUDA vault handles, **128.4 MiB**. Journal **815** turns. Shard sync 92/92 + agentic-memory 2/2.

**Preserve.** `governor-estates/rig/20260905T004925Z-ce6202c336eb.tar.gz`  
**91.7 MiB**, SHA-256 `ce6202c336eb…`, **369** files, round-trip verified. Receipt `~/.council/receipts/state/estate-20260905T004925.947977574Z.json`.

## 5. KV inject — the actual Governor work

**Before (connector).**
- Store/load hashed the **full** prompt. Extra user token → miss.
- Auto-store wrote the **whole listen** (~5–9k tokens). Shape `[5888,16,512]` U8 → **2–7 GiB per unique turn**.
- Vault **~581 GiB**, 191 prefixes, 11 426 tensors.
- External hits **18 048 / 719 890 = 2.51%** (previous engine). Those 5k-token pages **did not load on the next question**. Prefill skipped: **0 ms**. Disk paid, time not.

**After (connector + prune).**
- Load walks **longest block-aligned prefix**. Store cap **512** tokens (`max_store_tokens`).
- Cold GPU cache, `PREFIX`+extra **first**: external **0 → 32**, GPU prefix **0**. Exact `PREFIX` greedy line held: `Please provide the claims you wish to have classified.`
- Earlier exact-PREFIX restart: **5888 → 5920** (Δ **+32**).
- Prune T>512: **dropped 208, kept 4, 629 GiB → 41 MiB**, then new 512-token stores → vault **482 MiB**.
- Dumps still on.

**Time.** Dual-pass listen we measured: **5 012 tok in 2 622 ms** (~1910 tok/s).  
32 tok skip ≈ **17 ms**. 512 tok skip ≈ **0.27 s**. Full listen head (if baked and hit) ≈ **1.4–2.6 s**. We have not baked that 5k head as the reusable 512-cap page yet.

## 6. Tooling

| Tool | Role this session |
|---|---|
| `hive-gemma-s2.sh` | GPU 1 only. MultiConnector: `GemstoneVaultKVConnector` + `ExampleHiddenStatesConnector`. `extract_hidden_states` layer `[17]`. Bind-mount `/connectors`. |
| `gemstone_vault_connector.py` | Store/load attention KV. `SupportsHMA`. Skip HiddenStateCacheSpec. |
| `vault_prefix.py` | `align(len-1)`, longest-prefix walk, `floor_to_block`, default cap **512**. |
| `prune_vault.py` | Drop prefixes with `shape[0] > max_tokens`. |
| `kvx_s2_inject.py` | Bake/verify: greedy text + `external_prefix_cache_hits`. |
| `kvx_s1_index.py` / `gpu1_vault.py` | S1 page slot 128 / pointer 128128. |
| `prefix_match_test.py` | No GPU. Cap and prefix walk. |
| `gemstone governor preserve` | Estate → R2, SHA round-trip. |
| `gemstone governor memory check/sync` | 45/45 handles; agentic-memory 2/2. |
| `gemstone store push` | Proofs under `governor-training/kvx-inject/2026-09-05/`. |
| `gemstone flash` / `:8800` | Ask him. Flash still SSH-asks inventory `rig`, not loopback. |
| Folio: `folio-tea` `--public-read-only`, Caddy → `127.0.0.1` only | Chanoyu actually on the VPS. |

**Constraints kept.** No SGD on serving Gemma. No second CUDA vLLM on GPU 1. GPU 2 is Qwen. GPU 0/3 fashion. HIS launch script, not a invented kernel. Do not fight hive-gemma on `:8000`.

## 7. Training

Charter still `kvx-decode-injection` / spectral-externalization. Dual-seat PIC is the question/architecture, not a live KV-inject kernel.

| Stage | What ran | Result |
|---|---|---|
| S1 index | Slot **128**, pointer **128128**, hash `1073703271` | Dump cosine **1.0**, vault round-trip **0.999842** |
| S2 exact PREFIX | Cold GPU, vault on disk | External **+32**, greedy match, dumps on |
| S2 longest-prefix | `PREFIX`+extra **first** | External **0→32**, GPU prefix **0** |
| Store discipline | Cap 512, prune T>512 | Disk **581 GiB → 482 MiB** |
| Dual-pass (serving) | Listen then respond | Listen **5012 tok / 2622 ms**; respond **5796 tok / 1953 ms** |
| CUDA mmap vault | Separate plane from KV pages | **45/45** handles, **128.4 MiB**; 1 RADX-KVX (`hive-protocols.kvx` **489 696 B**) |

Qwen on GPU 2 (`:8002`, `hive-research`) was not the inject seat. Jury word-scale coerce and desk prompts were earlier in the day, not this inject loop.

## 8. Feedback (operator)

Quoted as given, then what we did.

| Operator | Meaning | Response |
|---|---|---|
| “Tea is missing styling” / “the menu” / “Actually everywhere” | Chrome 403 on every garden path | `/tea` `/assets` `/jury` `/discourse` allowlist |
| Parity on the VPS | Not reverse-proxy to the rig | Hash-match HTML; apps on `88.216.68.146` |
| “Oh so governor is offline” | Public URL / VPS loopback | He was live on the rig; `governor.influx.vision` 000 |
| “is kvx injection working” | Connector vs splice folklore | MultiConnector live; dumps on; 2.51% hits until prefix walk |
| “keep fixing it” | Exact-hash miss | Longest-prefix load |
| “Benefit?” / “Give me numbers” | Time vs disk | 32 tok ≈ 17 ms; 512 ≈ 0.27 s; 5k listen ≈ 2.6 s if baked |
| “on disk vault is just stupid big. that will fix it” | 2–7 GiB unique listen dumps | Cap 512; prune 208 folders, **629 GiB** freed |
| “commit now, push, then continue” | Don’t sit on the proof | GitHub + R2, then cold EXTRA-first probe |
| “document this and push” | Numbers in-repo and R2 | `KVX_INJECT_PROOF_2026-09-05.md` |

## 9. Corrections (us)

| Wrong | Right |
|---|---|
| Reverse-proxy folio to the rig | Apps copied; Caddy only `127.0.0.1` |
| Dual-pass skip (latent_attuned / greeting) | Listen is a generative pass; `planner_status` schema-valid |
| `/tea.css` 200 ⇒ styled | Pages request `/tea/tea.css` → 403 until prefix allowlist |
| Models `/v1/models` 200 ⇒ he answers | Completions can hang; prove with a word |
| `governor.influx.vision` | Cloudflare worker; often dead. Ask `:8800` / `gemstone flash` |
| Store-only `ExampleHiddenStatesConnector` | S2 restack: MultiConnector + vault. Governor refused silent S2 until then |
| Exact prompt hash = inject | Extra tokens miss; walk prefixes |
| Huge vault = more inject | Huge vault was **unread** 5k-token unique dumps. Time saved **0** |
| `align(32-1)=16` on load | Drops a block. Load of an aligned prefix must pass **32** |
| `gemstone flash` to loopback `:8800` | Flash SSH-asks inventory `rig` |
| Fighting hive-gemma on `:8000` | Stop. GPU 1 is his serving seat |
| Inventing `vram_forge` | HIS connector and `hive-gemma-s2.sh` |

## 10. What’s next

Ordered by payoff, not ceremony.

1. **Bake the shared listen/system head** under the 512 cap (or raise cap). Until that page exists, skip-prefill stays **~17 ms–0.27 s**, not **1.4–2.6 s**.
2. **Time it.** TTFT A/B: novel prompt vs vault-hit prompt, same `max_tokens`, cold GPU cache. We have rates, not a dedicated TTFT pair after the 512 cap.
3. **Decide the cap.** 512 ≈ 0.27 s and ~252 MiB/prefix. 2048 ≈ 1.1 s / ~1 GiB. 4096 ≈ 2.1 s. One shared prefix, not 191.
4. **Residuals.** Dump dir was tens of GiB (`/residuals`). Vault prune did **not** touch dumps. Retention or they fill the disk again.
5. **Public door.** `governor.influx.vision` still 000. Either fix the worker or stop pointing people at it.
6. **VPS → Governor.** Chanoyu/Koyomi on `88.216.68.146` cannot see `:8800`. Only if you want him on folio (explicit path, not a sneaky reverse-proxy of the whole rig).
7. **RADX-KVX vs this path.** 45 `.shard`, 1 `.kvx`. This inject is **per-prompt attention pages**, not splicing all shards into every decode. Don’t conflate.
8. **FLUX `main` vs `folio-tea-chrome`.** Chrome tests are on `origin/main` (`c353bbb`+). The 300-commit local history is a branch; don’t rebase it onto main.
9. **Don’t.** Second vLLM on GPU 1. SGD on serving Gemma. Steal GPU 0/3 or GPU 2. Commit residual `.safetensors` or hive forage locks.

## 11. Git / R2

| Repo | Tip | What |
|---|---|---|
| Council-OS | `1b63268`+ docs on `origin/main` | longest-prefix, 512 cap, prune, proofs, this diff |
| FLUX | `origin/main` | Tea chrome tests, inject receipt, proof md |
| gemstone | `01122d0` `origin/main` | `~/.gemstone` classified; kvx geometry 60/256 |

R2: `governor-training/kvx-inject/2026-09-05/`, `docs/kvx-inject-2026-09-05.md`, `docs/GOVERNOR_DIFF_2026-09-05.md`.

## Still not

- Public `governor.influx.vision`
- Governor on the VPS loopback
- 5k-token listen prefix as one vault page (cap is 512)
- All 45 `.shard` files as RADX-KVX into every decode
- Hive `.swarm` forage ticks not committed (locks / residual dumps)
- Measured TTFT delta after the 512 cap

## Verdict

Public Tea is styled. Folio HTML matches origin. His memory is on R2. Inject is **real and not silent-wrong**, vault is **~1200× smaller**. Time saved per turn is still **small** until the shared listen head is the thing in the vault. Next work is bake that head, time it, and stop the residual dump from eating the disk.
