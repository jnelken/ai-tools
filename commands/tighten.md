---
description: Scan a file, folder, or repo for high-confidence refactor wins that reduce complexity with minimal side effects
argument-hint: File path, folder path, or '.' for the whole repo
---

# Tighten

Scan the target for a single clear, low-risk refactor win. Stop as soon as you find one high-confidence issue you can solve, fix it, and move on.

Log any other potential issues you notice along the way to the repo's tighten memory file for future runs.

## Target

$ARGUMENTS (if empty, scan recently modified files from `git diff HEAD`)

## Tighten Memory

Before scanning, check for an existing tighten memory file at `.claude/tighten-backlog.md` (relative to the repo root). This file tracks issues spotted in previous runs.

- **Before scanning:** Read the backlog. If it contains unresolved issues, try the first one before scanning for new issues. If you can confidently fix it, do so and remove it from the backlog. If it's no longer relevant (code changed, already fixed), remove it and move on.
- **During scanning:** When you encounter potential issues that you are NOT going to fix right now, append them to the backlog with file path, line number, pattern name, and a brief description.
- **After fixing:** Remove the fixed issue from the backlog if it was listed there.

## Detection Patterns

For each pattern, search the target scope (.ts/.tsx files). Stop at the FIRST high-confidence finding where the current code is objectively more complex than the simplified form.

### 1. Dead union variant
A union type has variants that are never assigned anywhere in the codebase.
- Search: export the type, grep all files for assignments/usages of each variant
- Win: remove the dead variant or collapse to the used type
- Example: `type Foo = "fill" | "content"` where `"fill"` is never assigned -> `type Foo = "content"`

### 2. Single-value union -> boolean
A prop typed as `"value" | undefined` (one string variant) where the semantics are binary.
- Search: props/fields typed as a union of exactly one string literal + undefined
- Win: replace with `propName?: boolean`
- Example: `visible?: "shown"` -> `visible?: boolean`

### 3. Parallel props in conflict
Two props on the same interface that control the same output, evidenced by suppression logic
(`a === "x" ? null : getB(a)`).
- Search: conditional expressions that null-out one function call based on another prop's value
- Win: merge the props (extend existing type or replace with boolean)
- Example: `growth === "content" ? null : getWidthClass(width)` -> add `"content"` to width type

### 4. Dead export
A type or function is exported but has zero import sites across the entire codebase.
- Search: run `npx knip --include exports,types` to detect unused exports. If knip is not installed or fails, fall back to grepping for import usage manually.
- Win: remove the export (and the definition if it has no internal uses either)

### 5. Identity adapter field
A mapper/converter function copies a field from input to output unchanged AND the field
exists identically on both types (same name, same type). The field on the source type can
instead be inherited/spread.
- Search: mapper functions where `output.x = input.x` with no transformation
- Win: use spread or remove the redundant mapping

### 6. Prop forwarded but unused
A component accepts a prop in its interface but only passes it through to a child without
reading it. The prop type on the parent duplicates the child's type.
- Search: props destructured and immediately spread/passed to a single child
- Win: use `ComponentProps<typeof Child>` or remove the explicit re-declaration

---

## Workflow

1. Read `.claude/tighten-backlog.md` if it exists. Try to resolve the first backlog item before scanning.
2. If no backlog item was resolvable, scan the target scope using the detection patterns above.
3. **Stop at the first high-confidence finding.** Do not keep scanning for more.
4. Present the finding to the user:

```
[Pattern Name] -- <file>:<line>
Current:
  <code snippet>
Proposed:
  <code snippet>
Confidence: high
Side-effect risk: none | low
```

5. Ask: "Want me to apply this fix?"
6. If approved, apply the fix using the Edit tool.
7. Log any other potential issues noticed during scanning to `.claude/tighten-backlog.md`.

---

## Scope Rules

- If $ARGUMENTS is a file path -> analyze that file + files it imports
- If $ARGUMENTS is a folder -> glob `**/*.{ts,tsx}` within it
- If $ARGUMENTS is `.` -> full repo scan; warn the user it may take a moment
- If $ARGUMENTS is empty -> run `git diff HEAD --name-only` and scan those files

## Constraints

- Only report findings where the win is unambiguous -- skip anything requiring judgment calls
- Never report style/formatting issues (those belong to /simplify)
- Never report logic changes, only structural/type-level simplifications
- Stop after the first high-confidence finding -- do not exhaustively scan
- Keep the backlog file concise: one line per issue, max ~20 entries (drop lowest-confidence items if it grows beyond that)
