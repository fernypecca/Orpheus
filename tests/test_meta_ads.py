"""Meta Ad Library fetcher — mocked HTTP, no network, no token needed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from growth_scraper.meta_ads import _normalize, fetch_meta_ads


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Client:
    """Scripted responses in order; records params for assertions."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def close(self):
        self.closed = True


_AD = {
    "id": "123", "page_id": "9", "page_name": "Acme ES",
    "ad_snapshot_url": "https://www.facebook.com/ads/library/?id=123",
    "ad_creative_bodies": ["Zapatillas al -30% solo hoy"],
    "ad_creative_link_titles": ["Comprar ahora"],
    "ad_creative_link_descriptions": ["Envío gratis"],
    "ad_delivery_start_time": "2026-09-01",
    "publisher_platforms": ["facebook", "instagram"],
}


def _page(items, after=None):
    paging = {"cursors": {"after": after}} if after else {}
    if after:
        paging["next"] = "https://graph.facebook.com/next?after=" + after
    return {"data": items, "paging": paging}


def test_normalize_shape():
    a = _normalize(dict(_AD))
    assert a["library"] == "meta" and a["advertiser"] == "Acme ES"
    assert "Zapatillas" in a["text"] and "Comprar ahora" in a["text"]
    assert a["creative_url"].endswith("id=123") and a["id"] == "meta:123"
    assert a["platforms"] == ["facebook", "instagram"]


def test_fetch_paginates_and_caps():
    c = _Client([_Resp(200, _page([_AD], after="A1")),
                 _Resp(200, _page([_AD, _AD]))])
    ads, err = fetch_meta_ads(token="TOK", search_terms="zapatillas",
                              countries=["ES"], limit=3, _client=c)
    assert err is None and len(ads) == 3


def test_fetch_injected_client_not_closed():
    c = _Client([_Resp(200, _page([_AD]))])
    fetch_meta_ads(token="TOK", _client=c)
    assert c.closed is False


def test_fetch_errors_honest_and_token_safe():
    ads, err = fetch_meta_ads(token="", _client=_Client([]))
    assert ads == [] and "token" in err
    ads, err = fetch_meta_ads(token="TOK", ad_type="NOPE", _client=_Client([]))
    assert "ad_type" in err
    c = _Client([_Resp(400, {"error": {"code": 190, "message": "bad"}})])
    ads, err = fetch_meta_ads(token="SECRET-TOKEN", _client=c)
    assert "token" in err.lower() and "SECRET-TOKEN" not in err
    c = _Client([_Resp(400, {"error": {"code": 100}})])
    ads, err = fetch_meta_ads(token="T", _client=c)
    assert "parameters" in err


def test_rate_limit_backoff_then_success(monkeypatch):
    import growth_scraper.meta_ads as m

    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    c = _Client([_Resp(400, {"error": {"code": 613}}),
                 _Resp(200, _page([_AD]))])
    ads, err = fetch_meta_ads(token="T", _client=c)
    assert err is None and len(ads) == 1 and len(c.calls) == 2
    assert c.calls[0][1]["ad_reached_countries"] == '["ES"]'
