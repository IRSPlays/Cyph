mod agent;
pub mod chat;
mod commands;
pub mod link;
pub mod net;
pub mod pin;
pub mod settings;
mod widget;

/// Installs the `ring` crypto provider as the process default. Safe to call more than once.
pub fn init_crypto() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    use tauri::Manager;
    init_crypto();
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let dir = app.path().app_data_dir()?;
            std::fs::create_dir_all(&dir)?;
            app.manage(commands::AppState::new(dir.join("settings.json")));
            #[cfg(desktop)]
            widget::create(app.handle())?;
            agent::start(app.handle());
            #[cfg(desktop)]
            if let Some(main) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                let watched = main.clone();
                main.on_window_event(move |ev| match ev {
                    // closing the main window ends the app, companion and all
                    tauri::WindowEvent::CloseRequested { .. } => handle.exit(0),
                    // there is no "minimized" event on Windows, but minimizing resizes the window: look then
                    tauri::WindowEvent::Resized(_) => {
                        let minimized = watched.is_minimized().unwrap_or(false);
                        let handle = handle.clone();
                        tauri::async_runtime::spawn(async move {
                            if minimized {
                                // only a paired app has anything to show
                                if handle.state::<commands::AppState>().settings.lock().await.paired() {
                                    widget::show(&handle);
                                }
                            } else {
                                widget::hide(&handle);
                            }
                        });
                    }
                    _ => {}
                });
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::health,
            commands::get_settings,
            commands::pair,
            commands::unpair,
            commands::api,
            commands::chat_connect,
            commands::chat_say,
            commands::chat_disconnect,
            commands::events_connect,
            commands::events_disconnect,
            commands::link_status,
            commands::arm_watch_start,
            commands::arm_watch_stop,
            widget::widget_fit,
            widget::widget_hide,
            widget::show_main,
            widget::quit_app
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
