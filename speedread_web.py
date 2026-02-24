#!/usr/bin/env python3
"""
speedread_web.py

Single-file local web app (Flask) for read-only PDF/EPUB viewing + RSVP/ORP speed reading.

Features:
- Upload PDF/EPUB (read-only)
- Extract text from PDF/EPUB
- Document view + search
- RSVP mode with ORP center focus (improved alignment)
- Adjustable WPM, chunk size, phrase mode, punctuation pauses
- Experimental per-eye split mode (single display, left/right lanes):
  - per chunk
  - per word (round robin)
  - per sentence
- Adjustable L/R inter-eye delay
- Start per-eye alternation from RIGHT eye toggle
- Desktop/Meta Quest UI profiles (desktop default)
- Color vision themes (normal / protanopia / deuteranopia / tritanopia / monochrome / high contrast)
- Optional per-eye guide lines (red)
- No tkinter required
"""

from __future__ import annotations

import html
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import List, Tuple

from flask import Flask, request, render_template_string, jsonify

# -------------------------------
# Optional imports (graceful fallback)
# -------------------------------
HAS_PYPDF = False
HAS_EBOOKLIB = False
HAS_BS4 = False

try:
    from pypdf import PdfReader  # type: ignore
    HAS_PYPDF = True
except Exception:
    try:
        from PyPDF2 import PdfReader  # type: ignore
        HAS_PYPDF = True
    except Exception:
        HAS_PYPDF = False

try:
    from ebooklib import epub  # type: ignore
    HAS_EBOOKLIB = True
except Exception:
    HAS_EBOOKLIB = False

try:
    from bs4 import BeautifulSoup  # type: ignore
    HAS_BS4 = True
except Exception:
    HAS_BS4 = False


# ============================================================
# Function List (explicit to help preserve all functions)
# ============================================================
# normalize_whitespace
# split_preserving_whitespace
# tokenize_for_rsvp
# build_chunks
# compute_orp_index
# estimate_pause_multiplier
# is_url_like
# extract_text_from_pdf
# extract_text_from_epub
# extract_text_from_epub_fallback
# extract_text_from_file
# allowed_file
# build_payload
# index
# api_extract
# main

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)

TOKEN_RE = re.compile(
    r"""
    (\n{2,})                           # paragraph break(s)
    |([A-Za-z0-9]+(?:['’][A-Za-z0-9]+)*(?:-[A-Za-z0-9]+)*)  # words/hyphenated
    |(\.\.\.)                          # ellipsis
    |([—–])                            # dashes
    |([.,;:!?()\[\]{}"“”'‘’/\\])       # punctuation/symbols
    |(\s+)                             # whitespace
    |(.)                               # fallback any char
    """,
    re.VERBOSE | re.UNICODE,
)


