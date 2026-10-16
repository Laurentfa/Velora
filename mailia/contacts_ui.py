"""Carnet d'adresses : saisie assistée des destinataires, favoris, groupes, fenêtre « Contacts »."""
from email.utils import getaddresses

from nicegui import run, ui

from . import db, sync


def split_addrs(text: str) -> list[str]:
    """« Nom <a@b.fr>; c@d.fr » -> ['a@b.fr', 'c@d.fr'] (sans doublons, dans l'ordre)."""
    found = [a.strip().lower() for _, a in getaddresses([(text or "").replace(";", ",")]) if "@" in a]
    return list(dict.fromkeys(found))


def my_addresses() -> set[str]:
    return {c.email.lower() for c in sync.ACCOUNTS.values()}


def label_of_contact(c: dict) -> str:
    star = "⭐ " if c["favorite"] else ""
    return f"{star}{c['name']} — {c['email']}" if c["name"] else f"{star}{c['email']}"


def recipient_options() -> dict[str, str]:
    """Liste proposée dans À / Cc : favoris d'abord, puis groupes, puis contacts les plus fréquents."""
    groups = db.groups_list()
    mine = my_addresses()
    contacts = [c for c in db.contacts_list() if c["email"] not in mine]
    glabel = lambda g: f"{'⭐ ' if g['favorite'] else ''}👥 {g['name']} ({len(g['members'])})"
    ordered = ([(f"group:{g['id']}", glabel(g)) for g in groups if g["favorite"]]
               + [(c["email"], label_of_contact(c)) for c in contacts if c["favorite"]]
               + [(f"group:{g['id']}", glabel(g)) for g in groups if not g["favorite"]]
               + [(c["email"], label_of_contact(c)) for c in contacts if not c["favorite"]])
    return dict(ordered)


def favorites() -> list[tuple[str, str, object]]:
    """(libellé, type, référence) des favoris : groupes (id) puis contacts (adresse)."""
    out = [(f"👥 {g['name']}", "group", g["id"]) for g in db.groups_list() if g["favorite"] and g["members"]]
    out += [(c["name"] or c["email"], "contact", c["email"]) for c in db.contacts_list() if c["favorite"]]
    return out


def favorite_addresses(kind: str, ref) -> list[str]:
    """Adresses d'un favori, relues au moment du clic (le groupe a pu être modifié depuis)."""
    if kind == "group":
        return next((g["members"] for g in db.groups_list() if g["id"] == ref), [])
    return [ref]


