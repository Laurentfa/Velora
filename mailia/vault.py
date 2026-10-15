"""Chiffrement de la base locale et verrouillage de l'application.

- Base chiffrée (SQLCipher, AES-256) : un mail copié depuis le disque, une sauvegarde ou un disque volé reste illisible. La clé est
  aléatoire et vit dans le trousseau du système (Trousseau d'accès macOS, Gestionnaire d'identifiants Windows), jamais à côté du fichier.
- Verrouillage : un code (ou Touch ID sur Mac) demandé à chaque ouverture et après un temps d'inactivité choisi.
Les mots de passe des boîtes mail ne sont pas dans la base (trousseau ou fichier de configuration)."""
import hashlib
import hmac
import logging
import os
import secrets
import sys
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger("vault")
SERVICE, KEY_NAME = "MailIA-db", "cle"
PLAIN_HEADER = b"SQLite format 3\x00"

_key: str | None = None
_unlocked_at: float | None = None
_fails: list[float] = []
BOOT_ID = secrets.token_hex(8)  # change à chaque lancement : un déverrouillage ne survit jamais à la fermeture de l'appli


# ---------- bibliothèque et clé ----------
def sqlcipher():
    try:
        import sqlcipher3.dbapi2 as sc
        return sc
    except ImportError:
        return None


def available() -> bool:
    return sqlcipher() is not None and _keyring() is not None


def _keyring():
    try:
        import keyring
        keyring.get_keyring()
        return keyring
    except Exception:
        return None


def is_encrypted(path: Path | None = None) -> bool:
    p = path or config.DB_PATH
    try:
        with open(p, "rb") as f:
            head = f.read(16)
    except FileNotFoundError:
        return False
    return bool(head) and head != PLAIN_HEADER


def stored_key() -> str | None:
    kr = _keyring()
    if not kr:
        return None
    try:
        return kr.get_password(SERVICE, KEY_NAME)
    except Exception:
        log.warning("lecture de la clé de chiffrement impossible", exc_info=True)
        return None


def key() -> str | None:
    """Clé hexadécimale de la base (mise en mémoire après la première lecture du trousseau)."""
    global _key
    if _key is None:
        _key = stored_key() or os.environ.get("MAILIA_DB_KEY")
    return _key


def connect(path, timeout: float = 30):
    """Connexion à la base : chiffrée (clé du trousseau) si le fichier l'est, sinon SQLite ordinaire."""
    import sqlite3
    if is_encrypted(path):
        sc = sqlcipher()
        k = key()
        if not sc or not k:
            raise RuntimeError("La base de données est chiffrée, mais sa clé est introuvable dans le trousseau du système. "
                               "Restaurez la clé de récupération (Paramètres › Confidentialité et sécurité).")
        c = sc.connect(str(path), timeout=timeout)
        c.execute(f"PRAGMA key=\"x'{k}'\"")
        c.execute("PRAGMA synchronous=NORMAL")
        c.row_factory = sc.Row
        return c
    c = sqlite3.connect(path, timeout=timeout)
    c.execute("PRAGMA synchronous=NORMAL")
    c.row_factory = sqlite3.Row
    return c


# ---------- activer / désactiver le chiffrement ----------
def _wipe(path: Path) -> None:
    """Efface un fichier en l'écrasant d'abord (au mieux : un disque SSD ne garantit rien)."""
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            for _ in range(1):
                f.seek(0)
                remaining = size
                while remaining > 0:
                    n = min(remaining, 1 << 20)
                    f.write(b"\x00" * n)
                    remaining -= n
                f.flush()
                os.fsync(f.fileno())
    except OSError:
        pass
    try:
        path.unlink()
    except OSError:
        pass


def _export(src: Path, dst: Path, src_key: str | None, dst_key: str | None) -> None:
    sc = sqlcipher()
    c = sc.connect(str(src))
    try:
        if src_key:
            c.execute(f"PRAGMA key=\"x'{src_key}'\"")
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        c.execute("ATTACH DATABASE ? AS target KEY " + (f"\"x'{dst_key}'\"" if dst_key else "''"), (str(dst),))
        c.execute("SELECT sqlcipher_export('target')")
        c.execute("DETACH DATABASE target")
    finally:
        c.close()


def _count(path: Path, k: str | None) -> int:
    sc = sqlcipher()
    c = sc.connect(str(path))
    try:
        if k:
            c.execute(f"PRAGMA key=\"x'{k}'\"")
        return sum(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("messages", "kv", "contacts"))
    finally:
        c.close()


def enable_encryption() -> str:
    """Chiffre la base existante. Retourne la clé de récupération (à conserver). À appeler avec les accès à la base suspendus (db.rekey)."""
    global _key
    sc, kr = sqlcipher(), _keyring()
    if not sc or not kr:
        raise RuntimeError("Le chiffrement n'est pas disponible sur ce système (bibliothèque ou trousseau manquant).")
    if is_encrypted():
        raise RuntimeError("La base est déjà chiffrée.")
    new_key = secrets.token_hex(32)
    src, tmp = config.DB_PATH, config.DB_PATH.with_suffix(".enc.tmp")
    tmp.unlink(missing_ok=True)
    _export(src, tmp, None, new_key)
    if _count(src, None) != _count(tmp, new_key):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Le chiffrement a échoué (contrôle de cohérence) : rien n'a été modifié.")
    kr.set_password(SERVICE, KEY_NAME, new_key)  # la clé est en lieu sûr AVANT de remplacer le fichier
    if kr.get_password(SERVICE, KEY_NAME) != new_key:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("Impossible d'enregistrer la clé dans le trousseau : rien n'a été modifié.")
    old = src.with_suffix(".plain.old")
    os.replace(src, old)
    os.replace(tmp, src)
    for ext in ("-wal", "-shm"):
        Path(str(old) + ext).unlink(missing_ok=True)
        Path(str(src) + ext).unlink(missing_ok=True)
    _key = new_key
    _wipe(old)
    return new_key


