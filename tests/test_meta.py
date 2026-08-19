"""Unit tests for meta.py (pure HTML parsing, no browser)."""

from growth_scraper.meta import detect_language, extract_meta

LANG_ATTR_HTML = '<html lang="es"><head><title>Acme</title></head><body><h1>Hola</h1></body></html>'

CONTENT_LANG_HTML = '''<html><head><meta http-equiv="content-language" content="fr-FR"></head>
<body><h1>Bonjour</h1></body></html>'''

NAME_LANG_HTML = '<html><head><meta name="language" content="de"></head><body><h1>Hallo</h1></body></html>'

EN_TEXT = ("The quick brown fox jumps over the lazy dog in the park and this is a "
           "sample of a longer text that should be detected as English because the "
           "and of to in for with that this are used more than twenty times in the "
           "following sentences that repeat the same words over and over again so the "
           "classifier has enough signal to pick the right language for this page "
           "content body.") * 3

ES_TEXT = ("El perro corre por el parque y la casa es grande y bonita pero el gato "
           "no quiere jugar con los niños en la calle de la ciudad que esta cerca del "
           "mar y las montañas y por eso la familia va de paseo cada fin de semana al "
           "campo para descansar del trabajo y de la rutina diaria de la vida en la "
           "gran ciudad moderna.") * 3

META_HTML = '''<html><head>
<meta property="og:title" content="Fotografía Alba">
<meta property="og:description" content="Fotógrafa de bodas en Valencia.">
<meta property="og:image" content="/img/alba.jpg">
<meta name="twitter:card" content="summary_large_image">
<link rel="canonical" href="/fotografia-alba">
<meta name="author" content="Alba Ruiz">
<meta property="article:published_time" content="2026-01-15T10:00:00Z">
<link rel="icon" href="/favicon.png">
</head><body><h1>Fotografía Alba</h1></body></html>'''


def test_lang_from_html_attr():
    assert detect_language(LANG_ATTR_HTML, "") == "es"


def test_lang_from_content_language_meta():
    assert detect_language(CONTENT_LANG_HTML, "") == "fr"


def test_lang_from_name_language_meta():
    assert detect_language(NAME_LANG_HTML, "") == "de"


def test_lang_from_content_stopwords_en():
    assert detect_language("<html><body></body></html>", EN_TEXT) == "en"


def test_lang_from_content_stopwords_es():
    assert detect_language("", ES_TEXT) == "es"


def test_lang_empty_none():
    assert detect_language("", "") is None


def test_lang_short_text_none():
    assert detect_language("<html><body></body></html>", "hello world and stuff") is None


def test_extract_meta_fields():
    m = extract_meta(META_HTML, "https://example.com/post")
    assert m["ogTitle"] == "Fotografía Alba"
    assert m["ogDescription"] == "Fotógrafa de bodas en Valencia."
    assert m["twitterCard"] == "summary_large_image"
    assert m["author"] == "Alba Ruiz"
    assert m["publishedAt"] == "2026-01-15T10:00:00Z"


def test_extract_meta_relative_urls_absolute():
    m = extract_meta(META_HTML, "https://example.com/post")
    assert m["canonical"] == "https://example.com/fotografia-alba"
    assert m["ogImage"] == "https://example.com/img/alba.jpg"
    assert m["favicon"] == "https://example.com/favicon.png"


def test_extract_meta_empty_all_none():
    m = extract_meta("", "https://example.com")
    assert set(m.keys()) == {"canonical", "ogTitle", "ogDescription", "ogImage",
                             "twitterCard", "author", "publishedAt", "favicon"}
    assert all(v is None for v in m.values())


def test_extract_meta_broken_html_no_raise():
    m = extract_meta("<meta property=og:title content=unclosed", "https://example.com")
    assert m is not None


def test_record_to_dict_meta_screenshots():
    from growth_scraper.config import Record

    r = Record(url="https://example.com")
    d = r.to_dict()
    assert d["meta"] is None
    assert d["screenshots"] is None

    r.meta = {"ogTitle": "X"}
    r.screenshots = ["/tmp/a.png"]
    d = r.to_dict()
    assert d["meta"] == {"ogTitle": "X"}
    assert d["screenshots"] == ["/tmp/a.png"]