# InfinityDB symbol tools

Source-side tooling for Infinity SVG evidence. This repository is intended to
own **raw acquisition**, not application-specific symbol interpretation or
publication. It contains no third-party symbol images, logos, fonts, or raw
Army data. The MIT code license does not grant any rights over downloaded
assets.

## Current scope

The package validates the `InfinityDB raw symbol acquisition requests`
v1 format without network access by default. An explicit `--acquire` run
fetches the specified **raw** upstream SVG bytes, retains the existing
deterministic `SYMBOLS ...zip` format and writes version-3 snapshot
provenance bound to the supplied Army ZIP. It also records fetched and
HTTP-404-unavailable URLs in a separate `.raw-report.json` report.
It does not modify InfinityDB.

The application repository remains authoritative for:
- Which Army, legacy and static declarations contribute symbol URLs.
- Category and archive path selection, alias handling and URL collisions.
- Reviewed overrides, canonical SVG transformations, deduplication, fonts,
  browser mapping, publication and release packaging.

The raw retrieval/provenance implementation is **not yet a pipeline cutover**.
It does not implement override propagation, reuse of verified upstream caches,
or the full `army-symbol-build.json` contract required by InfinityDB's
current symbol processing and resume stages. The in-tree pipeline remains
unchanged and operational.

## Source handoff

Run from an InfinityDB checkout, providing one existing Army ZIP with its
matching source provenance manifest:

```powershell
.\.venv\Scripts\python.exe -m tools.export_symbol_source_plan --snapshot "data/raw/JSON example.zip" --manifest "data/manifests/snapshots/JSON example.json" --output local/symbol-source-plan.json
```

Copy the read-only plan and the **same** Army ZIP to the source-tool
machine. Then, in this project's installed environment:

```powershell
infinity-symbol-source --plan symbol-source-plan.json --army "JSON example.zip"
```

This verifies the exact `inputArtifact` basename and byte SHA-256, schema,
lowercase hash, one approved HTTPS host/path, deterministic URL ordering,
unique archive paths and portable relative `.svg` destinations. Without
`--acquire`, it performs **no HTTP requests**.

To explicitly acquire and archive raw SVGs:

```powershell
infinity-symbol-source --plan symbol-source-plan.json --army "JSON example.zip" --acquire --destination data/raw/symbols --manifest-dir data/manifests/snapshots
```

Acquisition validates returned XML without altering the source bytes. Only
HTTP 404 is recorded as unavailable; other HTTP errors, invalid SVGs and
unexpected redirects fail without publishing a new ZIP/manifest. Existing
archives are never overwritten. The companion `.raw-report.json` identifies
source URLs, hashes and unavailable assets, but is **not** the application's
processed-symbol build manifest. The new raw archive must not yet be substituted
into an InfinityDB `build_symbols --resume` run without that build manifest.

A v1 plan contains `format`, `formatVersion`, `inputArtifact`
(`name`, `sha256`), `sourceDocumentCount` and sorted `requests`
(`url`, `archivePath`). URL meaning and request authority come from
InfinityDB's existing, reviewed source discovery; the validator does not
infer gameplay identities or invent aliases.

## Development

Python 3.11 or newer, standard library only at runtime.

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

All tests run offline using simulated HTTP responses. GitHub Actions checks
the installed package on Windows, Ubuntu and macOS. No other repository is
needed to install or test this acquisition stage.

## Cutover prerequisites

Before replacing raw acquisition in InfinityDB: retain cached original SVG
identities, reviewed override propagation, ambiguous source-name diagnostics
and full source pinning; connect the independent raw report to the maintained
`army-symbol-build.json` and processing checkpoints; verify end-to-end
parity using the current consumer. No normal build, serving or Docker
command may acquire new assets or depend on this repository.