def disable_encryption() -> None:
    global _key
    if not is_encrypted():
        raise RuntimeError("La base n'est pas chiffrée.")
    k = key()
    src, tmp = config.DB_PATH, config.DB_PATH.with_suffix(".plain.tmp")
    tmp.unlink(missing_ok=True)
    _export(src, tmp, k, None)
    if _count(src, k) != _count(tmp, None):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("L'opération a échoué (contrôle de cohérence) : rien n'a été modifié.")
    old = src.with_suffix(".enc.old")
    os.replace(src, old)
    os.replace(tmp, src)
    for ext in ("-wal", "-shm"):
        Path(str(old) + ext).unlink(missing_ok=True)
        Path(str(src) + ext).unlink(missing_ok=True)
    old.unlink(missing_ok=True)
    try:
        _keyring().delete_password(SERVICE, KEY_NAME)
    except Exception:
        pass
    _key = None


def restore_key(hex_key: str) -> bool:
    """Rétablit la clé de récupération dans le trousseau ; vrai si elle ouvre la base."""
    global _key
    hex_key = (hex_key or "").strip().lower()
    if len(hex_key) != 64 or any(c not in "0123456789abcdef" for c in hex_key):
        return False
    sc = sqlcipher()
    try:
        c = sc.connect(str(config.DB_PATH))
        c.execute(f"PRAGMA key=\"x'{hex_key}'\"")
        c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        c.close()
    except Exception:
        return False
    _keyring().set_password(SERVICE, KEY_NAME, hex_key)
    _key = hex_key
    return True


# ---------- verrouillage de l'application ----------
def _hash(code: str, salt: bytes) -> str:
    return hashlib.scrypt(code.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32).hex()


def lock_enabled(db) -> bool:
    return bool(db.kv_get("lock_hash"))


def set_code(db, code: str) -> None:
    salt = secrets.token_bytes(16)
    db.kv_set("lock_hash", salt.hex() + ":" + _hash(code, salt))


def clear_code(db) -> None:
    for k in ("lock_hash", "lock_bio", "lock_idle"):
        db.kv_set(k, "")


def check_code(db, code: str) -> tuple[bool, int]:
    """(ok, secondes d'attente avant un nouvel essai). Après 5 échecs en 5 minutes, l'attente double à chaque essai."""
    now = time.time()
    _fails[:] = [t for t in _fails if now - t < 300]
    if len(_fails) >= 5:
        wait = int(min(900, 2 ** (len(_fails) - 4) * 5 - (now - _fails[-1])))
        if wait > 0:
            return False, wait
    stored = db.kv_get("lock_hash") or ""
    try:
        salt_hex, h = stored.split(":", 1)
        ok = hmac.compare_digest(_hash(code, bytes.fromhex(salt_hex)), h)
    except ValueError:
        ok = False
    if ok:
        _fails.clear()
        return True, 0
    _fails.append(now)
    return False, 0


def unlock() -> None:
    global _unlocked_at
    _unlocked_at = time.time()


def lock() -> None:
    global _unlocked_at
    _unlocked_at = None


def is_unlocked(db) -> bool:
    return (not lock_enabled(db)) or _unlocked_at is not None


def idle_minutes(db) -> int:
    try:
        return max(0, int(db.kv_get("lock_idle") or 0))
    except ValueError:
        return 0


# ---------- Touch ID (Mac) ----------
def biometric_available() -> bool:
    if sys.platform != "darwin":
        return False
    try:
        from LocalAuthentication import LAContext, LAPolicyDeviceOwnerAuthenticationWithBiometrics
        ok, _err = LAContext.alloc().init().canEvaluatePolicy_error_(LAPolicyDeviceOwnerAuthenticationWithBiometrics, None)
        return bool(ok)
    except Exception:
        return False


def biometric_prompt(reason: str = "Déverrouiller Velora") -> bool:
    """Demande Touch ID (bloquant jusqu'à la réponse). Faux si indisponible, refusé ou annulé."""
    if sys.platform != "darwin":
        return False
    try:
        from LocalAuthentication import LAContext, LAPolicyDeviceOwnerAuthenticationWithBiometrics
    except Exception:
        return False
    result: dict = {}
    done = threading.Event()
    ctx = LAContext.alloc().init()

    def reply(success, error):
        result["ok"] = bool(success)
        done.set()

    ctx.evaluatePolicy_localizedReason_reply_(LAPolicyDeviceOwnerAuthenticationWithBiometrics, reason, reply)
    done.wait(60)
    return bool(result.get("ok"))
