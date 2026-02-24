#!/usr/bin/env python3
"""
speedread_doc_viewer.py

Standalone local HTTP server for a browser-based speed-reading document viewer.
Supports TXT / PDF / EPUB with ORP/RSVP, per-eye split mode, guide lines,
Quest presets, color-blind themes, emoji assist, and keyboard shortcuts.

Run:
    python3 speedread_doc_viewer.py

Then open:
    http://127.0.0.1:8000

Optional:
    python3 speedread_doc_viewer.py --port 8787 --open-browser
"""

from __future__ import annotations

import argparse
import http.server
import socketserver
import sys
import threading
import webbrowser
from typing import Final


HTML_DOC: Final[str] = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>Speed Read Document Viewer (PDF/EPUB/TXT) — ORP + Per-Eye</title>

<!-- PDF.js -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.6.82/pdf.min.js"></script>
<!-- JSZip (required by many ePub.js builds) -->
<script src="https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js"></script>
<!-- ePub.js -->
<script src="https://cdn.jsdelivr.net/npm/epubjs/dist/epub.min.js"></script>

<style>
  :root {
    --bg: #0b1220;
    --panel: #0f172a;
    --panel2: #111827;
    --text: #e5e7eb;
    --muted: #94a3b8;
    --border: #243041;
    --accent: #60a5fa;
    --danger: #f87171;
    --guide: #ef4444;
    --guide2: #cbd5e1;
    --orp: #f87171;
    --btn: #1f2937;
    --btn-hover: #2b3647;
    --input: #111827;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
    --sans: Inter, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    height: 100%;
    overflow: hidden;
  }
  .app {
    display: grid;
    grid-template-rows: auto auto 1fr auto;
    height: 100vh;
    width: 100vw;
  }
  .toolbar, .toolbar2, .statusbar {
    border-bottom: 1px solid var(--border);
    background: linear-gradient(180deg, #0f172a 0%, #0c1324 100%);
    padding: 8px 10px;
  }
  .toolbar2 { border-top: 0; }
  .statusbar {
    border-top: 1px solid var(--border);
    border-bottom: 0;
    display: flex;
    gap: 10px;
    align-items: center;
    color: var(--muted);
    font-size: 12px;
    flex-wrap: wrap;
  }
  .row {
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
  }
  label {
    font-size: 12px;
    color: var(--muted);
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
  input[type="number"], input[type="text"], select {
    background: var(--input);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 8px;
    min-width: 72px;
    font-size: 13px;
  }
  input[type="checkbox"] { transform: translateY(1px); }
  .btn {
    background: var(--btn);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 7px 10px;
    font-size: 13px;
    cursor: pointer;
    user-select: none;
  }
  .btn:hover { background: var(--btn-hover); }
  .btn.active {
    border-color: var(--accent);
    box-shadow: 0 0 0 1px color-mix(in oklab, var(--accent) 55%, transparent);
  }
  .file-wrap {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    border: 1px dashed var(--border);
    border-radius: 8px;
    padding: 6px 8px;
  }
  .small { font-size: 12px; color: var(--muted); }
  .main {
    display: grid;
    grid-template-columns: 1.1fr 1fr;
    gap: 10px;
    padding: 10px;
    min-height: 0;
  }
  .panel {
    border: 1px solid var(--border);
    border-radius: 12px;
    background: linear-gradient(180deg, #0c1324 0%, #0a1120 100%);
    min-height: 0;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }
  .panel h3 {
    margin: 0;
    font-size: 13px;
    color: var(--muted);
    font-weight: 600;
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
  }
  .doc-view {
    flex: 1;
    overflow: auto;
    padding: 12px;
    font-family: var(--mono);
    font-size: 14px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .doc-view mark {
    background: color-mix(in oklab, var(--accent) 35%, transparent);
    color: white;
    border-radius: 3px;
    padding: 0 2px;
  }

  .rsvp-shell {
    display: grid;
    grid-template-rows: auto 1fr auto;
    min-height: 0;
    height: 100%;
  }
  .rsvp-meta {
    padding: 8px 10px;
    border-bottom: 1px solid var(--border);
    color: var(--muted);
    font-size: 12px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .rsvp-stage {
    position: relative;
    min-height: 0;
    overflow: hidden;
    background:
      radial-gradient(circle at 50% 40%, rgba(37, 99, 235, 0.06), transparent 40%),
      #060d1a;
  }
  .rsvp-line,
  .rsvp-eye-line {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    font-family: var(--mono);
    user-select: none;
    pointer-events: none;
  }

  .rsvp-line-inner {
    position: relative;
    width: 100%;
    height: 100%;
  }

  .rsvp-word-host,
  .rsvp-eye-word-host {
    position: absolute;
    inset: 0;
    font-family: var(--mono);
    font-weight: 700;
    letter-spacing: 0;
    word-spacing: 0;
  }

  .rsvp-line-plain {
    position: absolute;
    left: 50%;
    top: 50%;
    transform: translate(-50%, -50%);
    white-space: pre;
    font-family: var(--mono);
    font-weight: 700;
  }

  .orp-pixel {
    position: absolute;
    top: 50%;
    transform: translateY(-50%);
    white-space: pre;
    font-family: var(--mono);
    font-weight: 700;
    line-height: 1;
    box-sizing: content-box;
    letter-spacing: 0 !important;
    word-spacing: 0 !important;
  }
  .orp-left-px, .orp-center-px, .orp-right-px {
    display: inline;
    white-space: pre;
    font-family: var(--mono);
    font-weight: 700;
    letter-spacing: 0 !important;
    word-spacing: 0 !important;
  }
  .orp-center-px {
    color: var(--orp);
    text-shadow: 0 0 14px color-mix(in oklab, var(--orp) 45%, transparent);
  }

  .guide-line {
    position: absolute;
    top: 8%;
    bottom: 8%;
    width: 1px;
    background: var(--guide);
    opacity: 0.95;
    pointer-events: none;
  }
  .guide-line.alt {
    background: var(--guide2);
    opacity: 0.7;
  }

  .eye-wrap {
    position: absolute;
    inset: 0;
    display: none;
    grid-template-columns: 1fr 1fr;
    gap: 0;
  }
  .eye-panel {
    position: relative;
    border-left: 1px solid rgba(255,255,255,0.03);
    border-right: 1px solid rgba(255,255,255,0.03);
    overflow: hidden;
  }
  .eye-panel:first-child { border-left: 0; }
  .eye-panel:last-child { border-right: 0; }
  .eye-label {
    position: absolute;
    top: 6px;
    left: 8px;
    font-size: 11px;
    color: var(--muted);
    font-family: var(--sans);
    z-index: 3;
  }
  .eye-word-host {
    position: absolute;
    inset: 0;
    font-family: var(--mono);
    font-weight: 700;
  }

  .rsvp-footer {
    padding: 8px 10px;
    border-top: 1px solid var(--border);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 10px;
    align-items: center;
  }
  .range { width: 100%; }
  .hidden { display: none !important; }

  body.quest-left .main { grid-template-columns: 0fr 1fr; }
  body.quest-left #docPanel { display: none; }
  body.quest-right .main { grid-template-columns: 1fr 0fr; }
  body.quest-right #rsvpPanel { display: none; }
  body.quest-both .main { grid-template-columns: 1fr 1fr; }

  body.theme-protanopia { --orp: #fbbf24; --guide: #fbbf24; --accent: #93c5fd; }
  body.theme-deuteranopia { --orp: #f59e0b; --guide: #f59e0b; --accent: #a5b4fc; }
  body.theme-tritanopia { --orp: #f472b6; --guide: #f472b6; --accent: #60a5fa; }
  body.theme-highcontrast {
    --bg: #000; --panel: #000; --panel2: #000; --text: #fff; --border: #666;
    --muted: #ddd; --accent: #00ffff; --orp: #ffff00; --guide: #ff2d2d; --guide2: #ffffff;
  }

  .mono { font-family: var(--mono); }

  @media (max-width: 1100px) {
    .main { grid-template-columns: 1fr; }
    #docPanel { max-height: 36vh; }
  }
</style>
</head>
<body class="theme-normal">
<div class="app">

  <div class="toolbar">
    <div class="row">
      <div class="file-wrap">
        <input id="fileInput" type="file" accept=".txt,.md,.epub,.pdf,text/plain,application/epub+zip,application/pdf" />
        <span id="fileName" class="small">No file chosen</span>
      </div>

      <button id="loadBtn" class="btn">Load</button>
      <button id="viewDocBtn" class="btn active">Document</button>
      <button id="viewRsvpBtn" class="btn">RSVP</button>

      <button id="playBtn" class="btn">Play</button>
      <button id="prevBtn" class="btn">Prev</button>
      <button id="nextBtn" class="btn">Next</button>
      <button id="rebuildBtn" class="btn">Rebuild RSVP</button>
      <button id="fullscreenBtn" class="btn">Fullscreen</button>
      <button id="detectQuestBtn" class="btn">Detect Quest</button>
    </div>
  </div>

  <div class="toolbar2">
    <div class="row" style="margin-bottom:6px;">
      <label>WPM <input id="wpmInput" type="number" min="50" max="2000" step="10" value="400" /></label>
      <label>Chunk <input id="chunkInput" type="number" min="1" max="15" step="1" value="1" /></label>
      <label><input id="phraseModeInput" type="checkbox" /> Phrase mode</label>
      <label><input id="orpInput" type="checkbox" checked /> ORP</label>
      <label>ORP offset px <input id="orpPivotInput" type="number" value="0" step="1" /></label>
      <label>ORP single nudge <input id="orpSingleNudgeInput" type="number" value="0" step="1" /></label>
      <label>ORP left nudge <input id="orpLeftNudgeInput" type="number" value="0" step="1" /></label>
      <label>ORP right nudge <input id="orpRightNudgeInput" type="number" value="0" step="1" /></label>
      <label><input id="orpDebugInput" type="checkbox" checked /> ORP debug</label>
      <label><input id="skipUrlsInput" type="checkbox" checked /> Skip URLs</label>
    </div>

    <div class="row" style="margin-bottom:6px;">
      <label><input id="perEyeInput" type="checkbox" /> Per-eye split (experimental)</label>
      <label><input id="startRightEyeInput" type="checkbox" /> Start from RIGHT eye</label>
      <label><input id="eyeGuidesInput" type="checkbox" checked /> Eye red guide lines</label>
      <label>Per-eye mode
        <select id="perEyeModeInput">
          <option value="word">Per word (round robin)</option>
          <option value="sentence">Per sentence</option>
        </select>
      </label>
      <label>L/R delay ms <input id="lrDelayInput" type="number" min="0" max="500" step="1" value="35" /></label>

      <label><input id="emojiAssistInput" type="checkbox" /> Emoji assist</label>
      <label>Emoji mode
        <select id="emojiModeInput">
          <option value="replace">Replace</option>
          <option value="append">Append</option>
        </select>
      </label>
      <label>Emoji % <input id="emojiPctInput" type="number" min="0" max="100" step="5" value="60" /></label>

      <label>Quest mode
        <select id="questModeInput">
          <option value="desktop">Desktop</option>
          <option value="quest-left">Quest Left</option>
          <option value="quest-right">Quest Right</option>
          <option value="quest-both">Quest Both</option>
        </select>
      </label>
    </div>

    <div class="row">
      <label>Vision theme
        <select id="visionThemeInput">
          <option value="normal">Normal</option>
          <option value="protanopia">Protanopia-friendly</option>
          <option value="deuteranopia">Deuteranopia-friendly</option>
          <option value="tritanopia">Tritanopia-friendly</option>
          <option value="highcontrast">High Contrast</option>
        </select>
      </label>
      <label>UI scale <input id="uiScaleInput" type="number" min="0.6" max="2.0" step="0.05" value="1.00" /></label>

      <label>Comma pause <input id="commaPauseInput" type="number" min="1.0" max="5.0" step="0.1" value="1.6" /></label>
      <label>Sentence pause <input id="sentencePauseInput" type="number" min="1.0" max="6.0" step="0.1" value="2.2" /></label>
      <label>Para pause <input id="paraPauseInput" type="number" min="1.0" max="10.0" step="0.1" value="3.0" /></label>

      <label>Search <input id="searchInput" type="text" placeholder="Find in document..." style="min-width:220px;" /></label>
      <button id="findBtn" class="btn">Find</button>
      <button id="clearFindBtn" class="btn">Clear</button>
    </div>
  </div>

  <div class="main">
    <div id="docPanel" class="panel">
      <h3>Document View (Read-only)</h3>
      <div id="docView" class="doc-view">Load a TXT, EPUB, or PDF file to begin.</div>
    </div>

    <div id="rsvpPanel" class="panel">
      <h3>RSVP Viewer</h3>
      <div class="rsvp-shell">
        <div id="rsvpMeta" class="rsvp-meta">No document loaded.</div>

        <div id="rsvpStage" class="rsvp-stage">
          <div id="singleWrap" class="rsvp-line">
            <div class="rsvp-line-inner">
              <div id="singleHost" class="rsvp-word-host"></div>
            </div>
          </div>

          <div id="eyeWrap" class="eye-wrap">
            <div id="eyeLeftPanel" class="eye-panel">
              <div class="eye-label">LEFT eye</div>
              <div id="eyeLeftHost" class="rsvp-eye-word-host"></div>
            </div>
            <div id="eyeRightPanel" class="eye-panel">
              <div class="eye-label">RIGHT eye</div>
              <div id="eyeRightHost" class="rsvp-eye-word-host"></div>
            </div>
          </div>

          <div id="singleGuide" class="guide-line"></div>
          <div id="singleDebug" class="guide-line alt"></div>

          <div id="leftGuide" class="guide-line hidden"></div>
          <div id="rightGuide" class="guide-line hidden"></div>
          <div id="leftDebug" class="guide-line alt hidden"></div>
          <div id="rightDebug" class="guide-line alt hidden"></div>
        </div>

        <div class="rsvp-footer">
          <input id="progressRange" class="range" type="range" min="0" max="0" step="1" value="0" />
          <div class="mono small"><span id="progressText">0 / 0</span></div>
        </div>
      </div>
    </div>
  </div>

  <div class="statusbar">
    <span id="statusText">Ready.</span>
    <span id="shortcutsText" class="mono">Shortcuts: Space play/pause • ←/→ prev/next • / search • S per-eye • M mode • X start-eye • G guides • E emoji • V emoji mode • Q quest cycle • C theme cycle • ,/. ORP single nudge • D ORP debug</span>
  </div>
</div>

<script>
(() => {
  // -----------------------------
  // State
  // -----------------------------
  const state = {
    rawText: "",
    displayItems: [],
    idx: 0,
    playing: false,
    timer: null,
    loadedName: "",
    lastSearchQuery: "",
    searchHits: [],
    searchHitIndex: 0,
    currentEye: "left",
    sentenceEyeMap: new Map(),
    view: "document",
  };

  // -----------------------------
  // Element cache
  // -----------------------------
  const els = {
    fileInput: qs("#fileInput"),
    fileName: qs("#fileName"),
    loadBtn: qs("#loadBtn"),
    viewDocBtn: qs("#viewDocBtn"),
    viewRsvpBtn: qs("#viewRsvpBtn"),
    playBtn: qs("#playBtn"),
    prevBtn: qs("#prevBtn"),
    nextBtn: qs("#nextBtn"),
    rebuildBtn: qs("#rebuildBtn"),
    fullscreenBtn: qs("#fullscreenBtn"),
    detectQuestBtn: qs("#detectQuestBtn"),

    wpmInput: qs("#wpmInput"),
    chunkInput: qs("#chunkInput"),
    phraseModeInput: qs("#phraseModeInput"),
    orpInput: qs("#orpInput"),
    orpPivotInput: qs("#orpPivotInput"),
    orpSingleNudgeInput: qs("#orpSingleNudgeInput"),
    orpLeftNudgeInput: qs("#orpLeftNudgeInput"),
    orpRightNudgeInput: qs("#orpRightNudgeInput"),
    orpDebugInput: qs("#orpDebugInput"),
    skipUrlsInput: qs("#skipUrlsInput"),

    perEyeInput: qs("#perEyeInput"),
    startRightEyeInput: qs("#startRightEyeInput"),
    eyeGuidesInput: qs("#eyeGuidesInput"),
    perEyeModeInput: qs("#perEyeModeInput"),
    lrDelayInput: qs("#lrDelayInput"),

    emojiAssistInput: qs("#emojiAssistInput"),
    emojiModeInput: qs("#emojiModeInput"),
    emojiPctInput: qs("#emojiPctInput"),

    questModeInput: qs("#questModeInput"),
    visionThemeInput: qs("#visionThemeInput"),
    uiScaleInput: qs("#uiScaleInput"),

    commaPauseInput: qs("#commaPauseInput"),
    sentencePauseInput: qs("#sentencePauseInput"),
    paraPauseInput: qs("#paraPauseInput"),

    searchInput: qs("#searchInput"),
    findBtn: qs("#findBtn"),
    clearFindBtn: qs("#clearFindBtn"),

    docView: qs("#docView"),
    rsvpMeta: qs("#rsvpMeta"),
    rsvpStage: qs("#rsvpStage"),
    singleWrap: qs("#singleWrap"),
    singleHost: qs("#singleHost"),
    eyeWrap: qs("#eyeWrap"),
    eyeLeftHost: qs("#eyeLeftHost"),
    eyeRightHost: qs("#eyeRightHost"),

    singleGuide: qs("#singleGuide"),
    singleDebug: qs("#singleDebug"),
    leftGuide: qs("#leftGuide"),
    rightGuide: qs("#rightGuide"),
    leftDebug: qs("#leftDebug"),
    rightDebug: qs("#rightDebug"),

    progressRange: qs("#progressRange"),
    progressText: qs("#progressText"),

    statusText: qs("#statusText"),
    docPanel: qs("#docPanel"),
    rsvpPanel: qs("#rsvpPanel"),
  };

  // -----------------------------
  // Utilities
  // -----------------------------
  function qs(sel) { return document.querySelector(sel); }
  function safeInt(v, d = 0) { const n = parseInt(v, 10); return Number.isFinite(n) ? n : d; }
  function safeFloat(v, d = 0) { const n = parseFloat(v); return Number.isFinite(n) ? n : d; }
  function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }
  function escapeSpaces(s) { return escapeHtml(s).replace(/ /g, "&nbsp;"); }

  function isLikelyUrl(word) {
    return /^(https?:\/\/|www\.|[a-z0-9.-]+\.[a-z]{2,}(\/|$))/i.test(word);
  }

  function normalizeText(text) {
    return text
      .replace(/\r\n/g, "\n")
      .replace(/\r/g, "\n")
      .replace(/\u00A0/g, " ")
      .replace(/\t/g, "    ");
  }

  function sentenceSplit(text) {
    const paras = text.split(/\n{2,}/);
    const out = [];
    for (let p = 0; p < paras.length; p++) {
      const para = paras[p].trim();
      if (!para) continue;
      const parts = para.match(/[^.!?]+[.!?…]+(?:["')\]]+)?|[^.!?]+$/g) || [para];
      for (const s of parts) {
        const t = s.trim();
        if (t) out.push({ type: "sentence", text: t });
      }
      if (p < paras.length - 1) out.push({ type: "para" });
    }
    return out;
  }

  function tokeniseWordish(text) { return (text.match(/\S+/g) || []); }

  function estimateStats(text) {
    const words = (text.match(/\b[\p{L}\p{N}'’-]+\b/gu) || []).length;
    const chars = text.length;
    return { words, chars };
  }

  // Canvas text measure (monospace char width etc)
  const textMeasureCanvas = document.createElement("canvas");
  const textMeasureCtx = textMeasureCanvas.getContext("2d");

  function textWidthPx(hostEl, s) {
    const cs = getComputedStyle(hostEl);
    const font = `${cs.fontStyle} ${cs.fontVariant} ${cs.fontWeight} ${cs.fontSize} / ${cs.lineHeight} ${cs.fontFamily}`;
    textMeasureCtx.font = font;
    return textMeasureCtx.measureText(s).width;
  }

  // -----------------------------
  // Emoji assist
  // -----------------------------
  const EMOJI_MAP = new Map([
    ["dog","🐶"],["cat","🐱"],["fish","🐟"],["bird","🐦"],["whale","🐋"],["sea","🌊"],["water","💧"],
    ["fire","🔥"],["earth","🌍"],["wind","💨"],["sun","☀️"],["moon","🌙"],["star","⭐"],
    ["book","📖"],["read","👀"],["eye","👁️"],["eyes","👀"],["brain","🧠"],["heart","❤️"],["hand","✋"],
    ["run","🏃"],["walk","🚶"],["car","🚗"],["ship","🚢"],["house","🏠"],["home","🏡"],
    ["king","👑"],["queen","👸"],["man","👨"],["woman","👩"],["child","🧒"],["children","🧒🧒"],
    ["food","🍽️"],["bread","🍞"],["money","💰"],["time","⏰"],["clock","🕒"],
    ["english","🔤"],["language","🗣️"],["question","❓"],["warning","⚠️"],["idea","💡"]
  ]);

  function maybeEmojiAssist(text) {
    if (!els.emojiAssistInput.checked) return text;
    const pct = clamp(safeInt(els.emojiPctInput.value, 60), 0, 100);
    if (Math.random() * 100 > pct) return text;
    const bare = text.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}]+$/gu, "").toLowerCase();
    const emoji = EMOJI_MAP.get(bare);
    if (!emoji) return text;
    return (els.emojiModeInput.value === "replace") ? emoji : `${text} ${emoji}`;
  }

  // -----------------------------
  // ORP logic
  // -----------------------------
  function computeOrpIndex(word) {
    const chars = [...String(word)];
    const len = chars.length;
    if (len <= 2) return 0;
    if (len <= 5) return 1;
    if (len <= 9) return 2;
    if (len <= 13) return 3;
    return Math.min(4, len - 1);
  }

  function scoreWordForFocus(w) {
    const bare = w.replace(/[^\p{L}\p{N}'’-]/gu, "");
    return bare.length * 2 + (/^[A-Z]/.test(bare) ? 1 : 0);
  }

  function pickFocusWord(displayChunk) {
    const parts = String(displayChunk).trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return displayChunk;
    let best = parts[0];
    let bestScore = scoreWordForFocus(best);
    for (const p of parts) {
      const s = scoreWordForFocus(p);
      if (s > bestScore) { best = p; bestScore = s; }
    }
    return best;
  }

  function buildOrpPlaceholder(displayChunk, orpEnabled) {
    if (!orpEnabled || displayChunk === "¶") {
      return `<span class="rsvp-line-plain">${escapeSpaces(displayChunk)}</span>`;
    }
    const focus = pickFocusWord(displayChunk);
    const idx = computeOrpIndex(focus);
    const arr = [...focus];
    const left = arr.slice(0, idx).join("");
    const center = arr.slice(idx, idx + 1).join("") || " ";
    const right = arr.slice(idx + 1).join("");

    return [
      `<span class="orp-pixel"`,
      ` data-focus="${escapeHtml(focus)}"`,
      ` data-left="${escapeHtml(left)}"`,
      ` data-center="${escapeHtml(center)}"`,
      ` data-right="${escapeHtml(right)}"`,
      ` data-pivot-index="${idx}">`,
      `<span class="orp-left-px">${escapeSpaces(left)}</span>`,
      `<span class="orp-center-px">${escapeSpaces(center)}</span>`,
      `<span class="orp-right-px">${escapeSpaces(right)}</span>`,
      `</span>`
    ].join("");
  }

  function getHostDebugMarker(hostEl) {
    if (hostEl === els.singleHost) return els.singleDebug;
    if (hostEl === els.eyeLeftHost) return els.leftDebug;
    if (hostEl === els.eyeRightHost) return els.rightDebug;
    return null;
  }

  function setOrpDebugMarker(markerEl, xPx, enabled) {
    if (!markerEl) return;
    markerEl.style.display = enabled ? "block" : "none";
    if (!enabled) return;
    markerEl.style.left = `${Math.round(xPx)}px`;
  }

  function getHostOrpNudge(hostEl) {
    if (hostEl === els.singleHost) return safeInt(els.orpSingleNudgeInput.value, 0);
    if (hostEl === els.eyeLeftHost) return safeInt(els.orpLeftNudgeInput.value, 0);
    if (hostEl === els.eyeRightHost) return safeInt(els.orpRightNudgeInput.value, 0);
    return 0;
  }

  function applyPixelOrp(hostEl) {
    if (!hostEl) return;

    const plainEls = hostEl.querySelectorAll(".rsvp-line-plain");
    plainEls.forEach(plain => {
      plain.style.left = "50%";
      plain.style.transform = "translate(-50%, -50%)";
    });

    const roots = hostEl.querySelectorAll(".orp-pixel");
    const debugEnabled = !!els.orpDebugInput.checked;
    const debugMarker = getHostDebugMarker(hostEl);

    if (!roots.length) {
      setOrpDebugMarker(debugMarker, 0, false);
      return;
    }

    const globalPivotOffsetPx = safeInt(els.orpPivotInput.value, 0);
    const hostNudgePx = getHostOrpNudge(hostEl);

    const charW = Math.max(1, textWidthPx(hostEl, "M"));
    const hostPivotX = (hostEl.clientWidth / 2) + globalPivotOffsetPx + hostNudgePx;

    const stageRect = els.rsvpStage.getBoundingClientRect();
    const hostRect = hostEl.getBoundingClientRect();
    const pivotInStage = (hostRect.left - stageRect.left) + hostPivotX;
    setOrpDebugMarker(debugMarker, pivotInStage, debugEnabled);

    for (const root of roots) {
      const leftEl = root.querySelector(".orp-left-px");
      const centerEl = root.querySelector(".orp-center-px");
      const rightEl = root.querySelector(".orp-right-px");
      if (!leftEl || !centerEl || !rightEl) continue;

      const leftText = leftEl.textContent || "";
      const centerText = centerEl.textContent || " ";
      const rightText = rightEl.textContent || "";

      const leftChars = [...leftText].length;
      const centerChars = Math.max(1, [...centerText].length);
      const rightChars = [...rightText].length;
      const totalChars = leftChars + centerChars + rightChars;

      const totalW = totalChars * charW;
      // pivot at center of highlighted glyph; red line should pass through center glyph
      const pivotWithinRoot = (leftChars * charW) + (centerChars * charW / 2);

      root.style.width = `${Math.ceil(totalW)}px`;
      root.style.position = "absolute";
      root.style.left = `${Math.round(hostPivotX - pivotWithinRoot)}px`;
      root.style.top = "50%";
      root.style.transform = "translateY(-50%)";
    }
  }

  // -----------------------------
  // Layout / themes / guides
  // -----------------------------
  function updateUiScale() {
    const s = clamp(safeFloat(els.uiScaleInput.value, 1), 0.6, 2.0);
    document.documentElement.style.fontSize = `${16 * s}px`;
    renderCurrent();
  }

  function positionGuides() {
    const stageRect = els.rsvpStage.getBoundingClientRect();

    const singleHostRect = els.singleHost.getBoundingClientRect();
    const singleCenter = (singleHostRect.left - stageRect.left) + (els.singleHost.clientWidth / 2);
    els.singleGuide.style.left = `${Math.round(singleCenter)}px`;

    const leftRect = els.eyeLeftHost.getBoundingClientRect();
    const rightRect = els.eyeRightHost.getBoundingClientRect();
    const leftCenter = (leftRect.left - stageRect.left) + (els.eyeLeftHost.clientWidth / 2);
    const rightCenter = (rightRect.left - stageRect.left) + (els.eyeRightHost.clientWidth / 2);

    els.leftGuide.style.left = `${Math.round(leftCenter)}px`;
    els.rightGuide.style.left = `${Math.round(rightCenter)}px`;
  }

  function updateGuideVisibility() {
    const perEye = els.perEyeInput.checked;
    const showEyeGuides = !!els.eyeGuidesInput.checked;
    const showDebug = !!els.orpDebugInput.checked;

    els.singleGuide.classList.toggle("hidden", perEye || !showEyeGuides);
    els.singleDebug.classList.toggle("hidden", perEye || !showDebug);

    els.leftGuide.classList.toggle("hidden", !perEye || !showEyeGuides);
    els.rightGuide.classList.toggle("hidden", !perEye || !showEyeGuides);
    els.leftDebug.classList.toggle("hidden", !perEye || !showDebug);
    els.rightDebug.classList.toggle("hidden", !perEye || !showDebug);
  }

  function updateQuestModeClass() {
    document.body.classList.remove("quest-left", "quest-right", "quest-both");
    const mode = els.questModeInput.value;
    if (mode === "quest-left") document.body.classList.add("quest-left");
    if (mode === "quest-right") document.body.classList.add("quest-right");
    if (mode === "quest-both") document.body.classList.add("quest-both");
    renderCurrent();
  }

  function updateVisionTheme() {
    const v = els.visionThemeInput.value;
    document.body.classList.remove("theme-normal","theme-protanopia","theme-deuteranopia","theme-tritanopia","theme-highcontrast");
    document.body.classList.add(`theme-${v}`);
    renderCurrent();
  }

  function setView(view) {
    state.view = view;
    const showDoc = (view === "document");
    els.docPanel.style.display = showDoc ? "" : "none";
    els.rsvpPanel.style.display = "";
    els.viewDocBtn.classList.toggle("active", showDoc);
    els.viewRsvpBtn.classList.toggle("active", !showDoc);
  }

  // -----------------------------
  // Document loading
  // -----------------------------
  async function loadSelectedFile() {
    const file = els.fileInput.files?.[0];
    if (!file) { setStatus("Choose a file first."); return; }

    state.loadedName = file.name;
    els.fileName.textContent = file.name;

    try {
      setStatus(`Loading ${file.name}...`);
      const ext = file.name.toLowerCase().split(".").pop();
      let text = "";

      if (ext === "txt" || ext === "md") {
        text = normalizeText(await file.text());
      } else if (ext === "pdf") {
        text = await extractTextFromPdf(file);
      } else if (ext === "epub") {
        text = await extractTextFromEpub(file);
      } else {
        throw new Error(`Unsupported file type: .${ext}`);
      }

      if (!text.trim()) throw new Error("No text could be extracted from this document.");

      state.rawText = text;
      buildDisplayDoc(text);
      rebuildRsvpFromCurrentSettings(true);
      setStatus(`Loaded ${file.name}`);
    } catch (err) {
      console.error(err);
      setStatus(`Load failed: ${err.message || err}`);
    }
  }

  async function extractTextFromPdf(file) {
    const pdfjsLib = window["pdfjsLib"];
    if (!pdfjsLib) throw new Error("PDF.js failed to load.");

    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.6.82/pdf.worker.min.js";

    const bytes = new Uint8Array(await file.arrayBuffer());
    const pdf = await pdfjsLib.getDocument({ data: bytes }).promise;
    const out = [];

    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const content = await page.getTextContent();
      const strs = content.items.map(it => it.str);
      out.push(strs.join(" "));
      out.push("\n\n");
    }
    return normalizeText(out.join(""));
  }

  // Reliable EPUB extraction via hidden rendition + iframe text extraction
  async function extractTextFromEpub(file) {
    if (!window.ePub) throw new Error("ePub.js failed to load.");
    if (!window.JSZip) throw new Error("JSZip lib not loaded (required for EPUB support).");

    const blob = new Blob([await file.arrayBuffer()], { type: "application/epub+zip" });

    let book;
    try {
      book = window.ePub(blob);
    } catch (e) {
      throw new Error(`Failed to open EPUB: ${e?.message || e}`);
    }

    await book.ready;

    const hidden = document.createElement("div");
    hidden.style.position = "fixed";
    hidden.style.left = "-99999px";
    hidden.style.top = "0";
    hidden.style.width = "900px";
    hidden.style.height = "1200px";
    hidden.style.overflow = "hidden";
    hidden.style.visibility = "hidden";
    document.body.appendChild(hidden);

    let rendition;
    const out = [];

    try {
      rendition = book.renderTo(hidden, {
        width: 900,
        height: 1200,
        manager: "default",
        flow: "paginated"
      });

      await rendition.display();

      const spineItems = (book.spine && book.spine.items) ? book.spine.items : [];
      if (!spineItems.length) throw new Error("EPUB spine is empty.");

      for (const item of spineItems) {
        try {
          await rendition.display(item.href || item.idref || item.index);
          await new Promise(r => setTimeout(r, 30));

          const iframes = hidden.querySelectorAll("iframe");
          let sectionText = "";

          for (const iframe of iframes) {
            try {
              const doc = iframe.contentDocument;
              if (!doc || !doc.body) continue;
              const txt = (doc.body.innerText || doc.body.textContent || "").trim();
              if (txt) sectionText += txt + "\n\n";
            } catch (_) {
              // ignore individual iframe errors
            }
          }

          if (sectionText.trim()) out.push(sectionText.trim());
        } catch (e) {
          console.warn("EPUB section render warning:", item, e);
        }
      }

      if (!out.length) {
        // Fallback: direct spine loading path (some builds behave better here)
        const spineFallback = (book.spine && book.spine.items) ? book.spine.items : [];
        for (const item of spineFallback) {
          try {
            const section = book.spine.get(item.index);
            const loaded = await section.load(book.load.bind(book));
            let html = "";
            if (loaded?.documentElement?.innerHTML) html = loaded.documentElement.innerHTML;
            else if (loaded?.body?.innerHTML) html = loaded.body.innerHTML;
            else if (typeof loaded === "string") html = loaded;
            const txt = htmlToText(html);
            if (txt.trim()) out.push(txt.trim());
            await section.unload();
          } catch (e) {
            console.warn("EPUB fallback section parse warning:", e);
          }
        }
      }

      if (!out.length) throw new Error("EPUB opened, but no readable text was extracted.");
      return normalizeText(out.join("\n\n"));
    } finally {
      try { rendition && rendition.destroy && rendition.destroy(); } catch {}
      try { book && book.destroy && book.destroy(); } catch {}
      hidden.remove();
    }
  }

  function htmlToText(html) {
    const doc = new DOMParser().parseFromString(html || "", "text/html");
    doc.querySelectorAll("script,style,noscript").forEach(n => n.remove());
    doc.querySelectorAll("p,div,section,article,li,h1,h2,h3,h4,h5,h6,blockquote,pre").forEach(el => {
      el.insertAdjacentText("afterend", "\n");
    });
    return (doc.body?.textContent || "").replace(/\n{3,}/g, "\n\n").trim();
  }

  function buildDisplayDoc(text) {
    els.docView.textContent = text;
    const st = estimateStats(text);
    els.rsvpMeta.textContent = `${state.loadedName || "document"} | ${st.words.toLocaleString()} words | ${st.chars.toLocaleString()} chars`;
  }

  // -----------------------------
  // RSVP build / render
  // -----------------------------
  function rebuildRsvpFromCurrentSettings(resetIndex = false) {
    if (!state.rawText) return;
    const chunkSize = clamp(safeInt(els.chunkInput.value, 1), 1, 15);
    const phraseMode = !!els.phraseModeInput.checked;
    const skipUrls = !!els.skipUrlsInput.checked;

    const sentenceBlocks = sentenceSplit(state.rawText);
    const displayItems = [];
    let sentenceId = 0;
    let sourceIndex = 0;
    state.sentenceEyeMap.clear();

    for (const block of sentenceBlocks) {
      if (block.type === "para") {
        displayItems.push({ text: "¶", sentenceId, paraBreak: true, sourceIndex: sourceIndex++ });
        continue;
      }

      const words = tokeniseWordish(block.text).filter(w => !(skipUrls && isLikelyUrl(w)));
      if (!words.length) { sentenceId++; continue; }

      if (phraseMode) {
        let current = [];
        for (const w of words) {
          current.push(w);
          const punctBreak = /[,;:—-]$/.test(w) || /[.!?…]$/.test(w);
          if (current.length >= chunkSize || punctBreak) {
            displayItems.push({ text: current.join(" "), sentenceId, paraBreak: false, sourceIndex: sourceIndex++ });
            current = [];
          }
        }
        if (current.length) {
          displayItems.push({ text: current.join(" "), sentenceId, paraBreak: false, sourceIndex: sourceIndex++ });
        }
      } else {
        for (let i = 0; i < words.length; i += chunkSize) {
          displayItems.push({
            text: words.slice(i, i + chunkSize).join(" "),
            sentenceId,
            paraBreak: false,
            sourceIndex: sourceIndex++
          });
        }
      }
      sentenceId++;
    }

    state.displayItems = displayItems;
    if (resetIndex) state.idx = 0;
    state.idx = clamp(state.idx, 0, Math.max(0, displayItems.length - 1));
    state.currentEye = els.startRightEyeInput.checked ? "right" : "left";
    assignSentenceEyes();
    updateProgressUi();
    renderCurrent();
    setStatus(`RSVP rebuilt: ${displayItems.length.toLocaleString()} chunks`);
  }

  function assignSentenceEyes() {
    const startRight = !!els.startRightEyeInput.checked;
    let eye = startRight ? "right" : "left";
    const mode = els.perEyeModeInput.value;
    state.sentenceEyeMap.clear();
    if (mode !== "sentence") return;

    const seen = new Set();
    for (const item of state.displayItems) {
      if (item.text === "¶") continue;
      if (seen.has(item.sentenceId)) continue;
      seen.add(item.sentenceId);
      state.sentenceEyeMap.set(item.sentenceId, eye);
      eye = (eye === "left") ? "right" : "left";
    }
  }

  function currentItem() {
    if (!state.displayItems.length) return null;
    return state.displayItems[state.idx] || null;
  }

  function applyBaseFontSizing(hostEl, text) {
    const stageW = hostEl.clientWidth || 600;
    const stageH = hostEl.clientHeight || 300;
    const len = [...String(text)].length || 1;
    let px = Math.min(stageH * 0.22, stageW / Math.max(6, len * 0.75));
    px = clamp(px, 22, 72);
    hostEl.style.fontSize = `${Math.round(px)}px`;
    hostEl.style.lineHeight = "1";
    hostEl.style.fontFamily = getComputedStyle(document.documentElement).getPropertyValue("--mono") || "monospace";
    hostEl.style.fontWeight = "700";
  }

  function eyeForWordRoundRobin(index) {
    const startRight = !!els.startRightEyeInput.checked;
    return ((index % 2) === 0)
      ? (startRight ? "right" : "left")
      : (startRight ? "left" : "right");
  }

  function renderCurrent() {
    positionGuides();
    updateGuideVisibility();

    const item = currentItem();
    if (!item) {
      els.singleHost.innerHTML = `<span class="rsvp-line-plain">Load a document…</span>`;
      els.eyeLeftHost.innerHTML = "";
      els.eyeRightHost.innerHTML = "";
      applyPixelOrp(els.singleHost);
      return;
    }

    const perEye = !!els.perEyeInput.checked;
    els.singleWrap.style.display = perEye ? "none" : "";
    els.eyeWrap.style.display = perEye ? "grid" : "none";

    const shownText = maybeEmojiAssist(item.text);
    const orpOn = !!els.orpInput.checked;

    if (!perEye) {
      els.singleHost.innerHTML = buildOrpPlaceholder(shownText, orpOn);
      applyBaseFontSizing(els.singleHost, shownText);
      applyPixelOrp(els.singleHost);

      els.eyeLeftHost.innerHTML = "";
      els.eyeRightHost.innerHTML = "";
      setOrpDebugMarker(els.leftDebug, 0, false);
      setOrpDebugMarker(els.rightDebug, 0, false);
    } else {
      const eyeMode = els.perEyeModeInput.value;
      let activeEye = "left";
      if (eyeMode === "word") activeEye = eyeForWordRoundRobin(state.idx);
      else activeEye = state.sentenceEyeMap.get(item.sentenceId) || (els.startRightEyeInput.checked ? "right" : "left");

      const inactiveEye = activeEye === "left" ? "right" : "left";
      const activeHost = activeEye === "left" ? els.eyeLeftHost : els.eyeRightHost;
      const inactiveHost = inactiveEye === "left" ? els.eyeLeftHost : els.eyeRightHost;

      activeHost.innerHTML = buildOrpPlaceholder(shownText, orpOn);
      inactiveHost.innerHTML = "";

      applyBaseFontSizing(activeHost, shownText);
      applyPixelOrp(activeHost);

      const inactiveDebug = inactiveEye === "left" ? els.leftDebug : els.rightDebug;
      setOrpDebugMarker(inactiveDebug, 0, false);
    }

    const total = state.displayItems.length;
    const perEyeTxt = perEye
      ? `per-eye ON (${els.perEyeModeInput.value === "word" ? "word round robin" : "sentence"})`
      : "per-eye OFF";
    const orpTxt = orpOn
      ? `ORP ON (+${safeInt(els.orpPivotInput.value,0)}px, S:${safeInt(els.orpSingleNudgeInput.value,0)} L:${safeInt(els.orpLeftNudgeInput.value,0)} R:${safeInt(els.orpRightNudgeInput.value,0)}, dbg:${els.orpDebugInput.checked ? "ON":"OFF"})`
      : "ORP OFF";

    els.rsvpMeta.textContent =
      `${state.loadedName || "document"} | chunk ${state.idx + 1}/${total} | WPM ${safeInt(els.wpmInput.value,400)} | chunkSize ${safeInt(els.chunkInput.value,1)} | phrase ${els.phraseModeInput.checked ? "ON":"OFF"} | ${orpTxt} | ${perEyeTxt} | quest ${els.questModeInput.value} | theme ${els.visionThemeInput.value}`;

    updateProgressUi();
  }

  // -----------------------------
  // Playback
  // -----------------------------
  function computeDelayMs(item) {
    const wpm = clamp(safeInt(els.wpmInput.value, 400), 50, 2000);
    const wordsInChunk = Math.max(1, tokeniseWordish(item.text === "¶" ? "" : item.text).length);
    let base = (60000 / wpm) * wordsInChunk;

    const txt = item.text;
    if (item.paraBreak || txt === "¶") {
      base *= clamp(safeFloat(els.paraPauseInput.value, 3.0), 1.0, 10.0);
    } else if (/[.!?…]["')\]]*$/.test(txt)) {
      base *= clamp(safeFloat(els.sentencePauseInput.value, 2.2), 1.0, 10.0);
    } else if (/[,;:—-]["')\]]*$/.test(txt)) {
      base *= clamp(safeFloat(els.commaPauseInput.value, 1.6), 1.0, 10.0);
    }
    return Math.round(base);
  }

  function scheduleNextTick() {
    clearTimeout(state.timer);
    if (!state.playing) return;
    const item = currentItem();
    if (!item) { togglePlay(false); return; }

    let delay = computeDelayMs(item);
    if (els.perEyeInput.checked) delay += clamp(safeInt(els.lrDelayInput.value, 35), 0, 500);

    state.timer = setTimeout(() => {
      stepNext();
      if (state.playing) scheduleNextTick();
    }, delay);
  }

  function togglePlay(force = null) {
    state.playing = (force == null) ? !state.playing : !!force;
    els.playBtn.textContent = state.playing ? "Pause" : "Play";
    els.playBtn.classList.toggle("active", state.playing);

    if (state.playing) {
      if (!state.displayItems.length) {
        state.playing = false;
        els.playBtn.textContent = "Play";
        return;
      }
      scheduleNextTick();
    } else {
      clearTimeout(state.timer);
    }
  }

  function stepNext() {
    if (!state.displayItems.length) return;
    state.idx = clamp(state.idx + 1, 0, state.displayItems.length - 1);
    renderCurrent();
    if (state.idx >= state.displayItems.length - 1 && state.playing) togglePlay(false);
  }

  function stepPrev() {
    if (!state.displayItems.length) return;
    state.idx = clamp(state.idx - 1, 0, state.displayItems.length - 1);
    renderCurrent();
  }

  // -----------------------------
  // Search
  // -----------------------------
  function doFind() {
    const q = els.searchInput.value.trim();
    if (!q || !state.rawText) return;
    const lc = state.rawText.toLowerCase();
    const needle = q.toLowerCase();

    if (state.lastSearchQuery !== needle) {
      state.searchHits = [];
      state.searchHitIndex = 0;
      let idx = 0;
      while ((idx = lc.indexOf(needle, idx)) !== -1) {
        state.searchHits.push([idx, idx + needle.length]);
        idx += needle.length;
        if (state.searchHits.length > 20000) break;
      }
      state.lastSearchQuery = needle;
    }

    if (!state.searchHits.length) { setStatus(`No matches for "${q}"`); return; }

    const [start, end] = state.searchHits[state.searchHitIndex % state.searchHits.length];
    state.searchHitIndex++;
    scrollDocToMatch(start, end);
    setStatus(`Find "${q}": ${Math.min(state.searchHitIndex, state.searchHits.length)} / ${state.searchHits.length}`);
  }

  function clearFind() {
    els.searchInput.value = "";
    state.lastSearchQuery = "";
    state.searchHits = [];
    state.searchHitIndex = 0;
    if (state.rawText) els.docView.textContent = state.rawText;
    setStatus("Search cleared.");
  }

  function scrollDocToMatch(start, end) {
    const t = state.rawText;
    const before = escapeHtml(t.slice(0, start));
    const match = escapeHtml(t.slice(start, end));
    const after = escapeHtml(t.slice(end));
    els.docView.innerHTML = `${before}<mark id="findMark">${match}</mark>${after}`;
    const mark = qs("#findMark");
    if (mark) mark.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  // -----------------------------
  // Progress / status
  // -----------------------------
  function updateProgressUi() {
    const total = state.displayItems.length;
    els.progressRange.max = Math.max(0, total - 1);
    els.progressRange.value = clamp(state.idx, 0, Math.max(0, total - 1));
    els.progressText.textContent = `${Math.min(total, state.idx + 1)} / ${total}`;
  }
  function setStatus(msg) { els.statusText.textContent = msg; }

  // -----------------------------
  // Fullscreen / Quest
  // -----------------------------
  async function toggleFullscreen() {
    try {
      if (!document.fullscreenElement) await document.documentElement.requestFullscreen();
      else await document.exitFullscreen();
    } catch (e) {
      setStatus(`Fullscreen error: ${e.message || e}`);
    }
  }

  function detectQuestMode() {
    const ua = navigator.userAgent || "";
    const isQuest = /OculusBrowser|Quest/i.test(ua);
    if (isQuest) {
      els.questModeInput.value = "quest-both";
      updateQuestModeClass();
      setStatus("Quest browser detected. Switched to Quest Both mode.");
    } else {
      setStatus("Quest browser not detected. Staying in current mode.");
    }
  }

  function cycleQuestMode() {
    const opts = ["desktop", "quest-left", "quest-right", "quest-both"];
    const i = opts.indexOf(els.questModeInput.value);
    els.questModeInput.value = opts[(i + 1) % opts.length];
    updateQuestModeClass();
  }

  function cycleTheme() {
    const opts = ["normal", "protanopia", "deuteranopia", "tritanopia", "highcontrast"];
    const i = opts.indexOf(els.visionThemeInput.value);
    els.visionThemeInput.value = opts[(i + 1) % opts.length];
    updateVisionTheme();
  }

  // -----------------------------
  // Events
  // -----------------------------
  function onRenderSettingChange(e) {
    if (e?.target === els.startRightEyeInput || e?.target === els.perEyeModeInput) assignSentenceEyes();
    if (e?.target === els.questModeInput) updateQuestModeClass();
    if (e?.target === els.visionThemeInput) updateVisionTheme();
    if (e?.target === els.uiScaleInput) updateUiScale();
    renderCurrent();
  }

  function onKeyDown(e) {
    if (e.target && /input|textarea|select/i.test(e.target.tagName)) {
      if (e.key === "Escape") e.target.blur();
      return;
    }

    switch (e.key) {
      case " ":
        e.preventDefault(); togglePlay(); break;
      case "ArrowLeft":
        e.preventDefault(); togglePlay(false); stepPrev(); break;
      case "ArrowRight":
        e.preventDefault(); togglePlay(false); stepNext(); break;
      case "/":
        e.preventDefault(); els.searchInput.focus(); els.searchInput.select(); break;
      case "s": case "S":
        e.preventDefault(); els.perEyeInput.checked = !els.perEyeInput.checked; renderCurrent(); break;
      case "m": case "M":
        e.preventDefault();
        els.perEyeModeInput.value = (els.perEyeModeInput.value === "word") ? "sentence" : "word";
        assignSentenceEyes(); renderCurrent(); break;
      case "x": case "X":
        e.preventDefault();
        els.startRightEyeInput.checked = !els.startRightEyeInput.checked;
        assignSentenceEyes(); renderCurrent(); break;
      case "g": case "G":
        e.preventDefault(); els.eyeGuidesInput.checked = !els.eyeGuidesInput.checked; renderCurrent(); break;
      case "e": case "E":
        e.preventDefault(); els.emojiAssistInput.checked = !els.emojiAssistInput.checked; renderCurrent(); break;
      case "v": case "V":
        e.preventDefault();
        els.emojiModeInput.value = (els.emojiModeInput.value === "replace") ? "append" : "replace";
        renderCurrent(); break;
      case "q": case "Q":
        e.preventDefault(); cycleQuestMode(); break;
      case "c": case "C":
        e.preventDefault(); cycleTheme(); break;
      case ",":
        e.preventDefault();
        els.orpSingleNudgeInput.value = String(safeInt(els.orpSingleNudgeInput.value, 0) - 1);
        renderCurrent(); break;
      case ".":
        e.preventDefault();
        els.orpSingleNudgeInput.value = String(safeInt(els.orpSingleNudgeInput.value, 0) + 1);
        renderCurrent(); break;
      case "d": case "D":
        e.preventDefault(); els.orpDebugInput.checked = !els.orpDebugInput.checked; renderCurrent(); break;
      default: break;
    }
  }

  function bindEvents() {
    els.fileInput.addEventListener("change", () => {
      const f = els.fileInput.files?.[0];
      els.fileName.textContent = f ? f.name : "No file chosen";
    });
    els.loadBtn.addEventListener("click", loadSelectedFile);

    els.viewDocBtn.addEventListener("click", () => setView("document"));
    els.viewRsvpBtn.addEventListener("click", () => setView("rsvp"));

    els.playBtn.addEventListener("click", () => togglePlay());
    els.prevBtn.addEventListener("click", () => { togglePlay(false); stepPrev(); });
    els.nextBtn.addEventListener("click", () => { togglePlay(false); stepNext(); });
    els.rebuildBtn.addEventListener("click", () => { togglePlay(false); rebuildRsvpFromCurrentSettings(false); });
    els.fullscreenBtn.addEventListener("click", toggleFullscreen);
    els.detectQuestBtn.addEventListener("click", detectQuestMode);

    [
      els.wpmInput, els.orpInput, els.orpPivotInput, els.orpSingleNudgeInput, els.orpLeftNudgeInput, els.orpRightNudgeInput,
      els.orpDebugInput, els.perEyeInput, els.eyeGuidesInput, els.perEyeModeInput, els.lrDelayInput,
      els.startRightEyeInput, els.emojiAssistInput, els.emojiModeInput, els.emojiPctInput,
      els.questModeInput, els.visionThemeInput, els.uiScaleInput
    ].forEach(el => el.addEventListener("change", onRenderSettingChange));

    [els.chunkInput, els.phraseModeInput, els.skipUrlsInput].forEach(el =>
      el.addEventListener("change", () => { togglePlay(false); rebuildRsvpFromCurrentSettings(false); })
    );

    [els.commaPauseInput, els.sentencePauseInput, els.paraPauseInput].forEach(el =>
      el.addEventListener("change", () => setStatus("Pause settings updated."))
    );

    els.progressRange.addEventListener("input", () => {
      togglePlay(false);
      state.idx = clamp(safeInt(els.progressRange.value, 0), 0, Math.max(0, state.displayItems.length - 1));
      renderCurrent();
    });

    els.findBtn.addEventListener("click", doFind);
    els.clearFindBtn.addEventListener("click", clearFind);
    els.searchInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); doFind(); }
    });

    window.addEventListener("resize", () => { positionGuides(); renderCurrent(); });
    document.addEventListener("keydown", onKeyDown);
  }

  function initDefaults() {
    updateQuestModeClass();
    updateVisionTheme();
    updateUiScale();
    setView("document");
    positionGuides();
    renderCurrent();
    setStatus("Ready. Load a TXT, EPUB, or PDF file.");
  }

  function validateFunctionPresence() {
    const required = [
      computeOrpIndex, buildOrpPlaceholder, applyPixelOrp, rebuildRsvpFromCurrentSettings,
      extractTextFromPdf, extractTextFromEpub, renderCurrent, loadSelectedFile, onKeyDown,
      bindEvents, initDefaults, htmlToText
    ];
    if (required.some(fn => typeof fn !== "function")) {
      throw new Error("Function validation failed: one or more required functions are missing.");
    }
  }

  try {
    validateFunctionPresence();
    bindEvents();
    initDefaults();
  } catch (err) {
    console.error(err);
    alert(`Startup error: ${err.message || err}`);
  }
})();
</script>
</body>
</html>
"""


class SpeedReadRequestHandler(http.server.BaseHTTPRequestHandler):
    """Serve the embedded HTML app."""

    server_version = "SpeedReadDocViewer/1.0"

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            data = HTML_DOC.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
            return

        if self.path == "/healthz":
            data = b"ok\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return

        self.send_error(404, "Not Found")

    def log_message(self, fmt: str, *args) -> None:
        # Cleaner console logging
        sys.stdout.write(f"[http] {self.address_string()} - {fmt % args}\n")
        sys.stdout.flush()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve a browser-based speed reader for TXT/PDF/EPUB with ORP/per-eye mode."
    )
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the app automatically in your default browser",
    )
    return parser


def run_server(host: str, port: int, open_browser: bool) -> None:
    # Threading server so the UI stays responsive even with multiple browser requests
    class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    httpd = ThreadingHTTPServer((host, port), SpeedReadRequestHandler)

    url = f"http://{host}:{port}/"
    print(f"Serving Speed Read Viewer at: {url}")
    print("Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        httpd.server_close()


def main() -> None:
    args = build_arg_parser().parse_args()
    if not (1 <= args.port <= 65535):
        raise SystemExit("Port must be between 1 and 65535.")
    run_server(args.host, args.port, args.open_browser)


if __name__ == "__main__":
    main()
