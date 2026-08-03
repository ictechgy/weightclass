# Releasing weightclass

weightclass is published to PyPI, and a Homebrew formula in
[`ictechgy/homebrew-tap`](https://github.com/ictechgy/homebrew-tap) installs the
PyPI source distribution.

Releasing is deliberately a human action. Pushing a tag is the approval; nothing
publishes on a merge to `main`.

## Version policy

- The version lives in exactly one place, `src/weightclass/__version__`.
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
   PYTHONPATH=src python3 -m unittest discover -s tests
   ruff check src tests && ruff format --check src tests && mypy
   python3 -m build && twine check --strict dist/*
   ```

3. Tag the merged commit and push the tag:

   ```sh
   git tag v0.1.0
   git push origin v0.1.0
   ```

4. The `Release` workflow re-runs every gate against the tagged commit, checks
   the tag against the declared version, builds the wheel and sdist, and uploads
   them to PyPI. Watch it finish before continuing.

5. Verify the published artifact from a clean environment:

   ```sh
   uv tool install weightclass    # or: pipx install weightclass
   printf '%s' 'Fix a typo.' | wclass classify
   ```

## Updating the Homebrew formula

The formula installs the PyPI sdist, so it can only be updated after step 4.

1. Take the checksum of the published source distribution:

   ```sh
   VERSION=0.1.0
   URL="https://files.pythonhosted.org/packages/source/w/weightclass/weightclass-${VERSION}.tar.gz"
   curl -fsSL "$URL" | shasum -a 256
   ```

2. In `ictechgy/homebrew-tap`, update `Formula/weightclass.rb` with the new
   `url` and `sha256`, then commit.
3. Verify from a clean shell:

   ```sh
   brew update
   brew install ictechgy/tap/weightclass
   brew test weightclass
   ```

`packaging/homebrew/weightclass.rb` in this repository is the source of truth
for that formula; copy it into the tap rather than editing the tap by hand.

## If a release goes wrong

A PyPI version can be yanked but never replaced or deleted. Yank the bad
version, fix the defect, and release the next patch version. Do not attempt to
reuse a version number.
