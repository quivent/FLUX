# Beauty studies: bring-up on a fresh box

This is the study system as it runs on the H200: the study loop, its three jurors, the Beauty site behind Caddy, and the R2 backup. It also covers publishing to influx.pictures. Do the steps in order.

Paths are the box's: user `ubuntu`, FLUX at `/home/ubuntu/FLUX`, study scripts in `/home/ubuntu/tracker/`, models in `/home/ubuntu/models/`.

Prerequisites: the FLUX worker renders and writes `~/FLUX/.fluxd/jobs.jsonl`, since the loop is driven by that file. Also required: `inotify-tools`, the AWS CLI at `/home/ubuntu/.local/bin/aws`, Docker with the NVIDIA runtime, and Caddy at `/usr/local/bin/caddy`.

## 0. Code

```sh
cd /home/ubuntu/FLUX && git pull && go build -o flux ./cmd/flux     # the `flux` CLI
mkdir -p /home/ubuntu/tracker
cp apps/beauty/study/*.py apps/beauty/study/*.sh /home/ubuntu/tracker/
```

`beauty_jury.py` imports `jury_evaluator` and `sensory_gates` from `~/FLUX`. `beauty_diff.py` loads DINOv2 (`/home/ubuntu/models/facebook/dinov2-giant`) and SigLIP (`/home/ubuntu/models/google/siglip-base-patch16-224`).

## 1. Services and systemd units

| unit | runs as | port | ExecStart |
|---|---|---|---|
| `beauty-prompt.service` | ubuntu | 127.0.0.1:8096 | `/usr/bin/python3 /home/ubuntu/tracker/beauty_prompt.py` |
| `beauty-jury.service` | ubuntu | — | `/home/ubuntu/FLUX/.venv/bin/python /home/ubuntu/tracker/beauty_jury.py` |
| Beauty site | ubuntu | 127.0.0.1:7863 | `/home/ubuntu/FLUX/flux beauty serve --addr 127.0.0.1:7863` |
| `sessions-sync.service` | root | — | `/usr/bin/python3 /home/ubuntu/tracker/sessions_sync.py` |

```ini
# /etc/systemd/system/beauty-prompt.service
[Unit]
Description=Beauty study API (prompt, collection, config, marks, scores, jurors)
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tracker
ExecStart=/usr/bin/python3 /home/ubuntu/tracker/beauty_prompt.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/beauty-jury.service
[Unit]
Description=Beauty study loop (event-driven on ~/FLUX/.fluxd/jobs.jsonl)
After=network.target beauty-prompt.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tracker
ExecStart=/home/ubuntu/FLUX/.venv/bin/python /home/ubuntu/tracker/beauty_jury.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/beauty-jury.service.d/endpoints.conf
[Service]
Environment=MOJ_VISUAL_WITNESS_URL=http://127.0.0.1:8006/v1
Environment=MOJ_VISUAL_WITNESS_MODEL=ornith-judge
Environment=MOJ_PIXTRAL_URL=http://127.0.0.1:8004/v1
Environment=MOJ_PIXTRAL_MODEL=pixtral-critic
Environment=MOJ_GOVERNOR_URL=http://127.0.0.1:8005/v1
Environment=MOJ_GOVERNOR_MODEL=governor
```

```ini
# /etc/systemd/system/sessions-sync.service
[Unit]
Description=Stream sessions, Beauty collections and influx.pictures frames to R2 (creds via FIFO only)
After=network-online.target

[Service]
User=root
ExecStart=/usr/bin/python3 /home/ubuntu/tracker/sessions_sync.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

The Beauty site server runs `flux beauty serve --addr 127.0.0.1:7863` under whatever unit the box already uses for it. It binds loopback, so it needs no token.

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now beauty-prompt sessions-sync
# beauty-jury after the jurors are up (section 2)
```

Roles in the loop:
- `beauty_jury.py` is the loop.
- `beauty_diff.py` is the neutral judge: Ornith on :8006, with DINOv2/SigLIP through `sensory_gates`.
- `beauty_vision.py` is the writer: Gemma on :8005, model `governor`.
- The loop reads its settings from `~/FLUX/.fluxd/study_config.json` on every frame. These are written through `/api/collection/config`.

## 2. The three juror containers

Image: `vllm/vllm-openai:v0.29.0-cu129`. That tag is the one in `tools/ornith-h200/jury_up.sh` (Cook). Confirm the tag in use on the box with `docker inspect -f '{{.Config.Image}}' beauty-governor-gemma` and use that one. Start one container at a time and wait for `/health` before starting the next. Concurrent vLLM starts have crashed.

```sh
IMG=vllm/vllm-openai:v0.29.0-cu129   # the tag in use on the box
```

Gemma writer, `:8005`:

