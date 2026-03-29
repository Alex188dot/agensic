#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=""
REPO="${REPO:-Alex188dot/agensic}"
DEFAULT_BRANCH="${DEFAULT_BRANCH:-main}"
LEGACY_TUIS_TAG="${LEGACY_TUIS_TAG:-tuis-latest}"

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh <version>

Examples:
  ./scripts/release.sh 0.1.1

Behavior:
  - requires a clean checkout on main
  - pulls the latest main from origin
  - validates semantic version format (X.Y.Z)
  - updates pyproject.toml and agensic/version.py
  - creates a release commit and annotated git tag
  - pushes main and the release tag
  - creates the GitHub Release with gh
  - deletes the legacy tuis-latest release/tag if present
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      if [[ -z "$VERSION" ]]; then
        VERSION="$1"
      else
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 1
      fi
      ;;
  esac
  shift
done

if [[ -z "$VERSION" ]]; then
  usage >&2
  exit 1
fi

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must match semantic version format X.Y.Z" >&2
  exit 1
fi

require_clean_git() {
  if ! git -C "$ROOT_DIR" diff --quiet --ignore-submodules --; then
    echo "Working tree has unstaged changes. Commit or stash them first." >&2
    exit 1
  fi
  if ! git -C "$ROOT_DIR" diff --cached --quiet --ignore-submodules --; then
    echo "Working tree has staged but uncommitted changes. Commit or stash them first." >&2
    exit 1
  fi
}

current_branch() {
  git -C "$ROOT_DIR" rev-parse --abbrev-ref HEAD
}

current_version() {
  python3 - "$ROOT_DIR" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
if not match:
    raise SystemExit("could not find version in pyproject.toml")
print(match.group(1))
PY
}

update_versions() {
  python3 - "$ROOT_DIR" "$VERSION" <<'PY'
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
version = sys.argv[2]

targets = [
    root / "pyproject.toml",
    root / "agensic" / "version.py",
]

patterns = {
    targets[0]: (r'(^version = ")[^"]+(")$', r'\g<1>' + version + r'\2'),
    targets[1]: (r'(^__version__ = ")[^"]+(")$', r'\g<1>' + version + r'\2'),
}

for path in targets:
    text = path.read_text(encoding="utf-8")
    pattern, replacement = patterns[path]
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise SystemExit(f"failed to update version in {path}")
    path.write_text(updated, encoding="utf-8")
PY
}

require_clean_git

require_main_branch() {
  local branch
  branch="$(current_branch)"
  if [[ "$branch" != "$DEFAULT_BRANCH" ]]; then
    echo "Releases must be created from '$DEFAULT_BRANCH'. Current branch: '$branch'." >&2
    exit 1
  fi
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 1
  fi
}

delete_legacy_tuis_release_if_present() {
  if gh release view "$LEGACY_TUIS_TAG" --repo "$REPO" >/dev/null 2>&1; then
    gh release delete "$LEGACY_TUIS_TAG" --repo "$REPO" --yes
  fi
  if git ls-remote --tags origin "$LEGACY_TUIS_TAG" | grep -q .; then
    git -C "$ROOT_DIR" push origin ":refs/tags/$LEGACY_TUIS_TAG"
  fi
}

require_main_branch
require_command git
require_command python3
require_command gh

git -C "$ROOT_DIR" pull --ff-only origin "$DEFAULT_BRANCH"
require_clean_git

OLD_VERSION="$(current_version)"
TAG="v$VERSION"

if [[ "$OLD_VERSION" == "$VERSION" ]]; then
  echo "Version is already $VERSION; nothing to bump." >&2
  exit 1
fi

if git -C "$ROOT_DIR" rev-parse "$TAG" >/dev/null 2>&1; then
  echo "Git tag $TAG already exists locally." >&2
  exit 1
fi

update_versions

git -C "$ROOT_DIR" add pyproject.toml agensic/version.py
git -C "$ROOT_DIR" commit -m "Release $TAG"
git -C "$ROOT_DIR" tag -a "$TAG" -m "Agensic $TAG"
git -C "$ROOT_DIR" push origin "$DEFAULT_BRANCH"
git -C "$ROOT_DIR" push origin "$TAG"
gh release create "$TAG" \
  --repo "$REPO" \
  --title "$TAG" \
  --notes "Release $TAG"
delete_legacy_tuis_release_if_present

cat <<EOF
Release completed successfully.

Previous version: $OLD_VERSION
New version:      $VERSION
Commit:           Release $TAG
Tag:              $TAG
Branch:           $DEFAULT_BRANCH
Repository:       $REPO
EOF
