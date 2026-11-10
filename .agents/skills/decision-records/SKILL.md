---
name: decision-records
description: Create, review, and revise living architectural decision documents, or use an ADR only when maintained immutable history is required. Use when recording current design reasoning, comparing serious alternatives, defining reconsideration triggers, separating rationale from contracts or plans, or reviewing historical ADR lifecycle state.
---

# Decision Documentation

Default to one living document that explains the current approach and why it remains sensible.
Git preserves ordinary history, so do not create an immutable record merely because a decision is
important or expensive to reverse.

Read [`references/template.md`](references/template.md) before creating or revising a living
decision. Read [`references/adr-template.md`](references/adr-template.md) only when maintained
immutable history is required. Read
[`references/review-checklist.md`](references/review-checklist.md) before either adversarial pass.

## Select the operation

- **Create:** Develop a canonical living decision or, exceptionally, an ADR.
- **Review:** Test the current reasoning, alternatives, assumptions, and confirmation method.
- **Revise:** Update a living decision in place so it continues to describe today's position.
- **Split:** Move independently reversible decisions or normative details to their correct owners.
- **Preserve history:** Maintain accepted ADRs as historical records and link them to current
  guidance instead of treating them as present policy.

## Classify the artifact

| Artifact | Question |
|---|---|
| Living decision | What should we do now, why, and when should we reconsider? |
| ADR | Which historical commitment must remain explicitly immutable? |
| Contract or specification | What interface or behavior is normative? |
| Design document | How will the system satisfy the decision? |
| Implementation plan | In what order will we build it? |
| Test plan | How will we verify the behavior? |

Use an ADR only for an audit obligation, external commitment, compatibility promise, or significant
transition whose maintained historical record matters after current guidance changes. Importance,
cost, and reversibility alone do not require one.

Keep enough architecture in either decision format to explain the reasoning. Move normative
fields, commands, schemas, procedures, and exhaustive tests to linked contracts or specifications.

## Develop the decision

1. Inspect repository conventions, current decisions, historical ADRs, contracts, issues, and
   implementation evidence before asking questions.
2. State the current problem and forces without assuming an outcome.
3. Record the drivers that distinguish acceptable options.
4. Set one decision boundary. Split choices that can change independently.
5. Record only serious options and each option's strongest benefit and cost.
6. State the current approach and connect it directly to the drivers.
7. Record constraints, accepted costs, assumptions, and concrete reconsideration triggers.
8. Add a confirmation method that can detect drift and link the owning contracts and tests.
9. Update living documents in place without status, date, numbering, acceptance, or supersession.
10. For an exceptional ADR, default to `Proposed` and preserve accepted history through the
    repository's existing lifecycle.

Do not invent alternatives, evidence, or reconsideration triggers. Ask a focused question when
missing intent would materially change the decision.

## Run the adversarial pass

Treat consequential decision documentation as unfinished until this pass is complete:

- Present the strongest case for every rejected option.
- Identify the current approach's most consequential weakness.
- Challenge assumptions, reversibility, migration, enforcement, failure modes, and downstream
  consequences.
- Check whether normative contract material or independent decisions leaked into the document.
- Check whether the confirmation method can detect drift and the reconsideration triggers are
  observable.

When the decision is expensive to reverse or cross-cutting, and delegation is both available and
authorized, delegate one bounded, read-only challenger pass via `invoke_subagent` with
`TypeName="research"`. Give the reviewer raw context, options, and the draft without the intended
conclusion.

Revise the document and implementation plan from the findings. Disclose every material change
before requesting explicit acceptance. Never defer this pass to implementation.

## Review existing records

- Identify whether the artifact owns current guidance or maintained history.
- Separate missing rationale from excessive contract detail.
- Identify one-sided option analysis and missing selected-option costs.
- Flag historical ADRs that present themselves as current guidance and link them to the living
  source.
- Preserve accepted history instead of rewriting it to match the current template.

When current guidance changes, update its living document. Add or supersede an ADR only when the
historical transition itself meets the exceptional ADR threshold.

## Persist the result

Follow repository requirements and confirmed writing guidance. Change only authorized artifacts
and preserve unrelated work.
