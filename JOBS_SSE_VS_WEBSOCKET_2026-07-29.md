# `/api/jobs/events` (SSE) vs `/api/jobs/ws` (WebSocket) — measured, 2026-07-29

Scope: the job-progress feed that `motion-atlas` uses to show a multi-hour
render's live state. Both endpoints now exist side by side in
`internal/server/server.go`, subscribing to the same `motionJobsHub` — same
poller, same per-client output-URL logic, same broadcast fan-out. The only
difference is the wire protocol. This documents what that difference
actually costs, measured against the real `flux` binary, not estimated.

## TL;DR

**Fan-out latency is identical.** Handshake cost and bytes-on-wire favor
WebSocket by a real but small margin. The performance work that mattered —
replacing N per-connection subprocesses with one shared hub — was already
done and is why both protocols perform the same here. WS was still the
right call, but not because it's "faster" in the way that matters most for
a multi-hour job: it's leaner per message and it's the only one of the two
that can carry a client→server message (job control) on the same
connection later, which SSE structurally cannot do.

**It is not a free swap.** WebSockets are exempt from CORS, so the first
version of this migration silently turned a cross-origin-blocked endpoint
into a cross-origin-readable one. That had to be closed by hand — see
"The one real downside" below. Anyone repeating this migration elsewhere
should treat that as the default outcome, not an edge case.

## Method

`jobs_ws_vs_sse_bench.py` (kept in the session scratchpad, not committed —
this is a one-off measurement, not a regression suite):

1. Opens N real SSE connections to `/api/jobs/events` and N real WebSocket
   connections to `/api/jobs/ws` concurrently against a live `flux serve`
   process (2N total sockets).
2. Waits for every client's handshake **and** its initial snapshot (the
   hub's cached `latest`, sent immediately on subscribe).
3. Fires one real trigger: a temp-file-then-rename onto
   `.fluxd/jobs.jsonl` — the same atomic-write pattern actual job writes
   use. This is what the server's kqueue directory watch
   (`internal/server/watch_darwin.go`) actually reacts to; a plain
   in-place write does **not** fire it, since the watch is on directory
   entries, not file content. (First draft of this benchmark hung
   indefinitely on exactly this — worth stating since it's the kind of
   mistake that produces a confident, wrong number if it goes unnoticed.)
4. Every client then waits for the resulting broadcast and records
   arrival time and exact on-wire byte count for that one message.

Both groups wake from the *same* trigger in the *same* process, so this
isolates protocol overhead from everything else (worker IPC cost, hub
polling cadence, run-to-run OS scheduling noise).

## Results (localhost, `flux serve`, worker daemon not running)

| N (per protocol) | metric | SSE | WS |
|---|---|---:|---:|
| 50  | handshake mean (ms) | 4.64 | 3.53 |
| 50  | fan-out latency mean (ms) | 1.73 | 1.73 |
| 200 | handshake mean (ms) | 21.90 | 16.80 |
| 200 | fan-out latency mean (ms) | 3.20 | 3.23 |
| 800 | handshake mean (ms) | 101.14 | 73.69 |
| 800 | fan-out latency mean (ms) | 18.19 | 18.24 |
| — | bytes-on-wire per update | 191 | 169 |

Raw wire bytes for one update (same payload, both protocols):

```
SSE: b9\r\nevent: jobs\ndata: {"jobs":[],"model_downloaded":false,"ok":true,
     "worker_error":"dial unix .../flux.sock: connect: no such file or
     directory","worker_running":false}\n\n\r\n
WS:  \x81~{"jobs":[],"model_downloaded":false,"ok":true,"worker_error":
     "dial unix .../flux.sock: connect: no such file or directory",
     "worker_running":false}
```

## Interpretation

**Fan-out latency: a wash, by design.** At every concurrency tier tested,
mean/p50/p95/max fan-out latency between SSE and WS sit within noise of
each other (e.g. 800 clients: 18.19ms vs 18.24ms mean). This is expected —
both ride the identical in-memory hub broadcast and one goroutine per
client. It confirms the actual performance lever was the earlier
shared-hub rewrite (one poller instead of N redundant `nvidia-smi`/inotify
watchers), not the wire protocol. Swapping SSE for WS on top of an already
correct broadcast architecture doesn't buy latency — there's nothing left
to win there.

