//! Opt-in check against a real server: CYPH_LIVE_HOST=127.0.0.1:18443 CYPH_LIVE_FP=<sha256> cargo test --test live_health -- --ignored
use cyph_app_lib::net::{request, Server};

fn live() -> Option<(String, String)> {
    Some((std::env::var("CYPH_LIVE_HOST").ok()?, std::env::var("CYPH_LIVE_FP").ok()?))
}

#[tokio::test]
#[ignore]
async fn live_pinned_health_ok_and_wrong_fingerprint_refused() {
    cyph_app_lib::init_crypto();
    let (host, fp) = live().expect("set CYPH_LIVE_HOST and CYPH_LIVE_FP");
    let ok = request(&Server { host: host.clone(), fp: fp.clone() }, "GET", "/health", None, None).await.unwrap();
    assert_eq!(ok["ok"], true);
    let mut bad = fp.clone().into_bytes();
    bad[0] = if bad[0] == b'0' { b'1' } else { b'0' };
    let err = request(&Server { host, fp: String::from_utf8(bad).unwrap() }, "GET", "/health", None, None).await.unwrap_err();
    assert_eq!(err.status, 0, "a wrong fingerprint must fail the handshake: {}", err.detail);
}
