# Decision Documentation Review Checklist

## Artifact boundary

- If this is living guidance, does the document explain one current decision and why it remains sensible?
- If this is an ADR, does it preserve the original rationale and consequences, and does maintained immutable history matter beyond ordinary Git history?
- Should independently reversible choices become separate living documents?
- Should normative schemas, protocols, procedures, or tests move to another artifact?
- Does the review avoid treating importance or length alone as a reason to create an ADR?

## Context and options

- Does the context state facts and forces without assuming the outcome?
- Do the drivers distinguish acceptable options?
- Are all listed options serious, without filler alternatives?
- Does every option include its strongest material benefit and cost?
- Does the selected approach include its disadvantages?

## Current guidance and consequences

- Does the current approach connect directly to the drivers?
- Are rationale, resulting consequences, and normative behavior kept distinct?
- Are constraints, accepted costs, and assumptions explicit?

## Reconsideration, confirmation, and history

- Are observable reconsideration triggers explicit?
- Can the confirmation method detect drift without copying an exhaustive test matrix?
- Does a living document avoid status, date, numbering, acceptance, and supersession ceremony?
- Does a historical ADR link to current guidance without presenting itself as current policy?
- If an ADR is justified, does its lifecycle preserve accepted history?

## Adversarial pass

- What is the strongest case for every rejected option?
- What is the current approach's most consequential weakness?
- Which assumptions could fail?
- How difficult is reversal or migration?
- Where can enforcement or confirmation fail?
- Which downstream consequences become future decision context?
- Did the findings require a split, a different outcome, changed reconsideration triggers, or
  changes to the implementation plan?
- Were all material revisions and remaining risks disclosed before finalizing the decision?
