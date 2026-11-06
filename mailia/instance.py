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


def _safe_show() -> None:
    try:
        on_show()
    except Exception:  # noqa: BLE001 — au pire la fenêtre reste où elle est
        log.debug("fenêtre non ramenée au premier plan", exc_info=True)


def _raise_other() -> None:
    """Depuis la copie qui vient d'être lancée (elle a le droit de passer au premier plan) : ramène la fenêtre de la copie existante."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
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
            for h in found:
                u.ShowWindow(h, 9)  # SW_RESTORE : sort la fenêtre de la barre des tâches si elle était réduite
                u.SetForegroundWindow(h)
        elif sys.platform == "darwin":
            subprocess.run(["osascript", "-e", 'tell application id "fr.lfauvel.mailia" to activate'], capture_output=True, timeout=10)
    except Exception:  # noqa: BLE001
        pass


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
