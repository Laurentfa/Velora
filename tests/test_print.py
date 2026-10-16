import os
import subprocess
import sys

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
    monkeypatch.setattr(ui_common, "native_print", lambda path, timeout=4.0: False)  # la boîte d'impression native est testée à part
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


def responder(answer: str, delay: float = 0.05):
    """Joue le rôle de la fenêtre native : lit « print_request » et répond dans « print_status »."""
    import json
    import threading
    import time
    from mailia import db

    def run():
        time.sleep(delay)
        with db.conn() as c:
            rid = json.loads(c.execute("SELECT v FROM kv WHERE k='print_request'").fetchone()["v"])["id"]
        db.kv_set("print_status", f"{rid}:{answer}")
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_native_print_dialog_is_used_for_pdf_and_images_on_mac(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    t = responder("ok")
    assert ui_common.native_print(pdf, timeout=3) is True
    t.join()
    t = responder("échec RuntimeError PDF illisible")
    assert ui_common.native_print(tmp_path / "photo.JPG", timeout=3) is False  # échec côté fenêtre : on ouvrira le fichier comme avant
    t.join()


def test_native_print_gives_up_quickly_without_answer_and_ignores_office_files(monkeypatch, tmp_path):
    import time
    from mailia import db
    monkeypatch.setattr(sys, "platform", "darwin")
    t0 = time.time()
    assert ui_common.native_print(tmp_path / "a.pdf", timeout=0.4) is False and time.time() - t0 < 2  # fenêtre absente : repli
    db.kv_set("print_request", "")
    assert ui_common.native_print(tmp_path / "devis.docx") is False and db.kv_get("print_request") == ""  # Word reste ouvert dans Word : aucune demande
    monkeypatch.setattr(sys, "platform", "win32")
    assert ui_common.native_print(tmp_path / "a.pdf") is False  # Windows : inchangé


def test_open_to_print_skips_the_viewer_when_the_native_dialog_opens(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **k: opened.append(cmd))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(ui_common, "native_print", lambda path, timeout=4.0: True)
    p = ui_common.open_to_print("Facture.pdf", b"%PDF-1.4")
    assert p.read_bytes() == b"%PDF-1.4" and not opened  # pas d'Aperçu


def test_window_process_runs_the_print_request_and_ignores_an_old_one(monkeypatch):
    import json
    import threading
    import time
    from mailia import db, native_menu
    printed = []
    monkeypatch.setattr(native_menu, "print_native", lambda path: printed.append(path))
    db.kv_set("print_request", json.dumps({"id": "ancien", "path": "/tmp/ancien.pdf"}))  # resté d'un lancement précédent
    stop = threading.Event()
    t = threading.Thread(target=native_menu.watch_print, args=(stop, 0.02, lambda fn: fn()), daemon=True)  # sans PyObjC : le test tourne aussi sous Windows
    t.start()
    time.sleep(0.2)
    assert printed == []  # rejouer une vieille demande ouvrirait une boîte d'impression inattendue
    db.kv_set("print_request", json.dumps({"id": "neuf", "path": "/tmp/neuf.pdf"}))
    end = time.time() + 3
    while not printed and time.time() < end:
        time.sleep(0.02)
    stop.set()
    t.join(2)
    assert printed == ["/tmp/neuf.pdf"] and db.kv_get("print_status") == "neuf:ok"
