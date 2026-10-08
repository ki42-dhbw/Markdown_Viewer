# Markdown Viewer

Ein lokaler Viewer für Markdown-Dateien und Jupyter-Notebooks unter Windows – als
einzelne `.exe` oder direkt mit Python. Zusätzlich wandelt er Screenshots, kopierten
Text, Inhalte aus der Zwischenablage und Word-Dateien in Markdown um.

Die Oberfläche ist deutsch. Das Programm braucht außer Python selbst **keine
zusätzlichen Pakete**.

## Funktionen

**Anzeigen**

- Markdown mit Tabellen, Codeblöcken und Formeln (MathJax)
- Jupyter-Notebooks (`.ipynb`) samt Ausgaben – erkannt am Inhalt, nicht nur an der Endung
- Inhaltsverzeichnis, Suche im Dokument
- Export als PDF über den Druckdialog des Browsers
- Öffnen per Dateiauswahl, Drag & Drop oder Einfügen aus der Zwischenablage

**Umwandeln in Markdown** (Schaltfläche „Screenshots/Text“)

- Screenshots → Text per OCR; hohe Bildschirmfotos werden gekachelt statt verkleinert
- Kopierter Text → Struktur (Überschriften, Listen, Tabellen) wird wiederhergestellt
- Kopierte Inhalte aus Browser, Word oder Outlook → Formatierung und Bilder bleiben erhalten
- `.docx`- und `.html`-Dateien
- Ergebnis im Editor nachbearbeiten und als `.md` speichern; Bilder landen im Unterordner `bilder/`

## Voraussetzungen

- Windows 10 oder 11
- Python 3 (nur Standardbibliothek, inklusive `tkinter`) – entfällt bei Nutzung der `.exe`
- Ein aktueller Browser

## Starten

Am einfachsten: `MarkdownViewer.exe` von der
[Release-Seite](https://github.com/ki42-dhbw/Markdown_Viewer/releases/latest)
herunterladen und starten – Python ist dafür nicht nötig. Die Datei ist nicht
signiert, Windows SmartScreen fragt beim ersten Start deshalb nach.

Mit Python:

```powershell
python Markdown_Viewer_webapp_01.py
```

oder per Doppelklick auf `start.bat`. Das Programm startet einen kleinen Server auf
`http://127.0.0.1:8742` und öffnet die Seite im Browser. Beenden mit `Strg+C`.

Zum Ausprobieren liegen in `Testdaten/` ein Beispieldokument und ein Beispiel-Notebook.

## Umwandlung: zwei Engines

| Engine | Voraussetzung | Netzwerk | Eignung |
|--------|---------------|----------|---------|
| **Lokal** (Standard) | Windows-OCR, in Windows enthalten | keines | Fließtext, einfache Struktur |
| **Claude-API** (optional) | eigener Anthropic-API-Schlüssel | ja | Tabellen und komplexe Layouts |

Die lokale Engine nutzt die in Windows eingebaute Texterkennung. Erkannt werden die
Sprachen, für die in den Windows-Einstellungen ein Sprachpaket mit Texterkennung
installiert ist.

Für die Claude-API-Engine den Schlüssel vor dem Start als Umgebungsvariable setzen:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
python Markdown_Viewer_webapp_01.py
```

Der Schlüssel wird nur aus der Umgebung gelesen und nirgends gespeichert. Die Nutzung
der API ist kostenpflichtig; das verwendete Modell steht in `CLAUDE_MODEL` am Anfang
von `Markdown_Viewer_webapp_01.py`.

## Datenschutz und Netzwerk

Der Server lauscht ausschließlich auf `127.0.0.1` und ist von anderen Rechnern aus
nicht erreichbar. Daten verlassen den Rechner nur in diesen Fällen:

- **Darstellungsbibliotheken:** `marked.js` und `MathJax` lädt der Browser vom CDN
  jsDelivr. Ohne Internetverbindung fehlen Markdown-Darstellung und Formelsatz, sofern
  der Browser die Dateien nicht bereits zwischengespeichert hat.
- **Claude-API-Engine:** Bilder und Texte, die damit umgewandelt werden, gehen an
  Anthropic – nur, wenn diese Engine ausdrücklich gewählt ist.
- **Verlinkte Bilder:** Bilder, auf die eingefügter Inhalt nur verweist, werden erst
  nach Ankreuzen von „externe Bilder laden“ oder nach Rückfrage beim Speichern geladen.

## Tastenkürzel

| Kürzel | Wirkung |
|--------|---------|
| `Strg+V` | Screenshot, Text oder kopierten Inhalt einfügen |
| `Strg+Enter` | Text aus dem Eingabefeld übernehmen |
| `Strg+P` | Als PDF speichern / drucken |
| `Strg+Umschalt+L` | Ansicht leeren |

## Als .exe bauen

Benötigt [PyInstaller](https://pyinstaller.org/) (`pip install pyinstaller`):

```powershell
python -m PyInstaller MarkdownViewer.spec
```

Das Ergebnis liegt in `dist/MarkdownViewer.exe`. Vor dem Bauen laufende Instanzen des
Viewers beenden – sie sperren die Datei und belegen den Port.

## Dateien

| Datei | Zweck |
|-------|-------|
| `Markdown_Viewer_webapp_01.py` | Aktuelle Version; daraus entsteht die `.exe` |
| `Markdown_Viewer_webapp.py` | Ältere Webapp-Variante ohne Umwandlung und PDF-Export |
| `Markdown_Viewer_v01.py` | Frühere, eigenständige Tkinter-Version ohne Browser |
| `MarkdownViewer.spec` | PyInstaller-Konfiguration |
| `start.bat` | Startet die Webapp mit dem vorhandenen Python |
| `CLAUDE.md` | Ausführliche Architekturnotizen (englisch) |

Es gibt keine automatisierten Tests. Die gesamte Oberfläche – HTML, CSS und
JavaScript – steckt als eine Zeichenkette in der Python-Datei.

## Lizenz

[MIT-Lizenz](LICENSE) – der Code darf frei verwendet, verändert und weitergegeben
werden, auch kommerziell. Einzige Bedingung: Der Lizenztext bleibt erhalten.

## Verwendete Bibliotheken

Zur Laufzeit vom CDN geladen, nicht im Repository enthalten:

- [marked](https://github.com/markedjs/marked) – MIT-Lizenz
- [MathJax](https://github.com/mathjax/MathJax) – Apache-Lizenz 2.0
