"""Relevé des temps d'exécution, pour comprendre où l'appli est lente (surtout sur un autre ordinateur que le vôtre).

Rien n'est envoyé nulle part : les mesures restent en mémoire, les opérations lentes sont notées dans le journal, et le rapport (Paramètres ›
Utilisation et données › Mesurer les performances) se copie à la main. Il ne contient aucun contenu de mail."""
import asyncio
import functools
import logging
import os
import platform
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger("perf")


def _origin() -> tuple[float, bool]:
    """Instant de lancement du programme, partagé avec la fenêtre native : elle tourne dans un second processus qui hérite de cette variable
    (le numéro du processus évite de reprendre l'heure d'un lancement précédent, par ex. après une mise à jour)."""
    try:
        pid, t0 = os.environ.get("VELORA_T0", "").split(":")
        if int(pid) in (os.getpid(), os.getppid()):
            return float(t0), int(pid) == os.getppid()
    except ValueError:
        pass
    t0 = time.time()
    os.environ["VELORA_T0"] = f"{os.getpid()}:{t0}"
    return t0, False


T0, IS_WINDOW = _origin()  # IS_WINDOW : ce processus est la fenêtre native, pas le programme principal
SLOW = 0.5  # secondes : au-delà, l'opération est notée dans le journal
STATS: dict[str, list[float]] = {}  # nom -> [nombre, total, maximum]
MARKS: list[tuple[str, float]] = []
_lock = threading.Lock()


def record(name: str, dt: float) -> None:
    with _lock:
        s = STATS.setdefault(name, [0, 0.0, 0.0])
        s[0] += 1
        s[1] += dt
        s[2] = max(s[2], dt)
    if dt >= SLOW:
        log.info("lent : %s %.2f s", name, dt)


@contextmanager
def span(name: str):
    t = time.perf_counter()
    try:
        yield
    finally:
        record(name, time.perf_counter() - t)


def mark(name: str, at: float | None = None) -> None:
    """Jalon depuis le lancement du programme (ex : « fenêtre prête »). `at` : instant (heure Unix) si l'événement a eu lieu avant."""
    name = ("fenêtre native : " if IS_WINDOW else "") + name
    MARKS.append((name, (at if at is not None else time.time()) - T0))
    log.info("jalon : %s à %.1f s après le lancement", name, MARKS[-1][1])


def save_window_marks() -> None:
    """Appelé par la fenêtre native (autre processus) : ses jalons sont rangés dans la base pour figurer dans le rapport du programme principal."""
    import json
    from . import db
    try:
        db.kv_set("perf:window", json.dumps({"t0": T0, "marks": MARKS}))
    except Exception:
        log.warning("jalons de la fenêtre non enregistrés", exc_info=True)


def browser_marks(info: dict) -> None:
    """Jalons mesurés par le navigateur intégré (envoyés une fois la page dessinée) : début du chargement, document chargé, premier affichage."""
    if any(n.startswith("navigateur") for n, _ in MARKS):
        return
    try:
        origin = float(info["origin"]) / 1000
        for label, ms in (("navigateur : page demandée", 0), ("navigateur : document chargé", info.get("dcl")),
                          ("navigateur : ressources chargées", info.get("load")), ("navigateur : interface dessinée à l'écran", info.get("now"))):
            if ms is not None and (ms or label.endswith("demandée")):
                MARKS.append((label, origin + float(ms) / 1000 - T0))
    except (KeyError, TypeError, ValueError):
        return
    log.info("jalons du navigateur : " + " · ".join(f"{n.split(' : ')[1]} {t:.1f} s" for n, t in MARKS if n.startswith("navigateur")))


def wrap(module, attr: str, label: str | None = None) -> None:
    fn = getattr(module, attr)
    if getattr(fn, "_perf", False):
        return
    name = label or f"{module.__name__.rsplit('.', 1)[-1]}.{attr}"

    @functools.wraps(fn)
    def timed(*a, **k):
        with span(name):
            return fn(*a, **k)
    timed._perf = True
    setattr(module, attr, timed)


def install() -> None:
    """Instrumente les opérations qui comptent : synchronisation, liste, ouverture d'un mail, analyse IA."""
    from . import ai, queries, sync
    for mod, attr in ((sync, "run_cycle"), (sync, "ensure_details"), (sync, "perform_many"), (sync, "refresh_folders"),
                      (queries, "list_messages"), (queries, "get_message"), (ai, "analyze_and_store")):
        try:
            wrap(mod, attr)
        except AttributeError:
            pass


_beat = [time.monotonic()]
BLOCKS: list[str] = []  # ce que faisait le serveur de l'interface pendant ses derniers blocages (les 5 derniers)


