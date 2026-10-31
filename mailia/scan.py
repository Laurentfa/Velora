"""Numérisation de documents, sur Mac comme sur Windows, sans pilote ni application tierce.

Deux sources de pages :
- un scanner / une imprimante multifonction du réseau, par le protocole standard eSCL (AirScan, Mopria) ;
- le téléphone : l'ordinateur affiche un QR code, le téléphone ouvre une page sur le réseau local, photographie, recadre et envoie.

Les pages sont ensuite assemblées en un seul PDF (couleur, gris ou noir et blanc, taille maîtrisée). Rien ne passe par un service
en ligne ; le téléphone et l'ordinateur parlent directement sur le réseau local.
"""
import hmac
import http.client
import io
import ipaddress
import json
import logging
import re
import secrets
import socket
import ssl
import threading
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlparse

from PIL import Image, ImageChops, ImageFilter, ImageOps

log = logging.getLogger("scan")

MAX_PAGES = 80
MAX_PDF_BYTES = 20 * 1024 * 1024  # l'envoi d'un message est limité à 25 Mo au total
A4_LONG_INCH = 11.69


class ScanError(Exception):
    """Message destiné à l'utilisateur."""


# =====================================================================================
#  Scanner réseau (eSCL)
# =====================================================================================
_NO_VERIFY = ssl.create_default_context()
_NO_VERIFY.check_hostname = False
_NO_VERIFY.verify_mode = ssl.CERT_NONE  # les scanners ont des certificats auto-signés ; on ne parle qu'à des adresses locales


def _is_local(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return host.lower().endswith(".local") or "." not in host


def _http(method: str, url: str, body: bytes | None = None, headers: dict | None = None, timeout: float = 30) -> tuple[int, dict, bytes]:
    u = urlparse(url)
    if not u.hostname or not _is_local(u.hostname):
        raise ScanError("Cette adresse n'est pas sur votre réseau local.")
    tls = u.scheme == "https"
    try:
        conn = (http.client.HTTPSConnection(u.hostname, u.port or 443, timeout=timeout, context=_NO_VERIFY) if tls
                else http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout))
        conn.request(method, (u.path or "/") + (f"?{u.query}" if u.query else ""), body, headers or {})
        r = conn.getresponse()
        data = r.read()
        out = (r.status, {k.lower(): v for k, v in r.getheaders()}, data)
        conn.close()
        return out
    except (OSError, http.client.HTTPException) as e:
        raise ScanError(f"Le scanner ne répond pas ({e.__class__.__name__}). Vérifiez qu'il est allumé et sur le même réseau.") from e


def normalize_base(addr: str) -> str:
    """« 192.168.1.20 », « 192.168.1.20:8080 » ou une URL complète -> URL de base eSCL."""
    a = (addr or "").strip()
    if not a:
        raise ScanError("Indiquez l'adresse du scanner.")
    if "://" not in a:
        a = "http://" + a
    u = urlparse(a)
    if not u.hostname:
        raise ScanError("Adresse de scanner invalide.")
    path = u.path.rstrip("/") or "/eSCL"
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{u.hostname}{port}{path}"


def known() -> list[dict]:
    """Scanners déjà rencontrés ou utilisés, mémorisés d'une fois sur l'autre : [{name, base}], le dernier utilisé en premier."""
    from . import db
    try:
        items = json.loads(db.kv_get("scanners") or "[]")
    except ValueError:
        return []
    return [s for s in items if isinstance(s, dict) and s.get("base")]


def remember(name: str, base: str, used: bool = False) -> None:
    """Mémorise un scanner. Même nom : on met à jour son adresse (elle peut changer avec le routeur). `used` : il passe en tête (choisi par défaut)."""
    from . import db
    items = known()
    old = next((s for s in items if s["base"] == base or (name and s.get("name") == name)), None)
    entry = {"name": name or (old or {}).get("name") or base, "base": base}
    if used:
        items = [entry] + [s for s in items if s is not old]
    elif old is not None:
        items = [entry if s is old else s for s in items]
    else:
        items = items + [entry]
    db.kv_set("scanners", json.dumps(items[:12], ensure_ascii=False))


def forget(base: str) -> None:
    from . import db
    db.kv_set("scanners", json.dumps([s for s in known() if s["base"] != base], ensure_ascii=False))


