"""Règles de tri du serveur Exchange : lecture, calcul d'un plan de modifications, écriture sûre.

Reprend le moteur de l'application « Gestion des règles de messagerie » : requêtes EWS GetInboxRules / UpdateInboxRules, le XML d'origine des règles
qu'on ne sait pas modifier est renvoyé tel quel (rien n'est perdu), et toute écriture suit la chaîne plan → confirmation → sauvegarde → envoi → relecture.
Ce module ne fait aucun accès réseau : le transport est fourni par un « client » (voir EwsRulesClient dans providers/ews.py)."""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from xml.sax.saxutils import escape

from . import config, i18n

NS = {
    "s": "http://schemas.xmlsoap.org/soap/envelope/",
    "m": "http://schemas.microsoft.com/exchange/services/2006/messages",
    "t": "http://schemas.microsoft.com/exchange/services/2006/types",
}
ENVELOPE = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"
               xmlns:t="http://schemas.microsoft.com/exchange/services/2006/types"
               xmlns:m="http://schemas.microsoft.com/exchange/services/2006/messages">
  <soap:Header><t:RequestServerVersion Version="Exchange2013"/></soap:Header>
  <soap:Body>{body}</soap:Body>
</soap:Envelope>"""
GET_RULES = "<m:GetInboxRules><m:MailboxSmtpAddress>{mailbox}</m:MailboxSmtpAddress></m:GetInboxRules>"
FIND_FOLDERS = (
    '<m:FindFolder Traversal="Deep"><m:FolderShape><t:BaseShape>IdOnly</t:BaseShape>'
    '<t:AdditionalProperties><t:FieldURI FieldURI="folder:DisplayName"/>'
    '<t:FieldURI FieldURI="folder:ParentFolderId"/></t:AdditionalProperties></m:FolderShape>'
    '<m:ParentFolderIds><t:DistinguishedFolderId Id="msgfolderroot"/></m:ParentFolderIds></m:FindFolder>'
)

CONDITION_TYPES = {
    "ContainsSenderStrings": "sender_contains", "FromAddresses": "received_from", "ContainsSubjectStrings": "subject_contains",
    "ContainsSubjectOrBodyStrings": "subject_or_body_contains", "ContainsBodyStrings": "body_contains", "ContainsHeaderStrings": "header_contains",
    "ContainsRecipientStrings": "recipient_contains", "SentToAddresses": "sent_to", "SentOnlyToMe": "sent_only_to_me", "SentToMe": "sent_to_me",
    "SentCcMe": "sent_cc_me", "HasAttachments": "has_attachment",
}
BOOLEAN_KINDS = {"SentOnlyToMe", "SentToMe", "SentCcMe", "HasAttachments"}
UI_TO_EWS = {v: k for k, v in CONDITION_TYPES.items()}
EDITABLE_CONDITIONS = ("sender_contains", "subject_contains", "subject_or_body_contains", "body_contains",
                       "sent_only_to_me", "sent_to_me", "sent_cc_me", "has_attachment")
EDITABLE_ACTIONS = ("move_to_folder", "delete", "mark_read")
CONDITION_ORDER = ["ContainsBodyStrings", "ContainsSenderStrings", "ContainsSubjectOrBodyStrings", "ContainsSubjectStrings",
                   "HasAttachments", "SentCcMe", "SentOnlyToMe", "SentToMe"]  # ordre imposé par le schéma XML d'Exchange
# libellés des critères et actions, y compris ceux qu'on ne sait pas modifier (affichés tels quels, jamais cachés)
LABEL = {
    "sender_contains": "l'expéditeur contient", "subject_contains": "l'objet contient", "subject_or_body_contains": "l'objet ou le corps contient",
    "body_contains": "le corps contient", "received_from": "le message vient de", "header_contains": "l'en-tête contient", "recipient_contains": "un destinataire contient",
    "sent_to": "le message est envoyé à", "sent_only_to_me": "le message m'est adressé à moi seul", "sent_to_me": "le message m'est adressé (dans À)", "sent_cc_me": "je suis en copie",
    "has_attachment": "le message a une pièce jointe",
    "Categories": "catégorie", "FlaggedForAction": "marqué pour suivi", "Importance": "importance", "IsApprovalRequest": "le message est une demande d'approbation",
    "IsAutomaticForward": "le message est un transfert automatique", "IsAutomaticReply": "le message est une réponse automatique", "IsEncrypted": "le message est chiffré",
    "IsMeetingRequest": "le message est une invitation à une réunion", "IsMeetingResponse": "le message est une réponse à une réunion", "IsNDR": "le message est un échec de distribution",
    "IsPermissionControlled": "le message est protégé par des droits", "IsReadReceipt": "le message est un accusé de lecture", "IsSigned": "le message est signé", "IsVoicemail": "le message est un message vocal",
    "NotSentToMe": "le message ne m'est pas adressé", "SentToOrCcMe": "le message m'est adressé ou j'y suis en copie", "Sensitivity": "confidentialité",
    "WithinDateRange": "reçu dans une période", "WithinSizeRange": "taille comprise entre", "ItemClasses": "type de message", "MessageClassifications": "classification",
    "FromConnectedAccounts": "le message vient d'un compte connecté",
}
ACTION_LABEL = {
    "delete": "le mettre à la corbeille", "mark_read": "le marquer comme lu", "move_to_folder": "le déplacer vers", "AssignCategories": "lui attribuer la catégorie",
    "CopyToFolder": "en copier une version vers", "ForwardAsAttachmentToRecipients": "le transférer en pièce jointe à", "ForwardToRecipients": "le transférer à",
    "MarkImportance": "lui donner l'importance", "PermanentDelete": "le supprimer définitivement", "RedirectToRecipients": "le rediriger vers",
    "SendSMSAlertToRecipients": "envoyer une alerte SMS à", "ServerReplyWithMessage": "répondre automatiquement",
}
FLAG_CONDITIONS = ("sent_only_to_me", "sent_to_me", "sent_cc_me", "has_attachment")
WORD_CONDITIONS = ("sender_contains", "subject_contains", "subject_or_body_contains", "body_contains")


class RulesError(Exception):
    """Message destiné à l'utilisateur."""


