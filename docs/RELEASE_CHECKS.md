# Release checks

Before sharing or tagging a release, run the release validator from the project root:

```bash
python -m pip install -e ".[dev]"
python scripts/release_check.py
```

The release check:

1. removes macOS AppleDouble metadata files such as `._auth.py`;
2. clears build artifacts;
3. compiles `src`, `tests`, `scripts`, and `tools`;
4. runs the offline pytest suite;
5. regenerates API reference docs with stale-doc pruning;
6. runs `mkdocs build --strict`; and
7. builds a wheel into `dist/`.

To create a clean project zip after the checks pass:

```bash
python scripts/make_release_zip.py
```

The zip creator excludes `.git`, virtual environments, build products, generated
site output, Python bytecode, pytest caches, egg-info, `__MACOSX`, `.DS_Store`,
and `._*` AppleDouble metadata files.

For a faster local check while editing docs tooling, use:

```bash
python scripts/release_check.py --skip-wheel
```

For a code-only check when docs dependencies are not installed, use:

```bash
python scripts/release_check.py --skip-docs --skip-wheel
```
