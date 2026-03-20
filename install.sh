#!/bin/bash

echo "🚀 Installing Agensic 🫆..."

# 1. Create directory
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
STATE_HOME="${XDG_STATE_HOME:-$HOME/.local/state}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
BIN_HOME="${XDG_BIN_HOME:-$HOME/.local/bin}"
APP_CONFIG_DIR="$CONFIG_HOME/agensic"
APP_STATE_DIR="$STATE_HOME/agensic"
APP_CACHE_DIR="$CACHE_HOME/agensic"
INSTALL_DIR="$APP_STATE_DIR/install"
INSTALL_BIN_DIR="$INSTALL_DIR/bin"
USER_BIN_DIR="$BIN_HOME"
VENV_DIR="$INSTALL_DIR/.venv"
TORCH_VARIANT="${AGENSIC_TORCH_VARIANT:-cpu}"
FIRST_INSTALL=0
if [ ! -x "$USER_BIN_DIR/agensic" ]; then
    FIRST_INSTALL=1
fi
mkdir -p "$APP_CONFIG_DIR"
mkdir -p "$APP_STATE_DIR"
mkdir -p "$APP_CACHE_DIR"
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_BIN_DIR"
mkdir -p "$USER_BIN_DIR"

install_linux_emoji_font_if_missing() {
    if [ "$(uname -s 2>/dev/null)" != "Linux" ]; then
        return 0
    fi

    if command -v fc-list >/dev/null 2>&1; then
        if fc-list | grep -qi "Noto Color Emoji"; then
            return 0
        fi
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        echo "⚠️ Linux emoji font not detected and apt-get is unavailable."
        echo "   Install an emoji font manually, for example: fonts-noto-color-emoji"
        return 0
    fi

    if command -v dpkg-query >/dev/null 2>&1; then
        if dpkg-query -W -f='${Status}' fonts-noto-color-emoji 2>/dev/null | grep -q "install ok installed"; then
            return 0
        fi
    fi

    echo "🔤 Installing Linux emoji font support (fonts-noto-color-emoji)..."
    if command -v sudo >/dev/null 2>&1; then
        sudo apt-get update -y >/dev/null 2>&1 || true
        sudo apt-get install -y fonts-noto-color-emoji || {
            echo "⚠️ Could not install fonts-noto-color-emoji automatically."
            echo "   Run manually: sudo apt-get install -y fonts-noto-color-emoji"
            return 0
        }
    else
        apt-get update -y >/dev/null 2>&1 || true
        apt-get install -y fonts-noto-color-emoji || {
            echo "⚠️ Could not install fonts-noto-color-emoji automatically."
            echo "   Run manually: apt-get install -y fonts-noto-color-emoji"
            return 0
        }
    fi
}

install_linux_emoji_font_if_missing

legacy_bash_block_marker() {
    local side="$1"
    local legacy_name
    printf -v legacy_name '\x62\x6c\x65'
    printf '# %s agensic %s %s\n' "$side" "$legacy_name" "$side"
}

# 2. Copy shell integration assets
cp agensic.zsh "$INSTALL_DIR/"
cp agensic.bash "$INSTALL_DIR/"
cp shell_client.py "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR/shell"
cp shell/agensic_shared.sh "$INSTALL_DIR/shell/"

# 2b. Build the local provenance TUI sidecar from source when cargo is available.
TUI_MANIFEST_PATH="$PWD/rust/provenance_tui/Cargo.toml"
LOCAL_TUI_BIN="$PWD/rust/provenance_tui/target/release/agensic-provenance-tui"
CARGO_BIN="${CARGO:-}"
if [ -z "$CARGO_BIN" ] && command -v cargo >/dev/null 2>&1; then
    CARGO_BIN="$(command -v cargo)"
fi
if [ -z "$CARGO_BIN" ] && [ -x "$HOME/.cargo/bin/cargo" ]; then
    CARGO_BIN="$HOME/.cargo/bin/cargo"
fi
if [ -n "$CARGO_BIN" ]; then
    export PATH="$(dirname "$CARGO_BIN"):$PATH"
fi
if [ -f "$TUI_MANIFEST_PATH" ] && [ -n "$CARGO_BIN" ]; then
    echo "🛠️ Building provenance TUI sidecar from source..."
    "$CARGO_BIN" build --manifest-path "$TUI_MANIFEST_PATH" --release || {
        echo "❌ Failed to build provenance TUI sidecar from source."
        echo "   Fix the Rust build or install without local sidecar changes."
        exit 1
    }
