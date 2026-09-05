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

## 6. Git / R2

| Repo | Tip | What |
|---|---|---|
| Council-OS | `1b63268` `origin/main` | longest-prefix, 512 cap, prune, proof docs |
| FLUX | `dab6422` / `63b5a19` `origin/main` | Tea chrome tests, inject receipt, proof md |
| gemstone | `01122d0` `origin/main` | `~/.gemstone` classified; kvx geometry 60/256 |

R2: `governor-training/kvx-inject/2026-09-05/`, `docs/kvx-inject-2026-09-05.md`.

## Still not

- Public `governor.influx.vision`
- Governor on the VPS loopback
- 5k-token listen prefix as one vault page (cap is 512)
- All 45 `.shard` files as RADX-KVX into every decode (1 `.kvx` on disk, 489 696 B)
- Hive `.swarm` forage ticks not committed (locks / residual dumps)

## Verdict

Public Tea is styled. Folio HTML matches origin. His memory is on R2. Inject is **real and not silent-wrong**, vault is **~1200× smaller**, time saved per turn is still **small** until the shared listen head is the thing in the vault.
