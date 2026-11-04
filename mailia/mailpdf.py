"""PDF d'un message (impression sous Windows, repli ailleurs) : en-tête (objet, de, à, cc, date), texte du message, noms des pièces jointes.
Fabriqué en Python (fpdf2), sans navigateur ni fenêtre : le PDF s'affiche ensuite dans l'aperçu de Velora, d'où on l'imprime comme une pièce jointe.
Seul le texte du message est imprimé (pas sa mise en forme HTML ni ses images)."""
import os
from pathlib import Path

PAGE_MARGIN = 15  # mm


def _font_files() -> tuple[Path, Path] | None:
    """Une police Unicode (accents, apostrophes et guillemets typographiques, €, œ…) : Arial de Windows ou du Mac, DejaVu sous Linux. None : police de base du PDF."""
    win = Path(os.environ.get("WINDIR") or r"C:\Windows") / "Fonts"
    for regular, bold in ((win / "arial.ttf", win / "arialbd.ttf"),
                          (Path("/System/Library/Fonts/Supplemental/Arial.ttf"), Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")),
                          (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))):
        if regular.is_file() and bold.is_file():
            return regular, bold
    return None


_ASCII = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u2026": "...", "\u00a0": " ", "\u202f": " ",
                        "\u20ac": "EUR", "\u0153": "oe", "\u0152": "OE", "\u2022": "-", "\u200b": ""})


def _base_font_text(s: str) -> str:
    """Police de base (Latin-1) : ce qui n'y figure pas est remplacé par un équivalent simple, puis par « ? »."""
    return (s or "").translate(_ASCII).encode("latin-1", "replace").decode("latin-1")


def message_pdf(head: dict, text: str) -> bytes:
    """head : {subject, from, to, cc, bcc, date, attachments: [noms]} ; text : texte du message tel qu'affiché. Rend le PDF (octets)."""
    from fpdf import FPDF
    files = _font_files()
    pdf = FPDF(format="A4")
    pdf.set_margins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
    pdf.set_auto_page_break(True, PAGE_MARGIN)
    if files:
        pdf.add_font("Mail", "", str(files[0]))
        pdf.add_font("Mail", "B", str(files[1]))
        family, fix = "Mail", (lambda s: s or "")
    else:
        family, fix = "Helvetica", _base_font_text
    pdf.add_page()
    pdf.set_title(fix(head.get("subject") or "(sans objet)"))

    def line(label: str, value: str) -> None:
        pdf.set_font(family, "B", 10)
        pdf.cell(max(20, pdf.get_string_width(fix(label)) + 3), 5.5, fix(label))
        pdf.set_font(family, "", 10)
        pdf.multi_cell(0, 5.5, fix(value), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font(family, "B", 15)
    pdf.multi_cell(0, 7.5, fix(head.get("subject") or "(sans objet)"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    for label, key in (("De :", "from"), ("À :", "to"), ("Cc :", "cc"), ("Cci :", "bcc"), ("Date :", "date")):
        if head.get(key):
            line(label, head[key])
    pdf.ln(2)
    pdf.set_draw_color(150, 150, 150)
    pdf.line(PAGE_MARGIN, pdf.get_y(), pdf.w - PAGE_MARGIN, pdf.get_y())
    pdf.ln(4)
    pdf.set_font(family, "", 11)
    pdf.multi_cell(0, 5.8, fix((text or "").replace("\r\n", "\n").replace("\t", "    ")) or fix("(message vide)"), new_x="LMARGIN", new_y="NEXT")
    atts = [a for a in (head.get("attachments") or []) if a]
    if atts:
        pdf.ln(4)
        line("Pièces jointes :", " · ".join(atts))
    return bytes(pdf.output())
