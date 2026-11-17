# Agent improvement log

This is the single collection point for actionable improvements to code,
tests, tooling, workflows and agent instructions in InfinityDB-symbol-tools,
including findings outside an agent's current assignment.

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
   duplicating it. Give each new entry the next unused `AI-####` ID and
   keep that ID stable.
3. Include the observation date, affected files or instructions, supporting
   evidence, proposed improvement, expected benefit and status. Use the
   format below. Keep entries free of secrets, private source material
   and private operator evidence; use safe repository-relative references.
4. Continue the assigned work. Implement a suggestion only when it is
   already within the authorized scope and follows the normal validation
   rules. Otherwise leave it `proposed` or `deferred` for review.
5. Update the status when a suggestion is accepted, implemented, declined
   or superseded. Link the relevant task, commit or recorded decision;
   retain the entry's history. Mention relevant new entries in the final
   handoff without presenting proposals as completed work.

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

Allowed statuses are `proposed`, `deferred`, `accepted`, `implemented`,
`declined` and `superseded`. Acceptance records a decision to schedule work;
implementation and validation must still follow the authorized task scope.

## Entries

No improvements recorded yet.
