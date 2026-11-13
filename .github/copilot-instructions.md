# GitHub Copilot instructions — symbol acquisition and processing

This is the VS Code entry point, not an independent policy. Read
[AGENTS.md](../AGENTS.md), then the Git-ignored `AGENTS.local.md` if it exists,
and [the development guide](../docs/DEVELOPMENT.md). For specialized questions,
follow the owner documents linked from that guide. Tracked policy wins over
private preferences; do not replicate the main InfinityDB repository's
inapplicable release, browser or worker procedures.

Prefer the currently authorized checkout. Before modifying files, verify its
branch, HEAD, status, and any unrelated in-progress changes. Windows-terminal
examples should use PowerShell, while Linux commands belong to their explicitly
authorized environment. Do not assume sibling checkouts, remote machines or
test workers share a Git revision. Never infer SSH authorization, hosts, secret
paths or permission to sync from these instructions.

Run offline focused checks first. Use live acquisition and external tools
only when deliberately selected, and never claim that a producer test alone
validates a changed InfinityDB consumer. Report exact revisions, artifact
identity, performed checks, remaining integration work and any blockers.
