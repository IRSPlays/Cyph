"""A self-signed certificate for the API. The apps do not trust a certificate authority: they pin this
certificate's SHA-256 fingerprint, which is printed when you make a pair code. It lives in data/core/ on the
server and is made on first use (valid ten years)."""
from __future__ import annotations

import hashlib
import ipaddress
import os
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fingerprint(cert_path) -> str:
    der = ssl.PEM_cert_to_DER_cert(Path(cert_path).read_text(encoding="ascii"))
    return hashlib.sha256(der).hexdigest()


def ensure_cert(folder, hosts=("127.0.0.1",)):
    """(cert_path, key_path, sha256_hex). Creates the pair if it is missing."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / "core.crt", folder / "core.key"
    if not (cert.exists() and key.exists()):
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID
        k = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "cyph-core")])
        now = datetime.now(timezone.utc)
        san = x509.SubjectAlternativeName([x509.DNSName("cyph-core")]
                                          + [x509.IPAddress(ipaddress.ip_address(h)) for h in hosts])
        c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
             .serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
             .add_extension(san, critical=False).sign(k, hashes.SHA256()))
        pem = k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption())
        key.unlink(missing_ok=True)                           # a leftover key without its cert is replaced
        # Created owner-only from the start (O_EXCL: never a moment where it is world-readable or someone else's file).
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
        try:
            os.chmod(key, 0o600)
        except OSError:
            pass
        cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key, fingerprint(cert)
