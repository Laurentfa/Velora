"""Faux fournisseur OAuth pour tester la connexion sans compte réel : python tools/mock_oauth.py [port]
/authorize redirige vers l'appli avec un code ; /token échange le code ou le jeton de renouvellement."""
import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

CODES: dict[str, dict] = {}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        if u.path == "/authorize":
            CODES["abc"] = {"challenge": q.get("code_challenge"), "redirect": q["redirect_uri"]}
            self.send_response(302)
            self.send_header("Location", f"{q['redirect_uri']}/?code=abc&state={q['state']}")
            self.end_headers()
            return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        body = urllib.parse.parse_qs(self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode())
        g = {k: v[0] for k, v in body.items()}
        out = None
        if g.get("grant_type") == "authorization_code" and g.get("code") == "abc":
            import base64, hashlib
            ok = base64.urlsafe_b64encode(hashlib.sha256(g["code_verifier"].encode()).digest()).rstrip(b"=").decode() == CODES["abc"]["challenge"]
            if ok:
                out = {"access_token": "AT1", "refresh_token": "RT1", "expires_in": 3600}
        elif g.get("grant_type") == "refresh_token" and g.get("refresh_token") == "RT1":
            out = {"access_token": "AT2", "expires_in": 3600}
        data = json.dumps(out or {"error": "invalid_grant", "error_description": "refusé par le faux serveur"}).encode()
        self.send_response(200 if out else 400)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8098), H).serve_forever()
