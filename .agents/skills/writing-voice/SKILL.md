---
name: writing-voice
description: Apply the manager's established voice when drafting or editing persisted prose such as documentation, comments, commits, changelogs, pull requests, issues, reviews, ADRs, and design documents. Do not use for conversational replies or factual answers.
---

# Writing voice

Apply this skill only to persisted prose. Do not apply it to conversational replies or factual
answers.

1. Read [`references/calibrations.md`](references/calibrations.md) and
   [`references/profile.md`](references/profile.md) in full before every persisted-prose write,
   including comments, commit subjects, changelogs, pull requests, and short documentation.
2. Apply repository requirements first, then the most specific confirmed calibration, then the
   profile, then general writing defaults. Preserve factual claims, unprotected names, numbers,
   quotations, and code behavior.
3. Treat the manager’s name, private account identifiers, and every explicitly protected value as
   protected identifiers. Never copy them into persisted prose. Replace an occurrence with a
   neutral role or `[protected identifier]`; never infer a protected value from style, metadata,
   or context.
4. If a material voice gap remains, pause before mutation and provide exactly three ranked,
   artifact-sized candidate drafts. Give each candidate an independent score out of 100 and a
   decision-relevant reason. Keep facts and constraints fixed, vary one named writing dimension,
   and let the manager select, blend, or reject them. After a selection, propose a concise rule and
   a sanitized representative sample, then obtain explicit confirmation before updating
   `references/calibrations.md`.
5. Do not start calibration for conversational replies, factual uncertainty, routine word choice,
   metadata-only operations, repository-mandated formats, or prose already covered clearly by the
   profile.

## Registers

- Documentation uses the direct, claim-first documentation register in the profile.
- Code comments state purpose and the reason for non-obvious choices.
- Commit subjects and changelog entries stay short, specific, and past-tense by default.
- Pull-request bodies follow the confirmed calibration and preserve repository-required details.

The corpus is a curated source of patterns, not proof of authorship or production method. Do not
infer who wrote a passage or how it was produced from style, metadata, or surrounding context.
