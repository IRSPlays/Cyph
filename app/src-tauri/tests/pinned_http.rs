mod common;

use cyph_app_lib::net::{request, Server};
use serde_json::json;

fn server_for(s: &common::TestServer, fp: &str) -> Server {
    cyph_app_lib::init_crypto();
    Server { host: format!("127.0.0.1:{}", s.port), fp: fp.to_string() }
}

#[tokio::test]
async fn pinned_client_talks_to_the_pinned_server() {
    let s = common::https_json(200, r#"{"ok":true}"#).await;
    let v = request(&server_for(&s, &s.fp), "GET", "/health", None, None).await.unwrap();
    assert_eq!(v["ok"], true);
}

#[tokio::test]
async fn fingerprint_formatting_does_not_matter() {
    let s = common::https_json(200, r#"{"ok":true}"#).await;
    let spaced: String = s.fp.to_uppercase().as_bytes().chunks(2)
        .map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(":");
    assert!(request(&server_for(&s, &spaced), "GET", "/health", None, None).await.is_ok());
}

#[tokio::test]
async fn a_different_certificate_is_refused() {
    let s = common::https_json(200, r#"{"ok":true}"#).await;
    let other = common::https_json(200, r#"{"ok":true}"#).await;   // a second server has a different cert
    let err = request(&server_for(&s, &other.fp), "GET", "/health", None, None).await.unwrap_err();
    assert_eq!(err.status, 0, "a refused certificate is a network error, not an HTTP status");
}

#[tokio::test]
async fn http_errors_carry_status_and_detail() {
    let s = common::https_json(401, r#"{"detail":"not paired"}"#).await;
    let err = request(&server_for(&s, &s.fp), "GET", "/me", Some("tok"), None).await.unwrap_err();
    assert_eq!((err.status, err.detail.as_str()), (401, "not paired"));
}

#[tokio::test]
async fn bodies_are_sent_as_json() {
    let s = common::https_json(200, r#"{"device_id":"d1","token":"t"}"#).await;
    let v = request(&server_for(&s, &s.fp), "POST", "/pair", None, Some(json!({"code": "X", "name": "n"}))).await.unwrap();
    assert_eq!(v["device_id"], "d1");
}
