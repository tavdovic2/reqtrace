# Repository settings

Some protections cannot live in the repository. The settings below are made by hand in
the GitHub web interface, by a user with admin rights on `tavdovic2/reqtrace`. They
work together with [.github/workflows/ci.yml](../.github/workflows/ci.yml).

Apply them **after** the pull request that adds the `docker` job is merged. A status
check can only be selected once it has run in the repository during the last 7 days.

## 1. Ruleset for `main`

Without it, the merge button is active before CI has finished, and `main` can be
force-pushed or deleted.

1. Open the repository, then **Settings** → **Rules** → **Rulesets** (sidebar, under
   "Code and automation").
2. Click **New ruleset** → **New branch ruleset**.
3. **Ruleset Name**: `main`.
4. **Enforcement status**: **Active**.
5. **Bypass list**: leave empty, so the rules apply to admins as well.
6. **Target branches**: **Add target** → **Include default branch**. This is `main`.
7. Under **Branch rules**, set:
   - **Restrict deletions**: checked. It is on by default; leave it.
   - **Block force pushes**: checked. It is on by default; leave it.
   - **Require a pull request before merging**: checked.
     - **Required approvals**: `0`. GitHub does not let authors approve their own pull
       requests, so with one maintainer any other value blocks every merge.
     - If **Allowed merge methods** is shown, keep **Merge** selected.
   - **Require status checks to pass**: checked.
     - Click **Add checks**, type each name and select it: `backend`, `frontend`,
       `docker`. If a source can be chosen, pick **GitHub Actions**.
     - These are the job names in `ci.yml`. If a job is renamed, update the ruleset as
       well. Otherwise the old check name stays required and pull requests can no
       longer be merged.
8. Click **Create**.

## 2. Pull request merge options

1. **Settings** → **General**, then scroll down to **Pull Requests**.
2. Check **Allow merge commits**.
3. Further down in the same section, check **Allow auto-merge**.

These checkboxes save as soon as they are clicked. The other merge methods (squash,
rebase) are not needed by this workflow.

With auto-merge enabled, a pull request can be set to merge itself once the required
checks pass: on the pull request, click **Enable auto-merge**, choose **Create a merge
commit** and confirm.

## 3. Read-only workflow token by default

1. **Settings** → **Actions** → **General**, then scroll down to **Workflow
   permissions**.
2. Select **Read repository contents and packages permissions**.
3. Leave **Allow GitHub Actions to create and approve pull requests** unchecked.
4. Click **Save**.

`ci.yml` already asks for `contents: read` only. This setting makes read-only the
default for any workflow added later that does not declare its own permissions.

## Checking that it works

- Open a pull request. Its merge button should stay blocked until `backend`, `frontend`
  and `docker` pass.
- Push twice in quick succession to a branch with an open pull request. The **Actions**
  tab should show one run per push, triggered by `pull_request`, and the older run
  should be cancelled.
- `git push --force` to `main` should be rejected.
