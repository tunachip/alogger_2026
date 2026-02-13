# Alog GUI-Only Upload

This payload is intentionally limited to the desktop GUI surface.

## Run

### Windows

```powershell
.\install_windows.bat
.\run_gui.bat
```

### macOS / Linux

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./run_gui.sh
```

## Notes

- CLI entry points from `src/alog/cli.py` and `src/alog/__main__.py` are removed.
- Runtime tools are still required on the target machine:
  - `yt-dlp`
  - `ffmpeg`
  - VLC (desktop app/libvlc)
