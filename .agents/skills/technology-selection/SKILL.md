---
name: technology-selection
description: Evaluate and recommend best-fit technology stacks for greenfield projects, major subsystems, rewrites, and consequential architecture choices. Do not use for routine work inside a settled stack unless reevaluation is requested.
---

# Technology Selection

Use this skill when the choice of language, runtime, framework, storage, messaging, or major
infrastructure will materially shape the architecture. The goal is to find the best fit for the
actual requirements, not to justify a familiar default.

Read [`references/language-heuristics.md`](references/language-heuristics.md) when selecting a
language, runtime, or full stack. It contains preferred starting points, not a mandatory menu.

## Decide whether it applies

- Use broad evaluation for a greenfield project, a major subsystem, an architectural rewrite, or
  a consequential replacement.
- Give an existing stack substantial weight for an incremental feature, because compatibility,
  migration cost, operational consistency, and the surrounding system can outweigh theoretical
  technical advantages.
- Do not reopen a settled choice for a small or local implementation unless a requirement changed
  or the request explicitly asks for reevaluation.
- Honor a technology that the request has already fixed unless the request asks you to challenge it.

## Evaluate the decision

1. Inspect the repository, existing architecture, deployment environment, integrations, and
   operational constraints before proposing a stack. Ask focused questions when an unknown could
   change the recommendation.
2. State the decision boundary and the dominant drivers. Consider only the drivers that matter to
   this problem, such as latency, throughput, concurrency, fault isolation, lifecycle management,
   ecosystem fit, development speed, deployment complexity, operational burden, portability,
   maintainability, security, and interoperability.
3. Form at least three materially distinct, serious approaches when the decision space genuinely
   supports them. Do not invent weak alternatives to satisfy a quota. If fewer than three credible
   approaches exist, explain why before recommending one.
4. Allow a hybrid stack only when each boundary has a clear owner and the benefits exceed the
   integration, deployment, debugging, and operational costs.
5. Verify current claims that could have changed, including maintenance status, platform support,
   ecosystem capability, versions, and deployment availability. Use authoritative sources and cite
   them when they affect the recommendation. Recommend a prototype or benchmark when evidence
   cannot establish the deciding claim.
6. Compare every candidate against the same drivers. Give each candidate an independent
   recommendation-strength score out of 100 with a reason, then state its strongest benefit,
   strongest cost, migration implications, and important risks.
7. Recommend one approach, explain why its advantages matter here, disclose the selected approach's
   accepted costs, and name the conditions that would make the runner-up preferable.

## Report the result

Use this shape unless the request calls for a different artifact:

- Decision boundary and hard constraints.
- Decision drivers and relevant existing-stack constraints.
- A comparison of the serious candidates with independent scores, strongest benefits, and costs.
- The recommendation, accepted tradeoffs, assumptions, and unresolved evidence.
- A focused validation spike, benchmark, or reconsideration trigger when uncertainty remains.

Route durable architectural reasoning to the `decision-records` skill only when the request asks for
that documentation or separately authorizes it. This skill selects and explains technologies; it does
not scaffold a project, install dependencies, run a benchmark automatically, or replace a
repository's normative contract.
