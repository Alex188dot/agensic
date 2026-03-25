#!/bin/bash

set -euo pipefail

REPO_URL="${AGENSIC_REPO_URL:-https://github.com/Alex188dot/agensic.git}"
BRANCH="${AGENSIC_BRANCH:-main}"

if ! command -v git >/dev/null 2>&1; then
    echo "❌ git is required to install Agensic."
    exit 1
fi

if ! command -v bash >/dev/null 2>&1; then
    echo "❌ bash is required to install Agensic."
    exit 1
fi

TMP_DIR="$(mktemp -d 2>/dev/null || mktemp -d -t agensic-install)"
cleanup() {
    rm -rf "$TMP_DIR"
}
trap cleanup EXIT

echo "🚀 Fetching Agensic..."
git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$TMP_DIR"

echo "📦 Running installer..."
cd "$TMP_DIR"
bash ./install.sh