```sh
docker run -d --name beauty-governor-gemma --restart unless-stopped --gpus all --ipc=host \
  -p 127.0.0.1:8005:8000 -v /home/ubuntu/models:/home/ubuntu/models:ro -e HF_HUB_OFFLINE=1 \
  $IMG \
  --model /home/ubuntu/models/gemma-4/12b-fp8/vocab65k-v3/target \
  --served-model-name governor gemma-4-12b \
  --gpu-memory-utilization 0.14 --kv-cache-memory 3758096384 \
  --max-model-len 16384 --max-num-seqs 4 --kv-cache-dtype fp8 \
  --limit-mm-per-prompt '{"image":1,"video":0,"audio":0}' \
  --trust-remote-code
until curl -fsS localhost:8005/health; do sleep 5; done
```

Ornith judge, `:8006`: English-v2 trained stock, FP8, served as `ornith-judge`, MTP 5, two images per prompt. Set `ORNITH_JUDGE` to that checkpoint's directory on the box. Take the memory flags from the running container (`docker inspect beauty-judge-ornith`). The two flags below marked "as on the box" are placeholders.

```sh
ORNITH_JUDGE=/home/ubuntu/models/<english-v2 trained-stock fp8 checkpoint>
docker run -d --name beauty-judge-ornith --restart unless-stopped --gpus all --ipc=host \
  -p 127.0.0.1:8006:8000 -v /home/ubuntu/models:/home/ubuntu/models:ro -e HF_HUB_OFFLINE=1 \
  $IMG \
  --model "$ORNITH_JUDGE" --served-model-name ornith-judge \
  --speculative-config '{"method":"mtp","num_speculative_tokens":5}' \
  --limit-mm-per-prompt '{"image":2,"video":0}' \
  --gpu-memory-utilization <as on the box> --max-model-len <as on the box> \
  --kv-cache-dtype fp8 --trust-remote-code
until curl -fsS localhost:8006/health; do sleep 5; done
```

Pixtral critic, `:8004`:

```sh
docker run -d --name arcane-vllm-pixtral --restart unless-stopped --gpus all --ipc=host \
  -p 127.0.0.1:8004:8000 -v /home/ubuntu/models:/home/ubuntu/models:ro -e HF_HUB_OFFLINE=1 \
  $IMG \
  --model /home/ubuntu/models/RedHatAI/pixtral-12b-quantized.w4a16 --served-model-name pixtral-critic \
  --gpu-memory-utilization 0.10 --kv-cache-memory 1610612736 --max-model-len 8192 \
  --max-num-seqs 4 --kv-cache-dtype fp8 --limit-mm-per-prompt '{"image":2,"video":0}' \
  --trust-remote-code
until curl -fsS localhost:8004/health; do sleep 5; done
```

Check that every seat answers, then start the loop:

```sh
curl -s 127.0.0.1:8096/api/jurors     # each seat live, checked at its port
sudo systemctl enable --now beauty-jury
```

## 3. Caddy site block

In `/etc/caddy/Caddyfile`:

```caddy
beauty.influx.vision {
	@study path /api/prompt* /api/judging* /api/collection* /api/jurors* /api/saved-prompt*
	handle @study {
		reverse_proxy 127.0.0.1:8096
	}
	handle /collection {
		root * /home/ubuntu/FLUX/apps/beauty/public
		rewrite * /collection.html
		file_server
	}
	handle /scoring {
		root * /home/ubuntu/FLUX/apps/beauty/public
		rewrite * /scoring.html
		file_server
	}
	handle {
		reverse_proxy 127.0.0.1:7863
	}
}
```

```sh
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
```

There is no login or token gate on the site.

## 4. Arming the sync

`sessions-sync` holds R2 credentials in memory only. After every start or restart it blocks on the FIFO `/run/sessions-sync/creds` until they arrive. Arm it from the Mac, in the Cook repo:

```sh
tools/ornith-h200/arm_sessions_sync.sh ubuntu@<ip>
```

Never write the credentials to a file on the box or put them on a command line. Status is in `~/tracker/sessions_sync.json` (armed, pending, per-source local vs R2 counts). Flushes run at most every 300 s. What gets synced:

| source | local | R2 |
|---|---|---|
| ornith-chat | `~/ornith-eval/live_concept_capture` | `s3://models/j-a-a-a-y/sessions/ornith-chat` |
| studio | `~/ornith-session` | `s3://models/j-a-a-a-y/sessions/studio` |
| lab | `~/ornith-lab` | `s3://models/j-a-a-a-y/sessions/lab` |
| beauty-collections | `~/Models/flux-output/collections` | `s3://models/j-a-a-a-y/beauty/collections` |
| influx-pictures | `~/Models/flux-output/influx-outputs` | `s3://governor/outputs` |

The sync is additive: nothing is deleted on R2.

## 5. Starting a study

The CLI talks to `beauty_prompt.py`. To reach it, set `--url` or `FLUX_BEAUTY_STUDY_URL`; the default is `http://127.0.0.1:8096`.

