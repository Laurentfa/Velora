"""Connexion « moderne » aux messageries qui n'acceptent plus les mots de passe simples (Microsoft 365 / Outlook.com, Gmail) : OAuth 2.0
avec PKCE dans le navigateur du système, puis jeton d'accès présenté à IMAP/SMTP (mécanisme XOAUTH2).

L'appli ne voit jamais votre mot de passe : le fournisseur lui remet seulement un jeton de renouvellement, gardé comme un mot de passe
(trousseau du système si activé). Il faut, pour chaque fournisseur, un « identifiant d'application » créé gratuitement par l'éditeur
du logiciel (voir README) : variables OAUTH_MICROSOFT_CLIENT_ID, OAUTH_GOOGLE_CLIENT_ID et OAUTH_GOOGLE_CLIENT_SECRET."""
import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

from . import config, tlsutil

log = logging.getLogger("oauth")
PREFIX = "oauth2:"

PROVIDERS = {
    "microsoft": {
        "name": "Microsoft",
        "auth_url": "https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        "token_url": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
        "scope": "https://outlook.office.com/IMAP.AccessAsUser.All https://outlook.office.com/SMTP.Send offline_access",
        "redirect_host": "localhost", "extra": {"prompt": "select_account"}, "needs_secret": False,
    },
    "google": {
        "name": "Google",
        "auth_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "scope": "https://mail.google.com/",
        "redirect_host": "127.0.0.1", "extra": {"access_type": "offline", "prompt": "consent"}, "needs_secret": True,
    },
}

_cache: dict[str, tuple[str, float]] = {}  # compte -> (jeton d'accès, expiration)


class OAuthError(Exception):
    """Message destiné à l'utilisateur."""


def client_id(provider: str) -> str:
    return config.env(f"OAUTH_{provider.upper()}_CLIENT_ID") or ""


def client_secret(provider: str) -> str:
    return config.env(f"OAUTH_{provider.upper()}_CLIENT_SECRET") or ""


def configured(provider: str | None) -> bool:
    if provider not in PROVIDERS:
        return False
    return bool(client_id(provider)) and (not PROVIDERS[provider]["needs_secret"] or bool(client_secret(provider)))


def is_oauth(password: str) -> bool:
    return (password or "").startswith(PREFIX)


def pack(provider: str, refresh_token: str) -> str:
    return f"{PREFIX}{provider}:{refresh_token}"


def unpack(password: str) -> tuple[str, str]:
    _, provider, token = password.split(":", 2)
    return provider, token


def xoauth2_string(user: str, token: str) -> str:
    return f"user={user}\x01auth=Bearer {token}\x01\x01"


def _post(url: str, data: dict) -> dict:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
    try:
        with tlsutil.opener(follow=False).open(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read())
            msg = detail.get("error_description") or detail.get("error") or str(e)
        except Exception:
            msg = str(e)
        raise OAuthError(f"Le fournisseur a refusé la connexion : {str(msg)[:300]}") from e
    except (OSError, ValueError) as e:
        raise OAuthError(f"Connexion au fournisseur impossible : {e}") from e


class _Catcher(BaseHTTPRequestHandler):
    result: dict

    def log_message(self, *a) -> None:
        pass

    def do_GET(self) -> None:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q or "error" in q:
            self.server.result.update({k: v[0] for k, v in q.items()})  # type: ignore[attr-defined]
            ok = "code" in q
            page = ("<h2>Connexion réussie</h2><p>Vous pouvez fermer cet onglet et revenir à Velora.</p>" if ok else
                    "<h2>Connexion refusée</h2><p>Revenez à Velora pour réessayer.</p>")
        else:
            page = "<p>En attente…</p>"
        body = f"<!doctype html><meta charset=utf-8><title>Velora</title><body style='font-family:sans-serif;text-align:center;margin-top:20vh'>{page}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar.events"  # créer et modifier des rendez-vous, rien d'autre
TASKS_SCOPE = "https://www.googleapis.com/auth/tasks.readonly"  # lire les tâches Google (affichage dans l'agenda du brief), sans pouvoir les modifier


