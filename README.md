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
archives are never overwritten. Snapshot provenance publication uses
exclusive same-directory `.json.tmp` staging and no-replace hard-link
creation: an existing v3 JSON is reusable only with identical canonical
bytes. Different provenance, symlink destinations or occupied staging fail
without replacing earlier evidence; inspect interrupted staging before
cleanup. This requires a filesystem supporting hard links and does not
change raw/effective ZIP or stage-receipt publication rules. The companion
`.raw-report.json` identifies source URLs, hashes and unavailable assets,
but is **not** the application's processed-symbol build manifest. The new raw archive must not yet be substituted
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

The read-only audit stages are `infinity-symbol-preflight` and
`infinity-symbol-font-audit`.

The first stage is `infinity-symbol-preflight`. It verifies
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

After a passed structural preflight, `infinity-symbol-font-audit`
resolves used font references using the installed font index and the
reviewed alias configuration. It preserves InfinityDB's established
font-audit report fields and emits a second receipt pinned to both
preflight files, the alias configuration and hashes of the **actual
installed font files** used for resolution. No font binaries or
absolute font paths appear in either report.

```powershell
infinity-symbol-font-audit --effective-archive "EFFECTIVE-SYMBOLS example.zip" --effective-manifest "EFFECTIVE-SYMBOLS example.json" --effective-report "EFFECTIVE-SYMBOLS example.effective-report.json" --preflight-report data/reports/svg-preflight.json --preflight-receipt data/reports/svg-preflight-state.json --output data/reports/font-audit.json --receipt data/reports/font-audit-state.json
```

Unresolved or ambiguous effective font references produce a failed
receipt and a reproducible diagnostic report; later processing stages
must not consume that failed audit.

The next optional stage is `infinity-symbol-duplicates`. It consumes the
unchanged effective ZIP and a **passed, hash-pinned font-audit receipt**,
verifies every SVG member and classification, and runs the ported exact-
and visual-duplicate detector. It emits the existing three CSV reports,
a stable canonical URL/path mapping and a separate stage receipt that
pins each CSV SHA-256, renderer/version, render size and font-audit
evidence. It never modifies the source ZIP.

```powershell
infinity-symbol-duplicates --effective-archive "EFFECTIVE-SYMBOLS example.zip" --effective-manifest "EFFECTIVE-SYMBOLS example.json" --effective-report "EFFECTIVE-SYMBOLS example.effective-report.json" --font-report data/reports/font-audit.json --font-receipt data/reports/font-audit-state.json --reports-directory data/reports/duplicates --receipt data/reports/duplicate-state.json --render-size 512 --renderer resvg --jobs 4
```

The renderer (default: `resvg`) and its runtime dependencies must
be installed separately. The exact-byte detector remains usable as
fallback evidence if rendering fails, but a nonzero visual-render
error count produces a **failed** receipt rather than a completed
deduplication claim. CSV summary timing can vary between runs; its
exact produced bytes are pinned in the receipt.

The next independent stage is `infinity-symbol-text-conversion`.
It requires the **passed duplicate-stage receipt and its three CSV
reports**, plus the pinned font audit and original effective ZIP.
It verifies the canonical mapping against the source hashes and CSV
rows, rechecks font aliases, font resolution and installed font-file
hashes, then runs the original text converter **only for canonical
text-bearing representatives**. Canonical SVGs with no active text
are retained unchanged; redundant source identities remain in the
mapping, not in the intermediate output ZIP.

```powershell
infinity-symbol-text-conversion --effective-archive "EFFECTIVE-SYMBOLS example.zip" --effective-manifest "EFFECTIVE-SYMBOLS example.json" --effective-report "EFFECTIVE-SYMBOLS example.effective-report.json" --font-report data/reports/font-audit.json --font-receipt data/reports/font-audit-state.json --duplicate-receipt data/reports/duplicate-state.json --duplicate-groups data/reports/duplicates/duplicate-groups.csv --duplicate-errors data/reports/duplicates/duplicate-render-errors.csv --duplicate-summary data/reports/duplicates/duplicate-summary.csv --output-archive data/intermediate/CANONICAL.zip --conversion-report data/reports/svg-text-to-path-report.csv --conversion-summary data/reports/text-conversion-summary.csv --receipt data/reports/text-conversion-state.json --converter inkscape-shell --jobs 2
```

The result is a deterministic intermediate canonical ZIP (not a
publication artifact), the existing two conversion CSVs, and a stage
receipt pinned to the source, font evidence, duplicate evidence,
converter/version and per-member output SHA-256. Conversion errors
or remaining active text yield a failed receipt and diagnostics but
**no output ZIP**. No previous report or artifact is overwritten.
Final visual fidelity and compression remain pending.

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

## Canonical render-validated compression

`infinity-symbol-canonical-compression` accepts the canonical-only
text-conversion ZIP and its passed, SHA-256-pinned receipt. It verifies
every ZIP member and source hash, rejects remaining active SVG text,
then invokes the existing display-aware compressor using a **balanced**
(default) or **small** raster-validated profile. Both resvg and
Inkscape are supported as explicitly selected renderers. The selected
renderer and SVGO versions, target sizes, DPRs and tolerance settings
are recorded in a new `compression` stage receipt.

```powershell
infinity-symbol-canonical-compression --canonical-archive data/intermediate/CANONICAL.zip --conversion-receipt data/reports/text-conversion-state.json --output-archive data/intermediate/COMPRESSED.zip --receipt data/reports/compression-state.json --report data/reports/compression-report.csv --candidates-report data/reports/compression-candidates.csv --run-report data/reports/compression-run.json --profile balanced --renderer resvg --jobs 2
```

The compressed ZIP retains the canonical relative paths, with a
per-member SHA-256 mapping to the text-converted source. All three
original compressor reports are pinned. Missing output members,
unresolved render/SVGO errors, missing tool versions, or a mismatch
in the input receipt abort the stage without publishing an output.
The original canonical ZIP is not modified. This remains an
**intermediate**, not an accepted browser publication artifact:
production rendering parity and an InfinityDB-side final verifier
are still required.

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
