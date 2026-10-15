import os
import subprocess

import pytest
from mailia import ui_common


def test_only_safe_types_are_printable():
    for ok in ("a.pdf", "Photo.JPG", "devis.docx", "t.xlsx", "n.txt", "x.heic"):
        assert ui_common.can_print(ok), ok
    for bad in ("m.docm", "v.xlsm", "s.svg", "a.zip", "mail.eml", "event.ics", "run.sh", "x.exe", "noext", "", "a.pdf.exe"):
        assert not ui_common.can_print(bad), bad


def test_open_to_print_writes_file_and_opens_it(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **k: opened.append(cmd))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(os, "startfile", lambda f: opened.append(["startfile", f]), raising=False)  # Windows ouvre ainsi le fichier
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    p = ui_common.open_to_print("../../évil/Facture 1.pdf", b"%PDF-1.4")
    assert p.name == "Facture 1.pdf" and p.read_bytes() == b"%PDF-1.4" and p.parent.parent == tmp_path
    assert opened and opened[0][-1] == str(p) and opened[0][0] in ("open", "xdg-open", "startfile")
    with pytest.raises(ValueError):
        ui_common.open_to_print("macro.docm", b"x")


def test_old_print_folders_are_purged(monkeypatch, tmp_path):
    import os
    import time
    old = tmp_path / (ui_common.PRINT_DIR_PREFIX + "old"); old.mkdir()
    (old / "f.pdf").write_bytes(b"x")
    os.utime(old, (time.time() - 3 * 86400,) * 2)
    fresh = tmp_path / (ui_common.PRINT_DIR_PREFIX + "new"); fresh.mkdir()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    ui_common.open_to_print("a.pdf", b"x")
    assert not old.exists() and fresh.exists()