**Bytes-on-wire: WS is smaller by a mostly-fixed amount, not a percentage.**
SSE's 22-byte overhead here is the `event: jobs\ndata: ` prefix + trailing
`\n\n`, plus HTTP chunked-transfer-encoding's own framing (hex length line
+ CRLFs). WS's frame header is 2–4 bytes. That ~20-byte gap is roughly
**constant regardless of payload size** — it doesn't scale with job-list
length. Which means the percentage savings is *largest for frequent small
messages* (e.g. per-step progress ticks) and shrinks for large ones (e.g.
a snapshot listing dozens of jobs). For a job that ticks progress every few
hundred milliseconds for hours, that fixed-per-message overhead is what
actually compounds — cumulative megabytes over the life of the job, not a
one-time cost.

**Handshake cost: WS wins by ~25–30% at scale, and this is the number that
matters for "showing the process."** At 800 concurrent connections, mean
handshake time was 101ms (SSE) vs 74ms (WS). The gap is the extra HTTP
response header block SSE still carries (CORS headers, `Cache-Control`,
`X-Accel-Buffering`, `Content-Type`, etc.) versus WS's four fixed short
lines. This is the concrete case where it's not a wash: a **reconnect
storm** — Wi-Fi drops, laptop sleeps, several viewer tabs open on the same
multi-hour job all recover at once — resolves measurably faster over WS.
`EventSource` also auto-reconnects on a fixed ~3s browser-internal backoff
you can't tune; the WS client added here reconnects on its own 1s backoff
instead, trading "browser handles it for you" for "we control recovery
time," which is worth it for a job someone is actively watching.

**What this benchmark does *not* show, and shouldn't be read into:** this
ran on loopback. A real deployment to a remote pro6000 box adds RTT that
increases absolute numbers for both protocols roughly equally — it doesn't
change which protocol wins, since the difference measured here is wire
bytes and header-parsing cost, not network topology. It also ran without
a live worker, so every payload was the small "worker unreachable" snapshot
(169–191 bytes); a real render with many active jobs would carry a larger
JSON body, at which point the fixed SSE framing overhead becomes a smaller
fraction of each message — this is exactly the "small overhead, but it
compounds at high update frequency" case described above, not a large one.

## The one real downside, and what it cost to close

WebSocket is not a strict superset of SSE. It gives up one thing, and the
first version of this migration gave it up silently:

**Browsers do not apply CORS to the WebSocket handshake.** `EventSource` is
CORS-checked; `new WebSocket()` is not. So the moment a feed moved from SSE
to WS, an endpoint that browsers had been *blocking* cross-origin became
one they would happily open from any page on the internet. Measured against
the server before the fix:

```
WebSocket /api/jobs/ws
  Origin: https://evil-attacker-site.example  -> 101 Switching Protocols  (payload flows)

SSE /api/jobs/events   (what it replaced)
  Origin: https://evil-attacker-site.example  -> Access-Control-Allow-Origin: http://127.0.0.1
                                                 (browser blocks the read)
```

That is cross-site WebSocket hijacking. In the default local no-token
configuration, any site the user visited while the dashboard was running
could read their render jobs, prompts, and output paths off 127.0.0.1. A
bearer token does mitigate it (browsers cannot set custom headers on a WS
handshake, so a cross-origin attacker gets 401) -- but the documented
default for local use is no token, so the default was the exposed case.

Fixed by `originAllowed()` in `internal/server/websocket.go`, checked before
the hijack. The policy deliberately mirrors what `localOrigin()` already
permitted over SSE, so nothing that worked before stops working: absent
Origin (CLI/curl/python) allowed, localhost family allowed (a vite dev
server on another port keeps working), same-origin-as-Host allowed (a real
deployment behind a domain keeps working), everything else refused. Pinned
by `TestOriginAllowed` (11 cases, including `Origin: null` from a sandboxed
iframe and the `127.0.0.1.evil.example` lookalike-subdomain bypass) and
`TestUpgradeRefusesForeignOrigin`.

The honest framing: this is not a reason to prefer SSE. It is a reason to
know that "switch SSE to WS" is not a free swap -- it silently drops a
protection you were getting for free, and you have to put it back by hand.

## The actual reason to have done this

Not latency — that was already solved. The reason a push-only WebSocket is
still the right primitive here is that it's the only one of the two that
*can* carry something back from the browser over the same live connection
later (cancel/pause a job, adjust a running batch) without opening a
second request. `jobsWS` today is push-only, matching `jobsEvents`
byte-for-byte in behavior — that capability is unexploited, not yet built.
The measured win today is smaller messages and faster reconnect storms;
the reason it was worth building is what it makes possible next.