```sh
flux beauty study start --name forest-at-a-distance --subject "<the teacher's subject, exactly>" \
  --title "Nudity in nature" --album nudity-in-nature
flux beauty study status          # active study, cycle, stall, last change, best, frames
flux beauty study stop
```

`--album` makes the loop stage every new frame for influx.pictures. `start` passes `title` and `album` in the start call. If the service ignores them, the CLI writes them into `collections/<name>/collection.json` and into the active `~/FLUX/.fluxd/collection.json`. Restarting an existing name resumes it. A different subject under an existing name is refused (409).

Study settings, which the loop reads on every frame:

```sh
flux beauty study config                                   # show
flux beauty study config gain=0.5 min_change=0.12 judge_calls=2
flux beauty study config weights.beauty=3 weights.change=2
flux beauty study config weights='{"beauty":3,"original":2,"change":2,"anatomy and hands":1,"defects":1}'
flux beauty study config writer=on vision="<the teacher's vision>" size=768 steps=18
```

The keys are `weights`, `gain`, `min_change`, `bold_after`, `revision_after`, `reseed_after`, `judge_calls`, `size`, `steps`, `vision` and `writer`. Unset keys use the defaults in `beauty_jury.py` (`STUDY_DEFAULTS`).

A collection lives in `~/Models/flux-output/collections/<name>/` and holds `collection.json`, `history.jsonl`, `renders/`, `marks.json`, `scores.json` and `removed.json`.

## 6. Scoring

The teacher's four scores run from -20 to 20. A value of 0 clears the score.

```sh
flux beauty score --study forest-at-a-distance --cycle 42 --dim beauty     --value 12   # /api/collection/mark
flux beauty score --study forest-at-a-distance --cycle 42 --dim direction  --value 5    # /api/collection/score
flux beauty score --study forest-at-a-distance --cycle 42 --dim difference --value -3
flux beauty score --study forest-at-a-distance --cycle 42 --dim uniqueness --value 8
flux beauty remove --study forest-at-a-distance --cycle 42              # out of the collection, unstaged from influx.pictures
flux beauty remove --study forest-at-a-distance --cycle 42 --restore    # back in; `flux beauty publish` stages it again
```

A removed frame stays on disk. The same actions are available on the web at `beauty.influx.vision/scoring` and `/collection`.

Tools in `~/tracker`:
- `audit.py` checks the invariants.
- `replicate.py` re-judges accepted steps in both orders.
- `merge_emerald.py` merges collections by hardlinks.

## 7. Publishing to influx.pictures

On the box, stage the study's frames. Each frame is hardlinked into `~/Models/flux-output/influx-outputs` as `protocol-<album>-stream-<YYYYMMDD>-<HHMMSS>-<NNN>.png` (UTC time of the history entry, cycle mod 1000). The command skips removed frames and frames the loop already linked. It also records `album` (and `--title`) in the collection:

```sh
flux beauty publish --study forest-at-a-distance --album nudity-in-nature --dry-run
flux beauty publish --study forest-at-a-distance --album nudity-in-nature --title "Nudity in nature"
```

`sessions-sync` uploads the staged frames to `s3://governor/outputs` within 300 s.

On the Mac, in `~/gallery` (github.com/quivent/gallery, branch `master`, Vercel project `gallery`):

1. Declare the album in `lib/r2.mjs` `GALLERIES`: `{ id: "<album>", title: "<Title>", kind: "album", stream: "<album>" }`. Add `censor: true` only for the nudity album.
2. Build, with the R2 credentials in the environment only (`R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_ENDPOINT`):

   ```sh
   node tools/build.mjs
   ```
3. Deploy:

   ```sh
   vercel deploy --prod
   ```

## 8. The rules of the loop

1. **Strictly linear.** One image is rendered, judged and filed, then the next. No batches, and no parallel rendering or judging.
2. **The teacher's prompt is never altered.** The subject is sent and kept exactly as written. Nothing rewrites, prefills or rotates it.
3. **One controlled change on the base seed.** Each frame is the best frame so far with one change, rendered on the best frame's seed. Any difference therefore comes from that change.
4. **The neutral judge judges in both orders.** Ornith (`ornith-judge`) compares each candidate with the best in both positions, so the order in which the two frames are shown does not decide the result.
5. **Change is a gate.** A frame that does not differ from the best by at least `min_change` is not a step, however it scores.
6. **Direction means away from the line's start.** It is measured from the study's first frame.
7. **Momentum.** A change that gained is continued. After `bold_after` frames without a gain the writer makes a bolder move. After `revision_after` frames it revises. After `reseed_after` frames the best's craft is reseeded. Progression never stops.
8. **The teacher's four scores outrank the judge.** These are beauty, direction, difference and uniqueness. Where the teacher has scored a frame, that score decides, in either direction.
9. **Censor only the nudity album.** `censor: true` goes on `nudity-in-nature` in `GALLERIES` and on no other album.