elif [ -f "$TUI_MANIFEST_PATH" ]; then
    echo "⚠️ cargo not found; local Rust sidecar changes will not be included."
fi

if [ -x "$LOCAL_TUI_BIN" ]; then
    cp "$LOCAL_TUI_BIN" "$INSTALL_BIN_DIR/agensic-provenance-tui"
    chmod +x "$INSTALL_BIN_DIR/agensic-provenance-tui"
    echo "✅ Installed local provenance TUI sidecar to $INSTALL_BIN_DIR"
else
    MANIFEST_URL="${AGENSIC_PROVENANCE_TUI_MANIFEST_URL:-https://github.com/Alex188dot/agensic/releases/latest/download/provenance_tui_manifest.json}"
    python3 - "$MANIFEST_URL" "$INSTALL_BIN_DIR/agensic-provenance-tui" <<'PY' || echo "⚠️ Provenance TUI sidecar was not installed; CLI fallback will still work."
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request

manifest_url = str(sys.argv[1] if len(sys.argv) > 1 else "").strip()
dest_bin = str(sys.argv[2] if len(sys.argv) > 2 else "").strip()
if not manifest_url or not dest_bin:
    raise SystemExit(1)

machine = platform.machine().strip().lower()
system = platform.system().strip().lower()
if system == "darwin" and machine in {"arm64", "aarch64"}:
    platform_key = "darwin-arm64"
elif system == "darwin" and machine in {"x86_64", "amd64"}:
    platform_key = "darwin-x64"
elif system == "linux" and machine in {"x86_64", "amd64"}:
    platform_key = "linux-x64"
elif system == "linux" and machine in {"arm64", "aarch64"}:
    platform_key = "linux-arm64"
else:
    raise SystemExit(1)

default_manifest = "https://github.com/Alex188dot/agensic/releases/latest/download/provenance_tui_manifest.json"
published_platforms = {"darwin-arm64", "darwin-x64", "linux-arm64", "linux-x64", "windows-x64"}
manifest_overridden = manifest_url != default_manifest

if not manifest_overridden and platform_key not in published_platforms:
    print(
        f"No published provenance TUI sidecar is available for {platform_key}; skipping download.",
        file=sys.stderr,
    )
    print(
        "Set AGENSIC_PROVENANCE_TUI_MANIFEST_URL to use a custom external sidecar manifest.",
        file=sys.stderr,
    )
    raise SystemExit(0)

