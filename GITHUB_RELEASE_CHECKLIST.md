# GitHub release checklist — csv-safe-restore 0.1.0a1

This is the step-by-step plan for the first public **alpha** release on GitHub.
Every step is done by the maintainer, by hand.

**Progress (2026-11-23):** steps 0–4 are done. The code is pushed to a
**private** repository, https://github.com/parisaghm/csv-safe-restore, and all
19 CI jobs pass on Linux, Windows and macOS. The release notes are ready in
[docs/release-notes-v0.1.0a1.md](docs/release-notes-v0.1.0a1.md). Not done yet:
making the repository public, the tag, the GitHub release, and the user check
(steps 5–6). Nothing is on PyPI.

Tick each box as you go. If a step fails, stop and fix it before moving on.

Commands are for Linux and macOS. On Windows, use `.venv\Scripts\python`
instead of `.venv/bin/python`.

## 0. Decisions to make first

- [ ] **Repository owner.** Decide which GitHub account or organisation will
      host the project. Below it is called `OWNER`.
- [ ] **Commit identity — this becomes public.** Every git commit records a
      name and an email, and both are visible to anyone once you push. The
      package itself names no author (`pyproject.toml` has no `authors`, and
      `LICENSE` says "The csv-safe-restore contributors"). If you also want
      your commits to be anonymous, use your GitHub "noreply" address, from
      GitHub → Settings → Emails → "Keep my email addresses private":

      ```console
      git config user.name "your-github-username"
      git config user.email "ID+your-github-username@users.noreply.github.com"
      ```

      Run these inside the project folder after `git init` (step 2), so they
      apply to this project only.
- [ ] **Version.** The release is `0.1.0a1` (`a1` = first alpha, following
      PEP 440). It is set in `src/csv_safe_restore/__init__.py`. Keep it
      unless you have a reason to change it; if you do, change it there and
      in `CHANGELOG.md`, `README.md` and this checklist.
- [ ] **Copyright holder and author.** Keep them neutral, as they are now:
      `LICENSE` says "Copyright (c) 2026 The csv-safe-restore contributors",
      and `pyproject.toml` has no `authors` field. No change needed.

## 1. Final local check

From the project folder:

```console
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest
.venv/bin/python -X warn_default_encoding -W error::EncodingWarning -m pytest -p no:cacheprovider
```

- [ ] All tests pass in both runs (292 at the time of writing).

Optional, needs `pip install daff csv-diff datacompy uncorrupt pandas`:

```console
.venv/bin/python benchmarks/compare_tools.py --markdown benchmarks/results.md
```

- [ ] It ends with `csv-safe-restore made 0 false repairs.` Any other result
      is a release blocker.

## 2. Create the local git repository

```console
cd csv-safe-restore
git init -b main
# set your commit identity now (see step 0)
git add -A
git status
```

- [ ] `git status` lists **no** `.venv/`, `.hypothesis/`, `.pytest_cache/`,
      `__pycache__/`, `dist/` or `build/`. They are excluded by `.gitignore`.
      If any appear, stop and fix `.gitignore` first.
- [ ] `.gitattributes` is listed. It keeps the CSV files byte-for-byte the same
      on Windows, which CI relies on.
- [ ] Replace the `OWNER` placeholder in the README install commands with the
      real account name:

      ```console
      grep -rn "OWNER" README.md
      ```

      Edit each line it prints. (This checklist also says `OWNER`; that is
      fine.)
- [ ] Commit:

      ```console
      git add -A
      git commit -m "csv-safe-restore 0.1.0a1: first public alpha"
      ```

## 3. Create the GitHub repository

On github.com → **New repository**:

- [ ] Owner: `OWNER`. Name: `csv-safe-restore`.
- [ ] Visibility: **Public** (GitHub Actions is free for public repositories).
- [ ] Do **not** add a README, `.gitignore` or license there; the project
      already has them, and GitHub's versions would conflict.
- [ ] Description, for example: "Restore spreadsheet-damaged identifiers in
      CSV files from a clean reference copy. Never guesses."
- [ ] Topics, for example: `csv`, `excel`, `data-quality`, `leading-zeros`,
      `data-engineering`.

Then connect and push (GitHub shows the same commands):

```console
git remote add origin https://github.com/parisaghm/csv-safe-restore.git
git push -u origin main
```

- [ ] The push succeeded and the files are visible on GitHub.
- [ ] The README renders correctly on the repository page, including the
      tables and the *Safety limitations* section.

Recommended repository settings (Settings tab):

- [ ] **Issues**: enabled, so testers can report problems.
- [ ] **Private vulnerability reporting**: enable it (search the Settings
      page for that name), so a serious problem can be reported privately.
- [ ] **Branch protection for `main`** (optional): after CI has run once
      (step 4), add a rule that requires its status checks, for example the
      `test (...)` and `install (...)` jobs, to pass before a pull request
      can be merged.

## 4. Verify CI

The push to `main` starts the **tests** workflow. Open the **Actions** tab.

It runs 19 jobs:

