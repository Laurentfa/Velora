"""Exchange hébergé (EWS, NTLM) via exchangelib : synchro incrémentale, actions et notifications en direct."""
import json
import logging
from datetime import datetime, timezone

from exchangelib import Account, Configuration, Credentials, DELEGATE, FileAttachment, HTMLBody, Mailbox, Message
from exchangelib.errors import ErrorInvalidSyncStateData
from exchangelib.extended_properties import ExtendedProperty
from exchangelib.items import CalendarItem, MeetingCancellation, MeetingRequest, MeetingResponse
from exchangelib.folders.collections import FolderCollection
from exchangelib.properties import ItemId

from .. import sendstats
from ..config import AccountCfg, env
from ..textutil import html_to_text, normalize
from .base import Delta, FolderInfo, Msg



log = logging.getLogger("ews")


class _FlagStatus(ExtendedProperty):
    """PidTagFlagStatus : 0 = aucun drapeau, 2 = drapeau (c'est ce qu'Outlook lit pour afficher le drapeau rouge)."""
    property_tag = 0x1090
    property_type = "Integer"


class _FollowupIcon(ExtendedProperty):
    """PidTagFollowupIcon : 6 = drapeau rouge."""
    property_tag = 0x1095
    property_type = "Integer"


class _LastVerb(ExtendedProperty):
    """PidTagLastVerbExecuted : 102 = réponse, 103 = réponse à tous, 104 = transfert (c'est ce qu'Outlook lit pour sa petite icône)."""
    property_tag = 0x1081
    property_type = "Integer"


class _IconIndex(ExtendedProperty):
    """PidTagIconIndex : 261 = « répondu », 262 = « transféré »."""
    property_tag = 0x1080
    property_type = "Integer"


class _SupportMark(ExtendedProperty):
    """Propriété cachée posée sur nos brouillons de support (voir _send_threaded) : sert à retrouver ceux qu'un arrêt brutal aurait laissés."""
    distinguished_property_set_id = "PublicStrings"
    property_name = "MailIASupport"
    property_type = "String"


Message.register("support_mark", _SupportMark)
Message.register("last_verb", _LastVerb)
Message.register("icon_index", _IconIndex)
Message.register("flag_status", _FlagStatus)
Message.register("followup_icon", _FollowupIcon)

FIELDS = ["subject", "sender", "to_recipients", "cc_recipients", "datetime_received", "is_read", "body", "has_attachments", "attachments", "conversation_id", "flag_status", "last_verb",
          "start", "end", "location"]  # les trois derniers : invitations à une réunion
EVENTS = ["NewMailEvent", "CreatedEvent", "DeletedEvent", "ModifiedEvent", "MovedEvent"]


def _utc_iso(dt) -> str:
    return datetime.fromtimestamp(dt.timestamp(), tz=timezone.utc).isoformat()


def _addrs(recipients) -> str:
    return ", ".join(r.email_address for r in (recipients or []) if r.email_address)


def _atts(item) -> list:
    """Pièces jointes réelles (les images incrustées dans la signature sont ignorées)."""
    out = []
    for a in item.attachments or []:
        if getattr(a, "is_inline", False):
            continue
        out.append({"id": a.attachment_id.id if a.attachment_id else "", "name": a.name or "(sans nom)",
                    "size": a.size or 0, "type": getattr(a, "content_type", "") or ""})
    return out


def _html(i) -> str:
    return str(i.body) if isinstance(i.body, HTMLBody) and len(str(i.body)) <= 600_000 else ""


MEETING_TYPES = (MeetingRequest, MeetingResponse, MeetingCancellation)


def _meeting(i) -> str:
    """Invitation, réponse ou annulation de réunion : de quoi afficher une carte « quand, où » et répondre."""
    if not isinstance(i, MEETING_TYPES):
        return ""
    kind = "request" if isinstance(i, MeetingRequest) else "cancel" if isinstance(i, MeetingCancellation) else "response"
    iso = lambda d: _utc_iso(d) if d else ""
    return json.dumps({"kind": kind, "start": iso(getattr(i, "start", None)), "end": iso(getattr(i, "end", None)),
                       "location": getattr(i, "location", "") or "", "old": bool(getattr(i, "is_out_of_date", False)),
                       "response": str(getattr(i, "response_type", "") or "")})


