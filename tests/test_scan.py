import importlib.util
import io
import threading
import time
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from mailia import scan
from PIL import Image

spec = importlib.util.spec_from_file_location("mock_escl", Path(__file__).resolve().parent.parent / "tools" / "mock_escl.py")
mock = importlib.util.module_from_spec(spec); spec.loader.exec_module(mock)


@pytest.fixture(scope="module")
def scanner():
    srv = HTTPServer(("127.0.0.1", 0), mock.H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/eSCL"
    srv.shutdown()


def test_escl_capabilities_and_scans(scanner):
    caps = scan.capabilities(scanner)
    assert caps["platen"] and caps["feeder"] and caps["duplex"] and 300 in caps["dpis"]
    assert len(scan.scan(scanner, "platen", "color", 200, caps)) == 1
    assert len(scan.scan(scanner, "duplex", "gray", 300, caps)) == 3
    assert len(scan.scan(scanner)) == 3       # « auto » : le faux scanner annonce un chargeur garni


def test_only_local_addresses(scanner):
    with pytest.raises(scan.ScanError, match="réseau local"):
        scan._http("GET", "http://8.8.8.8/x")
    assert scan.normalize_base("192.168.1.20") == "http://192.168.1.20/eSCL"


def test_pdf_modes_and_size_control(scanner):
    pages = scan.scan(scanner, "feeder")
    sizes = {m: len(scan.build_pdf(pages, m, 200)) for m in ("color", "gray", "bw")}
    assert sizes["bw"] < sizes["gray"] < sizes["color"]
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(scan.build_pdf(pages, "color", 200)))
    assert len(r.pages) == 3 and abs(float(r.pages[0].mediabox.width) - 595) < 3   # A4
    with pytest.raises(scan.ScanError):
        scan.build_pdf([b"pas une image"])


def test_blank_page_detection():
    blank = io.BytesIO(); Image.new("RGB", (400, 500), (252, 252, 252)).save(blank, "JPEG")
    assert scan.is_blank(blank.getvalue())
    assert not scan.is_blank(mock.page(1))


def test_phone_session_security_and_upload():
    srv = scan.PhoneServer()
    try:
        url = srv.url("127.0.0.1")
        assert "Numériser".encode() in urllib.request.urlopen(url).read()
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(url.replace(srv.session.token, "zzzzzzzzzzzz"))
        assert e.value.code == 404
        req = urllib.request.Request(url + "/page", data=mock.page(1), method="POST", headers={"X-Corners": "0.1,0.1,0.9,0.1,0.9,0.9,0.1,0.9"})
        assert b'"n": 1' in urllib.request.urlopen(req).read()
        urllib.request.urlopen(urllib.request.Request(url + "/done", data=b"", method="POST"))
        assert srv.session.done and len(srv.session.pages) == 1
        bad = urllib.request.Request(url + "/page", data=b"pas une image", method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(bad)
        assert e.value.code == 400
    finally:
        srv.stop()
    time.sleep(0.2)
    with pytest.raises(Exception):
        urllib.request.urlopen(url, timeout=2)   # le serveur est bien fermé


def test_warp_straightens():
    w = scan.warp(mock.page(1), [0.1, 0.1, 0.9, 0.1, 0.9, 0.9, 0.1, 0.9])
    assert Image.open(io.BytesIO(w)).size[0] > 100


def test_scanners_are_remembered_between_openings():
    from mailia import scan
    assert scan.known() == []
    scan.remember("Brother MFC", "http://192.168.1.20:80/eSCL")
    scan.remember("HP Laser", "http://192.168.1.30:80/eSCL")
    assert [s["name"] for s in scan.known()] == ["Brother MFC", "HP Laser"]
    scan.remember("Brother MFC", "http://192.168.1.55:80/eSCL")  # même scanner, nouvelle adresse (routeur) : mis à jour, pas dupliqué
    assert [(s["name"], s["base"]) for s in scan.known()] == [("Brother MFC", "http://192.168.1.55:80/eSCL"), ("HP Laser", "http://192.168.1.30:80/eSCL")]
    scan.remember("HP Laser", "http://192.168.1.30:80/eSCL", used=True)  # le dernier utilisé passe en tête
    assert scan.known()[0]["name"] == "HP Laser"
    scan.forget("http://192.168.1.30:80/eSCL")
    assert [s["name"] for s in scan.known()] == ["Brother MFC"]
