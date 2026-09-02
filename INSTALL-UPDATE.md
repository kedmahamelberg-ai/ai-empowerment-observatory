# AIEO layout + full-body classification update

This is a focused update for the current `ai-empowerment-observatory` repository. It contains only the files that changed, so it will not replace unrelated repository data.

## Upload

1. Unzip the package.
2. Open the inner `ai-empowerment-observatory-main` folder.
3. On macOS, press **Command + Shift + .** so the `.github` folder is visible.
4. In the GitHub repository root, choose **Add file → Upload files**.
5. Drag all of these top-level items together: `.github`, `data`, `edu`, `scripts`, `validation`, `index.html`, `site.css`, and `site.js`.
6. Commit directly to `main` with a message such as: `Improve responsive signal layout and classify from article bodies`.

Uploading the folders together matters: it keeps the frontend, corrected W35 data, validation record, classifier, and weekly workflow in the same commit.

## Verify and publish

1. Wait for **Observatory Repository Integrity** to turn green. Do not rerun an older failed run; the new push creates a fresh run.
2. Open **Actions → Publish Observatory Release → Run workflow → main**.
3. Do **not** rerun the Weekly Observatory Pipeline. The current W35 collection is already complete.
4. When publishing is green, hard-refresh the main page and `/edu/`.

## Expected W35 signal

- People gaining: **20 of 111 — 18.0%**
- People losing ground: **3 of 111 — 2.7%**
- Not clear yet: **88 of 111 — 79.3%**
- “A mixed picture” and “Not everyone benefits” remain marked as awaiting full multi-label review; they are not displayed as false zeros.

The three supplied article bodies were reviewed and are no longer treated as unclear: the Thomson Reuters and AI Model Harness developments are “People gaining”; ChatGPT advertising in France is “People losing ground.” These AI-assisted source-body corrections are documented separately from owner-adjudicated gold labels.

Future weekly classification now waits for legal article-body extraction and uses the full body when available. It falls back transparently to a summary, snippet, or headline only when body text cannot be collected.