class AuthError(RulesError):
    pass


def _tag(el) -> str:
    return el.tag.split("}")[1]


def _check(node, what: str) -> None:
    if node is None:
        raise RulesError(f"Réponse EWS inattendue ({what} absent).")
    code = node.findtext("m:ResponseCode", namespaces=NS)
    if code != "NoError":
        raise RulesError(f"EWS : {code} - {node.findtext('m:MessageText', namespaces=NS)}")


# ---------- lecture ----------
def _predicates(node):
    items, raws = [], []
    for child in (node if node is not None else []):
        kind = _tag(child)
        raws.append(ET.tostring(child, encoding="unicode").strip())
        if kind == "FromAddresses":
            values = [a.findtext("t:Name", default="", namespaces=NS) or a.findtext("t:EmailAddress", default="", namespaces=NS) for a in child.findall("t:Address", NS)]
            values = [v for v in values if v]
        elif kind in BOOLEAN_KINDS:
            values = [(child.text or "true").strip().lower()]
        elif kind in CONDITION_TYPES:
            values = [e.text for e in child if e.text]
        else:
            text = " ".join(x.text for x in child.iter() if x.text and x.text.strip()).strip()
            values = [text] if text else []
        items.append({"type": CONDITION_TYPES.get(kind, kind), "ews_type": kind, "values": values})
    return items, raws


