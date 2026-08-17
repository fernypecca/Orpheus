"""JSONL output writer."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from .config import Record


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JsonlWriter:
    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8")

    def write(self, record: Record) -> None:
        record.scrapedAt = record.scrapedAt or utcnow_iso()
        line = json.dumps(record.to_dict(), ensure_ascii=False, default=str)
        self._fh.write(line + "\n")
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def emit_progress(verbose: bool, msg: str) -> None:
    """Progress to stderr so stdout stays clean for the JSONL file."""
    if verbose:
        print(f"[gscrape] {msg}", file=sys.stderr)