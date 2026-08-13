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

The lab uses [`protocols/stallion-motion-v2.json`](protocols/stallion-motion-v2.json)
as its auditable contract. The CPU runner proposes paths only through declared
row-serpentine atlas adjacency. The H100 reviewer segments the horse, measures
foreground, background, and camera flow separately, rejects symmetry and
static-object substitutions, and publishes only candidates whose every edge
passes. Lower object-rubric scores are better.

The interface starts and stops asynchronous batches through
`/api/studies/stallion-motion`, polls structured progress, and presents ranked
MP4 cards plus the complete scored manifest. A run can explore 1–12 rounds of
three path types: spectral loops, continuity paths, and kinetic paths. Its
continuous mode removes the round ceiling and checkpoints after every round;
it reuses one feature graph, pauses briefly between rounds, retains only each
film's poster after encoding, and stops before free disk falls below 2 GiB.

The checked-in 96×79 atlas grid is an index image and can never be a runtime
source. Set `TEA_STALLION_CELL_DIR` to a uniform directory of native
`cell_*.png` files. Publication is a literal native-PNG stitch: no resizing,
interpolation, sharpening, labels, or synthesized in-between frames.

The recovered Stallion lineage, measured H100 CPU budget, and safe next steps
are recorded in [`STALLION.md`](STALLION.md).
