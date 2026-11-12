# Changelog

All notable user- or operator-relevant changes to InfinityDB symbol tools are
documented here. Each version begins with a concise **Player summary**, followed
by fuller capability, compatibility and upgrade details. Entries describe
meaningful outcomes rather than individual commits or internal refactors.

The standalone repository was created on **2026-11-11**. Changes made here since
the split are recorded under **0.1** (package version `0.1.0`). Earlier work in
the main InfinityDB repository is explicitly marked **Pre-split** below; its
dates describe that project's development history, not standalone releases.

## [0.1] - Unreleased

### Player summary

- No player-facing changes.

### Added

- **Acquisition:** Added independent validation of InfinityDB's pinned raw
  symbol request plans against the exact Army ZIP name and SHA-256. The default
  command validates offline; network retrieval requires an explicit `--acquire`.
  Approved HTTPS locations, portable SVG paths and deterministic request
  ordering are checked before acquisition.
- **Acquisition:** Added raw SVG acquisition into deterministic `SYMBOLS` ZIPs
  with source provenance and a separate report of fetched and HTTP-404-missing
  assets. Acquired SVG bytes remain unchanged. Invalid SVGs, unexpected
  redirects and other HTTP failures abort acquisition without publishing a new
  archive; existing archives are never overwritten.
- **Data processing:** Added a standalone reviewed-override stage that produces
  an immutable `EFFECTIVE-SYMBOLS` archive. Its report retains original upstream
  identities, direct and propagated overrides, unused overrides and unavailable
  sources, with hashes linking the result to the raw archive, lookup plan and
  Army input. Conflicting replacements for identical upstream artwork in the
  same category fail explicitly.
- **Data processing:** Added read-only structural preflight and installed-font
  audit commands for effective archives. Their receipts bind each result to
  the source bytes; font evidence also pins the reviewed alias configuration
  and actual resolved font-file hashes. Missing or ambiguous fonts produce a
  failed audit without bundling font files or exposing absolute font paths in
  the reports.
- **Data processing:** Added standalone exact-byte and visual duplicate
  detection after a passed font audit. The stage preserves all source identities
  in a canonical mapping and emits duplicate-group, render-error and summary
  CSVs. Its receipt pins the reports and renderer settings; visual-render
  failures cannot be reported as completed deduplication.
- **Data processing:** Added canonical-only text-to-path conversion backed by
  passed duplicate and font evidence. The stage rechecks installed font hashes,
  converts only text-bearing canonical representatives and retains text-free
  SVGs unchanged. Conversion errors or remaining active text produce diagnostics
  without publishing a canonical ZIP.
- **Data processing:** Added receipt-chained compression of the canonical ZIP
  with `balanced` and `small` raster-validated profiles and an explicitly
  selected resvg or Inkscape renderer. The output records per-member source and
  result hashes, tool versions, display sizes, device pixel ratios and fidelity
  tolerances. Missing outputs, failed processing or mismatched input evidence
  prevent publication of the compressed archive.
- **Project infrastructure:** Added production stage runners for the pinned
  local input layout, covering overrides, preflight/font audit, duplicate
  detection, conversion and compression. These retain the stage evidence and
  stop when prerequisite processing fails or required receipts are missing.

### Changed

- **Acquisition + Data processing:** Established an independently installable
  source-processing package with the inherited font-processing and compression
  commands available as `infinity-symbol-fonts` and `infinity-symbol-compress`.
  The new stage commands verify their upstream artifacts and write separate
  outputs and receipts, preserving raw inputs for later verification.
- **Acquisition + Data processing:** Separated SVG retrieval, reviewed override
  application and graphical processing from InfinityDB's application-specific
  source selection and publication. InfinityDB remains authoritative for URL
  meaning, browser mappings, compatibility aliases and release packaging.

### Upgrade notes

- **Project infrastructure:** Requires Python 3.11 or newer. Acquisition and
  read-only source-plan validation use the standard library; graphical
  processing requires the optional `processing` dependencies and the selected
  external tools, including Inkscape/resvg and SVGO as applicable. Install fonts
  separately; this repository distributes no third-party SVGs or font binaries.
