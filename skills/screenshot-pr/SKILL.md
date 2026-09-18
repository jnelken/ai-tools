---
name: screenshot-pr
description: Capture one signature screenshot of the current branch's most visually-significant UI change and embed it in the corresponding GitHub PR description, all driven through a single Playwright browser session against the Netlify deploy preview. Use this skill whenever the user asks to "add screenshots", "screenshot the PR", "snap the PR", "drop a visual into the PR", "capture the change", or otherwise asks to attach a visual to a pull request — even if they don't mention Playwright. The skill handles diff analysis, deploy-preview login, capture, and the GitHub web-UI upload that `gh` CLI cannot do directly. Falls back to localhost only when explicitly requested.
---

# screenshot-pr

Take one screenshot of the current branch's UI work and embed it in the open PR's description, above the second section header. One screenshot per PR — the most visually-significant change. Driven by a single Playwright browser session that captures from the **Netlify deploy preview** and handles the GitHub upload in the same session (since `gh` CLI cannot upload images).

## When this skill applies

Trigger on phrases like "add screenshot(s)", "screenshot the PR", "snap a shot for the PR", "add a visual to the PR", or any equivalent intent to drop a screenshot into the open PR. It does **not** apply to in-progress design exploration, taking screenshots for Slack/docs, or running E2E tests — those use Playwright MCP directly.

The skill assumes:
- The user has already pushed the branch and opened a PR.
- The PR has a Netlify **deploy preview** ready or in progress (the default capture target — see below). Localhost is only used as an explicit fallback.
- `.env` contains the app's test-user login credentials (the same login works on the deploy preview).
- The user is signed into github.com in the Playwright browser session, or is willing to sign in once when prompted.

If any of these is false, stop and tell the user — don't try to work around them.

## Capture target: Netlify deploy preview, not localhost

**Default to the Netlify deploy preview URL.** It's already built, already deployed, and matches what reviewers see when they click through. The URL pattern is predictable from the PR number and the Netlify site slug:

```
https://deploy-preview-<PR_NUMBER>.folio.concentro.io
```

The Netlify check (`netlify/partner-concentro/deploy-preview`) links to a `*.netlify.app` host; ignore that link and use the `folio.concentro.io` alias above — it is the only preview origin the stage API's CORS allowlist admits.

**Wait for the deploy preview to be ready before capturing.** Run `gh pr checks <PR_NUMBER> --json name,state` and find the entry whose name starts with `netlify/`. If `state == "SUCCESS"`, capture immediately. If `PENDING` or `IN_PROGRESS`, tell the user "deploy preview building, polling for up to 5 min", then poll every 30 seconds. If `FAILURE`, stop and report — don't fall back to localhost silently.

