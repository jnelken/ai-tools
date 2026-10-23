---
name: fix-stale-docs
description: Find and repair documentation references or behavior descriptions that no longer match a repository. Use for stale README files, broken internal paths, or documentation cleanup.
---

# Fix Stale Docs

Review the user's specified repository or documents against current code and behavior.
Requires access to that source; when only text is provided, distinguish editorial
suggestions from references whose existence you actually verified.

## Verify candidates

Read repository instructions and Git state first. Search tracked Markdown and other
relevant documentation for the names or behavior that changed. Resolve relative paths
from the document's location and account for repository-root conventions, generated
files, consumer-relative paths, sibling repos, and Git refs. A missing local file is a
candidate, not proof of stale documentation.

Leave deliberate historical records in ADRs, completed plans, and changelogs intact;
an annotation or current link can be appropriate when history is causing confusion.
Do not rewrite history to make it describe today's implementation.

For stale prose, cite the source that establishes current behavior: code, configuration,
an executed check, or a current authoritative reference. Say what cannot be verified.

## Make focused corrections

- Repoint a moved reference and preserve useful rationale.
- Rewrite claims contradicted by verified behavior.
- Delete a document only when the user's cleanup scope permits it and its useful
  content is obsolete or already preserved. Check inbound links and update them together.
- Use the user's branch or an isolated branch when appropriate; do not choose another
  developer's approved PR as a convenient place to land unrelated docs.
- Keep code and configuration changes outside a docs-only request. If the documentation
  reveals a product bug, report it separately.

Check the final links, referenced commands, and Markdown rendering when available.
Commit and publish only under the session's normal authorization; this skill grants no
blanket push permission or hook bypass. Report changed files and unresolved candidates.
