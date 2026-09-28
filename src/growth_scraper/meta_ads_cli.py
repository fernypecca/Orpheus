"""`gscrape meta-ads` — Meta Ad Library through the official API.

Writes the SAME JSONL/CSV record shape as a scrape (pageType "ads", `ads`
cards, summary with adsLibrary/adsCount), so downstream LLM code does not
care whether ads came from a browser or from graph.facebook.com.

Token: --token or env META_ADS_TOKEN (never printed, never stored).
"""

from __future__ import annotations

import argparse
import os
import sys
from urllib.parse import quote


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gscrape meta-ads")
    p.add_argument("--query", default="", help="Search terms (blank space = AND, max 100 chars).")
    p.add_argument("--countries", default="ES",
                   help="Comma-separated ad-reached countries, e.g. ES,US (default ES).")
    p.add_argument("--ad-type", default="ALL",
                   help="ALL (EU/UK only) | POLITICAL_AND_ISSUE_ADS | HOUSING_ADS | EMPLOYMENT_ADS | FINANCIAL_PRODUCTS_AND_SERVICES_ADS.")
    p.add_argument("--page-ids", default="",
                   help="Up to 10 Facebook Page IDs, comma-separated (search by advertiser).")
    p.add_argument("--limit", type=int, default=50, help="Max ads (default 50, hard cap 1000).")
    p.add_argument("--token", default="", help="Ad Library API token (default: env META_ADS_TOKEN).")
    p.add_argument("-o", "--output", help="JSONL output path (default: stdout).")
    p.add_argument("--csv", action="store_true", help="Also write a .csv triage file next to -o.")
    p.add_argument("--max-text-chars", type=int, default=12000, help="Cap for the LLM-ready text.")
    return p


def _library_url(query: str, countries: list[str]) -> str:
    q = quote(query or "")
    c = (countries[0] if countries else "ES")
    return (f"https://www.facebook.com/ads/library/?active_status=active"
            f"&ad_type=all&country={c}&query={q}")


def meta_ads_main(argv: list[str]) -> int:
    from .config import Record
    from .meta_ads import fetch_meta_ads
    from .pipeline import _build_summary, _cap_text
    from .records import CsvWriter, JsonlWriter

    args = _parser().parse_args(argv)
    token = args.token or os.environ.get("META_ADS_TOKEN", "")
    if not token:
        print("[gscrape meta-ads] error: provide --token or set META_ADS_TOKEN", file=sys.stderr)
        return 2
    countries = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    page_ids = [p.strip() for p in args.page_ids.split(",") if p.strip()]

    ads, error = fetch_meta_ads(
        token=token,
        search_terms=args.query,
        countries=countries,
        ad_type=args.ad_type.upper(),
        page_ids=page_ids or None,
        limit=max(1, args.limit),
    )
    if error and not ads:
        print(f"[gscrape meta-ads] error: {error}", file=sys.stderr)
        return 1

    url = _library_url(args.query, countries)
    record = Record(url=url)
    record.statusCode = 200
    record.finalUrl = url
    record.title = f"Meta Ad Library: {args.query or 'all'} ({','.join(countries)})"
    record.pageType = "ads"
    chunks = [a["text"] for a in ads if a.get("text")]
    record.text = _cap_text("\n\n---\n\n".join(
        f"{a.get('advertiser') or 'Unknown'}: {t}" for a, t in
        zip(ads, chunks)), max_chars=args.max_text_chars)
    record.ads = ads or None
    record.summary = _build_summary(
        url, record.title, record.pageType, [], record.text, "",
        query=args.query or None, ads=record.ads, ads_library="meta")
    if error:
        record.summary["note"] = error  # partial page: honest about the cut

    if args.output:
        with JsonlWriter(args.output) as w:
            w.write(record)
        if args.csv:
            with CsvWriter(args.output + ".csv") as w:
                w.write(record)
    else:
        import json

        print(json.dumps(record.to_dict(), ensure_ascii=False))
    if error:
        print(f"[gscrape meta-ads] warning: {error} ({len(ads)} ads kept)", file=sys.stderr)
    return 0
