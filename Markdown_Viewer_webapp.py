"""
Markdown Viewer – lokale Webapp
Startet einen HTTP-Server und öffnet den Browser automatisch.
Keine externen Abhängigkeiten nötig.
"""

import http.server
import json
import os
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

PORT = 8742
BASE_DIR = Path(__file__).parent

HTML = r"""<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
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
  </style>
</head>
<body>

<div class="toolbar">
  <span class="toolbar-title">MD</span>
  <button class="btn primary" onclick="triggerFileInput()">Datei öffnen</button>
  <input id="fileInput" type="file" accept=".md,.markdown,.txt,.ipynb" style="display:none" onchange="loadLocalFile(this)">
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
      <p style="font-size:11px;margin-top:8px;">Unterstützt: .md · .markdown · .txt · .ipynb</p>
    </div>
    <div class="markdown-body" id="output" style="display:none"></div>
  </div>
</div>

<div class="status" id="status">Bereit</div>

<script>
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
  const file = e.dataTransfer.files[0];
  if (file) readFile(file);
});

function triggerFileInput() {
  document.getElementById('fileInput').click();
}

function loadLocalFile(input) {
  const file = input.files[0];
  if (file) readFile(file);
}

function readFile(file) {
  const reader = new FileReader();
  reader.onload = e => dispatchRender(e.target.result, file.name, file.size);
  reader.readAsText(file, 'UTF-8');
}

function dispatchRender(content, name, size) {
  if (name && name.toLowerCase().endsWith('.ipynb')) {
    renderNotebook(content, name, size);
  } else {
    renderMarkdown(content, name, size);
  }
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
});
</script>
</body>
</html>
"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Konsole ruhig halten

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/" or parsed.path == "/index.html":
            self._send(200, "text/html; charset=utf-8", HTML.encode())

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

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)


def start_server():
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def main():
    print(f"Markdown Viewer startet auf http://127.0.0.1:{PORT}")
    start_server()
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    print("Browser geöffnet. Strg+C zum Beenden.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("\nServer beendet.")


if __name__ == "__main__":
    main()
