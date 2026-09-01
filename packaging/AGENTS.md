# Packaging — `packaging/`

Scope: the distribution artifacts kept in this repository. The root
[`AGENTS.md`](../AGENTS.md) still applies. The full procedure lives in
[`../RELEASING.md`](../RELEASING.md); this file records the rules that are easy
to get wrong.

## The formula is a copy, not the original

`packaging/homebrew/weightclass.rb` is the **source of truth** for the formula
in `ictechgy/homebrew-tap`.

- Edit it here, then copy the file into the tap. Never edit the tap by hand;
  the two will drift and the tap has no other record of intent.
- `brew style` and `brew audit` only apply tap rules to a file that already sits
  inside a tap. Verifying the copy in this repository proves nothing about tap
  compliance — copy it in first, then verify.

## Ordering

The formula installs the PyPI sdist, so it can only be updated **after** the
release workflow has published that version. A formula pointing at an unbuilt
version fails for everyone who runs `brew upgrade`.

## Use the hashed URL

Homebrew's audit rejects the `/packages/source/w/...` redirect form. Take the
hashed URL that PyPI itself reports:

```sh
curl -s "https://pypi.org/pypi/weightclass/${VERSION}/json" | python3 -c '
import json, sys
for f in json.load(sys.stdin)["urls"]:
    if f["packagetype"] == "sdist":
        print(f["url"]); print(f["digests"]["sha256"])'
```

## Verification before the tap PR

Run all of these with the file already copied into the tap:

```sh
brew style "$(brew --repository ictechgy/tap)/Formula/weightclass.rb"
brew audit --strict --tap=ictechgy/tap weightclass
brew upgrade --build-from-source ictechgy/tap/weightclass
brew test ictechgy/tap/weightclass
```

Whole-tap `brew style` currently reports one unrelated pre-existing
`relay.rb` component-order warning. Check the weightclass file specifically so
that warning does not hide a real one.

## Test an exact entrypoint

Plain `wclass` on a maintainer machine can resolve to the user-level `uv` tool
rather than the Homebrew build. When packaging provenance matters, invoke the
exact path:

```sh
"$(brew --prefix)/bin/wclass" --version
"$HOME/.local/bin/wclass" --version
```

## Versions are permanent

A PyPI version can be yanked but never replaced, reused, or deleted. A defect
needs the next version, never a re-upload. The same holds for release tags:
retain a failed candidate tag and never move it.
