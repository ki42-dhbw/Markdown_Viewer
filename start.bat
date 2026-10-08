@echo off
REM Startet den Markdown Viewer (lokale Webapp) und oeffnet den Browser.
REM Nutzt den py-Launcher, faellt sonst auf python bzw. C:\Python313 zurueck.

setlocal
cd /d "%~dp0"

REM "Screenshots/Text -> Markdown" laeuft ohne Schluessel ueber die lokale Windows-OCR.
REM Nur fuer die optionale Claude-API-Engine den Schluessel als Umgebungsvariable
REM ANTHROPIC_API_KEY setzen - nicht hier eintragen, die Datei liegt im Repository.

set "SCRIPT=Markdown_Viewer_webapp_01.py"

where py >nul 2>nul && (
    py "%SCRIPT%"
    goto :end
)

where python >nul 2>nul && (
    python "%SCRIPT%"
    goto :end
)

if exist "C:\Python313\python.exe" (
    "C:\Python313\python.exe" "%SCRIPT%"
    goto :end
)

echo Python wurde nicht gefunden. Bitte Python 3 installieren oder zum PATH hinzufuegen.
pause

:end
endlocal
