//! A throwaway HTTPS server with a freshly generated self-signed certificate, for tests.
#![allow(dead_code)]

use std::sync::Arc;

use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

pub struct TestServer {
    pub port: u16,
    pub fp: String,
}

pub fn make_cert() -> (CertificateDer<'static>, PrivatePkcs8KeyDer<'static>) {
    let ck = rcgen::generate_simple_self_signed(vec!["127.0.0.1".to_string()]).unwrap();
    (ck.cert.der().clone(), PrivatePkcs8KeyDer::from(ck.key_pair.serialize_der()))
}

pub fn fingerprint(cert: &CertificateDer<'_>) -> String {
    Sha256::digest(cert.as_ref()).iter().map(|b| format!("{b:02x}")).collect()
}

pub fn acceptor(cert: CertificateDer<'static>, key: PrivatePkcs8KeyDer<'static>) -> TlsAcceptor {
    cyph_app_lib::init_crypto();
    let provider = Arc::new(rustls::crypto::ring::default_provider());
    let cfg = rustls::ServerConfig::builder_with_provider(provider)
        .with_safe_default_protocol_versions()
        .unwrap()
        .with_no_client_auth()
        .with_single_cert(vec![cert], PrivateKeyDer::Pkcs8(key))
        .unwrap();
    TlsAcceptor::from(Arc::new(cfg))
}

/// Answers every HTTP request with `status` and the JSON `body`, until the test process ends.
pub async fn https_json(status: u16, body: &'static str) -> TestServer {
    let (cert, key) = make_cert();
    let fp = fingerprint(&cert);
    let acceptor = acceptor(cert, key);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let Ok((tcp, _)) = listener.accept().await else { return };
            let acceptor = acceptor.clone();
            tokio::spawn(async move {
                let Ok(mut tls) = acceptor.accept(tcp).await else { return };
                let mut buf = [0u8; 4096];
                let mut seen = Vec::new();
                while !seen.windows(4).any(|w| w == b"\r\n\r\n") {
                    match tls.read(&mut buf).await {
                        Ok(0) | Err(_) => return,
                        Ok(n) => seen.extend_from_slice(&buf[..n]),
                    }
                }
                let reply = format!(
                    "HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = tls.write_all(reply.as_bytes()).await;
                let _ = tls.shutdown().await;
            });
        }
    });
    TestServer { port, fp }
}
