# Releasing weightclass

weightclass is published to PyPI, and a Homebrew formula in
[`ictechgy/homebrew-tap`](https://github.com/ictechgy/homebrew-tap) installs the
PyPI source distribution.

Releasing is deliberately a human action. Pushing a tag is the approval; nothing
publishes on a merge to `main`.

## Version policy

- The version lives in exactly one place, `src/weightclass/__init__.py`.
  `pyproject.toml` reads it through `[tool.setuptools.dynamic]`.
- Tags are `v<version>`, e.g. `v0.1.0`. The release workflow refuses to publish
  when the tag and the declared version disagree, because PyPI never lets a
  version number be reused.
- Semantic versioning, with `0.x` treated as unstable: while the major version
  is `0`, a minor bump may break the command line, the policy schema, or an exit
  code. This project has already changed all three.
- Every breaking change is recorded in its commit body as `BREAKING CHANGE:` and
  listed in the release notes.

## One-time setup

PyPI publishing uses [Trusted Publishing](https://docs.pypi.org/trusted-publishers/),
so no API token is stored in this repository or in GitHub secrets. Register the
publisher once, on PyPI:

1. Sign in to PyPI, then open **Your projects → weightclass → Publishing**. For
   a name that has never been published, use
   **Account settings → Publishing → Add a pending publisher** instead.
2. Enter:
   - Owner: `ictechgy`
   - Repository: `weightclass`
   - Workflow: `release.yml`
   - Environment: `pypi`
3. Optionally, in this repository's **Settings → Environments → pypi**, add
   yourself as a required reviewer. The release then pauses for an explicit
   approval between the verification job and the upload.

## Cutting a release

1. Update `__version__` in `src/weightclass/__init__.py` and merge that change.
2. Confirm `main` is green and reproduce the gates locally:

   ```sh
   PYTHONPATH=src python3 -W error::ResourceWarning -m unittest discover -s tests
   ruff check src tests && ruff format --check src tests && mypy
   release_dist_dir=$(mktemp -d "${TMPDIR:-/tmp}/weightclass-release.XXXXXX")
   python3 -m build --outdir "$release_dist_dir"
   twine check --strict "$release_dist_dir"/*.whl "$release_dist_dir"/*.tar.gz
   python3 tests/verify_distribution_isolation.py \
     --source . --dist-dir "$release_dist_dir" --run-sdist-tests
   ```

   The release is also blocked unless CI's macOS Python 3.10 and 3.14 triage
   process-group/FIFO boundary jobs pass. After building, verify the wheel's
   metadata version and an installed `wclass --version` against
   `weightclass.__version__`.

3. Tag the merged commit and push the tag:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. The `Release` workflow re-runs every gate against the tagged commit, checks
   the tag against the declared version, builds exactly one wheel and one sdist,
   verifies them, and uploads only those two patterns as an immutable unverified
   artifact before executing their extracted tests locally. The local copies are
   fingerprinted again after those tests. The immutable artifact then crosses a
   job boundary to a fresh runner, which installs no project tooling and executes
   only the standard-library isolation verifier. Publication is gated on that
   check and consumes the same immutable artifact instead of re-uploading mutable
   filesystem paths. Watch the workflow finish before continuing.

5. Verify the published artifact from a clean environment:

   ```sh
   uv tool install weightclass    # or: pipx install weightclass
   printf '%s' 'Fix a typo.' | wclass classify
   ```

## Updating the Homebrew formula

The formula installs the PyPI sdist, so it can only be updated after step 4.

1. Read the canonical source URL and its checksum from PyPI. Homebrew's audit
   rejects the `/packages/source/w/...` redirect form, so take the hashed URL
   that PyPI itself reports:

   ```sh
   VERSION=0.1.0
   curl -s "https://pypi.org/pypi/weightclass/${VERSION}/json" | python3 -c '
   import json, sys
   for f in json.load(sys.stdin)["urls"]:
       if f["packagetype"] == "sdist":
           print("url   :", f["url"])
           print("sha256:", f["digests"]["sha256"])'
   ```

2. In `ictechgy/homebrew-tap`, update `Formula/weightclass.rb` with the new
   `url` and `sha256`, then commit.
3. Verify before pushing the tap change. `brew audit` and `brew style` only
   apply tap rules to a file that already sits inside a tap, so copy it in
   first:

   ```sh
   cp packaging/homebrew/weightclass.rb "$(brew --repository ictechgy/tap)/Formula/"
   brew style ictechgy/tap
   brew audit --strict --tap=ictechgy/tap weightclass
   brew install --build-from-source ictechgy/tap/weightclass
   brew test ictechgy/tap/weightclass
   ```

`packaging/homebrew/weightclass.rb` in this repository is the source of truth
for that formula; copy it into the tap rather than editing the tap by hand.

## If a release goes wrong

A PyPI version can be yanked but never replaced or deleted. Yank the bad
version, fix the defect, and release the next patch version. Do not attempt to
reuse a version number.
