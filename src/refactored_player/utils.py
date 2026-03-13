from __future__ import annotations


def format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remainder = total % 60
    return f"{hours:02d}:{minutes:02d}:{remainder:02d}"
