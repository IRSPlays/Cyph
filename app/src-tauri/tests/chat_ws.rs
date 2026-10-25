mod common;

use std::sync::{Arc, Mutex};
use std::time::Duration;

use cyph_app_lib::chat::{session, Ended};
use cyph_app_lib::net::Server;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::tungstenite::protocol::frame::coding::CloseCode;
use tokio_tungstenite::tungstenite::protocol::CloseFrame;
use tokio_tungstenite::tungstenite::Message;

/// A TLS WebSocket server. `reject` = close with 4401 right after the auth frame.
async fn ws_server(reject: bool) -> common::TestServer {
    let (cert, key) = common::make_cert();
    let fp = common::fingerprint(&cert);
    let acceptor = common::acceptor(cert, key);
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let port = listener.local_addr().unwrap().port();
    tokio::spawn(async move {
        loop {
            let Ok((tcp, _)) = listener.accept().await else { return };
            let acceptor = acceptor.clone();
            tokio::spawn(async move {
                let Ok(tls) = acceptor.accept(tcp).await else { return };
                let Ok(mut ws) = tokio_tungstenite::accept_async(tls).await else { return };
                let auth = ws.next().await.unwrap().unwrap().into_text().unwrap();
                let auth: Value = serde_json::from_str(auth.as_str()).unwrap();
                if reject || auth["token"] != "good" {
                    let _ = ws.send(Message::Close(Some(CloseFrame { code: CloseCode::Library(4401), reason: "".into() }))).await;
                    return;
                }
                ws.send(Message::text(json!({"type": "ready", "device": "t"}).to_string())).await.unwrap();
                while let Some(Ok(Message::Text(t))) = ws.next().await {
                    let v: Value = serde_json::from_str(t.as_str()).unwrap();
                    if v["type"] == "say" {
                        ws.send(Message::text(json!({"type": "delta", "text": "[happy] he"}).to_string())).await.unwrap();
                        ws.send(Message::text(json!({"type": "final", "text": format!("[happy] you said {}", v["text"].as_str().unwrap())}).to_string())).await.unwrap();
                    }
                }
            });
        }
    });
    common::TestServer { port, fp }
}

fn collector() -> (Arc<Mutex<Vec<Value>>>, impl Fn(Value) + Send + Sync) {
    let seen = Arc::new(Mutex::new(Vec::new()));
    let sink = seen.clone();
    (seen, move |v| sink.lock().unwrap().push(v))
}

#[tokio::test]
async fn a_chat_turn_streams_back() {
    let s = ws_server(false).await;
    let server = Server { host: format!("127.0.0.1:{}", s.port), fp: s.fp.clone() };
    let (seen, emit) = collector();
    let (out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
    let (stop_tx, mut stop_rx) = watch::channel(false);
    let handle = tokio::spawn(async move { session(&server, "good", "/chat", &mut out_rx, &mut stop_rx, &emit).await });
    out_tx.send(json!({"type": "say", "text": "hello"}).to_string()).unwrap();
    for _ in 0..100 {
        if seen.lock().unwrap().iter().any(|v| v["type"] == "final") {
            break;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    stop_tx.send(true).unwrap();
    assert!(matches!(handle.await.unwrap(), Ended::Stopped));
    let types: Vec<String> = seen.lock().unwrap().iter().map(|v| v["type"].as_str().unwrap().to_string()).collect();
    assert_eq!(types, ["ready", "delta", "final"]);
    assert_eq!(seen.lock().unwrap()[2]["text"], "[happy] you said hello");
}

#[tokio::test]
async fn code_4401_means_unpaired() {
    let s = ws_server(false).await;
    let server = Server { host: format!("127.0.0.1:{}", s.port), fp: s.fp.clone() };
    let (_seen, emit) = collector();
    let (_out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
    let (_stop_tx, mut stop_rx) = watch::channel(false);
    let ended = session(&server, "revoked", "/chat", &mut out_rx, &mut stop_rx, &emit).await;
    assert!(matches!(ended, Ended::Unpaired));
}

#[tokio::test]
async fn a_wrong_pin_never_connects() {
    let s = ws_server(false).await;
    let other = ws_server(false).await;
    let server = Server { host: format!("127.0.0.1:{}", s.port), fp: other.fp.clone() };
    let (_seen, emit) = collector();
    let (_out_tx, mut out_rx) = mpsc::unbounded_channel::<String>();
    let (_stop_tx, mut stop_rx) = watch::channel(false);
    let ended = session(&server, "good", "/chat", &mut out_rx, &mut stop_rx, &emit).await;
    assert!(matches!(ended, Ended::Lost(_)));
}
