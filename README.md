# InfinityDB symbol tools

Source-side tooling for Infinity SVG evidence. This repository is intended to
own **raw acquisition**, not application-specific symbol interpretation or
publication. It contains no third-party symbol images, logos, fonts, or raw
Army data. The MIT code license does not grant any rights over downloaded
assets.

## Current scope

The initial version contains a **read-only validator** for the
`InfinityDB raw symbol acquisition requests` v1 format. It does **not**
download symbols, build an asset ZIP, or modify InfinityDB.

The application repository remains authoritative for:
- Which Army, legacy and static declarations contribute symbol URLs.
- Category and archive path selection, alias handling and URL collisions.
- Reviewed overrides, canonical SVG transformations, deduplication, fonts,
  browser mapping, publication and release packaging.

This source repository may eventually consume the validated request set to
retrieve raw SVG bytes and produce an immutable `SYMBOLS ...zip` with
version-3 provenance bound to its input Army ZIP. That cutover is **not**
implemented or approved yet; the working InfinityDB symbol pipeline is
unchanged.

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
unique archive paths and portable relative `.svg` destinations. It performs
no HTTP requests.

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

All tests run offline. GitHub Actions checks the installed package on
Windows, Ubuntu and macOS. No other repository is needed to install or test
this validator.

## Cutover prerequisites

Before replacing raw acquisition in InfinityDB: retain missing-asset
handling, cached original SVG identities, override propagation, ambiguous
source-name diagnostics and source pinning; implement a verifiable offline
result manifest; validate byte-level archive and v3 provenance parity and
the unchanged symbol publication workflow. No normal build, serving or
Docker command may acquire new assets or depend on this repository.
