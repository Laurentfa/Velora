"""Contexte TLS pour les connexions IMAP et SMTP : le certificat du serveur est TOUJOURS vérifié (sinon n'importe qui sur le même Wi-Fi
pourrait se faire passer pour votre serveur de messagerie et lire votre mot de passe). Le magasin de certificats est celui de « certifi »,
embarqué dans l'appli : il ne dépend pas de la configuration du système. Un serveur à certificat auto-signé peut être accepté boîte par
boîte, en connaissance de cause (réglages avancés)."""
import ssl


def context(insecure: bool = False) -> ssl.SSLContext:
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def is_cert_error(exc: BaseException) -> bool:
    text = f"{type(exc).__name__} {exc}".lower()
    return isinstance(exc, ssl.SSLCertVerificationError) or "certificate_verify_failed" in text or "certificate verify failed" in text \
        or "hostname mismatch" in text or "self-signed" in text or "self signed" in text


def opener(check_redirect=None, follow: bool = True):
    """Ouvreur urllib qui vérifie les certificats avec le même magasin que les boîtes mail. `check_redirect(url)` peut refuser une redirection
    (adresse locale, par exemple) ; `follow=False` n'en suit aucune."""
    import urllib.request

    class _Redirect(urllib.request.HTTPRedirectHandler):
        max_redirections = 3

        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not follow:
                return None
            if check_redirect and not check_redirect(newurl):
                raise OSError("redirection refusée : " + newurl[:80])
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=context()), _Redirect())
