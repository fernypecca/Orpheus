"""Ad-library extraction (Google verified live; FB/LinkedIn stubs documented)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from growth_scraper.ads import detect_library, extract_ads
from growth_scraper.pipeline import _build_summary

FIXTURE = Path(__file__).parent / "fixtures" / "google_ads_cards.html"
GURL = "https://adstransparency.google.com/advertiser/AR14188379519798214657?region=US"


def test_detect_library():
    assert detect_library(GURL) == "google"
    assert detect_library("https://www.facebook.com/ads/library/?query=x") == "facebook"
    assert detect_library("https://www.linkedin.com/ad-library/search?x=1") == "linkedin"
    assert detect_library("https://stripe.com/pricing") is None
    assert detect_library("not a url at all") is None


def test_google_cards_from_live_fixture():
    html = FIXTURE.read_text(encoding="utf-8")
    ads = extract_ads(html, GURL)
    assert len(ads) == 2, len(ads)
    img, vid = ads
    assert img["library"] == "google" and img["advertiser"] == "Google LLC"
    assert "/creative/CR" in (img["creative_url"] or "") and "region=US" in img["creative_url"]
    assert img["image_url"] and "googlesyndication.com" in img["image_url"]
    assert img["format"] == "image" and vid["format"] == "video"
    assert img["image_width"] == 348 and img["image_height"] == 160
    assert img["position"] == 1 and img["total"] == 120


def test_ads_fail_open_and_stubs():
    assert extract_ads("", GURL) == []
    assert extract_ads("<html><body><p>x</p></body></html>", GURL) == []
    assert extract_ads("<html></html>", "https://stripe.com/pricing") == []
    # facebook/linkedin: documented stubs until a live-DOM pass is possible
    fb = "https://www.facebook.com/ads/library/?query=x"
    li = "https://www.linkedin.com/ad-library/search?x=1"
    assert extract_ads("<html><body><div>ad</div></body></html>", fb) == []
    assert extract_ads("<html><body><div>ad</div></body></html>", li) == []


def test_summary_ads_fields():
    s = _build_summary("u", "t", "generic", [], "text", "",
                       ads=[{"library": "google"}], ads_library="google")
    assert s["adsCount"] == 1 and s["adsLibrary"] == "google"
    s2 = _build_summary("u", "t", "generic", [], "text", "")
    assert "adsCount" not in s2 and "adsLibrary" not in s2
