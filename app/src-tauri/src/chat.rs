//! The WebSocket to /chat: one connection at a time, reconnecting with a growing pause, speaking the protocol in
//! docs/core-api.md. Messages from the server are handed to `emit` as JSON; the UI never sees the socket.

use std::sync::Arc;
use std::time::{Duration, Instant};

use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::sync::{mpsc, watch};
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{connect_async_tls_with_config, Connector};

use crate::net::Server;

pub enum Ended {
    /// The server said our token is no longer valid (close code 4401).
    Unpaired,
    /// The connection dropped or never opened; worth retrying.
    Lost(String),
    /// We were asked to stop.
    Stopped,
}

type Emit<'a> = &'a (dyn Fn(Value) + Send + Sync);

pub async fn session(
    server: &Server,
    token: &str,
    path: &str,
    out: &mut mpsc::UnboundedReceiver<String>,
    stop: &mut watch::Receiver<bool>,
    emit: Emit<'_>,
) -> Ended {
    let cfg = match crate::pin::client_config(&server.fp) {
        Ok(c) => c,
        Err(e) => return Ended::Lost(e),
    };
    let url = format!("wss://{}{}", server.host, path);
    let connecting = connect_async_tls_with_config(url, None, false, Some(Connector::Rustls(Arc::new(cfg))));
    let (ws, _) = match tokio::time::timeout(Duration::from_secs(15), connecting).await {
        Ok(Ok(pair)) => pair,
        Ok(Err(e)) => return Ended::Lost(e.to_string()),
        Err(_) => return Ended::Lost("connecting timed out".into()),
    };
    let (mut tx, mut rx) = ws.split();
    if tx.send(Message::text(json!({"type": "auth", "token": token}).to_string())).await.is_err() {
        return Ended::Lost("could not send the login".into());
    }
    let mut beat = tokio::time::interval(Duration::from_secs(25));
    beat.tick().await;
    loop {
        tokio::select! {
            _ = beat.tick() => {
                if tx.send(Message::text(json!({"type": "ping"}).to_string())).await.is_err() {
                    return Ended::Lost("sending failed".into());
                }
            }
            _ = stop.changed() => {
                let _ = tx.close().await;
                return Ended::Stopped;
            }
            say = out.recv() => match say {
                // `out` carries ready-made `say` frames (text and any pictures), built by the chat_say command
                Some(frame) => {
                    if tx.send(Message::text(frame)).await.is_err() {
                        return Ended::Lost("sending failed".into());
                    }
                }
                None => return Ended::Stopped,
            },
            msg = rx.next() => match msg {
                Some(Ok(Message::Text(t))) => {
                    if let Ok(v) = serde_json::from_str::<Value>(t.as_str()) {
                        emit(v);
                    }
                }
                Some(Ok(Message::Close(frame))) => {
                    let code = frame.map(|f| u16::from(f.code)).unwrap_or(1005);
                    return if code == 4401 { Ended::Unpaired } else { Ended::Lost(format!("closed by the server ({code})")) };
                }
                Some(Ok(_)) => {}
                Some(Err(e)) => return Ended::Lost(e.to_string()),
                None => return Ended::Lost("the connection closed".into()),
            }
        }
    }
}

/// Keeps a session alive until stopped or unpaired. Tells the UI about drops with {"type":"disconnected"}.
pub async fn supervise(
    server: Server,
    token: String,
    path: &'static str,
    mut out: mpsc::UnboundedReceiver<String>,
    mut stop: watch::Receiver<bool>,
    emit: impl Fn(Value) + Send + Sync + 'static,
) {
    let mut pause = 1u64;
    loop {
        let started = Instant::now();
        match session(&server, &token, path, &mut out, &mut stop, &emit).await {
            Ended::Stopped => return,
            Ended::Unpaired => {
                emit(json!({"type": "unpaired"}));
                return;
            }
            Ended::Lost(reason) => {
                emit(json!({"type": "disconnected", "reason": reason}));
                if started.elapsed() > Duration::from_secs(10) {
                    pause = 1;
                }
                tokio::select! {
                    _ = stop.changed() => return,
                    _ = tokio::time::sleep(Duration::from_secs(pause)) => {}
                }
                pause = (pause * 2).min(30);
            }
        }
    }
}
