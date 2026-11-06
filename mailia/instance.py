"""Une seule copie de Velora à la fois.

Sans cela, un clic sur l'icône pendant que Velora tourne lançait une deuxième copie (deux fenêtres, deux synchronisations, deux serveurs). La première copie écoute sur un petit
port local réservé ; une copie lancée ensuite le voit occupé, ramène la fenêtre existante au premier plan et s'arrête. Si ce port est pris par un autre programme (réponse
inattendue), on ne bloque rien : Velora démarre comme avant. Une copie en train de se fermer (mise à jour, redémarrage) se signale : la suivante attend qu'elle ait fini."""
import logging
import os
import socket
import subprocess
import sys
import threading
import time

PORT = 8764
WAIT_FOR_CLOSING = 30  # secondes : la copie précédente se ferme (mise à jour, redémarrage) : on attend qu'elle ait fini
log = logging.getLogger("mailia")
_state: dict = {"sock": None, "closing": False}
on_show = lambda: None  # posé par l'application : ramène sa fenêtre au premier plan


def _bind() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)  # pas de partage du port avec une autre copie
    else:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", PORT))
        s.listen(8)
    except OSError:
        s.close()
        raise
    return s


def _ask(cmd: str, timeout: float = 2.0) -> str | None:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=timeout) as c:
            c.sendall((cmd + "\n").encode("ascii"))
            c.settimeout(timeout)
            return c.recv(128).decode("utf-8", "ignore").strip()
    except OSError:
        return None


def _serve(sock: socket.socket) -> None:
    while True:
        try:
            conn, _ = sock.accept()
        except OSError:
            return
        with conn:
            try:
                conn.settimeout(2)
                cmd = conn.recv(64).decode("ascii", "ignore").strip()
                if cmd in ("hello", "show"):
                    conn.sendall(f"VELORA {os.getpid()} {'closing' if _state['closing'] else 'running'}\n".encode("ascii"))
                    if cmd == "show" and not _state["closing"]:
                        threading.Thread(target=_safe_show, name="velora-show", daemon=True).start()
            except OSError:
                continue


def _raise_windows() -> int:
    """Windows : restaure et met au premier plan toute fenêtre intitulée « Velora » qui n'appartient pas à ce processus (la fenêtre d'une autre copie, ou celle de la copie
    en cours : elle vit dans un processus fils). Rend le nombre de fenêtres trouvées.
    Une fenêtre réduite d'un autre processus ne répond pas toujours à un simple « afficher » : on lui envoie aussi la commande système « restaurer » (celle de la barre des tâches)."""
    import ctypes
    from ctypes import wintypes
    u = ctypes.windll.user32
    for name, args, res in (("GetWindowTextLengthW", [wintypes.HWND], ctypes.c_int), ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                            ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD), ("IsIconic", [wintypes.HWND], wintypes.BOOL),
                            ("ShowWindow", [wintypes.HWND, ctypes.c_int], wintypes.BOOL), ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
                            ("BringWindowToTop", [wintypes.HWND], wintypes.BOOL), ("PostMessageW", [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM], wintypes.BOOL),
                            ("SwitchToThisWindow", [wintypes.HWND, wintypes.BOOL], None)):
        f = getattr(u, name)
        f.argtypes, f.restype = args, res
    me, found = os.getpid(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        n = u.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            u.GetWindowTextW(hwnd, buf, n + 1)
            pid = wintypes.DWORD(0)
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if buf.value == "Velora" and pid.value != me:
                found.append(hwnd)
        return True
    u.EnumWindows(cb, 0)
    try:
        u.AllowSetForegroundWindow(-1)  # ASFW_ANY : cette copie vient d'être lancée par l'utilisateur, elle laisse un autre processus passer au premier plan
    except Exception:  # noqa: BLE001
        pass
    for h in found:
        if u.IsIconic(h):
            u.PostMessageW(h, 0x0112, 0xF120, 0)  # WM_SYSCOMMAND, SC_RESTORE
            u.ShowWindow(h, 9)  # SW_RESTORE
        else:
            u.ShowWindow(h, 5)  # SW_SHOW : la montre si elle était masquée
        u.BringWindowToTop(h)
        u.SetForegroundWindow(h)
        u.SwitchToThisWindow(h, True)
    return len(found)


def _safe_show() -> None:
    """Dans la copie qui tourne : sa fenêtre revient au premier plan (par la bibliothèque de fenêtre, puis directement par Windows en renfort)."""
    try:
        on_show()
    except Exception:  # noqa: BLE001 — au pire la fenêtre reste où elle est
        log.info("deuxième lancement : fenêtre non ramenée par la bibliothèque de fenêtre", exc_info=True)
    if sys.platform == "win32":
        try:
            n = _raise_windows()
            log.info("deuxième lancement : %d fenêtre(s) Velora ramenée(s) au premier plan", n)
        except Exception:  # noqa: BLE001
            log.info("deuxième lancement : fenêtre non ramenée par Windows", exc_info=True)


def _note(text: str) -> None:
    """Ligne dans le journal de Velora, écrite directement : cette copie, lancée en second, n'a pas encore configuré ses propres journaux."""
    try:
        from datetime import datetime

        from . import config
        with open(config.DATA_DIR / "logs" / "mailia.log", "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S},000 INFO mailia : {text}\n")
    except Exception:  # noqa: BLE001
        pass


def _raise_other() -> None:
    """Depuis la copie qui vient d'être lancée (elle a le droit de passer au premier plan) : ramène la fenêtre de la copie existante."""
    try:
        if sys.platform == "win32":
            _note(f"deuxième lancement : {_raise_windows()} fenêtre(s) Velora ramenée(s) au premier plan par la copie lancée")
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", 'tell application id "fr.lfauvel.mailia" to activate'], capture_output=True, timeout=10)
    except Exception as e:  # noqa: BLE001
        _note(f"deuxième lancement : fenêtre non ramenée ({type(e).__name__} : {str(e)[:120]})")


def claim() -> bool:
    """True : cette copie est la seule (ou on ne peut pas savoir) : elle démarre. False : une autre copie tourne déjà, elle a été ramenée au premier plan : il faut s'arrêter."""
    deadline = time.time() + WAIT_FOR_CLOSING
    while True:
        try:
            sock = _bind()
            break
        except OSError:
            reply = _ask("show")
            parts = (reply or "").split()
            if len(parts) < 3 or parts[0] != "VELORA":
                log.info("instance unique : le port %d est pris par autre chose, démarrage sans verrou", PORT)
                return True
            if parts[2] == "closing" and time.time() < deadline:
                time.sleep(0.5)  # la copie précédente se ferme (mise à jour, redémarrage) : on lui laisse finir
                continue
            if parts[2] == "closing":
                return True  # elle n'en finit pas : on démarre quand même
            _raise_other()
            return False
    _state["sock"] = sock
    threading.Thread(target=_serve, args=(sock,), name="velora-instance", daemon=True).start()
    return True


def closing() -> None:
    """À appeler quand l'application se ferme : une copie lancée maintenant attendra la fin de celle-ci au lieu de la croire en marche."""
    _state["closing"] = True
