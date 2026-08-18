"""Constants, defaults and shared dataclasses.

Every tunable lives here, at the top of the file, so non-developers can adjust
behavior without reading the rest of the codebase (CLAUDE.md convention).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Defaults (CLI flags override these)
# ---------------------------------------------------------------------------
DEFAULT_DELAY = 0.4          # seconds between page loads (politeness floor)
DEFAULT_JITTER = 0.2         # random extra delay, uniform(0, jitter)
DEFAULT_MAX_PAGES = 50       # --crawl page cap
DEFAULT_CRAWL_CONCURRENCY = 2    # parallel page loads during crawl (robots/delay respected)
DEFAULT_MAX_API_RESPONSES = 30   # cap on apiResponses per page record
MAX_API_BODY_BYTES = 500_000     # drop JSON bodies larger than this
DEFAULT_MAX_EXPANSIONS = 25      # max auto-clicks for collapsed content per page
DEFAULT_EXPANSION_WAIT_MS = 350  # wait after a click before checking for XHRs
DEFAULT_PAGE_TIMEOUT_MS = 30_000
DEFAULT_MAX_TEXT_CHARS = 12_000  # --max-text-chars: keep `text` LLM/TPM-friendly by default
DEFAULT_MAX_RETRIES = 2          # transient errors (timeout/5xx) are retried with backoff
DEFAULT_RETRY_BACKOFF = 1.5      # base seconds; grows exponentially per attempt
SITEMAP_MAX_URLS = 2_000         # cap on URLs fetched from a sitemap
MAX_PAGINATION_PAGES = 5         # P1 API replay cap
MAX_LISTING_ITEMS = 50           # items extracted from a listing page
ROBOTS_UA_TOKEN = "GrowthScraperBot/0.1 (+research; respects robots.txt)"
ROBOTS_CACHE_TTL_SECONDS = 86_400  # 1 day

# ---------------------------------------------------------------------------
# Click guard
# ---------------------------------------------------------------------------
# Words that make an element NEVER safe to auto-click. Multi-language on purpose.
CLICK_BLACKLIST = [
    "comprar", "buy", "purchase", "pagar", "pay", "checkout", "carrito",
    "cart", "eliminar", "delete", "borrar", "remove", "cancelar", "cancel",
    "enviar", "submit", "send", "suscribirse", "subscribe", "iniciar sesion",
    "log in", "login", "sign in", "confirm", "confirmar", "aceptar", "accept",
    "agree", "add to cart", "agregar al carrito", "comprar ahora", "buy now",
    "donate", "donar", "registrarse", "register", "crear cuenta",
    "create account", "reservar", "book now", "reserve", "check availability",
    "solicitar", "request", "price", "precio", "oferta", "offer", "descargar",
    "download", "compartir", "share", "newsletter", "boletin", "boletín",
]

# Cookie/consent containers: NEVER auto-click inside these for expansion, and
# never treat their content as page content. Consent handling is the ONLY
# code that may touch them (and only to reject / hide).
COOKIE_CONTAINER_SELECTORS = [
    '[class*="onetrust"]', '[id*="onetrust"]',
    '[class*="cookie"]', '[id*="cookie"]',
    '[class*="consent"]', '[id*="consent"]',
    '[class*="cmp"]', '[id*="cmp"]',
    '[class*="didomi"]', '[id*="didomi"]',
    '[class*="gdpr"]', '[id*="gdpr"]',
    '[class*="cookiebot"]', '[id*="CybotCookiebotDialog"]',
    '[class*="sp_message"]', '[id*="sp_message"]',
    '[class*="iubenda"]', '[id*="iubenda"]',
    '[class*="qc-cmp"]', '[id*="qc-cmp"]',
    '[class*="truste"]', '[id*="truste"]',
    '[class*="usercentrics"]', '[id*="usercentrics"]',
    '[class*="klaro"]', '[class*="osano"]', '[class*="complianz"]',
    '[class*="onetrust-pc-dark-filter"]', '[id*="ot-sdk"]', '[class*="ot-sdk"]',
]

# Consent REJECT button text, multi-language. We never click anything that
# matches accept patterns; these are the only texts we look for inside consent
# containers (plus CMP-specific selectors in consent.py).
REJECT_TEXT_PATTERNS = [
    "reject", "decline", "deny", "refuse",
    "only essential", "necessary only", "essential only", "functional only",
    "reject all", "decline all", "deny all",
    "rechazar", "rechazo", "denegar", "no aceptar", "rechazar todas",
    "solo esenciales", "solo necesarias", "solo las necesarias",
    "continue without accepting", "continue without agreeing",
    "continuar sin aceptar", "continuar sin acuerdo",
    "no thanks", "no, gracias", "no, gracias por ahora",
    "manage options", "manage settings", "config", "settings",
    "personalizar", "personalize", "customize", "preferencias", "preferences",
    "ver opciones", "see options", "more options", "mas opciones",
    "close", "cerrar", "later", "mas tarde", "más tarde",
]

# Accept patterns — used to make sure we NEVER click these.
ACCEPT_TEXT_PATTERNS = [
    "accept", "accept all", "accept cookies", "i accept", "agree",
    "allow all", "allow", "continue", "aceptar", "aceptar todo",
    "aceptar todas", "de acuerdo", "ok", "entendido", "got it",
]

# "Load more"-style list buttons (optional, guarded). Semantic-only expansion
# is preferred; these are a fallback for infinite lists and are still gated by
# the click blacklist.
LOAD_MORE_TEXT_PATTERNS = [
    "load more", "show more", "see more", "view more", "more results",
    "cargar mas", "cargar más", "mostrar mas", "mostrar más", "ver mas",
    "ver más", "show all", "ver todos", "see all", "más resultados",
]

# ---------------------------------------------------------------------------
# Anti-bot / protection indicators (fail-closed detection)
# ---------------------------------------------------------------------------
PROTECTED_TITLE_FRAGMENTS = [
    "attention required", "just a moment", "verify you are human",
    "checking your browser", "access denied", "ddos-guard",
    "please enable cookies", "enable javascript and cookies",
]
# Only STRUCTURAL markers that can't appear in ordinary prose (e.g. an article
# about web scraping legitimately mentions "datadome"/"captcha"). Real block
# pages are caught by title hints, 403/429 + anti-bot server, or these shells.
PROTECTED_HTML_FRAGMENTS = [
    "challenge-running", "challenge-form", "cf-browser-verification",
    "cf_chl_opt", "__cf_chl", "_px3",
]

# ---------------------------------------------------------------------------
# Structured extraction (P2): heuristic selectors, used only when a page has no
# schema/meta signals. Multi-language on purpose (same convention as the click
# guard blacklist).
# ---------------------------------------------------------------------------
STRUCTURED_MAX_JSONLD_BYTES = 1_000_000  # guard against huge @graph blobs
STRUCTURED_PRICE_SELECTORS = [
    "[itemprop='price']", "[data-price]", ".price", "[class*='price']",
    ".precio", "[data-precio]",
]
STRUCTURED_RATING_SELECTORS = [
    "[itemprop='ratingValue']", "[class*='rating']", "[class*='stars']",
    "[data-rating]", "[aria-label*='stars']", ".valoracion",
]
STRUCTURED_REVIEW_COUNT_SELECTORS = [
    "[itemprop='reviewCount']", "[class*='review-count']", "[class*='reviews']",
    "[data-review-count]", ".num-resenas",
]
STRUCTURED_CATEGORY_SELECTORS = [
    ".breadcrumb", "[class*='breadcrumb']", "[aria-label='breadcrumb']",
    "nav[aria-label*='breadcrumb']", "[itemprop='itemListElement']",
]
STRUCTURED_PHONE_SELECTORS = [
    "[itemprop='telephone']", "a[href^='tel:']",
]
STRUCTURED_EMAIL_SELECTORS = [
    "[itemprop='email']", "a[href^='mailto:']",
]
STRUCTURED_ADDRESS_SELECTORS = [
    "[itemprop='address']", "address",
]


@dataclass
class ScrapeConfig:
    """Everything the pipeline needs for one run."""

    output: str = "out.jsonl"
    delay: float = DEFAULT_DELAY
    jitter: float = DEFAULT_JITTER
    ignore_robots: bool = False
    cache_dir: Optional[str] = None
    max_pages: int = DEFAULT_MAX_PAGES
    concurrency: int = DEFAULT_CRAWL_CONCURRENCY
    expand: bool = True
    capture_apis: bool = True
    handle_consent: bool = True
    export_images: Optional[str] = None
    max_api_responses: int = DEFAULT_MAX_API_RESPONSES
    max_expansions: int = DEFAULT_MAX_EXPANSIONS
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS
    fit_text: bool = False
    raw_html: bool = False
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_backoff: float = DEFAULT_RETRY_BACKOFF
    csv_output: bool = False
    page_timeout_ms: int = DEFAULT_PAGE_TIMEOUT_MS
    headful: bool = False
    verbose: bool = False

    def polite_delay(self, crawl_delay: Optional[float] = None) -> float:
        """Effective delay per request: config floor, robots crawl-delay wins if higher."""
        base = max(self.delay, crawl_delay or 0)
        return base + (__import__("random").uniform(0, self.jitter))


@dataclass
class ApiResponse:
    url: str
    body: Any


@dataclass
class Record:
    """One JSONL line."""

    url: str
    title: str = ""
    text: str = ""
    apiResponses: list = field(default_factory=list)
    pageType: str = "generic"
    items: list = field(default_factory=list)
    images: list = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    error: Optional[str] = None
    crawledFrom: Optional[str] = None
    scrapedAt: str = ""
    statusCode: Optional[int] = None
    protectionBlocked: bool = False
    rawHtml: Optional[str] = None
    structured: Optional[dict] = None
    fromCache: bool = False

    def to_dict(self) -> dict:
        d = {
            "url": self.url,
            "title": self.title,
            "text": self.text,
            "apiResponses": self.apiResponses,
            "pageType": self.pageType,
            "items": self.items,
            "images": self.images,
            "summary": self.summary,
            "error": self.error,
            "crawledFrom": self.crawledFrom,
            "scrapedAt": self.scrapedAt,
            "statusCode": self.statusCode,
            "protectionBlocked": self.protectionBlocked,
            "structured": self.structured,
        }
        # Opt-in (--raw-html): unprocessed HTML, for consumers that need what
        # Crawl4AI's cleaning strips (e.g. inline JSON <script> blocks).
        if self.rawHtml is not None:
            d["rawHtml"] = self.rawHtml
        if self.fromCache:
            d["fromCache"] = True
        return d