# init — brand context → BRAND.md

Capture durable brand context **before** logo exploration. Output lives at the **project root** as `BRAND.md`, not under `.logo-gestalt/`.

## When to run

- New product or rebrand with no pinned brand doc.
- User says "design a logo" but `BRAND.md` is missing or one paragraph of vibes.
- `craft` detected thin context.

Skip for throwaway doodles or "just sketch a cube icon" with no brand stake.

## Interview (ask in one message, bulleted)

Cover these; accept pasted docs (pitch deck, Notion) as answers:

1. **Name & one-liner** — what is it, in one sentence?
2. **Audience** — who chooses it vs who uses it daily?
3. **Category & alternatives** — what shelf does it sit on; what do people compare it to?
4. **Personality** — pick 3 adjectives and 1 anti-adjective (what we are *not*).
5. **Promise** — the outcome users care about, not feature list.
6. **Visual territory** — existing colors, type, references to embrace or avoid.
7. **Mark constraints** — must work as app icon? wordmark required? existing logo to evolve?
8. **Competitive marks** — 2–3 logos to **not** resemble (with reason).

If the user is impatient, minimum viable: (1), (3), (4), (7).

## Procedure

1. Copy `<skill-dir>/templates/BRAND.md` → `BRAND.md` if missing.
2. Fill every section from interview answers. Use `[TBD]` only with a follow-up question.
3. Pull **non-negotiables** into a short bullet list at the top (max 5).
4. Add a **Symbol seeds** subsection: nouns/verbs from the product domain (feeds `symbols` later).
5. Do **not** propose logo shapes in `BRAND.md` — that's `brief` / `symbols`.

## Quality bar

`BRAND.md` should let a stranger run `brief` without re-asking basics. Another designer should infer tone (precise vs playful, enterprise vs consumer) from personality + anti-patterns.

## Handoff

- → `brief` to narrow to logo-specific constraints.
- → `craft` if user wants the full pipeline in one go.

## Example non-negotiables block

```markdown
## Non-negotiables
- Must read at 16px favicon (dev tool, dock presence).
- Mono-first; color is accent only.
- No mascots; no literal brain/lightbulb AI clichés.
- Feels **craft studio**, not **enterprise SaaS generic**.
```
