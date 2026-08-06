import fonts


def test_font_css_uses_cache_without_network(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    cache.write_text("@font-face{font-family:'Hanken Grotesk';}", encoding="utf-8")

    def boom(url):
        raise AssertionError("network hit despite warm cache")

    monkeypatch.setattr(fonts, "get", boom)
    assert "Hanken Grotesk" in fonts.font_css(cache_path=cache)


def test_font_css_writes_cache_on_first_call(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    css = ("@font-face{font-family:'Hanken Grotesk';font-style:normal;"
           "font-weight:400;src:url(https://x/a.woff2) format('woff2');"
           "unicode-range:U+0000-00FF;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"FONTBYTES")
    out = fonts.font_css(cache_path=cache)
    assert "base64," in out
    assert cache.exists()
    assert "base64," in cache.read_text(encoding="utf-8")


def test_font_css_keeps_unicode_range(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    css = ("@font-face{font-family:'Space Mono';font-style:normal;font-weight:400;"
           "src:url(https://x/a.woff2) format('woff2');unicode-range:U+0000-00FF;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"B")
    assert "unicode-range:U+0000-00FF" in fonts.font_css(cache_path=cache)


def test_font_css_skips_unwanted_unicode_ranges(tmp_path, monkeypatch):
    cache = tmp_path / "fonts.css"
    css = ("@font-face{font-family:'Space Mono';font-style:normal;font-weight:400;"
           "src:url(https://x/cyr.woff2) format('woff2');unicode-range:U+0400-045F;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"B")
    # Should raise because no wanted ranges match; this prevents silently empty stylesheets
    try:
        fonts.font_css(cache_path=cache)
        assert False, "Expected RuntimeError for unwanted ranges"
    except RuntimeError as e:
        assert "No @font-face rules" in str(e)


def test_range_covers_keeps_latin():
    """_range_covers recognizes the basic Latin range."""
    assert fonts._range_covers("U+0000-00FF", fonts.WANTED_CODEPOINTS)


def test_range_covers_keeps_google_latin_ext():
    """_range_covers recognizes Google's actual latin-ext range string."""
    google_latin_ext = ("U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, "
                       "U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF")
    assert fonts._range_covers(google_latin_ext, fonts.WANTED_CODEPOINTS)


def test_range_covers_rejects_cyrillic_ext():
    """_range_covers rejects cyrillic-ext which covers neither 0x41 nor 0x100."""
    cyrillic_ext = "U+0460-052F, U+1C80-1C8A, U+20B4, U+2DE0-2DFF, U+A640-A69F"
    assert not fonts._range_covers(cyrillic_ext, fonts.WANTED_CODEPOINTS)


def test_range_covers_rejects_vietnamese():
    """_range_covers rejects vietnamese which starts at U+0102, missing both codepoints."""
    vietnamese = "U+0102-0103, U+0110-0111, U+0128-0129, U+0168-0169, U+01A0-01A1"
    assert not fonts._range_covers(vietnamese, fonts.WANTED_CODEPOINTS)


def test_build_includes_latin_ext_face(monkeypatch):
    """_build includes a latin-ext face when the mocked CSS contains one."""
    # Simulate Google Fonts returning both latin and latin-ext blocks for one weight
    google_css = (
        "@font-face{font-family:'Hanken Grotesk';font-style:normal;"
        "font-weight:400;src:url(https://x/latin.woff2) format('woff2');"
        "unicode-range:U+0000-00FF;}"
        "@font-face{font-family:'Hanken Grotesk';font-style:normal;"
        "font-weight:400;src:url(https://x/latin-ext.woff2) format('woff2');"
        "unicode-range:U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, "
        "U+02DD-02FF, U+0304, U+0308, U+0329, U+1D00-1DBF;}"
    )
    monkeypatch.setattr(fonts, "get",
                        lambda u: google_css.encode() if "googleapis" in u else b"X")
    out = fonts._build()
    # Should have both faces (2 @font-face blocks)
    assert out.count("@font-face") == 2
    # Both should have unicode-range preserved
    assert out.count("unicode-range") == 2


def test_font_css_rebuilds_empty_cache(tmp_path, monkeypatch):
    """font_css rebuilds from scratch if the cache file is empty."""
    cache = tmp_path / "fonts.css"
    cache.write_text("", encoding="utf-8")
    css = ("@font-face{font-family:'Space Mono';font-style:normal;font-weight:400;"
           "src:url(https://x/a.woff2) format('woff2');unicode-range:U+0000-00FF;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"B")
    out = fonts.font_css(cache_path=cache)
    assert "base64," in out
    assert "@font-face" in cache.read_text(encoding="utf-8")


def test_font_css_rebuilds_cache_with_no_font_face(tmp_path, monkeypatch):
    """font_css rebuilds from scratch if the cache file lacks @font-face."""
    cache = tmp_path / "fonts.css"
    cache.write_text("/* stale comment, no faces */", encoding="utf-8")
    css = ("@font-face{font-family:'Space Mono';font-style:normal;font-weight:400;"
           "src:url(https://x/a.woff2) format('woff2');unicode-range:U+0000-00FF;}")
    monkeypatch.setattr(fonts, "get",
                        lambda u: css.encode() if "googleapis" in u else b"B")
    out = fonts.font_css(cache_path=cache)
    assert "base64," in out
    assert "@font-face" in cache.read_text(encoding="utf-8")


def test_font_css_raises_on_build_failure(tmp_path, monkeypatch):
    """font_css raises RuntimeError if _build yields no @font-face."""
    cache = tmp_path / "fonts.css"
    # Mock get to return CSS with no @font-face blocks
    monkeypatch.setattr(fonts, "get",
                        lambda u: b"/* no fonts here */")
    try:
        fonts.font_css(cache_path=cache)
        assert False, "Expected RuntimeError"
    except RuntimeError as e:
        assert "No @font-face rules" in str(e)
    # Cache should not exist or should be empty (atomic write prevented bad file)
    if cache.exists():
        assert "@font-face" not in cache.read_text(encoding="utf-8")
