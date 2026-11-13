# Agent instructions — symbol acquisition and processing

These tracked instructions apply to coding and research agents in this repository.
Read [development guidance](docs/DEVELOPMENT.md) and the relevant parts of
[README](README.md) before substantial changes. Consult the owner's source,
tests, workflow and domain documentation rather than importing InfinityDB
procedures wholesale.

If a Git-ignored `AGENTS.local.md` exists, read it after this file. It is
for user, machine and tooling preferences only; it cannot override tracked
project architecture, semantic contracts or test requirements. Never commit
private host paths, credentials or local preferences.

## Scope and authority

- Own validated raw symbol retrieval, reviewed SVG overrides, font inspection/conversion, duplicate detection, graphical compression, and stage-level provenance.
- InfinityDB owns URL/source-plan meaning, semantic identity mappings, browser paths, application-side verification, and final publication.
- Intermediate stage artifacts are not accepted browser assets; do not claim or perform an integrated cutover from the in-tree pipeline before its separate gates are satisfied.

Follow this repository's own current contracts first. The main
[InfinityDB architecture](https://github.com/frankysan/InfinityDB/blob/main/docs/architecture.md),
[data lifecycle](https://github.com/frankysan/InfinityDB/blob/main/data/README.md)
and [project domains](https://github.com/frankysan/InfinityDB/blob/main/docs/project-domains.md)
describe the downstream application's boundaries, not extra local build or
release commands. When changing a handoff, inspect the exact consuming
InfinityDB revision and verify both sides; do not treat a remote branch name
as a pinned contract.

## Change workflow

- Inspect the authorized checkout's branch, HEAD, Git status, relevant files
  and any active concurrent work first. Preserve unrelated staged, unstaged,
  untracked and ignored files. Do not switch branches or rewrite history implicitly.
- Identify source identity, source revision, intended artifact and consumer
  before changing any interface. Preserve original inputs, exact references,
  provenance, ordering and unresolved evidence; fail closed on ambiguity.
- Keep normal builds/tests offline and deterministic. Live acquisition,
  private-source transfer, processing with external tools, and publication
  are separately selected actions; a test must not trigger them.
- Put maintained mappings/decisions in reviewed data or configuration and
  generated run receipts with the artifacts they describe. Never use a
  provenance report as a silent replacement for reviewed policy.
- For new ignored artifacts use categorized `local/` paths with domain,
  purpose, scope and lifetime. Keep existing documented CLI paths until
  consumers and migration are reviewed. Never bulk-delete, relocate or
  overwrite private sources because they are ignored or look obsolete.
- Apply the repository-specific [checks and documentation lifecycle](docs/DEVELOPMENT.md)
  to each coherent change. Design and run focused checks during implementation.
  Record deferred checks only for a concrete blocker, with exact inputs,
  revision and a resumption trigger; never mark a skipped/unrun check passed.
- Review the diff for accidental publication of copyrighted assets, source
  downloads, secrets, and private operator evidence. Respect third-party
  rights separately from the MIT source-code license.

## Cross-repository and Git safety

- Keep producer and consumer checkouts independent. An authorized change here
  does not authorize editing, staging, committing, pushing, or pulling another
  repository or a remote worker.
- For a changed cross-repository interface, specify the format/version, exact
  artifact and source hashes, compatibility impact and downstream verifier.
  Preserve currently accepted behavior until a separately validated cutover.
- With direct authorized access, edit and validate the actual working tree.
  Stage only reviewed task-related changes; use a lowercase conventional commit
  message and safe fast-forward publication only where authorized. Do not force
  push, reset, rebase, deploy or publish a release without separate permission.
  When only an archive is available, generate and check a Git-native patch
  against its real base instead of pretending the checkout was edited.
- Report files changed, the actual branch/SHA, tests run and outcomes, and
  specific deferred or blocked downstream verification. CI success, Linux
  worker success and application release acceptance are separate claims.

For specialized source and handoff work, use the
[repository-specific agent skill](.agents/skills/symbol-producer-handoff/SKILL.md).

These instructions do not activate InfinityDB's optional multi-agent
coordination system. That protocol applies only when explicitly requested
for a defined scope; concurrent work still requires isolated edits and safe
Git operations.