def discover(timeout: float = 3.0) -> list[dict]:
    """Scanners eSCL annoncés sur le réseau local (Bonjour/mDNS). [{name, base}]"""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    found: dict[str, dict] = {}
    types = ["_uscan._tcp.local.", "_uscans._tcp.local."]
    zc = Zeroconf()
    try:
        class Listener:
            def add_service(self, zc_, type_, name):
                try:
                    info = zc_.get_service_info(type_, name, timeout=2000)
                    if not info:
                        return
                    addrs = [a for a in info.parsed_addresses() if ":" not in a] or info.parsed_addresses()
                    if not addrs:
                        return
                    props = {(k.decode() if isinstance(k, bytes) else k): (v.decode(errors="replace") if isinstance(v, bytes) else v)
                             for k, v in (info.properties or {}).items()}
                    rs = (props.get("rs") or "eSCL").strip("/")
                    host = addrs[0] if ":" not in addrs[0] else f"[{addrs[0]}]"
                    scheme = "https" if type_.startswith("_uscans") else "http"
                    label = props.get("ty") or name.split(".")[0]
                    found.setdefault(f"{label}|{addrs[0]}", {"name": label, "base": f"{scheme}://{host}:{info.port}/{rs}"})
                except Exception:
                    log.debug("service mDNS ignoré", exc_info=True)

            update_service = remove_service = lambda self, *a, **k: None

        browsers = [ServiceBrowser(zc, t, Listener()) for t in types]
        time.sleep(timeout)
        for b in browsers:
            b.cancel()
    except Exception:
        log.warning("détection des scanners", exc_info=True)
    finally:
        try:
            zc.close()
        except Exception:
            pass
    return list(found.values())


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _all(el, name: str) -> list:
    return [e for e in el.iter() if _local(e.tag) == name]


def capabilities(base: str) -> dict:
    """{'name', 'platen', 'feeder', 'duplex', 'dpis', 'width', 'height', 'jpeg'} d'après ScannerCapabilities."""
    status, _, data = _http("GET", base + "/ScannerCapabilities", timeout=10)
    if status != 200:
        raise ScanError(f"Ce scanner n'accepte pas la numérisation réseau (code {status}).")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ScanError("Réponse du scanner illisible.") from e
    text = lambda el, n: next((e.text.strip() for e in _all(el, n) if e.text), "")
    platen = _all(root, "Platen")
    adf = _all(root, "Adf")
    scope = platen[0] if platen else (adf[0] if adf else root)
    dpis = sorted({int(e.text) for e in _all(scope, "XResolution") if (e.text or "").strip().isdigit()})
    width = int(text(scope, "MaxWidth") or 2550)
    height = int(text(scope, "MaxHeight") or 3507)
    formats = {(e.text or "").strip() for e in _all(root, "DocumentFormat") + _all(root, "DocumentFormatExt")}
    return {
        "name": text(root, "MakeAndModel") or text(root, "Model") or "Scanner",
        "platen": bool(platen), "feeder": bool(adf),
        "duplex": bool(_all(root, "AdfDuplexInputCaps")),
        "dpis": dpis, "width": width, "height": height,
        "jpeg": (not formats) or ("image/jpeg" in formats),
    }


def adf_loaded(base: str) -> bool:
    """Le chargeur automatique contient-il des feuilles ? (ScannerStatus ; False si le scanner ne le dit pas)"""
    try:
        status, _, data = _http("GET", base + "/ScannerStatus", timeout=8)
        return status == 200 and b"ScannerAdfLoaded" in data
    except ScanError:
        return False


def is_blank(data: bytes) -> bool:
    """Page quasi uniforme (vitre vide, document resté dans le chargeur…)."""
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
        im.thumbnail((96, 96))
        px = im.tobytes()
        mean = sum(px) / len(px)
        var = sum((p - mean) ** 2 for p in px) / len(px)
        return var ** 0.5 < 5 and mean > 190
    except Exception:
        return False


