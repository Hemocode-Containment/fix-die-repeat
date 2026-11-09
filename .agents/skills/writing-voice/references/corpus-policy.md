# Corpus selection policy

The profile corpus is a reproducible pattern source, not an authorship register. The source
manifest pins full commit hashes. The file manifest allowlists the documentation and source paths
that can contribute to each stream. The builder records both manifests, the pinned hashes,
per-file paths, and a SHA-256 digest of the redacted stream content before publication.

The builder requires an external newline-delimited protected-identifiers list. It replaces exact
values in source content and path labels with `[protected identifier]` before publishing the corpus.
The replacement pass is case-insensitive, matches the longest protected value first, and is
therefore independent of input ordering when values overlap. The list itself never belongs in the
repository or the corpus output. Prefer standard input when the values must not persist on disk;
stdin mode keeps the list in process-scoped memory. A missing or unreadable list fails closed, so an
invocation cannot silently publish unredacted material.

The allowlist favors project documentation, decision records, README files, and comments in the
project source trees. It excludes licenses, changelogs, issue templates, generated output,
vendored dependencies, and build artifacts because those files do not provide reliable evidence
of the intended writing register. Commit messages and author selectors are never corpus inputs.

Every pinned source must produce a non-empty documentation stream and a non-empty comment stream.
Corpus-derived profile rules may describe only observable patterns from those streams. A
manager-specific rule must come from a confirmed direct calibration, and protected identifiers must
be removed before any pattern is preserved.
