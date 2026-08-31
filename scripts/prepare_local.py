from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import settings  # noqa: E402


def prepare() -> None:
    if settings.inventory_provider != "local_excel": return
    target = settings.workbook_path
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        source = settings.source_workbook_path
        if not source.is_absolute(): source = ROOT / source
        if not source.exists(): raise SystemExit(f"Source workbook not found: {source}")
        shutil.copy2(source, target)
        print(f"Created working workbook: {target}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--serve", action="store_true"); args = parser.parse_args()
    prepare()
    if args.serve:
        os.execvp("uvicorn", ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"])
