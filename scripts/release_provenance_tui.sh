#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CRATE_DIR="$ROOT_DIR/rust/provenance_tui"
BINARY_NAME="agensic-provenance-tui"
VERSION="${1:-}"

detect_target() {
  local os arch
  os="$(uname -s)"
  arch="$(uname -m)"
  case "$os:$arch" in
    Darwin:arm64|Darwin:aarch64) echo "aarch64-apple-darwin" ;;
    Darwin:x86_64) echo "x86_64-apple-darwin" ;;
    Linux:x86_64|Linux:amd64) echo "x86_64-unknown-linux-gnu" ;;
    Linux:arm64|Linux:aarch64) echo "aarch64-unknown-linux-gnu" ;;
    *) return 1 ;;
  esac
}

platform_key_for_target() {
  case "$1" in
    aarch64-apple-darwin) echo "darwin-arm64" ;;
    x86_64-apple-darwin) echo "darwin-x64" ;;
    x86_64-unknown-linux-gnu) echo "linux-x64" ;;
    aarch64-unknown-linux-gnu) echo "linux-arm64" ;;
    x86_64-pc-windows-msvc) echo "windows-x64" ;;
    *) return 1 ;;
  esac
}

binary_name_for_target() {
  case "$1" in
    *-windows-*) echo "${BINARY_NAME}.exe" ;;
    *) echo "$BINARY_NAME" ;;
  esac
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
    return
  fi
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
    return
  fi
  "${PYTHON_BIN}" - "$1" <<'PY'
import hashlib
import sys

path = sys.argv[1]
h = hashlib.sha256()
with open(path, "rb") as f:
    for chunk in iter(lambda: f.read(1024 * 1024), b""):
        if not chunk:
            break
        h.update(chunk)
print(h.hexdigest())
PY
}

detect_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  return 1
}

TARGET="${TARGET:-$(detect_target)}"
PLATFORM_KEY="${PLATFORM_KEY:-$(platform_key_for_target "$TARGET")}"
TARGET_BINARY_NAME="$(binary_name_for_target "$TARGET")"
BUILD_CMD="${BUILD_CMD:-cargo}"
PYTHON_BIN="${PYTHON_BIN:-$(detect_python)}"

if [[ -z "$VERSION" ]]; then
  VERSION="$(git -C "$ROOT_DIR" describe --tags --always --dirty 2>/dev/null || date +%Y%m%d%H%M%S)"
fi

OUT_DIR="$ROOT_DIR/dist/provenance_tui/$VERSION"
STAGE_DIR="$OUT_DIR/stage"
ARTIFACT_NAME="${BINARY_NAME}-${PLATFORM_KEY}.tar.gz"
ARTIFACT_PATH="$OUT_DIR/$ARTIFACT_NAME"
MANIFEST_PATH="$OUT_DIR/provenance_tui_manifest.json"

mkdir -p "$STAGE_DIR"

echo "[release] building $BINARY_NAME for $TARGET with $BUILD_CMD"
"$BUILD_CMD" build --manifest-path "$CRATE_DIR/Cargo.toml" --release --target "$TARGET"

BIN_PATH="$CRATE_DIR/target/$TARGET/release/$TARGET_BINARY_NAME"
if [[ ! -x "$BIN_PATH" ]]; then
  echo "[release] expected binary not found: $BIN_PATH" >&2
  exit 1
fi

cp "$BIN_PATH" "$STAGE_DIR/$TARGET_BINARY_NAME"
chmod +x "$STAGE_DIR/$TARGET_BINARY_NAME"

(
  cd "$STAGE_DIR"
  tar -czf "$ARTIFACT_PATH" "$TARGET_BINARY_NAME"
)

ARTIFACT_SHA="$(sha256_file "$ARTIFACT_PATH")"
BINARY_SHA="$(sha256_file "$STAGE_DIR/$TARGET_BINARY_NAME")"

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
      "binary": "$TARGET_BINARY_NAME",
      "binary_sha256": "$BINARY_SHA"
    }
  }
}
JSON

echo "[release] artifact: $ARTIFACT_PATH"
echo "[release] manifest: $MANIFEST_PATH"
echo "[release] artifact_sha256: $ARTIFACT_SHA"
echo "[release] binary_sha256: $BINARY_SHA"
