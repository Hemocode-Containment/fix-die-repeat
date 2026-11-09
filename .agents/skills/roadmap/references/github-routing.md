# GitHub and Tool Routing

Use this reference for every roadmap intent.

## Resolve context

1. Prefer an explicit `owner/repo`, repository URL, issue URL, or PR URL.
2. Otherwise resolve the local checkout with `gh repo view --json nameWithOwner -q .nameWithOwner`.
   If `gh` is unavailable, inspect `git remote -v` and normalize a unique GitHub remote.
3. For portfolio work, use active Antigravity workspaces and local Git checkouts rather than scanning every accessible repository.
4. If repository identity remains ambiguous, ask for it before querying or mutating GitHub.

## Connector-first routing

Use tool discovery to load the relevant GitHub app action. Prefer structured connector actions for:

- searching and fetching issues;
- searching and fetching pull requests;
- reading comments and review state;
- creating or updating an issue;
- adding a project-domain comment;
- creating a pull request after its branch is pushed.

Use local `git` for tracked-file discovery, branch/worktree state, diffs, commits, and momentum.
Use authenticated `gh` for these connector gaps:

- repository identity, label enumeration/creation, and milestone enumeration/creation;
- native sub-issue and issue-dependency operations;
- milestone assignment when the connector cannot update the target issue or pull request;
- branch-specific PR discovery or fields the connector omits;
- GitHub Actions logs when CI diagnosis is requested.

If the preferred path fails, use the other GitHub path when it provides equivalent truth. Do not
interpret a failed query as an empty result.

## Read budget

For a repository overview or ranking, gather a single reusable snapshot containing open issues and
their number, title, body, labels, assignees, updated time, milestone, and URL. Gather open and
recently merged PRs with title, body, head branch, state, milestone, reviews, checks, and URL when
supported. Fetch open milestones and enough closed milestones to establish repository naming
conventions when no open milestone exists. For every open milestone with a due date, gather its
open issues and pull requests, then inspect native blockers and linked pull requests for each open
issue. Check linked-pull-request history for every open issue regardless of milestone; when that
history cannot fully cover an issue, record the check as unknown rather than clear. Include review
and check state
for the relevant pull requests when supported. For each open tracking issue, fetch its native
sub-issues and summarize each child's state; when the sub-issue endpoint fails or is unsupported,
record that relationship as unknown rather than empty. Treat unavailable blocker, review, or check
data as unknown rather than empty. Fetch individual issues only for missing details or a named
item.

For portfolio milestone coverage, read only the native issues, pull requests, and milestones linked
from each initiative. Verify that every open delivery artifact uses the linked milestone in its own
repository. Treat a repository transfer, archive, missing milestone, or inaccessible artifact as
unknown or drift instead of following a similarly named replacement.

Use native relationships as truth:

```text
GET repos/{owner}/{repo}/issues/{number}/sub_issues
GET repos/{owner}/{repo}/issues/{number}/dependencies/blocked_by
```

When `gh api` performs these calls, pin `X-GitHub-Api-Version: 2026-03-10`. Adding a sub-issue
requires the child's global numeric `id`, resolved once from
`repos/{owner}/{repo}/issues/{number}`:

```text
POST repos/{owner}/{repo}/issues/{parent}/sub_issues
sub_issue_id=<child-global-id>
```

Do not infer native dependencies or children from prose. If the endpoint is unavailable, mark the
relationship unknown and continue only when that uncertainty cannot change the requested action.

## Mutation gates

Treat these as separate approval units:

- create one issue;
- create one missing label;
- create one milestone;
- assign one issue or pull request to one milestone;
- edit one initiative's delivery milestone links;
- create or renew one milestone exception;
- close one issue;
- add one comment;
- create one tracking parent;
- create one child issue;
- link one existing or newly created child;
- create, rename, pin, message, or hand off a separate Antigravity session when not already explicit.

Before an issue creation, show its complete title, body, labels, assignees, and milestone. Before a
milestone creation, show its exact title, description, and due date or the explicit absence of one.
For a milestone assignment, show the exact issue or pull request and target milestone. Closing
requires showing the issue and completion evidence. Before a comment or link, show the exact target
and content/relationship. Wait for approval of that specific action.

Re-fetch open milestones immediately before an approved milestone creation. If an open milestone
now exists, report the changed state and stop rather than creating a competing milestone. Re-fetch
the target issue or pull request and milestone immediately before each approved assignment.

If issue creation succeeds but a later sub-issue link fails, leave the issue open, report the
failure, and provide the exact retry operation. Do not delete or silently rewrite the parent.

## Labels and classification

Respect repository-native label variants. Prefer one core work-type label when available:
`bug`, `enhancement`, `documentation`, or `tech-debt`. Additional area/component labels are useful.
`tracking` is a companion label, not a work type. `good first issue` is optional and applies only
when scope, verification, blast radius, and prerequisites are genuinely newcomer-safe.

If a useful label is missing, offer its exact name, color, and description for approval; never
silently scaffold labels. An overview flags missing or conflicting core classification without
blocking read-only output.

Use these defaults only when the repository has no accepted variant:

| Label | Color | Description |
|---|---|---|
| `bug` | `d73a4a` | `Something isn't working` |
| `enhancement` | `a2eeef` | `New feature or request` |
| `documentation` | `0075ca` | `Improvements or additions to documentation` |
| `tech-debt` | `fbca04` | `Internal refactor, tooling, or maintenance` |
| `tracking` | `0e8a16` | `Tracking issue for a scoped batch of children` |
| `good first issue` | `7057ff` | `Good for newcomers` |

## Failure discipline

- Never write a local item when GitHub is unavailable.
- Never mutate from stale cached data; re-fetch the named target immediately before a write.
- Never replace an issue's full label set when an additive label operation is intended.
- Never close children merely because a tracking parent closes, or close a parent merely because
  its children close.
- Never expose credentials, tokens, connector payload internals, or private repository content
  beyond what the user requested.