| Job | What it checks | Count |
| --- | --- | --- |
| `test` | Full test suite, then the command line on the example files: exact exit codes, output identical to `examples/repaired.csv`, existing output not replaced, inputs unchanged | 12: Ubuntu, Windows and macOS × Python 3.11, 3.12, 3.13 and 3.14 |
| (inside `test`, Ubuntu + 3.14 only) | Tests again, failing on any file access that relies on the platform's default encoding | — |
| `build` | Builds the wheel and source archive, checks them with `twine check --strict`, uploads them as the `dist` artifact | 1 |
| `install` | Installs the built wheel into a clean environment and runs it on the examples | 6: Ubuntu, Windows and macOS × Python 3.11 and 3.14 |

- [ ] All 19 jobs are green.

If a job fails (the first run found one Windows-only problem, since fixed):

1. Open the failing job and read the first error.
2. Do not just rerun it, and do not skip or delete the failing test.
3. Fix the cause. If it is a real bug, add a test that reproduces it.
4. Push the fix and wait for all jobs to be green again.
5. If a problem cannot be fixed now, document it in the README under
   *Safety limitations* and in `CHANGELOG.md` before releasing.

**About random tests.** Some tests use Hypothesis, which generates new random
data on every run. A failure that shows `Falsifying example:` is a real
counterexample, even if the next run passes. Copy the example into a normal
test, fix the bug, and keep the test. (This is how one bug was found during
the pre-release review.)

- [ ] After a fix, also run the workflow manually once: **Actions → tests →
      Run workflow**.

## 5. Publish the alpha

Only when all CI jobs are green.

0. **Make the repository public** (Settings → General → Danger Zone →
   Change visibility → Public). Until then nobody else can install the alpha:
   the README install commands only work for public repositories. Before
   switching, look through the file list on GitHub once more; everything in
   the repository's history becomes visible.

1. `CHANGELOG.md` is dated 2026-11-23. If you publish on a later day,
   change the date, then commit and push:

   ```console
   git commit -am "Set the 0.1.0a1 release date"
   git push
   ```

   - [ ] CI is green for the commit you are about to tag.

2. Tag the release and push the tag:

   ```console
   git tag -a v0.1.0a1 -m "csv-safe-restore 0.1.0a1 (alpha)"
   git push origin v0.1.0a1
   ```

   - [ ] The tag push starts the workflow again. Its `build` job checks that
         the tag `v0.1.0a1` matches the package version. All jobs are green.

3. Download the built files: open that workflow run → **Artifacts** →
   `dist`. It contains `csv_safe_restore-0.1.0a1-py3-none-any.whl` and
   `csv_safe_restore-0.1.0a1.tar.gz`.

4. On GitHub → **Releases → Draft a new release**:

   - [ ] Tag: `v0.1.0a1`. Title: `csv-safe-restore 0.1.0a1 (alpha)`.
   - [ ] Tick **Set as a pre-release**. (This is what marks it as an alpha
         on GitHub.)
   - [ ] Attach the wheel and the `.tar.gz` from step 3.
   - [ ] Release notes: copy everything in
         [docs/release-notes-v0.1.0a1.md](docs/release-notes-v0.1.0a1.md)
         into the description box. Use the **Preview** tab to check it.

   - [ ] Publish the release.

## 6. Check the installation instructions as a user

On a computer or virtual environment that has never had the project:

```console
python -m venv try-alpha
try-alpha/bin/python -m pip install "git+https://github.com/parisaghm/csv-safe-restore@v0.1.0a1"
try-alpha/bin/csv-safe-restore --version
```

- [ ] It prints `csv-safe-restore 0.1.0a1`.

Also try the attached wheel (copy its link from the release page):

```console
try-alpha/bin/python -m pip install --force-reinstall "https://github.com/parisaghm/csv-safe-restore/releases/download/v0.1.0a1/csv_safe_restore-0.1.0a1-py3-none-any.whl"
```

- [ ] It installs and `--version` works.
- [ ] Download `examples/original.csv` and `examples/edited.csv` from the
      repository, then run the README's `check` and `repair` commands on them.
      The repaired file is identical to `examples/repaired.csv`.
- [ ] If possible, repeat on Windows (`try-alpha\Scripts\python`) and macOS.

## 7. After the release

- [ ] Watch the issues. Treat any report of a **wrong repair** as the highest
      priority, as `CONTRIBUTING.md` says: reproduce it, add a test, fix it,
      and say in the release notes what was affected.
- [ ] If a serious problem is found in the alpha, edit the release notes on
      GitHub to warn about it at the top, then publish a fixed `0.1.0a2`.
      Do not delete or move the `v0.1.0a1` tag: people may have installed it.

## Not part of this release

- **PyPI.** Nothing is uploaded to PyPI in this release. That is a separate
  decision for later, after alpha feedback. It would need a PyPI account, a
  `[project.urls]` section in `pyproject.toml` pointing at the repository,
  and an upload step. Because the version is marked as an alpha, `pip` would
  not install it unless the user asks for pre-releases.
- **Real Excel testing.** Excel behaviour is still based on Microsoft's
  documented rules and a simulation. Testing against a real Excel on Windows
  and macOS is the most useful next step for accuracy.
