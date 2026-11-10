---
name: roadmap
description: Manage GitHub-backed project work from Antigravity using live issues, pull requests, milestones, GitHub Projects, local git state, and Antigravity conversations. Use when the user asks for a roadmap or portfolio overview, asks what to work on next, wants to prioritize or milestone-scope work, add or group issues, start or resume an issue, inspect current focus, audit milestone coverage, complete roadmap work, reconcile Project lanes or attention, or coordinate work across workspaces and repositories.
---

# Roadmap

Treat GitHub Issues as durable work state and Antigravity conversations/worktrees as execution sessions. Build every
overview and ranking from live data. Do not create a roadmap cache, archive, index, focus file, or
other parallel source of truth.

## Core workflow

1. Resolve the repository from an explicit repository/URL first, then the local checkout. Ask only
   when more than one repository remains plausible.
2. Read [references/github-routing.md](references/github-routing.md) in full before accessing or
   changing GitHub state.
3. Determine the user's intent from natural language; do not parse a command-argument variable.
4. Read the intent-specific reference in full:
   - Overview, add, tracking, start, resume, pause, complete, or portfolio:
     [references/lifecycle.md](references/lifecycle.md)
   - Next-work ranking or milestone-scoped ranking:
     [references/prioritization.md](references/prioritization.md)
   - Portfolio, all-projects, multi-lane priority, or GitHub Project work:
     [references/projects.md](references/projects.md)
5. Gather live data before drawing conclusions. Reuse results within the turn.
6. Lead with the outcome: current state, recommended next action, or exact proposed mutation.
7. Stop after the requested intent. An overview never starts implementation automatically.

## Intent routing

| User intent | Behavior |
|---|---|
| `roadmap`, `overview`, “show open work” | Reconcile live issues, PRs, milestones, repository artifacts, and related Antigravity conversations/worktrees. |
| `next`, “what should I work on?” | Rank actionable work from the full dependency graph without persisting the ranking. |
| `next <milestone>` | Rank globally, then display the matching milestone while preserving external blockers. |
| `add <description>` | Check duplicates and templates, then draft one issue for approval. |
| `add tracking <description>` | Draft a small same-category parent/child batch using native sub-issues. |
| `start next` or `start #N` | Verify the issue, then plan or deliver it in the current session through the confirmed delivery endpoint. |
| `resume` | Reconstruct status from the current conversation, branch, issue, PR, reviews, and CI. |
| `pause` | Explain that the session persists; pin, rename, or hand off only when explicitly requested. |
| `complete #N` | Verify landed evidence and offer an exact GitHub close action when needed. |
| `portfolio`, `all projects`, multi-lane priorities, or milestone coverage | Resolve the canonical initiative Project, validate it, and build the live lane, attention, and native milestone briefing. |

## Non-negotiable rules

- Prefer the connected GitHub plugin/MCP tools when installed, or authenticated `gh` CLI.
- Never invent issue, PR, dependency, milestone, label, task, branch, review, or CI state.
- Never create a local-only roadmap item.
- Preview and obtain explicit approval for each issue creation, label creation, milestone creation,
  milestone assignment, issue closure, comment, and sub-issue link. Approval of a broad plan is not
  approval of an individual mutation.
- Treat every Project field, view, workflow, item, and field-value change as its own previewed
  mutation under [references/projects.md](references/projects.md).
- Respect the active collaboration mode. In Plan Mode, `start` produces a decision-complete plan
  and performs no implementation or external mutation.
- Create a separate subagent or worktree session only when the user explicitly asks for one.
- Preserve dirty working trees. Never auto-stash, commit unrelated baseline changes, force-delete a
  branch, or discard changes.
- Keep persisted GitHub prose about the project itself. Do not mention Antigravity, skills, prompts, or
  local orchestration unless the issue is specifically about those artifacts.
- When the delivery endpoint includes a pull request, use the installed GitHub publishing workflow
  and require the PR body to close every covered issue (`Fixes` for defects; `Closes` otherwise).

## Degraded operation

If neither the GitHub app nor authenticated `gh` can provide required issue state, explain the
missing capability and stop before any durable roadmap action. Local git inspection may still
support a clearly labeled partial diagnostic, but never substitute for GitHub state.
