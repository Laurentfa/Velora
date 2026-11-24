"""Lecture de texte sur une image : choix du moteur, appel du programme, erreurs (faux sous-processus, aucun vrai moteur)."""
import subprocess
from pathlib import Path

import pytest

from mailia import modules, ocr


class Done:
    def __init__(self, code=0, out=b"", err=b""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_engine_by_platform(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    assert ocr.engine() == "" and not ocr.available()
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    assert ocr.engine() == "windows" and ocr.available()
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    monkeypatch.setattr(ocr, "helper_path", lambda: None)
    assert ocr.engine() == ""
    monkeypatch.setattr(ocr, "helper_path", lambda: tmp_path / "velora-ocr")
    assert ocr.engine() == "vision"


def test_read_text_mac_calls_the_helper_and_cleans_up(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ocr.sys, "platform", "darwin")
    helper = tmp_path / "velora-ocr"
    monkeypatch.setattr(ocr, "helper_path", lambda: helper)
    seen = {}

    def fake(cmd, timeout):
        seen["cmd"] = cmd
        seen["existed"] = Path(cmd[1]).is_file()
        return Done(0, "Nom : MARTIN\n\n  Prénom : Léa  \n".encode())
    monkeypatch.setattr(ocr, "run", fake)
    assert ocr.read_text(b"\xff\xd8\xffx") == "Nom : MARTIN\nPrénom : Léa"
    assert seen["cmd"][0] == str(helper) and seen["existed"] and not Path(seen["cmd"][1]).exists()  # fichier temporaire effacé


def test_read_text_windows_uses_powershell_script(monkeypatch) -> None:
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    seen = {}

    def fake(cmd, timeout):
        seen["cmd"] = cmd
        seen["script_exists"] = Path(cmd[cmd.index("-File") + 1]).is_file()
        return Done(0, "Bonjour\r\nmonde\r\n".encode())
    monkeypatch.setattr(ocr, "run", fake)
    assert ocr.read_text(b"\xff\xd8\xffx") == "Bonjour\nmonde"
    assert seen["cmd"][0] == "powershell" and "-NoProfile" in seen["cmd"] and seen["script_exists"] and not Path(seen["cmd"][seen["cmd"].index("-File") + 1]).exists()
    assert "Windows.Media.Ocr.OcrEngine" in ocr.WINDOWS_SCRIPT and "exit 3" in ocr.WINDOWS_SCRIPT


def test_read_text_errors_are_clear(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ocr.sys, "platform", "linux")
    with pytest.raises(ocr.OcrUnavailable, match="pas disponible"):
        ocr.read_text(b"x")
    monkeypatch.setattr(ocr.sys, "platform", "win32")
    for code, expected in ((3, "langue"), (2, "illisible"), (1, "échoué")):
        monkeypatch.setattr(ocr, "run", lambda cmd, timeout, c=code: Done(c))
        with pytest.raises(ocr.OcrUnavailable, match=expected):
            ocr.read_text(b"\xff\xd8\xffx")

    def slow(cmd, timeout):
        raise subprocess.TimeoutExpired(cmd, timeout)
    monkeypatch.setattr(ocr, "run", slow)
    with pytest.raises(ocr.OcrUnavailable, match="trop de temps"):
        ocr.read_text(b"\xff\xd8\xffx")

    def nostart(cmd, timeout):
        raise FileNotFoundError("powershell")
    monkeypatch.setattr(ocr, "run", nostart)
    with pytest.raises(ocr.OcrUnavailable, match="ne démarre pas"):
        ocr.read_text(b"\xff\xd8\xffx")
    with pytest.raises(ocr.OcrUnavailable, match="absente"):
        ocr.read_text(b"")


def test_module_api_ocr(monkeypatch) -> None:
    api = modules.ModuleAPI({"id": "alpha", "dir": Path("."), "name": "Alpha"})
    monkeypatch.setattr(ocr, "available", lambda: True)
    assert api.ocr_available() is True
    monkeypatch.setattr(ocr, "read_text", lambda image, suffix=".jpg": "texte")
    assert api.ocr_text(b"\xff\xd8\xffx") == "texte"

    def boom(image, suffix=".jpg"):
        raise ocr.OcrUnavailable("pas ici")
    monkeypatch.setattr(ocr, "read_text", boom)
    with pytest.raises(ValueError, match="pas ici"):
        api.ocr_text(b"x")