def scan(base: str, source: str = "auto", color: str = "color", dpi: int = 200, caps: dict | None = None) -> list[bytes]:
    """Numérise et retourne les pages (JPEG). source : auto (chargeur s'il est garni, sinon vitre) | platen (vitre) | feeder (chargeur)
    | duplex (chargeur recto-verso)."""
    caps = caps or capabilities(base)
    if source == "auto":
        source = "feeder" if caps["feeder"] and adf_loaded(base) else "platen"
    if not caps["jpeg"]:
        raise ScanError("Ce scanner ne fournit pas d'images JPEG : numérisation impossible depuis l'appli.")
    if source in ("feeder", "duplex") and not caps["feeder"]:
        raise ScanError("Ce scanner n'a pas de chargeur automatique.")
    if source == "platen" and not caps["platen"]:
        source = "feeder"
    if caps["dpis"] and dpi not in caps["dpis"]:
        dpi = min(caps["dpis"], key=lambda d: abs(d - dpi))
    mode = "RGB24" if color == "color" else "Grayscale8"
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm" xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
<pwg:Version>2.0</pwg:Version>
<pwg:ScanRegions><pwg:ScanRegion><pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>{caps['width']}</pwg:Width><pwg:Height>{caps['height']}</pwg:Height></pwg:ScanRegion></pwg:ScanRegions>
<pwg:InputSource>{'Platen' if source == 'platen' else 'Feeder'}</pwg:InputSource>
<scan:ColorMode>{mode}</scan:ColorMode>
<scan:XResolution>{dpi}</scan:XResolution><scan:YResolution>{dpi}</scan:YResolution>
<pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
{'<scan:Duplex>true</scan:Duplex>' if source == 'duplex' else ''}
</scan:ScanSettings>"""
    status = 503
    for _ in range(8):  # scanner occupé : on réessaie un peu
        status, hdr, _data = _http("POST", base + "/ScanJobs", xml.encode(), {"Content-Type": "text/xml"}, timeout=20)
        if status != 503:
            break
        time.sleep(1.5)
    if status in (409, 503):
        raise ScanError("Le scanner est occupé, ou le chargeur est vide / un capot est ouvert.")
    if status not in (200, 201) or not hdr.get("location"):
        raise ScanError(f"Le scanner a refusé la numérisation (code {status}).")
    b = urlparse(base)
    job = urlparse(urljoin(base + "/", hdr["location"]))
    job_url = f"{b.scheme}://{b.netloc}{job.path}".rstrip("/")  # on garde l'adresse qui répond, pas un nom que le Mac ne saurait pas résoudre
    pages: list[bytes] = []
    try:
        for _ in range(MAX_PAGES):
            st = 503
            for _try in range(40):
                st, _h, data = _http("GET", job_url + "/NextDocument", timeout=120)
                if st != 503:
                    break
                time.sleep(1.5)
            if st == 200 and data:
                pages.append(data)
                continue
            if st in (404, 410):
                break
            raise ScanError(f"Le scanner a interrompu la numérisation (code {st}).")
    finally:
        try:
            _http("DELETE", job_url, timeout=5)
        except ScanError:
            pass
    if not pages:
        raise ScanError("Aucune page n'a été numérisée (chargeur vide ?).")
    return pages


# =====================================================================================
#  Images -> PDF
# =====================================================================================
def _otsu(im: Image.Image) -> int:
    hist = im.histogram()[:256]
    total = sum(hist)
    sum_all = sum(i * h for i, h in enumerate(hist))
    wb = sum_b = 0
    best, thr = -1.0, 128
    for t in range(256):
        wb += hist[t]
        if not wb:
            continue
        wf = total - wb
        if not wf:
            break
        sum_b += t * hist[t]
        mb, mf = sum_b / wb, (sum_all - sum_b) / wf
        var = wb * wf * (mb - mf) ** 2
        if var > best:
            best, thr = var, t
    return thr


def _flatten(gray: Image.Image) -> Image.Image:
    """Uniformise l'éclairage d'une photo de document : le fond devient blanc, le texte reste net."""
    small = gray.copy()
    small.thumbnail((600, 600))
    bg = small.filter(ImageFilter.GaussianBlur(25)).filter(ImageFilter.MaxFilter(9)).resize(gray.size, Image.BILINEAR)
    return ImageOps.autocontrast(ImageChops.add(gray, ImageChops.invert(bg)), cutoff=1)


def _prepare(data: bytes, mode: str, dpi: int, enhance: bool) -> Image.Image:
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        im.load()
    except Exception as e:
        raise ScanError("Une des pages n'est pas une image lisible.") from e
    side = int(A4_LONG_INCH * dpi)
    im.thumbnail((side, side), Image.LANCZOS)
    if mode == "color":
        im = im.convert("RGB")
        return ImageOps.autocontrast(im, cutoff=0.5) if enhance else im
    gray = im.convert("L")
    if enhance:
        gray = _flatten(gray)
    if mode == "gray":
        return gray
    return gray.point(lambda p, t=_otsu(gray): 255 if p > t else 0).convert("1")


def build_pdf(pages: list[bytes], mode: str = "color", dpi: int = 200, enhance: bool | list[bool] = False) -> bytes:
    """Assemble les pages en un PDF. mode : color | gray | bw. Réduit la qualité si le fichier dépasse la taille maximale."""
    if not pages:
        raise ScanError("Aucune page à ajouter.")
    quality, cur_dpi = 80, dpi
    for _ in range(6):
        flags = enhance if isinstance(enhance, list) else [enhance] * len(pages)  # amélioration par page : les photos en ont besoin, pas les scans
        ims = [_prepare(p, mode, cur_dpi, flags[i]) for i, p in enumerate(pages)]
        res = max(1.0, sum(max(i.size) for i in ims) / len(ims) / A4_LONG_INCH)
        out = io.BytesIO()
        opts = {} if mode == "bw" else {"quality": quality}  # le noir et blanc est compressé sans perte (CCITT)
        ims[0].save(out, "PDF", save_all=True, append_images=ims[1:], resolution=res, **opts)
        if out.tell() <= MAX_PDF_BYTES:
            return out.getvalue()
        quality, cur_dpi = max(45, quality - 15), int(cur_dpi * 0.8)
    raise ScanError("Ce document est trop volumineux pour être joint (20 Mo maximum). Scannez moins de pages ou en noir et blanc.")


def to_jpeg(data: bytes) -> bytes:
    """Toute image lisible (PNG, JPEG, HEIC si disponible…) en JPEG, orientation corrigée."""
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    out = io.BytesIO()
    im.save(out, "JPEG", quality=90)
    return out.getvalue()


def thumbnail(data: bytes, size: int = 240) -> bytes:
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((size, size))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=70)
    return out.getvalue()


def warp(data: bytes, corners: list[float] | None) -> bytes:
    """Redresse une photo selon 4 coins (x,y normalisés 0..1 : haut-gauche, haut-droit, bas-droit, bas-gauche). JPEG."""
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    im.thumbnail((3000, 3000))
    w, h = im.size
    if corners and len(corners) == 8 and all(0 <= c <= 1 for c in corners):
        p = [(corners[i] * w, corners[i + 1] * h) for i in range(0, 8, 2)]
        dist = lambda a, b: ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5
        ow = int(max(dist(p[0], p[1]), dist(p[3], p[2])))
        oh = int(max(dist(p[0], p[3]), dist(p[1], p[2])))
        if ow >= 100 and oh >= 100:
            tl, tr, br, bl = p
            im = im.transform((ow, oh), Image.QUAD, (*tl, *bl, *br, *tr), Image.BICUBIC)  # ordre PIL : haut-gauche, bas-gauche, bas-droit, haut-droit
    out = io.BytesIO()
    im.save(out, "JPEG", quality=88)
    return out.getvalue()


# =====================================================================================
#  Téléphone : petit serveur local, le temps d'une numérisation
# =====================================================================================
def lan_ip() -> str:
    """Adresse de l'ordinateur sur le réseau local (aucun paquet n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class PhoneSession:
    def __init__(self) -> None:
        self.token = secrets.token_urlsafe(12)
        self.pages: list[bytes] = []
        self.done = False
        self.created = time.time()
        self.lock = threading.Lock()

    @property
    def expired(self) -> bool:
        return time.time() - self.created > 30 * 60


