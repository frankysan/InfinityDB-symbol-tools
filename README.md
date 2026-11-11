# InfinityDB symbol tools

Independent tooling for Infinity SVG acquisition, reviewed overrides,
font resolution/text conversion, duplicate detection and compression.
InfinityDB retains authoritative source selection and browser publication.
This repository contains no third-party SVGs, logos, font files or raw
Army data. The MIT code license does not grant rights over acquired assets.

## Current scope

The package validates the `InfinityDB raw symbol acquisition requests`
v1 format without network access by default. An explicit `--acquire` run
fetches the specified **raw** upstream SVG bytes, retains the existing
deterministic `SYMBOLS ...zip` format and writes version-3 snapshot
provenance bound to the supplied Army ZIP. It also records fetched and
HTTP-404-unavailable URLs in a separate `.raw-report.json` report.
It does not modify InfinityDB.

The application repository remains authoritative for which Army, legacy
and static declarations contribute URLs, source/reference identities,
browser-facing symbol mappings, static asset placement and release packaging.
The **symbol-tools** repository should own reviewed SVG override application,
upstream identity propagation, font substitution/text-to-path processing,
duplicate detection and rendering-validated compression. These operations
are distinct from application publication.

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

## SVG processing components (first extraction slice)

The original `svg_processor` and `svg_compress` implementations have
been copied here with their source-facing test fixtures. Reviewed SVG
font aliases are packaged as `font-aliases.json`; no font files are bundled.
Their existing CLI entrypoints are now `infinity-symbol-fonts` and
`infinity-symbol-compress`.

The standalone `override_processing.resolve_reviewed_overrides` engine
handles reviewed direct overrides, category-scoped propagation when
original upstream SHA-256 identities match, conflicts, and unused
overrides. It takes a separately reviewed URL-to-*override* lookup
path mapping; **override paths are not raw ZIP archive paths**.

The new `infinity-symbol-overrides` command accepts that pinned
lookup plan, the original Army ZIP and raw ZIP/provenance/report, and
locally reviewed override SVGs. It validates every raw source outcome
and produces an immutable `EFFECTIVE-SYMBOLS ...zip`, v3 provenance
and a version-1 `.effective-report.json` that records:

- Exact input plan/lookup/raw report, raw ZIP and Army hashes.
- Original upstream hashes (including missing raw sources), direct
  and propagated override identities and effective output hashes.
- Unused override paths, raw 404s and effective unavailability.
- `overrides: completed` with font audit, deduplication, text
  conversion and compression **all pending**.

To generate the lookup plan, use the InfinityDB checkout:

```powershell
.\.venv\Scripts\python.exe -m tools.export_symbol_override_lookups --snapshot "data/raw/JSON example.zip" --manifest "data/manifests/snapshots/JSON example.json" --source-plan local/symbol-source-plan.json --output local/symbol-override-lookups.json
```

Then process the raw source in this repository:

```powershell
infinity-symbol-overrides --source-plan symbol-source-plan.json --override-lookups symbol-override-lookups.json --army "JSON example.zip" --raw-archive "SYMBOLS example.zip" --raw-manifest "SYMBOLS example.json" --raw-report "SYMBOLS example.raw-report.json" --overrides local/image_overrides --destination data/effective --manifest-dir data/manifests/snapshots
```

These are **intermediate research/build artifacts**, not ready-to-publish
browser symbols. No font files or override images are redistributed.

The next independent stage is `infinity-symbol-preflight`. It verifies
the complete effective ZIP against its v3 provenance and intermediate
report, then produces `svg-preflight.json` with the same structural
parse/text/font-declaration fields and counters as InfinityDB's existing
SVG preflight. A separate stage receipt pins the report and effective
archive SHA-256; it records `passed` or `failed`. The stage reads but
does **not** modify the override artifact or mark font resolution,
deduplication, conversion or compression as complete.

```powershell
infinity-symbol-preflight --effective-archive "EFFECTIVE-SYMBOLS example.zip" --effective-manifest "EFFECTIVE-SYMBOLS example.json" --effective-report "EFFECTIVE-SYMBOLS example.effective-report.json" --output data/reports/svg-preflight.json --receipt data/reports/svg-preflight-state.json
```

Install optional Python processing dependencies separately:

```powershell
python -m pip install -e ".[test,processing]"
python -m pytest -q
```

Actual font conversion/compression additionally requires the selected
external tools, including Inkscape or resvg and (for compression) SVGO.
Their versions and rendering tolerances must be pinned in processing
provenance before output can replace an InfinityDB artifact. This first
slice does **not** yet relocate the `symbol_work` orchestration,
reviewed override files or their manifest lifecycle.

## Development

Python 3.11 or newer; acquisition and read-only validation use only the
standard library at runtime.

```powershell
python -m pip install -e ".[test]"
python -m pytest -q
```

All tests run offline using simulated HTTP responses. GitHub Actions checks
the installed package on Windows, Ubuntu and macOS. No other repository is
needed to install or test acquisition or the lightweight processing fixtures.

## Cutover prerequisites

Before retiring the in-tree pipeline, transfer reviewed override selection
and upstream hash propagation into a versioned processed-result manifest;
separate SVG font substitution from InfinityDB's **browser font packaging**;
migrate `symbol_work` stage orchestration and checkpoints; then compare
the new pipeline on representative source archives, overrides and rendering
tools. InfinityDB should verify/import the final processed artifact and
publish semantic browser paths without recompressing it. Normal builds,
serving and Docker must remain offline and independent of this repository.