def parse_rules(xml_bytes: bytes, folders: dict | None = None) -> list[dict]:
    """Réponse GetInboxRules → règles normalisées. Chaque règle garde le XML d'origine de ses critères, exceptions et actions (champs « _raw_* »)."""
    folders = folders or {}
    if b"<!ENTITY" in xml_bytes:
        raise RulesError("Réponse refusée (entités XML).")
    root = ET.fromstring(xml_bytes)
    _check(root.find(".//m:GetInboxRulesResponse", NS), "GetInboxRulesResponse")
    rules = []
    for rule in root.findall(".//t:Rule", NS):
        actions, stop, raw_a = [], False, []
        conditions, raw_c = _predicates(rule.find("t:Conditions", NS))
        exceptions, raw_e = _predicates(rule.find("t:Exceptions", NS))
        act_node = rule.find("t:Actions", NS)
        for child in (act_node if act_node is not None else []):
            kind = _tag(child)
            raw_a.append(ET.tostring(child, encoding="unicode").strip())
            if kind == "StopProcessingRules":
                stop = (child.text or "").lower() == "true"
            elif kind == "Delete":
                actions.append({"type": "delete"})
            elif kind == "MarkAsRead":
                actions.append({"type": "mark_read"})
            elif kind == "MoveToFolder":
                fid = next((e.get("Id") for e in child.iter() if e.get("Id")), None)
                actions.append({"type": "move_to_folder", "value": folders.get(fid, "(dossier inconnu)"), "folder_id": fid})
            else:
                actions.append({"type": kind, "value": " ".join(e.text for e in child.iter() if e.text)})
        rules.append({
            "id": rule.findtext("t:RuleId", default="", namespaces=NS), "order": int(rule.findtext("t:Priority", default="0", namespaces=NS)),
            "name": rule.findtext("t:DisplayName", default="", namespaces=NS), "enabled": rule.findtext("t:IsEnabled", default="false", namespaces=NS) == "true",
            "not_supported": rule.findtext("t:IsNotSupported", default="false", namespaces=NS) == "true",
            "conditions": conditions, "exceptions": exceptions, "actions": actions, "stop_processing": stop,
            "_raw_conditions": raw_c, "_raw_exceptions": raw_e, "_raw_actions": raw_a,
        })
    rules.sort(key=lambda r: r["order"])
    return rules


def public(rule: dict) -> dict:
    return {k: v for k, v in rule.items() if not k.startswith("_")}


def parse_folders(xml_bytes: bytes) -> dict:
    """{id_dossier: chemin lisible}, ex. {'AAMk…': 'Boîte de réception/Service DAP'}."""
    root = ET.fromstring(xml_bytes)
    msg = root.find(".//m:FindFolderResponseMessage", NS)
    _check(msg, "FindFolderResponseMessage")
    nodes = {}
    for f in msg.find(".//t:Folders", NS):
        fid = f.find("t:FolderId", NS).get("Id")
        parent = f.find("t:ParentFolderId", NS)
        nodes[fid] = (f.findtext("t:DisplayName", default="", namespaces=NS), parent.get("Id") if parent is not None else None)

    def path(fid):
        name, parent = nodes[fid]
        return path(parent) + "/" + name if parent in nodes else name

    return {fid: path(fid) for fid in nodes}


def editable(rule: dict) -> bool:
    """Vrai si on sait recréer entièrement cette règle (sinon : renommer, activer, réordonner et supprimer seulement)."""
    return (not rule.get("not_supported") and all(c["type"] in EDITABLE_CONDITIONS for c in rule["conditions"] + (rule.get("exceptions") or []))
            and all(a["type"] in EDITABLE_ACTIONS for a in rule["actions"]))


# ---------- description en français ----------
def _cond_text(c: dict) -> str:
    T = i18n.tr
    label = T(LABEL.get(c["type"], c["type"]))
    if c["type"] in FLAG_CONDITIONS or not c["values"]:
        return label
    return f"{label} " + " / ".join(f"« {v} »" for v in c["values"])


def describe(rule: dict) -> str:
    T = i18n.tr
    conds = f" {T('ET')} ".join(_cond_text(c) for c in rule["conditions"]) or T("tout message")
    acts = []
    for a in rule["actions"]:
        label = T(ACTION_LABEL.get(a["type"], a["type"]))
        acts.append(f"{label} « {a.get('value', '?')} »" if a["type"] == "move_to_folder" else label + (f" « {a['value']} »" if a.get("value") else ""))
    if rule["stop_processing"]:
        acts.append(T("ne pas appliquer les règles suivantes"))
    exc = f" {T('OU')} ".join(_cond_text(c) for c in (rule.get("exceptions") or []))
    return f"{T('Si')} {conds} → {', '.join(acts) or T('aucune action')}" + (f" — {T('sauf si')} {exc}" if exc else "") + "."


