"""Mise à jour automatique : comparaison de versions, vérification de signature, choix du fichier, échange de l'application."""
import base64
import hashlib
import io
import json
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mailia import db, updater

KEY = Ed25519PrivateKey.generate()
PUB = base64.b64encode(KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()


def sig(data: bytes, key=KEY) -> str:
    return base64.b64encode(key.sign(hashlib.sha256(data).hexdigest().encode())).decode()  # ancien format : empreinte seule


def sig2(data: bytes, version: str = "1.1.0", name: str = "Velora-1.1.0-mac.zip", key=KEY) -> str:
    from mailia.version import release_message
    return base64.b64encode(key.sign(release_message(version, name, hashlib.sha256(data).hexdigest()))).decode()  # lié à la version et au nom du fichier


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_PUBKEY", PUB)
    monkeypatch.setattr(updater, "UPDATE_REPO", "moi/velora")
    monkeypatch.setattr(updater, "APP_VERSION", "1.0.0")


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def fake_net(monkeypatch, files: dict):
    monkeypatch.setattr(updater, "_get", lambda url, timeout=15, octet=False: Resp(files[url]))


def test_versions():
    assert updater.is_newer("v1.2.0", "1.1.9") and updater.is_newer("2.0.0", "1.9.9") and updater.is_newer("1.0.10", "1.0.9")
    assert not updater.is_newer("v1.0.0", "1.0.0") and not updater.is_newer("0.9.0", "1.0.0") and not updater.is_newer("", "1.0.0")


def test_signature(configured):
    assert updater.verify(hashlib.sha256(b"x").hexdigest(), sig(b"x"))
    assert not updater.verify(hashlib.sha256(b"y").hexdigest(), sig(b"x"))  # fichier modifié
    assert not updater.verify(hashlib.sha256(b"x").hexdigest(), sig(b"x", Ed25519PrivateKey.generate()))  # signé par un inconnu
    assert not updater.verify("abc", "pas-du-base64!")


def test_disabled_without_repo(monkeypatch):
    monkeypatch.setattr(updater, "UPDATE_REPO", "")
    assert not updater.available() and updater.check(True) is None


def release(tag="v1.1.0", **kw):
    return json.dumps({"tag_name": tag, "body": "Nouveautés", "assets": [
        {"name": "Velora-1.1.0-mac.zip", "browser_download_url": "u/mac"}, {"name": "Velora-1.1.0-mac.zip.sig", "browser_download_url": "u/mac.sig"}, {"name": "Velora-1.1.0-mac.zip.sig2", "browser_download_url": "u/mac.sig2"},
        {"name": "Velora-1.1.0-setup.exe", "browser_download_url": "u/win"}, {"name": "Velora-1.1.0-setup.exe.sig", "browser_download_url": "u/win.sig"}, {"name": "Velora-1.1.0-setup.exe.sig2", "browser_download_url": "u/win.sig2"}], **kw}).encode()


@pytest.mark.parametrize("platform,name", [("darwin", "Velora-1.1.0-mac.zip"), ("win32", "Velora-1.1.0-setup.exe")])
def test_fetch_picks_the_right_file(configured, monkeypatch, platform, name):
    monkeypatch.setattr(sys, "platform", platform)
    fake_net(monkeypatch, {"https://api.github.com/repos/moi/velora/releases/latest": release()})
    info = updater.fetch_latest()
    assert info["version"] == "1.1.0" and info["name"] == name and info["notes"] == "Nouveautés"


def test_fetch_ignores_same_draft_and_unsigned(configured, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    url = "https://api.github.com/repos/moi/velora/releases/latest"
    fake_net(monkeypatch, {url: release("v1.0.0")})
    assert updater.fetch_latest() is None
    fake_net(monkeypatch, {url: release(draft=True)})
    assert updater.fetch_latest() is None
    unsigned = json.dumps({"tag_name": "v1.1.0", "assets": [{"name": "Velora-1.1.0-mac.zip", "browser_download_url": "u"}]}).encode()
    fake_net(monkeypatch, {url: unsigned})
    assert updater.fetch_latest() is None  # pas de signature publiée : on n'y touche pas


def test_check_once_a_day_and_off_switch(configured, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(updater, "fetch_latest", lambda: calls.append(1) or {"version": "1.1.0"})
    assert updater.check() and updater.check() is None and len(calls) == 1  # une seule recherche par jour
    assert updater.check(force=True) and len(calls) == 2
    db.kv_set("update_auto", "0")
    db.kv_set("update_checked", "0")
    assert updater.check() is None and len(calls) == 2


def test_check_survives_network_failure(configured, monkeypatch):
    def boom():
        raise OSError("pas de réseau")
    monkeypatch.setattr(updater, "fetch_latest", boom)
    assert updater.check(True) is None


def test_download_verifies_signature(configured, monkeypatch, tmp_path):
    data = b"contenu de l'archive"
    info = {"version": "1.1.0", "name": "Velora-1.1.0-mac.zip", "url": "u/mac", "sig_url": "u/mac.sig2"}
    fake_net(monkeypatch, {"u/mac": data, "u/mac.sig2": sig2(data).encode()})
    assert updater.download(info, tmp_path).read_bytes() == data
    fake_net(monkeypatch, {"u/mac": data + b"!", "u/mac.sig2": sig2(data).encode()})  # archive modifiée en route
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    assert not (tmp_path / info["name"]).exists()  # et rien ne reste sur le disque


def test_a_signature_is_only_valid_for_its_own_version_and_file(configured, monkeypatch, tmp_path):
    """Retour en arrière : quelqu'un qui contrôle la page des versions ne peut pas faire passer une ancienne version signée pour une nouvelle."""
    old = b"ancienne version, peut-etre vulnerable"
    info = {"version": "1.1.0", "name": "Velora-1.1.0-mac.zip", "url": "u/mac", "sig_url": "u/mac.sig2"}
    # 1) signature légitime de la 1.0.50, présentée sous l'étiquette 1.1.0
    fake_net(monkeypatch, {"u/mac": old, "u/mac.sig2": sig2(old, "1.0.50", "Velora-1.0.50-mac.zip").encode()})
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    # 2) ancien format (empreinte seule) : plus accepté
    fake_net(monkeypatch, {"u/mac": old, "u/mac.sig2": sig(old).encode()})
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    # 3) bonne version mais autre nom de fichier (une signature d'un fichier ne sert pas pour un autre)
    fake_net(monkeypatch, {"u/mac": old, "u/mac.sig2": sig2(old, "1.1.0", "Velora-1.1.0-setup.exe").encode()})
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    # 4) une version qui n'est pas plus récente que celle installée est refusée même si la signature est valide
    monkeypatch.setattr(updater, "APP_VERSION", "1.1.0")
    fake_net(monkeypatch, {"u/mac": old, "u/mac.sig2": sig2(old).encode()})
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    assert not list(tmp_path.glob("*.zip"))


def test_a_release_without_the_version_bound_signature_is_not_offered(configured, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    legacy = json.dumps({"tag_name": "v1.1.0", "assets": [{"name": "Velora-1.1.0-mac.zip", "browser_download_url": "u/mac"},
                                                         {"name": "Velora-1.1.0-mac.zip.sig", "browser_download_url": "u/mac.sig"}]}).encode()
    fake_net(monkeypatch, {"https://api.github.com/repos/moi/velora/releases/latest": legacy})
    assert updater.fetch_latest() is None  # seulement l'ancienne signature : une ancienne version signée pourrait être servie à la place


def test_the_signing_tool_writes_both_signatures_and_the_app_accepts_the_new_one(configured, monkeypatch, tmp_path):
    import importlib.util
    import os
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("sign_release", Path(__file__).resolve().parent.parent / "tools" / "sign_release.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    f = tmp_path / "Velora-1.1.0-mac.zip"
    f.write_bytes(b"archive")
    monkeypatch.setitem(os.environ, "UPDATE_PRIVATE_KEY", base64.b64encode(KEY.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode())
    tool.sign(str(f), "1.1.0")
    digest = hashlib.sha256(b"archive").hexdigest()
    assert updater.verify(digest, (tmp_path / (f.name + ".sig")).read_text())  # ancien format, pour les Velora d'avant
    assert updater.verify_release("1.1.0", f.name, digest, (tmp_path / (f.name + ".sig2")).read_text())
    assert not updater.verify_release("1.1.1", f.name, digest, (tmp_path / (f.name + ".sig2")).read_text())


def run_swap(tmp_path, pid, fail=False):
    dst, new = tmp_path / "Velora.app", tmp_path / "Velora.app.new"
    dst.mkdir(exist_ok=True)
    (dst / "v").write_text("ancienne")
    if not fail:
        new.mkdir()
        (new / "v").write_text("nouvelle")
    script = tmp_path / "echange.sh"
    script.write_text(updater.SWAP_SCRIPT)
    subprocess.run(["/bin/sh", str(script), str(pid), str(new), str(dst), "true"], check=True, timeout=20)
    return dst


@pytest.mark.skipif(sys.platform == "win32", reason="script POSIX")
def test_swap_replaces_the_app(tmp_path):
    dst = run_swap(tmp_path, 2 ** 22 + 1)  # processus inexistant : on n'attend pas
    assert (dst / "v").read_text() == "nouvelle" and not (tmp_path / "Velora.app.old").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="script POSIX")
def test_swap_puts_back_the_old_app_on_failure(tmp_path):
    dst = run_swap(tmp_path, 2 ** 22 + 1, fail=True)  # nouvelle version absente : l'ancienne doit rester
    assert (dst / "v").read_text() == "ancienne"


def test_install_refuses_outside_a_proper_install(configured, monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(updater, "app_bundle", lambda: None)
    assert not updater.can_install()
    with pytest.raises(RuntimeError):
        updater.install(tmp_path / "x.zip")


def test_windows_update_runs_a_script_that_installs_then_relaunches(configured, monkeypatch, tmp_path):
    """Régression : /RESTARTAPPLICATIONS ne relançait rien (Velora s'était déjà fermée). Un script doit attendre, installer, puis relancer Velora."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda cmd, *a, **k: calls.append((cmd, k)))
    (tmp_path / "Velora-setup.exe").write_bytes(b"x")
    updater.install(tmp_path / "Velora-setup.exe", 4321)
    (cmd, kw), = calls
    assert cmd[:2] == ["cmd", "/c"] and cmd[2].endswith("maj.bat") and cmd[3] == "4321" and cmd[4].endswith("Velora-setup.exe") and cmd[5] == sys.executable
    assert "RESTARTAPPLICATIONS" not in " ".join(cmd) and cmd[6].endswith("mise-a-jour.log")
    text = (tmp_path / "maj.bat").read_text(encoding="cp1252")
    assert text.index("tasklist") < text.index("/VERYSILENT") < text.index('start "" "%~3"')  # attendre, installer jusqu'au bout, relancer
    assert "start /wait" in text and "/CLOSEAPPLICATIONS" in text
    assert text.index("tasklist") < text.index("taskkill /F /IM") < text.index("/VERYSILENT")  # les processus Velora restants sont fermés avant l'installation (le relais de Chrome la bloquait)


@pytest.mark.skipif(sys.platform == "win32", reason="script POSIX")
def test_swap_logs_what_it_did(tmp_path):
    dst, new, log = tmp_path / "Velora.app", tmp_path / "Velora.app.new", tmp_path / "maj.log"
    dst.mkdir(); new.mkdir()
    script = tmp_path / "echange.sh"
    script.write_text(updater.SWAP_SCRIPT)
    subprocess.run(["/bin/sh", str(script), str(2 ** 22 + 1), str(new), str(dst), "true", str(log)], check=True, timeout=20)
    text = log.read_text(encoding="utf-8")
    assert "application remplacée" in text and "relance demandée" in text



def test_a_504_from_the_direct_download_falls_back_to_the_github_api(configured, monkeypatch, tmp_path):
    """Régression vue sur un PC : « HTTP Error 504: Gateway Time-out » sur l'adresse de téléchargement directe de GitHub empêchait toute mise à jour."""
    import urllib.error
    data = b"installateur"
    info = {"version": "1.1.0", "name": "Velora-1.1.0-setup.exe", "url": "https://github.com/x/releases/download/v1.1.0/f", "sig_url": "https://github.com/x/releases/download/v1.1.0/f.sig2",
            "api_url": "https://api.github.com/x/assets/1", "sig_api_url": "https://api.github.com/x/assets/2"}
    files = {info["api_url"]: data, info["sig_api_url"]: sig2(data, "1.1.0", "Velora-1.1.0-setup.exe").encode()}
    calls = []

    def get(url, timeout=15, octet=False):
        calls.append((url, octet))
        if "github.com/x/releases/download" in url:
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
        return Resp(files[url])
    monkeypatch.setattr(updater, "_get", get)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    assert updater.download(info, tmp_path).read_bytes() == data
    assert [c for c in calls if c[0] == info["url"]] and (info["api_url"], True) in calls  # le direct a été tenté (deux fois), puis l'API (avec l'en-tête « octet-stream »)
    assert len([c for c in calls if c[0] == info["url"]]) == 2


def test_when_github_is_down_everywhere_the_message_is_clear_and_nothing_is_left(configured, monkeypatch, tmp_path):
    import urllib.error
    info = {"version": "1.1.0", "name": "Velora-1.1.0-setup.exe", "url": "u/f", "sig_url": "u/f.sig2", "api_url": "api/f", "sig_api_url": "api/f.sig2"}

    def down(url, timeout=15, octet=False):
        raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
    monkeypatch.setattr(updater, "_get", down)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError) as e:
        updater.download(info, tmp_path)
    assert "GitHub ne répond pas" in str(e.value) and "504" in str(e.value) and "pas un problème de votre ordinateur" in str(e.value)
    assert not list(tmp_path.glob("*.exe"))
    assert "Connexion à GitHub impossible" in updater.friendly_error(urllib.error.URLError("pas de réseau"))
    assert "refusé le téléchargement (erreur 404)" in updater.friendly_error(urllib.error.HTTPError("u", 404, "x", {}, None))


def test_fetch_latest_keeps_the_api_addresses_as_a_fallback(configured, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    rel = json.dumps({"tag_name": "v1.1.0", "body": "", "assets": [
        {"name": "Velora-1.1.0-setup.exe", "browser_download_url": "d/exe", "url": "api/exe"},
        {"name": "Velora-1.1.0-setup.exe.sig2", "browser_download_url": "d/sig2", "url": "api/sig2"}]}).encode()
    fake_net(monkeypatch, {"https://api.github.com/repos/moi/velora/releases/latest": rel})
    info = updater.fetch_latest()
    assert info["url"] == "d/exe" and info["api_url"] == "api/exe" and info["sig_url"] == "d/sig2" and info["sig_api_url"] == "api/sig2"


MIRROR = "https://miroir.exemple.fr/velora"


@pytest.fixture
def hook(monkeypatch):
    """Un module qui ajoute une source de mise à jour (miroir privé) : ce que fait le module « Miroir » sans que le code public en sache rien."""
    state = {"latest": None, "boom": False}

    def latest(suffix):
        if state["boom"]:
            raise RuntimeError("module en panne")
        return state["latest"]

    def sources(info, kind):
        if state["boom"]:
            raise RuntimeError("module en panne")
        name = info["name"] + (".sig2" if kind == "sig" else "")
        return [(f"{MIRROR}/{info['version']}/{name}", False), ("http://pas-https.exemple.fr/x", False)]
    monkeypatch.setitem(updater.hooks, "miroir", {"latest": latest, "sources": sources})
    return state


def test_a_module_can_announce_the_latest_version_when_github_does_not_answer(configured, hook, monkeypatch):
    import urllib.error
    monkeypatch.setattr(sys, "platform", "win32")
    hook["latest"] = {"version": "1.1.0", "name": "Velora-1.1.0-setup.exe", "url": MIRROR + "/1.1.0/Velora-1.1.0-setup.exe", "sig_url": MIRROR + "/1.1.0/Velora-1.1.0-setup.exe.sig2"}

    def get(url, timeout=15, octet=False):
        raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)
    monkeypatch.setattr(updater, "_get", get)
    info = updater.fetch_latest()
    assert info["version"] == "1.1.0" and info["url"].startswith(MIRROR) and info["api_url"] == ""
    hook["latest"] = {"version": "1.0.0", "name": "Velora-1.0.0-setup.exe", "url": "u", "sig_url": "s"}  # pas plus récent : rien
    with pytest.raises(urllib.error.HTTPError):
        updater.fetch_latest()
    hook["boom"] = True  # module en panne : l'erreur de GitHub remonte comme avant
    with pytest.raises(urllib.error.HTTPError):
        updater.fetch_latest()
    monkeypatch.setitem(updater.hooks, "miroir", {})
    with pytest.raises(urllib.error.HTTPError):
        updater.fetch_latest()


def test_when_github_answers_a_module_is_not_asked_for_the_version(configured, hook, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    hook["latest"] = {"version": "9.9.9", "name": "Velora-9.9.9-mac.zip", "url": MIRROR + "/9.9.9/f", "sig_url": MIRROR + "/9.9.9/f.sig2"}
    fake_net(monkeypatch, {"https://api.github.com/repos/moi/velora/releases/latest": release()})
    assert updater.fetch_latest()["version"] == "1.1.0"  # la version de GitHub, jamais celle d'un module


def test_download_falls_back_to_a_module_source_and_still_checks_the_signature(configured, hook, monkeypatch, tmp_path):
    import urllib.error
    data = b"installateur"
    info = {"version": "1.1.0", "name": "Velora-1.1.0-setup.exe", "url": "https://github.com/x/f", "sig_url": "https://github.com/x/f.sig2", "api_url": "https://api.github.com/a/1",
            "sig_api_url": "https://api.github.com/a/2"}
    served = {"data": data}
    asked = []

    def get(url, timeout=15, octet=False):
        asked.append(url)
        if "github.com" in url:
            raise urllib.error.HTTPError(url, 504, "Gateway Time-out", {}, None)  # tout GitHub en panne
        return Resp(served["data"] if url.endswith(".exe") else sig2(data, "1.1.0", "Velora-1.1.0-setup.exe").encode())
    monkeypatch.setattr(updater, "_get", get)
    monkeypatch.setattr(updater.time, "sleep", lambda s: None)
    assert updater.download(info, tmp_path).read_bytes() == data
    assert not any(u.startswith("http://") for u in asked)  # une adresse en clair proposée par un module n'est jamais utilisée
    served["data"] = b"fichier piege depose sur le miroir"  # un miroir piraté ne peut rien faire installer
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    assert not list(tmp_path.glob("*.exe"))
    hook["boom"] = True  # module en panne : l'échec reste celui de GitHub, clair
    with pytest.raises(RuntimeError, match="GitHub ne répond pas"):
        updater.download(info, tmp_path)


def test_unloading_a_module_removes_its_update_source(monkeypatch):
    from mailia import modules
    monkeypatch.setitem(updater.hooks, "miroir", {"sources": lambda i, k: []})
    modules.unload("miroir")
    assert "miroir" not in updater.hooks


def test_when_the_api_refuses_the_public_release_page_gives_the_version(configured, hook, monkeypatch):
    import urllib.error
    monkeypatch.setattr(sys, "platform", "darwin")

    def refused(url, timeout=15, octet=False):
        raise urllib.error.HTTPError(url, 403, "rate limit exceeded", {}, None)
    monkeypatch.setattr(updater, "_get", refused)
    monkeypatch.setattr(updater, "_page_tag", lambda repo: "v1.1.0")
    info = updater.fetch_latest()
    assert info["version"] == "1.1.0" and info["name"] == "Velora-1.1.0-mac.zip"
    assert info["url"] == "https://github.com/moi/velora/releases/download/v1.1.0/Velora-1.1.0-mac.zip" and info["sig_url"] == info["url"] + ".sig2"
    monkeypatch.setattr(updater, "_page_tag", lambda repo: "v1.0.0")  # à jour
    assert updater.fetch_latest() is None
    monkeypatch.setattr(updater, "_page_tag", lambda repo: (_ for _ in ()).throw(OSError("x")))  # page illisible : le miroir prend le relais, sinon l'erreur remonte
    with pytest.raises(urllib.error.HTTPError):
        updater.fetch_latest()


REAL_PAGE_TAG = updater._page_tag  # la vraie fonction, avant que conftest ne coupe le réseau


def test_page_tag_reads_the_redirect_without_following_it(monkeypatch):
    import email.message
    import urllib.error

    class Op:
        def open(self, req, timeout=15):
            h = email.message.Message()
            h["Location"] = "https://github.com/moi/velora/releases/tag/v1.0.83"
            raise urllib.error.HTTPError(req.full_url, 302, "Found", h, None)
    monkeypatch.setattr(updater.urllib.request, "build_opener", lambda *a: Op())
    assert REAL_PAGE_TAG("moi/velora") == "v1.0.83"
