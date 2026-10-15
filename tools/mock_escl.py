"""Faux scanner eSCL pour tester la numérisation sans matériel : python tools/mock_escl.py [port]
Vitre : 1 page ; chargeur : 3 pages. Les pages sont des images de test (fond clair, texte, cadre)."""
import io
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageDraw

CAPS = """<?xml version="1.0"?><scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
<pwg:Version>2.0</pwg:Version><pwg:MakeAndModel>Faux scanner de test</pwg:MakeAndModel>
<scan:Platen><scan:PlatenInputCaps><scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3507</scan:MaxHeight>
<scan:SettingProfiles><scan:SettingProfile><scan:ColorModes><scan:ColorMode>RGB24</scan:ColorMode></scan:ColorModes>
<scan:DocumentFormats><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat></scan:DocumentFormats>
<scan:SupportedResolutions><scan:DiscreteResolutions><scan:DiscreteResolution><scan:XResolution>150</scan:XResolution><scan:YResolution>150</scan:YResolution></scan:DiscreteResolution>
<scan:DiscreteResolution><scan:XResolution>300</scan:XResolution><scan:YResolution>300</scan:YResolution></scan:DiscreteResolution></scan:DiscreteResolutions></scan:SupportedResolutions>
</scan:SettingProfile></scan:SettingProfiles></scan:PlatenInputCaps></scan:Platen>
<scan:Adf><scan:AdfSimplexInputCaps><scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3507</scan:MaxHeight></scan:AdfSimplexInputCaps>
<scan:AdfDuplexInputCaps><scan:MaxWidth>2550</scan:MaxWidth><scan:MaxHeight>3507</scan:MaxHeight></scan:AdfDuplexInputCaps></scan:Adf>
</scan:ScannerCapabilities>"""

JOBS: dict[str, dict] = {}


def page(n: int) -> bytes:
    im = Image.new("RGB", (1240, 1754), (250, 248, 240))
    d = ImageDraw.Draw(im)
    d.rectangle([60, 60, 1180, 1694], outline=(30, 30, 30), width=6)
    for i in range(12):
        d.text((120, 160 + i * 60), f"Page {n} - ligne {i + 1} : texte de test du faux scanner", fill=(20, 20, 20))
    out = io.BytesIO()
    im.save(out, "JPEG", quality=85)
    return out.getvalue()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body=b"", headers=None):
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/eSCL/ScannerCapabilities":
            return self.send(200, CAPS.encode(), {"Content-Type": "text/xml"})
        if self.path == "/eSCL/ScannerStatus":
            return self.send(200, b'<scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"><pwg:State>Idle</pwg:State><scan:AdfState>ScannerAdfLoaded</scan:AdfState></scan:ScannerStatus>', {"Content-Type": "text/xml"})
        if self.path.endswith("/NextDocument"):
            job = JOBS.get(self.path.split("/")[3])
            if not job or job["left"] <= 0:
                return self.send(404)
            job["left"] -= 1
            job["done"] += 1
            return self.send(200, page(job["done"]), {"Content-Type": "image/jpeg"})
        self.send(404)

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
        if self.path == "/eSCL/ScanJobs":
            jid = str(len(JOBS) + 1)
            JOBS[jid] = {"left": 3 if "Feeder" in body else 1, "done": 0, "body": body}
            return self.send(201, headers={"Location": f"http://faux-hote.invalide:1/eSCL/ScanJobs/{jid}"})
        self.send(404)

    def do_DELETE(self):
        self.send(200)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8099
    HTTPServer(("127.0.0.1", port), H).serve_forever()