# ---------- empreintes ----------
def signature(rule: dict) -> tuple:
    """Empreinte du CONTENU d'une règle (ignore ordre et identifiant), pour détecter les changements (casse ignorée : Exchange peut renvoyer « LINKEDIN.COM »)."""
    key = lambda c: (c.get("ews_type") or UI_TO_EWS.get(c["type"], c["type"]), tuple(v.lower() for v in c["values"]))
    conds = tuple(sorted(key(c) for c in rule["conditions"]))
    excs = tuple(sorted(key(c) for c in (rule.get("exceptions") or [])))
    acts = tuple(sorted((a["type"], a.get("folder_id") or "") for a in rule["actions"]))
    return (rule["name"], bool(rule["enabled"]), conds, acts, bool(rule["stop_processing"]), excs)


def content_signature(rule: dict) -> tuple:
    s = signature(rule)
    return (s[2], s[3], s[4], s[5])


def live_fingerprint(live: list[dict]) -> str:
    data = [(r["id"], r["order"], signature(r)) for r in live]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, default=list).encode("utf-8")).hexdigest()


# ---------- construction des requêtes d'écriture ----------
def _build_predicates(items: list[dict]) -> list[str]:
    by_type: dict = {}
    for c in items:
        kind = c.get("ews_type") or UI_TO_EWS.get(c["type"])
        if c["type"] not in EDITABLE_CONDITIONS:
            raise RulesError(f"Critère non modifiable depuis l'application : {c['type']}")
        by_type.setdefault(kind, []).extend(v.strip() for v in c["values"] if v.strip())
    out = []
    for kind in CONDITION_ORDER:
        if kind in by_type:
            if kind in BOOLEAN_KINDS:
                out.append(f"<t:{kind}>true</t:{kind}>")
                continue
            if not by_type[kind]:
                raise RulesError("Un critère ne contient aucun mot-clé.")
            out.append(f"<t:{kind}>" + "".join(f"<t:String>{escape(v)}</t:String>" for v in by_type[kind]) + f"</t:{kind}>")
    return out


def _build_actions(rule: dict) -> list[str]:
    out = []
    for a in rule["actions"]:
        if a["type"] == "delete":
            out.append("<t:Delete>true</t:Delete>")
        elif a["type"] == "mark_read":
            out.append("<t:MarkAsRead>true</t:MarkAsRead>")
        elif a["type"] == "move_to_folder":
            if not a.get("folder_id"):
                raise RulesError("Dossier de destination manquant.")
            out.append(f'<t:MoveToFolder><t:FolderId Id="{escape(a["folder_id"], {chr(34): "&quot;"})}"/></t:MoveToFolder>')
        else:
            raise RulesError(f"Action non modifiable depuis l'application : {a['type']}")
    out.sort(key=lambda x: 0 if "<t:Delete>" in x else 1 if "<t:MarkAsRead>" in x else 2)  # ordre du schéma : Delete, MarkAsRead, MoveToFolder, puis StopProcessingRules
    if rule["stop_processing"]:
        out.append("<t:StopProcessingRules>true</t:StopProcessingRules>")
    if not out:
        raise RulesError("Une règle doit avoir au moins une action.")
    return out


def rule_xml(rule: dict, priority: int, with_id: bool, live: dict | None = None) -> str:
    """XML d'une règle. Si `live` est fourni et que critères et actions sont inchangés, leur XML d'origine est réutilisé tel quel."""
    reuse = live is not None and content_signature(rule) == content_signature(live)
    conds = live["_raw_conditions"] if reuse else _build_predicates(rule["conditions"])
    acts = live["_raw_actions"] if reuse else _build_actions(rule)
    excs = live.get("_raw_exceptions", []) if reuse else _build_predicates(rule.get("exceptions") or [])
    if not rule["name"].strip() or len(rule["name"]) > 255:
        raise RulesError("Nom de règle vide ou trop long.")
    parts = []
    if with_id:
        parts.append(f"<t:RuleId>{escape(rule['id'])}</t:RuleId>")
    parts += [f"<t:DisplayName>{escape(rule['name'].strip())}</t:DisplayName>", f"<t:Priority>{priority}</t:Priority>",
              f"<t:IsEnabled>{'true' if rule['enabled'] else 'false'}</t:IsEnabled>"]
    if conds:
        parts.append("<t:Conditions>" + "".join(conds) + "</t:Conditions>")
    if excs:
        parts.append("<t:Exceptions>" + "".join(excs) + "</t:Exceptions>")
    parts.append("<t:Actions>" + "".join(acts) + "</t:Actions>")
    return "<t:Rule>" + "".join(parts) + "</t:Rule>"


