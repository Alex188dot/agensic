#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VERSION=""
PUBLISH="${PUBLISH:-0}"
PUSH="${PUSH:-0}"
REPO="${REPO:-Alex188dot/agensic}"

usage() {
  cat <<'EOF'
Usage: ./scripts/release.sh <version> [--publish] [--push]

Examples:
  ./scripts/release.sh 0.1.1
  ./scripts/release.sh 0.1.1 --publish
  ./scripts/release.sh 0.1.1 --publish --push

Behavior:
  - validates semantic version format (X.Y.Z)
  - updates pyproject.toml and agensic/version.py
  - creates a release commit and annotated git tag
  - optionally pushes the branch and tag
  - optionally creates a GitHub Release with gh
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --publish) PUBLISH=1 ;;
    --push) PUSH=1 ;;
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

OLD_VERSION="$(current_version)"
TAG="v$VERSION"
BRANCH="$(current_branch)"

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

if [[ "$PUSH" == "1" ]]; then
  git -C "$ROOT_DIR" push origin "$BRANCH"
  git -C "$ROOT_DIR" push origin "$TAG"
fi

if [[ "$PUBLISH" == "1" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "GitHub CLI (gh) is required for --publish." >&2
    exit 1
  fi
  gh release create "$TAG" \
    --repo "$REPO" \
    --title "$TAG" \
    --notes "Release $TAG"
fi

cat <<EOF
Release prepared successfully.

Previous version: $OLD_VERSION
New version:      $VERSION
Commit:           Release $TAG
Tag:              $TAG

Next steps:
  git push origin "$BRANCH"
  git push origin "$TAG"
  gh release create "$TAG" --repo "$REPO" --title "$TAG" --notes "Release $TAG"
EOF
