---
name: symbol-producer-handoff
description: "Domain-specific procedure for symbol processing and publication handoff in InfinityDB-symbol-tools; use for source, provenance and cross-repository contract changes."
---

# Symbol processing and publication handoff

## Use this workflow when

- Changing raw SVG acquisition, overrides, deduplication, font audit, text conversion or compression.
- Modifying a stage receipt or proposing an InfinityDB symbol pipeline cutover.

## Procedure

1. Read [development guidance](../../../docs/DEVELOPMENT.md) and the [README](../../../README.md); identify the exact stage, its source plan and all upstream/downstream receipt dependencies.
2. Pin Army ZIP basename/SHA-256, source-plan v1, override-lookup identities, source archive and relevant toolchain. Do not confuse raw archive member paths with reviewed override paths.
3. Preserve original source SVGs and distinguish upstream SHA, direct/propagated override identity, missing/404 outcomes and effective output SHA. Never invent semantic Unit/Peripheral mappings.
4. Make each stage fail closed on stale inputs, missing/unpassed receipts, ambiguous fonts, failed render/conversion or unsafe paths. Keep raw, effective, canonical and compressed outputs separate and immutable.
5. Run focused fixture tests and offline pytest. When real render fidelity matters, check the explicitly selected fonts, renderer/convertor/SVGO versions and representative SVG comparison separately.
6. Test any changed producer/consumer output contract on a pinned InfinityDB revision. Final browser path mapping and asset promotion happen **only** in the application repository after verified cutover; never treat an intermediate archive as published.
7. Record source/archive/receipt hashes, producer/consumer SHAs, actual renderer/tool versions, validation and unresolved requirements. Update the canonical guide/README and substantive changelog.

Do not implicitly acquire, overwrite private overrides, publish assets, or synchronize sibling repositories.
