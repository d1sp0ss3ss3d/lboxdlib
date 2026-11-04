# Packaging hardening notes

This project uses a standard `src/` layout and a setuptools build backend:

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
```

The package discovery rule is:

```toml
[tool.setuptools]
package-dir = {"" = "src"}

[tool.setuptools.packages.find]
where = ["src"]
```

## Recommended local development install

For normal local development, use dependencies and the `dev` extra:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -U pip setuptools wheel
python -m pip install -e ".[dev]"
python -m pytest -q
```

You can also run a compile check:

```bash
python -m compileall -q -x '(^|/)\._' src tests scripts
```

The `-x '(^|/)\._'` exclusion avoids macOS AppleDouble/resource-fork files if they exist on an external or non-native filesystem.

## Do not use the sandbox validation flags locally

The earlier sandbox validation used commands like:

```bash
python -m pip install -e . --no-deps --no-build-isolation
```

Those flags were only used to avoid network/dependency installation inside the sandbox. They are not the correct local-development command.

- `--no-deps` means runtime and dev dependencies are not installed.
- `--no-build-isolation` means pip will not create an isolated build env containing `setuptools` and `wheel`.
- If `setuptools` is missing from the active virtual environment, `--no-build-isolation` can produce `BackendUnavailable: Cannot import 'setuptools.build_meta'`.

Use this locally instead:

```bash
python -m pip install -e ".[dev]"
```

## macOS `._*.py` files

If the project lives on an external volume or a non-native filesystem, macOS may create AppleDouble metadata files such as:

```text
src/lboxdlib/._auth.py
src/lboxdlib/modules/._review_from_page.py
```

These are not Python source files, but they end in `.py`, so `compileall` may try to compile them and report:

```text
SyntaxError: source code string cannot contain null bytes
```

Clean them with:

```bash
find . -name '._*' -delete
find . -name '__MACOSX' -type d -prune -exec rm -rf {} +
```

This repository's `.gitignore`, `MANIFEST.in`, and package-data exclusions now ignore/exclude those files, but if macOS creates them after checkout/unzip, remove them locally before running broad compile/build commands.

## Build checks

After installing development dependencies, useful checks are:

```bash
python -m compileall -q -x '(^|/)\._' src tests scripts
python -m pytest -q
python -m build
```

Or, without installing `build` separately if the dev extra is installed:

```bash
python -m pip wheel . -w dist
```

## Public API import contract

The test suite locks the most important public import paths:

```python
from lboxdlib import FilmHydrator, ListManager, hydrate_lids
from lboxdlib import fetch_reviews, fetch_users, select_rated_viewings
from lboxdlib import like_review, follow_user, unfollow_user, batch_unfollow_users
from lboxdlib import user_follows, resolve_user_slug
```

and facade imports:

```python
from lboxdlib.lists import FilmHydrator, ListManager, hydrate_lids
from lboxdlib.fetch import fetch_activity, fetch_reviews, fetch_users
from lboxdlib.select import select_rated_viewings
from lboxdlib.actions import like_review, follow_user, unfollow_user, batch_unfollow_users
from lboxdlib.relationships import user_follows, resolve_user_slug
```

## Notes before publishing

Before publishing outside local/private use, decide:

- whether `license = { text = "Proprietary" }` should become a standard SPDX license expression
- whether any runtime dependencies should move into optional extras
- whether scripts should remain plain repo scripts or become console entry points
- whether obsolete/experimental modules should be excluded from the wheel after internal reorganization
