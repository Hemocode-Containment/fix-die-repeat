# GitHub Projects Portfolio

Use this reference for portfolio, all-projects, multi-lane priority, and GitHub Project work. The
canonical Project classifies initiatives across repositories; it never replaces delivery issues,
pull requests, milestones, or dependencies.

## Resolve the canonical Project

1. Inspect active Git workspaces and find the one whose tracked files contain
   `docs/contracts/initiative-registry.md`.
2. Resolve that checkout's GitHub `owner/repo` through the normal repository routing.
3. Read the initiative-registry contract in full.
4. Discover Projects linked to that repository and require exactly one open linked Project. An
   explicit `--project-number` can narrow the linked set, but it cannot select an unlinked Project.
5. Stop when the repository, Project, schema, permissions, or required reads are missing or
   ambiguous.

Never hard-code an account, repository URL, Project number, Project ID, field ID, option ID, view
ID, or workflow ID. Supplemental program or repository Projects provide read-only delivery context
unless their own tracked contract explicitly assigns them portfolio authority.

## Capture live state

Run the read-only helper from the skill directory:

```text
scripts/project_snapshot.py --repo OWNER/REPO [--project-number N] --format json
```

The helper returns versioned Project metadata, fields and options, views, workflows, items, live
native milestone coverage, read completeness, and contract findings. Exit status `0` means a complete valid snapshot,
`2` means a complete snapshot with drift, and `1` means an incomplete read or error. Do not treat
`1` as empty state, and do not mutate from a status-`2` snapshot until the proposed write addresses
the relevant drift.

Refresh live delivery evidence after the snapshot. Project `Priority` records declared intent;
native blockers, incidents, deadlines, pull requests, checks, and milestones determine current
actionability. Explain an evidence-backed urgent override and propose the exact Project change
instead of silently changing declared intent.

## Interpret the portfolio

Use these four primary-rationale lanes exactly as the contract defines them:

- `Cost, Monetization, Reputation`
- `Automation and Leverage`
- `Releases and Community`
- `Foundations and Access`

Keep `Status`, `Priority`, and `Attention` disjoint:

- `Status` records lifecycle.
- `Priority` records declared ordering intent.
- `Attention` records current capacity.

An active initiative requires a lane, `Priority: Now`, an exit gate, and either `Human` or
`Monitored automation` attention. Other lifecycle states leave `Attention` unset.

The normal two-slot briefing contains:

- One reserved `Human` slot for actionable `Cost, Monetization, Reputation` work. Show it as empty
  when the lane has no actionable initiative.
- One shared `Human` slot for the other three lanes. Move it only at an exit gate, a documented
  blocker, or an approved critical interrupt.
- One portfolio-wide `Monitored automation` allowance, which can remain empty.

For each occupied slot, state the initiative, why it is actionable now, its next concrete action,
its exit gate, native delivery milestones, and any deadline or review date. State what waits and
which exit gate releases it. This makes the cost of each priority decision visible.

## Interpret milestone coverage

Milestone coverage follows open native delivery artifacts, not every repository mention:

- Every open issue or pull request under `Delivery work` makes its repository active delivery.
- Every `Planned` or `Active` initiative links the applicable open native milestone under
  `Delivery milestones`.
- Every open linked delivery artifact belongs to one of those milestones in its own repository.
- An `Active` initiative links open delivery work or records a current milestone exception.
- Candidates, pre-delivery planned work, completed evidence, dependencies, and contextual links do
  not create coverage by themselves.

Each milestone exception must name the repository, owner, evidence, rationale, creation date,
review or expiry date, and exit condition in the contract's exact format. It can last at most 14 days.
Failed reads, unknown permissions, inaccessible repositories, and renamed lookalikes never qualify
as exceptions.

Show native milestone due dates as delivery evidence, but never copy them into Project `Deadline`.
Do not create portfolio shadow milestones or a custom milestone-reference field. Keep differently
shared repository details one-way when a backlink would disclose the private initiative.

A critical incident can temporarily preempt either human slot when current evidence shows material
outage, security, data-loss, financial, or reputation harm. Require explicit approval and a record
of the evidence, displaced initiative, review or expiry time, and resumption plan. Keep at most two
human slots total and one per lane, then restore the normal reservation when the interrupt ends.

Allow at most one monitored initiative. Require a successful supervised pass, named assignee,
`Review by` date, health evidence, resource bound, stop condition, and exit gate. Unknown evidence
means the initiative does not qualify.

## Detect drift

Treat these as blocking findings for any related write:

- Incomplete reads, permission gaps, an ambiguous Project, or a changed schema.
- Missing required fields or options, view mismatches, or workflow mismatches.
- More than two human-attention items, more than one human item in a lane, or more than one shared
  item without a valid critical preemption.
- More than one monitored initiative or missing monitored safeguards.
- `Active` items without `Now`, lane, exit gate, or attention.
- Attention on any non-active item.
- Closed issues with nonterminal Project status, or open issues with terminal status.
- `Last reviewed` older than 14 days.
- Open linked delivery work without a native milestone or a matching initiative milestone link.
- Open delivery work using a closed milestone, an active initiative without delivery evidence, or
  an invalid or expired milestone exception.
- Incomplete milestone or delivery-artifact reads.

An empty reserved slot is valid. Report it plainly, and flag it only when live delivery evidence
shows actionable work in the cost lane that the Project has omitted or misclassified.

## Prepare Project mutations

Project writes are nontransactional across rows. Prepare every change before asking for approval:

1. Capture a fresh complete snapshot and its immutable Project, item, field, and option IDs.
2. Validate the proposed poststate against field, lifecycle, attention, terminal, monitored, and
   critical-interrupt invariants.
3. Show the exact target, old value, new value, and resulting slot distribution.
4. Treat each field creation, option change, view change, workflow change, item addition, and item
   field assignment as a specific approval unit. Broad plan approval does not approve these writes.

Issue, label, milestone, comment, and relationship mutations retain the separate approval units in
`github-routing.md`.

Treat each native milestone creation, issue or pull request assignment, initiative milestone-link
edit, and exception creation or renewal as a separate approval unit. Re-fetch the native repository
immediately before each approved write and read the result back before continuing.

## Execute approved mutations

1. Re-fetch the Project schema, workflows, and named items immediately before the first write.
2. Compare their IDs and values with the approved base. Stop and prepare a new preview when the base
   changed.
3. Clear outgoing `Attention` before assigning the same capacity to an incoming initiative.
4. Apply one approved write at a time.
5. Read the exact target back after each write and validate the intermediate state.
6. Stop after a permission failure, stale base, indeterminate result, or partial application.
   Preserve successful writes, report the last confirmed state, and provide the exact recovery
   operation. Never retry blindly or delete created issues to hide partial progress.

Keep receipts sanitized. Include stable URLs and human-readable old and new values, but omit
credentials, private connector payloads, and delivery details outside the portfolio audience.

## Project setup guidance

The canonical Project should provide:

- `Inventory`, grouped by `Lane`, with lifecycle, attention, deadline, and review fields visible.
- `Current`, a lane-column board filtered to `Active` and `Paused`.
- `Pipeline`, grouped by `Lane`, filtered to `Candidate` and `Planned`, and sorted by `Priority` then
  `Deadline`.
- `History`, grouped by `Lane` and filtered to `Completed` and `Dropped`.
- Item-added automation that assigns `Status: Candidate`.
- Disabled automatic sub-issue addition and disabled terminal automations.

Do not add misleading column limits, charts, broad auto-add rules, or external enforcement until
live use proves the need and the manager approves the specific feature.
