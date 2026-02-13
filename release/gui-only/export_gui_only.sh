#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="$ROOT_DIR/release/gui-only/upload_payload"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR/src"

cp -R "$ROOT_DIR/src/alogger_player" "$OUT_DIR/src/"
cp -R "$ROOT_DIR/src/alog" "$OUT_DIR/src/"
cp "$ROOT_DIR/requirements.txt" "$OUT_DIR/"
cp "$ROOT_DIR/release/gui-only/README_GUI_ONLY.md" "$OUT_DIR/README.md"
cp "$ROOT_DIR/release/gui-only/install_windows.ps1" "$OUT_DIR/"
cp "$ROOT_DIR/release/gui-only/install_windows.bat" "$OUT_DIR/"
cp "$ROOT_DIR/release/gui-only/run_gui.bat" "$OUT_DIR/"

# Remove non-GUI CLI entry points from exported source.
rm -f "$OUT_DIR/src/alog/cli.py" "$OUT_DIR/src/alog/__main__.py"

cat >"$OUT_DIR/run_gui.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
PYTHONPATH=src python -m alogger_player "$@"
EOF
chmod +x "$OUT_DIR/run_gui.sh"

find "$OUT_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$OUT_DIR" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

echo "Exported GUI-only payload to:"
echo "  $OUT_DIR"
