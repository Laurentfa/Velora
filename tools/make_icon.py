"""Dessine l'icône de Velora (un « V » formé de deux pétales bleus sur un carré clair) et écrit assets/icon.png, icon.ico, icon.icns, icon.svg,
ainsi que le petit logo de l'interface (mailia/brand.py).
Usage : python tools/make_icon.py   (le .icns demande l'outil `iconutil`, présent sur Mac)"""
import base64
import io
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

# ---- géométrie, en coordonnées d'un canevas de 1024 : le carré de 824 px est celui du gabarit des icônes macOS
SQ0, SQ = 100, 824
RADIUS = 215


def _map(p):
    """Coordonnées relevées sur le modèle (carré de 660 px centré en 456,360) -> canevas 1024."""
    return (512 + (p[0] - 456) * SQ / 660, 512 + (p[1] - 360) * SQ / 660)


# chemins : liste de segments ("M", p) / ("C", c1, c2, p) / ("L", p) dans les coordonnées du modèle
LEFT = [("M", (213, 247)), ("C", (206, 222), (238, 214), (268, 220)), ("C", (360, 232), (425, 280), (462, 347)),
        ("C", (500, 410), (536, 470), (545, 505)), ("C", (546, 545), (500, 573), (447, 573)),
        ("C", (395, 573), (355, 540), (335, 500)), ("C", (290, 420), (240, 320), (213, 250))]
RIGHT = [("M", (462, 347)), ("C", (485, 280), (555, 195), (640, 160)), ("C", (672, 148), (698, 150), (698, 185)),
         ("C", (660, 280), (595, 400), (545, 505)), ("L", (462, 347))]
CUT = ((462, 347), (362, 515))  # la ligne qui sépare le pétale gauche de sa partie claire (la lentille de recouvrement)

GRADIENTS = {  # (point de départ, point d'arrivée, [(position, couleur)]) en coordonnées du modèle
    "right": ((690, 150), (545, 505), [(0, "#3d88f6"), (0.45, "#1f50e6"), (1, "#0a24a6")]),
    "left": ((225, 225), (360, 520), [(0, "#80c3fb"), (0.5, "#4a95f6"), (1, "#2a63f3")]),
    "lens": ((480, 360), (390, 525), [(0, "#70d3fa"), (0.5, "#4aa5f8"), (1, "#2a62f2")]),
}


def _hex(c):
    return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))


def _path_points(segs, n=60):
    pts, cur = [], None
    for s in segs:
        if s[0] in ("M", "L"):
            cur = _map(s[1])
            pts.append(cur)
        else:
            p0, p1, p2, p3 = cur, _map(s[1]), _map(s[2]), _map(s[3])
            for i in range(1, n + 1):
                t = i / n
                u = 1 - t
                pts.append((u**3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t**3 * p3[0],
                            u**3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t**3 * p3[1]))
            cur = p3
    return pts


def _t_image(n, a, b):
    """Image « L » dont chaque pixel vaut la position (0 à 255) le long du segment a -> b : un dégradé linéaire sans numpy (transformation affine d'une rampe)."""
    (ax, ay), (bx, by) = a, b
    ux, uy = bx - ax, by - ay
    l2 = ux * ux + uy * uy
    pad = 384
    ramp = Image.new("L", (4, 256 + 2 * pad))
    for y in range(ramp.height):
        ramp.paste(min(255, max(0, y - pad)), (0, y, 4, y + 1))  # 0 au-dessus, rampe 0..255, puis 255 : le dégradé est prolongé au-delà des extrémités
    k = 255 / l2
    return ramp.transform((n, n), Image.AFFINE, (0, 0, 2, ux * k, uy * k, pad - k * (ax * ux + ay * uy)), resample=Image.BILINEAR)


def _colorize(t, stops):
    """Applique les couleurs d'un dégradé (positions 0..1) à une image de positions."""
    chans = []
    for ch in range(3):
        lut = []
        for i in range(256):
            x = i / 255
            for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
                if x <= p1 or (p1, c1) == stops[-1]:
                    f = 0 if p1 == p0 else min(1, max(0, (x - p0) / (p1 - p0)))
                    lut.append(round(_hex(c0)[ch] + (_hex(c1)[ch] - _hex(c0)[ch]) * f))
                    break
        chans.append(t.point(lut))
    return Image.merge("RGB", chans)


def draw(size: int = 1024, scale: int = 2, plate: bool = True) -> Image.Image:
    n = size * scale
    k = n / 1024
    sc = lambda pts: [(x * k, y * k) for x, y in pts]

    def mask(points):
        m = Image.new("L", (n, n), 0)
        ImageDraw.Draw(m).polygon(sc(points), fill=255)
        return m

    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    if plate:
        # ombre douce, légèrement bleutée, sous le carré
        sh = Image.new("RGBA", (n, n), (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle(((SQ0 + 4) * k, (SQ0 + 22) * k, (SQ0 + SQ - 4) * k, (SQ0 + SQ + 14) * k), radius=RADIUS * k, fill=(40, 70, 150, 70))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18 * k)))
        # carré blanc, très légèrement dégradé vers le bas
        base = _colorize(_t_image(n, (0, SQ0 * k), (0, (SQ0 + SQ) * k)), [(0, "#ffffff"), (1, "#f0f3fa")])
        sq = Image.new("L", (n, n), 0)
        ImageDraw.Draw(sq).rounded_rectangle((SQ0 * k, SQ0 * k, (SQ0 + SQ) * k, (SQ0 + SQ) * k), radius=RADIUS * k, fill=255)
        img.paste(base, (0, 0), sq)

    def paint(name, m):
        a, b, stops = GRADIENTS[name]
        (ax, ay), (bx, by) = _map(a), _map(b)
        img.paste(_colorize(_t_image(n, (ax * k, ay * k), (bx * k, by * k)), stops), (0, 0), m)

    right_m, left_m = mask(_path_points(RIGHT)), mask(_path_points(LEFT))
    paint("right", right_m)
    paint("left", left_m)
    # la partie claire du pétale gauche : à droite de la ligne CUT
    (cx0, cy0), (cx1, cy1) = _map(CUT[0]), _map(CUT[1])
    dx, dy = cx1 - cx0, cy1 - cy0
    f = 30
    nx, ny = dy * f, -dx * f  # côté du pétale foncé
    quad = [(cx0 - dx * f, cy0 - dy * f), (cx1 + dx * f, cy1 + dy * f), (cx1 + dx * f + nx, cy1 + dy * f + ny), (cx0 - dx * f + nx, cy0 - dy * f + ny)]
    half = Image.new("L", (n, n), 0)
    ImageDraw.Draw(half).polygon(sc(quad), fill=255)
    paint("lens", ImageChops.darker(left_m, half))
    return img.resize((size, size), Image.LANCZOS)


