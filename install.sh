#!/bin/bash

echo "🚀 Installing GhostShell 👻✨..."

# 1. Create directory
INSTALL_DIR="$HOME/.ghostshell"
mkdir -p "$INSTALL_DIR"

# 2. Copy files
cp requirements.txt "$INSTALL_DIR/"
cp server.py "$INSTALL_DIR/"
cp cli.py "$INSTALL_DIR/"
cp ghostshell.zsh "$INSTALL_DIR/"
cp engine.py "$INSTALL_DIR/"
cp vector_db.py "$INSTALL_DIR/"
cp privacy_guard.py "$INSTALL_DIR/"
cp shell_client.py "$INSTALL_DIR/"
rm -rf "$INSTALL_DIR/ghostshell"
cp -R ghostshell "$INSTALL_DIR/"

# 2b. Install local provenance TUI sidecar if already built
mkdir -p "$INSTALL_DIR/bin"
LOCAL_TUI_BIN="$PWD/rust/provenance_tui/target/release/ghostshell-provenance-tui"
if [ -x "$LOCAL_TUI_BIN" ]; then
    cp "$LOCAL_TUI_BIN" "$INSTALL_DIR/bin/ghostshell-provenance-tui"
    chmod +x "$INSTALL_DIR/bin/ghostshell-provenance-tui"
    echo "✅ Installed local provenance TUI sidecar to $INSTALL_DIR/bin"
else
    MANIFEST_URL="${GHOSTSHELL_PROVENANCE_TUI_MANIFEST_URL:-https://github.com/Alex188dot/ai-terminal/releases/latest/download/provenance_tui_manifest.json}"
    python3 - "$MANIFEST_URL" "$INSTALL_DIR/bin/ghostshell-provenance-tui" <<'PY' || echo "⚠️ Could not download provenance TUI sidecar; CLI fallback will still work."
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
binary_name = str(entry.get("binary", "ghostshell-provenance-tui") or "ghostshell-provenance-tui")

os.makedirs(os.path.dirname(dest_bin), exist_ok=True)
with tempfile.TemporaryDirectory(prefix="ghostshell-tui-install-") as tmp:
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

# 3. Install Python Dependencies
echo "📦 Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt"

# 4. Create alias for the CLI (idempotent)
# We add this to rc file so 'aiterminal' command works
SHELL_RC="$HOME/.zshrc"
if [ -f "$HOME/.bashrc" ]; then
    SHELL_RC="$HOME/.bashrc"
fi

START_MARKER="# >>> ghostshell >>>"
END_MARKER="# <<< ghostshell <<<"

touch "$SHELL_RC"

# Remove old unmanaged lines from previous installs.
sed -i.bak \
  -e "\|alias aiterminal='python3 .*\\.ghostshell/cli\\.py'|d" \
  -e "\|source .*\\.ghostshell/ghostshell\\.zsh|d" \
  "$SHELL_RC"

# Remove previous managed block if present.
sed -i.bak \
  -e "/$START_MARKER/,/$END_MARKER/d" \
  "$SHELL_RC"

cat >> "$SHELL_RC" <<EOF
$START_MARKER
alias aiterminal='python3 $INSTALL_DIR/cli.py'
source $INSTALL_DIR/ghostshell.zsh
$END_MARKER
EOF

echo ""
echo "✅ GhostShell 👻✨ Installation complete!"
echo "------------------------------------------------"
echo "1. Restart your terminal."
echo "2. Run: aiterminal setup"
echo "3. Start typing commands (e.g. 'git c', 'docker ru')."
echo "------------------------------------------------"
