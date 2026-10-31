from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class Msg:
    uid: str
    subject: str
    sender: str
    sender_name: str
    to: str
    date: str  # ISO 8601 UTC
    body: str
    is_read: bool
    has_att: bool
    cc: str = ""
    attachments: list | None = None  # [{"id","name","size","type"}] ; None = pas encore chargé
    conv: str | None = None  # identifiant de conversation (même valeur pour tous les mails d'un même fil)
    html: str | None = None  # corps HTML d'origine ("" : le message n'a pas de version HTML)
    flagged: bool = False  # drapeau posé sur le serveur : c'est ce qui épingle une conversation
    verb: int = 0  # 1 = on a répondu à ce message, 2 = on l'a transféré (marque posée par nous ou par Outlook)
    meeting: str = ""  # JSON d'une invitation / réponse / annulation de réunion (Exchange), sinon ""
    importance: int = 1  # importance fixée par l'expéditeur : 0 basse, 1 normale, 2 haute


@dataclass
class FolderInfo:
    key: str      # identifiant stable du dossier ("INBOX" pour la boîte de réception)
    name: str
    path: str     # chemin lisible, ex. "Clients/2025"
    role: str = ""  # inbox | archive | trash | sent | drafts | junk | ""


@dataclass
class Delta:
    """Ce qui a changé dans un dossier depuis la dernière synchro."""
    upserts: list[Msg] = field(default_factory=list)
    read_changes: list[tuple[str, bool]] = field(default_factory=list)  # (uid, lu)
    flag_changes: list[tuple[str, bool]] = field(default_factory=list)  # (uid, drapeau)
    deleted: list[str] = field(default_factory=list)                    # uids disparus du dossier
    new_state: str | None = None
    reset: bool = False  # l'état précédent n'était plus valable : le dossier a été relu depuis zéro


class Provider(Protocol):
    def list_folders(self) -> list[FolderInfo]: ...
    def sync_folder(self, key: str, state: str | None, known: dict[str, tuple[int, str]], limit: int) -> Delta: ...
    def set_read(self, key: str, uid: str, read: bool) -> None: ...
    def set_flag(self, key: str, uid: str, on: bool) -> None: ...
    def move(self, key: str, uid: str, dest_key: str) -> None: ...
    def delete(self, key: str, uid: str) -> None: ...
    def threading(self, key: str, uids: list[str]) -> dict[str, str]: ...
    def details(self, key: str, uid: str) -> dict: ...
    def inline_images(self, key: str, uid: str, cids: list[str]) -> dict[str, tuple[bytes, str]]: ...
    def attachment(self, key: str, uid: str, att_id: str) -> tuple[bytes, str]: ...
    def send(self, to: str, subject: str, body: str, cc: str = "", attachments: list | None = None,
             html: str | None = None, inline: list | None = None, bcc: str = "", ref: tuple | None = None, importance: int = 1, receipt: bool = False) -> None: ...
    def save_draft(self, d: dict, files: list, ref: str | None, folder_key: str) -> str: ...  # copie du brouillon dans le dossier Brouillons ; remplace `ref`
    def load_draft(self, ref: str, folder_key: str) -> dict: ...  # {"to","cc","bcc","subject","html","files":[(nom, octets, type)]}
    def delete_draft(self, ref: str, folder_key: str) -> None: ...
    def importance_marks(self, key: str, limit: int) -> dict[int, list[str]]: ...  # {2: [uids à importance haute], 0: [uids à importance basse]}
    def wait_for_changes(self, keys: list[str], timeout: int) -> bool: ...
