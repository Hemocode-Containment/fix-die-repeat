# Live Prioritization

Use for `next`, milestone-scoped ranking, and any request to recommend the next work item. Produce a
fresh view; never persist the ranking.

## Build the candidate graph

1. Fetch all open issues in the repository snapshot described by `github-routing.md`.
2. Fetch each issue's native `blocked_by` relationships and reuse the results.
3. Treat closed blockers as satisfied. Keep open or unknown blockers explicit.
4. Build directed edges `blocker -> blocked`.
5. Detect cycles. Exclude cyclic issues from actionable ranking and show each cycle.
6. Keep off-roadmap or cross-milestone blockers in the graph even if they will not appear in the
   final filtered table.

When relationship calls fail, do not parse `depends on #N` prose as authoritative. Say dependency
state is incomplete and avoid a confident recommendation when the missing edge could change rank 1.

## Classify urgency

Infer a base urgency band from labels and issue evidence:

1. **Critical defect** — active security exposure, data loss, outage, crash, or severe reliability
   regression.
2. **Defect** — confirmed user-facing incorrect behavior without critical impact.
3. **Risk reduction** — maintenance that prevents a likely incident, removes a known operational
   hazard, or restores required safeguards.
4. **Product work** — enhancements, documentation, cleanup, and other planned work.

Do not promote vague language into a higher band. Flag unlabeled or multiply core-labeled issues,
but still rank them from evidence when possible.

Propagate the strongest downstream urgency to every transitive blocker. This makes a lower-category
prerequisite inherit the urgency of the important work it unlocks.

## Order actionable work

Only an issue with no open or unknown blocker is actionable. Order actionable issues by:

1. strongest propagated downstream urgency;
2. direct and transitive unblocking value;
3. user or operational impact;
4. risk reduction;
5. bounded effort and clear acceptance criteria;
6. recent momentum in the same area from local commits and active PRs.

Use these as explained judgments, not a numeric score. A high-effort blocker may still lead when it
is the only path to urgent work. Recent momentum breaks close calls; it never outranks urgency or
dependencies.

After ordering actionable issues, place blocked dependents immediately after the latest prerequisite
that must land, marked with their blockers. When at least one actionable issue exists, rank 1 must
be actionable. When every issue is blocked, unknown-blocked, or cyclic, report that there is no
actionable work and list the relevant blockers or cycles.

## Milestone scope

For `next <milestone>`:

1. Build and order the complete graph first.
2. Filter the displayed issues by case-insensitive milestone title.
3. Preserve blocker markers for blockers outside the milestone.
4. If every matching issue is blocked externally, say so and name the first actionable external
   blocker separately.
5. If the milestone has no matching open issue, list available milestone titles and stop; do not
   silently fall back to an unrelated recommendation.
6. Label the first table column `Global rank` and retain the full ordering's rank numbers rather
   than renumbering the filtered rows.

Milestone membership does not boost global priority by itself.

## Output contract

Show up to five items for an unscoped request and all matching items for a milestone request. Use
`Rank` when unscoped and `Global rank` when milestone-scoped:

| Rank | Issue | Why now | Dependency state | Milestone |
|---|---|---|---|---|

Then include:

- cycles or incomplete dependency data;
- stale or classification caveats that materially affect confidence;
- one direct recommendation naming rank 1 and the work it unlocks.

Do not create branches, tasks, issues, comments, labels, or files during prioritization.
