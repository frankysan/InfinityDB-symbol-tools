# Agent improvement log

This is the single collection point for actionable improvements to code,
tests, tooling, workflows and agent instructions in InfinityDB-symbol-tools,
including findings outside an agent's current assignment.
Keep it limited to actionable, unresolved items; it is not a completion archive.

An entry records a suggestion for later review. It does not authorize
implementation, expand task scope, replace reviewed policy or provenance,
or approve a cross-repository change or publication.

## Required agent behavior

1. During implementation, investigation, testing, debugging, documentation
   review or handoff, record specific improvement opportunities supported
   by concrete observations. Include useful findings outside the current
   task without starting unrelated work. Distinguish observations from
   hypotheses; do not invent evidence or measurements.
2. Check existing entries first. Update a matching entry rather than
   duplicating it. Allocate each new entry from the next entry ID below,
   increment that counter, and keep the allocated ID stable. Never reuse
   a retired ID.
3. Include the observation date, affected files or instructions, supporting
   evidence, proposed improvement, expected benefit and status. Use the
   format below. Keep entries free of secrets, private source material
   and private operator evidence; use safe repository-relative references.
4. Continue the assigned work. Implement a suggestion only when it is
   already within the authorized scope and follows the normal validation
   rules. Otherwise leave it `proposed` or `deferred` for review.
5. Update the status and disposition while work remains unresolved. Keep
   accepted or partially implemented items until implementation and required
   validation are complete; do not treat deferred checks as completion.
6. Once implementation and required validation are complete, preserve any
   durable guidance in its owning documentation and remove the entry in
   the same change. Remove declined or superseded entries once their
   decision is recorded in the change or relevant documentation. Use Git
   history for past entries; do not retain completed sections, tombstones
   or a separate archive. Mention relevant additions and removals in the
   final handoff without presenting proposals as completed work.

If this log cannot be written, include the exact proposed entry and the
reason in the handoff so a later authorized agent can record it. Do not
modify another repository merely to capture an idea.

## Entry format

Use one improvement per entry:

```markdown
### AI-#### — Short actionable title

- **Status:** proposed
- **Area:** code / tests / tooling / workflow / instructions
- **Observed:** YYYY-MM-DD; affected files or instructions and supporting
  observations, commands or reproduction steps. Label hypotheses explicitly.
- **Proposed improvement:** Concrete change, including relevant trade-offs
  and compatibility checks.
- **Expected benefit:** Why the change would help; label unmeasured benefits
  as expected rather than established results.
- **Disposition:** Next review step, reason deferred, or linked task,
  commit or recorded decision.
```

Allowed retained statuses are `proposed`, `deferred` and `accepted`.
Acceptance records a decision to schedule work; implementation and validation
must still follow the authorized task scope. Completed, declined and
superseded items are removed under the retirement rule above.

## Entries

Next entry ID: `AI-0002`.

### AI-0001 — Enable generic acquisition and test execution through MCP

- **Status:** proposed
- **Area:** tooling
- **Observed:** 2026-11-25; raw acquisition requires the installed CLI, but
  the script runner accepts only tracked `tools.*` modules and the existing
  production runners are pinned to older inputs. Both advertised pytest job
  routes rejected this repository with `validation_unknown` during progress
  reporting verification.
- **Proposed improvement:** Provide a parameterized tracked raw acquisition
  runner and verify an allowlisted offline pytest profile for this repository.
  Preserve pinned inputs, explicit network opt-in and immutable outputs.
- **Expected benefit:** Repeatable agent-assisted acquisition and verification
  without requiring manual CLI execution.
- **Disposition:** Review runner and MCP integration separately; do not bypass
  repository or execution restrictions.