def build_update(mailbox: str, operations: list[tuple]) -> str:
    ops = []
    for kind, payload in operations:
        if kind == "create":
            ops.append(f"<t:CreateRuleOperation>{payload}</t:CreateRuleOperation>")
        elif kind == "set":
            ops.append(f"<t:SetRuleOperation>{payload}</t:SetRuleOperation>")
        elif kind == "delete":
            ops.append(f"<t:DeleteRuleOperation><t:RuleId>{escape(payload)}</t:RuleId></t:DeleteRuleOperation>")
    return (f"<m:UpdateInboxRules><m:MailboxSmtpAddress>{escape(mailbox)}</m:MailboxSmtpAddress>"
            f"<m:RemoveOutlookRuleBlob>true</m:RemoveOutlookRuleBlob><m:Operations>{''.join(ops)}</m:Operations></m:UpdateInboxRules>")


def parse_update_response(xml_bytes: bytes) -> None:
    root = ET.fromstring(xml_bytes)
    _check(root.find(".//m:UpdateInboxRulesResponse", NS), "UpdateInboxRulesResponse")
    errors = []
    for e in root.findall(".//m:RuleOperationErrors/t:RuleOperationError", NS):
        idx = e.findtext("t:OperationIndex", default="?", namespaces=NS)
        vals = [v.findtext("t:Value", default="", namespaces=NS) for v in e.findall(".//t:RuleValidationError", NS)]
        errors.append(f"opération {idx} : {'; '.join(vals) or 'erreur inconnue'}")
    if errors:
        raise RulesError("Exchange a refusé : " + " | ".join(errors))


# ---------- plan de modifications (aucun accès réseau) ----------
def clean_rule(r: dict) -> dict:
    """Valide et normalise une règle venant de l'interface."""
    vals = lambda vs: list(dict.fromkeys(str(v).strip() for v in vs if str(v).strip()))
    rule = {"id": str(r["id"]) if r.get("id") else None, "name": str(r["name"]).strip(), "enabled": bool(r.get("enabled", True)),
            "stop_processing": bool(r.get("stop_processing", False)), "conditions": [], "exceptions": [], "actions": []}
    for key in ("conditions", "exceptions"):
        for c in r.get(key) or []:
            item = {"type": str(c["type"]), "values": vals(c.get("values", []))}
            if c.get("ews_type"):
                item["ews_type"] = str(c["ews_type"])
            rule[key].append(item)
    for a in r.get("actions", []):
        act = {"type": str(a["type"])}
        if a.get("folder_id"):
            act["folder_id"] = str(a["folder_id"])
        if a.get("value"):
            act["value"] = str(a["value"])
        rule["actions"].append(act)
    return rule


def _validate(desired: list[dict], folder_ids: set, live_by_id: dict) -> None:
    seen = set()
    for r in desired:
        if r.get("id") and r["id"] not in live_by_id:
            raise RulesError(f"La règle « {r.get('name')} » n'existe plus sur le serveur : actualisez d'abord.")
        if r.get("id"):
            if r["id"] in seen:
                raise RulesError("Règle en double dans le brouillon.")
            seen.add(r["id"])
        for a in r["actions"]:
            if a["type"] == "move_to_folder" and a.get("folder_id") not in folder_ids:
                raise RulesError(f"Dossier de destination inconnu pour « {r['name']} ».")


