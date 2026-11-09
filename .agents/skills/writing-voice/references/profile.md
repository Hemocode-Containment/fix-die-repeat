# Persisted Writing Profile

This working profile combines one confirmed direct calibration with observable patterns from the
curated documentation and comment streams. The selected snapshots can contain multiple
contributors, so these sections are pattern evidence rather than proof of authorship or production
method. The corpus policy and its manifests define what evidence is eligible for a rebuild.

Protected identifiers never belong in this profile or in a derived artifact. Replace them with a
neutral role or `[protected identifier]` before preserving imported prose.

## Evidence boundary

- The pull-request-body rule in `references/calibrations.md` is a confirmed direct choice and is
  authoritative for that artifact type.
- The patterns below are corpus-derived observations from the pinned documentation and source
  comments. They are not claims about who wrote a passage.
- A rebuild must produce non-empty documentation and comment streams for every pinned source and
  record the source-manifest, file-manifest, and corpus SHA-256 digests before adoption.
- Read `references/corpus-policy.md` before changing source selection or interpreting a pattern.

## Rebuild receipt

The current profile was checked against the pinned, redacted corpus produced by builder revision
`f6935116d57480946173727761fa8de070b0acdc`:

| Evidence | Value |
| --- | --- |
| Source repositories | 2 |
| Documentation lines | 2356 |
| Comment lines | 726 |
| Source manifest SHA-256 | `81067baea2ed0d69e6c31f74e623ea0e36f8fd718c20492482cf8595e20e4bac` |
| File manifest SHA-256 | `57fa4cd53740465116f517ece359e1de52cdec75188ba4e2cda0e40f3052dc73` |
| Corpus content SHA-256 | `92c65b6545c0115d9260316c8d7e5e563e7b8f1c3baee6fff21aaac9d259ac43` |

Regenerate this receipt when the builder, either manifest, or either pinned snapshot changes.

## Register map

| Artifact | Register | Evidence boundary |
| --- | --- | --- |
| README files, guides, decision records, and checklists | Direct documentation | Corpus-derived |
| Code comments and XML documentation | Explanatory comment prose | Corpus-derived |
| Commit subjects and changelogs | Short action register | Repository convention |
| Pull-request bodies | Direct, unsectioned explanation | Confirmed calibration |

## Evidence register

Keep a corpus-derived rule only when the same observable behavior appears in at least two
allowlisted files. Record representative witnesses here so a rebuild can challenge the rule
against the pinned streams instead of trusting the digest alone.

| Pattern | Representative witnesses |
| --- | --- |
| Lead with observable behavior | `CONTRIBUTING.md`, `README.md`, `docs/crowd-control/overview.md` |
| Use concrete structure | `DOCUMENTATION_UPDATING_CHECKLIST.md`, `INTEGRATION_TESTING_CHECKLIST.md`, `docs/crowd-control/effect-development.md` |
| Prefer direct sentences | `CONTRIBUTING.md`, `docs/crowd-control/implementation-details.md`, `docs/decisions/0001-use-net-framework-3-5-for-mods.md` |
| Explain code comments | `LobotomyCorporationMods.BugFixes/Patches/CustomizingWindowPatchSetAgentStatBonus.cs`, `src/CrowdControl.LobotomyCorporation/Patches/GameManagerPatchUpdate.cs` |

These witnesses identify source locations, not authors. The pinned source and file manifests remain
the authority for the complete eligible set.

## Corpus-derived patterns

### Lead with observable behavior

- Open documentation with the project purpose or the behavior the reader needs to verify.
- Explain what the project does before explaining why the project exists.
- State a constraint beside its consequence, so the reader can tell what changes and what remains
  unchanged.

### Use concrete structure

- Use headings that name the subject, such as `Overview`, `Building`, `Testing`, or `Why create
  this mod?`.
- Use bullets for parallel requirements and checklists for independent verification steps.
- Use a numbered list only when the reader must follow an ordered procedure.
- Keep examples close to the rule they demonstrate.

### Prefer direct sentences

- Use a concrete verb and name the subject early. Join related ideas with a causal link such as
  “because,” “which means,” or “so.”
- Keep qualifiers narrow and state the reason for a meaningful limitation in the same sentence.
- Address readers as “you,” collaborators as “we,” and personal motivation as “I” only when the
  artifact genuinely needs a first-person account.

### Explain code comments

- State the purpose of a non-obvious block, then explain the reason for its ordering, workaround,
  deferred step, or unusual value.
- Keep comments next to the invariant they explain. Do not restate a method name or claim that a
  comment proves authorship or production method.
- XML documentation uses one concise imperative summary when the contract needs to be exposed.

## General defaults

- Put every code identifier, path, filename, and configuration value in backticks in prose.
- Use parentheses for asides and the Oxford comma. Avoid em dashes for inline asides.
- Avoid generic openings, filler transitions, vague hedges, passive recommendations, and
  unexplained caveats.
- Keep commit subjects and changelog entries short, specific, and past-tense when repository rules
  do not prescribe another form.