MOBILE_PAGE = r"""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>Numériser</title>
<style>
*{box-sizing:border-box}body{margin:0;font:16px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;background:#f5f7fa;color:#1b2430;padding:16px}
h1{font-size:20px;margin:4px 0 2px}p{margin:6px 0;color:#5a6b80}
.btn{display:block;width:100%;border:0;border-radius:14px;padding:16px;font-size:17px;font-weight:600;margin:10px 0;color:#fff;
 background:linear-gradient(180deg,#2f80f2,#0a66e8);box-shadow:0 2px 6px rgba(10,90,220,.35)}
.btn.sec{background:#fff;color:#0a66e8;border:1.5px solid #0a66e8;box-shadow:none}.btn.grey{background:#e6ebf2;color:#33445a;box-shadow:none}
.card{background:#fff;border:1px solid #dfe6ef;border-radius:16px;padding:14px;margin:12px 0}
.count{font-size:34px;font-weight:700;color:#0a66e8}canvas{width:100%;touch-action:none;border-radius:10px;background:#000;display:block}
.hide{display:none}.row{display:flex;gap:10px}.row .btn{margin:10px 0}.ok{color:#18803a;font-weight:600}
</style></head><body>
<h1>✦ Numériser un document</h1>
<div id="home">
 <div class="card"><div class="count" id="n">0</div><p id="lbl">page envoyée à l'ordinateur</p></div>
 <button class="btn" id="shoot">📷 Photographier une page</button>
 <button class="btn sec" id="pick">🖼 Choisir une photo existante</button>
 <button class="btn grey" id="finish">Terminer</button>
 <p>Posez la feuille sur une surface plane, bien éclairée, puis ajustez les 4 coins avant de valider.</p>
</div>
<div id="crop" class="hide">
 <p>Faites glisser les 4 points sur les coins de la feuille.</p>
 <canvas id="cv"></canvas>
 <div class="row"><button class="btn grey" id="retake">Reprendre</button><button class="btn" id="send">Valider</button></div>
</div>
<div id="done" class="hide"><div class="card"><p class="ok">✓ Terminé</p><p>Vous pouvez revenir à l'ordinateur : les pages y sont déjà.</p></div></div>
<input type="file" id="f1" accept="image/*" capture="environment" class="hide">
<input type="file" id="f2" accept="image/*" class="hide">
<script>
const T = location.pathname.split('/')[2];
const $ = id => document.getElementById(id);
let img = null, pts = [], drag = -1, scale = 1;
const cv = $('cv'), ctx = cv.getContext('2d');
function show(id){ for (const s of ['home','crop','done']) $(s).classList.toggle('hide', s !== id); }
async function load(file){
  if(!file) return;
  let bmp;
  try { bmp = await createImageBitmap(file, {imageOrientation:'from-image'}); }
  catch(e){ bmp = await new Promise((ok,ko)=>{const i=new Image(); i.onload=()=>ok(i); i.onerror=ko; i.src=URL.createObjectURL(file);}); }
  const m = 2800, k = Math.min(1, m / Math.max(bmp.width, bmp.height));
  const c = document.createElement('canvas'); c.width = Math.round(bmp.width*k); c.height = Math.round(bmp.height*k);
  c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height); img = c;
  const w = Math.min(window.innerWidth - 32, 640); scale = w / c.width;
  cv.width = Math.round(c.width * scale); cv.height = Math.round(c.height * scale);
  const i = .05; pts = [[i,i],[1-i,i],[1-i,1-i],[i,1-i]];
  show('crop'); draw();
}
function draw(){
  ctx.clearRect(0,0,cv.width,cv.height); ctx.drawImage(img,0,0,cv.width,cv.height);
  ctx.fillStyle='rgba(0,0,0,.45)'; ctx.beginPath(); ctx.rect(0,0,cv.width,cv.height);
  ctx.moveTo(pts[0][0]*cv.width,pts[0][1]*cv.height); for (const j of [3,2,1]) ctx.lineTo(pts[j][0]*cv.width,pts[j][1]*cv.height);
  ctx.closePath(); ctx.fill('evenodd');
  ctx.strokeStyle='#2f80f2'; ctx.lineWidth=3; ctx.beginPath();
  pts.forEach((p,j)=>{ j?ctx.lineTo(p[0]*cv.width,p[1]*cv.height):ctx.moveTo(p[0]*cv.width,p[1]*cv.height); }); ctx.closePath(); ctx.stroke();
  for (const p of pts){ ctx.fillStyle='#fff'; ctx.beginPath(); ctx.arc(p[0]*cv.width,p[1]*cv.height,13,0,7); ctx.fill(); ctx.strokeStyle='#0a66e8'; ctx.stroke(); }
}
function pos(e){ const r=cv.getBoundingClientRect(); return [(e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height]; }
cv.addEventListener('pointerdown',e=>{ const [x,y]=pos(e); let b=-1,bd=.09; pts.forEach((p,j)=>{const d=Math.hypot(p[0]-x,(p[1]-y)*cv.height/cv.width); if(d<bd){bd=d;b=j;}}); drag=b; if(b>=0)cv.setPointerCapture(e.pointerId); });
cv.addEventListener('pointermove',e=>{ if(drag<0)return; const [x,y]=pos(e); pts[drag]=[Math.min(1,Math.max(0,x)),Math.min(1,Math.max(0,y))]; draw(); });
cv.addEventListener('pointerup',()=>drag=-1);
$('shoot').onclick=()=>$('f1').click(); $('pick').onclick=()=>$('f2').click();
$('f1').onchange=e=>{load(e.target.files[0]); e.target.value='';}; $('f2').onchange=e=>{load(e.target.files[0]); e.target.value='';};
$('retake').onclick=()=>show('home');
$('send').onclick=async()=>{
  const b=$('send'); b.disabled=true; b.textContent='Envoi…';
  const blob=await new Promise(ok=>img.toBlob(ok,'image/jpeg',.88));
  try{
    const r=await fetch('/s/'+T+'/page',{method:'POST',headers:{'X-Corners':pts.flat().map(v=>v.toFixed(4)).join(',')},body:blob});
    if(!r.ok) throw new Error(r.status); const j=await r.json(); $('n').textContent=j.n; $('lbl').textContent=j.n>1?'pages envoyées à l\'ordinateur':'page envoyée à l\'ordinateur'; show('home');
  }catch(e){ alert('Envoi impossible : vérifiez que le téléphone est sur le même Wi-Fi que l\'ordinateur.'); }
  b.disabled=false; b.textContent='Valider';
};
$('finish').onclick=async()=>{ try{ await fetch('/s/'+T+'/done',{method:'POST'}); }catch(e){} show('done'); };
</script></body></html>"""