def recipients_field(label: str, d: dict, key: str, options: dict[str, str]) -> tuple[ui.select, callable]:
    """Champ de destinataires avec suggestions (nom ou adresse), pastilles et groupes.
    Retourne le champ et une fonction pour y ajouter des adresses."""
    state = {"busy": False}
    opts = dict(options)
    cur = split_addrs(d[key])
    for a in cur:
        opts.setdefault(a, a)
    sel = ui.select(opts, value=cur, label=label, multiple=True, with_input=True, new_value_mode="add-unique").props(
        "outlined dense use-chips stack-label hide-dropdown-icon input-debounce=0").classes("w-full")
    sel.add_slot("selected-item", """
        <q-chip removable dense square color="blue-1" text-color="blue-10" class="q-ma-xs"
                :tabindex="props.tabindex" @remove="props.removeAtIndex(props.index)">
          {{ String(props.opt.label).replace('⭐ ', '').split(' — ')[0] }}
          <q-tooltip>{{ props.opt.value }}</q-tooltip>
        </q-chip>""")

    # dans la liste qui s'ouvre au clic : une étoile sur chaque ligne ajoute ou retire le favori (les favoris passent en tête)
    sel.add_slot("option", """
        <q-item v-bind="props.itemProps">
          <q-item-section><q-item-label>{{ String(props.opt.label).replace('⭐ ', '') }}</q-item-label></q-item-section>
          <q-item-section side>
            <q-btn flat dense round size="sm" :icon="String(props.opt.label).startsWith('⭐') ? 'star' : 'star_border'"
                   :color="String(props.opt.label).startsWith('⭐') ? 'amber-8' : 'grey-6'"
                   @click.stop.prevent="() => { const nonce = Math.random(); let p = $parent; for (let i = 0; p && i < 8; i++, p = p.$parent) p.$emit('fav', [props.opt.value, nonce]) }">
              <q-tooltip>{{ String(props.opt.label).startsWith('⭐') ? 'Retirer des favoris' : 'Ajouter aux favoris' }}</q-tooltip>
            </q-btn>
          </q-item-section>
        </q-item>""")

    def apply(values: list[str]) -> None:
        groups = {f"group:{g['id']}": g for g in db.groups_list()}  # relus à chaque fois : un groupe a pu changer depuis l'ouverture
        out: list[str] = []
        for v in values:
            out += groups[v]["members"] if v in groups else (split_addrs(v) or [])
        out = list(dict.fromkeys(out))
        for a in out:
            opts.setdefault(a, a)
        state["busy"] = True
        try:
            sel.set_options(opts, value=out)
        finally:
            state["busy"] = False
        d[key] = ", ".join(out)
        sel.run_method("hidePopup")  # la liste de suggestions se referme dès qu'un contact est choisi
        sel.run_method("updateInputValue", "", True)  # le texte tapé pour trouver le contact ne doit pas rester dans le champ après le choix

    sel.on_value_change(lambda e: None if state["busy"] else apply(list(e.value or [])))

    def refresh_options() -> None:  # contacts, groupes et favoris ajoutés ou modifiés depuis l'ouverture de l'éditeur
        fresh = recipient_options()
        for a in split_addrs(d[key]):
            fresh.setdefault(a, a)
        opts.clear()
        opts.update(fresh)
        state["busy"] = True
        try:
            sel.set_options(opts, value=split_addrs(d[key]))
        finally:
            state["busy"] = False

    seen = {"nonce": None}

    def toggle_favorite(e) -> None:
        args = e.args if isinstance(e.args, list) and len(e.args) == 2 else [e.args, None]
        if args[1] is not None and args[1] == seen["nonce"]:
            return  # le clic remonte par plusieurs niveaux du composant : un seul compte
        seen["nonce"] = args[1]
        values = list(getattr(sel, "_values", []))  # NiceGUI numérote les options : l'étoile envoie le rang de la ligne
        rank = args[0]
        ref = values[rank] if isinstance(rank, int) and 0 <= rank < len(values) else (rank if isinstance(rank, str) else "")
        if ref.startswith("group:"):
            gid = int(ref.split(":", 1)[1])
            g = next((g for g in db.groups_list() if g["id"] == gid), None)
            if g:
                db.group_favorite(gid, not g["favorite"])
        elif ref:
            c = next((c for c in db.contacts_list(ref) if c["email"] == ref.lower()), None)
            if c:
                db.contact_favorite(ref, not c["favorite"])
        fresh = recipient_options()  # l'étoile change tout de suite, mais les lignes ne bougent pas pendant que la liste est ouverte (le rang d'une ligne doit rester juste)
        for k in list(opts):
            opts[k] = fresh.get(k, opts[k])
        state["busy"] = True
        try:
            sel.set_options(opts, value=split_addrs(d[key]))
        finally:
            state["busy"] = False

    sel.on("fav", toggle_favorite)
    sel.on("popup-show", refresh_options)
    # une adresse tapée mais non validée (on passe au champ suivant sans Entrée) ne doit pas se perdre
    sel.on("blur", js_handler="""(e) => { const i = e && e.target; if (i && i.tagName === 'INPUT' && i.value && i.value.includes('@'))
        i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', keyCode: 13, which: 13, bubbles: true})); }""")

    def add(addresses: list[str]) -> None:
        apply(split_addrs(d[key]) + addresses)

    return sel, add


