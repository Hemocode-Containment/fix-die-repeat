# Historical ADR Template

Use this template only when an audit obligation, external commitment, compatibility promise, or
significant transition requires maintained immutable history beyond Git.

```markdown
# [State the historical decision]

Status: Proposed

Date: YYYY-MM-DD

## Context

[Describe the historical problem and forces without arguing for an option.]

## Considered options

### [Option]

- Good, because [state the strongest material advantage].
- Bad, because [state the strongest material cost].

## Decision outcome

We will [state the selected direction], because [connect it to the historical drivers].

## Consequences

- Good, because [state a positive effect].
- Bad, because [state an accepted cost].

## Current guidance

[Link the living decision document that tells maintainers what to do now.]
```

An ADR introduced on the current branch remains directly revisable until merge. Supersede only an
accepted record already present on the integration base, and keep reciprocal links.
