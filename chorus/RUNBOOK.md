# Chorus runbook

Written on the assumption that this will stop working because of something not
listed here. Everything in this file is a failure already understood, and the
understood ones are not what kill a system — the CLIP truncation ran for hours
while confident fixes were shipped into a discarded tail, and the gate returned
524 for forty minutes while reporting nothing at all. Both were invisible
because nothing was watching for *that*.

So this is not a defence. It is a map for whoever attends next, including a
future session that remembers none of this.

## The fuses, with dates

These are the failures with known timers. Attend before they burn down, not
after.

| fuse | burns at | what happens | pre-empt with |
|---|---|---|---|
| flux-worker capability URL | **2026-08-09 22:03Z** | `tea.influx.vision` 404s while the node is perfectly healthy | mint a new endpoint, `./chorus/publish.sh` |
| governor-eyes capability URL | **2026-08-09 23:12Z** | the judge loses its fast engine and falls back to the governor, which 524s | re-expose port 8000, update `CHORUS_SECOND_ENGINE` |
| workspace spend cap | ~$325, was $269 at 22:00Z | node stops mid-run, nothing announces it | `gman billing`, raise or accept the stop |
| `gman` CLI token | ~1 hour, always | any CLI path fails; MCP is unaffected | `gman login`, or mint a service token |
| R2 credentials on the node | node stop | the stream stops silently, frames exist in one place again | restart `r2sync` with env |
| node idle grace | 15 min unused | node stops, endpoint dies, watchdog dies with it | land any command |

A capability URL dies **whenever the node stops**, not only at expiry. Node
stop is therefore three failures at once: endpoint, R2 credentials, watchdog.

## The supervision chain, and where it ends

```
loop ─ watched by ─ watchdog   (same node: dies with what it watches)
     ─ judged by  ─ sentinel   (same node)
     ─ steered by ─ hive       (same node)
                    ↑
       all three ─ watched by ─ cloud routine, hourly, public HTTP only
                                  ↑
                                YOU
```

The routine cannot reach the node's logs and cannot re-publish the domain. It
reports; it does not repair. And nothing supervises the routine itself, so the
chain terminates in a person by construction rather than by oversight. That is
the honest shape: **the last supervisor is always human**, and the way a human
supervises a watch is by expecting its report and noticing the silence.

If you want that silence to be loud, the routine has Gmail attached and can
mail a one-line status — the absence of an expected message is a better alarm
than any log.

## Diagnosing a failure that is not on this list

In order, because each step rules out everything above it:

1. **Is the wall serving?** `curl -o /dev/null -w '%{http_code}' https://tea.influx.vision/atelier/`
   Not 200 → the endpoint, not the art. Go to the fuse table.
2. **Is the node running?** `get_node('flux-worker')`. Stopped or queued explains
   everything downstream; nothing else is worth checking first.
3. **Are the services up?** `bash chorus/up.sh` prints them and restarts what
   is dead. A service reported DOWN that is actually running was started
   outside the script and has no pid file.
4. **Is it still making frames?** Compare the newest frame's mtime to now. A
   healthy server rendering nothing is the failure that looks most like health.
5. **Is the gate still looking?** `tail ~/models/flux-output/taste-log.jsonl`.
   Consecutive `judged: false` means no engine answered. This has happened for
   forty minutes without anyone noticing.
6. **Only now, look at the art.** `chorus/contact.py --recent 80` and open the
   sheet. Everything above this line is plumbing; only this step is the work.

## The one habit that mattered

Look at the output before believing any claim about it, including your own.
Six consecutive rounds of "this is better" shipped against a wall that was not
better, each argued from one or two frames chosen by recency. The contact sheet
exists so that looking costs one glance, and every collapse that mattered —
the satellites, the corridors, the interchangeable pomegranates — was visible
only across a sample and invisible in the frame in front of you.

## Last known good

- Language: the short-prompt rewrite, anchored by the operator at `68f2a0a`
  ("This is beautiful"), with scale disparity raised to 47% after law 14 broke
  on all sixteen frames of a sheet.
- Wall: ~1,500 frames, paged, orderable by the panel's picks.
- Streaming to R2 at `governor/chorus/{frames,state}`.
- Governor holds the standing judging mandate and the universal-failure
  principle in memory.
