# Tea

Tea is the public-facing living image garden in FLUX. Its landing page,
gallery, motion work, exhibitions, and authored media live together in
`apps/tea/public`.

The app deliberately shares the root FLUX Go server and resident inference
worker. That keeps live assets, thumbnails, WebSocket events, and the H100
queue on one origin without duplicating model or GPU lifecycle code.

## Setup and development

Build the repository-local CLI once from the repository root:

```sh
make go-build
```

Then use the Tea suite:

```sh
./flux tea check
./flux tea setup
./flux tea dev --open
```

`tea check` only validates the app bundle. `tea setup` installs the shared
FLUX Python runtime and then validates Tea. `tea dev` starts the local Go
server at `http://127.0.0.1:7861/`; it does not load the model until a render
or warm request asks for it.

To bind a private H100 listener with authentication:

```sh
FLUX_HTTP_TOKEN='...' ./flux tea serve --addr 0.0.0.0:7861
```

To expose only the presentation and safe read APIs:

```sh
./flux tea serve --addr 0.0.0.0:7861 \
  --public-read-only --unsafe-no-auth
```

The unauthenticated example is appropriate only behind the existing trusted
gateway. The read-only gate blocks render, warm, cancel, and other GPU-mutating
requests.

## Public routes

| Route | Source |
| --- | --- |
| `/` | `public/index.html` |
| `/gallery/`, `/portraits` | `public/gallery.html` |
| `/movement` | `public/movement.html` |
| `/studies` | study library plus generated-results gallery and compact history API |
| `/studies/stallion` | runnable Stallion motion-graph laboratory |
| `/exhibition` | `public/exhibition.html` |
| `/exhibition/stallion` | `public/stallion.html` |
| `/sentinel` | `public/sentinel.html` |

Authored videos and images are in `public/assets`. Generated work remains in
the configured FLUX output directory and is reached through `/outputs/` and
the existing API/event routes.

## Stallion motion protocol

The lab uses [`protocols/stallion-motion-v1.json`](protocols/stallion-motion-v1.json)
as its auditable contract and [`../../scripts/stallion_motion_graph.py`](../../scripts/stallion_motion_graph.py)
as the runner. It separates identity, pose, background, palette, composition,
and quality; clusters coherent shot families; estimates gait phase; builds a
gated transition graph; and searches whole paths with acceleration, reversal,
revisit, and loop-seam penalties. Lower ranked coherence scores are better.

The interface starts and stops asynchronous batches through
`/api/studies/stallion-motion`, polls structured progress, and presents ranked
MP4 cards plus the complete scored manifest. A run can explore 1–12 rounds of
three path types: spectral loops, continuity paths, and kinetic paths. Its
continuous mode removes the round ceiling and checkpoints after every round;
it reuses one feature graph, pauses briefly between rounds, retains only each
film's poster after encoding, and stops before free disk falls below 2 GiB.

The checked-in 96×79 atlas grid is used as a 7,584-cell proxy corpus today.
The runner also accepts a directory of `cell_*.png` originals; feature analysis
stays at 32 px while final frames are decoded from the source files at delivery
time, so connecting the recovered originals does not require a protocol or UI
rewrite.

The recovered Stallion lineage, measured H100 CPU budget, and safe next steps
are recorded in [`STALLION.md`](STALLION.md).