def _to_msg(i) -> Msg | None:
    if not isinstance(i, (Message, *MEETING_TYPES)) or i.datetime_received is None:
        return None
    body = str(i.body or "")
    text = html_to_text(body) if "<" in body[:2000] and ">" in body[:2000] else normalize(body)
    s = i.sender
    return Msg(
        uid=i.id,
        subject=i.subject or "(sans objet)",
        sender=(s.email_address if s else "") or "",
        sender_name=(s.name if s else "") or "",
        to=_addrs(i.to_recipients),
        cc=_addrs(i.cc_recipients),
        date=_utc_iso(i.datetime_received),
        body=text[:20000],
        is_read=bool(i.is_read),
        has_att=bool(i.has_attachments) or bool(_atts(i)),
        # la synchro incrémentale ne renvoie pas toujours le détail des pièces jointes : None = à charger à l'ouverture
        attachments=_atts(i) or (None if i.has_attachments else []),
        conv=("ews:" + i.conversation_id.id) if getattr(i, "conversation_id", None) else None,
        html=_html(i),
        flagged=getattr(i, "flag_status", None) == 2,
        verb={102: 1, 103: 1, 104: 2}.get(getattr(i, "last_verb", None) or 0, 0),
        meeting=_meeting(i),
    )


SEARCH_FIELDS = ["subject", "sender", "to_recipients", "cc_recipients", "datetime_received", "is_read", "has_attachments", "conversation_id"]


def _stub(i) -> Msg | None:
    """Résultat de recherche sur le serveur : en-têtes seulement (le texte et les pièces jointes sont chargés à l'ouverture du message)."""
    if not isinstance(i, (Message, *MEETING_TYPES)) or i.datetime_received is None:
        return None
    s = i.sender
    return Msg(
        uid=i.id, subject=i.subject or "(sans objet)", sender=(s.email_address if s else "") or "", sender_name=(s.name if s else "") or "",
        to=_addrs(i.to_recipients), cc=_addrs(i.cc_recipients), date=_utc_iso(i.datetime_received), body="", is_read=bool(i.is_read),
        has_att=bool(i.has_attachments), attachments=None, conv=("ews:" + i.conversation_id.id) if getattr(i, "conversation_id", None) else None, html=None)


_COND = {
    "contains_sender_strings": ("l'expéditeur contient", "list"), "contains_subject_strings": ("l'objet contient", "list"),
    "contains_body_strings": ("le texte contient", "list"), "contains_subject_or_body_strings": ("l'objet ou le texte contient", "list"),
    "contains_header_strings": ("l'en-tête contient", "list"), "contains_recipient_strings": ("un destinataire contient", "list"),
    "from_addresses": ("il vient de", "addr"), "sent_to_addresses": ("il est envoyé à", "addr"), "categories": ("catégorie", "list"),
    "has_attachments": ("il a une pièce jointe", "flag"), "sent_to_me": ("il m'est adressé", "flag"), "sent_only_to_me": ("il m'est adressé à moi seul", "flag"),
    "sent_cc_me": ("je suis en copie", "flag"), "sent_to_or_cc_me": ("il m'est adressé ou j'y suis en copie", "flag"),
    "not_sent_to_me": ("il ne m'est pas adressé", "flag"), "is_meeting_request": ("c'est une invitation à une réunion", "flag"),
    "is_meeting_response": ("c'est une réponse à une réunion", "flag"), "is_automatic_reply": ("c'est une réponse automatique", "flag"),
    "is_ndr": ("c'est un message d'échec de distribution", "flag"), "is_read_receipt": ("c'est un accusé de lecture", "flag"),
    "is_approval_request": ("c'est une demande d'approbation", "flag"), "is_voicemail": ("c'est un message vocal", "flag"),
    "importance": ("importance", "val"), "sensitivity": ("confidentialité", "val"), "flagged_for_action": ("marqué pour suivi", "val"),
}
_ACT = {
    "move_to_folder": ("le déplacer vers", "folder"), "copy_to_folder": ("en copier une version vers", "folder"),
    "delete": ("le mettre à la corbeille", "flag"), "permanent_delete": ("le supprimer définitivement", "flag"),
    "mark_as_read": ("le marquer comme lu", "flag"), "mark_importance": ("lui donner l'importance", "val"),
    "assign_categories": ("lui attribuer la catégorie", "list"), "stop_processing_rules": ("ne pas appliquer les règles suivantes", "flag"),
    "forward_to_recipients": ("le transférer à", "addr"), "forward_as_attachment_to_recipients": ("le transférer en pièce jointe à", "addr"),
    "redirect_to_recipients": ("le rediriger vers", "addr"), "send_sms_alert_to_recipients": ("envoyer une alerte SMS à", "addr"),
    "server_reply_with_message": ("répondre automatiquement", "flag"),
}


