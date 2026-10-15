"""Serveur de l'API REST et du serveur MCP : un petit serveur web local, démarré seulement si vous l'activez (deux interrupteurs séparés).
Par défaut il n'écoute que cet ordinateur (127.0.0.1) ; l'accès depuis le réseau local est une option distincte."""
import logging
import threading
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import apicore, apikeys, db
from .apikeys import ApiError, Key

log = logging.getLogger("apiserver")
VERSION = "1"
DEFAULT_PORT = 8766


# ---------- réglages ----------
def rest_on() -> bool:
    return db.kv_get("api_rest_on") == "1"


def mcp_on() -> bool:
    return db.kv_get("api_mcp_on") == "1"


def lan_on() -> bool:
    return db.kv_get("api_lan") == "1"


def port() -> int:
    try:
        return max(1024, min(65535, int(db.kv_get("api_port") or DEFAULT_PORT)))
    except ValueError:
        return DEFAULT_PORT


DOCS_HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>Velora — API</title>
<style>body{font:15px/1.5 -apple-system,Segoe UI,sans-serif;max-width:920px;margin:24px auto;padding:0 16px;color:#1b2430}
h1{margin:0 0 4px}.m{display:inline-block;min-width:58px;text-align:center;border-radius:6px;padding:1px 6px;color:#fff;font:600 12px monospace;margin-right:8px}
.GET{background:#0a66e8}.POST{background:#18803a}.PUT{background:#b8860b}.DELETE{background:#d92d3a}.ep{border:1px solid #dfe6ef;border-radius:10px;padding:8px 12px;margin:8px 0}
code,pre{background:#eef2f8;border-radius:6px;padding:1px 5px}pre{padding:8px;overflow:auto}.t{color:#66768a}h2{margin-top:28px}</style></head><body>
<h1>Velora — API</h1><p class="t" id="d"></p>
<p>Authentification : en-tête <code>Authorization: Bearer mia_…</code> (clé créée dans Paramètres › API et assistants). Description complète au format OpenAPI : <a href="/openapi.json">/openapi.json</a>.</p>
<div id="out">Chargement…</div>
<script>
fetch('/openapi.json').then(r=>r.json()).then(o=>{
  document.getElementById('d').textContent = o.info.description || '';
  const tags = {}, esc = s => String(s||'').replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
  for (const [p, ms] of Object.entries(o.paths)) for (const [m, e] of Object.entries(ms)) {
    const t = (e.tags||['Autres'])[0]; (tags[t] = tags[t]||[]).push({p, m: m.toUpperCase(), e});
  }
  let h = '';
  for (const [t, list] of Object.entries(tags)) {
    h += '<h2>'+esc(t)+'</h2>';
    for (const {p, m, e} of list) {
      const params = (e.parameters||[]).map(x=>'<code>'+esc(x.name)+'</code>'+(x.required?'*':'')).join(' ');
      h += '<div class="ep"><span class="m '+m+'">'+m+'</span><code>'+esc(p)+'</code><div>'+esc(e.summary)+'</div>'
        + (e.description?'<div class="t">'+esc(e.description)+'</div>':'') + (params?'<div class="t">Paramètres : '+params+'</div>':'') + '</div>';
    }
  }
  document.getElementById('out').innerHTML = h;
});
</script></body></html>"""


# ---------- modèles (documentation interactive) ----------
class Attachment(BaseModel):
    name: str = Field(description="Nom du fichier")
    content_base64: str = Field(description="Contenu du fichier encodé en base64")
    mime: Optional[str] = None


class DraftBody(BaseModel):
    account: Optional[str] = Field(None, description="Identifiant de la boîte d'envoi (voir GET /v1/accounts) ; facultatif s'il n'y en a qu'une")
    to: Any = Field(None, description="Destinataires : une adresse, une liste ou « a@x.fr, b@y.fr »")
    cc: Any = None
    bcc: Any = None
    subject: Optional[str] = ""
    text: Optional[str] = Field(None, description="Corps du message en texte brut")
    html: Optional[str] = Field(None, description="Corps du message en HTML (nettoyé à l'enregistrement)")
    attachments: Optional[list[Attachment]] = None
    reply_to_message_id: Optional[str] = Field(None, description="Répond à ce message (le message cité est ajouté)")
    include_signature: bool = True
    include_quote: bool = True
    send_at: Optional[str] = Field(None, description="Heure d'envoi souhaitée, ISO 8601 (ex : 2026-10-12T08:00:00+02:00)")
    idempotency_key: Optional[str] = Field(None, description="Évite un doublon si la requête est rejouée")
    notify: bool = True


class SendBody(DraftBody):
    delay_minutes: Optional[float] = Field(None, description="Envoyer dans N minutes (alternative à send_at)")
    followup_days: int = Field(0, description="Me rappeler si je n'ai pas de réponse après N jours")


class SendDraftBody(BaseModel):
    send_at: Optional[str] = None
    delay_minutes: Optional[float] = None
    followup_days: int = 0


class ReplyBody(SendBody):
    reply_all: bool = False
    send: bool = Field(False, description="Envoyer au lieu de créer un brouillon (soumis aux limites de la clé)")


class ActionBody(BaseModel):
    action: str = Field(description="read, unread, archive, move, flag ou unflag")
    folder: Optional[str] = Field(None, description="Pour « move » : identifiant du dossier de destination")


class AiDraftBody(BaseModel):
    instruction: str = Field(description="Ce que l'IA doit écrire ou répondre")
    tone: str = "neutre"
    reply_to_message_id: Optional[str] = None
    create_draft: bool = Field(False, description="Créer aussi un brouillon à relire dans l'application")
    account: Optional[str] = None
    to: Any = None
    cc: Any = None


class AiMessageBody(BaseModel):
    message_id: str
    thread: bool = False


# ---------- authentification ----------
def _bearer(authorization: Optional[str]) -> str:
    return (authorization or "").removeprefix("Bearer ").removeprefix("bearer ").strip()


def key_rest(request: Request, authorization: Optional[str] = Header(None)) -> Key:
    if not rest_on():
        raise HTTPException(404, "API désactivée")
    ip = request.client.host if request.client else "?"
    apikeys.check_not_blocked(ip)
    try:
        k = apikeys.authenticate(_bearer(authorization), "rest")
    except ApiError as e:
        if e.status in (401, 403):
            apikeys.note_failure(ip, "rest", e.code)
        raise
    request.state.key = k
    return k


def _with_idem(payload: dict, header: Optional[str]) -> dict:
    """Accepte la clé d'idempotence aussi par l'en-tête standard « Idempotency-Key » (le champ du corps prime)."""
    if header and not payload.get("idempotency_key"):
        payload["idempotency_key"] = header.strip()[:200]
    return payload


def build_app() -> FastAPI:
    app = FastAPI(title="Velora — API", version=VERSION, docs_url=None, redoc_url=None, openapi_url="/openapi.json",
                  description="Rédiger, envoyer (immédiatement ou à l'heure choisie) et lire vos mails. Authentification : en-tête "
                              "« Authorization: Bearer mia_… » avec une clé créée dans Paramètres › API et assistants.")

    @app.exception_handler(ApiError)
    async def api_error(_r: Request, e: ApiError):
        return JSONResponse({"error": {"code": e.code, "message": e.message}}, status_code=e.status,
                            headers={"Retry-After": "10"} if e.status == 429 else None)

    @app.exception_handler(Exception)
    async def unexpected(_r: Request, e: Exception):
        log.exception("erreur inattendue dans l'API")
        return JSONResponse({"error": {"code": "server_error", "message": "Erreur interne de l'application : consultez le journal (Paramètres › Utilisation et données)."}},
                            status_code=500)

    @app.middleware("http")
    async def no_cache(request: Request, call_next):
        resp = await call_next(request)
        resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        return resp

    def logged(k: Key, action: str, target: str = "") -> None:
        apikeys.log(k, action, target)

    @app.get("/docs", include_in_schema=False)
    def docs():
        return Response(DOCS_HTML, media_type="text/html; charset=utf-8", headers={"Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"})

    @app.get("/v1/health", tags=["Général"], summary="Le serveur répond-il ?")
    def health():
        return {"ok": True, "api": "velora", "version": VERSION, "rest": rest_on(), "mcp": mcp_on()}

    # ----- lecture
    @app.get("/v1/accounts", tags=["Lecture"], summary="Boîtes accessibles avec cette clé")
    def accounts(k: Key = Depends(key_rest)):
        out = apicore.list_accounts(k); logged(k, "accounts.list"); return {"accounts": out}

    @app.get("/v1/folders", tags=["Lecture"], summary="Dossiers synchronisés")
    def folders(account: str = "", k: Key = Depends(key_rest)):
        out = apicore.list_folders(k, account); logged(k, "folders.list"); return {"folders": out}

    @app.get("/v1/messages", tags=["Lecture"], summary="Chercher ou lister des mails",
             description="`q` accepte du texte libre et les filtres de la recherche avancée : de:, à:, objet:, pj:oui, non-lu, depuis:, avant:, cat:, dossier:.")
    def messages(q: str = "", account: str = "", folder: str = "", unread: bool = False, since: str = "",
                 limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), k: Key = Depends(key_rest)):
        out = apicore.search_messages(k, q, account, folder, unread, limit, offset, since); logged(k, "messages.search", q); return out

    @app.get("/v1/messages/{mid}", tags=["Lecture"], summary="Lire un mail (texte, pièces jointes, analyse IA déjà calculée)")
    def message(mid: str, include_html: bool = False, max_chars: int = 20000, k: Key = Depends(key_rest)):
        out = apicore.get_message(k, mid, include_html, max_chars); logged(k, "messages.get", out["subject"]); return out

    @app.get("/v1/messages/{mid}/thread", tags=["Lecture"], summary="Conversation complète")
    def thread(mid: str, k: Key = Depends(key_rest)):
        out = apicore.get_thread(k, mid); logged(k, "messages.thread"); return out

    @app.get("/v1/messages/{mid}/attachments/{aid}", tags=["Lecture"], summary="Télécharger une pièce jointe")
    def attachment(mid: str, aid: str, k: Key = Depends(key_rest)):
        data, name, mime = apicore.get_attachment(k, mid, aid); logged(k, "attachments.get", name)
        from urllib.parse import quote
        safe = "".join(c for c in name if c.isprintable() and c not in '"\\/:*?<>|\r\n') or "piece-jointe"
        return Response(data, media_type=mime, headers={"Content-Disposition": f"attachment; filename=\"{safe.encode('ascii', 'replace').decode()}\"; filename*=UTF-8''{quote(safe)}"})

    @app.get("/v1/messages/{mid}/attachments/{aid}/text", tags=["Lecture"], summary="Texte d'une pièce jointe (Word, Excel, PowerPoint, PDF, texte)")
    def attachment_text(mid: str, aid: str, k: Key = Depends(key_rest)):
        out = apicore.attachment_text(k, mid, aid); logged(k, "attachments.text", out["name"]); return out

    @app.post("/v1/messages/{mid}/actions", tags=["Actions"], summary="Marquer lu/non lu, archiver, déplacer, épingler (jamais de suppression)")
    def action(mid: str, body: ActionBody, k: Key = Depends(key_rest)):
        return apicore.message_action(k, mid, body.action, body.folder or "")

    # ----- rédaction
    @app.post("/v1/drafts", tags=["Rédaction"], status_code=201, summary="Créer un brouillon (visible dans « Brouillons » de l'appli)")
    def draft_create(body: DraftBody, k: Key = Depends(key_rest)):
        out = apicore.create_draft(k, body.model_dump()); logged(k, "drafts.create", body.subject or ""); return out

    @app.get("/v1/drafts", tags=["Rédaction"], summary="Brouillons créés par cette clé")
    def drafts(k: Key = Depends(key_rest)):
        return {"drafts": apicore.list_drafts(k)}

    @app.get("/v1/drafts/{did}", tags=["Rédaction"], summary="Lire un brouillon")
    def draft_get(did: str, k: Key = Depends(key_rest)):
        return apicore.get_draft(k, did)

    @app.put("/v1/drafts/{did}", tags=["Rédaction"], summary="Modifier un brouillon (champs à changer seulement)")
    def draft_update(did: str, body: DraftBody, k: Key = Depends(key_rest)):
        out = apicore.update_draft(k, did, body.model_dump(exclude_unset=True)); logged(k, "drafts.update", did); return out

    @app.delete("/v1/drafts/{did}", tags=["Rédaction"], summary="Supprimer un brouillon")
    def draft_delete(did: str, k: Key = Depends(key_rest)):
        out = apicore.delete_draft(k, did); logged(k, "drafts.delete", did); return out

    @app.post("/v1/messages/{mid}/reply", tags=["Rédaction"], summary="Répondre à un mail (brouillon, ou envoi si la clé le permet)")
    def reply(mid: str, body: ReplyBody, k: Key = Depends(key_rest)):
        out = apicore.reply(k, mid, body.model_dump()); logged(k, "messages.reply", mid); return out

    # ----- envoi
    @app.post("/v1/send", tags=["Envoi"], summary="Envoyer maintenant ou à l'heure choisie",
              description="Selon la clé : mise en file d'envoi (annulable jusqu'à l'heure d'envoi) ou, par défaut, **brouillon à valider** dans l'application "
                          "(réponse 202, statut `pending_approval`).")
    def send(body: SendBody, response: Response, k: Key = Depends(key_rest), idem: Optional[str] = Header(None, alias="Idempotency-Key")):
        out = apicore.send(k, _with_idem(body.model_dump(), idem))
        response.status_code = 202 if out["status"] == "pending_approval" else 201
        return out

    @app.post("/v1/drafts/{did}/send", tags=["Envoi"], summary="Envoyer un brouillon créé par cette clé")
    def draft_send(did: str, body: SendDraftBody, response: Response, k: Key = Depends(key_rest),
                   idem: Optional[str] = Header(None, alias="Idempotency-Key")):
        out = apicore.send_draft(k, did, _with_idem(body.model_dump(), idem))
        response.status_code = 202 if out["status"] == "pending_approval" else 201
        return out

    @app.get("/v1/outbox", tags=["Envoi"], summary="Envois en attente faits par cette clé")
    def outbox(k: Key = Depends(key_rest)):
        return {"outbox": apicore.list_outbox(k)}

    @app.delete("/v1/outbox/{oid}", tags=["Envoi"], summary="Annuler un envoi pas encore parti (il redevient brouillon)")
    def outbox_cancel(oid: str, k: Key = Depends(key_rest)):
        return apicore.cancel_send(k, oid)

    # ----- IA
    @app.post("/v1/ai/draft", tags=["IA"], summary="Faire rédiger un message ou une réponse par l'IA (consomme votre budget IA)")
    def ai_draft(body: AiDraftBody, k: Key = Depends(key_rest)):
        return apicore.ai_draft(k, body.model_dump())

    @app.post("/v1/ai/summarize", tags=["IA"], summary="Résumé et analyse d'un mail ou d'une conversation")
    def ai_summarize(body: AiMessageBody, k: Key = Depends(key_rest)):
        return apicore.ai_summarize(k, body.message_id, body.thread)

    @app.post("/v1/ai/translate", tags=["IA"], summary="Traduction d'un mail")
    def ai_translate(body: AiMessageBody, k: Key = Depends(key_rest)):
        return apicore.ai_translate(k, body.message_id)

    # ----- MCP
    from . import mcp

    @app.post("/mcp", include_in_schema=False)
    async def mcp_endpoint(request: Request, authorization: Optional[str] = Header(None)):
        if not mcp_on():
            raise HTTPException(404, "Serveur MCP désactivé")
        origin = request.headers.get("origin")
        if origin and not _origin_ok(origin):
            raise HTTPException(403, "Origine refusée")
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalide"}}, status_code=400)
        ip = request.client.host if request.client else "?"
        try:
            apikeys.check_not_blocked(ip)
            k = apikeys.authenticate(_bearer(authorization), "mcp")
        except ApiError as e:
            if e.status in (401, 403):
                apikeys.note_failure(ip, "mcp", e.code)
            return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": e.message}}, status_code=e.status)
        import asyncio
        result = await asyncio.get_running_loop().run_in_executor(None, mcp.handle, payload, k)
        if result is None:
            return Response(status_code=202)
        return JSONResponse(result)

    @app.get("/mcp", include_in_schema=False)
    def mcp_get():
        return Response(status_code=405, headers={"Allow": "POST"})

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"] if lan_on() else ["localhost", "127.0.0.1", "[::1]", "::1", "testserver"])
    return app


def _origin_ok(origin: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(origin).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or lan_on()


# ---------- cycle de vie ----------
class _Runner:
    def __init__(self) -> None:
        self.server = None
        self.thread: threading.Thread | None = None
        self.error = ""
        self.bound = ("", 0)

    def running(self) -> bool:
        return bool(self.thread and self.thread.is_alive() and self.server and self.server.started)

    def start(self) -> None:
        import uvicorn
        host, prt = ("0.0.0.0" if lan_on() else "127.0.0.1"), port()
        cfg = uvicorn.Config(build_app(), host=host, port=prt, log_level="warning", access_log=False)
        self.server = uvicorn.Server(cfg)
        self.error, self.bound = "", (host, prt)

        def run() -> None:
            try:
                self.server.run()
            except Exception as e:  # port occupé, etc.
                self.error = str(e)
                log.warning("serveur API : %s", e)
            if not self.server.started and not self.error:
                self.error = f"Impossible d'ouvrir le port {prt} (déjà utilisé ?)."

        self.thread = threading.Thread(target=run, daemon=True, name="mailia-api")
        self.thread.start()
        for _ in range(40):
            if self.server.started or not self.thread.is_alive():
                break
            time.sleep(0.1)

    def stop(self) -> None:
        if self.server:
            self.server.should_exit = True
        if self.thread:
            self.thread.join(timeout=5)
        self.server, self.thread = None, None


runner = _Runner()


def ensure() -> dict:
    """Met le serveur en accord avec les réglages : démarré si au moins un des deux interrupteurs est actif, arrêté sinon."""
    want = rest_on() or mcp_on()
    cur = (("0.0.0.0" if lan_on() else "127.0.0.1"), port())
    if runner.running() and (not want or runner.bound != cur):
        runner.stop()
    if want and not runner.running():
        runner.start()
    return status()


def status() -> dict:
    host = "127.0.0.1" if not lan_on() else __import__("mailia.scan", fromlist=["lan_ip"]).lan_ip()
    return {"running": runner.running(), "error": runner.error, "base": f"http://{host}:{port()}", "rest": rest_on(), "mcp": mcp_on(), "lan": lan_on()}
