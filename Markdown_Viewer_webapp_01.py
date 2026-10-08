"""
Markdown Viewer – lokale Webapp
Startet einen HTTP-Server und öffnet den Browser automatisch.
Keine externen Abhängigkeiten nötig.
"""

import base64
import http.server
import io
import json
import os
import queue
import re
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import url2pathname

PORT = 8742
BASE_DIR = Path(__file__).parent

# -- Screenshot -> Markdown (Claude Vision) --
# Der API-Schluessel wird aus der Umgebungsvariable ANTHROPIC_API_KEY gelesen.
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
CLAUDE_MODEL = "claude-opus-5"     # guenstiger: claude-sonnet-5 / claude-haiku-4-5
CLAUDE_MAX_TOKENS = 16000
CLAUDE_TIMEOUT = 300               # Sekunden pro Bild
CLAUDE_RETRIES = 3                 # Versuche bei Ueberlast/Netzfehler
RETRY_CODES = (408, 429, 500, 502, 503, 504, 529)
IMAGE_DIR_NAME = "bilder"          # Unterordner neben der gespeicherten .md-Datei

VISION_PROMPT = (
    "Wandle diesen Screenshot in sauber formatiertes Markdown um.\n"
    "- Uebernimm den sichtbaren Text vollstaendig und wortgetreu.\n"
    "- Erhalte die Struktur: Ueberschriften (#, ##, ###), Aufzaehlungen, nummerierte "
    "Listen, Tabellen, Codebloecke, Zitate, Fett-/Kursivauszeichnung und Links.\n"
    "- Gib Tabellen als Markdown-Tabellen aus.\n"
    "- Lass reine Bedienelemente weg (Browserleiste, Cookie-Banner, Werbung), sofern "
    "sie nicht zum Inhalt gehoeren.\n"
    "- Beschreibe rein grafische Inhalte kurz kursiv, z. B. *[Diagramm: Umsatz 2024]*.\n"
    "- Antworte ausschliesslich mit dem Markdown selbst - ohne Einleitung, ohne "
    "Kommentar und ohne umschliessende Code-Fences."
)

# Fuer kopierten Text von Webseiten/Dokumenten. Beim Kopieren geht die Auszeichnung
# verloren - das Modell soll die Struktur aus dem Layout zurueckgewinnen, den
# Wortlaut aber nicht anfassen.
TEXT_PROMPT = (
    "Formatiere den folgenden kopierten Text als sauberes Markdown.\n"
    "- Uebernimm den Text vollstaendig und wortgetreu. Formuliere nichts um, "
    "kuerze nichts und ergaenze nichts.\n"
    "- Stelle die verlorene Struktur wieder her: Ueberschriften (#, ##, ###), "
    "Aufzaehlungen, nummerierte Listen, Tabellen, Codebloecke und Zitate.\n"
    "- Spalten, die beim Kopieren zu Tabulatoren oder Zeilenumbruechen geworden "
    "sind, wieder als Markdown-Tabelle ausgeben.\n"
    "- Harte Zeilenumbrueche innerhalb eines Absatzes zusammenfuehren, echte "
    "Absatzgrenzen aber erhalten.\n"
    "- Reine Bedienelemente weglassen (Navigation, Cookie-Banner, Werbung, "
    "'Zum Inhalt springen'), sofern sie nicht zum Inhalt gehoeren.\n"
    "- Antworte ausschliesslich mit dem Markdown selbst - ohne Einleitung, ohne "
    "Kommentar und ohne umschliessende Code-Fences."
)

HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <!-- Ohne Referrer laden: Bild-CDNs weisen Anfragen von fremden Seiten sonst
       ab, und eingefügte Inhalte verweisen genau auf solche CDNs. -->
  <meta name="referrer" content="no-referrer">
  <title>Markdown Viewer</title>
  <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
  <script>
    window.MathJax = {
      tex: { inlineMath: [['$', '$'], ['\\(', '\\)']], displayMath: [['$$', '$$'], ['\\[', '\\]']] },
      svg: { fontCache: 'global' },
      startup: { typeset: false }
    };
  </script>
  <script src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js" async></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg: #1e1e1e;
      --surface: #252526;
      --border: #3c3c3c;
      --toolbar: #2d2d2d;
      --text: #d4d4d4;
      --muted: #858585;
      --accent: #569cd6;
      --accent2: #4ec9b0;
      --code-bg: #1a1a1a;
      --code-fg: #ce9178;
      --blockquote-border: #569cd6;
      --link: #6a9955;
      --h1: #569cd6;
      --h2: #4ec9b0;
      --h3: #9cdcfe;
    }

    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      height: 100vh;
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    /* ── Toolbar ── */
    .toolbar {
      background: var(--toolbar);
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      flex-shrink: 0;
    }

    .toolbar-title {
      font-weight: 600;
      font-size: 14px;
      color: var(--accent);
      margin-right: 8px;
    }

    .btn {
      background: #3c3c3c;
      border: 1px solid var(--border);
      color: var(--text);
      padding: 5px 14px;
      border-radius: 4px;
      cursor: pointer;
      font-size: 13px;
      transition: background 0.15s;
    }
    .btn:hover { background: #505050; }
    .btn.primary { background: #0e639c; border-color: #0e639c; color: #fff; }
    .btn.primary:hover { background: #1177bb; }

    .filepath {
      font-size: 12px;
      color: var(--muted);
      margin-left: 8px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }

    .toc-toggle {
      margin-left: auto;
    }

    /* ── Layout ── */
    .main {
      display: flex;
      flex: 1;
      overflow: hidden;
    }

    /* ── TOC Sidebar ── */
    .toc {
      width: 240px;
      background: var(--surface);
      border-right: 1px solid var(--border);
      overflow-y: auto;
      padding: 16px 0;
      flex-shrink: 0;
      transition: width 0.2s;
    }
    .toc.hidden { width: 0; overflow: hidden; padding: 0; }

    .toc-header {
      font-size: 11px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 0 16px 8px;
    }

    .toc-item {
      display: block;
      padding: 4px 16px;
      font-size: 13px;
      color: var(--text);
      text-decoration: none;
      border-left: 2px solid transparent;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .toc-item:hover { background: rgba(255,255,255,0.05); color: var(--accent); }
    .toc-item.active { border-left-color: var(--accent); color: var(--accent); }
    .toc-item.h2 { padding-left: 28px; font-size: 12px; }
    .toc-item.h3 { padding-left: 40px; font-size: 11px; color: var(--muted); }

    /* ── Content ── */
    .content-wrap {
      flex: 1;
      overflow-y: auto;
      padding: 40px 60px;
    }

    /* ── Drop Zone ── */
    .dropzone {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      height: 100%;
      gap: 16px;
      color: var(--muted);
    }
    .dropzone-icon { font-size: 64px; opacity: 0.3; }
    .dropzone h2 { font-size: 20px; font-weight: 400; }
    .dropzone p { font-size: 13px; }
    .dropzone.dragover { background: rgba(86,156,214,0.06); }

    /* ── Rendered Markdown ── */
    .markdown-body { max-width: 860px; line-height: 1.7; }

    .markdown-body h1, .markdown-body h2, .markdown-body h3,
    .markdown-body h4, .markdown-body h5, .markdown-body h6 {
      margin: 1.4em 0 0.5em;
      line-height: 1.3;
      font-weight: 600;
    }
    .markdown-body h1 { font-size: 2em; color: var(--h1); border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
    .markdown-body h2 { font-size: 1.6em; color: var(--h2); border-bottom: 1px solid var(--border); padding-bottom: 0.2em; }
    .markdown-body h3 { font-size: 1.3em; color: var(--h3); }
    .markdown-body h4 { font-size: 1.1em; color: var(--h3); }

    .markdown-body p { margin: 0.75em 0; }

    .markdown-body a { color: var(--link); text-decoration: underline; }
    .markdown-body a:hover { color: var(--accent); }

    .markdown-body code {
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 0.88em;
      background: var(--code-bg);
      color: var(--code-fg);
      padding: 2px 6px;
      border-radius: 3px;
    }

    .markdown-body pre {
      background: var(--code-bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 16px;
      overflow-x: auto;
      margin: 1em 0;
    }
    .markdown-body pre code {
      background: none;
      padding: 0;
      color: var(--code-fg);
      font-size: 0.9em;
    }

    .markdown-body blockquote {
      border-left: 3px solid var(--blockquote-border);
      margin: 1em 0;
      padding: 6px 16px;
      color: var(--muted);
      font-style: italic;
    }

    .markdown-body ul, .markdown-body ol {
      margin: 0.5em 0;
      padding-left: 2em;
    }
    .markdown-body li { margin: 0.25em 0; }

    .markdown-body table {
      border-collapse: collapse;
      width: 100%;
      margin: 1em 0;
    }
    .markdown-body th, .markdown-body td {
      border: 1px solid var(--border);
      padding: 8px 12px;
      text-align: left;
    }
    .markdown-body th { background: var(--surface); color: var(--accent2); }
    .markdown-body tr:nth-child(even) { background: rgba(255,255,255,0.02); }

    .markdown-body hr {
      border: none;
      border-top: 1px solid var(--border);
      margin: 2em 0;
    }

    .markdown-body img { max-width: 100%; border-radius: 4px; }

    .markdown-body strong { color: #dcdcaa; }
    .markdown-body em { color: #c586c0; }

    /* ── Jupyter Notebook ── */
    .nb-cell {
      margin: 1.2em 0;
      border: 1px solid var(--border);
      border-radius: 6px;
      overflow: hidden;
      background: var(--surface);
    }
    .nb-cell-md { border: none; background: transparent; }
    .nb-cell-md > .nb-md { padding: 0; }
    .nb-md { padding: 12px 16px; }
    .nb-input, .nb-output {
      display: flex;
      gap: 10px;
      padding: 8px 12px;
      border-top: 1px solid var(--border);
    }
    .nb-input { border-top: none; background: #1a1a1a; }
    .nb-output { background: #222; }
    .nb-prompt {
      flex-shrink: 0;
      width: 70px;
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 11px;
      color: var(--muted);
      user-select: none;
      padding-top: 4px;
    }
    .nb-prompt.in { color: #569cd6; }
    .nb-prompt.out { color: #c586c0; }
    .nb-code-body { flex: 1; min-width: 0; }
    .nb-code-body pre {
      margin: 0;
      background: transparent;
      border: none;
      padding: 0;
      white-space: pre;
      overflow-x: auto;
    }
    .nb-code-body pre code {
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 0.88em;
      color: var(--text);
    }
    .nb-out-text {
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 0.85em;
      white-space: pre-wrap;
      word-break: break-word;
      margin: 0;
      color: var(--text);
    }
    .nb-out-err {
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 0.85em;
      white-space: pre-wrap;
      color: #f48771;
      margin: 0;
    }
    .nb-out-img { max-width: 100%; border-radius: 3px; }
    .nb-out-html { color: var(--text); }
    .nb-out-html table { border-collapse: collapse; margin: 4px 0; }
    .nb-out-html th, .nb-out-html td {
      border: 1px solid var(--border);
      padding: 4px 10px;
      font-size: 12px;
    }
    .nb-out-html th { background: var(--surface); color: var(--accent2); }

    /* Scrollbar */
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: #424242; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #555; }

    /* Search bar */
    .search-bar {
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .search-bar input {
      background: var(--bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 4px 10px;
      border-radius: 4px;
      font-size: 13px;
      width: 180px;
      outline: none;
    }
    .search-bar input:focus { border-color: var(--accent); }
    .highlight { background: rgba(255,200,0,0.25); border-radius: 2px; }

    .status {
      font-size: 11px;
      color: var(--muted);
      padding: 3px 16px;
      background: var(--toolbar);
      border-top: 1px solid var(--border);
      flex-shrink: 0;
    }

    /* ── Druck / PDF-Export ── */
    /* ── Screenshots → Markdown ── */
    .shots-panel { max-width: 900px; }
    .shots-panel h2 { font-size: 18px; font-weight: 500; color: var(--h2); margin-bottom: 4px; }
    .shots-panel .hint { font-size: 12px; color: var(--muted); margin-bottom: 16px; line-height: 1.6; }
    .shots-panel .section-label {
      font-size: 11px;
      font-weight: 600;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin: 22px 0 6px;
    }

    .shot-drop {
      border: 1px dashed var(--border);
      border-radius: 6px;
      padding: 22px;
      text-align: center;
      color: var(--muted);
      font-size: 13px;
    }
    .shot-drop.dragover { border-color: var(--accent); background: rgba(86,156,214,0.06); }
    .shot-drop .drop-sep {
      display: block;
      margin: 12px 0 10px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      opacity: 0.7;
    }
    /* Eingabefeld fuer kopierten Text – gleiche Optik wie der Markdown-Editor,
       nur flacher, damit die Dropzone nicht die halbe Seite einnimmt. */
    .text-input {
      width: 100%;
      min-height: 90px;
      background: var(--code-bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 10px;
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 12px;
      line-height: 1.6;
      resize: vertical;
      outline: none;
      text-align: left;
    }
    .text-input:focus { border-color: var(--accent); }

    .shot-item {
      display: flex;
      align-items: center;
      gap: 12px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 8px;
      margin-bottom: 8px;
    }
    .shot-thumb {
      width: 120px;
      height: 70px;
      object-fit: cover;
      object-position: top;
      border-radius: 4px;
      flex-shrink: 0;
      background: var(--code-bg);
    }
    /* Textabschnitte haben kein Bild – stattdessen die ersten Zeilen als Vorschau. */
    .shot-thumb.text {
      display: block;
      padding: 6px 8px;
      overflow: hidden;
      border: 1px solid var(--border);
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 10px;
      line-height: 1.4;
      color: var(--muted);
      white-space: pre-wrap;
      word-break: break-word;
    }
    .shot-meta { display: flex; flex-direction: column; gap: 2px; font-size: 12px; flex: 1; min-width: 0; }
    .shot-meta span { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .shot-meta .done { color: var(--accent2); }
    .shot-actions { display: flex; gap: 4px; flex-shrink: 0; }
    .shot-actions .btn { padding: 3px 9px; font-size: 12px; }
    .shot-empty { font-size: 13px; color: var(--muted); font-style: italic; padding: 4px 0; }

    .shot-bar { display: flex; align-items: center; gap: 8px; margin: 14px 0; flex-wrap: wrap; }
    .shot-bar .count { font-size: 12px; color: var(--muted); margin-left: auto; }
    .engine-pick { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
    .engine-pick select {
      background: var(--surface);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 4px 6px;
      font-family: inherit;
      font-size: 12px;
      outline: none;
    }
    .engine-pick select:focus { border-color: var(--accent); }
    #engineHint { margin: -6px 0 6px; }
    .btn:disabled { opacity: 0.5; cursor: default; }

    .shot-progress {
      height: 4px;
      background: var(--border);
      border-radius: 2px;
      overflow: hidden;
      margin: -6px 0 12px;
    }
    .shot-progress-bar {
      height: 100%;
      width: 0;
      background: var(--accent);
      transition: width 0.25s;
    }
    .shot-meta .warn { color: #dcdcaa; }
    .shot-meta .fail { color: #f48771; }

    .md-editor {
      width: 100%;
      min-height: 220px;
      background: var(--code-bg);
      color: var(--text);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 12px;
      font-family: 'Cascadia Code', 'Consolas', monospace;
      font-size: 13px;
      line-height: 1.6;
      resize: vertical;
      outline: none;
    }
    .md-editor:focus { border-color: var(--accent); }

    @media print {
      /* Nur den Dokumentinhalt drucken – Bedienelemente ausblenden. */
      .toolbar, .toc, .status { display: none !important; }
      /* Im Screenshot-Modus nur die Vorschau drucken, nicht die Bedienelemente. */
      .shot-drop, .shot-list, .shot-bar, .md-editor,
      .shots-panel h2, .shots-panel .hint,
      .shots-panel .section-label { display: none !important; }

      /* Helles Layout für Papier/PDF statt des dunklen Themes. */
      body, .content-wrap, .markdown-body {
        background: #fff !important;
        color: #000 !important;
        height: auto !important;
        overflow: visible !important;
        display: block !important;
      }
      .main { display: block !important; overflow: visible !important; }
      .content-wrap { padding: 0 !important; overflow: visible !important; }
      .markdown-body { max-width: none !important; }

      .markdown-body a { color: #0645ad !important; }
      .markdown-body h1, .markdown-body h2 { border-bottom-color: #ccc !important; }
      .markdown-body h1, .markdown-body h2, .markdown-body h3,
      .markdown-body h4, .markdown-body h5, .markdown-body h6 {
        color: #000 !important;
      }
      .markdown-body strong { color: #000 !important; }
      .markdown-body em { color: #000 !important; }
      .markdown-body code, .markdown-body pre {
        background: #f4f4f4 !important;
        color: #000 !important;
        border-color: #ddd !important;
      }
      .markdown-body pre code { color: #000 !important; }
      .markdown-body blockquote { color: #444 !important; border-left-color: #999 !important; }
      .markdown-body th { background: #eee !important; color: #000 !important; }
      .markdown-body th, .markdown-body td { border-color: #ccc !important; }
      .nb-cell, .nb-input, .nb-output { background: #fafafa !important; border-color: #ddd !important; }
      .nb-out-err { color: #b00 !important; }

      /* Sinnvolle Seitenumbrüche. */
      .markdown-body h1, .markdown-body h2, .markdown-body h3 { break-after: avoid; }
      .markdown-body pre, .markdown-body table, .markdown-body blockquote,
      .markdown-body img, .nb-cell { break-inside: avoid; }

      /* Suchtreffer-Markierung im Druck neutralisieren. */
      .highlight { background: transparent !important; }
    }
  </style>
</head>
<body>

<div class="toolbar">
  <span class="toolbar-title">MD</span>
  <button class="btn primary" onclick="triggerFileInput()">Datei öffnen</button>
  <input id="fileInput" type="file" accept=".md,.markdown,.txt,.ipynb,.docx,.html,.htm" style="display:none" onchange="loadLocalFile(this)">
  <button class="btn" onclick="pasteFromClipboard()" title="Inhalt der Zwischenablage übernehmen – Text wird angezeigt, Bilder und formatierter Inhalt gehen in den Umwandlungsbereich">Einfügen</button>
  <button class="btn" id="pdfBtn" onclick="saveAsPdf()" title="Als PDF speichern (Strg+P)">Als PDF</button>
  <button class="btn" id="shotBtn" onclick="toggleShotMode()" title="Screenshots und kopierten Text in Markdown umwandeln">Screenshots/Text</button>
  <button class="btn" id="clearBtn" onclick="clearScreen()" title="Ansicht zurücksetzen (Strg+Umschalt+L)">Löschen</button>
  <div class="search-bar">
    <input id="searchInput" type="text" placeholder="Suchen…" oninput="doSearch(this.value)" onkeydown="searchNav(event)">
  </div>
  <span class="filepath" id="filepath">Datei öffnen oder Markdown hierher ziehen</span>
  <button class="btn toc-toggle" id="tocBtn" onclick="toggleToc()" title="Inhaltsverzeichnis">☰ TOC</button>
</div>

<div class="main">
  <nav class="toc hidden" id="toc">
    <div class="toc-header">Inhaltsverzeichnis</div>
    <div id="tocLinks"></div>
  </nav>

  <div class="content-wrap" id="contentWrap">
    <div class="dropzone" id="dropzone">
      <div class="dropzone-icon">📄</div>
      <h2>Markdown- oder Notebook-Datei öffnen</h2>
      <p>Datei hierher ziehen oder den Button oben verwenden</p>
      <p style="margin-top:10px">
        <button class="btn" onclick="pasteFromClipboard()">Aus Zwischenablage einfügen</button>
      </p>
      <p style="font-size:11px;margin-top:8px;">Unterstützt: .md · .markdown · .txt · .ipynb</p>
      <p style="font-size:11px;">.docx und .html werden in Markdown umgewandelt</p>
      <p style="font-size:11px;">Bilder oder markierter Text hierher ziehen öffnet den Screenshot-Modus</p>
    </div>
    <div class="markdown-body" id="output" style="display:none"></div>

    <div class="shots-panel" id="shotsPanel" style="display:none">
      <h2>Screenshots, Text &amp; Dokumente → Markdown</h2>
      <p class="hint">
        Screenshots mit <strong>Strg+V</strong> einfügen, hierher ziehen oder auswählen.
        Ebenso lassen sich <strong>kopierter Text</strong> und
        <strong>gemischter Inhalt aus Webseite, Word oder Outlook</strong> einfügen – dort
        bleiben Überschriften, Listen, Tabellen und Bilder erhalten, weil die Formatierung
        aus der Zwischenablage übernommen und nicht neu erkannt wird. Solcher Inhalt
        erscheint <strong>sofort samt Bildern</strong> in der Vorschau, ohne Umwandeln.
        <strong>.docx</strong>- und <strong>.html</strong>-Dateien lassen sich direkt
        hineinziehen. Beim Speichern landen alle Bilder im Unterordner <code>bilder/</code>
        neben der .md-Datei und werden von dort relativ verlinkt – Bilder, die noch als
        Web-Adresse im Text stehen, werden dabei auf Nachfrage heruntergeladen.
      </p>

      <div class="shot-drop" id="shotDrop">
        Bilder, Word-Dateien oder markierten Inhalt hierher ziehen – oder mit Strg+V einfügen<br><br>
        <button class="btn" onclick="document.getElementById('shotInput').click()">Bilder wählen</button>
        <input id="shotInput" type="file" accept="image/*" multiple style="display:none"
               onchange="addShotFiles(this.files); this.value='';">
        <button class="btn" onclick="document.getElementById('docInput').click()">Word-/HTML-Datei wählen</button>
        <input id="docInput" type="file" accept=".docx,.html,.htm" multiple style="display:none"
               onchange="addImportFiles(this.files); this.value='';">
        <input id="missingInput" type="file" accept="image/*" multiple style="display:none"
               onchange="fillMissingImages(this.files); this.value='';">
        <span class="drop-sep">oder Text einfügen</span>
        <textarea class="text-input" id="textInput"
                  placeholder="Kopierten Text hier einfügen (Strg+V) und übernehmen …"></textarea>
        <div style="margin-top:8px">
          <button class="btn" onclick="addTextFromInput()">Text übernehmen</button>
          <button class="btn" onclick="diagnoseClipboard()"
                  title="Zeigt an, was die kopierte Seite in die Zwischenablage gelegt hat – hilfreich, wenn Bilder fehlen">Zwischenablage prüfen</button>
        </div>
      </div>

      <div class="section-label">Screenshots &amp; Textabschnitte</div>
      <div id="shotList"></div>

      <div class="shot-bar">
        <button class="btn primary" id="convertBtn" onclick="convertShots()">In Markdown umwandeln</button>
        <button class="btn" id="cancelBtn" onclick="cancelConvert()" style="display:none">Abbrechen</button>
        <button class="btn" onclick="clearShots()">Alle entfernen</button>
        <label class="engine-pick">Verfahren
          <select id="engineSelect" onchange="onEngineChange()">
            <option value="local">Lokal – Windows-Texterkennung (ohne API-Schlüssel)</option>
            <option value="api">Claude API (bessere Struktur, API-Schlüssel nötig)</option>
          </select>
        </label>
        <label class="engine-pick" title="Bilder, die im eingefügten Inhalt nur verlinkt sind, aus dem Netz nachladen">
          <input type="checkbox" id="allowRemote"> externe Bilder laden
        </label>
        <span class="count" id="shotCount"></span>
      </div>
      <p class="hint" id="engineHint"></p>
      <div class="shot-progress" id="shotProgress" style="display:none">
        <div class="shot-progress-bar" id="shotProgressBar"></div>
      </div>

      <div class="section-label">Markdown (bearbeitbar)</div>
      <textarea class="md-editor" id="mdEditor" oninput="previewShots()"
                placeholder="Hier erscheint das umgewandelte Markdown …"></textarea>

      <div class="shot-bar">
        <button class="btn primary" id="saveBtn" onclick="saveShots()">Als .md speichern</button>
        <button class="btn" onclick="previewShots()">Vorschau aktualisieren</button>
      </div>

      <div class="section-label">Vorschau</div>
      <div class="markdown-body" id="shotPreview"></div>
    </div>
  </div>
</div>

<div class="status" id="status">Bereit</div>

<script>
// Muss mit IMAGE_DIR_NAME auf der Serverseite übereinstimmen.
const IMG_DIR = 'bilder';

// ── marked.js konfigurieren ──
marked.setOptions({
  gfm: true,
  breaks: false,
  pedantic: false,
});

// ── Drag & Drop ──
const contentWrap = document.getElementById('contentWrap');
contentWrap.addEventListener('dragover', e => {
  e.preventDefault();
  document.getElementById('dropzone').classList.add('dragover');
});
contentWrap.addEventListener('dragleave', () => {
  document.getElementById('dropzone').classList.remove('dragover');
});
contentWrap.addEventListener('drop', e => {
  e.preventDefault();
  document.getElementById('dropzone').classList.remove('dragover');
  const files = e.dataTransfer.files;
  if (!files || !files.length) {
    // Keine Datei, aber markierter Inhalt: ab in den Screenshot-/Text-Modus.
    const html = e.dataTransfer.getData('text/html');
    if (html && html.trim()) {
    const entry = richFromHtml(html, 'Hereingezogener Inhalt');
    if (isRichClipboard(html, entry) && addRich(entry)) return;
  }
    addShotText(e.dataTransfer.getData('text/plain'), 'Hereingezogener Text');
    return;
  }
  // Bilder und Word-/HTML-Dateien landen im Screenshot-Modus,
  // alles andere im normalen Viewer.
  if (addShotFiles(files)) return;
  if (addImportFiles(files)) return;
  readFile(files[0]);
});

function triggerFileInput() {
  document.getElementById('fileInput').click();
}

// ── Zwischenablage einfügen (Knopf auf der Hauptseite) ──
// Strg+V setzt einen Tastendruck voraus und funktioniert nur, wenn die Seite
// den Fokus hat. Der Knopf liest die Zwischenablage aktiv aus und sortiert den
// Inhalt ein: Text wird angezeigt, Bilder und formatierter Inhalt gehen in den
// Umwandlungsbereich – dort gehören sie hin.
async function pasteFromClipboard() {
  if (!navigator.clipboard || !navigator.clipboard.read) {
    alert('Dieser Browser gibt die Zwischenablage nicht direkt frei.\n\n' +
          'Bitte stattdessen Strg+V drücken.');
    return;
  }

  let items;
  try {
    items = await navigator.clipboard.read();
  } catch (err) {
    alert('Der Zugriff auf die Zwischenablage wurde nicht erlaubt.\n\n' +
          'Beim ersten Mal fragt der Browser nach – die Frage bitte bestätigen. ' +
          'Alternativ funktioniert weiterhin Strg+V.');
    setStatus('Zwischenablage nicht gelesen: ' + err.message);
    return;
  }

  try {
    // 1. Bild – wie ein eingefügter Screenshot behandeln.
    for (const item of items) {
      const imageType = (item.types || []).find(t => t.startsWith('image/'));
      if (imageType) {
        const blob = await item.getType(imageType);
        const name = 'Zwischenablage.' + extFor(normalizeMediaType(imageType));
        addShotFiles([new File([blob], name, { type: imageType })]);
        return;
      }
    }

    // 2. Formatierter Inhalt mit Bildern und Auszeichnung.
    const htmlItem = items.find(item => (item.types || []).includes('text/html'));
    if (htmlItem) {
      const html = await (await htmlItem.getType('text/html')).text();
      const entry = richFromHtml(html, 'Aus der Zwischenablage');
      if (isRichClipboard(html, entry) && addRich(entry)) return;
    }

    // 3. Reiner Text: auf der Hauptseite heißt das anzeigen.
    const textItem = items.find(item => (item.types || []).includes('text/plain'));
    if (textItem) {
      const text = await (await textItem.getType('text/plain')).text();
      if (text.trim()) { showPastedText(text); return; }
    }

    alert('In der Zwischenablage ist nichts, was sich einfügen lässt.');
  } catch (err) {
    alert('Die Zwischenablage konnte nicht gelesen werden:\n\n' + err.message);
    setStatus('Fehler: ' + err.message);
  }
}

// Zeigt eingefügten Text wie eine geöffnete Datei an.
function showPastedText(text) {
  if (shotMode) toggleShotMode();
  dispatchRender(text, 'Zwischenablage', text.length);
  setStatus('Inhalt aus der Zwischenablage angezeigt · ' + text.length + ' Zeichen');
}

function loadLocalFile(input) {
  const file = input.files[0];
  if (!file) return;
  // Word-/HTML-Dateien gehören in den Umwandlungsbereich, nicht in den Viewer.
  if (isImportable(file)) { addImportFiles([file]); return; }
  readFile(file);
}

function readFile(file) {
  const reader = new FileReader();
  reader.onload = e => dispatchRender(e.target.result, file.name, file.size);
  reader.readAsText(file, 'UTF-8');
}

function dispatchRender(content, name, size) {
  const isIpynbByName = name && name.toLowerCase().endsWith('.ipynb');
  const isIpynbByContent = looksLikeNotebook(content);
  if (isIpynbByName || isIpynbByContent) {
    renderNotebook(content, name, size);
  } else {
    renderMarkdown(content, name, size);
  }
}

function looksLikeNotebook(content) {
  if (!content) return false;
  const trimmed = content.trimStart();
  if (trimmed.charAt(0) !== '{') return false;
  const head = trimmed.slice(0, 8192);
  if (/"cell_type"\s*:\s*"(code|markdown|raw)"/.test(head)) return true;
  if (/"cells"\s*:\s*\[/.test(head) && /"nbformat"\s*:/.test(content)) return true;
  return false;
}

// ── Server-seitig geladene Dateien (via API) ──
async function loadServerFile(path) {
  const res = await fetch('/api/file?path=' + encodeURIComponent(path));
  if (!res.ok) { alert('Fehler beim Laden der Datei'); return; }
  const data = await res.json();
  dispatchRender(data.content, data.name, data.size);
}

// ── Rendering ──
function renderMarkdown(source, name, size) {
  const output = document.getElementById('output');
  const dropzone = document.getElementById('dropzone');

  output.innerHTML = marked.parse(source);
  dropzone.style.display = 'none';
  output.style.display = 'block';

  document.getElementById('filepath').textContent = name || '';
  document.title = (name ? name + ' – ' : '') + 'Markdown Viewer';

  const words = source.trim().split(/\s+/).length;
  const kb = size ? (size / 1024).toFixed(1) + ' KB · ' : '';
  document.getElementById('status').textContent =
    `${kb}${words} Wörter · ${source.split('\n').length} Zeilen`;

  buildToc(output);
  typesetMath(output);
  document.getElementById('contentWrap').scrollTop = 0;
  document.getElementById('searchInput').value = '';
}

// ── Jupyter Notebook Rendering ──
function renderNotebook(source, name, size) {
  const output = document.getElementById('output');
  const dropzone = document.getElementById('dropzone');

  let nb;
  try {
    nb = JSON.parse(source);
  } catch (err) {
    output.innerHTML = '<p style="color:#f48771;">Ungültige .ipynb-Datei: ' +
                       escapeHtml(err.message) + '</p>';
    dropzone.style.display = 'none';
    output.style.display = 'block';
    return;
  }

  const cells = Array.isArray(nb.cells) ? nb.cells : [];
  const lang = (nb.metadata && nb.metadata.kernelspec && nb.metadata.kernelspec.language)
               || (nb.metadata && nb.metadata.language_info && nb.metadata.language_info.name)
               || 'python';

  const parts = [];
  let codeCells = 0;
  let mdCells = 0;

  cells.forEach(cell => {
    const src = joinSource(cell.source);
    if (cell.cell_type === 'markdown') {
      mdCells++;
      parts.push('<div class="nb-cell nb-cell-md"><div class="nb-md">' +
                 marked.parse(src) + '</div></div>');
    } else if (cell.cell_type === 'code') {
      codeCells++;
      const execCount = cell.execution_count == null ? ' ' : cell.execution_count;
      let html = '<div class="nb-cell">';
      html += '<div class="nb-input"><div class="nb-prompt in">In [' + execCount + ']:</div>' +
              '<div class="nb-code-body"><pre><code>' + escapeHtml(src) + '</code></pre></div></div>';
      const outs = Array.isArray(cell.outputs) ? cell.outputs : [];
      outs.forEach(o => {
        html += renderOutput(o, execCount);
      });
      html += '</div>';
      parts.push(html);
    } else if (cell.cell_type === 'raw') {
      parts.push('<div class="nb-cell"><div class="nb-input">' +
                 '<div class="nb-prompt">raw:</div>' +
                 '<div class="nb-code-body"><pre><code>' + escapeHtml(src) +
                 '</code></pre></div></div></div>');
    }
  });

  output.innerHTML = parts.join('\n');
  dropzone.style.display = 'none';
  output.style.display = 'block';

  document.getElementById('filepath').textContent = name || '';
  document.title = (name ? name + ' – ' : '') + 'Markdown Viewer';

  const kb = size ? (size / 1024).toFixed(1) + ' KB · ' : '';
  document.getElementById('status').textContent =
    `${kb}${cells.length} Zellen · ${codeCells} Code · ${mdCells} Markdown · ${lang}`;

  buildToc(output);
  typesetMath(output);
  document.getElementById('contentWrap').scrollTop = 0;
  document.getElementById('searchInput').value = '';
}

function joinSource(s) {
  if (Array.isArray(s)) return s.join('');
  return s == null ? '' : String(s);
}

function renderOutput(o, execCount) {
  const type = o.output_type;
  if (type === 'stream') {
    const text = joinSource(o.text);
    const cls = o.name === 'stderr' ? 'nb-out-err' : 'nb-out-text';
    return '<div class="nb-output"><div class="nb-prompt"></div>' +
           '<div class="nb-code-body"><pre class="' + cls + '">' +
           escapeHtml(stripAnsi(text)) + '</pre></div></div>';
  }
  if (type === 'error') {
    const tb = (o.traceback || []).map(l => stripAnsi(l)).join('\n');
    return '<div class="nb-output"><div class="nb-prompt"></div>' +
           '<div class="nb-code-body"><pre class="nb-out-err">' +
           escapeHtml(tb) + '</pre></div></div>';
  }
  if (type === 'execute_result' || type === 'display_data') {
    const data = o.data || {};
    const prompt = type === 'execute_result'
      ? '<div class="nb-prompt out">Out[' + execCount + ']:</div>'
      : '<div class="nb-prompt"></div>';
    let body = '';
    if (data['image/png']) {
      body = '<img class="nb-out-img" src="data:image/png;base64,' +
             joinSource(data['image/png']).replace(/\s+/g, '') + '">';
    } else if (data['image/jpeg']) {
      body = '<img class="nb-out-img" src="data:image/jpeg;base64,' +
             joinSource(data['image/jpeg']).replace(/\s+/g, '') + '">';
    } else if (data['image/svg+xml']) {
      body = '<div class="nb-out-html">' + sanitizeHtml(joinSource(data['image/svg+xml'])) + '</div>';
    } else if (data['text/html']) {
      body = '<div class="nb-out-html">' + sanitizeHtml(joinSource(data['text/html'])) + '</div>';
    } else if (data['text/markdown']) {
      body = '<div class="nb-out-html">' + marked.parse(joinSource(data['text/markdown'])) + '</div>';
    } else if (data['text/latex']) {
      body = '<div class="nb-out-html">' + escapeHtml(joinSource(data['text/latex'])) + '</div>';
    } else if (data['text/plain']) {
      body = '<pre class="nb-out-text">' + escapeHtml(joinSource(data['text/plain'])) + '</pre>';
    }
    return '<div class="nb-output">' + prompt +
           '<div class="nb-code-body">' + body + '</div></div>';
  }
  return '';
}

function stripAnsi(s) {
  return String(s).replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '');
}

// Minimaler HTML-Sanitizer: entfernt <script>, on*-Handler und javascript:-URLs.
function sanitizeHtml(html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = String(html);
  const walker = document.createTreeWalker(tpl.content, NodeFilter.SHOW_ELEMENT);
  const toRemove = [];
  let el;
  while ((el = walker.nextNode())) {
    const tag = el.tagName.toLowerCase();
    if (tag === 'script' || tag === 'iframe' || tag === 'object' || tag === 'embed' || tag === 'link' || tag === 'meta') {
      toRemove.push(el);
      continue;
    }
    for (const attr of Array.from(el.attributes)) {
      const n = attr.name.toLowerCase();
      const v = attr.value.trim().toLowerCase();
      if (n.startsWith('on')) el.removeAttribute(attr.name);
      else if ((n === 'href' || n === 'src' || n === 'xlink:href') && v.startsWith('javascript:')) {
        el.removeAttribute(attr.name);
      }
    }
  }
  toRemove.forEach(n => n.remove());
  return tpl.innerHTML;
}

function typesetMath(root) {
  if (window.MathJax && window.MathJax.typesetPromise) {
    window.MathJax.typesetPromise([root]).catch(() => {});
  }
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── Inhaltsverzeichnis ──
function buildToc(container) {
  const headings = container.querySelectorAll('h1, h2, h3');
  const tocLinks = document.getElementById('tocLinks');
  tocLinks.innerHTML = '';

  if (headings.length < 2) {
    document.getElementById('toc').classList.add('hidden');
    return;
  }

  let id = 0;
  headings.forEach(h => {
    const level = h.tagName.toLowerCase();
    if (!h.id) h.id = 'heading-' + (id++);
    const a = document.createElement('a');
    a.className = `toc-item ${level}`;
    a.href = '#' + h.id;
    a.textContent = h.textContent;
    a.onclick = e => {
      e.preventDefault();
      h.scrollIntoView({ behavior: 'smooth' });
      document.querySelectorAll('.toc-item').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
    };
    tocLinks.appendChild(a);
  });

  document.getElementById('toc').classList.remove('hidden');
}

function toggleToc() {
  document.getElementById('toc').classList.toggle('hidden');
}

// ── Volltextsuche ──
let searchMatches = [];
let searchIndex = 0;
let lastSearchTerm = '';

function doSearch(term) {
  // Highlights entfernen
  document.querySelectorAll('.highlight').forEach(el => {
    el.outerHTML = el.innerHTML;
  });
  searchMatches = [];
  if (!term || term.length < 2) return;

  const output = document.getElementById('output');
  highlightText(output, term);
  searchMatches = Array.from(document.querySelectorAll('.highlight'));
  searchIndex = 0;
  if (searchMatches.length > 0) {
    searchMatches[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    updateStatus();
  }
  lastSearchTerm = term;
}

function highlightText(node, term) {
  if (node.nodeType === Node.TEXT_NODE) {
    const regex = new RegExp(`(${escapeRegex(term)})`, 'gi');
    if (regex.test(node.textContent)) {
      const span = document.createElement('span');
      span.innerHTML = node.textContent.replace(regex, '<mark class="highlight">$1</mark>');
      node.replaceWith(span);
    }
  } else if (node.nodeType === Node.ELEMENT_NODE &&
             !['SCRIPT','STYLE','CODE','PRE'].includes(node.tagName)) {
    Array.from(node.childNodes).forEach(child => highlightText(child, term));
  }
}

function searchNav(e) {
  if (e.key === 'Enter' && searchMatches.length > 0) {
    searchMatches[searchIndex].classList.remove('current');
    searchIndex = (searchIndex + (e.shiftKey ? -1 : 1) + searchMatches.length) % searchMatches.length;
    searchMatches[searchIndex].classList.add('current');
    searchMatches[searchIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
    updateStatus();
    e.preventDefault();
  }
}

function updateStatus() {
  if (searchMatches.length === 0) return;
  document.getElementById('status').textContent =
    `Treffer: ${searchIndex + 1} / ${searchMatches.length}`;
}

function escapeRegex(s) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// ── Als PDF speichern (Browser-Druckdialog) ──
function saveAsPdf() {
  const output = document.getElementById('output');
  const preview = document.getElementById('shotPreview');
  const hasDoc = output.style.display !== 'none' && output.innerHTML.trim();
  const hasShots = shotMode && preview.innerHTML.trim();
  if (!hasDoc && !hasShots) {
    alert('Bitte zuerst eine Datei öffnen oder Screenshots umwandeln.');
    return;
  }
  // Offene Suchmarkierungen entfernen, damit sie nicht mitgedruckt werden.
  doSearch('');
  document.getElementById('searchInput').value = '';
  window.print();
}

// ── Screenshots & Text → Markdown ──
// Die Sammlung mischt zwei Sorten Einträge, unterschieden über kind:
//   kind:'image' = { dataUrl, mediaType, name, markdown, truncated, error, width, height }
//   kind:'text'  = { text, name, markdown, truncated, error }
// dataUrl haelt immer das Original - gespeichert wird ungerechnet, an die API
// geht bei Bedarf eine verkleinerte Fassung (spart Tokens und Zeit).
let shots = [];
let shotMode = false;
let converting = false;
let cancelRequested = false;
let lastGenerated = '';

// Breiter als das bringt für die Texterkennung nichts mehr und kostet nur Tokens.
const MAX_API_WIDTH = 1568;
// Höhere Bilder werden in Kacheln zerlegt. Ein sehr langer Full-Page-Screenshot
// darf nicht einfach kleingerechnet werden – dann wäre der Text unlesbar.
const MAX_TILE_HEIGHT = 1200;
// Lange Texte werden in Abschnitte zerlegt – analog zu den Bildkacheln, damit
// eine Antwort nicht an der Token-Grenze abgeschnitten wird.
const MAX_TEXT_CHARS = 12000;
// Für die lokale Windows-Erkennung wird nicht verkleinert (das kostet
// Genauigkeit), nur sehr hohe Bilder werden in Kacheln zerlegt.
const MAX_LOCAL_TILE_HEIGHT = 4000;

// Dateinamen der Bilder werden einmal vergeben und nie wieder geändert.
// Sie stehen im umgewandelten Text, und der bleibt beim Umsortieren oder
// Entfernen stehen – eine laufende Neunummerierung würde die Links brechen.
let shotSeq = 0;
let embedSeq = 0;

function toggleShotMode() {
  shotMode = !shotMode;
  const panel = document.getElementById('shotsPanel');
  const output = document.getElementById('output');
  const dropzone = document.getElementById('dropzone');
  const btn = document.getElementById('shotBtn');

  if (shotMode) {
    panel.style.display = 'block';
    output.style.display = 'none';
    dropzone.style.display = 'none';
    btn.classList.add('primary');
    renderShotList();
    setStatus('Screenshot-/Text-Modus – Bilder oder Text mit Strg+V einfügen, ziehen oder auswählen.');
  } else {
    panel.style.display = 'none';
    btn.classList.remove('primary');
    if (output.innerHTML.trim()) output.style.display = 'block';
    else dropzone.style.display = 'flex';
    setStatus('Bereit');
  }
  document.getElementById('contentWrap').scrollTop = 0;
}

function setStatus(text) {
  document.getElementById('status').textContent = text;
}

// ── Umwandlungsverfahren ──
// 'local' = Windows-Texterkennung im Hintergrund, ohne Schlüssel und ohne Netz.
// 'api'   = Claude liest Bild bzw. Text; erkennt Struktur deutlich besser,
//           braucht aber ANTHROPIC_API_KEY.
let serverStatus = { local_ocr: false, has_key: false, model: '' };

function currentEngine() {
  return document.getElementById('engineSelect').value;
}

function onEngineChange() {
  updateEngineHint();
  // Bereits umgewandelte Einträge bleiben stehen; mit ↻ lässt sich ein
  // einzelner mit dem neuen Verfahren wiederholen.
  setStatus(currentEngine() === 'local'
    ? 'Verfahren: lokale Windows-Texterkennung.'
    : 'Verfahren: Claude API (' + (serverStatus.model || 'API') + ').');
}

function updateEngineHint() {
  const hint = document.getElementById('engineHint');
  if (currentEngine() === 'local') {
    hint.textContent = serverStatus.local_ocr
      ? 'Läuft vollständig auf diesem Rechner – kein API-Schlüssel, keine Internetverbindung. '
        + 'Überschriften, Absätze und Listen werden aus Schriftgröße und Zeilenabständen abgeleitet; '
        + 'Tabellen bleiben dabei einfacher Text.'
      : 'Die Windows-Texterkennung ist auf diesem System nicht verfügbar (nur Windows 10/11 mit PowerShell). '
        + 'Eingefügter Text wird trotzdem lokal formatiert.';
  } else {
    hint.textContent = serverStatus.has_key
      ? 'Claude (' + serverStatus.model + ') liest Bild bzw. Text und erkennt Überschriften, Listen und Tabellen zuverlässiger. '
        + 'Die Inhalte werden dafür an die Anthropic-API übertragen.'
      : 'Kein API-Schlüssel gesetzt – bitte ANTHROPIC_API_KEY setzen und neu starten, oder das lokale Verfahren wählen.';
  }
}

async function loadServerStatus() {
  try {
    const res = await fetch('/api/status');
    if (res.ok) serverStatus = await res.json();
  } catch (e) { /* Vorgaben behalten */ }
  // Vorgabe: ist ein Schlüssel gesetzt, wurde er absichtlich gesetzt – dann die
  // API. Sonst die lokale Erkennung, damit der Modus ohne Schlüssel läuft.
  const sel = document.getElementById('engineSelect');
  sel.value = serverStatus.has_key ? 'api' : (serverStatus.local_ocr ? 'local' : 'api');
  updateEngineHint();
}

function extFor(mediaType) {
  if (mediaType === 'image/jpeg') return 'jpg';
  if (mediaType === 'image/gif') return 'gif';
  if (mediaType === 'image/webp') return 'webp';
  return 'png';
}

// Claude Vision unterstützt png, jpeg, gif und webp.
function normalizeMediaType(type) {
  if (type === 'image/jpeg' || type === 'image/jpg') return 'image/jpeg';
  if (type === 'image/gif') return 'image/gif';
  if (type === 'image/webp') return 'image/webp';
  return 'image/png';
}

// Liefert true, wenn mindestens ein Bild übernommen wurde.
function addShotFiles(files) {
  const images = Array.from(files || []).filter(f => f.type && f.type.startsWith('image/'));
  if (!images.length) return false;
  if (!shotMode) toggleShotMode();

  images.forEach(file => {
    const reader = new FileReader();
    reader.onload = e => {
      const shot = {
        kind: 'image',
        dataUrl: e.target.result,
        mediaType: normalizeMediaType(file.type),
        file: 'screenshot-' + (++shotSeq) + '.' + extFor(normalizeMediaType(file.type)),
        name: file.name || 'Zwischenablage',
        markdown: '',
        truncated: false,
        error: '',
        width: 0,
        height: 0
      };
      shots.push(shot);
      renderShotList();
      setStatus(shots.length + ' Eintrag/Einträge bereit.');
      // Abmessungen nachtragen, damit die Kachelzahl angezeigt werden kann.
      const probe = new Image();
      probe.onload = () => {
        shot.width = probe.naturalWidth;
        shot.height = probe.naturalHeight;
        renderShotList();
      };
      probe.src = shot.dataUrl;
    };
    reader.onerror = () => setStatus('Ein Bild konnte nicht gelesen werden.');
    reader.readAsDataURL(file);
  });
  return true;
}

// ── HTML aus der Zwischenablage → Markdown ──
// Beim Kopieren aus Browser, Word oder Outlook liegt neben dem reinen Text auch
// text/html in der Zwischenablage – mit vollständiger Formatierung und den
// Bildverweisen. Die Struktur muss also nicht geraten werden wie bei der OCR,
// sie ist bereits vorhanden. Der Läufer unten setzt sie 1:1 in Markdown um und
// sammelt dabei die Bilder ein; die Auflösung der Adressen passiert später.

// Blockelemente, die im Markdown auf einer eigenen Zeile stehen müssen.
const BLOCK_TAGS = new Set(['ADDRESS','ARTICLE','ASIDE','BLOCKQUOTE','DIV','DL','DT','DD',
  'FIELDSET','FIGCAPTION','FIGURE','FOOTER','FORM','H1','H2','H3','H4','H5','H6','HEADER',
  'HR','LI','MAIN','NAV','OL','P','PRE','SECTION','TABLE','TD','TH','TR','UL']);

// Setzt Blöcke zu einem Dokument zusammen. Listenpunkte bleiben ohne Leerzeile
// aneinander, damit sie eine Liste bilden – aber nur, solange sie zusammen-
// gehören: ein Wechsel zwischen Aufzählung und Nummerierung auf gleicher oder
// flacherer Ebene beginnt eine neue Liste und braucht die Leerzeile. Tiefer
// eingerückt heißt verschachtelt und bleibt eng.
function itemInfo(block) {
  const m = /^([ \t]*)(-|\d+\.)\s/.exec(block);
  return m ? { indent: m[1].length, ordered: m[2] !== '-' } : null;
}

function joinBlocks(blocks) {
  let md = '';
  blocks.forEach((block, i) => {
    if (i) {
      const prev = itemInfo(blocks[i - 1]);
      const cur = itemInfo(block);
      const tight = prev && cur && (cur.indent > prev.indent || cur.ordered === prev.ordered);
      md += tight ? '\n' : '\n\n';
    }
    md += block;
  });
  return md.trim();
}

// Zeichen, die Markdown sonst als Auszeichnung liest.
function escapeMd(text) {
  return text.replace(/([\\`*_\[\]])/g, '\\$1');
}

// Zeilenumbrüche in der Quelle sind in HTML bedeutungsloser Leerraum – die
// Struktur kommt aus den Blockelementen. Echte Umbrüche liefert nur <br>.
function collapseWs(text) {
  return text.replace(/\s+/g, ' ').replace(/ /g, ' ');
}

// Bedienelemente der Seite gehören nicht ins Dokument: Kopier-Knöpfe, Symbole,
// Formularfelder. Beim Kopieren aus Web-Apps hängt so etwas regelmäßig mitten
// im Inhalt – ein "Kopieren" über jedem Code-Block ist der Normalfall.
const SKIP_TAGS = new Set(['SCRIPT','STYLE','NOSCRIPT','HEAD','BUTTON','SVG','CANVAS',
  'VIDEO','AUDIO','IFRAME','SELECT','TEXTAREA','INPUT','OPTION']);

function isSkipped(node) {
  const tag = (node.tagName || '').toUpperCase();
  const chrome = SKIP_TAGS.has(tag) ||
                 (node.getAttribute && node.getAttribute('aria-hidden') === 'true');
  if (!chrome) return false;
  // Aber niemals einen Zweig wegwerfen, in dem ein Bild steckt: Web-Apps
  // machen ihre Bilder gern anklickbar und verpacken sie dafür in einen
  // Knopf. Ein "Kopieren"-Knopf enthält kein Bild und fliegt weiterhin raus.
  return !(node.querySelector && node.querySelector('img'));
}

// Lazy geladene Bilder tragen im src nur einen Platzhalter; die echte Adresse
// steht dann in data-src oder im srcset.
function imageSource(node) {
  const src = (node.getAttribute('src') || '').trim();
  const isPlaceholder = !src || (/^data:/i.test(src) && src.length < 256);
  if (!isPlaceholder) return src;

  const attr = ['data-src', 'data-original', 'data-lazy-src']
    .map(a => (node.getAttribute(a) || '').trim()).find(Boolean);
  if (attr) return attr;

  const fromSet = pickFromSrcset(node.getAttribute('srcset') || node.getAttribute('data-srcset'));
  if (fromSet) return fromSet;

  // <picture>: die Adresse steht dann in einem <source> daneben.
  const picture = node.parentNode;
  if (picture && (picture.tagName || '').toUpperCase() === 'PICTURE') {
    const sources = Array.prototype.slice.call(picture.querySelectorAll('source'));
    for (const source of sources) {
      const found = pickFromSrcset(source.getAttribute('srcset') || source.getAttribute('src'));
      if (found) return found;
    }
  }
  return src;
}

// Aus einem srcset die letzte (üblicherweise größte) Fassung ziehen.
function pickFromSrcset(value) {
  const srcset = (value || '').trim();
  if (!srcset) return '';
  return srcset.split(',').pop().trim().split(/\s+/)[0] || '';
}

// Sprache eines Code-Blocks aus der üblichen class="language-xyz".
function codeLanguage(pre) {
  const code = pre.querySelector('code') || pre;
  const cls = (code.className && String(code.className)) || '';
  const m = /(?:language|lang|highlight)[-_]([a-z0-9+#]+)/i.exec(cls);
  return m ? m[1].toLowerCase() : '';
}

// Word gibt Aufzählungen als normale Absätze mit einem Bullet-Span aus.
// Dieser Rest bleibt im Text stehen und wird hier abgeräumt.
const WORD_BULLET_RE = /^\s*(?:[·•●▪o]|\d+[.)])\s+/;

function looksLikeWordList(el) {
  const style = (el.getAttribute && el.getAttribute('style')) || '';
  const cls = (el.className && String(el.className)) || '';
  return /mso-list/i.test(style) || /MsoListParagraph/i.test(cls);
}

// Wandelt einen HTML-Baum in Markdown. Bilder gehen an sink(src, alt) und
// werden durch dessen Rückgabe (ein Platzhalter) ersetzt.
function htmlToMarkdown(root, sink) {
  const blocks = [];

  function inline(node) {
    if (node.nodeType === 3) return escapeMd(collapseWs(node.nodeValue || ''));
    if (node.nodeType !== 1) return '';
    const tag = node.tagName;

    if (tag === 'BR') return '  \n';
    if (tag === 'IMG') {
      // Manche Seiten schreiben die Bildadresse in das alt-Attribut. Als
      // Bildunterschrift ist eine URL unbrauchbar.
      let alt = (node.getAttribute('alt') || '').trim();
      if (/^(https?:|data:|blob:)/i.test(alt) || alt.length > 120) alt = '';
      return sink(imageSource(node), alt);
    }
    if (isSkipped(node)) return '';

    const inner = children(node).map(inline).join('');
    if (!inner.trim()) return inner;

    if (tag === 'STRONG' || tag === 'B') return wrap(inner, '**');
    if (tag === 'EM' || tag === 'I') return wrap(inner, '*');
    if (tag === 'DEL' || tag === 'S' || tag === 'STRIKE') return wrap(inner, '~~');
    if (tag === 'CODE' || tag === 'KBD' || tag === 'SAMP') {
      return '`' + inner.replace(/\\([\\`*_\[\]])/g, '$1') + '`';
    }
    if (tag === 'A') {
      const href = (node.getAttribute('href') || '').trim();
      // javascript:-Adressen und Ankerreste bringen im Dokument nichts.
      if (!href || /^javascript:/i.test(href) || href.startsWith('#')) return inner;
      return '[' + inner + '](' + href + ')';
    }
    return inner;
  }

  // Auszeichnung darf keine Leerzeichen einschließen, sonst greift sie nicht.
  function wrap(text, marker) {
    const m = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
    return m[1] + marker + m[2] + marker + m[3];
  }

  function children(node) {
    return Array.prototype.slice.call(node.childNodes);
  }

  function push(text) {
    const clean = text.replace(/[ \t]+$/gm, '').trim();
    if (clean) blocks.push(clean);
  }

  // Wie push, behält aber die führende Einrückung – die trägt bei
  // verschachtelten Listen die Ebene.
  function pushIndented(text) {
    const clean = text.replace(/[ \t]+$/gm, '').replace(/^\n+|\n+$/g, '');
    if (clean.trim()) blocks.push(clean);
  }

  function list(node, depth, ordered) {
    let n = 1;
    children(node).forEach(li => {
      if (li.nodeType !== 1 || li.tagName !== 'LI') return;
      // Verschachtelte Listen erst herausnehmen, sonst landen sie im Text.
      const nested = children(li).filter(c => c.nodeType === 1 &&
                                              (c.tagName === 'UL' || c.tagName === 'OL'));
      nested.forEach(c => li.removeChild(c));
      const body = children(li).map(inline).join('').trim();
      const marker = ordered ? (n++) + '. ' : '- ';
      if (body) pushIndented('  '.repeat(depth) + marker + body);
      nested.forEach(c => list(c, depth + 1, c.tagName === 'OL'));
    });
  }

  function table(node) {
    const rows = [];
    node.querySelectorAll('tr').forEach(tr => {
      const cells = [];
      tr.querySelectorAll('th,td').forEach(td => {
        cells.push(children(td).map(inline).join('').replace(/\s*\n\s*/g, ' ')
                                                    .replace(/\|/g, '\\|').trim());
      });
      if (cells.length) rows.push(cells);
    });
    if (!rows.length) return;
    const width = Math.max.apply(null, rows.map(r => r.length));
    const pad = r => r.concat(new Array(width - r.length).fill(''));
    const out = ['| ' + pad(rows[0]).join(' | ') + ' |', '|' + ' --- |'.repeat(width)];
    rows.slice(1).forEach(r => out.push('| ' + pad(r).join(' | ') + ' |'));
    push(out.join('\n'));
  }

  function block(node, depth) {
    if (node.nodeType === 3) {
      const text = collapseWs(node.nodeValue || '');
      if (text.trim()) push(escapeMd(text));
      return;
    }
    if (node.nodeType !== 1) return;
    const tag = node.tagName;

    if (isSkipped(node)) return;
    if (/^H[1-6]$/.test(tag)) { push('#'.repeat(+tag[1]) + ' ' + inline(node).trim()); return; }
    if (tag === 'UL' || tag === 'OL') { list(node, depth, tag === 'OL'); return; }
    if (tag === 'TABLE') { table(node); return; }
    if (tag === 'HR') { push('---'); return; }
    if (tag === 'PRE') {
      // Nur den Code selbst nehmen. Web-Apps bauen Kopfzeile, Sprachlabel und
      // Kopieren-Knopf mit in das <pre> – im <code> steht dagegen wirklich nur
      // das Listing.
      const lang = codeLanguage(node);
      const holder = (node.querySelector('code') || node).cloneNode(true);
      Array.prototype.slice.call(holder.querySelectorAll('button,svg')).forEach(
        el => el.parentNode && el.parentNode.removeChild(el));
      const code = (holder.textContent || '').replace(/^\s*\n/, '').replace(/\s+$/, '');
      if (code.trim()) blocks.push('```' + lang + '\n' + code + '\n```');
      return;
    }
    if (tag === 'BLOCKQUOTE') {
      const inner = children(node).map(inline).join('').trim();
      if (inner) push(inner.split('\n').map(l => '> ' + l).join('\n'));
      return;
    }
    if (tag === 'P' || tag === 'DIV' || tag === 'LI' || tag === 'SECTION' ||
        tag === 'ARTICLE' || tag === 'FIGURE' || tag === 'FIGCAPTION' ||
        tag === 'DD' || tag === 'DT' || tag === 'BODY' || tag === 'HTML') {
      // Enthält der Knoten selbst wieder Blöcke, einzeln weiterlaufen –
      // sonst als ein Absatz übernehmen.
      const kids = children(node);
      const hasBlocks = kids.some(c => c.nodeType === 1 && BLOCK_TAGS.has(c.tagName));
      if (hasBlocks) {
        kids.forEach(c => block(c, depth));
        return;
      }
      let text = inline(node).trim();
      if (!text) return;
      if (looksLikeWordList(node) && WORD_BULLET_RE.test(text)) {
        text = '- ' + text.replace(WORD_BULLET_RE, '');
      }
      push(text);
      return;
    }
    // Alles Übrige (span, a, b … auf oberster Ebene) als Absatz.
    const text = inline(node).trim();
    if (text) push(text);
  }

  children(root).forEach(c => block(c, 0));

  // Viele Seiten schreiben die Sprache als eigene Zeile über den Code-Block.
  // Steht direkt davor genau dieses eine Wort, ist es Beschriftung, kein Inhalt.
  const cleaned = blocks.filter((b, i) => {
    const next = blocks[i + 1] || '';
    if (!next.startsWith('```')) return true;
    const lang = next.slice(3, next.indexOf('\n')).trim();
    return !(lang && b.trim().toLowerCase() === lang);
  });

  return joinBlocks(cleaned);
}

// ── Zwischenablage untersuchen ──
// Wenn Bilder nicht ankommen, liegt es fast immer daran, was die Quellseite
// überhaupt in die Zwischenablage legt. Das lässt sich nicht erraten, also
// zeigt der Viewer es auf Wunsch an.
let diagnoseNextPaste = false;

function diagnoseClipboard() {
  diagnoseNextPaste = true;
  setStatus('Bitte jetzt Strg+V drücken – die Zwischenablage wird nur untersucht, nichts übernommen.');
  alert('Kopiere den Inhalt wie gewohnt und drücke jetzt Strg+V.\n\n' +
        'Es wird nichts hinzugefügt – es wird nur angezeigt, was die Seite in die ' +
        'Zwischenablage gelegt hat.');
}

function clipboardReport(clip) {
  const types = clip ? Array.from(clip.types || []) : [];
  const html = clip ? clip.getData('text/html') : '';
  const text = clip ? clip.getData('text/plain') : '';
  const files = clip ? Array.from(clip.items || []).filter(
    it => it.kind === 'file').map(it => it.type) : [];

  const tags = html.match(/<img\b[^>]*>/gi) || [];
  const lines = [];
  lines.push('Formate in der Zwischenablage: ' + (types.join(', ') || '(keine)'));
  lines.push('Bilddateien direkt enthalten:  ' + (files.length ? files.join(', ') : 'nein'));
  lines.push('text/plain:                   ' + text.length + ' Zeichen');
  lines.push('text/html:                    ' + html.length + ' Zeichen');
  lines.push('<img>-Tags im HTML:           ' + tags.length);
  lines.push('<picture>/<source>:           ' + (html.match(/<(picture|source)\b/gi) || []).length);
  lines.push('<svg>:                        ' + (html.match(/<svg\b/gi) || []).length);
  lines.push('background-image im Stil:     ' + (html.match(/background-image/gi) || []).length);
  lines.push('');
  if (tags.length) {
    lines.push('Die ersten Bild-Tags:');
    tags.slice(0, 5).forEach((t, i) => lines.push('  ' + (i + 1) + ') ' + t.slice(0, 300)));
  } else {
    lines.push('Es sind keine <img>-Tags enthalten. Die Bilder gehören dann entweder');
    lines.push('nicht zur Auswahl, oder die Seite stellt sie anders dar (Hintergrundbild,');
    lines.push('Zeichenfläche, eingebettetes SVG). In dem Fall hilft nur: Bild einzeln');
    lines.push('kopieren (Rechtsklick → Grafik kopieren) und mit Strg+V einfügen.');
  }
  lines.push('');
  lines.push('--- Anfang des HTML (500 Zeichen) ---');
  lines.push(html.slice(0, 500) || '(leer)');
  return lines.join('\n');
}

function showClipboardReport(clip) {
  diagnoseNextPaste = false;
  const report = clipboardReport(clip);
  const preview = document.getElementById('shotPreview');
  preview.textContent = '';
  const pre = document.createElement('pre');
  pre.style.whiteSpace = 'pre-wrap';
  pre.style.fontSize = '12px';
  pre.textContent = report;
  preview.appendChild(pre);
  preview.scrollIntoView({ block: 'start' });
  setStatus('Zwischenablage untersucht – Ergebnis unten unter "Vorschau".');
}

// Lohnt sich der HTML-Weg? Trägt die Zwischenablage Bilder oder echte Struktur,
// ist er dem reinen Text klar überlegen. Bei bloß eingefärbten Textschnipseln
// (Web-Apps verpacken selbst einen Satz in <span style=…>) bringt er nichts –
// dann ist der Textweg besser, weil dort noch Struktur erkannt werden kann.
const RICH_TAGS = /<(h[1-6]|ul|ol|li|table|tr|td|pre|code|blockquote|img|a|strong|em|b|i)\b/i;

function isRichClipboard(html, entry) {
  if (entry && entry.images.length) return true;
  return RICH_TAGS.test(html || '');
}

// Erzeugt aus eingefügtem HTML einen Eintrag mit Markdown-Vorlage und Bildliste.
// Die Bilder stehen dort zunächst nur als Adresse; aufgelöst wird beim Umwandeln.
function richFromHtml(html, name) {
  const doc = new DOMParser().parseFromString(html, 'text/html');
  // Derselbe Sanitizer wie für Notebook-Ausgaben – eingefügtes HTML ist fremd.
  const holder = document.createElement('div');
  holder.innerHTML = sanitizeHtml(doc.body ? doc.body.innerHTML : html);

  const images = [];
  const seen = {};
  const template = htmlToMarkdown(holder, (src, alt) => {
    // Auch ein Bild ohne brauchbare Adresse wird aufgenommen – als sichtbare
    // Lücke mit Fehlermeldung. Stillschweigend wegzulassen wäre schlimmer:
    // dann fehlt es im Ergebnis, ohne dass es jemand merkt.
    const key = src || 'leer-' + images.length;
    if (seen[key] === undefined) {
      seen[key] = images.length;
      images.push({
        src: src, alt: alt, dataUrl: '', mediaType: '', file: '',
        error: src ? '' : 'Bild ohne lesbare Adresse – die Seite lädt es per Skript nach.'
      });
    }
    return '{{IMG:' + seen[key] + '}}';
  });
  if (!template.trim() && !images.length) return null;

  // Standen im HTML mehr Bilder, als übernommen wurden, ist etwas an der
  // Umwandlung falsch – das darf nicht als "ohne Bilder" durchgehen.
  const inSource = (String(html).match(/<img\b/gi) || []).length;
  const lost = inSource - images.length;

  return {
    kind: 'rich',
    lostImages: lost > 0 ? lost : 0,
    template: template,
    images: images,
    text: (holder.textContent || '').replace(/\s+/g, ' ').trim(),
    name: name || 'Eingefügter Inhalt',
    markdown: '',
    truncated: false,
    error: ''
  };
}

// Aufnehmen eines fertigen Rich-Eintrags (aus Zwischenablage oder Datei).
function addRich(entry) {
  if (!entry) return false;
  if (!shotMode) toggleShotMode();

  // Sofort anzeigen statt erst nach dem Umwandeln: Eingebettete Bilder
  // bekommen gleich ihren Dateinamen, Bilder aus dem Netz behalten vorerst
  // ihre Adresse – anzeigen kann der Browser beides ohne Umweg.
  assignLocalImages(entry);
  entry.markdown = renderRichMarkdown(entry);
  shots.push(entry);
  renderShotList();
  refreshEditor();

  const total = entry.images.length;
  const remote = entry.images.filter(img => !img.file && /^https?:/i.test(img.src)).length;
  let msg = 'Formatierter Inhalt übernommen und angezeigt';
  msg += total ? ' – ' + total + ' Bild(er).' : ' (ohne Bilder).';
  if (remote) {
    msg += ' ' + remote + ' Bild(er) liegen noch im Netz und werden beim Speichern' +
           ' heruntergeladen.';
  }
  if (entry.lostImages) {
    msg += ' Achtung: ' + entry.lostImages + ' Bild(er) aus der Quelle wurden nicht' +
           ' übernommen – bitte mit "Zwischenablage prüfen" melden.';
  }
  setStatus(msg);
  return true;
}

// Kopierten Text als eigenen Eintrag aufnehmen. Gleiche Rückgabe wie
// addShotFiles, damit beide Wege austauschbar in den Drop-/Paste-Handlern sind.
function addShotText(text, name) {
  const raw = (text || '').replace(/\r\n/g, '\n').trim();
  if (!raw) return false;
  if (!shotMode) toggleShotMode();

  shots.push({
    kind: 'text',
    text: raw,
    name: name || 'Eingefügter Text',
    markdown: '',
    truncated: false,
    error: ''
  });
  renderShotList();
  setStatus(shots.length + ' Eintrag/Einträge bereit.');
  return true;
}

// ── Dateien mit gemischtem Inhalt (.docx, .html) ──
// Word-Dateien liest der Server (zipfile + xml), HTML-Dateien der Browser.
// Beide landen als Rich-Eintrag und laufen danach denselben Weg wie
// eingefügter Inhalt.
function isImportable(file) {
  return /\.(docx|html?|htm)$/i.test(file.name || '');
}

function bytesToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  // In Blöcken, sonst sprengt ein großes Dokument den Aufrufstapel.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

// Liefert true, wenn mindestens eine Datei übernommen wurde.
function addImportFiles(files) {
  const docs = Array.from(files || []).filter(isImportable);
  if (!docs.length) return false;
  if (!shotMode) toggleShotMode();

  docs.forEach(file => {
    if (/\.docx$/i.test(file.name)) {
      importDocx(file);
    } else {
      const reader = new FileReader();
      reader.onload = e => {
        if (!addRich(richFromHtml(e.target.result, file.name))) {
          setStatus('In ' + file.name + ' war kein verwertbarer Inhalt.');
        }
      };
      reader.readAsText(file, 'UTF-8');
    }
  });
  return true;
}

async function importDocx(file) {
  setStatus(file.name + ' wird gelesen …');
  try {
    const buffer = await file.arrayBuffer();
    const res = await fetch('/api/import', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: file.name, data: bytesToBase64(buffer) })
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));

    // Die Bilder liegen schon vor; aufzulösen ist nichts mehr.
    const images = (data.images || []).map(img => ({
      src: '', alt: '', mediaType: img.media_type, error: '', file: '',
      dataUrl: 'data:' + img.media_type + ';base64,' + img.data
    }));
    addRich({
      kind: 'rich', template: data.markdown || '', images: images,
      text: (data.markdown || '').replace(/\s+/g, ' ').trim(),
      name: file.name, markdown: '', truncated: false, error: ''
    });
  } catch (err) {
    alert('Die Datei ' + file.name + ' konnte nicht gelesen werden:\n\n' + err.message);
    setStatus('Fehler: ' + err.message);
  }
}

// ── Bilder eines Rich-Eintrags auflösen ──
// data:-Bilder liegen bereits vor. file://-Bilder (so fügt Word ein) und
// http(s)-Bilder holt der Server – Letztere nur, wenn es freigegeben ist.
function allowRemoteImages() {
  const box = document.getElementById('allowRemote');
  return !!(box && box.checked);
}

function extForUrl(url, fallback) {
  const m = /\.(png|jpe?g|gif|webp|bmp|tiff?|svg)(?:[?#]|$)/i.exec(url || '');
  if (!m) return fallback || 'png';
  return m[1].toLowerCase() === 'jpeg' ? 'jpg' : m[1].toLowerCase();
}

async function resolveRichImages(entry) {
  for (const img of entry.images) {
    if (cancelRequested) return;
    if (img.dataUrl) {
      if (!img.file) {
        img.file = 'bild-' + (++embedSeq) + '.' + extFor(img.mediaType ||
                     normalizeMediaType((img.dataUrl.split(';')[0] || '').slice(5)));
      }
      continue;
    }
    if (img.error) continue;

    if (/^data:/i.test(img.src)) {
      const mediaType = normalizeMediaType((img.src.split(';')[0] || '').slice(5));
      img.dataUrl = img.src;
      img.mediaType = mediaType;
      img.file = 'bild-' + (++embedSeq) + '.' + extFor(mediaType);
      continue;
    }

    try {
      const res = await fetch('/api/fetch-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: img.src, allow_remote: allowRemoteImages() })
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
      img.mediaType = data.media_type;
      img.dataUrl = 'data:' + data.media_type + ';base64,' + data.data;
      img.file = 'bild-' + (++embedSeq) + '.' + extForUrl(img.src, extFor(data.media_type));
    } catch (err) {
      img.error = err.message;
    }
  }
}

// Setzt die Platzhalter der Vorlage durch die Bildpfade.
// Noch nicht heruntergeladene Bilder behalten ihre Originaladresse: die zeigt
// der Browser sofort an – so wie Word es beim Einfügen tut – und beim
// Speichern werden sie durch die lokalen Dateien ersetzt. Ein toter relativer
// Pfad wäre schlechter als eine Adresse, die wenigstens noch etwas zeigt.
function renderRichMarkdown(entry) {
  return entry.template.replace(/\{\{IMG:(\d+)\}\}/g, (_all, n) => {
    const img = entry.images[+n];
    if (!img) return '';
    const alt = img.alt || 'Bild';
    if (img.file) return '![' + alt + '](' + IMG_DIR + '/' + img.file + ')';
    if (/^https?:/i.test(img.src)) return '![' + alt + '](' + img.src + ')';
    return '*[Bild nicht übernommen: ' + (img.error || 'unbekannte Quelle') + ']*';
  });
}

// Bilder, die bereits im Browser vorliegen (eingebettete data:-Bilder, Bilder
// aus einer Word-Datei), brauchen weder Netz noch Server – ihnen wird sofort
// ein Dateiname gegeben. Damit steht im Editor der kurze Pfad bilder/… statt
// einer seitenlangen data:-Adresse, und die Vorschau zeigt sie trotzdem an.
function assignLocalImages(entry) {
  entry.images.forEach(img => {
    if (img.file) return;
    if (!img.dataUrl && /^data:/i.test(img.src)) {
      img.dataUrl = img.src;
      img.mediaType = normalizeMediaType((img.src.split(';')[0] || '').slice(5));
    }
    if (img.dataUrl) {
      img.mediaType = img.mediaType || 'image/png';
      img.file = 'bild-' + (++embedSeq) + '.' + extFor(img.mediaType);
    }
  });
}

// Noch offene Bilder, die im Netz liegen – die lassen sich nachträglich holen.
function pendingRemoteImages() {
  const out = [];
  shots.forEach(entry => {
    if (entry.kind !== 'rich') return;
    entry.images.forEach(img => {
      if (!img.file && /^https?:/i.test(img.src)) out.push({ entry: entry, img: img });
    });
  });
  return out;
}

// Übernimmt den Inhalt des Textfelds in der Dropzone und leert es wieder.
function addTextFromInput() {
  if (converting) return;
  const box = document.getElementById('textInput');
  if (!addShotText(box.value)) {
    alert('Bitte zuerst Text in das Feld einfügen.');
    return;
  }
  box.value = '';
}

// Bilder, die sich nicht von selbst holen lassen: blob:-Adressen, relative
// Pfade, oder solche, an denen ein Versuch bereits gescheitert ist. Nur die
// muss der Anwender von Hand nachreichen.
function stuckImages(entry) {
  if (entry.kind !== 'rich') return [];
  return entry.images.filter(img => !img.file && (img.error || !/^https?:/i.test(img.src)));
}

function shotState(s) {
  if (s.error) return '<span class="fail">✕ ' + escapeHtml(s.error) + '</span>';
  if (s.truncated) return '<span class="warn">⚠ umgewandelt, evtl. gekürzt</span>';
  if (s.markdown) {
    if (s.kind === 'rich') {
      // Der Text steht immer; unterschieden wird nur der Stand der Bilder.
      const stuck = stuckImages(s).length;
      if (stuck) {
        return '<span class="warn">⚠ angezeigt, ' + stuck +
               ' Bild(er) nicht ladbar – bitte einsetzen</span>';
      }
      const remote = s.images.filter(i => !i.file).length;
      if (remote) {
        return '<span class="done">✓ angezeigt</span><span>' + remote +
               ' Bild(er) noch als Web-Link – werden beim Speichern geholt</span>';
      }
      return '<span class="done">✓ übernommen, Bilder liegen lokal</span>';
    }
    return '<span class="done">✓ umgewandelt</span>';
  }
  return '<span>noch nicht umgewandelt</span>';
}

// Zeigt Abmessungen bzw. Textlänge und – wenn zerlegt wird – die Anzahl der Teile.
function shotSizeInfo(s) {
  if (s.kind === 'rich') {
    const n = s.images.length;
    return '<span>' + s.text.length + ' Zeichen · ' +
           (n ? n + ' Bild(er)' : 'ohne Bilder') + ' · Formatierung übernommen</span>';
  }
  if (s.kind === 'text') {
    const parts = splitText(s.text).length;
    return '<span>' + s.text.length + ' Zeichen' +
           (parts > 1 ? ' · wird in ' + parts + ' Teilen gelesen' : '') + '</span>';
  }
  if (!s.width || !s.height) return '<span></span>';
  const rows = planTiles(s.width, s.height).rows;
  const size = s.width + ' × ' + s.height + ' px';
  return '<span>' + size + (rows > 1 ? ' · wird in ' + rows + ' Teilen gelesen' : '') + '</span>';
}

// Nummerierung läuft je Sorte getrennt, damit die Bilddateinamen
// (screenshot-N.ext) lückenlos bleiben, auch wenn Text dazwischen liegt.
function ordinalOf(index) {
  const kind = shots[index].kind;
  let n = 0;
  for (let i = 0; i <= index; i++) if (shots[i].kind === kind) n++;
  return n;
}

const KIND_LABEL = { text: 'Textabschnitt ', rich: 'Inhalt ', image: 'Screenshot ' };

function shotTitle(i) {
  return (KIND_LABEL[shots[i].kind] || 'Screenshot ') + ordinalOf(i);
}

function renderShotList() {
  const list = document.getElementById('shotList');
  if (!shots.length) {
    list.innerHTML = '<p class="shot-empty">Noch nichts hinzugefügt – Screenshots oder Text einfügen.</p>';
  } else {
    list.innerHTML = shots.map((s, i) => {
      const redo = s.markdown || s.error
        ? '<button class="btn" onclick="resetShot(' + i + ')" title="erneut umwandeln">↻</button>'
        : '';
      // Nur anbieten, wenn Bilder wirklich hängen – Web-Links holt der
      // Speichern-Schritt von selbst, dafür braucht es keinen Knopf.
      const fill = stuckImages(s).length
        ? '<button class="btn" onclick="pickMissingImages(' + i + ')" ' +
          'title="Fehlende Bilder von Hand einsetzen">+ Bilder</button>'
        : '';
      const title = shotTitle(i);
      // Rich-Einträge zeigen ihr erstes eingebettetes Bild, sonst den Textanfang.
      const firstImage = s.kind === 'rich' && s.images.length && s.images[0].dataUrl
        ? s.images[0].dataUrl : '';
      const thumb = (s.kind === 'text' || (s.kind === 'rich' && !firstImage))
        ? '<div class="shot-thumb text">' + escapeHtml(s.text.slice(0, 220)) + '</div>'
        : '<img class="shot-thumb" src="' + (firstImage || s.dataUrl) + '" alt="' + title + '">';
      return '<div class="shot-item">' + thumb +
        '<div class="shot-meta"><strong>' + title + '</strong>' +
        '<span>' + escapeHtml(s.name) + '</span>' + shotSizeInfo(s) + shotState(s) + '</div>' +
        '<div class="shot-actions">' + fill + redo +
        '<button class="btn" onclick="moveShot(' + i + ',-1)" title="nach oben">↑</button>' +
        '<button class="btn" onclick="moveShot(' + i + ',1)" title="nach unten">↓</button>' +
        '<button class="btn" onclick="removeShot(' + i + ')" title="entfernen">✕</button>' +
        '</div>' +
        '</div>';
    }).join('');
  }
  const count = kind => shots.filter(s => s.kind === kind).length;
  const parts = [];
  if (count('image')) parts.push(count('image') + ' Screenshot(s)');
  if (count('text')) parts.push(count('text') + ' Textabschnitt(e)');
  if (count('rich')) parts.push(count('rich') + ' eingefügte(r) Inhalt(e)');
  document.getElementById('shotCount').textContent = parts.join(' · ');
}

function moveShot(i, dir) {
  if (converting) return;
  const j = i + dir;
  if (j < 0 || j >= shots.length) return;
  const tmp = shots[i];
  shots[i] = shots[j];
  shots[j] = tmp;
  renderShotList();
  refreshEditor();
}

function removeShot(i) {
  if (converting) return;
  shots.splice(i, 1);
  renderShotList();
  refreshEditor();
}

function resetShot(i) {
  if (converting) return;
  shots[i].markdown = '';
  shots[i].truncated = false;
  shots[i].error = '';
  // Auch die Bildfehler zurücksetzen – sonst versucht ein erneuter Lauf
  // die fehlgeschlagenen Bilder gar nicht noch einmal (z. B. nachdem das
  // Häkchen für externe Bilder gesetzt wurde).
  if (shots[i].kind === 'rich') shots[i].images.forEach(img => { img.error = ''; });
  renderShotList();
}

// Bilder, die sich nicht holen ließen – etwa blob:-Adressen aus einer
// Web-App – lassen sich von Hand nachreichen. Die Dateien füllen die offenen
// Stellen in der Reihenfolge, in der sie im Text vorkommen.
function pickMissingImages(i) {
  if (converting) return;
  pendingImageTarget = i;
  document.getElementById('missingInput').click();
}

let pendingImageTarget = -1;

function fillMissingImages(files) {
  const entry = shots[pendingImageTarget];
  pendingImageTarget = -1;
  if (!entry) return;
  const open = entry.images.filter(img => !img.file);
  const picked = Array.from(files || []).filter(f => f.type && f.type.startsWith('image/'));
  if (!open.length || !picked.length) return;

  let pending = Math.min(open.length, picked.length);
  picked.slice(0, open.length).forEach((file, k) => {
    const reader = new FileReader();
    reader.onload = e => {
      const slot = open[k];
      slot.mediaType = normalizeMediaType(file.type);
      slot.dataUrl = e.target.result;
      slot.error = '';
      slot.file = 'bild-' + (++embedSeq) + '.' + extFor(slot.mediaType);
      if (--pending === 0) {
        entry.markdown = renderRichMarkdown(entry);
        renderShotList();
        refreshEditor();
        setStatus('Fehlende Bilder eingesetzt.');
      }
    };
    reader.onerror = () => { pending--; };
    reader.readAsDataURL(file);
  });
}

function clearShots() {
  if (converting) return;
  if (shots.length && !confirm('Alle Einträge entfernen?')) return;
  shots = [];
  lastGenerated = '';
  shotSeq = 0;
  embedSeq = 0;
  document.getElementById('mdEditor').value = '';
  document.getElementById('shotPreview').innerHTML = '';
  renderShotList();
  setStatus('Sammlung geleert.');
}

// Baut den Editor neu auf, ohne eigene Korrekturen zu überschreiben.
// Liefert false, wenn der Text vom Nutzer verändert wurde und stehen bleibt.
function refreshEditor(force) {
  const ed = document.getElementById('mdEditor');
  const edited = ed.value.trim() && ed.value !== lastGenerated;
  if (edited && !force) return false;
  lastGenerated = buildCombinedMarkdown();
  ed.value = lastGenerated;
  previewShots();
  return true;
}

// Bilder werden relativ als bilder/screenshot-N.ext verlinkt. Der Pfad ist unabhängig
// vom späteren Dateinamen, weil der Ordner immer neben der .md-Datei liegt.
function buildCombinedMarkdown() {
  return shots.map((s, i) => {
    let body = (s.markdown || '_(noch nicht umgewandelt)_').trim();
    if (s.truncated) {
      body += '\n\n> ⚠ Die Umwandlung wurde an der Token-Grenze abgeschnitten. ' +
              'Die Quelle enthält vermutlich mehr Text als übernommen wurde.';
    }
    // Eingefügte Inhalte bringen ihre eigenen Überschriften mit – eine
    // zusätzliche Abschnittsüberschrift wäre hier nur Lärm.
    if (s.kind === 'rich') return body + '\n';
    // Nur Screenshots bekommen das Originalbild angehängt; Text steht für sich.
    const img = s.kind === 'text' ? '' :
      '\n\n![' + shotTitle(i) + '](' + IMG_DIR + '/' + s.file + ')\n';
    return '## ' + shotTitle(i) + '\n\n' + body + img + (s.kind === 'text' ? '\n' : '');
  }).join('\n---\n\n');
}

// Alle Bilder, die beim Speichern in den Unterordner gehören: die Screenshots
// selbst und die Bilder aus eingefügten Inhalten. Der Dateiname steht bereits
// im Markdown, deshalb wird hier nicht neu nummeriert.
function allImages() {
  const out = [];
  shots.forEach(s => {
    if (s.kind === 'image') {
      out.push({ dataUrl: s.dataUrl, file: s.file });
    } else if (s.kind === 'rich') {
      s.images.forEach(img => {
        if (img.dataUrl && img.file) out.push({ dataUrl: img.dataUrl, file: img.file });
      });
    }
  });
  return out;
}

// Zerlegt langen Text an Absatz-, sonst an Zeilengrenzen. Analog zu den
// Bildkacheln: mehrere Anfragen statt einer abgeschnittenen Antwort.
function splitText(text) {
  const raw = (text || '').trim();
  if (raw.length <= MAX_TEXT_CHARS) return raw ? [raw] : [];

  const chunks = [];
  let rest = raw;
  while (rest.length > MAX_TEXT_CHARS) {
    let cut = rest.lastIndexOf('\n\n', MAX_TEXT_CHARS);
    if (cut < MAX_TEXT_CHARS * 0.5) cut = rest.lastIndexOf('\n', MAX_TEXT_CHARS);
    if (cut < MAX_TEXT_CHARS * 0.5) cut = rest.lastIndexOf(' ', MAX_TEXT_CHARS);
    if (cut < MAX_TEXT_CHARS * 0.5) cut = MAX_TEXT_CHARS;   // notfalls hart schneiden
    chunks.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) chunks.push(rest);
  return chunks;
}

// Zerlegt ein Bild in die Kacheln, die an die API gehen. Breite wird auf
// MAX_API_WIDTH begrenzt, die Höhe aber nie zusammengestaucht – stattdessen
// entstehen mehrere Kacheln. Das Original bleibt unangetastet und wird später
// unverändert auf die Platte geschrieben.
function planTiles(w, h) {
  // Die lokale Erkennung arbeitet am Original am genauesten – hier wird nur
  // zerlegt, damit die Windows-Engine nicht an ihrer Größengrenze scheitert.
  if (currentEngine() === 'local') {
    return { factor: 1, rows: Math.max(1, Math.ceil(h / MAX_LOCAL_TILE_HEIGHT)) };
  }
  const factor = w > MAX_API_WIDTH ? MAX_API_WIDTH / w : 1;
  const rows = Math.max(1, Math.ceil((h * factor) / MAX_TILE_HEIGHT));
  return { factor: factor, rows: rows };
}

function prepareForApi(shot) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => {
      const ow = img.naturalWidth;
      const oh = img.naturalHeight;
      if (!ow || !oh) { reject(new Error('Bild hat keine gültige Größe.')); return; }

      const plan = planTiles(ow, oh);
      const targetW = Math.max(1, Math.round(ow * plan.factor));
      const out = shot.mediaType === 'image/jpeg' ? 'image/jpeg' : 'image/png';

      // Klein genug: unverändert schicken, spart eine Neucodierung.
      if (plan.factor >= 1 && plan.rows === 1) {
        resolve([{ data: shot.dataUrl.split(',')[1], media_type: shot.mediaType }]);
        return;
      }

      const tiles = [];
      for (let r = 0; r < plan.rows; r++) {
        // In Originalkoordinaten rechnen, damit sich keine Rundungslücken bilden.
        const sy = Math.round((r * oh) / plan.rows);
        const sh = Math.round(((r + 1) * oh) / plan.rows) - sy;
        const dh = Math.max(1, Math.round(sh * plan.factor));
        const canvas = document.createElement('canvas');
        canvas.width = targetW;
        canvas.height = dh;
        const ctx = canvas.getContext('2d');
        ctx.imageSmoothingEnabled = true;
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(img, 0, sy, ow, sh, 0, 0, targetW, dh);
        tiles.push({ data: canvas.toDataURL(out, 0.92).split(',')[1], media_type: out });
      }
      resolve(tiles);
    };
    img.onerror = () => reject(new Error('Bild konnte nicht gelesen werden.'));
    img.src = shot.dataUrl;
  });
}

function setProgress(done, total) {
  const wrap = document.getElementById('shotProgress');
  const bar = document.getElementById('shotProgressBar');
  if (!total) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'block';
  bar.style.width = Math.round((done / total) * 100) + '%';
}

function cancelConvert() {
  cancelRequested = true;
  setStatus('Abbruch nach dem laufenden Screenshot …');
}

// Jeder Screenshot wird einzeln geschickt. Das gibt Fortschritt, erlaubt
// Abbrechen und hält Teilergebnisse fest, wenn ein einzelnes Bild scheitert.
async function convertShots() {
  if (converting) return;
  if (!shots.length) { alert('Bitte zuerst Screenshots oder Text hinzufügen.'); return; }

  // Rich-Einträge sind schon beim Einfügen sichtbar; offen ist bei ihnen nur
  // noch, die Bilder aus dem Netz zu holen.
  const needsWork = s => !s.markdown ||
    (s.kind === 'rich' && s.images.some(img => !img.file && !img.error));
  const todo = shots.map((s, i) => i).filter(i => needsWork(shots[i]));
  if (!todo.length) {
    alert('Alles ist bereits umgewandelt.\n\nMit ↻ lässt sich ein einzelner Eintrag erneut umwandeln.');
    return;
  }

  converting = true;
  cancelRequested = false;
  const btn = document.getElementById('convertBtn');
  const cancelBtn = document.getElementById('cancelBtn');
  const label = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Wird umgewandelt …';
  cancelBtn.style.display = 'inline-block';

  let done = 0;
  let failed = 0;
  setProgress(0, todo.length);

  try {
    for (const i of todo) {
      if (cancelRequested) break;
      shots[i].error = '';
      try {
        // Rich-Einträge tragen ihre Formatierung schon in sich – hier ist
        // nichts zu erkennen, nur die Bilder sind noch zu holen.
        if (shots[i].kind === 'rich') {
          setStatus(shotTitle(i) + ' – Bilder werden geholt …');
          await resolveRichImages(shots[i]);
          if (cancelRequested) break;
          shots[i].markdown = renderRichMarkdown(shots[i]);
          done++;
          setProgress(done, todo.length);
          renderShotList();
          refreshEditor();
          continue;
        }
        // Bilder werden in Kacheln zerlegt, Text in Abschnitte – beides geht
        // als Folge einzelner Anfragen raus.
        const isText = shots[i].kind === 'text';
        const tiles = isText ? splitText(shots[i].text) : await prepareForApi(shots[i]);
        if (!tiles.length) throw new Error('Kein Inhalt zum Umwandeln.');
        const parts = [];
        let truncated = false;
        for (let t = 0; t < tiles.length; t++) {
          if (cancelRequested) break;
          setStatus(shotTitle(i) + ' (' + (i + 1) + ' von ' + shots.length + ')' +
                    (tiles.length > 1 ? ' – Teil ' + (t + 1) + ' von ' + tiles.length : '') +
                    ' wird umgewandelt …');
          const res = await fetch('/api/convert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(
              isText ? { text: tiles[t], engine: currentEngine() }
                     : { image: tiles[t], engine: currentEngine() })
          });
          const data = await res.json();
          if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
          const md = (data.markdown || '').trim();
          if (md) parts.push(md);
          if (data.truncated) truncated = true;
        }
        // Beim Abbruch mitten in den Kacheln nichts Halbes als Ergebnis ablegen.
        if (cancelRequested && parts.length < tiles.length) break;
        shots[i].markdown = parts.join('\n\n');
        shots[i].truncated = truncated;
      } catch (err) {
        shots[i].error = err.message;
        failed++;
      }
      done++;
      setProgress(done, todo.length);
      renderShotList();
      refreshEditor();
    }
  } finally {
    converting = false;
    btn.disabled = false;
    btn.textContent = label;
    cancelBtn.style.display = 'none';
    setProgress(0, 0);
  }

  const kept = refreshEditor();
  const parts = [];
  if (cancelRequested) parts.push('abgebrochen');
  parts.push((done - failed) + ' von ' + todo.length + ' umgewandelt');
  if (failed) parts.push(failed + ' fehlgeschlagen');
  if (!kept) parts.push('Editor unverändert gelassen (eigene Änderungen erkannt)');
  setStatus(parts.join(' · '));

  if (failed) {
    const msgs = shots.map((s, i) => s.error ? (shotTitle(i) + ': ' + s.error) : '')
                      .filter(Boolean);
    alert('Nicht alles konnte umgewandelt werden:\n\n' + msgs.join('\n') +
          '\n\nDie übrigen Ergebnisse bleiben erhalten. Mit ↻ lässt sich ein einzelner erneut versuchen.');
  }
}

// Für die Vorschau werden die relativen Bildpfade durch die Data-URLs ersetzt,
// damit die Screenshots schon vor dem Speichern sichtbar sind.
function previewShots() {
  const md = document.getElementById('mdEditor').value;
  let html = marked.parse(md);
  allImages().forEach(img => {
    html = html.split('"' + IMG_DIR + '/' + img.file + '"').join('"' + img.dataUrl + '"');
  });
  const preview = document.getElementById('shotPreview');
  preview.innerHTML = html;
  typesetMath(preview);
}

// Holt die noch verlinkten Bilder herunter und ersetzt ihre Adressen im
// bereits geschriebenen Text. Die Ersetzung läuft über den Editorinhalt und
// nicht über die Vorlage, damit eigene Änderungen daran erhalten bleiben.
async function downloadPendingImages() {
  const pending = pendingRemoteImages();
  if (!pending.length) return { done: 0, failed: 0 };

  const editor = document.getElementById('mdEditor');
  let done = 0, failed = 0;
  for (const { entry, img } of pending) {
    setStatus('Bild ' + (done + failed + 1) + ' von ' + pending.length + ' wird geholt …');
    try {
      const res = await fetch('/api/fetch-image', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // Der Anwender hat den Download eben ausdrücklich bestätigt.
        body: JSON.stringify({ url: img.src, allow_remote: true })
      });
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));
      img.mediaType = data.media_type;
      img.dataUrl = 'data:' + data.media_type + ';base64,' + data.data;
      img.file = 'bild-' + (++embedSeq) + '.' + extForUrl(img.src, extFor(data.media_type));
      // Adresse überall im Dokument durch den lokalen Pfad ersetzen.
      const local = IMG_DIR + '/' + img.file;
      editor.value = editor.value.split('(' + img.src + ')').join('(' + local + ')');
      entry.markdown = renderRichMarkdown(entry);
      done++;
    } catch (err) {
      img.error = err.message;
      failed++;
    }
  }
  lastGenerated = editor.value;
  renderShotList();
  previewShots();
  return { done: done, failed: failed };
}

async function saveShots() {
  if (converting) { alert('Bitte warten, bis die Umwandlung fertig ist.'); return; }
  let md = document.getElementById('mdEditor').value.trim();
  if (!md) { alert('Kein Markdown zum Speichern vorhanden.'); return; }

  // Bilder, die noch als Web-Adresse im Text stehen, gehören für ein
  // vollständiges Dokument in den Bilderordner. Hier – und nicht früher –
  // ist der Moment, danach zu fragen.
  const pending = pendingRemoteImages();
  if (pending.length) {
    const yes = confirm(pending.length + ' Bild(er) stehen noch als Web-Adresse im Text.\n\n' +
      'Jetzt herunterladen und in den Ordner "' + IMG_DIR + '" legen?\n\n' +
      'Abbrechen speichert das Dokument mit den Web-Adressen – dann sind die ' +
      'Bilder nur mit Internetverbindung sichtbar.');
    if (yes) {
      const result = await downloadPendingImages();
      if (result.failed) {
        alert(result.failed + ' Bild(er) konnten nicht geladen werden.\n\n' +
              'Sie bleiben als Web-Adresse im Text stehen. Über "+ Bilder" lassen ' +
              'sie sich von Hand einsetzen.');
      }
      md = document.getElementById('mdEditor').value.trim();
    }
  }
  const btn = document.getElementById('saveBtn');
  btn.disabled = true;
  setStatus('Speichern-Dialog geöffnet – bitte Ziel wählen …');

  try {
    // Die Dateinamen stehen schon im Markdown – hier werden sie nur mitgegeben.
    const payload = {
      markdown: md,
      images: allImages().map(img => ({
        data: img.dataUrl.split(',')[1],
        filename: img.file
      }))
    };
    const res = await fetch('/api/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || ('HTTP ' + res.status));

    if (data.cancelled) { setStatus('Speichern abgebrochen.'); return; }
    setStatus('Gespeichert: ' + data.path);
    alert('Gespeichert:\n' + data.path +
          (data.images_dir ? '\n\nBilder: ' + data.images_dir : ''));
  } catch (err) {
    alert('Fehler beim Speichern:\n\n' + err.message);
    setStatus('Fehler: ' + err.message);
  } finally {
    btn.disabled = false;
  }
}

// Ob das Ziel ein Eingabefeld ist – dort gehört Strg+V dem Feld selbst.
function isEditable(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'TEXTAREA' || tag === 'INPUT' || el.isContentEditable;
}

// Screenshots und kopierten Text direkt aus der Zwischenablage einfügen (Strg+V).
document.addEventListener('paste', e => {
  // Untersuchen geht allem voran – auch im Textfeld.
  if (diagnoseNextPaste) {
    e.preventDefault();
    showClipboardReport(e.clipboardData);
    return;
  }
  if (isEditable(e.target)) return;   // Text-/Suchfeld behält sein eigenes Einfügen
  const items = (e.clipboardData && e.clipboardData.items) || [];
  const files = [];
  for (const item of items) {
    if (item.kind === 'file' && item.type && item.type.startsWith('image/')) {
      const f = item.getAsFile();
      if (f) files.push(f);
    }
  }
  if (files.length) {
    e.preventDefault();
    addShotFiles(files);
    return;
  }
  // Liegt HTML in der Zwischenablage, ist die Formatierung samt Bildern darin
  // enthalten – das ist der verlustfreie Weg und hat Vorrang vor reinem Text.
  const html = e.clipboardData ? e.clipboardData.getData('text/html') : '';
  if (html && html.trim()) {
    const entry = richFromHtml(html, 'Aus der Zwischenablage');
    if (isRichClipboard(html, entry)) {
      e.preventDefault();
      if (addRich(entry)) return;
    }
  }
  const text = e.clipboardData ? e.clipboardData.getData('text/plain') : '';
  if (text && text.trim()) {
    e.preventDefault();
    addShotText(text, 'Aus der Zwischenablage');
  }
});

// Eigene Dropzone im Panel
const shotDrop = document.getElementById('shotDrop');
shotDrop.addEventListener('dragover', e => {
  e.preventDefault();
  shotDrop.classList.add('dragover');
});
shotDrop.addEventListener('dragleave', () => shotDrop.classList.remove('dragover'));
shotDrop.addEventListener('drop', e => {
  shotDrop.classList.remove('dragover');
  // Direkt ins Textfeld gezogen: dort einfügen lassen, nicht abfangen.
  if (isEditable(e.target)) return;
  e.preventDefault();
  e.stopPropagation();
  if (addShotFiles(e.dataTransfer.files)) return;
  if (addImportFiles(e.dataTransfer.files)) return;
  // Markierter Inhalt lässt sich aus Browser oder Word direkt herüberziehen;
  // auch hier hat die HTML-Fassung Vorrang.
  const html = e.dataTransfer.getData('text/html');
  if (html && html.trim()) {
    const entry = richFromHtml(html, 'Hereingezogener Inhalt');
    if (isRichClipboard(html, entry) && addRich(entry)) return;
  }
  addShotText(e.dataTransfer.getData('text/plain'), 'Hereingezogener Text');
});

// Im Textfeld der Dropzone übernimmt Strg+Enter den Inhalt.
document.getElementById('textInput').addEventListener('keydown', e => {
  if (e.ctrlKey && e.key === 'Enter') {
    e.preventDefault();
    addTextFromInput();
  }
});

// Wird formatierter Inhalt in das Textfeld eingefügt, darf er dort nicht
// landen: eine Textarea kann nur reinen Text, Bilder und Auszeichnung wären
// verloren. Solcher Inhalt wird deshalb direkt als eigener Eintrag übernommen.
// Reiner Text bleibt im Feld – dafür ist es da.
document.getElementById('textInput').addEventListener('paste', e => {
  // Beim Untersuchen übernimmt der Handler am Dokument – hier nichts tun.
  if (diagnoseNextPaste) return;
  const html = e.clipboardData && e.clipboardData.getData('text/html');
  if (!html || !html.trim()) return;
  const entry = richFromHtml(html, 'Aus der Zwischenablage');
  if (!entry || !isRichClipboard(html, entry)) return;
  e.preventDefault();
  addRich(entry);
});

// Warnen, wenn beim Verlassen noch nicht gespeicherte Umwandlungen offen sind.
window.addEventListener('beforeunload', e => {
  if (shots.some(s => s.markdown)) {
    e.preventDefault();
    e.returnValue = '';
  }
});

renderShotList();
loadServerStatus();

// ── Bildschirm löschen ──
// Kontextabhängig: im Viewer wird die Anzeige zurückgesetzt, im Screenshot-Modus
// die Sammlung geleert. Nur Letzteres kann Arbeit kosten – daher die Rückfrage.
function clearScreen() {
  if (converting) {
    alert('Die Umwandlung läuft noch. Bitte warten oder zuerst abbrechen.');
    return;
  }
  if (shotMode) {
    clearShotArea();
  } else {
    clearViewer();
  }
  document.getElementById('contentWrap').scrollTop = 0;
}

function clearShotArea() {
  const editor = document.getElementById('mdEditor');
  const textBox = document.getElementById('textInput');
  const hasWork = shots.length || editor.value.trim() || textBox.value.trim();
  if (!hasWork) { setStatus('Nichts zu löschen.'); return; }
  if (!confirm('Screenshots, Textabschnitte und umgewandelten Text verwerfen?')) return;

  shots = [];
  lastGenerated = '';
  editor.value = '';
  textBox.value = '';
  document.getElementById('shotPreview').innerHTML = '';
  document.getElementById('shotInput').value = '';
  document.getElementById('docInput').value = '';
  // Nichts verweist mehr auf die alten Bildnamen – also wieder bei 1 anfangen.
  shotSeq = 0;
  embedSeq = 0;
  renderShotList();
  setProgress(0, 0);
  setStatus('Screenshot-Bereich geleert.');
}

function clearViewer() {
  const output = document.getElementById('output');
  if (!output.innerHTML.trim()) { setStatus('Nichts zu löschen.'); return; }

  output.innerHTML = '';
  output.style.display = 'none';
  document.getElementById('dropzone').style.display = 'flex';

  // Suche zurücksetzen – sonst zeigen Treffer auf entfernte Knoten.
  searchMatches = [];
  searchIndex = 0;
  lastSearchTerm = '';
  document.getElementById('searchInput').value = '';

  // Inhaltsverzeichnis und Kopfzeile in den Ausgangszustand.
  document.getElementById('tocLinks').innerHTML = '';
  document.getElementById('toc').classList.add('hidden');
  document.getElementById('filepath').textContent = 'Datei öffnen oder Markdown hierher ziehen';
  document.title = 'Markdown Viewer';

  // Leeren, damit dieselbe Datei danach erneut geöffnet werden kann.
  document.getElementById('fileInput').value = '';
  setStatus('Bereit');
}

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'o') {
    e.preventDefault();
    triggerFileInput();
  }
  if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
    e.preventDefault();
    document.getElementById('searchInput').focus();
  }
  // Strg+Umschalt+L statt Strg+L – Letzteres belegt der Browser für die Adresszeile.
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'L' || e.key === 'l')) {
    e.preventDefault();
    clearScreen();
  }
});
</script>
</body>
</html>
"""


def _claude_headers():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Kein API-Schluessel gefunden. Bitte die Umgebungsvariable "
            "ANTHROPIC_API_KEY setzen und den Viewer neu starten."
        )
    return {
        "x-api-key": key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


def _strip_fences(text):
    """Entfernt ein umschliessendes Code-Fence, falls das Modell doch eines setzt."""
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip().startswith("```"):
            return "\n".join(lines[1:-1]).strip()
    return text


def _post_to_api(body):
    """POST an die Messages-API. Wiederholt bei Ueberlast und Netzfehlern."""
    data = json.dumps(body).encode("utf-8")
    for attempt in range(CLAUDE_RETRIES):
        last = attempt == CLAUDE_RETRIES - 1
        req = urllib.request.Request(
            ANTHROPIC_API_URL, data=data, headers=_claude_headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=CLAUDE_TIMEOUT) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            try:
                detail = json.loads(detail)["error"]["message"]
            except Exception:
                detail = detail[:300]
            if e.code in RETRY_CODES and not last:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("API-Fehler {}: {}".format(e.code, detail))
        except urllib.error.URLError as e:
            if not last:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("Keine Verbindung zur API: {}".format(e.reason))
    raise RuntimeError("API nach mehreren Versuchen nicht erreichbar.")


def transcribe_image(b64_data, media_type):
    """Schickt einen Screenshot an Claude.

    Liefert (markdown, truncated). truncated=True bedeutet, dass die Antwort
    an der Token-Grenze abgeschnitten wurde und der Text unvollstaendig ist.
    """
    if not b64_data:
        raise RuntimeError("Leeres Bild uebergeben.")

    payload = _post_to_api({
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                },
                {"type": "text", "text": VISION_PROMPT},
            ],
        }],
    })

    return _extract_text(payload)


def _extract_text(payload):
    """Holt den Text aus einer Antwort und meldet Ablehnung/Abschneiden."""
    # Sicherheitsklassifikatoren koennen eine Anfrage ablehnen (kommt als HTTP 200).
    if payload.get("stop_reason") == "refusal":
        raise RuntimeError("Die Anfrage wurde vom Modell abgelehnt.")
    text = "".join(
        b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"
    )
    if not text.strip():
        raise RuntimeError("Das Modell hat keinen Text zurueckgegeben.")
    return _strip_fences(text.strip()), payload.get("stop_reason") == "max_tokens"


def transcribe_text(raw):
    """Formatiert kopierten Rohtext als Markdown.

    Gleicher Rueckgabewert wie transcribe_image: (markdown, truncated).
    Lange Texte zerlegt der Client vorher in Abschnitte.
    """
    if not raw or not raw.strip():
        raise RuntimeError("Leerer Text uebergeben.")

    payload = _post_to_api({
        "model": CLAUDE_MODEL,
        "max_tokens": CLAUDE_MAX_TOKENS,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": TEXT_PROMPT},
                {"type": "text", "text": "<text>\n" + raw + "\n</text>"},
            ],
        }],
    })
    return _extract_text(payload)


# ── Lokale Umwandlung (ohne API-Schluessel) ───────────────────────────
# Die Texterkennung uebernimmt die in Windows eingebaute Engine
# (Windows.Media.Ocr), angesprochen ueber einen kurzen PowerShell-Aufruf.
# Das braucht weder einen Schluessel noch eine Internetverbindung noch ein
# zusaetzliches Paket - passt also zur Abhaengigkeitsfreiheit des Viewers.
# Die Struktur (Ueberschriften, Absaetze, Listen) leiten wir anschliessend aus
# Schriftgroesse und Zeilenabstaenden ab; das bleibt naturgemaess grober als
# die Umwandlung ueber die API.

POWERSHELL = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"),
    "System32", "WindowsPowerShell", "v1.0", "powershell.exe")

OCR_TIMEOUT = 180          # Sekunden pro Bild
_NO_WINDOW = 0x08000000    # CREATE_NO_WINDOW - sonst blitzt eine Konsole auf

# Gibt je erkannter Zeile eine Zeile "links<TAB>oben<TAB>hoehe<TAB>text" aus.
# Ganzzahlen, damit das Dezimaltrennzeichen der Systemsprache keine Rolle spielt.
OCR_PS = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, $type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op))
    $t.Wait(-1) | Out-Null
    $t.Result
}
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
    $langs = [Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages
    if ($langs.Count -gt 0) {
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($langs[0])
    }
}
if (-not $engine) { throw 'Keine OCR-Sprache installiert.' }

$file    = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:MDV_OCR_IMAGE)) ([Windows.Storage.StorageFile])
$stream  = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result  = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])

foreach ($line in $result.Lines) {
    $tops    = $line.Words | ForEach-Object { $_.BoundingRect.Y }
    $lefts   = $line.Words | ForEach-Object { $_.BoundingRect.X }
    $bottoms = $line.Words | ForEach-Object { $_.BoundingRect.Y + $_.BoundingRect.Height }
    $top = ($tops | Measure-Object -Minimum).Minimum
    $bot = ($bottoms | Measure-Object -Maximum).Maximum
    $left = ($lefts | Measure-Object -Minimum).Minimum
    $cells = @([int]$left, [int]$top, [int]($bot - $top), $line.Text)
    [Console]::Out.WriteLine(($cells -join "`t"))
}
"""


def local_ocr_available():
    """Windows-OCR laesst sich nur unter Windows mit PowerShell 5.1 ansprechen."""
    return sys.platform == "win32" and os.path.isfile(POWERSHELL)


def _run_ocr(image_path):
    """Ruft die Windows-OCR auf und liefert die Rohzeilen als Tupel-Liste."""
    encoded = base64.b64encode(OCR_PS.encode("utf-16-le")).decode("ascii")
    env = dict(os.environ, MDV_OCR_IMAGE=image_path)
    try:
        proc = subprocess.run(
            [POWERSHELL, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, timeout=OCR_TIMEOUT, env=env, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Die Texterkennung hat zu lange gebraucht.")
    except OSError as e:
        raise RuntimeError("PowerShell nicht startbar: {}".format(e))

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        msg = detail[0] if detail else "unbekannter Fehler"
        raise RuntimeError("Windows-Texterkennung fehlgeschlagen: {}".format(msg[:300]))

    rows = []
    for raw in proc.stdout.decode("utf-8", "replace").splitlines():
        parts = raw.split("\t", 3)
        if len(parts) != 4:
            continue
        try:
            rows.append((int(parts[0]), int(parts[1]), int(parts[2]), parts[3].strip()))
        except ValueError:
            continue
    return rows


def ocr_image_local(b64_data, media_type):
    """Erkennt den Text eines Screenshots lokal. Rueckgabe wie transcribe_image."""
    if not b64_data:
        raise RuntimeError("Leeres Bild uebergeben.")
    if not local_ocr_available():
        raise RuntimeError(
            "Die lokale Texterkennung steht nur unter Windows zur Verfuegung.")

    suffix = "." + {"image/jpeg": "jpg", "image/gif": "gif",
                    "image/webp": "webp"}.get(media_type, "png")
    fd, path = tempfile.mkstemp(prefix="mdv-ocr-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(base64.b64decode(b64_data))
        rows = _run_ocr(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass

    if not rows:
        raise RuntimeError("Im Bild wurde kein Text gefunden.")
    return ocr_rows_to_markdown(rows), False


def _median(values):
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return 0.0
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


# Aufzaehlungszeichen, wie sie in Screenshots und beim Kopieren auftauchen.
_BULLET_RE = re.compile(r"^[\u2022\u25aa\u25cf\u25e6\u2023\u00b7\u2219\u25c6\u25a0\u2043*+-]\s+")
_NUMBER_RE = re.compile(r"^\d{1,3}[.)]\s+")


def _as_list_item(text):
    """Macht aus einer Aufzaehlungszeile Markdown - sonst None."""
    m = _BULLET_RE.match(text)
    if m:
        return "- " + text[m.end():].strip()
    if _NUMBER_RE.match(text):
        return text
    return None


def ocr_rows_to_markdown(rows):
    """Baut aus den erkannten Zeilen Markdown.

    Die OCR liefert nur Zeilen mit Position und Hoehe. Groessere Schrift wird
    zur Ueberschrift, ein groesserer Zeilenabstand trennt Absaetze, und
    umgebrochene Zeilen eines Absatzes werden wieder zusammengezogen.
    """
    rows = [r for r in rows if r[3]]
    if not rows:
        return ""
    med = _median([h for (_l, _t, h, _x) in rows if h > 0]) or 1.0

    # Absatzgrenzen vorab bestimmen: ein Abstand groesser als eine halbe
    # Zeilenhoehe trennt zwei Absaetze.
    breaks = [False]
    prev_bottom = rows[0][1] + rows[0][2]
    for _left, top, height, _text in rows[1:]:
        breaks.append((top - prev_bottom) > 0.6 * med)
        prev_bottom = top + height

    out = []
    para = []

    def flush():
        if para:
            out.append(" ".join(para))
            del para[:]

    for i, (_left, _top, height, text) in enumerate(rows):
        if breaks[i]:
            flush()
        # Allein stehende Zeile: davor und danach eine Absatzgrenze.
        alone = (i == 0 or breaks[i]) and (i == len(rows) - 1 or breaks[i + 1])

        item = _as_list_item(text)
        if item:
            flush()
            out.append(item)
            continue

        # Die Zeilenhoehe kommt aus den Buchstabenkaesten und schwankt je nach
        # Ober-/Unterlaengen. Deutlich groessere Schrift ist eine Ueberschrift;
        # bei nur leicht groesserer muss die Zeile zusaetzlich allein stehen,
        # kurz sein und ohne Satzzeichen enden.
        level = None
        if height > 1.6 * med:
            level = "#"
        elif height > 1.3 * med:
            level = "##"
        elif (height >= 1.05 * med and alone and len(text) <= 60
                and text[-1:] not in ".!?,;:"):
            level = "###"
        if level and len(text) <= 90:
            flush()
            out.append(level + " " + text)
            continue

        para.append(text)
    flush()

    return _join_blocks(out)


def _item_info(block):
    """(Einrueckung, nummeriert?) fuer einen Listenpunkt - sonst None."""
    stripped = block.lstrip()
    indent = len(block) - len(stripped)
    if stripped.startswith("- "):
        return (indent, False)
    if _NUMBER_RE.match(stripped):
        return (indent, True)
    return None


def _join_blocks(blocks):
    """Setzt Bloecke zu einem Dokument zusammen.

    Listenpunkte bleiben ohne Leerzeile aneinander, damit sie eine Liste
    bilden - aber nur solange sie zusammengehoeren: ein Wechsel zwischen
    Aufzaehlung und Nummerierung auf gleicher oder flacherer Ebene beginnt
    eine neue Liste und braucht die Leerzeile. Tiefer eingerueckt heisst
    verschachtelt und bleibt eng.
    """
    out = []
    for i, block in enumerate(blocks):
        if i:
            prev, cur = _item_info(blocks[i - 1]), _item_info(block)
            tight = prev and cur and (cur[0] > prev[0] or cur[1] == prev[1])
            if not tight:
                out.append("")
        out.append(block)
    return "\n".join(out).strip()


def format_text_markdown(raw):
    """Formatiert kopierten Rohtext lokal als Markdown - ohne Modell.

    Absichtlich konservativ: erkannt werden Aufzaehlungen, nummerierte Listen,
    Tabulator-Tabellen und einzeln stehende Ueberschriftszeilen. Alles andere
    bleibt Absatz. Rueckgabe wie transcribe_text: (markdown, truncated).
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text)
    out = []

    for block in blocks:
        lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
        if not lines:
            continue

        # Mehrere Zeilen mit Tabulatoren: als Tabelle uebernehmen.
        tabbed = [ln for ln in lines if "\t" in ln]
        if len(lines) > 1 and len(tabbed) == len(lines):
            out.append(_tab_table(lines))
            continue

        # Einzelne kurze Zeile ohne Satzzeichen: als Ueberschrift lesen.
        if len(lines) == 1 and len(lines[0]) <= 70 and not lines[0][-1:] in ".!?:,;":
            item = _as_list_item(lines[0])
            out.append(item if item else "## " + lines[0])
            continue

        para = []
        for line in lines:
            item = _as_list_item(line)
            if item:
                if para:
                    out.append(" ".join(para))
                    para = []
                out.append(item)
            else:
                para.append(line)
        if para:
            out.append(" ".join(para))

    result = _join_blocks(out)
    if not result:
        raise RuntimeError("Leerer Text uebergeben.")
    return result, False


def _tab_table(lines):
    """Tabulatorspalten (so kopiert man Tabellen) als Markdown-Tabelle."""
    rows = [[c.strip() for c in re.split(r"\t+", ln)] for ln in lines]
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head = "| " + " | ".join(rows[0]) + " |"
    sep = "|" + " --- |" * width
    body = ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join([head, sep] + body)


# ── Bilder aus eingefuegten Inhalten holen ────────────────────────────
# Beim Einfuegen aus Word/Outlook zeigen die Bilder auf lokale Temp-Dateien
# (file:///...msohtmlclip1/...), die der Browser aus Sicherheitsgruenden nicht
# lesen darf - der lokale Server aber schon. Bilder von Webseiten werden nur
# geholt, wenn der Anwender das ausdruecklich erlaubt hat.

FETCH_TIMEOUT = 30
MAX_IMAGE_BYTES = 25 * 1024 * 1024

IMAGE_TYPES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    ".tif": "image/tiff", ".tiff": "image/tiff", ".svg": "image/svg+xml",
}
# Vektorformate aus Office: der Browser zeigt sie nicht an, also melden wir
# sie sauber zurueck statt ein kaputtes Bild einzubauen.
UNSUPPORTED_IMAGE_EXT = {".emf", ".wmf", ".emz", ".wmz"}


def _media_type_for(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in UNSUPPORTED_IMAGE_EXT:
        raise RuntimeError("Format {} kann nicht eingebettet werden.".format(ext))
    return IMAGE_TYPES.get(ext, "image/png")


def fetch_image(url, allow_remote=False):
    """Holt ein Bild als (base64, media_type). Nur file:// und http(s)://."""
    url = (url or "").strip()
    if not url:
        raise RuntimeError("Leere Bildadresse.")

    if url.lower().startswith("file:"):
        path = url2pathname(urlparse(url).path)
        if not os.path.isfile(path):
            raise RuntimeError("Datei nicht gefunden: {}".format(os.path.basename(path)))
        if os.path.getsize(path) > MAX_IMAGE_BYTES:
            raise RuntimeError("Bild ist groesser als 25 MB.")
        with open(path, "rb") as f:
            raw = f.read()
        return base64.b64encode(raw).decode("ascii"), _media_type_for(path)

    if url.lower().startswith(("http://", "https://")):
        if not allow_remote:
            raise RuntimeError(
                "Externes Bild nicht geladen - Haekchen 'externe Bilder laden' setzen.")
        req = urllib.request.Request(url, headers={"User-Agent": "MarkdownViewer/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
                raw = resp.read(MAX_IMAGE_BYTES + 1)
                ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
        except urllib.error.HTTPError as e:
            raise RuntimeError("HTTP {} beim Laden des Bildes.".format(e.code))
        except urllib.error.URLError as e:
            raise RuntimeError("Bild nicht erreichbar: {}".format(e.reason))
        if len(raw) > MAX_IMAGE_BYTES:
            raise RuntimeError("Bild ist groesser als 25 MB.")
        if not ctype.startswith("image/"):
            ctype = _media_type_for(urlparse(url).path)
        return base64.b64encode(raw).decode("ascii"), ctype

    # blob:-Adressen gehoeren zum Speicher der Ursprungsseite. Weder der Server
    # noch unsere Seite kommen daran - das laesst sich nicht "reparieren",
    # deshalb hier im Klartext sagen, was zu tun ist.
    if url.lower().startswith("blob:"):
        raise RuntimeError(
            "Bild liegt nur im Zwischenspeicher der Webseite (blob:). "
            "Bitte einzeln kopieren und ueber 'Fehlende Bilder' einsetzen.")

    # Ein relativer Pfad laesst sich nicht aufloesen: beim Einfuegen kommt die
    # Adresse der Ursprungsseite nicht mit.
    if "://" not in url:
        raise RuntimeError(
            "Relative Bildadresse - beim Einfuegen ist die Ursprungsseite nicht bekannt.")
    raise RuntimeError("Nicht unterstuetzte Bildquelle ({}).".format(url.split(":")[0][:20]))


# ── Word-Dokumente einlesen ───────────────────────────────────────────
# .docx ist ein ZIP mit XML - zipfile und xml.etree reichen dafuer, es kommt
# also keine Abhaengigkeit dazu. Zurueck geht Markdown mit Platzhaltern
# {{IMG:n}}; die Bilder liefert der Server getrennt, damit der Client sie
# genauso benennt und ablegt wie Screenshots.

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V_NS = "{urn:schemas-microsoft-com:vml}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

_HEADING_RE = re.compile(r"(?i)^(?:heading|berschrift|title|titel)\s*([1-6])?$")


def _docx_styles(zf):
    """styleId -> Ueberschriftenebene (1-6), aus styles.xml."""
    levels = {}
    try:
        root = ET.fromstring(zf.read("word/styles.xml"))
    except (KeyError, ET.ParseError):
        return levels
    for style in root.iter(W + "style"):
        sid = style.get(W + "styleId") or ""
        name = style.find(W + "name")
        label = (name.get(W + "val") if name is not None else "") or ""
        for candidate in (label, sid):
            m = _HEADING_RE.match(candidate.strip())
            if m:
                levels[sid] = int(m.group(1) or 1)
                break
    return levels


def _docx_numbering(zf):
    """(numId, ilvl) -> True, wenn es eine Aufzaehlung (kein Nummernformat) ist."""
    bullets = {}
    try:
        root = ET.fromstring(zf.read("word/numbering.xml"))
    except (KeyError, ET.ParseError):
        return bullets
    abstract = {}
    for anum in root.iter(W + "abstractNum"):
        aid = anum.get(W + "abstractNumId")
        for lvl in anum.iter(W + "lvl"):
            fmt = lvl.find(W + "numFmt")
            abstract[(aid, lvl.get(W + "ilvl"))] = (
                (fmt.get(W + "val") if fmt is not None else "bullet") == "bullet")
    for num in root.iter(W + "num"):
        nid = num.get(W + "numId")
        ref = num.find(W + "abstractNumId")
        aid = ref.get(W + "val") if ref is not None else None
        for (a, ilvl), is_bullet in abstract.items():
            if a == aid:
                bullets[(nid, ilvl)] = is_bullet
    return bullets


def _docx_rels(zf):
    """rId -> Ziel (Bildpfad im ZIP oder externe URL)."""
    rels = {}
    try:
        root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
    except (KeyError, ET.ParseError):
        return rels
    for rel in root.iter(PKG_REL + "Relationship"):
        target = rel.get("Target") or ""
        if rel.get("TargetMode") != "External":
            target = "word/" + target.lstrip("./").replace("../", "")
        rels[rel.get("Id")] = target
    return rels


def _md_inline(text):
    """Entschaerft Zeichen, die Markdown sonst als Auszeichnung liest."""
    return re.sub(r"([\\`*_\[\]])", r"\\\1", text)


class _DocxReader:
    """Laeuft einmal durch document.xml und baut daraus Markdown."""

    def __init__(self, zf):
        self.zf = zf
        self.rels = _docx_rels(zf)
        self.heading_levels = _docx_styles(zf)
        self.bullets = _docx_numbering(zf)
        self.images = []        # [{data, media_type}] in Reihenfolge des Auftretens
        self.seen = {}          # Zielpfad -> Platzhalternummer, spart Doppelte

    # -- Bilder ------------------------------------------------------
    def _image_placeholder(self, rid):
        target = self.rels.get(rid)
        if not target:
            return ""
        if target in self.seen:
            return "{{IMG:%d}}" % self.seen[target]
        if target.lower().startswith(("http://", "https://")):
            return "![Bild](%s)" % target
        try:
            raw = self.zf.read(target)
            media = _media_type_for(target)
        except KeyError:
            return ""
        except RuntimeError as e:
            return "*[Bild ausgelassen: %s]*" % e
        self.images.append({"data": base64.b64encode(raw).decode("ascii"),
                            "media_type": media})
        idx = len(self.images) - 1
        self.seen[target] = idx
        return "{{IMG:%d}}" % idx

    # -- Textlauf ----------------------------------------------------
    def _run(self, run):
        parts = []
        for node in run.iter():
            tag = node.tag
            if tag == W + "t":
                parts.append(_md_inline(node.text or ""))
            elif tag == W + "tab":
                parts.append(" ")
            elif tag == W + "br":
                parts.append("  \n")
            elif tag == A_NS + "blip":
                parts.append(self._image_placeholder(node.get(R_NS + "embed")))
            elif tag == V_NS + "imagedata":
                parts.append(self._image_placeholder(node.get(R_NS + "id")))
        text = "".join(parts)
        if not text.strip():
            return text

        props = run.find(W + "rPr")
        if props is not None and not text.startswith("{{IMG:"):
            if props.find(W + "b") is not None:
                text = "**" + text + "**"
            if props.find(W + "i") is not None:
                text = "*" + text + "*"
        return text

    def _paragraph_body(self, para):
        parts = []
        for child in para:
            if child.tag == W + "r":
                parts.append(self._run(child))
            elif child.tag == W + "hyperlink":
                inner = "".join(self._run(r) for r in child.findall(W + "r"))
                target = self.rels.get(child.get(R_NS + "id"), "")
                parts.append("[{}]({})".format(inner, target) if target else inner)
        return re.sub(r"[ \t]+", " ", "".join(parts)).strip()

    def _paragraph(self, para):
        body = self._paragraph_body(para)
        if not body:
            return ""
        props = para.find(W + "pPr")
        if props is None:
            return body

        style = props.find(W + "pStyle")
        sid = style.get(W + "val") if style is not None else ""
        level = self.heading_levels.get(sid)
        if level is None and sid:
            m = _HEADING_RE.match(sid)
            if m:
                level = int(m.group(1) or 1)
        if level:
            return "#" * level + " " + body

        numpr = props.find(W + "numPr")
        if numpr is not None:
            ilvl = numpr.find(W + "ilvl")
            numid = numpr.find(W + "numId")
            lvl = int((ilvl.get(W + "val") if ilvl is not None else "0") or 0)
            nid = numid.get(W + "val") if numid is not None else None
            is_bullet = self.bullets.get((nid, str(lvl)), True)
            return "  " * lvl + ("- " if is_bullet else "1. ") + body

        if props.find(W + "ind") is not None and props.find(W + "pBdr") is not None:
            return "> " + body
        return body

    def _table(self, tbl):
        rows = []
        for tr in tbl.findall(W + "tr"):
            cells = []
            for tc in tr.findall(W + "tc"):
                text = " ".join(filter(None, (self._paragraph_body(p)
                                              for p in tc.findall(W + "p"))))
                cells.append(text.replace("|", "\\|"))
            if cells:
                rows.append(cells)
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        out = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
        out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        return "\n".join(out)

    def read(self):
        root = ET.fromstring(self.zf.read("word/document.xml"))
        body = root.find(W + "body")
        if body is None:
            raise RuntimeError("Das Dokument hat keinen lesbaren Inhalt.")

        blocks = []
        for child in body:
            if child.tag == W + "p":
                block = self._paragraph(child)
            elif child.tag == W + "tbl":
                block = self._table(child)
            else:
                continue
            if block:
                blocks.append(block)

        return _join_blocks(blocks), self.images


def import_docx(raw):
    """Liest ein .docx und liefert (markdown_mit_platzhaltern, bilder)."""
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            return _DocxReader(zf).read()
    except zipfile.BadZipFile:
        raise RuntimeError("Die Datei ist kein gueltiges .docx.")
    except KeyError:
        raise RuntimeError("In der Datei fehlt word/document.xml.")
    except ET.ParseError as e:
        raise RuntimeError("Das Dokument liess sich nicht lesen: {}".format(e))


# ── Speichern-Dialog ──────────────────────────────────────────────────
# Tk vertraegt keine Aufrufe aus fremden Threads. Die Server-Threads stellen
# ihre Anfrage deshalb in eine Queue; geoeffnet wird der Dialog im Hauptthread.
_dialog_requests = queue.Queue()


def ask_save_path():
    """Oeffnet den nativen Speichern-Dialog. Nur im Hauptthread aufrufen."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        return filedialog.asksaveasfilename(
            parent=root,
            title="Markdown speichern",
            defaultextension=".md",
            initialfile="screenshots.md",
            filetypes=[("Markdown", "*.md"), ("Alle Dateien", "*.*")],
        )
    finally:
        root.destroy()


def request_save_path(timeout=600):
    """Laesst den Dialog vom Hauptthread oeffnen und wartet auf das Ergebnis."""
    answer = queue.Queue(maxsize=1)
    _dialog_requests.put(answer)
    try:
        result = answer.get(timeout=timeout)
    except queue.Empty:
        raise RuntimeError("Zeitueberschreitung beim Speichern-Dialog.")
    if isinstance(result, BaseException):
        raise RuntimeError("Speichern-Dialog nicht verfuegbar: {}".format(result))
    return result


def pump_dialogs():
    """Bedient im Hauptthread die Dialoganfragen der Server-Threads."""
    while True:
        try:
            answer = _dialog_requests.get(timeout=0.25)
        except queue.Empty:
            continue
        try:
            answer.put(ask_save_path())
        except BaseException as e:
            answer.put(e)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # Konsole ruhig halten

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._send(200, "text/html; charset=utf-8", HTML.encode())

        elif parsed.path == "/api/status":
            # Sagt der Oberflaeche, welche Umwandlungswege hier verfuegbar sind.
            payload = json.dumps({
                "local_ocr": local_ocr_available(),
                "has_key": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
                "model": CLAUDE_MODEL,
            })
            self._send(200, "application/json; charset=utf-8", payload.encode())

        elif parsed.path == "/api/file":
            params = parse_qs(parsed.query)
            path = params.get("path", [None])[0]
            if path:
                path = unquote(path)
            if not path or not os.path.isfile(path):
                self._send(404, "application/json", b'{"error":"not found"}')
                return
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                size = os.path.getsize(path)
                name = os.path.basename(path)
                payload = json.dumps({"content": content, "name": name, "size": size})
                self._send(200, "application/json; charset=utf-8", payload.encode())
            except Exception as e:
                err = json.dumps({"error": str(e)})
                self._send(500, "application/json", err.encode())
        else:
            self._send(404, "text/plain", b"Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"error": "Ungueltige Anfrage: {}".format(e)})
            return

        if parsed.path == "/api/convert":
            self._handle_convert(data)
        elif parsed.path == "/api/fetch-image":
            self._handle_fetch_image(data)
        elif parsed.path == "/api/import":
            self._handle_import(data)
        elif parsed.path == "/api/save":
            self._handle_save(data)
        else:
            self._json(404, {"error": "Unbekannte Route"})

    def _handle_fetch_image(self, data):
        """Holt ein einzelnes Bild, auf das eingefuegtes HTML verweist."""
        try:
            b64, media = fetch_image(data.get("url", ""),
                                     bool(data.get("allow_remote")))
        except RuntimeError as e:
            self._json(502, {"error": str(e)})
            return
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        self._json(200, {"data": b64, "media_type": media})

    def _handle_import(self, data):
        """Liest eine hochgeladene .docx-Datei ein."""
        name = data.get("filename") or ""
        if not name.lower().endswith(".docx"):
            self._json(400, {"error": "Nur .docx-Dateien koennen so gelesen werden."})
            return
        try:
            raw = base64.b64decode(data.get("data", ""))
            markdown, images = import_docx(raw)
        except RuntimeError as e:
            self._json(502, {"error": str(e)})
            return
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        self._json(200, {"markdown": markdown, "images": images})

    def _handle_convert(self, data):
        """Wandelt genau einen Screenshot oder einen Textabschnitt um.

        Der Client ruft die Route pro Bild bzw. pro Abschnitt einmal auf. Das gibt
        Fortschritt, erlaubt Abbrechen und haelt Teilergebnisse fest, wenn eines
        scheitert.
        """
        image = data.get("image") or {}
        text = data.get("text")
        local = data.get("engine") != "api"    # ohne Angabe: lokal, ohne Schluessel
        try:
            if text is not None:
                markdown, truncated = (format_text_markdown(text) if local
                                       else transcribe_text(text))
            else:
                b64 = image.get("data", "")
                media = image.get("media_type", "image/png")
                markdown, truncated = (ocr_image_local(b64, media) if local
                                       else transcribe_image(b64, media))
        except RuntimeError as e:
            self._json(502, {"error": str(e)})
            return
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        self._json(200, {"markdown": markdown, "truncated": truncated})

    def _handle_save(self, data):
        """Schreibt die .md-Datei und legt die Screenshots im Unterordner ab."""
        markdown = data.get("markdown", "")
        images = data.get("images") or []
        try:
            target = request_save_path()
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        if not target:
            self._json(200, {"cancelled": True})
            return
        try:
            md_path = Path(target)
            img_dir = md_path.parent / IMAGE_DIR_NAME
            if images:
                img_dir.mkdir(parents=True, exist_ok=True)
            for img in images:
                # basename() verhindert, dass ".." aus dem Bildordner ausbricht.
                name = os.path.basename(img.get("filename") or "screenshot.png")
                (img_dir / name).write_bytes(base64.b64decode(img.get("data", "")))
            with io.open(md_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(markdown)
        except Exception as e:
            self._json(500, {"error": str(e)})
            return
        self._json(200, {
            "path": str(md_path),
            "images_dir": str(img_dir) if images else "",
        })

    def _json(self, code, obj):
        self._send(code, "application/json; charset=utf-8",
                   json.dumps(obj).encode("utf-8"))

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(body)


class ThreadingServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Mehrere Threads, damit eine laufende Umwandlung die Oberflaeche nicht blockiert."""
    daemon_threads = True
    allow_reuse_address = True


def start_server():
    server = ThreadingServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def main():
    print(f"Markdown Viewer startet auf http://127.0.0.1:{PORT}")
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        print("Hinweis: ANTHROPIC_API_KEY ist nicht gesetzt - "
              "'Screenshots/Text -> Markdown' laeuft lokal ueber die Windows-Texterkennung.")
    if not local_ocr_available():
        print("Hinweis: Lokale Windows-Texterkennung nicht verfuegbar "
              "(nur Windows mit PowerShell 5.1).")
    start_server()
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    print("Browser geöffnet. Strg+C zum Beenden.")
    try:
        pump_dialogs()   # haelt den Hauptthread und bedient Tk-Dialoge
    except KeyboardInterrupt:
        print("\nServer beendet.")


if __name__ == "__main__":
    main()