def plan(live: list[dict], desired: list[dict], folders: dict) -> dict:
    """{'ops': [...], 'summary': [...], 'warnings': [...]} pour passer de l'état du serveur (`live`) à l'état voulu (`desired`, dans l'ordre voulu)."""
    live_by_id = {r["id"]: r for r in live}
    _validate(desired, set(folders), live_by_id)
    desired_ids = {r["id"] for r in desired if r.get("id")}
    deleted = [r for r in live if r["id"] not in desired_ids]
    survivors_live = [r["id"] for r in live if r["id"] in desired_ids]
    survivors_desired = [r["id"] for r in desired if r.get("id")]
    n = len(desired)
    creates_idx = [i for i, r in enumerate(desired) if not r.get("id")]
    renumber = survivors_live != survivors_desired or creates_idx != list(range(n - len(creates_idx), n))
    ops, summary, warnings = [], [], []
    for r in deleted:
        ops.append(("delete", r["id"]))
        summary.append({"kind": "delete", "text": f"Supprimer la règle « {r['name']} » ({describe(r)})"})
        warnings.append(f"« {r['name']} » sera supprimée du serveur (une sauvegarde est faite avant).")
    for i, r in enumerate(desired):
        if not r.get("id"):
            continue
        old = live_by_id[r["id"]]
        prio = i + 1
        changes = []
        if r["name"].strip() != old["name"]:
            changes.append(f"renommée « {old['name']} » → « {r['name'].strip()} »")
        if bool(r["enabled"]) != bool(old["enabled"]):
            changes.append("activée" if r["enabled"] else "désactivée")
        content_changed = content_signature(r) != content_signature(old)
        if content_changed:
            changes.append(f"critères/actions modifiés : {describe(r)}")
        if renumber and old["order"] != prio:
            changes.append(f"ordre {old['order']} → {prio}")
        if changes:
            ops.append(("set", rule_xml(r, prio if renumber else old["order"], True, live=old)))
            summary.append({"kind": "set", "text": f"Modifier « {old['name']} » : " + " ; ".join(changes)})
            if content_changed and any(a["type"] == "delete" for a in r["actions"]):
                warnings.append(f"« {r['name']} » supprime désormais les messages qui correspondent.")
    max_live = max([r["order"] for r in live if r["id"] in desired_ids] or [0])
    for k, i in enumerate(creates_idx):
        r = desired[i]
        for c in r["conditions"]:
            if c["type"] not in EDITABLE_CONDITIONS:
                raise RulesError(f"Critère non pris en charge pour une nouvelle règle : {c['type']}")
        if not r["conditions"]:
            raise RulesError(f"La nouvelle règle « {r['name']} » n'a aucun critère : elle s'appliquerait à tous les messages.")
        prio = i + 1 if renumber else max_live + 1 + k
        ops.append(("create", rule_xml(r, prio, False)))
        summary.append({"kind": "create", "text": f"Créer « {r['name'].strip()} » en position {i + 1} : {describe(r)}" + ("" if r["enabled"] else " (désactivée)")})
        if any(a["type"] == "delete" for a in r["actions"]):
            warnings.append(f"« {r['name']} » supprimera les messages qui correspondent.")
        short = [v for c in r["conditions"] if c["type"] in ("subject_or_body_contains", "body_contains") for v in c["values"] if len(v) <= 5]
        if short:
            warnings.append(f"« {r['name']} » cherche dans le corps avec des termes très courts ({', '.join(short)}) : risque de faux positifs.")
    order = {"delete": 0, "set": 1, "create": 2}  # ordre d'exécution sûr : suppressions, modifications, créations
    ops.sort(key=lambda o: order[o[0]])
    summary.sort(key=lambda s: order[s["kind"]])
    if ops:
        warnings.append("Exchange demande de retirer les règles propres à Outlook (« blob » du poste) pour accepter une modification : les règles que seul Outlook sait lire "
                        "peuvent disparaître. Une sauvegarde de toutes les règles est faite avant l'envoi.")
    return {"ops": ops, "summary": summary, "warnings": warnings}


