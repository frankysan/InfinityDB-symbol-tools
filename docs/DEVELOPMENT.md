# Development and integration

This guide owns the standalone graphical-producer workflow. See
[README.md](../README.md) for actual stage CLI contracts and current
cutover status, [pyproject.toml](../pyproject.toml) for dependencies,
[GitHub Actions](../.github/workflows/tests.yml) for hosted tests, and
[CHANGELOG.md](../CHANGELOG.md) for meaningful operator outcomes.
[AGENTS.md](../AGENTS.md) is the agent entry point.

## Boundary between producer and application

InfinityDB selects and exports authoritative Army-bound raw-source URLs
and reviewed override lookup identities. This repository validates the
pinned source/lookup plans and processes **raw graphical bytes**. It owns
retrieval, reviewed overrides, font audit/text-to-path conversion, exact
and visual deduplication, and render-validated SVG compression. InfinityDB
owns semantic identity resolution, published browser paths, application
verification, final tracked symbol inventory and release packaging.

Do not infer Unit/Peripheral identity from filenames, perform application
publication here, or let an intermediate ZIP masquerade as a processed,
accepted symbol publication. The external producer is an incremental
cutover; the existing in-tree processing stage cannot be removed until
the separately documented cross-repo contract and real-source parity
checks are satisfied.

## Stage invariants

- A v1 raw source plan binds requests and sorted portable archive paths
  to an exact Army ZIP basename/SHA-256. Reject unexpected URL hosts,
  malformed paths, altered input ZIPs or ambiguous source requests.
- Raw acquisition is opt-in with `--acquire`; validation without that
  flag must not perform HTTP. Preserve original SVG bytes and report
  fetched versus 404-unavailable URLs separately. Failed HTTP, invalid
  SVGs and mismatched provenance do not publish a complete source ZIP.
- Reviewed override lookup paths are **not** raw ZIP member names.
  Direct override, identical-*upstream*-hash propagation, conflicts,
  unavailable originals and effective output hashes are distinct
  evidence. Never propagate from matching names or already-overridden
  bytes; never drop a conflict silently.
- Keep raw, effective, canonical text-converted and compressed ZIPs
  separate. Every downstream stage verifies its upstream archives,
  reports and passed SHA-256-pinned receipts; no stale/failed receipt
  may authorize a later stage.
- Use the explicitly selected installed fonts, font-alias configuration,
  renderer, text converter and SVGO versions. Report unresolved fonts,
  rendering errors and visual fidelity checks as failures; never assume
  portable output without checking renderer/toolchain differences.
- No stage edits the original Army snapshot, source ZIP, override input
  or InfinityDB checkout. New outputs should refuse overwriting earlier
  evidence. Record exact source/processed byte hashes and stage status.

## Development and acceptance

Python 3.11+; source validation is standard-library-only at runtime.
Graphical processing uses the optional Python dependencies and selected
external binaries. For the repository's broad offline suite, use:

```powershell
python -m pip install -e ".[test,processing]"
python -m pip check
python -m pytest -q
```

CI installs these extras and runs pytest on Ubuntu, Windows and macOS
with Python 3.11. Tests use simulated HTTP/fixtures and do not require
live Army data or a sibling checkout. The test matrix does **not**
establish that installed external Inkscape/resvg/SVGO versions pass a
complete production image-fidelity run; that is a separate, explicitly
pinned integration check.

When changing an external stage output, verify: plan/source/Army hash
parity; producer and consumer schema/version; correct negative cases;
required external tool versions; representative real SVG rendered
fidelity; and InfinityDB's importing verifier on the exact receiving
revision. Keep existing processed-result contracts available until a
reviewed cutover. Source-tool completion does not by itself authorize
runtime/browser publication.

## Files, documents and retirement

New ignored source/override originals and working outputs belong in
purpose-specific `local/data/symbols/`, `local/research/symbols/`,
`local/validation/symbols/`, `local/handoffs/symbols/` or
`local/work/symbols/`, with source SHA/run ID in the path. Existing
documented `data/` command examples and `local/image_overrides/`
are compatibility paths, **not** permission for a blind migration.
Do not commit upstream SVGs, downloaded fonts, raw Army data or
private override files. The MIT code license does not cover their
copyrights; any processed-asset publication requires the relevant
rights and the consumer's review.

Update README for CLI/stage changes, this guide for development
conventions, tests for stage guarantees, and changelog only for
meaningful operator changes. For a bounded audit, helper, conversion
experiment or report, identify its reason, input revision, consumers,
permanent findings and retirement trigger at creation. When fulfilled,
retain useful accepted provenance or the reusable code, and retire
obsolete wrappers/reports/tests together instead of an automatic
`archive/` copy. Protect ignored/unique inputs with verified backups
before any deletion.

Cross-repo delivery records source and Army ZIP SHA-256, plan/lookup
versions, every required stage receipt, toolchain, producer commit,
consumer commit and actual acceptance result. Never automatically
synchronize or modify another repository.
