use std::path::PathBuf;

use serde_json::{json, Value};
use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Emitter, Manager, State, WebviewWindow};
use tokio::sync::{mpsc, watch, Mutex};

use crate::chat;
use crate::link;
use crate::net::{self, ApiError, Server};
use crate::settings::{Public, Settings};

pub struct ChatHandle {
    tx: mpsc::UnboundedSender<String>,
    stop: watch::Sender<bool>,
}

pub struct AppState {
    pub settings: Mutex<Settings>,
    pub path: PathBuf,
    pub chat: Mutex<Option<ChatHandle>>,
    pub events: Mutex<Option<ChatHandle>>,
    /// whether the chat / change-feed sockets are up right now: a second window that starts late missed their `ready`
    pub chat_live: AtomicBool,
    pub events_live: AtomicBool,
    /// the window that last sent a chat message: only it hears "busy" (a refusal of its own message)
    pub say_from: std::sync::Mutex<String>,
    /// the camera pull is running while this holds its stop switch
    pub arm_watch: Mutex<(usize, Option<watch::Sender<bool>>)>,
}

impl AppState {
    pub fn new(path: PathBuf) -> Self {
        AppState { settings: Mutex::new(Settings::load(&path)), path, chat: Mutex::new(None), events: Mutex::new(None), chat_live: AtomicBool::new(false), events_live: AtomicBool::new(false), say_from: std::sync::Mutex::new("main".into()), arm_watch: Mutex::new((0, None)) }
    }
}

/// Remember whether a socket is up, from the frames it reports.
fn track(live: &AtomicBool, v: &Value) {
    match v["type"].as_str() {
        Some("ready") => live.store(true, Ordering::SeqCst),
        Some("disconnected") | Some("unpaired") => live.store(false, Ordering::SeqCst),
        _ => {}
    }
}

fn fail(detail: &str) -> ApiError {
    ApiError { status: 0, detail: detail.to_string() }
}

async fn creds(state: &AppState) -> Result<(Server, String), ApiError> {
    let s = state.settings.lock().await;
    match (&s.token, s.paired()) {
        (Some(token), true) => Ok((Server { host: s.host.clone(), fp: s.fp.clone() }, token.clone())),
        _ => Err(fail("not paired")),
    }
}

/// Spike: GET /health on a server with a pinned fingerprint. No token needed.
#[tauri::command]
pub async fn health(host: String, fp: String) -> Result<Value, ApiError> {
    net::request(&Server { host, fp }, "GET", "/health", None, None).await
}

#[tauri::command]
pub async fn get_settings(state: State<'_, AppState>) -> Result<Public, ApiError> {
    Ok(state.settings.lock().await.public())
}

#[tauri::command]
pub async fn pair(state: State<'_, AppState>, link: String, name: String) -> Result<Public, ApiError> {
    let l = link::parse(&link).map_err(|e| fail(&e))?;
    let server = Server { host: l.host.clone(), fp: l.fp.clone() };
    let name = if name.trim().is_empty() { "This computer".to_string() } else { name.trim().to_string() };
    let res = net::request(&server, "POST", "/pair", None, Some(json!({"code": l.code, "name": name}))).await?;
    let token = res["token"].as_str().ok_or_else(|| fail("the server sent no token"))?.to_string();
    let device_id = res["device_id"].as_str().unwrap_or_default().to_string();
    let mut s = state.settings.lock().await;
    *s = Settings { host: l.host, fp: l.fp, token: Some(token), device_name: name, device_id };
    s.save(&state.path).map_err(|e| fail(&e))?;
    Ok(s.public())
}

/// Best effort: stop the chat, ask the server to revoke this device, forget the token.
#[tauri::command]
pub async fn unpair(state: State<'_, AppState>) -> Result<Public, ApiError> {
    if let Some(h) = state.chat.lock().await.take() {
        let _ = h.stop.send(true);
    }
    if let Some(h) = state.events.lock().await.take() {
        let _ = h.stop.send(true);
    }
    state.chat_live.store(false, Ordering::SeqCst);
    state.events_live.store(false, Ordering::SeqCst);
    if let Ok((server, token)) = creds(&state).await {
        let id = state.settings.lock().await.device_id.clone();
        if !id.is_empty() {
            let _ = net::request(&server, "DELETE", &format!("/devices/{id}"), Some(&token), None).await;
        }
    }
    let mut s = state.settings.lock().await;
    s.token = None;
    s.save(&state.path).map_err(|e| fail(&e))?;
    Ok(s.public())
}

/// The UI's only way to the REST API. Only fixed verbs and paths on our own server are allowed.
#[tauri::command]
pub async fn api(state: State<'_, AppState>, method: String, path: String, body: Option<Value>) -> Result<Value, ApiError> {
    let method = method.to_uppercase();
    let verb_ok = matches!(method.as_str(), "GET" | "POST" | "PATCH" | "PUT" | "DELETE");
    if !verb_ok || !path.starts_with('/') || path.contains("..") || path.contains("://") {
        return Err(fail("bad request"));
    }
    let (server, token) = creds(&state).await?;
    net::request(&server, &method, &path, Some(&token), body).await
}