def matches_desired(after: list[dict], desired: list[dict]) -> bool:
    """L'état relu sur le serveur correspond-il à ce qu'on a demandé (même ordre, même contenu) ?"""
    return [signature(r) for r in sorted(after, key=lambda r: r["order"])] == [signature(r) for r in desired]


def desired_from(live: list[dict]) -> list[dict]:
    """Copie de travail des règles du serveur, dans l'ordre : c'est ce qu'on modifie avant de demander un plan."""
    return [json.loads(json.dumps(public(r))) for r in sorted(live, key=lambda r: r["order"])]


# ---------- sauvegarde, plan en attente, application ----------
_PLANS: dict = {}


def backup(rules: list[dict], prefix: str, source: str = "") -> str:
    d = config.DATA_DIR / "rules_backups"
    d.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    path = d / f"{prefix}_{now:%Y%m%d_%H%M%S}.json"
    path.write_text(json.dumps({"source": source, "exported": now.strftime("%Y-%m-%d %H:%M"), "inbox_rules": [public(r) for r in rules]}, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.name


def make_plan(client, desired: list[dict]) -> dict:
    """Calcule le plan (aucune écriture) et le garde 30 minutes. {"plan_id", "summary", "warnings"} ; plan_id vaut None s'il n'y a rien à changer."""
    desired = [clean_rule(r) for r in desired]
    folders = client.folders()
    live = client.inbox_rules(folders)
    result = plan(live, desired, folders)
    if not result["ops"]:
        return {"plan_id": None, "summary": [], "warnings": []}
    now = time.time()
    for k in [k for k, v in _PLANS.items() if v["expires"] < now]:
        del _PLANS[k]
    pid = secrets.token_urlsafe(12)
    _PLANS[pid] = {"ops": result["ops"], "desired": desired, "fingerprint": live_fingerprint(live), "expires": now + 1800}
    return {"plan_id": pid, "summary": result["summary"], "warnings": result["warnings"]}


def apply_plan(client, plan_id: str, confirm: bool, source: str = "") -> dict:
    """Après confirmation : relit le serveur, refuse si les règles ont changé depuis le plan, sauvegarde, envoie, relit pour contrôle."""
    pending = _PLANS.pop(plan_id, None)
    if not pending or pending["expires"] < time.time():
        raise RulesError("Plan expiré ou déjà appliqué : recalculez-le.")
    if confirm is not True:
        raise RulesError("Confirmation manquante.")
    folders = client.folders()
    live = client.inbox_rules(folders)
    if live_fingerprint(live) != pending["fingerprint"]:
        raise RulesError("Les règles ont changé sur le serveur depuis le calcul du plan. Actualisez puis recommencez ; rien n'a été modifié.")
    saved = backup(live, "avant_ecriture", source)
    client.update_rules(pending["ops"])
    after = client.inbox_rules(folders)
    return {"verified": matches_desired(after, pending["desired"]), "backup": saved, "rules": after}


# ---------- aperçu sur les mails de la base locale ----------
def _has(term: str, text: str) -> bool:
    return term.lower() in text.lower()


def matches_local(rule: dict, m: dict) -> bool:
    """Le message (ligne de la base locale) correspond-il aux critères de la règle ? Critères non testables localement : ignorés."""
    def test(c: dict) -> bool | None:
        t, vals = c["type"], c["values"]
        sender, subject, body = f"{m.get('sender') or ''} {m.get('sender_name') or ''}", m.get("subject") or "", (m.get("body") or "")[:5000]
        if t == "sender_contains":
            return any(_has(v, sender) for v in vals)
        if t == "subject_contains":
            return any(_has(v, subject) for v in vals)
        if t == "body_contains":
            return any(_has(v, body) for v in vals)
        if t == "subject_or_body_contains":
            return any(_has(v, subject) or _has(v, body) for v in vals)
        if t == "has_attachment":
            return bool(m.get("has_att"))
        return None
    res = [test(c) for c in rule["conditions"]]
    tested = [r for r in res if r is not None]
    if not tested or not all(tested):
        return False
    exc = [test(c) for c in (rule.get("exceptions") or [])]
    return not any(e for e in exc if e is not None)
