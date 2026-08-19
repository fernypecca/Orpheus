"""JSONL output writer + optional CSV mirror for spreadsheet triage."""

from __future__ import annotations

import csv
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


class CsvWriter:
    """Flat summary of every record, one row per URL. For Google Sheets/Excel."""

    HEADERS = [
        "url", "title", "pageType", "domain", "metaDescription", "h1",
        "wordCount", "itemCount", "statusCode", "retries", "error", "text_preview",
        "structuredSource", "structuredPrice", "structuredRatingValue",
        "structuredReviewCount", "structuredCategory", "language",
    ]

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._fh)
        self._writer.writerow(self.HEADERS)

    def write(self, record: Record) -> None:
        s = record.summary or {}
        preview = " ".join(record.text.split())[:1000]
        self._writer.writerow([
            record.url,
            record.title,
            record.pageType,
            s.get("domain", ""),
            s.get("metaDescription", ""),
            s.get("h1", ""),
            s.get("wordCount", 0),
            s.get("itemCount", 0),
            record.statusCode or "",
            record.retries,
            record.error or "",
            preview,
            s.get("structuredSource", ""),
            s.get("structuredPrice", ""),
            s.get("structuredRatingValue", ""),
            s.get("structuredReviewCount", ""),
            s.get("structuredCategory", ""),
            s.get("language", ""),
        ])
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "CsvWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def emit_progress(verbose: bool, msg: str) -> None:
    """Progress to stderr so stdout stays clean for the JSONL file."""
    if verbose:
        print(f"[gscrape] {msg}", file=sys.stderr)