#[tauri::command]
pub async fn chat_connect(app: AppHandle, state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut slot = state.chat.lock().await;
    if slot.is_some() {
        return Ok(());
    }
    let (server, token) = creds(&state).await?;
    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let (stop, stop_rx) = watch::channel(false);
    tauri::async_runtime::spawn(chat::supervise(server, token, "/chat", rx, stop_rx, move |v| {
        let st = app.state::<AppState>();
        track(&st.chat_live, &v);
        if v["type"] == "busy" {
            let to = st.say_from.lock().map(|g| g.clone()).unwrap_or_else(|_| "main".into());
            let _ = app.emit_to(to, "chat", v);
        } else {
            let _ = app.emit("chat", v);
        }
    }));
    *slot = Some(ChatHandle { tx, stop });
    Ok(())
}

#[tauri::command]
pub async fn chat_say(window: WebviewWindow, state: State<'_, AppState>, text: String, images: Option<Vec<serde_json::Value>>) -> Result<(), ApiError> {
    if let Ok(mut g) = state.say_from.lock() {
        *g = window.label().to_string();
    }
    let frame = serde_json::json!({"type": "say", "text": text, "images": images.unwrap_or_default()}).to_string();
    match state.chat.lock().await.as_ref() {
        Some(h) => h.tx.send(frame).map_err(|_| fail("the chat is not connected")),
        None => Err(fail("the chat is not connected")),
    }
}

#[tauri::command]
pub async fn chat_disconnect(state: State<'_, AppState>) -> Result<(), ApiError> {
    state.chat_live.store(false, Ordering::SeqCst);
    if let Some(h) = state.chat.lock().await.take() {
        let _ = h.stop.send(true);
    }
    Ok(())
}

/// The live change feed: the UI hears `sync` events ({"type":"changed","what":"plan"} and relayed chat turns).
#[tauri::command]
pub async fn events_connect(app: AppHandle, state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut slot = state.events.lock().await;
    if slot.is_some() {
        return Ok(());
    }
    let (server, token) = creds(&state).await?;
    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let (stop, stop_rx) = watch::channel(false);
    tauri::async_runtime::spawn(chat::supervise(server, token, "/events", rx, stop_rx, move |v| {
        track(&app.state::<AppState>().events_live, &v);
        let _ = app.emit("sync", v);
    }));
    *slot = Some(ChatHandle { tx, stop });
    Ok(())
}

#[tauri::command]
pub async fn events_disconnect(state: State<'_, AppState>) -> Result<(), ApiError> {
    state.events_live.store(false, Ordering::SeqCst);
    if let Some(h) = state.events.lock().await.take() {
        let _ = h.stop.send(true);
    }
    Ok(())
}

/// Start pulling the arm's camera. Frames reach every window as `arm_frame` events {seq, age_ms, url} where `url` is a
/// `data:image/jpeg` URL. Pulling frames is what makes the arm's agent send them, so this runs only while the Arm page is
/// open. Starting twice is harmless.
#[tauri::command]
pub async fn arm_watch_start(app: AppHandle, state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut slot = state.arm_watch.lock().await;
    slot.0 += 1; // one more viewer; the pull runs until the last one stops
    if slot.1.is_some() {
        return Ok(());
    }
    let (server, token) = match creds(&state).await {
        Ok(c) => c,
        Err(e) => {
            slot.0 -= 1;
            return Err(e);
        }
    };
    let http = net::client(&server.fp).map_err(ApiError::net)?;
    let (stop, mut stop_rx) = watch::channel(false);
    tauri::async_runtime::spawn(async move {
        let (mut after, mut fails) = (0u64, 0u32);
        loop {
            let got = tokio::select! {
                _ = stop_rx.changed() => break,
                r = net::frame(&http, &server, &token, after) => r,
            };
            match got {
                Ok(Some(f)) => {
                    after = f.seq;
                    fails = 0;
                    let url = format!("data:image/jpeg;base64,{}", net::base64(&f.jpeg));
                    let _ = app.emit("arm_frame", json!({"seq": f.seq, "age_ms": f.age_ms, "url": url}));
                }
                Ok(None) => fails = 0,
                Err(e) => {
                    // say why, so the page can show it instead of an empty picture
                    let _ = app.emit("arm_frame", json!({"seq": 0, "age_ms": 0, "url": "", "error": format!("{} {}", e.status, e.detail)}));
                    fails = (fails + 1).min(5);
                    tokio::time::sleep(std::time::Duration::from_millis(500 * fails as u64)).await;
                }
            }
        }
    });
    slot.1 = Some(stop);
    Ok(())
}

#[tauri::command]
pub async fn arm_watch_stop(state: State<'_, AppState>) -> Result<(), ApiError> {
    let mut slot = state.arm_watch.lock().await;
    slot.0 = slot.0.saturating_sub(1);
    if slot.0 == 0 {
        if let Some(stop) = slot.1.take() {
            let _ = stop.send(true);
        }
    }
    Ok(())
}

/// Which sockets are up now, for a window that opened after their `ready` was announced.
#[tauri::command]
pub fn link_status(state: State<'_, AppState>) -> Value {
    json!({"chat": state.chat_live.load(Ordering::SeqCst), "events": state.events_live.load(Ordering::SeqCst)})
}