def _describe(obj, table: dict, folders: dict) -> list[str]:
    """Traduit les champs renseignés d'une condition, exception ou action de règle Exchange. Ce qui n'est pas connu est signalé, jamais ignoré."""
    if obj is None:
        return []
    out = []
    for f in type(obj).FIELDS:
        v = getattr(obj, f.name)
        if v is None or v is False or (isinstance(v, list) and not v):
            continue
        label, kind = table.get(f.name, (f.name.replace("_", " "), "val"))
        if kind == "flag":
            out.append(label)
        elif kind == "folder":
            fid = getattr(getattr(v, "folder_id", None), "id", None)
            dist = getattr(v, "distinguished_folder_id", None)
            out.append(f"{label} « {folders.get(fid) or (getattr(dist, 'id', None) if dist else None) or 'un dossier'} »")
        elif kind == "addr":
            items = v if isinstance(v, list) else [v]
            out.append(f"{label} " + " ou ".join("« " + str(getattr(x, "name", None) or getattr(x, "email_address", "") or x) + " »" for x in items))
        elif kind == "list":
            out.append(f"{label} " + " ou ".join(f"« {x} »" for x in (v if isinstance(v, list) else [v])))
        else:
            out.append(f"{label} : {v}")
    return out


def _stat(kind: str, subject: str = "") -> None:
    sendstats.record(kind, subject)


class _AlreadySent(Exception):
    """L'envoi du brouillon a échoué sans qu'on sache si le message est parti : pas de nouvel essai automatique."""


