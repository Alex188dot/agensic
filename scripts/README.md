# Scripts

## Release

Use the release script only when all feature work is already merged and pushed.

Preconditions:

- be on `main`
- have a clean repo
- have already pushed all non-release code
- have GitHub CLI (`gh`) installed and authenticated
- have permission to push directly to `main`

Run:

```bash
bash ./scripts/release.sh 0.1.2
```

What it does:

- pulls the latest `main`
- bumps `pyproject.toml` and `agensic/version.py`
- creates commit `Release vX.Y.Z`
- creates tag `vX.Y.Z`
- pushes `main`
- pushes the tag
- creates the GitHub Release
- removes the legacy `tuis-latest` release/tag if it still exists

Do not run it from a feature branch.
