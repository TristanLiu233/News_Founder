"""Conservative text cleanup shared by profile adapters: no rewriting, only normalization."""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<[^>]+>")
_KANA = re.compile(r"[぀-ヿ]")
_CJK = re.compile(r"[㐀-鿿]")


def clean_text(value: str | None, limit: int | None = None) -> str | None:
    """Remove markup, unescape entities, collapse whitespace, and shorten at a word boundary."""

    if not value:
        return None
    text = " ".join(html.unescape(_TAG.sub(" ", value)).split())
    if not text:
        return None
    if limit and len(text) > limit:
        cut = text[: limit - 1]
        # Only back off to a space for space-delimited text; CJK text has no word spaces.
        if " " in cut[limit // 2 :]:
            cut = cut[: cut.rindex(" ")]
        text = cut.rstrip(" ,;:") + "…"
    return text


def detect_language(text: str, declared: str | None = None) -> str:
    """The page's declared language when present, otherwise a script-based fallback."""

    if declared and declared.strip():
        # Region subtags only matter for Chinese script (zh-CN vs zh-TW); a geo-redirected
        # English page (en-AU, en-US) is still English.
        tag = declared.strip().replace("_", "-")
        primary = tag.split("-", 1)[0].lower()
        return tag[:20] if primary == "zh" else primary[:20]
    if _KANA.search(text):
        return "ja"
    if _CJK.search(text):
        return "zh"
    return "en"