class EwsProvider:
    def __init__(self, cfg: AccountCfg) -> None:
        self.cfg = cfg
        self._account: Account | None = None
        self._fmap: dict | None = None

    @property
    def account(self) -> Account:
        if self._account is None:
            creds = Credentials(self.cfg.login, self.cfg.password)
            if self.cfg.host:
                conf = Configuration(server=self.cfg.host, credentials=creds)
                self._account = Account(self.cfg.email, config=conf, autodiscover=False, access_type=DELEGATE)
            else:  # aucun serveur indiqué : détection automatique à partir de l'adresse e-mail
                self._account = Account(self.cfg.email, credentials=creds, autodiscover=True, access_type=DELEGATE)
        return self._account

    # ---------- dossiers ----------
    def list_folders(self) -> list[FolderInfo]:
        a = self.account
        roles = {a.inbox.id: "inbox", a.trash.id: "trash", a.sent.id: "sent", a.drafts.id: "drafts", a.junk.id: "junk"}
        root = a.msg_folder_root
        base = root.absolute
        fmap = {"INBOX": a.inbox}
        infos = [FolderInfo("INBOX", "Boîte de réception", "Boîte de réception", "inbox")]
        for f in root.walk():
            if f.id == root.id or f.id == a.inbox.id:
                continue
            if not (f.folder_class or "IPF.Note").startswith("IPF.Note"):
                continue
            path = f.absolute[len(base):].strip("/") or f.name
            role = roles.get(f.id, "")
            if not role and "archive" in f.name.lower():
                role = "archive"
            fmap[f.id] = f
            infos.append(FolderInfo(f.id, f.name, path, role))
        self._fmap = fmap
        return infos

    def _folder(self, key: str):
        if key == "INBOX":
            return self.account.inbox
        if self._fmap is None or key not in self._fmap:
            self.list_folders()
        if key not in self._fmap:
            raise RuntimeError("Dossier introuvable sur le serveur")
        return self._fmap[key]

    # ---------- synchro incrémentale ----------
    def sync_folder(self, key: str, state: str | None, known: dict, limit: int) -> Delta:
        folder = self._folder(key)
        if state:
            try:
                return self._changes(folder, state, known)
            except ErrorInvalidSyncStateData:
                state = None  # état périmé côté serveur : on repart de zéro
        d = self._initial(folder, known, limit)
        d.reset = True
        return d

    def _initial(self, folder, known: dict, limit: int) -> Delta:
        # 1) l'état de synchro d'abord (sans rapatrier le contenu), 2) puis les messages récents :
        #    ce qui arrive entre les deux sera livré par la synchro suivante
        for _ in folder.sync_items(only_fields=["is_read"], max_changes_returned=512):
            pass
        new_state = folder.item_sync_state
        recent = folder.all().order_by("-datetime_received").only(*FIELDS)[:limit]
        msgs = [m for m in (_to_msg(i) for i in recent) if m]
        d = Delta(upserts=msgs, new_state=new_state)
        if msgs:  # réconciliation : ce qu'on avait dans cette plage de dates et qui n'est plus sur le serveur
            seen = {m.uid for m in msgs}
            oldest = min(m.date for m in msgs)
            d.deleted = [u for u, (_, date, *_x) in known.items() if u not in seen and date >= oldest]
        return d

    def _changes(self, folder, state: str, known: dict) -> Delta:
        d = Delta()
        for change, item in folder.sync_items(sync_state=state, only_fields=FIELDS, max_changes_returned=100):
            if change == "create":
                m = _to_msg(item)
                if m:
                    if not m.body:  # la synchro incrémentale livre parfois le message sans corps, sans destinataires ni pièces jointes : à charger
                        m.attachments, m.html = None, None
                    d.upserts.append(m)
            elif change == "update":
                m = _to_msg(item)
                if m and m.uid in known:
                    d.upserts.append(m)
            elif change == "read_flag_change":
                item_id, is_read = item
                d.read_changes.append((item_id.id, bool(is_read)))
            elif change == "delete":
                d.deleted.append(item.id)
        d.new_state = folder.item_sync_state
        return d

    def search(self, text: str, folder_keys: list[str], per_folder: int = 100) -> list[tuple[str, Msg]]:
        """Recherche plein texte faite par Exchange lui-même (objet, expéditeur, corps) : un dossier à la fois, car le serveur refuse de chercher
        dans plusieurs dossiers d'un coup. Un dossier qui échoue n'empêche pas les autres."""
        out: list[tuple[str, Msg]] = []
        for key in folder_keys:
            try:
                found = self._folder(key).filter(text).only(*SEARCH_FIELDS).order_by("-datetime_received")[:per_folder]
                out += [(key, m) for m in (_stub(i) for i in found) if m]
            except Exception as e:
                log.warning("recherche dans %s : %s", key, e)
        return out

    # ---------- actions ----------
    def _item(self, uid: str):
        for item in self.account.fetch(ids=[ItemId(id=uid)]):
            if isinstance(item, Exception):
                raise item
            return item
        raise RuntimeError("Message introuvable sur le serveur")

    def set_read(self, key: str, uid: str, read: bool) -> None:
        item = self._item(uid)
        item.is_read = read
        item.save(update_fields=["is_read"])

    def respond_meeting(self, key: str, uid: str, action: str) -> None:
        """Répond à une invitation (envoie la réponse à l'organisateur) : accept | tentative | decline."""
        item = self._item(uid)
        if not isinstance(item, MeetingRequest):
            raise RuntimeError("Ce message n'est pas une invitation à une réunion.")
        {"accept": item.accept, "tentative": item.tentatively_accept, "decline": item.decline}[action]()

    def create_event(self, title: str, start: datetime, end: datetime, location: str = "", body: str = "") -> None:
        """Ajoute un rendez-vous à l'agenda (sans invité : aucune invitation n'est envoyée). Heures naïves = fuseau du compte."""
        from exchangelib import EWSDateTime
        tz = self.account.default_timezone
        mk = lambda d: EWSDateTime(d.year, d.month, d.day, d.hour, d.minute, tzinfo=tz)
        CalendarItem(account=self.account, folder=self.account.calendar, subject=title, start=mk(start), end=mk(end),
                     location=location or None, body=body or None).save()

    def mark_verb(self, folder: str, uid: str, verb: int) -> None:
        """Pose sur le message d'origine la marque « répondu » (1) ou « transféré » (2), comme Outlook."""
        item = self._folder(folder).get(id=uid)
        item.last_verb, item.icon_index = (104, 262) if verb == 2 else (102, 261)
        item.save(update_fields=["last_verb", "icon_index"])

    def rules_get(self) -> list[dict]:
        """Règles de boîte de réception du serveur, en lecture seule, décrites en français."""
        from exchangelib.services import GetInboxRules
        a = self.account
        self.list_folders()
        names = {i: f.name for i, f in (self._fmap or {}).items()}
        out = []
        for r in GetInboxRules(account=a).call(mailbox=a.primary_smtp_address):
            out.append({"name": r.display_name or "(sans nom)", "enabled": bool(r.is_enabled), "priority": r.priority,
                        "unsupported": bool(r.is_not_supported or r.is_in_error),
                        "if": _describe(r.conditions, _COND, names), "except": _describe(r.exceptions, _COND, names),
                        "then": _describe(r.actions, _ACT, names)})
        return out

    def oof_get(self) -> dict:
        o = self.account.oof_settings
        tz = self.account.default_timezone
        loc = lambda d: d.astimezone(tz).strftime("%Y-%m-%dT%H:%M") if d else ""
        return {"state": o.state, "audience": o.external_audience or "All", "start": loc(o.start), "end": loc(o.end),
                "internal": o.internal_reply or "", "external": o.external_reply or ""}

    def oof_set(self, state: str, audience: str, start: datetime | None, end: datetime | None, internal: str, external: str) -> None:
        from exchangelib import EWSDateTime
        from exchangelib.settings import OofSettings
        tz = self.account.default_timezone
        mk = lambda d: EWSDateTime(d.year, d.month, d.day, d.hour, d.minute, tzinfo=tz) if d else None
        self.account.oof_settings = OofSettings(state=state, external_audience=audience, start=mk(start), end=mk(end),
                                                internal_reply=internal or None, external_reply=external or None)

    def contacts(self) -> tuple[list[tuple[str, str]], list[tuple[str, list[tuple[str, str]]]]]:
        """Carnet Exchange (lecture seule) : ((adresse, nom) des contacts, (nom, membres) des listes de distribution)."""
        people: list[tuple[str, str]] = []
        lists: list[tuple[str, list[tuple[str, str]]]] = []
        for c in self.account.contacts.all()[:3000]:
            if hasattr(c, "email_addresses"):
                for e in c.email_addresses or []:
                    if e.email:
                        people.append((e.email, c.display_name or ""))
            elif hasattr(c, "members"):
                members = [(m.mailbox.email_address, m.mailbox.name or "") for m in (c.members or [])
                           if getattr(m, "mailbox", None) and m.mailbox.email_address]
                if members:
                    lists.append((getattr(c, "display_name", None) or getattr(c, "subject", "") or "Liste", members))
        return people, lists

    def directory(self, query: str) -> list[tuple[str, str]]:
        """Annuaire de l'entreprise (lecture seule) : (adresse, nom) des personnes dont le nom ressemble à la recherche."""
        out: list[tuple[str, str]] = []
        for r in self.account.protocol.resolve_names(names=[query], return_full_contact_data=False):
            mb = r[0] if isinstance(r, tuple) else r
            email = getattr(mb, "email_address", None)
            if email:
                out.append((email, getattr(mb, "name", "") or ""))
        return out

    def set_flag(self, key: str, uid: str, on: bool) -> None:
        item = self._item(uid)
        item.flag_status = 2 if on else 0
        item.followup_icon = 6 if on else None
        item.save(update_fields=["flag_status", "followup_icon"])

    def move(self, key: str, uid: str, dest_key: str) -> None:
        self._item(uid).move(self._folder(dest_key))

    def delete(self, key: str, uid: str) -> None:
        self._item(uid).move_to_trash()  # corbeille, pas de suppression définitive

    def threading(self, key: str, uids: list[str]) -> dict[str, str]:
        """uid -> identifiant de conversation, pour les messages synchronisés avant l'arrivée des conversations."""
        out: dict[str, str] = {}
        for n in range(0, len(uids), 50):
            for item in self.account.fetch(ids=[ItemId(id=u) for u in uids[n:n + 50]], only_fields=["conversation_id"]):
                if not isinstance(item, Exception) and getattr(item, "conversation_id", None):
                    out[item.id] = "ews:" + item.conversation_id.id
        return out

    def details(self, key: str, uid: str) -> dict:
        i = self._item(uid)
        body = str(i.body or "")
        text = html_to_text(body) if "<" in body[:2000] and ">" in body[:2000] else normalize(body)
        hdr = {h.name.lower(): h.value for h in (getattr(i, "headers", None) or [])}
        multi: dict[str, list[str]] = {}
        for h in getattr(i, "headers", None) or []:
            multi.setdefault(h.name.lower(), []).append(h.value)
        from .. import authcheck, unsub
        return {"to": _addrs(i.to_recipients), "cc": _addrs(i.cc_recipients), "bcc": _addrs(i.bcc_recipients), "attachments": _atts(i), "html": _html(i),
                "text": text[:20000],
                "unsub": unsub.parse(hdr.get("list-unsubscribe", ""), hdr.get("list-unsubscribe-post", ""), _html(i)),
                "auth": authcheck.parse(multi)}

    def inline_images(self, key: str, uid: str, cids: list[str]) -> dict[str, tuple[bytes, str]]:
        out: dict[str, tuple[bytes, str]] = {}
        wanted = {c.strip("<>").lower() for c in cids}
        for a in self._item(uid).attachments or []:
            cid = (getattr(a, "content_id", None) or "").strip("<>").lower()
            if cid in wanted and hasattr(a, "content") and (a.size or 0) <= 1_500_000 and len(out) < 12:
                out[cid] = (bytes(a.content), a.content_type or "image/png")
        return out

    def attachment(self, key: str, uid: str, att_id: str) -> tuple[bytes, str]:
        for a in self._item(uid).attachments or []:
            if a.attachment_id and a.attachment_id.id == att_id and hasattr(a, "content"):
                return bytes(a.content), a.name or "piece-jointe"
        raise RuntimeError("Pièce jointe introuvable (ou message joint non téléchargeable)")

    def send(self, to: str, subject: str, body: str, cc: str = "", attachments: list | None = None,
             html: str | None = None, inline: list | None = None, bcc: str = "", ref: tuple | None = None) -> None:
        """`ref` = (« reply » ou « forward », dossier, uid) du message d'origine : le message part alors dans SA conversation."""
        box = lambda s: [Mailbox(email_address=a.strip()) for a in s.replace(";", ",").split(",") if a.strip()]
        if ref and env("MAILIA_THREADED_SEND", "1") != "0":  # MAILIA_THREADED_SEND=0 : retour à l'envoi simple (dépannage)
            try:
                if self._send_threaded(ref, subject, body, box(to), box(cc), box(bcc), attachments, html, inline):
                    _stat("threaded")
                    return
            except _AlreadySent:
                _stat("uncertain", subject)
                raise
            except Exception:
                _stat("fallback", subject)
                log.warning("envoi rattaché à la conversation impossible : envoi simple", exc_info=True)
        m = self.build(subject, body, box(to), box(cc), attachments, html, inline, box(bcc))
        m.send()  # une copie est enregistrée dans les éléments envoyés

    def _send_threaded(self, ref, subject, body, to, cc, bcc, attachments, html, inline) -> bool:
        """Réponse ou transfert rattaché à la conversation de l'original, quel que soit son âge ou son objet exact : Exchange le fait quand
        le brouillon est créé « à partir de » l'original (sinon il ne regroupe que d'après l'objet, et seulement les messages récents).
        Le brouillon créé par le serveur ne sert que de support : on le remplace par ce que l'utilisateur a rédigé (texte, citation, pièces jointes)."""
        kind, folder, uid = ref
        self._clean_support_drafts()
        orig = self._folder(folder).get(id=uid)
        make = orig.create_forward if kind == "forward" else orig.create_reply
        saved = make(subject=subject, body="", to_recipients=to, cc_recipients=cc or None, bcc_recipients=bcc or None).save(self.account.drafts)
        d = self.account.drafts.get(id=saved.id)
        try:
            copied = list(d.attachments or [])
            if copied:  # le serveur a recopié les pièces jointes de l'original (images de signature, etc.) : l'utilisateur a déjà fait son choix
                d.detach(copied)
                d = self.account.drafts.get(id=d.id)  # chaque suppression change la clé de version du brouillon : on la relit, sinon la mise à jour est refusée
            d.subject = subject
            d.body = HTMLBody(html) if html else body
            d.to_recipients, d.cc_recipients, d.bcc_recipients = to, cc or None, bcc or None
            d.support_mark = "1"
            d.save(update_fields=["subject", "body", "to_recipients", "cc_recipients", "bcc_recipients", "support_mark"])
            for name, data, ctype in attachments or []:
                d.attach(FileAttachment(name=name, content=data, content_type=ctype or "application/octet-stream"))
            for cid, data, mime, name in inline or []:
                d.attach(FileAttachment(name=name, content=data, content_type=mime, is_inline=True, content_id=cid))
        except Exception:
            try:
                d.delete()  # pas de brouillon orphelin : l'envoi simple prend le relais
            except Exception:
                log.warning("brouillon de support non supprimé", exc_info=True)
            raise
        try:
            d.send()
        except Exception as e:
            raise _AlreadySent(str(e)) from e  # on ne sait pas si c'est parti : surtout ne pas renvoyer un second message
        return True

    _cleaned = False

    def _clean_support_drafts(self, max_age: int = 900) -> None:
        """Une fois par session : supprime les brouillons de support oubliés (arrêt brutal entre deux étapes d'un envoi), après 15 minutes."""
        if self._cleaned:
            return
        self._cleaned = True
        try:
            limit = datetime.now(timezone.utc).timestamp() - max_age
            for d in list(self.account.drafts.all().only("support_mark", "datetime_created")):
                if getattr(d, "support_mark", None) == "1" and d.datetime_created and d.datetime_created.timestamp() < limit:
                    d.delete()
                    log.info("brouillon de support oublié supprimé")
        except Exception:
            log.warning("nettoyage des brouillons de support impossible", exc_info=True)

    def build(self, subject, body, to, cc, attachments, html=None, inline=None, bcc=None):
        m = Message(account=self.account, subject=subject, body=HTMLBody(html) if html else body,
                    to_recipients=to, cc_recipients=cc or None, bcc_recipients=bcc or None)
        for name, data, ctype in attachments or []:
            m.attach(FileAttachment(name=name, content=data, content_type=ctype or "application/octet-stream"))
        for cid, data, mime, name in inline or []:  # images affichées dans le corps du message
            m.attach(FileAttachment(name=name, content=data, content_type=mime, is_inline=True, content_id=cid))
        return m

    # ---------- notifications en direct ----------
    def wait_for_changes(self, keys: list[str], timeout: int) -> bool:
        """Bloque jusqu'à un événement (nouveau mail, lecture, déplacement…) ou la fin du délai.
        Lève une exception si le serveur ne propose pas les notifications."""
        folders = [self._folder(k) for k in keys]
        sub_id = FolderCollection(account=self.account, folders=folders).subscribe_to_streaming(event_types=EVENTS)
        # l'abonnement expire seul côté serveur ; un nouvel abonnement est créé à chaque attente
        for _ in folders[0].get_streaming_events(sub_id, connection_timeout=max(1, timeout // 60)):
            return True
        return False
