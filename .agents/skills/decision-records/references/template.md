# Living Decision Template

Remove optional sections that do not help explain the current position. Do not invent content to
fill an empty heading.

```markdown
# [Name the architectural topic]

## Current approach

[State what we do now.]

## Rationale

- [State a force, constraint, or desired quality that distinguishes acceptable outcomes.]

## Alternatives considered

### [Option]

- Good, because [state a material advantage].
- Bad, because [state a material disadvantage].

## Constraints and tradeoffs

- [State an accepted cost, limit, or assumption and why it matters.]

## Assumptions

- [State an assumption that would change the decision if it stopped holding.]

## Reconsider if

- [State an observable change that would invalidate the current reasoning.]

## Confirmation

[State how maintainers can detect drift.]

## Related contracts

- [Link the normative contract, focused tests, or relevant historical ADR.]
```

Do not add status, date, numbering, acceptance, or supersession fields. Update the document in
place when the current position changes. Keep only serious alternatives and concrete triggers.