def normalize_whitespace(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_preserving_whitespace(text: str) -> List[str]:
    return [m.group(0) for m in TOKEN_RE.finditer(text)]


def tokenize_for_rsvp(text: str) -> List[str]:
    raw = split_preserving_whitespace(text)
    tokens: List[str] = []
    current = ""

    def flush_current() -> None:
        nonlocal current
        if current.strip():
            tokens.append(current.strip())
        current = ""

    for tok in raw:
        if not tok:
            continue

        if tok.isspace():
            if "\n\n" in tok:
                flush_current()
                tokens.append("<PARA>")
            else:
                flush_current()
            continue

        if tok in {".", ",", ";", ":", "!", "?", "...", "—", "–"}:
            if current:
                current += tok
                flush_current()
            elif tokens and tokens[-1] != "<PARA>":
                tokens[-1] += tok
            else:
                tokens.append(tok)
            continue

        if tok in {'"', "“", "”", "'", "‘", "’", "(", "[", "{", "/", "\\"}:
            if current:
                current += tok
            else:
                current = tok
            continue

        if tok in {")", "]", "}"}:
            if current:
                current += tok
                flush_current()
            elif tokens and tokens[-1] != "<PARA>":
                tokens[-1] += tok
            else:
                tokens.append(tok)
            continue

        if current:
            current += tok
        else:
            current = tok
        flush_current()

    flush_current()

    compact: List[str] = []
    for t in tokens:
        if t == "<PARA>" and compact and compact[-1] == "<PARA>":
            continue
        compact.append(t)
    return compact


def build_chunks(tokens: List[str], chunk_size: int = 1, phrase_mode: bool = False, skip_urls: bool = True) -> List[str]:
    if skip_urls:
        tokens = [t for t in tokens if t == "<PARA>" or not is_url_like(t)]

    if not tokens:
        return []

    if phrase_mode:
        chunks: List[str] = []
        current: List[str] = []
        max_tokens = max(3, chunk_size)

        for tok in tokens:
            if tok == "<PARA>":
                if current:
                    chunks.append(" ".join(current))
                    current = []
                chunks.append("<PARA>")
                continue

            current.append(tok)
            ends_phrase = bool(re.search(r"[.!?;:]$", tok))
            if ends_phrase or len(current) >= max_tokens:
                chunks.append(" ".join(current))
                current = []

        if current:
            chunks.append(" ".join(current))
        return chunks

    chunks: List[str] = []
    current: List[str] = []
    for tok in tokens:
        if tok == "<PARA>":
            if current:
                chunks.append(" ".join(current))
                current = []
            chunks.append("<PARA>")
            continue

        current.append(tok)
        if len(current) >= max(1, chunk_size):
            chunks.append(" ".join(current))
            current = []

    if current:
        chunks.append(" ".join(current))
    return chunks


def compute_orp_index(word: str) -> int:
    if not word:
        return 0
    core = re.sub(r"^[^\w]+|[^\w]+$", "", word, flags=re.UNICODE)
    core_len = len(core) if core else len(word)

    if core_len <= 1:
        idx = 0
    elif core_len <= 5:
        idx = 1
    elif core_len <= 9:
        idx = 2
    elif core_len <= 13:
        idx = 3
    else:
        idx = 4

    leading = 0
    for ch in word:
        if ch.isalnum():
            break
        leading += 1

    return max(0, min(leading + idx, max(0, len(word) - 1)))


def estimate_pause_multiplier(chunk: str, comma_mult: float, sentence_mult: float, para_mult: float) -> float:
    if chunk == "<PARA>":
        return max(1.0, para_mult)

    mult = 1.0
    if re.search(r"[.!?](?:['”’)\]]+)?$", chunk):
        mult = max(mult, sentence_mult)
    elif re.search(r"[,;:](?:['”’)\]]+)?$", chunk):
        mult = max(mult, comma_mult)

    if "..." in chunk:
        mult = max(mult, comma_mult + 0.25)
    return mult


def is_url_like(text: str) -> bool:
    return bool(URL_RE.search(text))


def extract_text_from_pdf(path: str) -> str:
    if not HAS_PYPDF:
        raise RuntimeError("PDF support requires pypdf (or PyPDF2). Install with: pip install pypdf")

    reader = PdfReader(path)
    pages_text: List[str] = []
    for i, page in enumerate(reader.pages):
        try:
            txt = page.extract_text() or ""
        except Exception:
            txt = ""
        pages_text.append(f"\n\n[Page {i + 1}]\n{txt}".rstrip())
    return normalize_whitespace("\n".join(pages_text))


def extract_text_from_epub(path: str) -> str:
    if HAS_EBOOKLIB and HAS_BS4:
        book = epub.read_epub(path)
        parts: List[str] = []
        for item in book.get_items():
            try:
                media_type = str(getattr(item, "media_type", ""))
                if "application/xhtml+xml" not in media_type and "text/html" not in media_type:
                    continue
                content = item.get_content()
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup(["script", "style", "nav"]):
                    tag.decompose()
                text = soup.get_text(separator=" ", strip=True)
                text = normalize_whitespace(text)
                if text:
                    parts.append(text)
            except Exception:
                continue
        if parts:
            return normalize_whitespace("\n\n".join(parts))

    return extract_text_from_epub_fallback(path)


def extract_text_from_epub_fallback(path: str) -> str:
    if not zipfile.is_zipfile(path):
        raise RuntimeError("Invalid EPUB file (not a zip archive).")

    html_files: List[Tuple[str, str]] = []
    with zipfile.ZipFile(path, "r") as zf:
        for name in zf.namelist():
            low = name.lower()
            if low.endswith((".xhtml", ".html", ".htm")):
                try:
                    data = zf.read(name)
                    text = data.decode("utf-8", errors="ignore")
                    html_files.append((name, text))
                except Exception:
                    continue

    if not html_files:
        raise RuntimeError("No readable HTML/XHTML content found in EPUB.")

    html_files.sort(key=lambda x: x[0])

    parts: List[str] = []
    for _, raw_html in html_files:
        cleaned = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", raw_html)
        cleaned = re.sub(r"(?is)<br\s*/?>", "\n", cleaned)
        cleaned = re.sub(r"(?is)</p\s*>", "\n\n", cleaned)
        cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        cleaned = html.unescape(cleaned)
        cleaned = normalize_whitespace(cleaned)
        if cleaned:
            parts.append(cleaned)

    return normalize_whitespace("\n\n".join(parts))


def extract_text_from_file(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".pdf":
        return extract_text_from_pdf(path)
    if ext == ".epub":
        return extract_text_from_epub(path)
    raise RuntimeError(f"Unsupported file type: {ext} (expected .pdf or .epub)")


def allowed_file(filename: str) -> bool:
    ext = Path(filename).suffix.lower()
    return ext in {".pdf", ".epub"}


def build_payload(text: str, filename: str) -> dict:
    tokens = tokenize_for_rsvp(text)
    chunks = build_chunks(tokens, chunk_size=1, phrase_mode=False, skip_urls=True)
    word_count = len(re.findall(r"\b\w+\b", text))
    return {
        "ok": True,
        "filename": filename,
        "text": text,
        "tokens": tokens,
        "chunks": chunks,
        "word_count": word_count,
        "char_count": len(text),
        "orp_helper": {"note": "ORP index is computed client-side for display"},
    }


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200MB


HTML_PAGE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Local Speed Reader (PDF/EPUB RSVP + ORP + Per-Eye Split + Quest Modes)</title>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <style>
    :root {
      --bg: #0f1115;
      --panel: #181c24;
      --panel2: #202633;
      --text: #e8edf5;
      --muted: #9fb0c8;
      --accent: #66b3ff;
      --danger: #ff5c5c;
      --line: #2e3645;
      --hl: #ffd54d;

      --eye-left: rgba(255, 90, 90, 0.95);
      --eye-right: rgba(90, 220, 255, 0.95);
      --eye-left-guide: rgba(255, 90, 90, 0.28);
      --eye-right-guide: rgba(90, 220, 255, 0.28);
      --orp-center: #ff6f6f;

      --eye-guide-red: rgba(255, 64, 64, 0.95);

      --ui-scale: 1;
      --mono-font: "Roboto Mono", "SF Mono", "SFMono-Regular", Menlo, Consolas, "Liberation Mono", "Courier New", monospace;
      --sans-font: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    }

    /* Color-vision themes */
    body.theme-protanopia {
      --danger: #f6a623;
      --accent: #7cc7ff;
      --eye-left: rgba(246, 166, 35, 0.95);
      --eye-right: rgba(124, 199, 255, 0.95);
      --eye-left-guide: rgba(246, 166, 35, 0.28);
      --eye-right-guide: rgba(124, 199, 255, 0.28);
      --orp-center: #ffd166;
      --eye-guide-red: rgba(255, 150, 40, 0.95);
    }
    body.theme-deuteranopia {
      --danger: #ff9f43;
      --accent: #54a0ff;
      --eye-left: rgba(255, 159, 67, 0.95);
      --eye-right: rgba(84, 160, 255, 0.95);
      --eye-left-guide: rgba(255, 159, 67, 0.28);
      --eye-right-guide: rgba(84, 160, 255, 0.28);
      --orp-center: #ffcf6b;
      --eye-guide-red: rgba(255, 160, 60, 0.95);
    }
    body.theme-tritanopia {
      --danger: #ff7f50;
      --accent: #7bed9f;
      --eye-left: rgba(255, 127, 80, 0.95);
      --eye-right: rgba(123, 237, 159, 0.95);
      --eye-left-guide: rgba(255, 127, 80, 0.28);
      --eye-right-guide: rgba(123, 237, 159, 0.28);
      --orp-center: #ffe08a;
      --eye-guide-red: rgba(255, 170, 100, 0.95);
    }
    body.theme-monochrome {
      --danger: #ffffff;
      --accent: #cfd8e3;
      --hl: #bfbfbf;
      --eye-left: rgba(230, 230, 230, 0.95);
      --eye-right: rgba(170, 170, 170, 0.95);
      --eye-left-guide: rgba(230,230,230,0.24);
      --eye-right-guide: rgba(170,170,170,0.24);
      --orp-center: #ffffff;
      --eye-guide-red: rgba(255,255,255,0.95);
    }
    body.theme-highcontrast {
      --bg: #000000;
      --panel: #080808;
      --panel2: #121212;
      --text: #ffffff;
      --muted: #d2d2d2;
      --accent: #00ffff;
      --danger: #ffea00;
      --line: #3a3a3a;
      --hl: #00ff00;
      --eye-left: rgba(255, 255, 0, 0.98);
      --eye-right: rgba(0, 255, 255, 0.98);
      --eye-left-guide: rgba(255, 255, 0, 0.28);
      --eye-right-guide: rgba(0, 255, 255, 0.28);
      --orp-center: #ffea00;
      --eye-guide-red: rgba(255, 0, 0, 1);
    }

    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: var(--sans-font);
      transform: scale(var(--ui-scale));
      transform-origin: top left;
      width: calc(100% / var(--ui-scale));
      min-height: calc(100% / var(--ui-scale));
    }

    .app {
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      height: 100vh;
      max-height: 100vh;
    }

    .topbar {
      display: flex;
      gap: 10px;
      align-items: center;
      padding: 10px 12px;
      background: var(--panel);
      border-bottom: 1px solid var(--line);
      flex-wrap: wrap;
    }

    .topbar input[type="file"] { color: var(--text); }

    .btn, button, select {
      background: var(--panel2);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 7px 10px;
      cursor: pointer;
    }
    .btn:hover, button:hover, select:hover { border-color: var(--accent); }

    .btn.toggle-on {
      border-color: var(--accent);
      box-shadow: 0 0 0 1px rgba(102,179,255,0.25) inset;
    }

    .toolbar2 {
      display: flex;
      gap: 12px;
      align-items: center;
      padding: 10px 12px;
      background: #131721;
      border-bottom: 1px solid var(--line);
      flex-wrap: wrap;
    }

    .toolbar2 label {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 13px;
    }

    .toolbar2 input[type="number"],
    .toolbar2 input[type="text"],
    .toolbar2 select {
      width: 108px;
      background: var(--panel2);
      color: var(--text);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 5px 6px;
    }

    .toolbar2 input.search { width: 220px; }
    .toolbar2 select.eye-mode { width: 180px; }
    .toolbar2 select.quest-mode { width: 130px; }
    .toolbar2 select.cvd-mode { width: 145px; }

    .main {
      display: grid;
      grid-template-columns: 1.2fr 1fr;
      min-height: 0;
    }

    .pane {
      min-width: 0;
      min-height: 0;
      border-right: 1px solid var(--line);
      display: flex;
      flex-direction: column;
      background: #111620;
    }

    .pane:last-child { border-right: none; }

    .pane-header {
      padding: 8px 12px;
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }

    #docView {
      white-space: pre-wrap;
      padding: 14px;
      overflow: auto;
      line-height: 1.5;
      font-size: 15px;
      font-family: var(--mono-font);
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
    }

    .page-marker { color: #8ec5ff; font-weight: 700; }

    mark.search-hit {
      background: rgba(255, 213, 77, 0.3);
      color: var(--text);
      padding: 0 1px;
      border-radius: 2px;
    }

    .rsvp-wrap {
      display: grid;
      grid-template-rows: auto auto 1fr auto auto;
      min-height: 0;
      height: 100%;
      padding: 10px;
      gap: 8px;
    }

    .rsvp-meta {
      color: var(--muted);
      font-size: 13px;
      min-height: 20px;
    }

    .rsvp-context {
      color: var(--muted);
      text-align: center;
      min-height: 20px;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: var(--mono-font);
    }

    .rsvp-stage {
      position: relative;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #0d1219;
      min-height: 260px;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      padding: 10px;
    }

    .center-guide {
      position: absolute;
      top: 10px;
      bottom: 10px;
      width: 2px;
      background: rgba(255, 92, 92, 0.75);
      left: 50%;
      transform: translateX(-50%);
      z-index: 1;
      pointer-events: none;
    }

    body.theme-highcontrast .center-guide {
      background: rgba(255, 234, 0, 0.95);
      width: 3px;
    }

    .rsvp-line {
      position: relative;
      z-index: 2;
      font-family: var(--mono-font);
      font-size: clamp(28px, 3vw, 46px);
      font-weight: 700;
      letter-spacing: 0;
      line-height: 1.1;
      white-space: pre;
      user-select: none;
      min-height: 1.6em;
      width: min(96%, 1200px);
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
    }

    .rsvp-line-normal {
      position: relative;
      display: block;
      width: 100%;
      height: 1.35em;
      text-align: center;
      overflow: hidden;
      white-space: nowrap;
      font-family: var(--mono-font);
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
    }

    /* Pixel-accurate ORP container (fixed - inline children, no child absolute overlap) */
    .orp-pixel {
      position: absolute;
      top: 50%;
      transform: translateY(-50%);
      display: inline-flex;
      align-items: baseline;
      white-space: pre;
      line-height: 1.1;
      min-height: 1.2em;
      font-family: var(--mono-font);
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
      pointer-events: none;
      will-change: transform, left;
    }

    .orp-left-px,
    .orp-center-px,
    .orp-right-px {
      position: static;
      display: inline;
      white-space: pre;
      font-family: var(--mono-font);
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
      line-height: 1.1;
    }

    .orp-left-px { color: var(--text); }
    .orp-center-px {
      color: var(--orp-center);
      text-shadow: 0 0 8px color-mix(in srgb, var(--orp-center) 35%, transparent);
    }
    .orp-right-px { color: var(--text); }

    .rsvp-line-plain {
      display: inline-block;
      position: absolute;
      top: 50%;
      transform: translate(-50%, -50%);
      left: 50%;
      white-space: pre;
      font-family: var(--mono-font);
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
    }

    /* Per-eye split mode */
    .rsvp-line-eyes {
      position: relative;
      width: min(96%, 1200px);
      min-height: 2.1em;
      display: none;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      align-items: stretch;
    }

    .eye-panel {
      position: relative;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: rgba(255,255,255,0.02);
      padding: 8px 10px;
      min-height: 120px;
      display: grid;
      grid-template-rows: auto 1fr;
      align-items: center;
      justify-items: center;
      overflow: hidden;
    }

    .eye-panel-guide {
      position: absolute;
      top: 28px;
      bottom: 8px;
      width: 2px;
      left: 50%;
      transform: translateX(-50%);
      background: var(--eye-guide-red);
      pointer-events: none;
      z-index: 0;
      opacity: 0.9;
    }

    .eye-panel-guides-off .eye-panel-guide {
      display: none !important;
    }

    .eye-panel-label {
      position: relative;
      z-index: 1;
      font-size: 11px;
      letter-spacing: 0.12em;
      color: var(--muted);
      opacity: 0.85;
      margin-bottom: 4px;
      user-select: none;
      font-family: var(--mono-font);
    }

    .eye-content {
      position: relative;
      z-index: 1;
      display: block;
      min-height: 1.35em;
      height: 1.35em;
      width: 100%;
      transition: opacity 35ms linear;
      opacity: 1;
      text-align: center;
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      font-family: var(--mono-font);
      font-variant-ligatures: none;
      font-feature-settings: "liga" 0, "calt" 0;
      white-space: nowrap;
    }

    .eye-left {
      color: var(--eye-left);
      text-shadow: 0 0 8px color-mix(in srgb, var(--eye-left) 18%, transparent);
    }

    .eye-right {
      color: var(--eye-right);
      text-shadow: 0 0 8px color-mix(in srgb, var(--eye-right) 18%, transparent);
    }

    .eye-content .orp-center-px {
      color: #ffffff;
      text-shadow: 0 0 8px rgba(255,255,255,0.45);
    }

    .eye-content .orp-left-px,
    .eye-content .orp-right-px {
      color: currentColor;
      opacity: 0.97;
      text-shadow: none;
    }

    /* Quest display profiles */
    body.quest-profile .topbar,
    body.quest-profile .toolbar2 {
      padding-left: 16px;
      padding-right: 16px;
    }

    body.quest-profile .toolbar2 label {
      font-size: 14px;
    }

    body.quest-profile .rsvp-stage {
      min-height: 320px;
      border-radius: 16px;
    }

    body.quest-profile .rsvp-line {
      font-size: clamp(34px, 4vw, 58px);
      width: min(98%, 1400px);
    }

    body.quest-profile.quest-left #questOverlay,
    body.quest-profile.quest-right #questOverlay,
    body.quest-profile.quest-both #questOverlay {
      display: block;
    }

    #questOverlay {
      display: none;
      position: absolute;
      inset: 0;
      pointer-events: none;
      z-index: 3;
    }

    #questOverlay.quest-left::before,
    #questOverlay.quest-right::before,
    #questOverlay.quest-both::before,
    #questOverlay.quest-both::after {
      content: "";
      position: absolute;
      top: 0;
      bottom: 0;
      width: 50%;
      background: transparent;
      border: 1px dashed rgba(255,255,255,0.06);
    }

    #questOverlay.quest-left::before {
      left: 0;
      box-shadow: inset 0 0 0 2px rgba(255, 255, 0, 0.08);
    }

    #questOverlay.quest-right::before {
      right: 0;
      box-shadow: inset 0 0 0 2px rgba(0, 255, 255, 0.08);
    }

    #questOverlay.quest-both::before {
      left: 0;
      box-shadow: inset 0 0 0 2px rgba(255, 255, 0, 0.08);
    }

    #questOverlay.quest-both::after {
      right: 0;
      box-shadow: inset 0 0 0 2px rgba(0, 255, 255, 0.08);
    }

    .rsvp-footer {
      color: var(--muted);
      text-align: center;
      font-size: 13px;
      min-height: 18px;
      font-family: var(--mono-font);
    }

    .seek-row {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      align-items: center;
    }

    input[type="range"] { width: 100%; }

    .statusbar {
      border-top: 1px solid var(--line);
      background: var(--panel);
      padding: 8px 12px;
      color: var(--muted);
      font-size: 13px;
      display: flex;
      justify-content: space-between;
      gap: 10px;
      flex-wrap: wrap;
      font-family: var(--mono-font);
    }

    .error { color: #ff9a9a; }
    .ok { color: #9be09b; }

    .hidden { display: none !important; }

    @media (max-width: 1100px) {
      .main { grid-template-columns: 1fr; }
      .pane { border-right: none; border-bottom: 1px solid var(--line); }
      .pane:last-child { border-bottom: none; }
      .rsvp-stage { min-height: 220px; }
      .rsvp-line-eyes { grid-template-columns: 1fr; }
      body.quest-profile .rsvp-line-eyes { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body class="theme-normal">
<div class="app">
  <div class="topbar">
    <input id="fileInput" type="file" accept=".pdf,.epub" />
    <button id="loadBtn">Load</button>
    <button id="docModeBtn" class="btn toggle-on">Document</button>
    <button id="rsvpModeBtn" class="btn">RSVP</button>
    <button id="playBtn" class="btn">Play</button>
    <button id="prevBtn" class="btn">Prev</button>
    <button id="nextBtn" class="btn">Next</button>
    <button id="rebuildBtn" class="btn">Rebuild RSVP</button>
    <button id="fullscreenBtn" class="btn">Fullscreen</button>
    <button id="questDetectBtn" class="btn">Detect Quest</button>
    <span id="loadState" class="ok"></span>
  </div>

  <div class="toolbar2">
    <label>WPM <input id="wpmInput" type="number" min="50" max="3000" step="10" value="400" /></label>
    <label>Chunk <input id="chunkSizeInput" type="number" min="1" max="8" step="1" value="1" /></label>
    <label><input id="phraseModeInput" type="checkbox" /> Phrase mode</label>
    <label><input id="orpInput" type="checkbox" checked /> ORP</label>
    <label>ORP offset px <input id="orpPivotInput" type="number" min="-200" max="200" step="1" value="0" /></label>
    <label><input id="skipUrlsInput" type="checkbox" checked /> Skip URLs</label>

    <label><input id="stereoModeInput" type="checkbox" /> Per-eye split (experimental)</label>
    <label><input id="stereoStartRightInput" type="checkbox" /> Start from RIGHT eye</label>
    <label><input id="eyeGuideLinesInput" type="checkbox" checked /> Eye red guide lines</label>
    <label>Per-eye mode
      <select id="stereoPatternInput" class="eye-mode">
        <option value="chunk">Per chunk</option>
        <option value="roundrobin">Per word (round robin)</option>
        <option value="sentence">Per sentence</option>
      </select>
    </label>
    <label>L/R delay ms <input id="lrDelayInput" type="number" min="0" max="1000" step="1" value="35" /></label>

    <label>Quest mode
      <select id="questModeInput" class="quest-mode">
        <option value="desktop" selected>Desktop</option>
        <option value="quest-left">Quest Left</option>
        <option value="quest-right">Quest Right</option>
        <option value="quest-both">Quest Both</option>
      </select>
    </label>

    <label>Vision theme
      <select id="cvdModeInput" class="cvd-mode">
        <option value="normal" selected>Normal</option>
        <option value="protanopia">Protanopia</option>
        <option value="deuteranopia">Deuteranopia</option>
        <option value="tritanopia">Tritanopia</option>
        <option value="monochrome">Monochrome</option>
        <option value="highcontrast">High Contrast</option>
      </select>
    </label>

    <label>UI scale <input id="uiScaleInput" type="number" min="0.5" max="2.0" step="0.05" value="1.00" /></label>

    <label>Comma pause <input id="commaPauseInput" type="number" min="1" max="10" step="0.1" value="1.6" /></label>
    <label>Sentence pause <input id="sentencePauseInput" type="number" min="1" max="10" step="0.1" value="2.2" /></label>
    <label>Para pause <input id="paraPauseInput" type="number" min="1" max="20" step="0.1" value="3.0" /></label>

    <label>Search <input id="searchInput" class="search" type="text" placeholder="Find in document..." /></label>
    <button id="searchBtn" class="btn">Find</button>
    <button id="clearSearchBtn" class="btn">Clear</button>
  </div>

  <div class="main">
    <section id="docPane" class="pane">
      <div class="pane-header">
        <span>Document View (Read-only)</span>
        <span id="docStats">No file loaded</span>
      </div>
      <div id="docView">Upload a PDF or EPUB to begin.</div>
    </section>

    <section id="rsvpPane" class="pane">
      <div class="rsvp-wrap">
        <div id="rsvpMeta" class="rsvp-meta">RSVP metadata will appear here.</div>
        <div id="rsvpContext" class="rsvp-context"></div>
        <div class="rsvp-stage" id="rsvpStage">
          <div class="center-guide"></div>
          <div id="questOverlay"></div>

          <div id="rsvpLine" class="rsvp-line">
            <div id="rsvpLineNormal" class="rsvp-line-normal">Open a document.</div>

            <div id="rsvpLineStereo" class="rsvp-line-eyes">
              <div class="eye-panel eye-panel-left">
                <div class="eye-panel-guide"></div>
                <div class="eye-panel-label">LEFT EYE</div>
                <div id="rsvpLeftEye" class="eye-content eye-left"></div>
              </div>
              <div class="eye-panel eye-panel-right">
                <div class="eye-panel-guide"></div>
                <div class="eye-panel-label">RIGHT EYE</div>
                <div id="rsvpRightEye" class="eye-content eye-right"></div>
              </div>
            </div>
          </div>
        </div>
        <div id="rsvpFooter" class="rsvp-footer"></div>
        <div class="seek-row">
          <input id="seekRange" type="range" min="0" max="0" value="0" />
          <span id="seekLabel">0 / 0</span>
        </div>
      </div>
    </section>
  </div>

  <div class="statusbar">
    <div id="statusLeft">Ready.</div>
    <div id="statusRight">Shortcuts: Space play/pause · ←/→ prev/next · / search · S per-eye · M mode · X start-eye toggle · Q quest mode cycle · C color theme cycle</div>
  </div>
</div>

<script>
(() => {
  const state = {
    filename: "",
    text: "",
    tokens: [],
    chunks: [],
    rsvpIndex: 0,
    playing: false,
    activePane: "doc",
    timer: null,
    stereoRevealTimer: null,
    stereoRevealTimer2: null,
    searchQuery: "",
    searchHitCount: 0,
    lastRenderedChunk: "",
    questDetected: false
  };

  const els = {
    body: document.body,
    fileInput: document.getElementById("fileInput"),
    loadBtn: document.getElementById("loadBtn"),
    docModeBtn: document.getElementById("docModeBtn"),
    rsvpModeBtn: document.getElementById("rsvpModeBtn"),
    playBtn: document.getElementById("playBtn"),
    prevBtn: document.getElementById("prevBtn"),
    nextBtn: document.getElementById("nextBtn"),
    rebuildBtn: document.getElementById("rebuildBtn"),
    fullscreenBtn: document.getElementById("fullscreenBtn"),
    questDetectBtn: document.getElementById("questDetectBtn"),
    loadState: document.getElementById("loadState"),

    wpmInput: document.getElementById("wpmInput"),
    chunkSizeInput: document.getElementById("chunkSizeInput"),
    phraseModeInput: document.getElementById("phraseModeInput"),
    orpInput: document.getElementById("orpInput"),
    orpPivotInput: document.getElementById("orpPivotInput"),
    skipUrlsInput: document.getElementById("skipUrlsInput"),

    stereoModeInput: document.getElementById("stereoModeInput"),
    stereoStartRightInput: document.getElementById("stereoStartRightInput"),
    eyeGuideLinesInput: document.getElementById("eyeGuideLinesInput"),
    stereoPatternInput: document.getElementById("stereoPatternInput"),
    lrDelayInput: document.getElementById("lrDelayInput"),

    questModeInput: document.getElementById("questModeInput"),
    cvdModeInput: document.getElementById("cvdModeInput"),
    uiScaleInput: document.getElementById("uiScaleInput"),

    commaPauseInput: document.getElementById("commaPauseInput"),
    sentencePauseInput: document.getElementById("sentencePauseInput"),
    paraPauseInput: document.getElementById("paraPauseInput"),

    searchInput: document.getElementById("searchInput"),
    searchBtn: document.getElementById("searchBtn"),
    clearSearchBtn: document.getElementById("clearSearchBtn"),

    docPane: document.getElementById("docPane"),
    rsvpPane: document.getElementById("rsvpPane"),
    docView: document.getElementById("docView"),
    docStats: document.getElementById("docStats"),

    rsvpStage: document.getElementById("rsvpStage"),
    questOverlay: document.getElementById("questOverlay"),
    rsvpMeta: document.getElementById("rsvpMeta"),
    rsvpContext: document.getElementById("rsvpContext"),
    rsvpLineNormal: document.getElementById("rsvpLineNormal"),
    rsvpLineStereo: document.getElementById("rsvpLineStereo"),
    rsvpLeftEye: document.getElementById("rsvpLeftEye"),
    rsvpRightEye: document.getElementById("rsvpRightEye"),
    rsvpFooter: document.getElementById("rsvpFooter"),
    seekRange: document.getElementById("seekRange"),
    seekLabel: document.getElementById("seekLabel"),

    statusLeft: document.getElementById("statusLeft"),
    statusRight: document.getElementById("statusRight"),
  };

  function setStatus(msg, isError = false) {
    els.statusLeft.textContent = msg;
    els.statusLeft.className = isError ? "error" : "";
  }

  function safeInt(v, d) { const n = parseInt(v, 10); return Number.isFinite(n) ? n : d; }
  function safeFloat(v, d) { const n = parseFloat(v); return Number.isFinite(n) ? n : d; }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  function escapeHtml(s) {
    return (s ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;");
  }

  function escapeSpaces(s) {
    return escapeHtml(s).replace(/ /g, "&nbsp;");
  }

  function detectQuestBrowser() {
    const ua = navigator.userAgent || "";
    const quest = /Quest/i.test(ua) || /OculusBrowser/i.test(ua);
    state.questDetected = quest;
    return quest;
  }

  function applyUiScale() {
    const s = clamp(safeFloat(els.uiScaleInput.value, 1), 0.5, 2.0);
    document.documentElement.style.setProperty("--ui-scale", String(s));
    requestAnimationFrame(() => applyAllVisibleOrpAlignments());
  }

  function applyVisionTheme() {
    const mode = els.cvdModeInput.value || "normal";
    els.body.classList.remove(
      "theme-normal",
      "theme-protanopia",
      "theme-deuteranopia",
      "theme-tritanopia",
      "theme-monochrome",
      "theme-highcontrast"
    );
    els.body.classList.add(`theme-${mode}`);
    requestAnimationFrame(() => applyAllVisibleOrpAlignments());
  }

  function applyEyeGuideLinesSetting() {
    // toggle red guide lines in each eye panel
    if (els.eyeGuideLinesInput.checked) {
      els.rsvpLineStereo.classList.remove("eye-panel-guides-off");
    } else {
      els.rsvpLineStereo.classList.add("eye-panel-guides-off");
    }
  }

  function applyQuestMode() {
    const mode = els.questModeInput.value || "desktop";

    els.body.classList.remove("quest-profile", "quest-left", "quest-right", "quest-both");
    els.questOverlay.className = "";

    // default desktop layout
    els.docPane.classList.remove("hidden");
    els.rsvpPane.classList.remove("hidden");

    if (mode === "desktop") {
      requestAnimationFrame(() => applyAllVisibleOrpAlignments());
      return;
    }

    // Quest profile
    els.body.classList.add("quest-profile");

    // For headset use, prioritize RSVP pane space. Keep doc pane hidden in quest display modes.
    els.docPane.classList.add("hidden");
    els.rsvpPane.classList.remove("hidden");
    setMode("rsvp");

    if (mode === "quest-left") {
      els.body.classList.add("quest-left");
      els.questOverlay.classList.add("quest-left");
    } else if (mode === "quest-right") {
      els.body.classList.add("quest-right");
      els.questOverlay.classList.add("quest-right");
    } else if (mode === "quest-both") {
      els.body.classList.add("quest-both");
      els.questOverlay.classList.add("quest-both");
    }

    requestAnimationFrame(() => applyAllVisibleOrpAlignments());
  }

  function setMode(mode) {
    state.activePane = mode;
    els.docModeBtn.classList.toggle("toggle-on", mode === "doc");
    els.rsvpModeBtn.classList.toggle("toggle-on", mode === "rsvp");
    if (mode === "rsvp") renderRSVP();
  }

  function computeOrpIndex(word) {
    if (!word) return 0;
    const core = word.replace(/^[^\w]+|[^\w]+$/gu, "");
    const coreLen = core ? core.length : word.length;
    let idx = 0;
    if (coreLen <= 1) idx = 0;
    else if (coreLen <= 5) idx = 1;
    else if (coreLen <= 9) idx = 2;
    else if (coreLen <= 13) idx = 3;
    else idx = 4;

    let leading = 0;
    for (const ch of word) {
      if (/[0-9A-Za-z]/.test(ch)) break;
      leading += 1;
    }
    return Math.max(0, Math.min(leading + idx, Math.max(0, word.length - 1)));
  }

  function isUrlLike(s) {
    return /(https?:\/\/\S+|www\.\S+)/i.test(s);
  }

  function buildChunksClient(tokens, chunkSize, phraseMode, skipUrls) {
    const filtered = skipUrls ? tokens.filter(t => t === "<PARA>" || !isUrlLike(t)) : [...tokens];
    if (!filtered.length) return [];

    if (phraseMode) {
      const chunks = [];
      let current = [];
      const maxTokens = Math.max(3, chunkSize);
      for (const tok of filtered) {
        if (tok === "<PARA>") {
          if (current.length) { chunks.push(current.join(" ")); current = []; }
          chunks.push("<PARA>");
          continue;
        }
        current.push(tok);
        if (/[.!?;:]$/.test(tok) || current.length >= maxTokens) {
          chunks.push(current.join(" "));
          current = [];
        }
      }
      if (current.length) chunks.push(current.join(" "));
      return chunks;
    }

    const chunks = [];
    let current = [];
    for (const tok of filtered) {
      if (tok === "<PARA>") {
        if (current.length) { chunks.push(current.join(" ")); current = []; }
        chunks.push("<PARA>");
        continue;
      }
      current.push(tok);
      if (current.length >= Math.max(1, chunkSize)) {
        chunks.push(current.join(" "));
        current = [];
      }
    }
    if (current.length) chunks.push(current.join(" "));
    return chunks;
  }

  function estimatePauseMultiplier(chunk, commaMult, sentenceMult, paraMult) {
    if (chunk === "<PARA>") return Math.max(1, paraMult);
    let mult = 1.0;
    if (/[.!?](?:['”’)\]]+)?$/.test(chunk)) mult = Math.max(mult, sentenceMult);
    else if (/[,;:](?:['”’)\]]+)?$/.test(chunk)) mult = Math.max(mult, commaMult);
    if (chunk.includes("...")) mult = Math.max(mult, commaMult + 0.25);
    return mult;
  }

  function stereoChunkStartsRight() {
    return !!els.stereoStartRightInput.checked;
  }

  function stereoChunkEye(index) {
    const invert = stereoChunkStartsRight() ? 1 : 0;
    return ((index + invert) % 2) === 1 ? "right" : "left";
  }

  function pickFocusWord(displayChunk) {
    if (!displayChunk || displayChunk === "¶") return displayChunk;
    const words = displayChunk.split(/\s+/).filter(Boolean);
    const wordish = words.filter(w => /\w/u.test(w));
    if (!wordish.length) return words[Math.floor(words.length / 2)] || displayChunk;
    return wordish[Math.floor(wordish.length / 2)];
  }

  function buildOrpPlaceholder(displayChunk, orpEnabled) {
    if (!orpEnabled || displayChunk === "¶") {
      return `<span class="rsvp-line-plain">${escapeSpaces(displayChunk)}</span>`;
    }

    const focus = pickFocusWord(displayChunk);
    const idx = computeOrpIndex(focus);
    const left = focus.slice(0, idx);
    const center = focus.slice(idx, idx + 1) || " ";
    const right = focus.slice(idx + 1);

    // Inline spans to avoid child absolute overlay bugs
    return [
      `<span class="orp-pixel" data-focus="${escapeHtml(focus)}" data-left="${escapeHtml(left)}" data-center="${escapeHtml(center)}" data-right="${escapeHtml(right)}">`,
      `<span class="orp-left-px">${escapeSpaces(left)}</span>`,
      `<span class="orp-center-px">${escapeSpaces(center)}</span>`,
      `<span class="orp-right-px">${escapeSpaces(right)}</span>`,
      `</span>`
    ].join("");
  }

  let __orpMeasureCanvas = null;
  function getOrpMeasureCtx() {
    if (!__orpMeasureCanvas) __orpMeasureCanvas = document.createElement("canvas");
    return __orpMeasureCanvas.getContext("2d");
  }

  function getFontStringForEl(el) {
    const cs = window.getComputedStyle(el);
    const style = cs.fontStyle || "normal";
    const variant = cs.fontVariant || "normal";
    const weight = cs.fontWeight || "700";
    const size = cs.fontSize || "32px";
    const family = cs.fontFamily || "monospace";
    return `${style} ${variant} ${weight} ${size} ${family}`;
  }

  function textWidthPx(el, text) {
    const ctx = getOrpMeasureCtx();
    ctx.font = getFontStringForEl(el);
    return ctx.measureText(text || "").width;
  }

  function getQuestMode() {
    return els.questModeInput.value || "desktop";
  }

  function getQuestPivotShift(hostEl) {
    const mode = getQuestMode();
    if (mode === "desktop" || !els.body.classList.contains("quest-profile")) return 0;
    const half = hostEl.clientWidth / 4;
    if (mode === "quest-left") return -half;
    if (mode === "quest-right") return +half;
    return 0;
  }

  function applyPixelOrp(hostEl) {
    if (!hostEl) return;

    // If there are multiple ORP placeholders (roundrobin mode), align each one independently.
    const plainEls = hostEl.querySelectorAll(".rsvp-line-plain");
    plainEls.forEach(plain => {
      plain.style.left = "50%";
    });

    const roots = hostEl.querySelectorAll(".orp-pixel");
    if (!roots.length) return;

    const pivotOffsetPx = safeInt(els.orpPivotInput.value, 0);
    const questShift = (hostEl === els.rsvpLineNormal) ? getQuestPivotShift(hostEl) : 0;

    roots.forEach((root) => {
      const leftEl = root.querySelector(".orp-left-px");
      const centerEl = root.querySelector(".orp-center-px");
      const rightEl = root.querySelector(".orp-right-px");
      if (!leftEl || !centerEl || !rightEl) return;

      const leftText = leftEl.textContent || "";
      const centerText = centerEl.textContent || " ";
      const rightText = rightEl.textContent || "";

      const leftW = textWidthPx(hostEl, leftText);
      const centerW = textWidthPx(hostEl, centerText);
      const rightW = textWidthPx(hostEl, rightText);
      const totalW = leftW + centerW + rightW;

      // Width is optional for inline flex, but setting helps debug and stable clipping
      root.style.width = `${Math.ceil(totalW)}px`;

      // For roundrobin mode there can be multiple ORP chunks inline; do NOT absolutely
      // place every one at host center. Detect if root is directly inside host (single ORP mode)
      // or nested inside a larger text flow (roundrobin word mode).
      const directChild = (root.parentElement === hostEl);

      if (directChild) {
        const pivotX = (hostEl.clientWidth / 2) + pivotOffsetPx + questShift;
        const rootLeft = pivotX - (leftW + centerW / 2);
        root.style.position = "absolute";
        root.style.left = `${Math.round(rootLeft)}px`;
        root.style.top = "50%";
        root.style.transform = "translateY(-50%)";
      } else {
        // Nested inline ORP inside a composed line: keep it inline and nudge relative so its own ORP
        // center aligns over the word's normal start position. This avoids all nested overlays.
        root.style.position = "relative";
        root.style.left = "0px";
        root.style.top = "0px";
        root.style.transform = `translateX(${-Math.round(leftW + centerW/2 - totalW/2)}px)`;
      }
    });
  }

  function applyAllVisibleOrpAlignments() {
    if (els.rsvpLineNormal.style.display !== "none") {
      applyPixelOrp(els.rsvpLineNormal);
    }
    if (els.rsvpLineStereo.style.display !== "none") {
      applyPixelOrp(els.rsvpLeftEye);
      applyPixelOrp(els.rsvpRightEye);
    }
  }

  function buildStereoSentenceHtml(displayChunk, orpEnabled) {
    if (displayChunk === "¶") return { leftHtml: "¶", rightHtml: "¶", reveal: "stagger" };

    const htmlChunk = buildOrpPlaceholder(displayChunk, orpEnabled);
    const firstEye = stereoChunkEye(state.rsvpIndex);

    if (firstEye === "left") return { leftHtml: htmlChunk, rightHtml: htmlChunk, reveal: "stagger" };
    return { leftHtml: htmlChunk, rightHtml: htmlChunk, reveal: "stagger-right-first" };
  }

  function buildStereoRoundRobinWordHtml(displayChunk, orpEnabled) {
    if (displayChunk === "¶") return { leftHtml: "¶", rightHtml: "¶", reveal: "simul" };

    const parts = displayChunk.split(/(\s+)/);
    let wordCounter = 0;
    const leftParts = [];
    const rightParts = [];

    const firstEye = stereoChunkEye(state.rsvpIndex);
    const firstToLeft = (firstEye === "left");

    for (const part of parts) {
      if (part === "") continue;

      if (/^\s+$/.test(part)) {
        const sp = escapeSpaces(part);
        leftParts.push(sp);
        rightParts.push(sp);
        continue;
      }

      const isWordish = /\w/u.test(part);
      if (!isWordish) {
        const p = escapeSpaces(part);
        leftParts.push(p);
        rightParts.push(p);
        continue;
      }

      const targetLeft = firstToLeft ? (wordCounter % 2 === 0) : (wordCounter % 2 === 1);
      const orpHtml = buildOrpPlaceholder(part, orpEnabled);
      const ghost = `<span style="opacity:.16">${escapeSpaces(part)}</span>`;

      if (targetLeft) {
        leftParts.push(orpHtml);
        rightParts.push(ghost);
      } else {
        leftParts.push(ghost);
        rightParts.push(orpHtml);
      }
      wordCounter += 1;
    }

    return {
      leftHtml: leftParts.join(""),
      rightHtml: rightParts.join(""),
      reveal: firstEye === "left" ? "stagger" : "stagger-right-first"
    };
  }

  function clearStereoTimers() {
    clearTimeout(state.stereoRevealTimer);
    clearTimeout(state.stereoRevealTimer2);
    state.stereoRevealTimer = null;
    state.stereoRevealTimer2 = null;
  }

  function renderPerEyeChunk(displayChunk, orpEnabled, pattern) {
    clearStereoTimers();

    const lrDelay = clamp(safeInt(els.lrDelayInput.value, 35), 0, 1000);

    els.rsvpLineNormal.style.display = "none";
    els.rsvpLineStereo.style.display = "grid";

    let payload;
    if (pattern === "roundrobin") payload = buildStereoRoundRobinWordHtml(displayChunk, orpEnabled);
    else if (pattern === "sentence") payload = buildStereoSentenceHtml(displayChunk, orpEnabled);
    else {
      const htmlChunk = buildOrpPlaceholder(displayChunk, orpEnabled);
      const firstEye = stereoChunkEye(state.rsvpIndex);
      payload = {
        leftHtml: htmlChunk,
        rightHtml: htmlChunk,
        reveal: firstEye === "left" ? "stagger" : "stagger-right-first"
      };
    }

    els.rsvpLeftEye.innerHTML = payload.leftHtml;
    els.rsvpRightEye.innerHTML = payload.rightHtml;
    applyEyeGuideLinesSetting();

    if (payload.reveal === "simul" || lrDelay === 0) {
      els.rsvpLeftEye.style.opacity = "1";
      els.rsvpRightEye.style.opacity = "1";
      requestAnimationFrame(() => applyAllVisibleOrpAlignments());
      return;
    }

    if (payload.reveal === "stagger-right-first") {
      els.rsvpLeftEye.style.opacity = "0";
      els.rsvpRightEye.style.opacity = "1";
      state.stereoRevealTimer = setTimeout(() => {
        els.rsvpLeftEye.style.opacity = "1";
        requestAnimationFrame(() => applyAllVisibleOrpAlignments());
      }, lrDelay);
      return;
    }

    els.rsvpLeftEye.style.opacity = "1";
    els.rsvpRightEye.style.opacity = "0";
    state.stereoRevealTimer = setTimeout(() => {
      els.rsvpRightEye.style.opacity = "1";
      requestAnimationFrame(() => applyAllVisibleOrpAlignments());
    }, lrDelay);
  }

  function renderDocument(search = false) {
    if (!state.text) {
      els.docView.textContent = "Upload a PDF or EPUB to begin.";
      els.docStats.textContent = "No file loaded";
      return;
    }

    let htmlText = escapeHtml(state.text);
    htmlText = htmlText.replace(/\[Page (\d+)\]/g, '<span class="page-marker">[Page $1]</span>');

    if (search && state.searchQuery) {
      const q = state.searchQuery.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const re = new RegExp(q, "gi");
      let count = 0;
      htmlText = htmlText.replace(re, (m) => {
        count += 1;
        return `<mark class="search-hit">${m}</mark>`;
      });
      state.searchHitCount = count;
    } else {
      state.searchHitCount = 0;
    }

    els.docView.innerHTML = htmlText;

    const words = (state.text.match(/\b\w+\b/g) || []).length;
    els.docStats.textContent = `${state.filename} | ${words.toLocaleString()} words | ${state.text.length.toLocaleString()} chars`;

    if (search && state.searchHitCount > 0) {
      const first = els.docView.querySelector("mark.search-hit");
      if (first) first.scrollIntoView({ behavior: "smooth", block: "center" });
      setStatus(`Found ${state.searchHitCount} matches for "${state.searchQuery}"`);
    } else if (search && state.searchQuery) {
      setStatus(`No matches for "${state.searchQuery}"`, true);
    }
  }

  function rebuildRSVP() {
    const chunkSize = clamp(safeInt(els.chunkSizeInput.value, 1), 1, 8);
    const phraseMode = !!els.phraseModeInput.checked;
    const skipUrls = !!els.skipUrlsInput.checked;

    state.chunks = buildChunksClient(state.tokens || [], chunkSize, phraseMode, skipUrls);
    state.rsvpIndex = clamp(state.rsvpIndex, 0, Math.max(0, state.chunks.length - 1));

    els.seekRange.min = "0";
    els.seekRange.max = String(Math.max(0, state.chunks.length - 1));
    els.seekRange.value = String(state.rsvpIndex);

    renderRSVP();
    setStatus(`RSVP rebuilt: ${state.chunks.length.toLocaleString()} chunks`);
  }

  function renderRSVP() {
    if (!state.chunks.length) {
      els.rsvpMeta.textContent = "No RSVP content loaded.";
      els.rsvpContext.textContent = "";
      els.rsvpLineNormal.innerHTML = `<span class="rsvp-line-plain">Open a document.</span>`;
      els.rsvpLineNormal.style.display = "block";
      els.rsvpLineStereo.style.display = "none";
      els.rsvpFooter.textContent = "";
      els.seekLabel.textContent = "0 / 0";
      return;
    }

    state.rsvpIndex = clamp(state.rsvpIndex, 0, state.chunks.length - 1);
    const chunk = state.chunks[state.rsvpIndex];
    const displayChunk = chunk === "<PARA>" ? "¶" : chunk;

    state.lastRenderedChunk = displayChunk;

    const wpm = clamp(safeInt(els.wpmInput.value, 400), 50, 3000);
    const chunkSize = clamp(safeInt(els.chunkSizeInput.value, 1), 1, 8);
    const phraseMode = !!els.phraseModeInput.checked;
    const orpEnabled = !!els.orpInput.checked;
    const orpPivot = safeInt(els.orpPivotInput.value, 0);

    const perEyeModeOn = !!els.stereoModeInput.checked;
    const perEyePattern = els.stereoPatternInput.value || "chunk";
    const lrDelay = clamp(safeInt(els.lrDelayInput.value, 35), 0, 1000);
    const startEye = stereoChunkStartsRight() ? "RIGHT" : "LEFT";
    const eyeGuides = els.eyeGuideLinesInput.checked ? "ON" : "OFF";
    const questMode = getQuestMode();
    const theme = els.cvdModeInput.value || "normal";

    els.rsvpMeta.textContent =
      `${state.playing ? "PLAY" : "PAUSE"} | chunk ${state.rsvpIndex + 1}/${state.chunks.length} | WPM ${wpm} | chunk ${chunkSize} | phrase ${phraseMode ? "ON" : "OFF"} | ORP ${orpEnabled ? `ON (${orpPivot >= 0 ? "+" : ""}${orpPivot}px)` : "OFF"} | per-eye ${perEyeModeOn ? `ON ${perEyePattern} (${lrDelay}ms, start ${startEye}, guides ${eyeGuides})` : "OFF"} | quest ${questMode} | theme ${theme}`;

    els.rsvpContext.textContent = displayChunk;
    els.seekRange.value = String(state.rsvpIndex);
    els.seekLabel.textContent = `${state.rsvpIndex + 1} / ${state.chunks.length}`;

    const commaPause = safeFloat(els.commaPauseInput.value, 1.6);
    const sentencePause = safeFloat(els.sentencePauseInput.value, 2.2);
    const paraPause = safeFloat(els.paraPauseInput.value, 3.0);
    els.rsvpFooter.textContent = `pauses: comma=${commaPause.toFixed(2)} sentence=${sentencePause.toFixed(2)} para=${paraPause.toFixed(2)}`;

    if (perEyeModeOn) {
      renderPerEyeChunk(displayChunk, orpEnabled, perEyePattern);
      requestAnimationFrame(() => applyAllVisibleOrpAlignments());
      return;
    }

    clearStereoTimers();
    els.rsvpLineStereo.style.display = "none";
    els.rsvpLineNormal.style.display = "block";
    els.rsvpLineNormal.innerHTML = buildOrpPlaceholder(displayChunk, orpEnabled);

    requestAnimationFrame(() => applyAllVisibleOrpAlignments());
  }

  function currentDelayMs() {
    if (!state.chunks.length) return 100;
    const chunk = state.chunks[state.rsvpIndex];
    const wpm = clamp(safeInt(els.wpmInput.value, 400), 50, 3000);
    const commaPause = clamp(safeFloat(els.commaPauseInput.value, 1.6), 1, 10);
    const sentencePause = clamp(safeFloat(els.sentencePauseInput.value, 2.2), 1, 10);
    const paraPause = clamp(safeFloat(els.paraPauseInput.value, 3.0), 1, 20);

    const base = 60000 / wpm;
    const wordsInChunk = Math.max(1, (chunk.match(/\b\w+\b/g) || []).length);
    let delay = base * wordsInChunk;
    delay *= estimatePauseMultiplier(chunk, commaPause, sentencePause, paraPause);
    return Math.max(20, Math.floor(delay));
  }

  function scheduleNextTick() {
    if (!state.playing) return;
    clearTimeout(state.timer);
    state.timer = setTimeout(() => {
      if (!state.playing) return;
      if (state.rsvpIndex < state.chunks.length - 1) {
        state.rsvpIndex += 1;
        renderRSVP();
        scheduleNextTick();
      } else {
        state.playing = false;
        els.playBtn.textContent = "Play";
        renderRSVP();
        setStatus("Reached end of RSVP stream");
      }
    }, currentDelayMs());
  }

  function setPlaying(on) {
    state.playing = !!on;
    els.playBtn.textContent = state.playing ? "Pause" : "Play";
    if (state.playing) scheduleNextTick();
    else {
      clearTimeout(state.timer);
      clearStereoTimers();
    }
    renderRSVP();
  }

  async function loadFile() {
    const file = els.fileInput.files && els.fileInput.files[0];
    if (!file) return setStatus("Choose a PDF or EPUB first.", true);
    if (!/\.(pdf|epub)$/i.test(file.name)) return setStatus("Unsupported file type. Use PDF or EPUB.", true);

    els.loadState.textContent = "Loading...";
    setStatus("Uploading and extracting text...");

    const form = new FormData();
    form.append("file", file);

    try {
      const res = await fetch("/api/extract", { method: "POST", body: form });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || "Extraction failed");

      state.filename = data.filename;
      state.text = data.text || "";
      state.tokens = data.tokens || [];
      state.chunks = data.chunks || [];
      state.rsvpIndex = 0;
      state.searchQuery = "";
      state.searchHitCount = 0;

      clearTimeout(state.timer);
      clearStereoTimers();
      state.playing = false;
      els.playBtn.textContent = "Play";

      els.seekRange.max = String(Math.max(0, state.chunks.length - 1));
      els.seekRange.value = "0";

      renderDocument(false);
      rebuildRSVP();
      if (getQuestMode() === "desktop") setMode("doc");
      else setMode("rsvp");

      els.loadState.textContent = "Loaded";
      setStatus(`Loaded ${data.filename} (${(data.word_count || 0).toLocaleString()} words)`);
    } catch (err) {
      console.error(err);
      els.loadState.textContent = "";
      setStatus(`Load failed: ${err.message || err}`, true);
    }
  }

  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) {
        await document.documentElement.requestFullscreen();
        setStatus("Entered fullscreen");
      } else {
        await document.exitFullscreen();
        setStatus("Exited fullscreen");
      }
      requestAnimationFrame(() => applyAllVisibleOrpAlignments());
    } catch (e) {
      setStatus(`Fullscreen failed: ${e.message || e}`, true);
    }
  }

  function cycleSelect(selectEl) {
    const idx = selectEl.selectedIndex;
    selectEl.selectedIndex = (idx + 1) % selectEl.options.length;
    selectEl.dispatchEvent(new Event("change"));
  }

  function autofitQuestDefaultsIfDetected() {
    if (!detectQuestBrowser()) return;
    els.questModeInput.value = "quest-both";
    els.uiScaleInput.value = "1.15";
    els.cvdModeInput.value = "highcontrast";
    applyUiScale();
    applyVisionTheme();
    applyQuestMode();
    setStatus("Quest browser detected — applied Quest-friendly defaults");
  }

  // ---------------- Event Wiring ----------------
  els.loadBtn.addEventListener("click", loadFile);
  els.fileInput.addEventListener("change", () => { els.loadState.textContent = ""; });

  els.docModeBtn.addEventListener("click", () => setMode("doc"));
  els.rsvpModeBtn.addEventListener("click", () => setMode("rsvp"));

  els.playBtn.addEventListener("click", () => {
    if (!state.chunks.length) return setStatus("No RSVP chunks loaded.", true);
    setPlaying(!state.playing);
  });

  els.prevBtn.addEventListener("click", () => {
    setPlaying(false);
    if (!state.chunks.length) return;
    state.rsvpIndex = clamp(state.rsvpIndex - 1, 0, state.chunks.length - 1);
    renderRSVP();
  });

  els.nextBtn.addEventListener("click", () => {
    setPlaying(false);
    if (!state.chunks.length) return;
    state.rsvpIndex = clamp(state.rsvpIndex + 1, 0, state.chunks.length - 1);
    renderRSVP();
  });

  els.rebuildBtn.addEventListener("click", () => {
    setPlaying(false);
    rebuildRSVP();
  });

  els.fullscreenBtn.addEventListener("click", toggleFullscreen);

  els.questDetectBtn.addEventListener("click", () => {
    const quest = detectQuestBrowser();
    setStatus(quest ? "Quest browser detected" : "Quest browser not detected");
    if (quest) autofitQuestDefaultsIfDetected();
  });

  for (const el of [
    els.wpmInput, els.chunkSizeInput, els.phraseModeInput, els.orpInput, els.orpPivotInput,
    els.skipUrlsInput, els.commaPauseInput, els.sentencePauseInput, els.paraPauseInput,
    els.stereoModeInput, els.stereoStartRightInput, els.eyeGuideLinesInput, els.stereoPatternInput, els.lrDelayInput,
    els.questModeInput, els.cvdModeInput, els.uiScaleInput
  ]) {
    el.addEventListener("change", () => {
      if (el === els.cvdModeInput) {
        applyVisionTheme();
        renderRSVP();
        return;
      }
      if (el === els.uiScaleInput) {
        applyUiScale();
        renderRSVP();
        return;
      }
      if (el === els.questModeInput) {
        applyQuestMode();
        renderRSVP();
        return;
      }
      if (el === els.eyeGuideLinesInput) {
        applyEyeGuideLinesSetting();
        renderRSVP();
        return;
      }

      if (
        el === els.wpmInput || el === els.orpInput || el === els.orpPivotInput ||
        el === els.commaPauseInput || el === els.sentencePauseInput || el === els.paraPauseInput ||
        el === els.stereoModeInput || el === els.stereoStartRightInput || el === els.stereoPatternInput || el === els.lrDelayInput
      ) {
        renderRSVP();
      } else {
        rebuildRSVP();
      }
    });
  }

  els.seekRange.addEventListener("input", () => {
    setPlaying(false);
    state.rsvpIndex = clamp(safeInt(els.seekRange.value, 0), 0, Math.max(0, state.chunks.length - 1));
    renderRSVP();
  });

  els.searchBtn.addEventListener("click", () => {
    state.searchQuery = els.searchInput.value.trim();
    renderDocument(true);
  });

  els.clearSearchBtn.addEventListener("click", () => {
    els.searchInput.value = "";
    state.searchQuery = "";
    renderDocument(false);
    setStatus("Search cleared");
  });

  els.searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      state.searchQuery = els.searchInput.value.trim();
      renderDocument(true);
    }
  });

  window.addEventListener("resize", () => {
    requestAnimationFrame(() => applyAllVisibleOrpAlignments());
  });

  document.addEventListener("fullscreenchange", () => {
    requestAnimationFrame(() => applyAllVisibleOrpAlignments());
  });

  window.addEventListener("keydown", (e) => {
    if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT")) {
      if (e.key === "Escape") e.target.blur();
      return;
    }

    if (e.key === "Tab") {
      e.preventDefault();
      setMode(state.activePane === "doc" ? "rsvp" : "doc");
      return;
    }

    if (e.key === "/") {
      e.preventDefault();
      els.searchInput.focus();
      els.searchInput.select();
      return;
    }

    if (state.activePane === "rsvp") {
      if (e.key === " ") {
        e.preventDefault();
        if (state.chunks.length) setPlaying(!state.playing);
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        setPlaying(false);
        if (state.chunks.length) {
          state.rsvpIndex = clamp(state.rsvpIndex - 1, 0, state.chunks.length - 1);
          renderRSVP();
        }
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        setPlaying(false);
        if (state.chunks.length) {
          state.rsvpIndex = clamp(state.rsvpIndex + 1, 0, state.chunks.length - 1);
          renderRSVP();
        }
      } else if (e.key === "[") {
        e.preventDefault();
        els.wpmInput.value = String(clamp(safeInt(els.wpmInput.value, 400) - 25, 50, 3000));
        renderRSVP();
      } else if (e.key === "]") {
        e.preventDefault();
        els.wpmInput.value = String(clamp(safeInt(els.wpmInput.value, 400) + 25, 50, 3000));
        renderRSVP();
      } else if (e.key === "-") {
        e.preventDefault();
        els.chunkSizeInput.value = String(clamp(safeInt(els.chunkSizeInput.value, 1) - 1, 1, 8));
        rebuildRSVP();
      } else if (e.key === "=") {
        e.preventDefault();
        els.chunkSizeInput.value = String(clamp(safeInt(els.chunkSizeInput.value, 1) + 1, 1, 8));
        rebuildRSVP();
      } else if (e.key.toLowerCase() === "p") {
        e.preventDefault();
        els.phraseModeInput.checked = !els.phraseModeInput.checked;
        rebuildRSVP();
      } else if (e.key.toLowerCase() === "o") {
        e.preventDefault();
        els.orpInput.checked = !els.orpInput.checked;
        renderRSVP();
      } else if (e.key.toLowerCase() === "r") {
        e.preventDefault();
        rebuildRSVP();
      } else if (e.key.toLowerCase() === "s") {
        e.preventDefault();
        els.stereoModeInput.checked = !els.stereoModeInput.checked;
        renderRSVP();
      } else if (e.key.toLowerCase() === "m") {
        e.preventDefault();
        const modes = ["chunk", "roundrobin", "sentence"];
        const i = modes.indexOf(els.stereoPatternInput.value);
        els.stereoPatternInput.value = modes[(i + 1) % modes.length];
        renderRSVP();
      } else if (e.key.toLowerCase() === "x") {
        e.preventDefault();
        els.stereoStartRightInput.checked = !els.stereoStartRightInput.checked;
        renderRSVP();
      } else if (e.key.toLowerCase() === "g") {
        e.preventDefault();
        els.eyeGuideLinesInput.checked = !els.eyeGuideLinesInput.checked;
        applyEyeGuideLinesSetting();
        renderRSVP();
      } else if (e.key.toLowerCase() === "q") {
        e.preventDefault();
        cycleSelect(els.questModeInput);
      } else if (e.key.toLowerCase() === "c") {
        e.preventDefault();
        cycleSelect(els.cvdModeInput);
      } else if (e.key.toLowerCase() === "f") {
        e.preventDefault();
        toggleFullscreen();
      }
    }
  });

  // Better font readiness = better ORP alignment
  async function initFontsThenRender() {
    try {
      if (document.fonts && document.fonts.ready) {
        await document.fonts.ready;
      }
    } catch (_) {}
    requestAnimationFrame(() => {
      applyAllVisibleOrpAlignments();
      renderRSVP();
    });
  }

  // Init
  applyVisionTheme();
  applyUiScale();
  applyQuestMode();
  applyEyeGuideLinesSetting();

  if (detectQuestBrowser()) {
    if (els.questModeInput.value === "desktop") {
      els.questModeInput.value = "quest-both";
      els.uiScaleInput.value = "1.10";
      applyUiScale();
      applyQuestMode();
      setStatus("Quest browser detected — switched to Quest Both mode");
    }
  }

  setMode("doc");
  renderDocument(false);
  renderRSVP();
  initFontsThenRender();
})();
</script>
</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    return render_template_string(HTML_PAGE)


@app.route("/api/extract", methods=["POST"])
def api_extract():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file uploaded"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "Missing file"}), 400

    filename = f.filename
    if not allowed_file(filename):
        return jsonify({"ok": False, "error": "Unsupported file type (use .pdf or .epub)"}), 400

    suffix = Path(filename).suffix.lower()

    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            temp_path = tmp.name
            f.save(temp_path)

        try:
            text = extract_text_from_file(temp_path)
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass

        if not text.strip():
            return jsonify({"ok": False, "error": "No extractable text found. (Scanned PDF likely needs OCR.)"}), 400

        return jsonify(build_payload(text, filename))

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def main() -> None:
    print("Starting local speed reader on http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)


if __name__ == "__main__":
    main()