class _Handler(BaseHTTPRequestHandler):
    session: PhoneSession  # défini dans la sous-classe créée par PhoneServer
    protocol_version = "HTTP/1.1"
    MAX_UPLOAD = 20 * 1024 * 1024

    def log_message(self, *a) -> None:
        pass

    def _reply(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _route(self) -> str | None:
        m = re.fullmatch(r"/s/([A-Za-z0-9_-]{8,40})(/page|/done)?", self.path.split("?")[0])
        if not m or not hmac.compare_digest(m.group(1), self.session.token) or self.session.expired:
            return None
        return m.group(2) or ""

    def do_GET(self) -> None:
        if self._route() != "":
            return self._reply(404, b"Introuvable")
        self._reply(200, MOBILE_PAGE.encode(), "text/html; charset=utf-8")

    def do_POST(self) -> None:
        route = self._route()
        if route not in ("/page", "/done"):
            return self._reply(404, b"Introuvable")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._reply(400, b"Requete invalide")
        if n < 0 or n > self.MAX_UPLOAD:
            return self._reply(413, b"Trop volumineux")
        body = self.rfile.read(n) if n else b""
        s = self.session
        if route == "/done":
            s.done = True
            return self._reply(200, b"{}", "application/json")
        if len(s.pages) >= MAX_PAGES:
            return self._reply(409, b"Trop de pages")
        try:
            corners = [float(v) for v in (self.headers.get("X-Corners") or "").split(",") if v.strip()]
            page = warp(body, corners or None)
        except Exception:
            return self._reply(400, b"Image illisible")
        with s.lock:
            s.pages.append(page)
            count = len(s.pages)
        self._reply(200, json.dumps({"n": count}).encode(), "application/json")


class PhoneServer:
    """Serveur local temporaire, ouvert seulement pendant que la fenêtre de numérisation est affichée."""

    def __init__(self) -> None:
        self.session = PhoneSession()
        session = self.session
        handler = type("Handler", (_Handler,), {"session": session})
        self.httpd = ThreadingHTTPServer(("0.0.0.0", 0), handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, host: str | None = None) -> str:
        return f"http://{host or lan_ip()}:{self.port}/s/{self.session.token}"

    def stop(self) -> None:
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:
            pass


def qr_svg(url: str) -> str:
    import segno
    return segno.make(url, error="m").svg_inline(border=2, dark="#1b2430", light="#ffffff", omitsize=True)