def _watchdog(thread_id: int) -> None:
    """Fil d'observation : si le serveur ne donne plus signe de vie depuis 1,5 s, note ce qu'il est en train d'exécuter (pile d'appels)."""
    import traceback
    reported = False
    while True:
        time.sleep(0.5)
        stale = time.monotonic() - _beat[0]
        if stale > 1.5 and not reported:
            frame = sys._current_frames().get(thread_id)
            where = "".join(traceback.format_stack(frame)[-8:]) if frame else "?"
            BLOCKS.append(f"bloqué depuis {stale:.1f} s :\n{where}")
            del BLOCKS[:-5]
            log.warning("serveur de l'interface bloqué depuis %.1f s, il exécutait :\n%s", stale, where)
            reported = True
        elif stale <= 1.5:
            reported = False


async def lag_monitor() -> None:
    """Mesure de combien la boucle du serveur est bloquée : si elle l'est, l'interface se fige (un traitement long dans le mauvais fil)."""
    threading.Thread(target=_watchdog, args=(threading.get_ident(),), daemon=True, name="perf-watchdog").start()
    while True:
        t = time.perf_counter()
        _beat[0] = time.monotonic()
        await asyncio.sleep(0.5)
        lag = time.perf_counter() - t - 0.5
        if lag > 0.1:
            record("serveur de l'interface bloqué", lag)


# ---------- mesures de la machine ----------
def _timeit(fn, n: int = 1) -> float:
    t = time.perf_counter()
    for _ in range(n):
        fn()
    return (time.perf_counter() - t) / n


def benchmark() -> dict[str, str]:
    """Quelques mesures simples, sans contenu personnel. Chaque ligne dit ce qu'on attend d'un ordinateur normal."""
    import socket
    from . import config, db
    out: dict[str, str] = {}
    out["Calcul (2 millions d'opérations ; normal : < 0,3 s)"] = f"{_timeit(lambda: sum(i * i for i in range(2_000_000))):.2f} s"

    def disk() -> None:
        p = config.DATA_DIR / "mesure.tmp"
        with open(p, "wb") as f:
            f.write(os.urandom(1 << 20))
            f.flush()
            os.fsync(f.fileno())
        p.unlink()
    out["Écriture d'un fichier de 1 Mo sur disque (normal : < 0,05 s)"] = f"{_timeit(disk, 5):.3f} s"

    def many_files() -> None:
        with tempfile.TemporaryDirectory() as d:
            for i in range(200):
                (Path(d) / f"f{i}.txt").write_text("x" * 2000)
            for i in range(200):
                (Path(d) / f"f{i}.txt").read_text()
    out["200 petits fichiers écrits puis relus (normal : < 0,3 s ; très long = antivirus qui analyse tout)"] = f"{_timeit(many_files):.2f} s"

    def query() -> None:
        with db.conn() as c:
            c.execute("SELECT COUNT(*) FROM messages").fetchone()
    out["Base de données : une requête (normal : < 0,002 s)"] = f"{_timeit(query, 30):.4f} s"
    out["Nom « localhost » (normal : < 0,01 s ; plus = délai réseau à chaque requête)"] = f"{_timeit(lambda: socket.getaddrinfo('localhost', 80), 5):.4f} s"
    try:
        out["Résolution d'un nom Internet (normal : < 0,2 s)"] = f"{_timeit(lambda: socket.getaddrinfo('github.com', 443)):.3f} s"
    except OSError as e:
        out["Résolution d'un nom Internet"] = f"échec : {e}"
    return out


def report(bench: dict[str, str] | None = None, browser: str = "") -> str:
    from . import config
    from .version import APP_VERSION
    lines = [f"Velora {APP_VERSION} · {platform.platform()} · Python {sys.version.split()[0]} · {os.cpu_count()} cœurs · données : {config.DATA_DIR}"]
    if browser:
        lines.append(f"Moteur d'affichage de la fenêtre : {browser}")
    lines.append(f"Programme lancé depuis {time.time() - T0:.0f} s")
    marks = list(MARKS)
    try:
        import json
        from . import db
        win = json.loads(db.kv_get("perf:window") or "{}")
        if win.get("t0") == T0:  # jalons de la fenêtre native, du lancement en cours seulement
            marks += [(n, t) for n, t in win["marks"] if n.startswith("fenêtre native")]
    except Exception:
        pass
    if marks:
        lines += ["", "Jalons depuis le lancement :"] + [f"  {t:6.1f} s  {n}" for n, t in sorted(marks, key=lambda m: m[1])]
    with _lock:
        rows = sorted(STATS.items(), key=lambda kv: -kv[1][1])
    if rows:
        lines += ["", f"{'Opération':44} {'nb':>5} {'total':>8} {'moyen':>8} {'max':>8}"]
        lines += [f"{n[:44]:44} {int(s[0]):>5} {s[1]:>7.1f}s {s[1] / s[0]:>7.2f}s {s[2]:>7.2f}s" for n, s in rows]
    if BLOCKS:
        lines += ["", "Derniers blocages de l'interface (ce que le programme exécutait) :"] + BLOCKS[-3:]
    if bench:
        lines += ["", "Mesures de l'ordinateur :"] + [f"  {k} : {v}" for k, v in bench.items()]
    return "\n".join(lines)
