# Observatory → Brief publication handoff

Every successful `Publish Observatory Release` deployment calls `Notify Brief After Publication`.
This includes the weekly pipeline, manual republication and corrections using the same publisher.
The build records the exact export's release ID and public-content fingerprint; the notifier waits
for that pair at the public URL before dispatching `Follow Observatory Editions` on `aieo-brief/main`.
It never dispatches following a failed build/deployment or an old cached export.

The Brief's existing hourly schedule remains the backup. Its existing concurrency group and
edition comparison serialize updates and skip editorial work when both live editions already
match. This is edition-independent: there are no dates to update each week. A dispatch is only
an accepted request, not proof of downstream publication. Check the linked Brief run for completion.
A failed notification makes the publication workflow visibly fail *after* the Observatory has
successfully deployed, rather than silently reporting a successful handoff.

## One-time GitHub App setup (required before activation)

Create a private GitHub App owned by `kedmahamelberg-ai`, with webhooks disabled and **Actions:
read and write** as its only writable repository permission (Metadata read is automatic).
Install it on **only `aieo-brief`**. GitHub's Actions permission can operate workflows throughout
that repository; GitHub does not offer permission scoped to just one workflow. The code always
calls the fixed `follow-observatory.yml` endpoint and the fixed `main` branch.

In the **Observatory** repository's Actions settings, save:

- Variable `BRIEF_HANDOFF_APP_CLIENT_ID`: the App's Client ID.
- Secret `BRIEF_HANDOFF_APP_PRIVATE_KEY`: the App's generated PEM private key.

Do not commit the key or paste it into an issue, chat or log. The official GitHub token action
creates a short-lived installation token restricted to `aieo-brief` and Actions write for each
handoff, and revokes it after the job. No personal access token with a recurring expiry is needed.
The App must remain installed and its private key valid. This integration adds no external paid
service; existing GitHub Actions usage and the Brief's existing editorial budgets still apply.

## Verification and recovery

Run `python3 -m unittest discover -s tests -p 'test_brief_publication_handoff.py' -v`.
After configuring the App, run **Notify Brief After Publication** manually with `release_id` and
`public_content_sha256` from the live `/data/brief/current.json` export. Confirm its live check and
dispatch succeed, and that a new **Follow Observatory Editions** run appears in the Brief repository.
If the user already updated the Brief, the triggered run should succeed without regeneration.

If notification fails, fix the named App configuration/permission or wait for the public export,
then rerun the failed job or use the manual notifier. Do not rerun the costly weekly collection
just to resend a notification. Scheduled Brief checks continue even if this App is unavailable.

References:
- https://docs.github.com/en/actions/concepts/security/github_token
- https://github.com/actions/create-github-app-token
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
