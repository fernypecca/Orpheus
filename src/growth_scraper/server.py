"""gscrape serve — warm-browser HTTP server (single-URL scrapes).

Binds to 127.0.0.1 only. Never expose this on a public interface: `/scrape`
fetches arbitrary URLs (SSRF surface).
"""

from __future__ import annotations

import asyncio
import copy
import os
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import ScrapeConfig
from .pipeline import Pipeline
from .robots import RobotsPolicy

DEFAULT_PORT = 8743


class ScrapeOptions(BaseModel):
    maxTextChars: int | None = None
    fitText: bool = False
    ignoreRobots: bool = False
    noConsent: bool = False
    noExpand: bool = False
    noApis: bool = False
    maxRetries: int | None = None
    maxApiResponses: int | None = None
    cacheDir: str | None = None
    rawHtml: bool = False


class ScrapeRequest(BaseModel):
    url: str
    options: ScrapeOptions = Field(default_factory=ScrapeOptions)


def check_token(token: str | None, authorization: str | None) -> None:
    """Raise HTTPException 401 when the server is token-protected and the
    request does not carry the right bearer token. When token is None, no
    authentication is enforced (open mode)."""
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid or missing token")


def create_app(base_cfg: ScrapeConfig, max_concurrency: int = 4, token: str | None = None) -> FastAPI:
    pipeline: Pipeline | None = None
    sem: asyncio.Semaphore | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal pipeline, sem
        robots = RobotsPolicy(cache_dir=base_cfg.cache_dir)
        pipeline = Pipeline(base_cfg, robots)
        await pipeline.start()
        sem = asyncio.Semaphore(max_concurrency)
        yield
        if pipeline is not None:
            await pipeline.close()

    app = FastAPI(title="gscrape serve", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=400, content={"error": "invalid request", "detail": exc.errors()})

    @app.get("/health")
    async def health(authorization: str | None = Header(None)):
        check_token(token, authorization)
        return {"status": "ok", "version": __version__}

    @app.post("/scrape")
    async def scrape(req: ScrapeRequest, authorization: str | None = Header(None)):
        check_token(token, authorization)
        url = req.url.strip()
        if not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="url must be http(s)")
        cfg = copy.copy(base_cfg)
        o = req.options
        if o.maxTextChars is not None:
            cfg.max_text_chars = o.maxTextChars
        cfg.fit_text = o.fitText
        cfg.ignore_robots = o.ignoreRobots
        cfg.handle_consent = not o.noConsent
        cfg.expand = not o.noExpand
        cfg.capture_apis = not o.noApis
        if o.maxRetries is not None:
            cfg.max_retries = o.maxRetries
        if o.maxApiResponses is not None:
            cfg.max_api_responses = o.maxApiResponses
        if o.cacheDir:
            cfg.cache_dir = o.cacheDir
        cfg.raw_html = o.rawHtml
        if pipeline is None or sem is None:
            raise HTTPException(status_code=500, detail="server not initialized")
        try:
            async with sem:
                record = await pipeline.run_one(url, cfg=cfg)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        return record.to_dict()

    return app


def serve_main(argv: list[str]) -> int:
    """`gscrape serve` CLI entrypoint (dispatched from cli.main)."""
    import argparse

    p = argparse.ArgumentParser(prog="gscrape serve")
    p.add_argument("--port", type=int, default=int(os.environ.get("GSCRAPE_PORT", DEFAULT_PORT)))
    p.add_argument("--cache-dir")
    p.add_argument("--max-concurrency", type=int, default=4)
    p.add_argument("--token")
    p.add_argument("--no-consent", action="store_true")
    p.add_argument("--no-expand", action="store_true")
    p.add_argument("--no-apis", action="store_true")
    p.add_argument("--ignore-robots", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    base_cfg = ScrapeConfig(
        cache_dir=args.cache_dir,
        handle_consent=not args.no_consent,
        expand=not args.no_expand,
        capture_apis=not args.no_apis,
        ignore_robots=args.ignore_robots,
        verbose=args.verbose,
    )
    app = create_app(base_cfg, max_concurrency=max(1, args.max_concurrency), token=args.token)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0