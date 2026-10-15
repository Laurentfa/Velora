"""Pont « stdio » vers le serveur MCP de l'appli : certains assistants (Claude Desktop) lancent un programme local et lui parlent par ses
entrées/sorties. Ce pont relaie chaque message vers http://127.0.0.1:<port>/mcp avec votre clé d'accès.
Usage : « Velora » --mcp-stdio [--url http://127.0.0.1:8766/mcp]   (la clé est lue dans la variable MAILIA_API_KEY)"""
import json
import os
import sys
import urllib.error
import urllib.request


def run(argv: list[str]) -> None:
    url = "http://127.0.0.1:8766/mcp"
    if "--url" in argv:
        url = argv[argv.index("--url") + 1]
    key = os.environ.get("MAILIA_API_KEY", "")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        req = urllib.request.Request(url, data=line.encode(), method="POST",
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + key, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read().decode()
        except urllib.error.HTTPError as e:
            body = e.read().decode()
        except OSError:
            rid = msg.get("id") if isinstance(msg, dict) else None
            body = json.dumps({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message":
                               "Velora n'est pas lancé, ou le serveur MCP est désactivé (Paramètres › API et assistants)."}}) if rid is not None else ""
        if body.strip():
            sys.stdout.write(body.strip().replace("\n", " ") + "\n")
            sys.stdout.flush()