def svg() -> str:
    def d(segs):
        out = []
        for s in segs:
            pts = [_map(p) for p in s[1:]]
            out.append(f'{s[0]}{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}')
        return " ".join(out) + " Z"

    defs = ""
    for name, (a, b, stops) in GRADIENTS.items():
        (ax, ay), (bx, by) = _map(a), _map(b)
        defs += (f'<linearGradient id="{name}" gradientUnits="userSpaceOnUse" x1="{ax:.1f}" y1="{ay:.1f}" x2="{bx:.1f}" y2="{by:.1f}">'
                 + "".join(f'<stop offset="{p}" stop-color="{c}"/>' for p, c in stops) + "</linearGradient>")
    defs += '<linearGradient id="base" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#f0f3fa"/></linearGradient>'
    (cx0, cy0), (cx1, cy1) = _map(CUT[0]), _map(CUT[1])
    dx, dy = cx1 - cx0, cy1 - cy0
    clip = (f'<clipPath id="lensclip"><polygon points="{cx0 - dx * 30:.0f},{cy0 - dy * 30:.0f} {cx1 + dx * 30:.0f},{cy1 + dy * 30:.0f} '
            f'{cx1 + dx * 30 + dy * 30:.0f},{cy1 + dy * 30 - dx * 30:.0f} {cx0 - dx * 30 + dy * 30:.0f},{cy0 - dy * 30 - dx * 30:.0f}"/></clipPath>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024"><defs>{defs}{clip}</defs>'
            f'<rect x="{SQ0}" y="{SQ0}" width="{SQ}" height="{SQ}" rx="{RADIUS}" fill="url(#base)"/>'
            f'<path d="{d(RIGHT)}" fill="url(#right)"/><path d="{d(LEFT)}" fill="url(#left)"/>'
            f'<path d="{d(LEFT)}" fill="url(#lens)" clip-path="url(#lensclip)"/></svg>')


def logo_png(big: Image.Image, px: int = 96) -> str:
    """Le carré de l'icône seul (sans la marge ni l'ombre), en petit PNG pour l'interface."""
    sq = big.crop((SQ0, SQ0, SQ0 + SQ, SQ0 + SQ)).resize((px, px), Image.LANCZOS)
    buf = io.BytesIO()
    sq.save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def mark_png(px: int = 420) -> str:
    """Le V seul, fond transparent (sans carré ni ombre), recadré au plus juste : pour le grand logo voilé."""
    m = draw(1024, plate=False)
    m = m.crop(m.getchannel("A").getbbox())
    side = max(m.size)
    sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    sq.paste(m, ((side - m.width) // 2, (side - m.height) // 2))
    buf = io.BytesIO()
    sq.resize((px, px), Image.LANCZOS).save(buf, "PNG", optimize=True)
    return base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    big = draw(1024)
    big.save(ASSETS / "icon.png")
    (ASSETS / "icon.svg").write_text(svg(), encoding="utf-8")
    big.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    brand = '''"""Logo de l'appli (généré par tools/make_icon.py : ne pas modifier à la main) : petit, à gauche du nom ; grand et voilé, quand aucun message n'est ouvert."""

LOGO_PNG = "__SMALL__"
LOGO_BIG_PNG = "__BIG__"


def logo_img(size: int = 26) -> str:
    """Balise <img> du logo, sans fichier à embarquer."""
    return (f'<img src="data:image/png;base64,{LOGO_PNG}" alt="Velora" '
            f'style="width:{size}px;height:{size}px;display:block;flex:none;border-radius:{round(size * 0.22)}px">')


def logo_big(size: int = 220, opacity: float = 0.22) -> str:
    """Grand logo voilé (discret), affiché à la place de la lecture d'un message."""
    return (f'<img src="data:image/png;base64,{LOGO_BIG_PNG}" alt="" draggable="false" '
            f'style="width:{size}px;height:{size}px;opacity:{opacity};display:block;user-select:none">')
'''
    (ROOT / "mailia" / "brand.py").write_text(brand.replace("__SMALL__", logo_png(big)).replace("__BIG__", mark_png(420)), encoding="utf-8")
    if shutil.which("iconutil"):
        iconset = ASSETS / "icon.iconset"
        iconset.mkdir(exist_ok=True)
        for base in (16, 32, 128, 256, 512):
            for factor in (1, 2):
                px = base * factor
                big.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{base}x{base}{'@2x' if factor == 2 else ''}.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ASSETS / "icon.icns")], check=True)
        shutil.rmtree(iconset)
    else:
        print("iconutil absent : icon.icns non régénéré", file=sys.stderr)


if __name__ == "__main__":
    main()
