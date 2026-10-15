"""Signe une mise à jour avec la clé privée de publication : écrit <fichier>.sig à côté.

Usage :  UPDATE_PRIVATE_KEY=<clé> python tools/sign_release.py Velora-1.2.0-mac.zip
Première fois : python tools/sign_release.py --new-key   (écrit la clé privée dans ~/velora-update-private.key, affiche la clé publique)"""
import base64
import hashlib
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)


def new_key() -> None:
    key = Ed25519PrivateKey.generate()
    path = Path.home() / "velora-update-private.key"
    if path.exists():
        sys.exit(f"{path} existe déjà : je ne l'écrase pas.")
    path.write_text(base64.b64encode(key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode())
    path.chmod(0o600)
    print(f"Clé privée écrite dans {path} (à copier dans le secret GitHub UPDATE_PRIVATE_KEY, puis à garder en lieu sûr).")
    print("Clé publique (UPDATE_PUBKEY dans mailia/version.py) :", base64.b64encode(key.public_key().public_bytes(**RAW)).decode())


def sign(file: str) -> None:
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ["UPDATE_PRIVATE_KEY"].strip()))
    digest = hashlib.sha256(Path(file).read_bytes()).hexdigest()
    Path(file + ".sig").write_text(base64.b64encode(key.sign(digest.encode())).decode())


if __name__ == "__main__":
    new_key() if sys.argv[1:] == ["--new-key"] else [sign(f) for f in sys.argv[1:]]
