#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CRATE_DIR="$ROOT_DIR/rust/provenance_tui"
TARGET="aarch64-apple-darwin"
PLATFORM_KEY="darwin-arm64"
BINARY_NAME="agensic-provenance-tui"
VERSION="${1:-}"

if [[ -z "$VERSION" ]]; then
  VERSION="$(git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || date +%Y%m%d%H%M%S)"
fi

OUT_DIR="$ROOT_DIR/dist/provenance_tui/$VERSION"
STAGE_DIR="$OUT_DIR/stage"
ARTIFACT_NAME="${BINARY_NAME}-${PLATFORM_KEY}.tar.gz"
ARTIFACT_PATH="$OUT_DIR/$ARTIFACT_NAME"
MANIFEST_PATH="$OUT_DIR/provenance_tui_manifest.json"

mkdir -p "$STAGE_DIR"

echo "[release] building $BINARY_NAME for $TARGET"
cargo build --manifest-path "$CRATE_DIR/Cargo.toml" --release --target "$TARGET"

BIN_PATH="$CRATE_DIR/target/$TARGET/release/$BINARY_NAME"
if [[ ! -x "$BIN_PATH" ]]; then
  echo "[release] expected binary not found: $BIN_PATH" >&2
  exit 1
fi

cp "$BIN_PATH" "$STAGE_DIR/$BINARY_NAME"
chmod +x "$STAGE_DIR/$BINARY_NAME"

(
  cd "$STAGE_DIR"
  tar -czf "$ARTIFACT_PATH" "$BINARY_NAME"
)

ARTIFACT_SHA="$(shasum -a 256 "$ARTIFACT_PATH" | awk '{print $1}')"
BINARY_SHA="$(shasum -a 256 "$STAGE_DIR/$BINARY_NAME" | awk '{print $1}')"

cat > "$MANIFEST_PATH" <<JSON
{
  "name": "$BINARY_NAME",
  "version": "$VERSION",
  "min_cli_version": "0.1.0",
  "generated_at": $(date +%s),
  "platforms": {
    "$PLATFORM_KEY": {
      "url": "https://github.com/Alex188dot/agensic/releases/download/$VERSION/$ARTIFACT_NAME",
      "artifact_sha256": "$ARTIFACT_SHA",
      "binary": "$BINARY_NAME",
      "binary_sha256": "$BINARY_SHA"
    }
  }
}
JSON

echo "[release] artifact: $ARTIFACT_PATH"
echo "[release] manifest: $MANIFEST_PATH"
echo "[release] artifact_sha256: $ARTIFACT_SHA"
echo "[release] binary_sha256: $BINARY_SHA"
