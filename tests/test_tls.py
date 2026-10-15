import socket
import ssl

import pytest
from mailia import tlsutil


def test_context_verifies_by_default():
    ctx = tlsutil.context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    bad = tlsutil.context(insecure=True)
    assert bad.verify_mode == ssl.CERT_NONE and not bad.check_hostname


def test_cert_error_detection():
    assert tlsutil.is_cert_error(ssl.SSLCertVerificationError("certificate verify failed: self-signed certificate"))
    assert tlsutil.is_cert_error(OSError("[SSL: CERTIFICATE_VERIFY_FAILED] ..."))
    assert not tlsutil.is_cert_error(ConnectionRefusedError("refused"))


def test_imap_and_smtp_use_verifying_context():
    """Les connexions de réception et d'envoi doivent recevoir le contexte qui vérifie les certificats."""
    from mailia.providers import imap
    import inspect
    src = inspect.getsource(imap)
    assert "tlsutil.context(self.cfg.tls_insecure)" in src and "starttls(context=ctx)" in src and "SMTP_SSL(self.cfg.smtp_host, 465, timeout=30, context=ctx)" in src
    assert "ssl_context=ctx" in src


@pytest.mark.network
def test_bad_certificates_refused_for_real():
    ctx = tlsutil.context()
    for host in ("expired.badssl.com", "self-signed.badssl.com", "wrong.host.badssl.com"):
        try:
            with socket.create_connection((host, 443), timeout=6) as s:
                with pytest.raises(ssl.SSLCertVerificationError):
                    ctx.wrap_socket(s, server_hostname=host)
        except OSError:
            pytest.skip("pas de réseau")