**Use localhost only when the user explicitly asks for it** (e.g. "screenshot from localhost", "use the dev server", or when capturing uncommitted work that isn't pushed yet). In that case, swap the deploy-preview URL for `http://localhost:5173` and start `npm run dev` if it's not running.

## The shape of the output

A single PNG saved to `.playwright-mcp/screenshots/<slug>-<unix-ts>.png` (gitignored) and embedded in the PR description above the second top-level (`## `) section header. By convention the first section is `## Summary` or `## TL;DR`; the screenshot lands between that section's body and the next `## ` heading. Reference an existing PR like #1011 for the target shape — GitHub renders uploaded files as `<img src="https://github.com/user-attachments/assets/…" />` markdown.

## Workflow

The skill runs five phases. Each phase has a clear exit condition; if you can't satisfy it, stop and ask the user rather than guessing your way through.

### Phase 1 — Verify prerequisites

Run in parallel:
- `gh pr view --json url,number,body,title` — confirm an open PR. If none, stop: "no open PR for branch X — push and open one first."
- `gh pr checks <PR_NUMBER> --json name,state,link` — find the Netlify deploy-preview check.
- `git diff origin/main...HEAD --name-only` — list changed files for diff analysis.
- Confirm the test-user credentials are present in `.env` (use whatever variable names your project uses, e.g. `APP_USER_EMAIL` / `APP_USER_PASSWORD`).

Hold the PR JSON in mind — you'll need `url` and `number` for navigation, and `body` for the second-header insertion point.

### Phase 2 — Identify the target

From the changed files, propose **one** target. The most visually-significant change is usually:
- A changed `*.tsx` under `app/components/` or `app/routes/` (route files outrank leaf components when both exist).
- A new component (a wholly new visual is usually more interesting than a tweak).
- A file the PR title/body already calls out by name.

When the diff is ambiguous, look at the PR body for hints — what does the user say the change is? That's a stronger signal than the file list alone.

Send the user one short message proposing:
- **Route**: e.g. `/dashboard/settings` or `/` (the home/sidebar surface)
- **Viewport**: default `1280×800`. Use `375×667` only if the PR is explicitly mobile.
- **Setup**: any clicks/hovers needed to make the change visible (e.g. "expand the sidebar", "open the variable popover").
- **Why this**: one-line rationale tying the route back to the diff.

Wait for confirmation. The user may correct the route, viewport, or setup steps.

### Phase 3 — Wait for the deploy preview (or start localhost)

**Default path (deploy preview):** confirm the Phase 1 `gh pr checks` output has a `netlify/partner-concentro/deploy-preview` entry in `SUCCESS`, then open `https://deploy-preview-<PR_NUMBER>.folio.concentro.io`. Never use the raw `deploy-preview-<N>--partner-concentro.netlify.app` host the check links to: the stage API's CORS allowlist admits only the `folio.concentro.io` alias, so sign-in there fails with "Login attempt timed out" (see the woodrow README, "Deploy Previews").

- `SUCCESS` → continue to Phase 4 with that URL.
- `PENDING` / `IN_PROGRESS` → tell the user "deploy preview building, polling up to 5 min", poll `gh pr checks` every 30s.
- `FAILURE` → stop. Report the deploy-log link from the check; don't silently fall back.
- No Netlify check at all → ask the user; the repo may not have deploy previews configured.

**Localhost fallback (only if the user explicitly asks):** `curl -s -o /dev/null -w "%{http_code}" http://localhost:5173` — if not 200, start `npm run dev` with `run_in_background: true` and poll until it responds. If `npm run dev` errors out, stop and surface it.

### Phase 4 — Drive the capture

One Playwright session, sequential calls:

1. `browser_resize` to the chosen viewport.
2. `browser_navigate` to the chosen base URL (deploy preview or `http://localhost:5173`).
3. Authenticate. Read the test-user email/password from `.env` (variable names vary by project). Fill the login form, submit, and `browser_wait_for` the app shell to render (look for sidebar text, a stable element, or a known route response).
4. `browser_navigate` to the chosen route on the same host (don't switch hosts mid-flow — relative paths only).
5. Run the setup interactions (clicks, hovers) the user confirmed.
6. `browser_wait_for` a UI element from the actual change — not a generic spinner. If the change is "stripe in the sidebar", wait for the sidebar to be present, not for the network to idle.
7. `browser_take_screenshot` saving to `.playwright-mcp/screenshots/<slug>-<unix-ts>.png`. `<slug>` derives from the branch name (e.g. `jake-env-colors` → `jake-env-colors`). Create the directory if missing.

Why one session: switching browsers loses the auth state, and the screenshot benefits from the same viewport + cookies that the GitHub upload step needs.

### Phase 5 — Upload via the same browser session

`gh` CLI cannot upload images, but GitHub's web editor accepts file uploads and rewrites them into `user-attachments` URLs server-side. Reuse the existing Playwright session:

1. `browser_navigate` to the PR URL.
2. Confirm GitHub is signed in (look for the avatar in the top-right). If not, stop and ask the user to sign in once — don't try to automate GitHub login.
3. Click the kebab (`···`) on the PR's opening comment → **Edit**. The body becomes a `<textarea>`.
4. Determine the insertion point. Read the textarea's value, find the **second** `\n## ` (the second top-level heading). Insert a blank line plus a placeholder marker like `__SCREENSHOT_HERE__` immediately above that heading, set the textarea's value, and dispatch an `input` event so React/GitHub registers the change.
5. Move the cursor to the placeholder line (`browser_evaluate` to set `selectionStart`/`selectionEnd` to the placeholder's position).
6. Trigger `browser_file_upload` on the file input GitHub provides (the textarea has an associated `<input type="file" multiple>` — Playwright's file_upload finds it). Upload the screenshot path.
7. `browser_wait_for` the `[Uploading …]` placeholder text to be replaced with a `https://github.com/user-attachments/assets/…` URL. Then remove the `__SCREENSHOT_HERE__` marker if any of it remains.
8. Click **Save**.
9. Verify with `gh pr view --json body` — the body should now contain a `user-attachments` URL above the second `## ` heading.

If any step fails, take a debug screenshot of the GitHub editor state, save it next to the original capture, and report what you saw — don't silently retry.

## Path conventions

- `.playwright-mcp/screenshots/<slug>-<unix-ts>.png` — output. Already covered by `.playwright-mcp/` in `.gitignore`.
- `.env` — login credentials. The repo's settings already pre-allow `Read(./.env)`.

## Things that go wrong, and what to do

| Symptom | Likely cause | Recovery |
|---|---|---|
| `gh pr view` returns nothing | No open PR for current branch | Stop; ask the user to push and open one |
| Netlify check is `FAILURE` | Build broken on this branch | Stop; report the deploy-log URL — fix the build first |
| Netlify check stays `PENDING` past 5 min | Slow build, queue, or stuck | Ask the user whether to keep waiting or fall back to localhost |
| No `netlify/*` check in `gh pr checks` | Repo doesn't have deploy previews | Ask the user; offer localhost as alternative |
| `localhost:5173` doesn't respond (fallback path) | Port mismatch or build error | Read the dev server's stdout; if build error, stop and report; if different port, ask the user |
| Login form selectors changed | App login UI was redesigned | Stop; ask the user to walk through it once so you can update the skill |
| GitHub edit textarea never appears | Not signed into GitHub in this session | Ask the user to sign in once; resume |
| `[Uploading …]` placeholder never replaced | Upload failed silently (file too large, GitHub flake) | Take a debug screenshot of the editor; abort; report |
| Two `## ` headers can't be found | PR body is short / unconventional | Ask the user where to insert (top of body? after first paragraph?) |

## What this skill explicitly does NOT do

- Capture multiple screenshots. One signature shot per PR. If the user wants several, they're outside this skill — use Playwright MCP directly.
- Edit a different PR than the current branch's. The branch determines the PR.
- Replace or delete an existing screenshot already in the PR description. It inserts; the user can clean up duplicates if needed.
- Run any kind of test suite, lint, typecheck, or build. This is purely visual capture + upload.
- Commit anything to the repo. The screenshot lives only in the gitignored output dir and on GitHub's CDN.

## Why these choices

**Deploy preview, not localhost.** Localhost adds two flaky steps (start dev server, hope it builds) and produces a screenshot that may not match what reviewers see. The deploy preview is already running, already auth'd against the real backend, and matches reviewer reality. Localhost is reserved for capturing uncommitted work.

**One screenshot, not many.** A reviewer scanning a PR has limited attention. One shot of the signature change does more work than a gallery — and forces the skill to make a judgment call about what matters, which is the user-facing point of having a skill at all.

**Same Playwright session for both capture and upload.** Two browsers means two auth states and two contexts to manage. Reusing the session keeps state simple and proves out the upload immediately after capture (no "saved a file, now what" dead end).

**Insert above the second `## `, not at the top.** TL;DR/Summary first, then the visual. The visual reinforces the prose; the prose isn't a caption for the visual.

**Stop on ambiguity, don't guess.** Picking the wrong screenshot wastes the user's review time worse than asking does. The skill is fine to be conversational at the target-selection step.