def authorize(provider: str, email: str = "", open_browser=webbrowser.open, timeout: float = 300, cancel: threading.Event | None = None,
              scope: str | None = None) -> str:
    """Ouvre le navigateur, attend la validation de l'utilisateur et retourne le mot de passe « emballé » (jeton de renouvellement).
    Bloquant : à lancer hors de la boucle de l'interface."""
    p = PROVIDERS.get(provider)
    if not p:
        raise OAuthError("Fournisseur inconnu.")
    if not configured(provider):
        raise OAuthError(f"La connexion {p['name']} n'est pas configurée dans cette version de l'application.")
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    httpd = HTTPServer(("127.0.0.1", 0), _Catcher)
    httpd.result = {}  # type: ignore[attr-defined]
    port = httpd.server_address[1]
    redirect = f"http://{p['redirect_host']}:{port}"
    servers = [httpd]
    try:  # « localhost » peut désigner ::1 : on écoute aussi sur l'IPv6 avec le même port, si possible
        import socket
        class V6(HTTPServer):
            address_family = socket.AF_INET6
        h6 = V6(("::1", port), _Catcher)
        h6.result = httpd.result  # type: ignore[attr-defined]
        servers.append(h6)
    except OSError:
        pass
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for t in threads:
        t.start()
    params = {"client_id": client_id(provider), "response_type": "code", "redirect_uri": redirect, "scope": scope or p["scope"], "state": state,
              "code_challenge": challenge, "code_challenge_method": "S256", **p["extra"]}
    if email:
        params["login_hint"] = email
    try:
        open_browser(p["auth_url"] + "?" + urllib.parse.urlencode(params))
        t0 = time.time()
        while not httpd.result and time.time() - t0 < timeout:  # type: ignore[attr-defined]
            if cancel is not None and cancel.is_set():
                raise OAuthError("Connexion annulée.")
            time.sleep(0.3)
        res = httpd.result  # type: ignore[attr-defined]
    finally:
        for s in servers:
            try:
                s.shutdown()
                s.server_close()
            except Exception:
                pass
    if not res:
        raise OAuthError("Délai dépassé : la connexion n'a pas été validée dans le navigateur.")
    if res.get("error"):
        raise OAuthError("Connexion refusée : " + (res.get("error_description") or res["error"]))
    if res.get("state") != state:
        raise OAuthError("Réponse inattendue du navigateur (état invalide) : connexion annulée par sécurité.")
    data = {"grant_type": "authorization_code", "code": res["code"], "redirect_uri": redirect, "client_id": client_id(provider), "code_verifier": verifier}
    if p["needs_secret"]:
        data["client_secret"] = client_secret(provider)
    tok = _post(p["token_url"], data)
    if not tok.get("refresh_token"):
        raise OAuthError("Le fournisseur n'a pas fourni de jeton durable. Réessayez en acceptant toutes les autorisations demandées.")
    return pack(provider, tok["refresh_token"])


def access_token(key: str, password: str) -> str:
    """Jeton d'accès valide pour ce compte (renouvelé automatiquement quelques minutes avant son expiration)."""
    real_key = key
    provider, refresh = unpack(password)
    key = hashlib.sha256(f"{key}|{refresh}".encode()).hexdigest()  # un jeton par couple (boîte, jeton) : jamais celui d'un autre compte testé avant
    hit = _cache.get(key)
    if hit and hit[1] - 120 > time.time():
        return hit[0]
    p = PROVIDERS[provider]
    data = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id(provider), "scope": p["scope"]}
    if p["needs_secret"]:
        data["client_secret"] = client_secret(provider)
    tok = _post(p["token_url"], data)
    if not tok.get("access_token"):
        raise OAuthError("Le fournisseur n'a pas renouvelé l'accès : reconnectez la boîte (Paramètres › Boîtes mail).")
    _cache[key] = (tok["access_token"], time.time() + int(tok.get("expires_in") or 3000))
    if tok.get("refresh_token") and tok["refresh_token"] != refresh:  # certains fournisseurs font tourner le jeton de renouvellement
        config.set_password(real_key, pack(provider, tok["refresh_token"]))
    return tok["access_token"]


def refresh_access(provider: str, refresh: str, scope: str) -> tuple[str, str]:
    """Jeton d'accès pour un jeton de renouvellement donné et une portée précise (agenda). Retourne (jeton d'accès, jeton de renouvellement à retenir)."""
    cache_key = hashlib.sha256(f"{provider}|{refresh}|{scope}".encode()).hexdigest()
    hit = _cache.get(cache_key)
    if hit and hit[1] - 120 > time.time():
        return hit[0], refresh
    p = PROVIDERS[provider]
    data = {"grant_type": "refresh_token", "refresh_token": refresh, "client_id": client_id(provider), "scope": scope}
    if p["needs_secret"]:
        data["client_secret"] = client_secret(provider)
    tok = _post(p["token_url"], data)
    if not tok.get("access_token"):
        raise OAuthError("Le fournisseur n'a pas renouvelé l'accès : reconnectez l'agenda (Paramètres › Agenda).")
    _cache[cache_key] = (tok["access_token"], time.time() + int(tok.get("expires_in") or 3000))
    return tok["access_token"], tok.get("refresh_token") or refresh