# ---------------------------------------------------------------- fenêtre « Contacts »
class ContactsWindow:
    """Contacts et groupes : favoris, groupes, ajout / modification / suppression, mise à jour depuis Exchange.
    Deux listes (contacts, groupes) qui se rafraîchissent l'une l'autre ; chaque action est une méthode."""

    def __init__(self, host, on_write) -> None:
        self.host, self.on_write = host, on_write
        self.state = {"q": "", "shown": 120}
        self.dlg: ui.dialog

    def build(self) -> None:
        with self.host, ui.dialog() as self.dlg, ui.card().classes("no-wrap q-pa-none").style(
                "width:min(1040px,96vw);max-width:96vw;height:min(88vh,820px)"):
            with ui.row().classes("w-full items-center q-px-lg q-py-md"):
                ui.icon("contacts", size="26px").style("color:var(--accent)")
                ui.label("Contacts et groupes").classes("text-h6").style("font-weight:700")
                ui.space()
                ui.button(icon="close", on_click=self.dlg.close).props("flat round dense")
            with ui.tabs().props("no-caps align=left").classes("w-full").style("border-top:1px solid var(--line)") as tabs:
                ui.tab("contacts", label="Contacts", icon="person")
                ui.tab("groups", label="Groupes", icon="groups")
            with ui.tab_panels(tabs, value="contacts").classes("w-full col").style(
                    "min-height:0;border-top:1px solid var(--line);background:transparent"):
                p_contacts = ui.tab_panel("contacts").classes("q-pa-md").style("overflow-y:auto")
                p_groups = ui.tab_panel("groups").classes("q-pa-md").style("overflow-y:auto")
            with p_contacts:
                self.contacts_tab()
            with p_groups:
                self.groups_tab()

    # ---------- contacts ----------
    def contacts_tab(self) -> None:
        with ui.row().classes("w-full items-center gap-2 q-mb-sm"):
            s = ui.input(placeholder="Rechercher un nom ou une adresse…").props("outlined dense clearable debounce=250").classes("col")
            s.on_value_change(lambda e: (self.state.update(q=e.value or "", shown=120), self.contacts_list.refresh()))
            ui.button("Nouveau", icon="person_add", on_click=lambda: self.edit_contact(None)).props("unelevated no-caps color=primary").mark("ct-new")
            ui.button("Annuaire…", icon="travel_explore", on_click=self.search_directory).props("outline no-caps")
            ui.button("Actualiser", icon="sync", on_click=self.refresh_all).props("outline no-caps").tooltip(
                "Relit vos mails et le carnet Exchange (lecture seule : rien n'est modifié sur le serveur)")
        ui.label("Les correspondants de vos mails et vos contacts Exchange sont ajoutés automatiquement. "
                 "Une étoile les place en tête des suggestions et dans la barre « Favoris » de l'éditeur.").classes("text-caption text-grey q-mb-sm")
        self.contacts_list()

    def groups_tab(self) -> None:
        with ui.row().classes("w-full items-center q-mb-sm"):
            ui.button("Nouveau groupe", icon="group_add", on_click=self.new_group).props("unelevated no-caps color=primary").mark("grp-new")
            ui.label("Un groupe réunit des adresses : le choisir dans « À » ajoute tous ses membres.").classes("text-caption text-grey")
        self.groups_list()

    async def edit_contact(self, c: dict | None) -> None:
        with self.host, ui.dialog() as ed, ui.card().style("width:480px;max-width:96vw"):
            ui.label("Modifier le contact" if c else "Nouveau contact").classes("text-h6")
            name = ui.input("Nom", value=(c or {}).get("name", "")).props("outlined dense stack-label").classes("w-full")
            mail = ui.input("Adresse e-mail", value=(c or {}).get("email", "")).props(
                "outlined dense stack-label" + (" readonly" if c else "")).classes("w-full")
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: ed.submit(False)).props("flat no-caps")
                ui.button("Enregistrer", on_click=lambda: ed.submit(True)).props("unelevated no-caps color=primary").mark("ct-save")
        if await ed:
            addr = split_addrs(mail.value)
            if not addr:
                return ui.notify("Adresse e-mail invalide.", type="warning")
            db.contact_save(addr[0], name.value or "", "manual" if not c else (c.get("source") or "manual"))
            self.contacts_list.refresh()

    def toggle_fav(self, c: dict) -> None:
        db.contact_favorite(c["email"], not c["favorite"])
        self.contacts_list.refresh()

    def toggle_member(self, g: dict, c: dict) -> None:
        db.group_set_member(g["id"], c["email"], c["email"] not in g["members"])
        self.contacts_list.refresh()
        self.groups_list.refresh()

    def drop_contact(self, c: dict) -> None:
        db.contact_delete(c["email"])
        self.contacts_list.refresh()
        self.groups_list.refresh()

    def write_to(self, addresses: list[str]) -> None:
        self.dlg.close()
        self.on_write(addresses)

    @ui.refreshable_method
    def contacts_list(self) -> None:
        rows = db.contacts_list(self.state["q"])
        groups = db.groups_list()
        ui.label(f"{len(rows)} contact(s)" + (f" — les {self.state['shown']} premiers sont affichés" if len(rows) > self.state["shown"] else "")).classes("rmeta")
        for c in rows[:self.state["shown"]]:
            with ui.row().classes("w-full items-center no-wrap gap-2").style(
                    "border-bottom:1px solid var(--line);padding:4px 2px"):
                ui.button(icon="star" if c["favorite"] else "star_border", on_click=lambda _, c=c: self.toggle_fav(c)).props(
                    "flat dense round" + (" color=amber-8" if c["favorite"] else " color=grey-6")).tooltip(
                    "Retirer des favoris" if c["favorite"] else "Ajouter aux favoris").mark("ct-fav")
                with ui.column().classes("gap-0 col").style("min-width:0"):
                    ui.label(c["name"] or c["email"]).style("font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                    if c["name"]:
                        ui.label(c["email"]).classes("rmeta").style("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                ui.label({"exchange": "Exchange", "manual": "ajouté", "annuaire": "annuaire"}.get(c["source"], "mails")).classes("badge acc")
                with ui.button(icon="group_add").props("flat dense round").tooltip("Groupes"):
                    with ui.menu():
                        if not groups:
                            ui.menu_item("Aucun groupe : créez-en un dans l'onglet Groupes").props("disable")
                        for g in groups:
                            ui.menu_item(("✓ " if c["email"] in g["members"] else "     ") + g["name"],
                                         on_click=lambda _, g=g, c=c: self.toggle_member(g, c)).props("v-close-popup=false")
                ui.button(icon="mail_outline", on_click=lambda _, c=c: self.write_to([c["email"]])).props("flat dense round").tooltip("Écrire")
                ui.button(icon="edit", on_click=lambda _, c=c: self.edit_contact(c)).props("flat dense round").tooltip("Modifier")
                ui.button(icon="delete_outline", on_click=lambda _, c=c: self.drop_contact(c)).props("flat dense round color=negative").tooltip("Retirer du carnet").mark("ct-drop")
        if len(rows) > self.state["shown"]:
            ui.button("Afficher plus", on_click=lambda: (self.state.update(shown=self.state["shown"] + 200), self.contacts_list.refresh())).props("flat no-caps")

    async def refresh_all(self) -> None:
        ui.notify("Mise à jour du carnet…")
        r = await run.io_bound(sync.refresh_contacts, True)
        ui.notify(f"Carnet à jour : {r['mail']} correspondants dans vos mails, {r['exchange']} contacts Exchange"
                  + (f", {r['groups']} groupe(s) importé(s)" if r["groups"] else "") + ".", type="positive")
        if r["errors"]:
            ui.notify("Exchange : " + " ; ".join(r["errors"]), type="warning", multi_line=True)
        self.contacts_list.refresh()
        self.groups_list.refresh()

    async def search_directory(self) -> None:
        with self.host, ui.dialog() as dd, ui.card().style("width:640px;max-width:96vw;max-height:80vh"):
            ui.label("Annuaire de l'entreprise (Exchange)").classes("text-h6")
            q = ui.input("Nom à chercher", placeholder="ex : Martin").props("outlined dense stack-label autofocus").classes("w-full")
            box = ui.column().classes("w-full gap-1")

            async def go() -> None:
                if len(q.value.strip()) < 2:
                    return ui.notify("Tapez au moins 2 lettres.", type="warning")
                box.clear()
                with box:
                    ui.spinner(size="20px")
                try:
                    res = await run.io_bound(sync.directory_search, q.value.strip())
                except Exception as e:
                    box.clear()
                    with box:
                        ui.label(f"Annuaire indisponible : {e}").classes("text-negative")
                    return
                box.clear()
                with box:
                    if not res:
                        ui.label("Aucun résultat.").classes("rmeta")
                    for email, name in res[:40]:
                        with ui.row().classes("w-full items-center no-wrap gap-2"):
                            with ui.column().classes("gap-0 col"):
                                ui.label(name or email).style("font-weight:600")
                                ui.label(email).classes("rmeta")
                            ui.button("Ajouter", icon="add", on_click=lambda _, e=email, n=name: (
                                db.contact_save(e, n, "annuaire"), self.contacts_list.refresh(), ui.notify("Ajouté au carnet", type="positive"))
                            ).props("flat dense no-caps")

            q.on("keydown.enter", go)
            with ui.row().classes("justify-between w-full"):
                ui.button("Chercher", icon="search", on_click=go).props("unelevated no-caps color=primary")
                ui.button("Fermer", on_click=dd.close).props("flat no-caps")
        dd.open()

    async def new_group(self) -> None:
        with self.host, ui.dialog() as gd, ui.card().style("width:420px;max-width:96vw"):
            ui.label("Nouveau groupe").classes("text-h6")
            name = ui.input("Nom du groupe", placeholder="ex : Équipe technique").props("outlined dense stack-label autofocus").classes("w-full")
            name.on("keydown.enter", lambda: gd.submit(name.value))
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: gd.submit(None)).props("flat no-caps")
                ui.button("Créer", on_click=lambda: gd.submit(name.value)).props("unelevated no-caps color=primary").mark("grp-create")
        n = await gd
        if n and n.strip():
            db.group_save(None, n)
            self.groups_list.refresh()
            self.contacts_list.refresh()

    async def rename_group(self, g: dict) -> None:
        with self.host, ui.dialog() as gd, ui.card().style("width:420px;max-width:96vw"):
            ui.label("Renommer le groupe").classes("text-h6")
            name = ui.input("Nom du groupe", value=g["name"]).props("outlined dense stack-label autofocus").classes("w-full")
            name.on("keydown.enter", lambda: gd.submit(name.value))
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: gd.submit(None)).props("flat no-caps")
                ui.button("Enregistrer", on_click=lambda: gd.submit(name.value)).props("unelevated no-caps color=primary")
        n = await gd
        if n and n.strip():
            db.group_save(g["id"], n)
            self.groups_list.refresh()
            self.contacts_list.refresh()

    async def drop_group(self, g: dict) -> None:
        with self.host, ui.dialog() as gd, ui.card():
            ui.label(f"Supprimer le groupe « {g['name']} » ? Les contacts ne sont pas supprimés.")
            with ui.row().classes("justify-end w-full"):
                ui.button("Annuler", on_click=lambda: gd.submit(False)).props("flat no-caps")
                ui.button("Supprimer", on_click=lambda: gd.submit(True)).props("unelevated no-caps color=negative")
        if await gd:
            db.group_delete(g["id"])
            self.groups_list.refresh()
            self.contacts_list.refresh()

    def group_fav(self, g: dict) -> None:
        db.group_favorite(g["id"], not g["favorite"])
        self.groups_list.refresh()

    def member_change(self, g: dict, email: str, on: bool) -> None:
        db.group_set_member(g["id"], email, on)
        self.groups_list.refresh()
        self.contacts_list.refresh()

    @ui.refreshable_method
    def groups_list(self) -> None:
        groups = db.groups_list()
        if not groups:
            ui.label("Aucun groupe pour l'instant. Créez-en un, puis ajoutez-y des contacts.").classes("rmeta")
        by_email = {c["email"]: c for c in db.contacts_list()}
        options = {c["email"]: label_of_contact(c) for c in by_email.values()}
        for g in groups:
            with ui.card().classes("w-full").props("flat bordered"):
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.button(icon="star" if g["favorite"] else "star_border", on_click=lambda _, g=g: self.group_fav(g)).props(
                        "flat dense round" + (" color=amber-8" if g["favorite"] else " color=grey-6")).tooltip(
                        "Retirer des favoris" if g["favorite"] else "Ajouter aux favoris")
                    ui.label(f"👥 {g['name']}").style("font-weight:700;font-size:15px")
                    ui.label(f"{len(g['members'])} membre(s)").classes("rmeta")
                    ui.space()
                    ui.button(icon="mail_outline", on_click=lambda _, g=g: self.write_to(g["members"])).props(
                        "flat dense round" + ("" if g["members"] else " disable")).tooltip("Écrire au groupe")
                    ui.button(icon="edit", on_click=lambda _, g=g: self.rename_group(g)).props("flat dense round").tooltip("Renommer")
                    ui.button(icon="delete_outline", on_click=lambda _, g=g: self.drop_group(g)).props("flat dense round color=negative").tooltip("Supprimer le groupe")
                with ui.row().classes("gap-1 q-mt-xs"):
                    for e in g["members"]:
                        c = by_email.get(e)
                        ui.chip(c["name"] or e if c else e, removable=True, on_value_change=None).props("dense").on(
                            "remove", lambda _, g=g, e=e: self.member_change(g, e, False)).tooltip(e)
                    if not g["members"]:
                        ui.label("Aucun membre").classes("rmeta")
                add = ui.select(options, label="Ajouter un membre", with_input=True, new_value_mode="add-unique").props(
                    "outlined dense stack-label clearable").classes("w-full q-mt-xs")

                def added(e, g=g, add=add) -> None:
                    if e.value:
                        for a in split_addrs(e.value):
                            db.contact_save(a, (by_email.get(a) or {}).get("name", ""), (by_email.get(a) or {}).get("source", "manual"))
                            self.member_change(g, a, True)

                add.on_value_change(added)


async def contacts_dialog(host, on_write, on_close=None) -> None:
    """Ouvre la fenêtre « Contacts et groupes »."""
    win = ContactsWindow(host, on_write)
    win.build()
    if on_close:
        win.dlg.on("hide", lambda: on_close())
    win.dlg.open()
