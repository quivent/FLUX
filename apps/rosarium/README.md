# Rosarium

Rosarium is the recovered FLUX visual estate originally published at
`https://atelier.geijutsu.work/`. Its public museum, generated-image catalog,
motion works, production scripts, and preservation records now live together
under this one name. The original domain and historical names are retained only
where they establish provenance.

## Layout

- `public/` is the recovered static museum, with its presentation reconciled
  into one Rosarium visual system. It contains the live 7,219-item manifest,
  7,218 available gallery thumbnails, the hero image, 26 full-resolution
  Arcane Princess renders, and three motion GIFs. The legacy `gallery4d.html`
  route remains as a compatibility alias for the Rotunda. The live deployment
  also lists `princessa.jpg`, but that object is no longer present on the
  public origin or in the preserved R2 render prefix.
- `production/scripts/` contains the 22 original FLUX generation, gallery
  assembly, motion, and R2 preservation programs recovered from the final
  evacuation archive.
- `production/provenance/` contains the August 5, 2026 evacuation receipt,
  sovereign manifest, and Cherry architecture shard.
- `archive/` preserves the earlier Atelier Oceanica and Atelier Sovereign page
  builds. The local recovery tree may also contain the ignored source
  evacuation tarball.

The production scripts are historical source, preserved unchanged. Several
refer to their original Cherry paths such as `/home/ubuntu/FLUX`,
`/home/ubuntu/Models/flux-output`, and `/home/ubuntu/arcane-princess-studio`;
review and adapt those paths before running a production loop on another node.

## Local viewing

From the repository root:

```sh
python3 -m http.server 7862 --directory apps/rosarium/public
```

Then open `http://127.0.0.1:7862/`.

## Recovery provenance

The live source was recovered from the public Atelier Oceanica deployment.
Production code and receipts were recovered from Cloudflare R2:

```text
council_os/bloom-preservation-20260805/
council_os/bloom-preservation-20260805/full-evacuation-20260805.tar.gz
council_os/bloom-preservation-20260805/renders/
surface/renders/
```

The R2 render preservation is roughly 12.5 GB and contains the larger original
generation estate. It remains the canonical cold archive; the repository app
keeps the complete browseable thumbnail catalog and the curated full-resolution
works used directly by the public presentation.
