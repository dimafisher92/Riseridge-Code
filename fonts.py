"""Base64-inline the RiseRidge brand fonts as @font-face rules.

Chrome --print-to-pdf will not fetch remote Google Fonts in headless mode and
silently falls back to Times/Arial. Two rules, both learned the hard way on the
OLASBET and Trybello report pipelines:

  1. Request ONE weight per CSS call. Multi-weight calls (and the whole css2
     API) return VARIABLE fonts, which Chrome fails to embed.
  2. Keep `unicode-range` on every rule. Several subsets for one weight without
     it makes the last rule shadow the others and silently breaks the font.

All three families are SIL Open Font License.
"""

import base64
import os
import re
import urllib.request

FAMILIES = {
    "Cormorant+Garamond": [500, 600, 700],
    "Hanken+Grotesk": [400, 500, 600, 700],
    "Space+Mono": [400, 700],
}
CSS_URLS = [
    "https://fonts.googleapis.com/css?family=%s:%d&display=swap" % (fam, w)
    for fam, ws in FAMILIES.items()
    for w in ws
]
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# Keep a face if its unicode-range covers basic Latin or the Latin Extended-A
# block start. Chosen over substring matching on Google's range strings, which
# silently dropped all latin-ext faces when Google's latin-ext range changed
# from U+0100-024F to U+0100-02BA.
WANTED_CODEPOINTS = (0x0041, 0x0100)   # 'A' -> latin, 'Latin capital A with macron' -> latin-ext
BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "state", "fonts.css")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _range_covers(unicode_range, codepoints):
    """True if the CSS unicode-range string covers any of the given codepoints."""
    for part in unicode_range.split(","):
        part = part.strip()
        if not part.upper().startswith("U+"):
            continue
        body = part[2:]
        try:
            if "-" in body:
                lo_s, hi_s = body.split("-", 1)
                lo, hi = int(lo_s, 16), int(hi_s, 16)
            else:
                lo = hi = int(body, 16)
        except ValueError:
            continue
        if any(lo <= cp <= hi for cp in codepoints):
            return True
    return False


def _build():
    blocks = []
    for u in CSS_URLS:
        blocks += re.findall(r"@font-face\s*\{[^}]+\}", get(u).decode("utf-8"))

    out, seen = [], set()
    for b in blocks:
        fam = re.search(r"font-family:\s*'([^']+)'", b)
        wgt = re.search(r"font-weight:\s*([\d ]+)", b)
        sty = re.search(r"font-style:\s*(\w+)", b)
        url = re.search(r"url\((https://[^)]+\.woff2)\)", b)
        rng = re.search(r"unicode-range:\s*([^;]+);", b)
        if not (fam and url and rng):
            continue
        ur = rng.group(1).strip()
        if not _range_covers(ur, WANTED_CODEPOINTS):
            continue
        key = (fam.group(1), (wgt.group(1) if wgt else "400").strip(),
               sty.group(1) if sty else "normal", ur)
        if key in seen:
            continue
        seen.add(key)
        b64 = base64.b64encode(get(url.group(1))).decode("ascii")
        out.append(
            "@font-face{font-family:'%s';font-style:%s;font-weight:%s;"
            "font-display:block;src:url(data:font/woff2;base64,%s) format('woff2');"
            "unicode-range:%s;}" % (key[0], key[2], key[1], b64, ur)
        )
    return "\n".join(out)


def font_css(cache_path=None, refresh=False):
    path = str(cache_path or CACHE)
    if not refresh and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cached = fh.read()
            if cached and "@font-face" in cached:
                return cached
        except (IOError, OSError):
            pass
    css = _build()
    if not css or "@font-face" not in css:
        raise RuntimeError(
            "No @font-face rules generated; refusing to cache an unusable stylesheet"
        )
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as fh:
        fh.write(css)
    os.replace(temp_path, path)
    return css


if __name__ == "__main__":
    print("%.1f KB cached to %s" % (len(font_css(refresh=True)) / 1024, CACHE))
