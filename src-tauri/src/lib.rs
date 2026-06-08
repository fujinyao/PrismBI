use tauri::{Manager, RunEvent};
use tauri::menu::{MenuBuilder, MenuItemBuilder};
use tauri::tray::TrayIconBuilder;
use std::sync::Mutex;

const MAX_URL_LEN: usize = 2048;

struct AppStateInner {
    frontend_url: String,
}

struct AppState(Mutex<AppStateInner>);

#[derive(serde::Serialize)]
struct AppInfo {
    name: &'static str,
    version: &'static str,
    platform: &'static str,
    arch: &'static str,
}

#[tauri::command]
fn get_app_info() -> AppInfo {
    AppInfo {
        name: "PrismBI",
        version: env!("CARGO_PKG_VERSION"),
        platform: std::env::consts::OS,
        arch: std::env::consts::ARCH,
    }
}

#[tauri::command]
fn get_frontend_url(state: tauri::State<'_, AppState>) -> String {
    state.0.lock().unwrap_or_else(|e| e.into_inner()).frontend_url.clone()
}

#[tauri::command]
async fn get_backend_status() -> String {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(3))
        .build()
        .unwrap_or_else(|_| reqwest::Client::new());
    let url = std::env::var("PRISMBI_BACKEND_URL").unwrap_or_else(|_| "http://localhost:8400".to_string());
    match client.get(format!("{}/health", url)).send().await {
        Ok(resp) if resp.status().is_success() => "running".to_string(),
        Ok(_) => "error".to_string(),
        Err(_) => "unreachable".to_string(),
    }
}

#[tauri::command]
async fn open_external(url: String) -> Result<(), String> {
    validate_url(&url)?;
    open::that(&url).map_err(|e| format!("Failed to open URL: {e}"))
}

fn validate_url(url: &str) -> Result<(), String> {
    if url.len() > MAX_URL_LEN {
        return Err("URL too long (max 2048 characters)".to_string());
    }
    let parsed = url::Url::parse(url).map_err(|e| format!("Invalid URL: {}", e))?;
    match parsed.scheme() {
        "http" | "https" => {}
        _ => return Err("URL must use http:// or https://".to_string()),
    }
    if parsed.host_str().is_none_or(|h| h.is_empty()) {
        return Err("URL must have a valid hostname".to_string());
    }
    if parsed.username() != "" || parsed.password().is_some() {
        return Err("URL must not contain credentials".to_string());
    }
    Ok(())
}

fn navigate_to_url(window: &tauri::WebviewWindow, url: &str) {
    match url::Url::parse(url) {
        Ok(parsed) => {
            if let Err(e) = window.navigate(parsed) {
                log::error!("Navigation failed: {e}");
            }
        }
        Err(e) => {
            log::error!("Invalid URL for navigation: {e}");
        }
    }
}

#[tauri::command]
async fn set_frontend_url(app: tauri::AppHandle, state: tauri::State<'_, AppState>, url: String) -> Result<String, String> {
    validate_url(&url)?;

    let config_path = app.path().app_config_dir().map_err(|e| e.to_string())?;
    std::fs::create_dir_all(&config_path).map_err(|e| format!("Failed to create config dir: {e}"))?;
    let config_file = config_path.join("frontend_url.txt");
    let old_config_file = config_path.join("backend_url.txt");
    if old_config_file.exists() {
        let _ = std::fs::remove_file(&old_config_file);
    }
    std::fs::write(&config_file, &url).map_err(|e| format!("Failed to write config: {e}"))?;

    {
        let mut s = state.0.lock().unwrap_or_else(|e| e.into_inner());
        s.frontend_url = url.clone();
    }

    if let Some(window) = app.get_webview_window("main") {
        navigate_to_url(&window, &url);
    }

    Ok(url)
}

fn load_frontend_url(app: &tauri::AppHandle) -> String {
    let default_url = std::env::var("PRISMBI_FRONTEND_URL")
        .unwrap_or_else(|_| "http://localhost:5173".to_string());

    if let Ok(config_dir) = app.path().app_config_dir() {
        let config_file = config_dir.join("frontend_url.txt");
        let old_config_file = config_dir.join("backend_url.txt");
        let content = config_file.exists().then(|| {
            std::fs::read_to_string(&config_file).ok()
        }).flatten().or_else(|| {
            old_config_file.exists().then(|| {
                std::fs::read_to_string(&old_config_file).ok()
            }).flatten()
        });
        if let Some(content) = content {
            let url = content.trim().to_string();
            if url.starts_with("http://") || url.starts_with("https://") {
                if validate_url(&url).is_ok() {
                    return url;
                }
                log::warn!("Ignoring invalid frontend URL in config: {}", url);
            }
        }
    }

    if validate_url(&default_url).is_ok() {
        default_url
    } else {
        log::warn!("Ignoring invalid PRISMBI_FRONTEND_URL '{}', falling back to http://localhost:5173", default_url);
        "http://localhost:5173".to_string()
    }
}

pub fn run() {
    env_logger::init();

    let frontend_url = "http://localhost:5173".to_string();
    let state = AppState(Mutex::new(AppStateInner {
        frontend_url,
    }));

    tauri::Builder::default()
        .manage(state)
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_process::init())
        .invoke_handler(tauri::generate_handler![get_app_info, get_frontend_url, set_frontend_url, get_backend_status, open_external])
        .setup(|app| {
            let frontend_url = load_frontend_url(app.handle());
            log::info!("Frontend URL: {}", frontend_url);
            {
                let state = app.state::<AppState>();
                let mut s = state.0.lock().unwrap_or_else(|e| e.into_inner());
                s.frontend_url = frontend_url.clone();
            }

            let settings_item = MenuItemBuilder::with_id("settings", "Frontend URL...").build(app)?;
            let show_item = MenuItemBuilder::with_id("show", "Show PrismBI").build(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "Quit").build(app)?;

            let menu = MenuBuilder::new(app)
                .items(&[&settings_item, &show_item, &quit_item])
                .build()?;

            let icon = app.default_window_icon().cloned().unwrap_or_else(|| {
                tauri::image::Image::from_bytes(include_bytes!("../icons/32x32.png"))
                    .expect("Failed to load fallback tray icon")
            });
            let _tray = TrayIconBuilder::new()
                .icon(icon)
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(move |app, event| {
                    match event.id().as_ref() {
                        "settings" => {
                            let state = app.state::<AppState>();
                            let current_url = state.0.lock().unwrap_or_else(|e| e.into_inner()).frontend_url.clone();
                            if let Some(window) = app.get_webview_window("main") {
                                let url_json = serde_json::to_string(&current_url)
                                    .unwrap_or_else(|_| "\"\"".to_string());
                                let js = format!(
                                    "var url = prompt('Enter frontend URL:', {}); if(url) {{ window.__TAURI__.invoke('set_frontend_url', {{ url: url }}) }}",
                                    url_json
                                );
                                if let Err(e) = window.eval(&js) {
                                    log::error!("Failed to show frontend URL prompt: {e}");
                                }
                            }
                        }
                        "show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        "quit" => {
                            app.exit(0);
                        }
                        _ => {}
                    }
                })
                .tooltip("PrismBI")
                .build(app)?;

            if let Some(window) = app.get_webview_window("main") {
                navigate_to_url(&window, &frontend_url);
            }

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while running tauri application")
        .run(|_app_handle, event| {
            if let RunEvent::Exit = event {
                std::thread::sleep(std::time::Duration::from_millis(500));
            }
        });
}
