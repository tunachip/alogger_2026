#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if ! command -v pyinstaller >/dev/null 2>&1; then
  echo "pyinstaller not found. Install it with: pip install pyinstaller"
  exit 1
fi

pyinstaller --clean --noconfirm release/gui-only/alog-gui.spec

echo
echo "GUI binary built at:"
echo "  $ROOT_DIR/dist/alog-gui"
