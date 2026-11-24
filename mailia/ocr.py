"""Lecture de texte sur une image (photo d'un document), sans IA et sans rien envoyer : tout se passe sur l'ordinateur.

- Mac : le framework Vision d'Apple, par un petit programme natif (tools/ocr, « velora-ocr », construit avec l'application) ;
- Windows : le moteur de lecture de texte de Windows 10 et 11 (Windows.Media.Ocr), par PowerShell (aucun composant à installer, hors la langue française de Windows) ;
- ailleurs : indisponible (`read_text` lève OcrUnavailable ; l'appelant se rabat sur autre chose).
`run` est remplaçable : les tests y mettent un faux sous-processus."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HELPER = "velora-ocr"
TIMEOUT = 90  # secondes : la première lecture d'un Mac charge les modèles de reconnaissance


class OcrUnavailable(Exception):
    """La lecture de texte n'est pas possible ici ou a échoué : le message est clair et montrable."""


# Windows : lecture par le moteur intégré. Le code retour 3 veut dire « aucune langue de reconnaissance installée ».
WINDOWS_SCRIPT = r"""
param([string]$Path)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $m = $asTask.MakeGenericMethod($t); $task = $m.Invoke($null, @($op)); $null = $task.Wait(-1); $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Path)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = $null
foreach ($tag in @('fr-FR', 'fr', 'en-US')) {
  $lang = New-Object Windows.Globalization.Language $tag
  if ([Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($lang)) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang); if ($engine) { break } }
}
if (-not $engine) { $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages() }
if (-not $engine) { [Console]::Error.WriteLine('aucune langue de reconnaissance'); exit 3 }
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Lines | ForEach-Object { $_.Text }
"""


def run(cmd: list, timeout: float):
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)


def helper_path() -> Path | None:
    """Le programme natif du Mac : à côté de l'exécutable dans l'appli Velora, sinon dans tools/bin (lancement depuis les sources)."""
    here = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent / "tools" / "bin"
    p = here / HELPER
    return p if p.is_file() else None


def engine() -> str:
    """« vision » (Mac), « windows » (Windows) ou « » (rien ici)."""
    if sys.platform == "darwin":
        return "vision" if helper_path() else ""
    if sys.platform == "win32":
        return "windows"
    return ""


def available() -> bool:
    return bool(engine())


def read_text(image: bytes, suffix: str = ".jpg") -> str:
    """Le texte lu sur l'image (une ligne par ligne de texte), ou "" s'il n'y en a pas. Lève OcrUnavailable (message clair) si la lecture est impossible."""
    eng = engine()
    if not eng:
        raise OcrUnavailable("La lecture de texte n'est pas disponible sur cet ordinateur.")
    if not image or len(image) > 16 * 1024 * 1024:
        raise OcrUnavailable("Image absente ou trop lourde.")
    fd, name = tempfile.mkstemp(suffix=suffix if suffix in (".jpg", ".jpeg", ".png") else ".jpg", prefix="velora-ocr-")
    script = None
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(image)
        if eng == "vision":
            cmd = [str(helper_path()), name]
        else:
            script = name + ".ps1"
            Path(script).write_text("﻿" + WINDOWS_SCRIPT, encoding="utf-8")  # avec BOM : Windows PowerShell 5 lit alors le fichier en UTF-8
            cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, name]
        try:
            r = run(cmd, TIMEOUT)
        except subprocess.TimeoutExpired:
            raise OcrUnavailable("La lecture du texte a pris trop de temps.") from None
        except OSError:
            raise OcrUnavailable("Le programme de lecture de texte ne démarre pas.") from None
        if r.returncode == 3:
            raise OcrUnavailable("Aucune langue de reconnaissance de texte n'est installée (Windows : Paramètres › Heure et langue › Langue et région, ajoutez le français)." if eng == "windows" else "La lecture du texte a échoué.")
        if r.returncode != 0:
            raise OcrUnavailable("Image illisible." if r.returncode == 2 else "La lecture du texte a échoué.")
        text = (r.stdout or b"").decode("utf-8", errors="replace")
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())
    finally:
        for p in (name, script):
            if p:
                try:
                    os.unlink(p)
                except OSError:
                    pass
