"""Boîte IMAP/SMTP classique (IONOS) : synchro par UID, actions et notifications IDLE."""
import hashlib
import logging
import re
import smtplib
from datetime import timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from imap_tools import AND, MailBox, MailBoxStartTls, MailMessageFlags

from .. import oauth, sendstats, tlsutil
from ..config import AccountCfg
from ..textutil import html_to_text, normalize
from .base import Delta, FolderInfo, Msg

log = logging.getLogger(__name__)

SPECIAL = {"\\Sent": "sent", "\\Trash": "trash", "\\Drafts": "drafts", "\\Junk": "junk", "\\Archive": "archive"}


def _conv(m) -> str:
    """Un fil = la racine de la chaîne References (sinon In-Reply-To, sinon le message lui-même) ;
    à défaut d'en-têtes exploitables, l'objet nettoyé de ses « RE: / TR: »."""
    h = m.headers or {}
    one = lambda k: " ".join(h.get(k, ())).split()
    chain = one("references") or one("in-reply-to") or one("message-id")
    if chain:
        return "msgid:" + chain[0]
    subject = re.sub(r"^\s*((re|tr|fwd?|rép?)\s*:\s*)+", "", (m.subject or "").lower()).strip()
    return "subj:" + hashlib.sha1(subject.encode()).hexdigest()[:16]


def thread_headers(msg, message_id: str, references: str = "") -> None:
    """En-têtes standard (RFC 5322) qui rattachent une réponse ou un transfert à son original : In-Reply-To = identifiant du message d'origine,
    References = chaîne des identifiants précédents + celui-là. Les messageries du destinataire s'en servent pour regrouper la conversation."""
    mid = message_id.strip()
    if not mid:
        return
    chain = [x for x in references.split() if x]
    if mid not in chain:
        chain.append(mid)
    for h in ("In-Reply-To", "References"):
        if h in msg:
            del msg[h]
    msg["In-Reply-To"] = mid
    msg["References"] = " ".join(chain[-20:])  # on borne la chaîne, comme le font les clients courants


def _atts(m) -> list:
    return [{"id": str(n), "name": a.filename or "(sans nom)", "size": a.size or 0, "type": a.content_type or ""}
            for n, a in enumerate(m.attachments) if a.content_disposition != "inline" or a.filename]


def _msg(m) -> Msg:
    text = normalize(m.text) if m.text and m.text.strip() else html_to_text(m.html or "")
    dt = m.date if m.date.tzinfo else m.date.replace(tzinfo=timezone.utc)
    return Msg(
        uid=m.uid,
        subject=m.subject or "(sans objet)",
        sender=m.from_ or "",
        sender_name=m.from_values.name if m.from_values else "",
        to=", ".join(m.to),
        cc=", ".join(m.cc),
        date=dt.astimezone(timezone.utc).isoformat(),
        body=text[:20000],
        is_read=MailMessageFlags.SEEN in m.flags,
        has_att=bool(_atts(m)),
        attachments=_atts(m),
        conv=_conv(m),
        html=(m.html or "") if len(m.html or "") <= 600_000 else "",
        flagged=MailMessageFlags.FLAGGED in m.flags,
        verb=2 if "$Forwarded" in m.flags else 1 if MailMessageFlags.ANSWERED in m.flags else 0,
    )


