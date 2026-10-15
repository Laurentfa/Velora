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
    return base64.b64encode(key.sign(hashlib.sha256(data).hexdigest().encode())).decode()


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
    monkeypatch.setattr(updater, "_get", lambda url, timeout=15: Resp(files[url]))


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
        {"name": "Velora-1.1.0-mac.zip", "browser_download_url": "u/mac"}, {"name": "Velora-1.1.0-mac.zip.sig", "browser_download_url": "u/mac.sig"},
        {"name": "Velora-1.1.0-setup.exe", "browser_download_url": "u/win"}, {"name": "Velora-1.1.0-setup.exe.sig", "browser_download_url": "u/win.sig"}], **kw}).encode()


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
    info = {"name": "Velora-1.1.0-mac.zip", "url": "u/mac", "sig_url": "u/mac.sig"}
    fake_net(monkeypatch, {"u/mac": data, "u/mac.sig": sig(data).encode()})
    assert updater.download(info, tmp_path).read_bytes() == data
    fake_net(monkeypatch, {"u/mac": data + b"!", "u/mac.sig": sig(data).encode()})  # archive modifiée en route
    with pytest.raises(RuntimeError):
        updater.download(info, tmp_path)
    assert not (tmp_path / info["name"]).exists()  # et rien ne reste sur le disque


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
