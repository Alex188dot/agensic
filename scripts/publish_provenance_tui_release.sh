#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${GITHUB_REPO:-Alex188dot/agensic}"
TAG="${1:-}"

if [[ -z "$TAG" ]]; then
  TAG="$(git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || date +%Y%m%d%H%M%S)"
fi

ARTIFACT_NAME="agensic-provenance-tui-darwin-arm64.tar.gz"
OUT_DIR="$ROOT_DIR/dist/provenance_tui/$TAG"
ARTIFACT_PATH="$OUT_DIR/$ARTIFACT_NAME"
MANIFEST_PATH="$OUT_DIR/provenance_tui_manifest.json"

if ! command -v gh >/dev/null 2>&1; then
  echo "[publish] GitHub CLI 'gh' is required." >&2
  exit 1
fi

echo "[publish] building artifact + manifest for tag '$TAG'"
"$ROOT_DIR/scripts/release_provenance_tui.sh" "$TAG"

if [[ ! -f "$ARTIFACT_PATH" ]]; then
  echo "[publish] missing artifact: $ARTIFACT_PATH" >&2
  exit 1
fi
if [[ ! -f "$MANIFEST_PATH" ]]; then
  echo "[publish] missing manifest: $MANIFEST_PATH" >&2
  exit 1
fi

if ! gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  echo "[publish] creating release $TAG on $REPO"
  gh release create "$TAG" \
    --repo "$REPO" \
    --title "$TAG" \
    --notes "Provenance TUI release $TAG"
else
  echo "[publish] release $TAG already exists on $REPO"
fi

echo "[publish] uploading artifacts"
gh release upload "$TAG" "$ARTIFACT_PATH" "$MANIFEST_PATH" --repo "$REPO" --clobber

echo "[publish] done"
echo "[publish] manifest URL:"
echo "https://github.com/$REPO/releases/latest/download/provenance_tui_manifest.json"
