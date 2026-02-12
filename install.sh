#!/bin/bash

echo "🚀 Installing GhostShell..."

# 1. Create directory
INSTALL_DIR="$HOME/.ghostshell"
mkdir -p "$INSTALL_DIR"

# 2. Copy files
cp requirements.txt "$INSTALL_DIR/"
cp server.py "$INSTALL_DIR/"
cp cli.py "$INSTALL_DIR/"
cp ghostshell.zsh "$INSTALL_DIR/"

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

echo "✅ Installation complete!"
echo "------------------------------------------------"
echo "1. Restart your terminal."
echo "2. Run: aiterminal setup"
echo "3. Start typing commands (e.g. 'git c', 'ros2 ru')."
echo "------------------------------------------------"