try:
    with urllib.request.urlopen(manifest_url, timeout=12) as response:
        manifest = json.loads(response.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    if int(getattr(exc, "code", 0) or 0) == 404:
        print("No published provenance TUI sidecar manifest was found; skipping download.", file=sys.stderr)
        print(
            "Set AGENSIC_PROVENANCE_TUI_MANIFEST_URL to use a custom external sidecar manifest.",
            file=sys.stderr,
        )
        raise SystemExit(0)
    raise SystemExit(1)
except Exception:
    raise SystemExit(1)

entry = ((manifest or {}).get("platforms", {}) or {}).get(platform_key, {})
if not isinstance(entry, dict) or not entry.get("url"):
    print(
        f"Manifest does not include a provenance TUI sidecar for {platform_key}; skipping download.",
        file=sys.stderr,
    )
    raise SystemExit(0)

artifact_url = str(entry.get("url", "") or "").strip()
artifact_sha = str(entry.get("artifact_sha256", "") or "").strip().lower()
binary_sha = str(entry.get("binary_sha256", "") or "").strip().lower()
binary_name = str(entry.get("binary", "agensic-provenance-tui") or "agensic-provenance-tui")

os.makedirs(os.path.dirname(dest_bin), exist_ok=True)
with tempfile.TemporaryDirectory(prefix="agensic-tui-install-") as tmp:
    artifact_path = os.path.join(tmp, "provenance_tui.tar.gz")
    try:
        with urllib.request.urlopen(artifact_url, timeout=30) as response, open(artifact_path, "wb") as out:
            shutil.copyfileobj(response, out)
    except Exception:
        raise SystemExit(1)

    if artifact_sha:
        with open(artifact_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest().lower()
        if digest != artifact_sha:
            raise SystemExit(1)

    with tarfile.open(artifact_path, "r:gz") as tar:
        members = [m for m in tar.getmembers() if m.isfile()]
        target = None
        for m in members:
            if os.path.basename(m.name) == binary_name:
                target = m
                break
        if target is None and members:
            target = members[0]
        if target is None:
            raise SystemExit(1)
        source = tar.extractfile(target)
        if source is None:
            raise SystemExit(1)
        with source, open(dest_bin, "wb") as out:
            shutil.copyfileobj(source, out)

os.chmod(dest_bin, 0o755)
if binary_sha:
    with open(dest_bin, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest().lower()
    if digest != binary_sha:
        raise SystemExit(1)
print(f"✅ Downloaded provenance TUI sidecar to {dest_bin}")
PY
fi

# 2c. Lock down install permissions for all Agensic files.
# Executable payloads keep owner-only execute bits; everything else becomes 0600.
chmod -R u=rwX,go= "$APP_CONFIG_DIR" "$APP_STATE_DIR" "$APP_CACHE_DIR"

# 2d. Install the Python package into an isolated virtual environment.
echo "📦 Installing Python package into $VENV_DIR..."
if command -v uv >/dev/null 2>&1; then
    echo "⚡ Using uv for faster environment setup"
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        uv venv "$VENV_DIR"
    fi
    if [ "$TORCH_VARIANT" = "cpu" ]; then
        echo "🧠 Installing CPU-only PyTorch to avoid CUDA downloads"
        uv pip install --python "$VENV_DIR/bin/python" --index-url https://download.pytorch.org/whl/cpu "torch==2.10.0+cpu"
    fi
    uv pip install --python "$VENV_DIR/bin/python" "$PWD"
else
    if [ ! -x "$VENV_DIR/bin/python" ]; then
        python3 -m venv "$VENV_DIR"
    fi
    if ! "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
        "$VENV_DIR/bin/python" -m ensurepip --upgrade
    fi
    "$VENV_DIR/bin/python" -m pip install --upgrade pip setuptools wheel
    if [ "$TORCH_VARIANT" = "cpu" ]; then
        echo "🧠 Installing CPU-only PyTorch to avoid CUDA downloads"
        "$VENV_DIR/bin/python" -m pip install --index-url https://download.pytorch.org/whl/cpu "torch==2.10.0+cpu"
    fi
    "$VENV_DIR/bin/python" -m pip install "$PWD"
fi

# 2e. Create stable PATH launchers for the CLI and session helpers.
CLI_LAUNCHER="$USER_BIN_DIR/agensic"
cat > "$CLI_LAUNCHER" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/agensic" "\$@"
EOF
chmod 700 "$CLI_LAUNCHER"

SESSION_START_LAUNCHER="$USER_BIN_DIR/agensic_session_start"
cat > "$SESSION_START_LAUNCHER" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/agensic" ai-session start "\$@"
EOF
chmod 700 "$SESSION_START_LAUNCHER"

SESSION_STATUS_LAUNCHER="$USER_BIN_DIR/agensic_session_status"
cat > "$SESSION_STATUS_LAUNCHER" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/agensic" ai-session status "\$@"
EOF
chmod 700 "$SESSION_STATUS_LAUNCHER"

SESSION_STOP_LAUNCHER="$USER_BIN_DIR/agensic_session_stop"
cat > "$SESSION_STOP_LAUNCHER" <<EOF
#!/bin/sh
exec "$VENV_DIR/bin/agensic" ai-session stop "\$@"
EOF
chmod 700 "$SESSION_STOP_LAUNCHER"

# 3. Add Agensic to PATH and source shell integration (idempotent)
TARGET_SHELL="$(basename "${SHELL:-zsh}")"
INTEGRATION_FILE="$INSTALL_DIR/agensic.zsh"
PATH_FILE="$HOME/.zprofile"
RC_FILE="$HOME/.zshrc"

case "$TARGET_SHELL" in
  bash)
    INTEGRATION_FILE="$INSTALL_DIR/agensic.bash"
    PATH_FILE="$HOME/.bashrc"
    RC_FILE="$HOME/.bashrc"
    ;;
  zsh|*)
    INTEGRATION_FILE="$INSTALL_DIR/agensic.zsh"
    PATH_FILE="$HOME/.zprofile"
    RC_FILE="$HOME/.zshrc"
    ;;
esac

PATH_START_MARKER="# >>> agensic path >>>"
PATH_END_MARKER="# <<< agensic path <<<"
LEGACY_BASH_BLOCK_START="$(legacy_bash_block_marker '>>>')"
LEGACY_BASH_BLOCK_END="$(legacy_bash_block_marker '<<<')"
RC_START_MARKER="# >>> agensic >>>"
RC_END_MARKER="# <<< agensic <<<"
LEGACY_INSTALL_NAME="ghost""shell"
LEGACY_CLI_NAME="ai""terminal"
LEGACY_START_MARKER="# >>> ${LEGACY_INSTALL_NAME} >>>"
LEGACY_END_MARKER="# <<< ${LEGACY_INSTALL_NAME} <<<"
UNINSTALL_SENTINEL="${TMPDIR:-/tmp}/agensic-shell-uninstalled-$(id -u)"

touch "$RC_FILE"
touch "$PATH_FILE"
rm -f "$UNINSTALL_SENTINEL"

# Remove old unmanaged lines from previous installs.
sed -i.bak \
  -e "\|alias ${LEGACY_CLI_NAME}='python3 .*\\.${LEGACY_INSTALL_NAME}/cli\\.py'|d" \
  -e "\|alias agensic='python3 .*\\.agensic/cli\\.py'|d" \
  -e '\|export PATH=".*\.agensic/bin:\$PATH"|d' \
  -e "\|source .*\\.${LEGACY_INSTALL_NAME}/${LEGACY_INSTALL_NAME}\\.zsh|d" \
  -e "\|source .*\\.agensic/agensic\\.zsh|d" \
  -e "\|source .*\\.agensic/agensic\\.bash|d" \
  "$RC_FILE"

# Remove previous managed blocks before rewriting the chosen target files.
sed -i.bak \
  -e "/$LEGACY_BASH_BLOCK_START/,/$LEGACY_BASH_BLOCK_END/d" \
  -e "/$LEGACY_START_MARKER/,/$LEGACY_END_MARKER/d" \
  -e "/$PATH_START_MARKER/,/$PATH_END_MARKER/d" \
  -e "/$RC_START_MARKER/,/$RC_END_MARKER/d" \
  "$RC_FILE"

sed -i.bak \
  -e "\|alias ${LEGACY_CLI_NAME}='python3 .*\\.${LEGACY_INSTALL_NAME}/cli\\.py'|d" \
  -e "\|alias agensic='python3 .*\\.agensic/cli\\.py'|d" \
  -e '\|export PATH=".*\.agensic/bin:\$PATH"|d' \
  -e "\|source .*\\.agensic/agensic\\.zsh|d" \
  -e "\|source .*\\.agensic/agensic\\.bash|d" \
  -e "/$LEGACY_BASH_BLOCK_START/,/$LEGACY_BASH_BLOCK_END/d" \
  -e "/$LEGACY_START_MARKER/,/$LEGACY_END_MARKER/d" \
  -e "/$PATH_START_MARKER/,/$PATH_END_MARKER/d" \
  -e "/$RC_START_MARKER/,/$RC_END_MARKER/d" \
  "$PATH_FILE"

if [ "$PATH_FILE" = "$RC_FILE" ]; then
  cat >> "$RC_FILE" <<EOF
$PATH_START_MARKER
export PATH="$USER_BIN_DIR:\$PATH"
$PATH_END_MARKER
$RC_START_MARKER
source "$INTEGRATION_FILE"
$RC_END_MARKER
EOF
else
  cat >> "$PATH_FILE" <<EOF
$PATH_START_MARKER
export PATH="$USER_BIN_DIR:\$PATH"
$PATH_END_MARKER
EOF

  cat >> "$RC_FILE" <<EOF
$RC_START_MARKER
source "$INTEGRATION_FILE"
$RC_END_MARKER
EOF
fi

echo ""
echo "✅ Agensic Installation complete!"
echo "------------------------------------------------"
echo "1. Open a new terminal, or run: export PATH=\"$USER_BIN_DIR:\$PATH\""
if [ "$FIRST_INSTALL" -eq 1 ]; then
    echo "2. Complete the first-time setup flow."
    echo "3. Start typing commands (e.g. 'git c', 'docker ru')."
else
    echo "2. Run: agensic setup"
    echo "3. Start typing commands (e.g. 'git c', 'docker ru')."
fi
echo "------------------------------------------------"

if [ "$FIRST_INSTALL" -eq 1 ]; then
    echo ""
    if [ -t 0 ] && [ -t 1 ]; then
        echo "Opening first-time Agensic onboarding..."
        "$VENV_DIR/bin/agensic" first-run || {
            echo "⚠️ First-time onboarding did not complete."
            echo "   Run: $VENV_DIR/bin/agensic first-run"
        }
    else
        echo "First-time onboarding was skipped because this install is not running in an interactive terminal."
        echo "Run: $VENV_DIR/bin/agensic first-run"
    fi
fi
