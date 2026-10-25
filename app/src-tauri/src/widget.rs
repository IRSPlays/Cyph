//! The companion window: a small frameless, transparent, always-on-top window that Cyph lives in while the main app is
//! minimized. It is a second webview of the same frontend (route `widget`), so it shares the Rust side (token, chat and
//! change-feed connections) with the main window.

#[cfg(desktop)]
use std::sync::atomic::{AtomicBool, Ordering};

use tauri::{AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, WebviewWindow};
#[cfg(desktop)]
use tauri::{WebviewUrl, WebviewWindowBuilder};

pub const LABEL: &str = "widget";
#[cfg(desktop)]
const MARGIN: i32 = 16; // logical pixels from the screen's work-area edge

#[cfg(desktop)]
static PLACED: AtomicBool = AtomicBool::new(false);

#[cfg(desktop)]
pub fn create(app: &AppHandle) -> tauri::Result<()> {
    let w = WebviewWindowBuilder::new(app, LABEL, WebviewUrl::App("index.html".into()))
        .title("Cyph")
        .inner_size(360.0, 220.0)
        .decorations(false)
        .transparent(true)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .visible(false)
        .focused(false)
        .build()?;
    // closing it (Alt+F4) only hides it: he comes back the next time the main window is minimized
    let hide = w.clone();
    w.on_window_event(move |ev| {
        if let tauri::WindowEvent::CloseRequested { api, .. } = ev {
            api.prevent_close();
            let _ = hide.hide();
        }
    });
    Ok(())
}

fn window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window(LABEL)
}

/// Bottom-right corner of the primary monitor's work area (above the taskbar), the first time he appears.
#[cfg(desktop)]
fn place_first(app: &AppHandle, w: &WebviewWindow) {
    if PLACED.swap(true, Ordering::SeqCst) {
        return;
    }
    let (Ok(Some(m)), Ok(size)) = (app.primary_monitor(), w.outer_size()) else { return };
    let wa = m.work_area();
    let k = m.scale_factor();
    let x = wa.position.x + wa.size.width as i32 - size.width as i32 - (MARGIN as f64 * k) as i32;
    let y = wa.position.y + wa.size.height as i32 - size.height as i32 - (MARGIN as f64 * k) as i32;
    let _ = w.set_position(PhysicalPosition::new(x, y));
}

#[cfg(desktop)]
pub fn show(app: &AppHandle) {
    if let Some(w) = window(app) {
        place_first(app, &w);
        let _ = w.show();
        let _ = w.set_always_on_top(true);
    }
}

pub fn hide(app: &AppHandle) {
    if let Some(w) = window(app) {
        let _ = w.hide();
    }
}

/// The page tells us how big its content is; the window follows, growing up and to the left so the bottom-right corner
/// (wherever the user dragged it) stays put.
#[tauri::command]
pub fn widget_fit(app: AppHandle, width: f64, height: f64) {
    let Some(w) = window(&app) else { return };
    let (Ok(pos), Ok(size), Ok(k)) = (w.outer_position(), w.outer_size(), w.scale_factor()) else { return };
    let (nw, nh) = ((width.clamp(120.0, 900.0) * k).round() as i32, (height.clamp(80.0, 1200.0) * k).round() as i32);
    let (right, bottom) = (pos.x + size.width as i32, pos.y + size.height as i32);
    let _ = w.set_size(LogicalSize::new(width.clamp(120.0, 900.0), height.clamp(80.0, 1200.0)));
    let _ = w.set_position(PhysicalPosition::new(right - nw, bottom - nh));
}

#[tauri::command]
pub fn widget_hide(app: AppHandle) {
    hide(&app);
}

/// Bring the main window back (and put the companion away); `route` is the page to open there.
#[tauri::command]
pub fn show_main(app: AppHandle, route: Option<String>) {
    if let Some(m) = app.get_webview_window("main") {
        #[cfg(desktop)]
        let _ = m.unminimize();
        let _ = m.show();
        let _ = m.set_focus();
        if let Some(r) = route {
            let _ = m.emit("navigate", r);
        }
    }
    hide(&app);
}

#[tauri::command]
pub fn quit_app(app: AppHandle) {
    app.exit(0);
}
