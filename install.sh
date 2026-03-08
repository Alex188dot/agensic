#!/bin/bash

echo "🚀 Installing Agensic 🔒..."

# 1. Create directory
INSTALL_DIR="$HOME/.agensic"
FIRST_INSTALL=0
if [ ! -f "$INSTALL_DIR/cli.py" ]; then
    FIRST_INSTALL=1
fi
mkdir -p "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/bin"

# 2. Copy files
cp requirements.txt "$INSTALL_DIR/"
cp server.py "$INSTALL_DIR/"
cp cli.py "$INSTALL_DIR/"
cp agensic.zsh "$INSTALL_DIR/"
cp engine.py "$INSTALL_DIR/"
cp vector_db.py "$INSTALL_DIR/"
cp privacy_guard.py "$INSTALL_DIR/"
cp shell_client.py "$INSTALL_DIR/"
rm -rf "$INSTALL_DIR/agensic"
cp -R agensic "$INSTALL_DIR/"

# 2b. Install local provenance TUI sidecar if already built
LOCAL_TUI_BIN="$PWD/rust/provenance_tui/target/release/agensic-provenance-tui"
if [ -x "$LOCAL_TUI_BIN" ]; then
    cp "$LOCAL_TUI_BIN" "$INSTALL_DIR/bin/agensic-provenance-tui"
    chmod +x "$INSTALL_DIR/bin/agensic-provenance-tui"
    echo "✅ Installed local provenance TUI sidecar to $INSTALL_DIR/bin"
else
    MANIFEST_URL="${AGENSIC_PROVENANCE_TUI_MANIFEST_URL:-https://github.com/Alex188dot/ai-terminal/releases/latest/download/provenance_tui_manifest.json}"
    python3 - "$MANIFEST_URL" "$INSTALL_DIR/bin/agensic-provenance-tui" <<'PY' || echo "⚠️ Could not download provenance TUI sidecar; CLI fallback will still work."
import hashlib
import json
import os
import platform
import shutil
import sys
import tarfile
import tempfile
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

with urllib.request.urlopen(manifest_url, timeout=12) as response:
    manifest = json.loads(response.read().decode("utf-8"))

entry = ((manifest or {}).get("platforms", {}) or {}).get(platform_key, {})
if not isinstance(entry, dict) or not entry.get("url"):
    raise SystemExit(1)

artifact_url = str(entry.get("url", "") or "").strip()
artifact_sha = str(entry.get("artifact_sha256", "") or "").strip().lower()
binary_sha = str(entry.get("binary_sha256", "") or "").strip().lower()
binary_name = str(entry.get("binary", "agensic-provenance-tui") or "agensic-provenance-tui")

os.makedirs(os.path.dirname(dest_bin), exist_ok=True)
with tempfile.TemporaryDirectory(prefix="agensic-tui-install-") as tmp:
    artifact_path = os.path.join(tmp, "provenance_tui.tar.gz")
    with urllib.request.urlopen(artifact_url, timeout=30) as response, open(artifact_path, "wb") as out:
        shutil.copyfileobj(response, out)

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
chmod -R u=rwX,go= "$INSTALL_DIR"

# 2d. Create a stable PATH launcher for the CLI.
CLI_LAUNCHER="$INSTALL_DIR/bin/agensic"
cat > "$CLI_LAUNCHER" <<EOF
#!/bin/sh
exec python3 "$INSTALL_DIR/cli.py" "\$@"
EOF
chmod 700 "$CLI_LAUNCHER"

# 3. Install Python Dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt"

# 4. Add Agensic to PATH and source shell integration (idempotent)
SHELL_RC="$HOME/.zshrc"
SHELL_PROFILE="$HOME/.zprofile"

PATH_START_MARKER="# >>> agensic path >>>"
PATH_END_MARKER="# <<< agensic path <<<"
RC_START_MARKER="# >>> agensic >>>"
RC_END_MARKER="# <<< agensic <<<"
LEGACY_INSTALL_NAME="ghost""shell"
LEGACY_CLI_NAME="ai""terminal"
LEGACY_START_MARKER="# >>> ${LEGACY_INSTALL_NAME} >>>"
LEGACY_END_MARKER="# <<< ${LEGACY_INSTALL_NAME} <<<"
UNINSTALL_SENTINEL="${TMPDIR:-/tmp}/agensic-shell-uninstalled-$(id -u)"

touch "$SHELL_RC"
touch "$SHELL_PROFILE"
rm -f "$UNINSTALL_SENTINEL"

# Remove old unmanaged lines from previous installs.
sed -i.bak \
  -e "\|alias ${LEGACY_CLI_NAME}='python3 .*\\.${LEGACY_INSTALL_NAME}/cli\\.py'|d" \
  -e "\|alias agensic='python3 .*\\.agensic/cli\\.py'|d" \
  -e '\|export PATH=".*\.agensic/bin:\$PATH"|d' \
  -e "\|source .*\\.${LEGACY_INSTALL_NAME}/${LEGACY_INSTALL_NAME}\\.zsh|d" \
  -e "\|source .*\\.agensic/agensic\\.zsh|d" \
  "$SHELL_RC"

# Keep PATH management out of .zshrc; scrub legacy PATH lines there too.
sed -i.bak \
  -e "/$LEGACY_START_MARKER/,/$LEGACY_END_MARKER/d" \
  -e "/$PATH_START_MARKER/,/$PATH_END_MARKER/d" \
  -e "/$RC_START_MARKER/,/$RC_END_MARKER/d" \
  "$SHELL_RC"

sed -i.bak \
  -e "\|alias ${LEGACY_CLI_NAME}='python3 .*\\.${LEGACY_INSTALL_NAME}/cli\\.py'|d" \
  -e "\|alias agensic='python3 .*\\.agensic/cli\\.py'|d" \
  -e '\|export PATH=".*\.agensic/bin:\$PATH"|d' \
  -e "/$LEGACY_START_MARKER/,/$LEGACY_END_MARKER/d" \
  -e "/$PATH_START_MARKER/,/$PATH_END_MARKER/d" \
  -e "/$RC_START_MARKER/,/$RC_END_MARKER/d" \
  "$SHELL_PROFILE"

cat >> "$SHELL_PROFILE" <<EOF
$PATH_START_MARKER
export PATH="$HOME/.agensic/bin:$PATH"
$PATH_END_MARKER
EOF

cat >> "$SHELL_RC" <<EOF
$RC_START_MARKER
source "$INSTALL_DIR/agensic.zsh"
$RC_END_MARKER
EOF

echo ""
echo "✅ Agensic 🔒 Installation complete!"
echo "------------------------------------------------"
echo "1. Open a new terminal, or run: export PATH=\"$INSTALL_DIR/bin:\$PATH\""
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
        python3 "$INSTALL_DIR/cli.py" first-run || {
            echo "⚠️ First-time onboarding did not complete."
            echo "   Run: python3 $INSTALL_DIR/cli.py first-run"
        }
    else
        echo "First-time onboarding was skipped because this install is not running in an interactive terminal."
        echo "Run: python3 $INSTALL_DIR/cli.py first-run"
    fi
fi