- **Acquisition + Data processing:** Export source and override-lookup plans
  from InfinityDB and supply the same pinned Army ZIP throughout processing.
  Override lookup paths are distinct from raw archive member paths. Raw,
  effective, canonical and compressed archives represent different stages;
  preserve their matching reports and receipts for the consuming verifier.
- **Data processing:** Stage outputs require InfinityDB-side verification and
  publication before they become browser assets. This package does not provide
  the former `build_symbols --resume` orchestration or its application build
  manifest, and processing does not modify an InfinityDB checkout.

## Pre-split — InfinityDB history (2026-09-15 to 2026-10-04)

These changes were implemented in the main **InfinityDB** repository before
the standalone split. They record the origins of the processing behavior and
source-evidence safeguards carried into this project. They are not releases of
InfinityDB symbol tools. Application UI, semantic symbol assignments and
browser-publication changes remain in
[InfinityDB's changelog](https://github.com/frankysan/InfinityDB/blob/main/docs/CHANGELOG.md).

### Added

- **Data processing — Pre-split, 2026-09-15:** Introduced SVG font inspection,
  font-alias resolution, text-to-path conversion, duplicate detection and
  display-aware compression tools. Compression compares rendered candidates
  across display sizes and device pixel ratios before selecting smaller SVGs.
  [Original tools](https://github.com/frankysan/InfinityDB/commit/cb0ab892f35638e11b06bf8a5951042ee61b9de7).
- **Acquisition — Pre-split, 2026-09-18:** Added snapshot-pinned symbol
  acquisition and layered resolution using reviewed overrides, verified cached
  sources and network retrieval. Source reports retained acquisition methods
  and original Army provenance. This was the original in-tree workflow; the
  standalone raw acquisition command does not reuse upstream caches.
  [Layered source resolution](https://github.com/frankysan/InfinityDB/commit/3cc4dc4705623bc8eb41ffda3385d926b1de9b72).
- **Data processing — Pre-split, 2026-09-18:** Integrated structural preflight
  and installed-font auditing into the pinned workflow, so malformed SVGs and
  unresolved fonts could be diagnosed before later processing.
  [SVG preflight](https://github.com/frankysan/InfinityDB/commit/7f5d0a22),
  [font audit](https://github.com/frankysan/InfinityDB/commit/016a2029).
- **Data processing — Pre-split, 2026-09-18:** Integrated duplicate detection
  and size-savings reports, then restricted text conversion and compression to
  canonical representatives. Redundant source identities remained traceable
  without repeatedly processing identical artwork.
  [Deduplication](https://github.com/frankysan/InfinityDB/commit/82b6f1a8),
  [canonical conversion](https://github.com/frankysan/InfinityDB/commit/25f03074),
  [canonical compression](https://github.com/frankysan/InfinityDB/commit/f86226f7).

### Fixed

- **Data processing — Pre-split, 2026-09-19:** Removed broken local image
  references from temporary conversion copies so missing linked files did not
  block text-to-path processing. Valid local images, embedded data and remote
  references were retained, and source SVGs were preserved.
  [Conversion correction](https://github.com/frankysan/InfinityDB/commit/a15e3cafbfa3d6a281be24a4f9a6b3f70cff4df2).
- **Data processing — Pre-split, 2026-09-19:** Made long-running compression
  progress visible while processing was still underway, improving diagnosis of
  apparently stalled production runs.
  [Streaming progress](https://github.com/frankysan/InfinityDB/commit/31fd6957).
- **Data processing — Pre-split, 2026-09-25:** Made generated compressor
  configuration and run-report line endings deterministic across platforms,
  reducing avoidable byte differences between equivalent runs.
  [Deterministic outputs](https://github.com/frankysan/InfinityDB/commit/53d2edfdfb66c314532b62b17ed35e60b81923ca).
- **Acquisition — Pre-split, 2026-10-04:** Propagated reviewed replacements
  across same-category URLs with identical original upstream hashes, retained
  upstream identity separately from replacement bytes and rejected conflicting
  overrides. This corrected inconsistent replacement coverage without assuming
  that matching names or already-overridden bytes established source identity.
  [Override propagation and provenance](https://github.com/frankysan/InfinityDB/commit/c09ec43dc4c0289b751f120a045f1d04d4e85a94).