class ImapProvider:
    def __init__(self, cfg: AccountCfg) -> None:
        self.cfg = cfg
        self._sig: dict[str, tuple] = {}  # dernière « empreinte » vue par dossier (UIDNEXT, MESSAGES, UNSEEN)
        self._polls: dict[str, int] = {}  # le drapeau ne change pas l'empreinte : relecture complète de temps en temps

    def _box(self, folder: str = "INBOX") -> MailBox:
        ctx = tlsutil.context(self.cfg.tls_insecure)  # certificat toujours vérifié, sauf choix explicite de l'utilisateur
        box = MailBoxStartTls(self.cfg.host, self.cfg.port, ssl_context=ctx) if self.cfg.tls == "starttls" else MailBox(self.cfg.host, self.cfg.port, ssl_context=ctx)
        if oauth.is_oauth(self.cfg.password):  # connexion moderne (Microsoft, Google) : jeton d'accès, pas de mot de passe
            return box.xoauth2(self.cfg.login, oauth.access_token(self.cfg.key, self.cfg.password), initial_folder=folder)
        return box.login(self.cfg.login, self.cfg.password, initial_folder=folder)

    # ---------- dossiers ----------
    def list_folders(self) -> list[FolderInfo]:
        out: list[FolderInfo] = []
        with self._box() as mb:
            for f in mb.folder.list():
                if "\\Noselect" in f.flags:
                    continue
                role = next((r for fl, r in SPECIAL.items() if fl in f.flags), "")
                if f.name.upper() == "INBOX":
                    out.append(FolderInfo("INBOX", "Boîte de réception", "Boîte de réception", "inbox"))
                    continue
                if not role and "archive" in f.name.lower():
                    role = "archive"
                out.append(FolderInfo(f.name, f.name.split(f.delim)[-1], f.name.replace(f.delim, "/"), role))
        return out

    # ---------- synchro ----------
    def sync_folder(self, key: str, state: str | None, known: dict, limit: int) -> Delta:
        """Compare les `limit` derniers messages du serveur (en-têtes seulement : léger) à la base locale,
        puis ne télécharge en entier que les nouveaux."""
        d = Delta()
        with self._box(key) as mb:
            st = mb.folder.status(key)
            uidvalidity = str(st.get("UIDVALIDITY", ""))
            sig = (uidvalidity, st.get("UIDNEXT"), st.get("MESSAGES"), st.get("UNSEEN"))
            self._polls[key] = self._polls.get(key, 0) + 1
            if state == uidvalidity and self._sig.get(key) == sig and self._polls[key] % 8:
                return Delta(new_state=state)  # rien n'a bougé : aucun en-tête à télécharger
            if state is not None and state != uidvalidity:
                d.reset = True  # les identifiants du serveur ont changé : les anciens ne valent plus rien
                d.deleted = list(known)
                known = {}
            headers = list(mb.fetch(limit=limit, reverse=True, headers_only=True, mark_seen=False, bulk=True))
            window = {m.uid: MailMessageFlags.SEEN in m.flags for m in headers}
            flags = {m.uid: MailMessageFlags.FLAGGED in m.flags for m in headers}
            new_uids = [u for u in window if u not in known]
            for uid, was_read in ((u, k[0]) for u, k in known.items()):
                if uid in window and bool(was_read) != window[uid]:
                    d.read_changes.append((uid, window[uid]))
            for uid, k in known.items():
                if uid in flags and bool(k[2]) != flags[uid]:
                    d.flag_changes.append((uid, flags[uid]))
            if window:
                lowest = min(int(u) for u in window)
                d.deleted = [u for u in known if u not in window and int(u) >= lowest]
            if new_uids:
                for m in mb.fetch(AND(uid=new_uids), mark_seen=False, bulk=True):
                    d.upserts.append(_msg(m))
        self._sig[key] = sig
        d.new_state = uidvalidity
        return d

    # ---------- actions ----------
    def set_read(self, key: str, uid: str, read: bool) -> None:
        with self._box(key) as mb:
            mb.flag(uid, MailMessageFlags.SEEN, read)

    def mark_verb(self, key: str, uid: str, verb: int) -> None:
        """Marques standard IMAP : \\Answered (répondu) ou $Forwarded (transféré), comprises par la plupart des clients."""
        with self._box(key) as mb:
            mb.flag(uid, "$Forwarded" if verb == 2 else MailMessageFlags.ANSWERED, True)

    def set_flag(self, key: str, uid: str, on: bool) -> None:
        with self._box(key) as mb:
            mb.flag(uid, MailMessageFlags.FLAGGED, on)

    def move(self, key: str, uid: str, dest_key: str) -> None:
        with self._box(key) as mb:
            mb.move(uid, dest_key)

    def delete(self, key: str, uid: str) -> None:
        with self._box(key) as mb:
            trash = next((f.name for f in mb.folder.list() if "\\Trash" in f.flags), None)
            if not trash:
                raise RuntimeError("Aucun dossier Corbeille trouvé sur ce compte")
            mb.move(uid, trash)  # corbeille, pas de suppression définitive

    def _full(self, key: str, uid: str):
        with self._box(key) as mb:
            for m in mb.fetch(AND(uid=[uid]), mark_seen=False):
                return m
        raise RuntimeError("Message introuvable sur le serveur")

    def threading(self, key: str, uids: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        with self._box(key) as mb:
            for n in range(0, len(uids), 200):
                for m in mb.fetch(AND(uid=uids[n:n + 200]), headers_only=True, mark_seen=False, bulk=True):
                    out[m.uid] = _conv(m)
        return out

    def details(self, key: str, uid: str) -> dict:
        m = self._full(key, uid)
        return {"to": ", ".join(m.to), "cc": ", ".join(m.cc), "bcc": " ".join(m.headers.get("bcc", ())), "attachments": _atts(m),
                "html": (m.html or "") if len(m.html or "") <= 600_000 else "",
                "text": (m.text or "").strip()[:20000],
                "unsub": __import__("mailia.unsub", fromlist=["parse"]).parse(
                    " ".join(m.headers.get("list-unsubscribe", ())), " ".join(m.headers.get("list-unsubscribe-post", ())), m.html or ""),
                "auth": __import__("mailia.authcheck", fromlist=["parse"]).parse({k.lower(): list(v) for k, v in m.headers.items()})}

    def inline_images(self, key: str, uid: str, cids: list[str]) -> dict[str, tuple[bytes, str]]:
        out: dict[str, tuple[bytes, str]] = {}
        wanted = {c.strip("<>").lower() for c in cids}
        for a in self._full(key, uid).attachments:
            cid = (a.content_id or "").strip("<>").lower()
            if cid in wanted and (a.size or 0) <= 1_500_000 and len(out) < 12:
                out[cid] = (a.payload, a.content_type or "image/png")
        return out

    def attachment(self, key: str, uid: str, att_id: str) -> tuple[bytes, str]:
        m = self._full(key, uid)
        a = m.attachments[int(att_id)]
        return a.payload, a.filename or "piece-jointe"

    def build(self, to: str, subject: str, body: str, cc: str = "", attachments: list | None = None,
              html: str | None = None, inline: list | None = None, bcc: str = "") -> EmailMessage:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.cfg.email, to, subject
        if cc.strip():
            msg["Cc"] = cc
        if bcc.strip():
            msg["Bcc"] = bcc  # send_message() l'utilise pour l'envoi puis le retire ; la copie « Envoyés » le garde
        # identifiant à la manière des messageries : @ le domaine de l'expéditeur, pas le nom de l'ordinateur (« …@macbook.home » ressemble à un
        # envoi automatique et pèse contre le message dans les filtres anti-spam)
        domain = self.cfg.email.rpartition("@")[2] or None
        msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid(domain=domain)
        msg.set_content(body)  # version texte (secours)
        if html:
            msg.add_alternative(html, subtype="html")
            page = msg.get_body(preferencelist=("html",))
            for cid, data, mime, name in inline or []:  # images affichées dans le corps du message
                maintype, _, subtype = mime.partition("/")
                page.add_related(data, maintype=maintype, subtype=subtype, cid=f"<{cid}>", filename=name)
        for name, data, ctype in attachments or []:
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            msg.add_attachment(data, maintype=maintype or "application", subtype=subtype or "octet-stream", filename=name)
        return msg

    def smtp(self):
        """Connexion SMTP authentifiée : SSL direct sur le port 465, sinon STARTTLS (587 ou autre)."""
        ctx = tlsutil.context(self.cfg.tls_insecure)
        if self.cfg.smtp_port == 465:
            s = smtplib.SMTP_SSL(self.cfg.smtp_host, 465, timeout=30, context=ctx)
        else:
            s = smtplib.SMTP(self.cfg.smtp_host, self.cfg.smtp_port, timeout=30)
            s.starttls(context=ctx)
        if oauth.is_oauth(self.cfg.password):
            s.ehlo()
            token = oauth.access_token(self.cfg.key, self.cfg.password)
            s.auth("XOAUTH2", lambda challenge=None: oauth.xoauth2_string(self.cfg.login, token), initial_response_ok=True)
            return s
        s.login(self.cfg.login, self.cfg.password)
        return s

    def send(self, to: str, subject: str, body: str, cc: str = "", attachments: list | None = None,
             html: str | None = None, inline: list | None = None, bcc: str = "", ref: tuple | None = None) -> None:
        msg = self.build(to, subject, body, cc, attachments, html, inline, bcc)
        if ref:
            try:
                orig = self._full(ref[1], ref[2])
                h = orig.headers or {}
                thread_headers(msg, " ".join(h.get("message-id", ())), " ".join(h.get("references", ())))
                sendstats.record("threaded")
            except Exception:
                log.warning("en-têtes de conversation non ajoutés (le message part quand même)", exc_info=True)
                sendstats.record("fallback", subject)
        with self.smtp() as s:
            s.send_message(msg)
        # Copie dans « Envoyés » (SMTP ne le fait pas seul)
        try:
            with self._box() as mb:
                sent = next((f.name for f in mb.folder.list() if "\\Sent" in f.flags), None)
                if sent:
                    mb.append(msg.as_bytes(), sent, flag_set=[MailMessageFlags.SEEN])
                else:
                    sendstats.record("nocopy", subject)  # aucun dossier « Envoyés » repéré : le message est parti mais n'y figure pas
        except Exception:
            log.warning("copie dans « Envoyés » impossible (le message est bien parti)", exc_info=True)
            sendstats.record("nocopy", subject)

    # ---------- notifications en direct (IMAP IDLE sur la boîte de réception) ----------
    def wait_for_changes(self, keys: list[str], timeout: int) -> bool:
        with self._box("INBOX") as mb:
            return bool(mb.idle.wait(timeout=timeout))
