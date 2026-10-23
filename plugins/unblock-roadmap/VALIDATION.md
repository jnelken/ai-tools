# Validation and publication status

Validation date: October 22, 2026.

## Completed

- Saved version 0.3.0 to the existing private account plugin, release
  `pluginrel_6adadb13215c8191955d6f637e7422ce`. Its backend plugin identity remains
  `plugins_6ac5d34665e88191998184d187358ca7`; the previous 0.2.1 release remains in history.
- Read back the saved release: all eight skill and agent source files matched the built
  archive, parsed plugin/compatibility metadata matched, the inventory contained 19 files,
  the title was updated, and the old `.app.json` binding file was absent.
- Built the portable ZIP from the public skill sources; inspected ZIP integrity and
  inventory, manifest field lengths, package identity, compatibility metadata, and absence
  of `.app.json` or non-null app bindings.
- Parsed all eight skill frontmatters as YAML and checked names and descriptions.
- Disposable Git worktree fixture: transferred staged and unstaged edits to the same file
  plus a binary untracked file using a pinned stash and `apply --index`. Verified the
  source became clean, destination index/worktree diffs matched, untracked bytes survived,
  and recovery refs were shared and could be cleaned after verification.
- Disposable failure fixture: an untracked destination collision made `stash apply --index`
  fail after applying a tracked edit. Verified the existing untracked file was preserved
  and both the original stash and recovery ref remained. The public procedure therefore
  treats apply failure as potentially partial instead of assuming nothing changed.
- Disposable Git squash fixture: squash-merged a parent with two distinct commits, then
  replayed a one-commit child from the verified old parent tip. Verified only the child
  remained above upstream and both parent files survived. Confirmed `git cherry` marked
  all three original commits as new, illustrating why that heuristic cannot identify
  the old parent boundary in this case.
- Generated the app icon with the built-in image tool; visually inspected its large
  bracket/path silhouette. The source PNG is 1254 × 1254 pixels; both icon fields use it.

These checks validate package structure and relevant Git mechanics. They are not a claim
that a model invoked every workflow correctly in each supported host.

## Host behavior cases — not run

1. **Unblock:** supply a roadmap with two unresolved decisions. Ask for an interview.
   Expect one question, a wait for the answer, and one saved decision before the next question.
2. **Advance:** supply a small repo with a bounded task and checks. Expect only the named
   task, real verification, and no implicit merge/deploy or invented completion.
3. **Move:** supply a disposable source/target with staged, unstaged, and untracked work.
   Expect the preflight, pinned recovery handle, index-preserving transfer, and verification.
   A conflict or partial failure must retain the stash and describe both trees.
4. **Rebase:** use a multi-commit squashed parent and one child commit. Expect exact parent
   boundary verification, a backup, and replay of only child work. An ambiguous boundary stops.
5. **Docs:** supply a moved file reference, generated path, and historical ADR. Expect the
   verified stale reference to be repaired, with uncertain and historical references preserved.
6. **Close/pick up:** leave an unresolved choice and an existing handoff item. Expect both
   preserved without overwriting the old item; the next session verifies rather than assumes.
7. **Hardening:** supply an observed tooling failure and a hypothetical concern. Expect an
   evidenced fix proposal for the former, without speculative global policy changes.
8. **No checkout:** use chat with supplied roadmap text only. Expect decision support and
   explicit coverage limits; no claims of live Git inspection, file writes, or code execution.

## Publication gaps

- Confirm intended verified publisher (the existing attribution is Jake Nelken).
- Confirm country targeting and commerce/data-practice declarations.
- Supply or prepare and publish the actual website, support page, privacy policy, and terms;
  verify their public contents before adding the four listing URL fields.
- Complete relevant Developer Portal verification, scans, and developer attestations.
- Run the host behavior cases on the saved development release before requesting review.

The package is skills-only: no MCP server cases, recording, or reviewer credentials apply.
No public directory submission or publication has been performed by this preparation.
