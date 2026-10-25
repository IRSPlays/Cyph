//! Certificate pinning. The server's certificate is self-signed, so there is no certificate authority to trust:
//! we accept exactly one end-entity certificate, identified by the SHA-256 of its DER encoding, and ignore the
//! host name (the address is a tunnel address that never appears in the certificate).

use std::sync::Arc;

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{ring, verify_tls12_signature, verify_tls13_signature, CryptoProvider};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{CertificateError, ClientConfig, DigitallySignedStruct, Error, SignatureScheme};
use sha2::{Digest, Sha256};

/// "e5:61:..." / "E561..." / " e561...\n" -> 32 bytes.
pub fn normalize_fp(text: &str) -> Result<[u8; 32], String> {
    let hex: String = text.chars().filter(|c| !c.is_whitespace() && *c != ':').collect::<String>().to_lowercase();
    if hex.len() != 64 || !hex.chars().all(|c| c.is_ascii_hexdigit()) {
        return Err("the certificate fingerprint must be 64 hex characters".into());
    }
    let mut out = [0u8; 32];
    for (i, byte) in out.iter_mut().enumerate() {
        *byte = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).map_err(|e| e.to_string())?;
    }
    Ok(out)
}

fn same(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

#[derive(Debug)]
struct Pinned {
    fingerprint: [u8; 32],
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for Pinned {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _server_name: &ServerName<'_>,
        _ocsp_response: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, Error> {
        let got = Sha256::digest(end_entity.as_ref());
        if same(&got, &self.fingerprint) {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(Error::InvalidCertificate(CertificateError::ApplicationVerificationFailure))
        }
    }

    fn verify_tls12_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, Error> {
        verify_tls12_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn verify_tls13_signature(
        &self,
        message: &[u8],
        cert: &CertificateDer<'_>,
        dss: &DigitallySignedStruct,
    ) -> Result<HandshakeSignatureValid, Error> {
        verify_tls13_signature(message, cert, dss, &self.provider.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider.signature_verification_algorithms.supported_schemes()
    }
}

/// A rustls client configuration that trusts only the certificate with this fingerprint.
pub fn client_config(fp_hex: &str) -> Result<ClientConfig, String> {
    let fingerprint = normalize_fp(fp_hex)?;
    let provider = Arc::new(ring::default_provider());
    let verifier = Arc::new(Pinned { fingerprint, provider: provider.clone() });
    Ok(ClientConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .map_err(|e| e.to_string())?
        .dangerous()
        .with_custom_certificate_verifier(verifier)
        .with_no_client_auth())
}

#[cfg(test)]
mod tests {
    use super::*;

    const HEX: &str = "e561069748c67beb0e05a9a0e8e5bd6a8f3a3f3d2f55c0c5b7a1b2c3d4e5f607";

    #[test]
    fn accepts_plain_hex() {
        assert_eq!(normalize_fp(HEX).unwrap().len(), 32);
    }

    #[test]
    fn accepts_colons_spaces_and_uppercase() {
        let colon: String = HEX.to_uppercase().as_bytes().chunks(2)
            .map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(":");
        assert_eq!(normalize_fp(&colon).unwrap(), normalize_fp(HEX).unwrap());
        assert_eq!(normalize_fp(&format!("  {HEX}\n")).unwrap(), normalize_fp(HEX).unwrap());
    }

    #[test]
    fn rejects_bad_fingerprints() {
        let too_long = format!("{HEX}00");
        let not_hex = HEX.replace('e', "z");
        for bad in ["", "abc", &HEX[..62], too_long.as_str(), not_hex.as_str()] {
            assert!(normalize_fp(bad).is_err(), "{bad:?} should be rejected");
        }
    }

    #[test]
    fn client_config_builds_for_a_good_fingerprint() {
        crate::init_crypto();
        assert!(client_config(HEX).is_ok());
        assert!(client_config("nope").is_err());
    }
}
