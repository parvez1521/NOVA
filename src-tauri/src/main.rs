#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    thread,
    time::Duration,
};
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
mod events;
use events::native_events;

const PACKAGED_BACKEND_URL: &str = "http://127.0.0.1:8742";
const APP_BUNDLE_ID: &str = "local.nova.desktop";
const HELPER_BUNDLE_ID: &str = "local.nova.computer-access";
const VOICE_BUNDLE_ID: &str = "local.nova.voice";
const STABLE_DESIGNATED_REQUIREMENT: &str = "identifier \"local.nova.desktop\"";

fn native_context<R: tauri::Runtime>() -> tauri::Context<R> {
    tauri::generate_context!()
}

#[derive(Clone, Serialize)]
struct RuntimeInfo {
    base_url: String,
    session: String,
    status: String,
    restarts: u32,
    pid: Option<u32>,
    data_directory: String,
    event_smoke: bool,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(default)]
struct Preferences {
    always_on_top: bool,
    hotkey: String,
    hotkey_enabled: bool,
    mode: String,
    position: Option<[i32; 2]>,
    lock_position: bool,
}
impl Default for Preferences {
    fn default() -> Self {
        Self {
            always_on_top: false,
            hotkey: "Alt+Space".into(),
            hotkey_enabled: true,
            mode: "pet".into(),
            position: None,
            lock_position: false,
        }
    }
}
struct Backend {
    child: Option<Child>,
    info: RuntimeInfo,
    executable: PathBuf,
    resources: PathBuf,
    data: PathBuf,
    preferences: Preferences,
}
struct NativeState {
    backend: Mutex<Backend>,
    quitting: AtomicBool,
    busy: AtomicBool,
}
struct NativeVoice {
    child: Mutex<Option<Child>>,
}

fn api(info: &RuntimeInfo, route: &str) -> bool {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(3))
        .build()
        .ok()
        .and_then(|client| {
            client
                .post(format!("{}{}", info.base_url, route))
                .header("x-nova-session", &info.session)
                .send()
                .ok()
        })
        .is_some_and(|response| response.status().is_success())
}
fn healthy(info: &RuntimeInfo) -> bool {
    reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(2))
        .build()
        .ok()
        .and_then(|client| {
            client
                .get(format!("{}/api/health/live", info.base_url))
                .header("x-nova-session", &info.session)
                .send()
                .ok()
        })
        .is_some_and(|response| response.status().is_success())
}
fn launch(backend: &mut Backend) -> Result<(), String> {
    let port = backend
        .info
        .base_url
        .rsplit(':')
        .next()
        .ok_or("Invalid local port")?;
    let mut command = Command::new(&backend.executable);
    command.env("NATIVE_APP_ENABLED", "true").env("NOVA_ENV", "production").env("APP_VERSION", "0.2.0")
        .env("NOVA_PORT", port).env("NATIVE_SESSION_TOKEN", &backend.info.session)
        .env("NATIVE_DATA_ROOT", &backend.data).env("DATABASE_PATH", backend.data.join("backend/data/nova.db"))
        .env("CORS_ORIGINS", "tauri://localhost,http://tauri.localhost,https://tauri.localhost,http://localhost:5173,http://127.0.0.1:5173")
        .env("NOVA_NATIVE_HELPER", backend.resources.join("NOVA Computer Access.app/Contents/MacOS/NovaNative"))
        .env("WHISPER_BINARY", backend.resources.join("speech/whisper-cli"))
        .env("GGML_BACKEND_DL_PATH", backend.resources.join("speech"))
        .env("SPEECH_MODELS_DIRECTORY", backend.resources.join("models"))
        .env("PATH", "/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin")
        .current_dir(&backend.data).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    let child = command
        .spawn()
        .map_err(|_| "Packaged backend could not start".to_string())?;
    backend.info.pid = Some(child.id());
    backend.info.status = "starting".into();
    backend.child = Some(child);
    write_status(backend);
    Ok(())
}
fn write_status(backend: &Backend) {
    // No session token, private content, or credentials in diagnostic files.
    let _=fs::write(backend.data.join("native-status.json"),serde_json::to_vec(&serde_json::json!({"pid":backend.info.pid,"base_url":backend.info.base_url,"status":backend.info.status,"restarts":backend.info.restarts,"version":"0.2.0"})).unwrap_or_default());
}
fn write_ui_status(backend: &Backend, connected: bool, microphone_available: bool, speech_ready: bool, wake_state: &str) {
    let value=serde_json::json!({"connected":connected,"microphone_available":microphone_available,"speech_ready":speech_ready,"wake_state":wake_state,"version":"0.2.0"});
    let _=fs::write(backend.data.join("native-ui-status.json"),serde_json::to_vec(&value).unwrap_or_default());
}
fn terminate(backend: &mut Backend) {
    if let Some(mut child) = backend.child.take() {
        #[cfg(unix)]
        unsafe {
            libc::kill(-(child.id() as i32), libc::SIGTERM);
        }
        for _ in 0..40 {
            if child.try_wait().ok().flatten().is_some() {
                backend.info.pid = None;
                return;
            }
            thread::sleep(Duration::from_millis(100));
        }
        #[cfg(unix)]
        unsafe {
            libc::kill(-(child.id() as i32), libc::SIGKILL);
        }
        let _ = child.wait();
        backend.info.pid = None;
    }
}
fn packaged_backend_path() -> Result<PathBuf, String> {
    let parent = std::env::current_exe()
        .map_err(|_| "Executable path unavailable")?
        .parent()
        .ok_or("Executable directory unavailable")?
        .to_path_buf();
    let candidates = [
        parent.join("nova-backend"),
        parent.join(format!("nova-backend-{}-apple-darwin", std::env::consts::ARCH)),
    ];
    candidates
        .into_iter()
        .find(|candidate| candidate.is_file())
        .ok_or("Packaged backend executable is missing".into())
}
fn set_mode(app: &tauri::AppHandle, mode: &str) -> Result<(), String> {
    let companion = app.get_webview_window("companion").ok_or("Companion window unavailable")?;
    let control = app.get_webview_window("main").ok_or("Control center unavailable")?;
    match mode {
        "background" => {
            companion.hide().map_err(|_| "Could not hide companion")?;
            control.hide().map_err(|_| "Could not hide control center")?;
        }
        "pet" | "task" => {
            control.hide().map_err(|_| "Could not hide control center")?;
            let (width, height) = if mode == "task" { (360., 430.) } else { (280., 330.) };
            companion.set_size(tauri::LogicalSize::new(width, height)).map_err(|_| "Could not resize companion")?;
            companion.show().map_err(|_| "Could not show companion")?;
        }
        "control" => {
            control.set_size(tauri::LogicalSize::new(1080., 760.)).map_err(|_| "Could not resize control center")?;
            control.show().map_err(|_| "Could not show control center")?;
            companion.show().map_err(|_| "Could not show companion")?;
            let _ = control.set_focus();
        }
        _ => return Err("Unknown window mode".into()),
    }
    let _ = app.emit(native_events().mode, mode);
    let state = app.state::<NativeState>();
    let mut backend = state.backend.lock().map_err(|_| "Runtime unavailable")?;
    backend.preferences.mode = mode.into();
    save_preferences(&backend);
    Ok(())
}

fn current_app_path() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|path| path.parent()?.parent()?.parent().map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("unknown"))
}

fn signing_identity(path: &PathBuf) -> String {
    let output = Command::new("/usr/bin/codesign").args(["-dvvv", path.to_string_lossy().as_ref()]).output();
    let text = output.ok().map(|value| {
        let mut combined = String::from_utf8_lossy(&value.stdout).to_string();
        combined.push_str(&String::from_utf8_lossy(&value.stderr));
        combined
    }).unwrap_or_default();
    text.lines()
        .find_map(|line| line.strip_prefix("Signature=").or_else(|| line.strip_prefix("Authority=")))
        .map(|value| value.trim().chars().filter(|character| character.is_ascii_alphanumeric() || *character == ' ' || *character == '-' || *character == '.' || *character == '_').collect())
        .unwrap_or_else(|| "unknown".into())
}

fn voice_diagnostic(resources: &PathBuf) -> Result<serde_json::Value, String> {
    let output = Command::new(resources.join("NovaVoice"))
        .arg("--status")
        .output()
        .map_err(|_| "Native voice diagnostic unavailable".to_string())?;
    serde_json::from_slice(&output.stdout).map_err(|_| "Native voice diagnostic unavailable".to_string())
}

fn helper_diagnostic(resources: &PathBuf) -> Result<serde_json::Value, String> {
    let mut child = Command::new(resources.join("NOVA Computer Access.app/Contents/MacOS/NovaNative"))
        .stdin(Stdio::piped()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn()
        .map_err(|_| "Computer permission diagnostic unavailable".to_string())?;
    if let Some(mut input) = child.stdin.take() {
        input.write_all(br#"{"action":"permissions","arguments":{}}"#)
            .map_err(|_| "Computer permission diagnostic unavailable".to_string())?;
    }
    let output = child.wait_with_output().map_err(|_| "Computer permission diagnostic unavailable".to_string())?;
    serde_json::from_slice(&output.stdout).map_err(|_| "Computer permission diagnostic unavailable".to_string())
}

#[tauri::command]
fn permission_diagnostics(state: tauri::State<NativeState>) -> Result<serde_json::Value, String> {
    let (resources, app_path) = {
        let backend = state.backend.lock().map_err(|_| "Runtime unavailable")?;
        (backend.resources.clone(), current_app_path())
    };
    let voice = voice_diagnostic(&resources)?;
    let helper = helper_diagnostic(&resources)?;
    let runtime_identity = if app_path.to_string_lossy().contains(".app") { "PACKAGED" } else { "DEVELOPMENT" };
    Ok(serde_json::json!({
        "microphone": voice.get("microphone_state").and_then(|value| value.as_str()).unwrap_or("NOT_DETERMINED"),
        "speech_recognition": voice.get("speech_recognition_state").and_then(|value| value.as_str()).unwrap_or("UNAVAILABLE"),
        "accessibility": if helper.get("accessibility").and_then(|value| value.as_bool()).unwrap_or(false) { "GRANTED" } else { "DENIED" },
        "screen_recording": if helper.get("screen_recording").and_then(|value| value.as_bool()).unwrap_or(false) { "GRANTED" } else { "DENIED" },
        "active_app_path": app_path,
        "bundle_identity": APP_BUNDLE_ID,
        "signing_identity": signing_identity(&app_path),
        "designated_requirement": STABLE_DESIGNATED_REQUIREMENT,
        "helper_path": resources.join("NOVA Computer Access.app"),
        "helper_identity": HELPER_BUNDLE_ID,
        "voice_identity": VOICE_BUNDLE_ID,
        "runtime_identity": runtime_identity,
        "permission_owners": {
            "microphone": VOICE_BUNDLE_ID,
            "speech_recognition": VOICE_BUNDLE_ID,
            "accessibility": HELPER_BUNDLE_ID,
            "screen_recording": HELPER_BUNDLE_ID
        }
    }))
}
fn save_preferences(backend: &Backend) {
    if let Ok(data) = serde_json::to_vec(&backend.preferences) {
        let _ = fs::write(backend.data.join("native-preferences.json"), data);
    }
}
fn control(app: &tauri::AppHandle, action: &str) {
    let handle = app.clone();
    let action = action.to_string();
    thread::spawn(move || {
        let info = handle
            .state::<NativeState>()
            .backend
            .lock()
            .unwrap()
            .info
            .clone();
        api(
            &info,
            if action == "pause" {
                "/api/runtime/pause"
            } else {
                "/api/runtime/stop"
            },
        );
        let _ = handle.emit(native_events().control, &action);
    });
}
#[tauri::command]
fn runtime_info(state: tauri::State<NativeState>) -> Result<RuntimeInfo, String> {
    Ok(state
        .backend
        .lock()
        .map_err(|_| "Runtime unavailable")?
        .info
        .clone())
}
#[tauri::command]
fn open_browser_ui(state: tauri::State<NativeState>) -> Result<(), String> {
    let session = state.backend.lock().map_err(|_| "Runtime unavailable")?.info.session.clone();
    let base = std::env::var("NOVA_BROWSER_UI_URL").unwrap_or_else(|_| "http://127.0.0.1:5173/".into());
    let separator = if base.contains('#') { "&" } else { "#" };
    Command::new("/usr/bin/open")
        .arg(format!("{}{}nova_session={}", base, separator, session))
        .spawn()
        .map_err(|_| "Browser UI could not be opened")?;
    Ok(())
}
#[tauri::command]
fn native_preferences(state: tauri::State<NativeState>) -> Result<Preferences, String> {
    Ok(state
        .backend
        .lock()
        .map_err(|_| "Runtime unavailable")?
        .preferences
        .clone())
}
#[tauri::command]
fn window_mode(app: tauri::AppHandle, mode: String) -> Result<(), String> {
    set_mode(&app, &mode)
}

#[tauri::command]
fn start_drag(app: tauri::AppHandle) -> Result<(), String> {
    app.get_webview_window("companion").ok_or("Companion window unavailable")?.start_dragging().map_err(|_| "Could not move companion".into())
}

#[tauri::command]
fn open_panel(app: tauri::AppHandle, panel: String) -> Result<(), String> {
    if !["settings", "connections", "wake"].contains(&panel.as_str()) {
        return Err("Unknown panel".into());
    }
    set_mode(&app, "control")?;
    app.emit(native_events().panel, panel).map_err(|_| "Could not open panel".into())
}

#[tauri::command]
fn quit_app(app: tauri::AppHandle) {
    app.state::<NativeState>().quitting.store(true, Ordering::SeqCst);
    app.exit(0);
}
#[tauri::command]
fn configure_native(app: tauri::AppHandle, preferences: Preferences) -> Result<(), String> {
    if !["Alt+Space", "Alt+V", "Control+Shift+Space"].contains(&preferences.hotkey.as_str()) {
        return Err("Unsupported interaction shortcut".into());
    }
    let old = app
        .state::<NativeState>()
        .backend
        .lock()
        .map_err(|_| "Runtime unavailable")?
        .preferences
        .clone();
    if old.hotkey_enabled {
        let _ = app.global_shortcut().unregister(old.hotkey.as_str());
    }
    if preferences.hotkey_enabled {
        app.global_shortcut()
            .register(preferences.hotkey.as_str())
            .map_err(|_| "Shortcut is already in use")?;
    }
    if let Some(window) = app.get_webview_window("companion") {
        window
            .set_always_on_top(preferences.always_on_top)
            .map_err(|_| "Could not update companion")?;
    }
    let state = app.state::<NativeState>();
    let mut backend = state.backend.lock().map_err(|_| "Runtime unavailable")?;
    backend.preferences = preferences;
    save_preferences(&backend);
    Ok(())
}
#[tauri::command]
fn interaction_active(app: tauri::AppHandle, active: bool) -> Result<(), String> {
    let state = app.state::<NativeState>();
    if state.busy.swap(active, Ordering::SeqCst) != active {
        if active {
            app.global_shortcut()
                .register("Escape")
                .map_err(|_| "Global stop unavailable")?;
        } else {
            let _ = app.global_shortcut().unregister("Escape");
        }
    }
    Ok(())
}
#[tauri::command]
fn stop_runtime(app: tauri::AppHandle) {
    control(&app, "stop");
}
#[tauri::command]
async fn voice_status(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let path = app
        .state::<NativeState>()
        .backend
        .lock()
        .map_err(|_| "Runtime unavailable")?
        .resources
        .join("NovaVoice");
    tauri::async_runtime::spawn_blocking(move || {
        let output = Command::new(path)
            .arg("--status")
            .output()
            .map_err(|_| "Native voice unavailable")?;
        serde_json::from_slice(&output.stdout).map_err(|_| "Native voice status unavailable".into())
    })
    .await
    .map_err(|_| "Native voice unavailable")?
}
#[tauri::command]
fn voice_command(app: tauri::AppHandle, options: serde_json::Value) -> Result<(), String> {
    let action = options
        .get("action")
        .and_then(|value| value.as_str())
        .ok_or("Voice action required")?;
    if ![
        "start", "capture", "finish", "wake", "followup", "passive", "speaking", "stop",
    ]
    .contains(&action)
    {
        return Err("Voice action blocked".into());
    }
    let voice = app.state::<NativeVoice>();
    let mut child = voice.child.lock().map_err(|_| "Native voice unavailable")?;
    if let Some(process) = child.as_mut() {
        if process
            .try_wait()
            .map_err(|_| "Native voice unavailable")?
            .is_some()
        {
            *child = None;
        }
    }
    if action == "stop" {
        if let Some(mut process) = child.take() {
            if let Some(input) = process.stdin.as_mut() {
                let _ = input.write_all(b"{\"action\":\"stop\"}\n");
            }
            let _ = process.kill();
            let _ = process.wait();
        }
        return Ok(());
    }
    if child.is_none() {
        if action != "start" {
            return Err("Native voice is off".into());
        }
        let path = app
            .state::<NativeState>()
            .backend
            .lock()
            .map_err(|_| "Runtime unavailable")?
            .resources
            .join("NovaVoice");
        let mut process = Command::new(path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .map_err(|_| "Native voice unavailable")?;
        let output = process
            .stdout
            .take()
            .ok_or("Native voice stream unavailable")?;
        let handle = app.clone();
        thread::spawn(move || {
            for line in BufReader::new(output).lines().map_while(Result::ok) {
                if line.len() > 2_600_000 {
                    break;
                }
                if let Ok(value) = serde_json::from_str::<serde_json::Value>(&line) {
                    let _ = handle.emit(native_events().voice, value);
                }
            }
        });
        *child = Some(process);
    }
    let process = child.as_mut().ok_or("Native voice unavailable")?;
    let input = process.stdin.as_mut().ok_or("Native voice unavailable")?;
    let mut data = serde_json::to_vec(&options).map_err(|_| "Invalid voice options")?;
    data.push(b'\n');
    input
        .write_all(&data)
        .map_err(|_| "Native voice stopped".into())
}
#[tauri::command]
fn frontend_status(
    state: tauri::State<NativeState>,
    connected: bool,
    microphone_available: bool,
    speech_ready: bool,
    wake_state: String,
) -> Result<(), String> {
    if !["OFF", "STARTING", "PASSIVE", "LISTENING", "FOLLOWUP", "UNAVAILABLE"].contains(&wake_state.as_str()) {
        return Err("Invalid internal wake state".into());
    }
    let backend = state.backend.lock().map_err(|_| "Runtime unavailable")?;
    write_ui_status(&backend,connected,microphone_available,speech_ready,&wake_state);
    Ok(())
}

#[tauri::command]
fn event_subscription_probe(app: tauri::AppHandle, completed: Option<bool>) -> Result<(), String> {
    if !std::env::args().any(|arg| arg == "--event-smoke") {
        return Err("Native event smoke is not enabled".into());
    }
    if let Some(passed) = completed {
        let state = app.state::<NativeState>();
        let backend = state.backend.lock().map_err(|_| "Runtime unavailable")?;
        let result = serde_json::json!({"passed":passed,"voice_event":native_events().voice,"cycles":2});
        fs::write(backend.data.join("native-event-smoke.json"), result.to_string()).map_err(|_| "Smoke result unavailable")?;
        drop(backend);
        app.exit(0);
        Ok(())
    } else {
        app.emit(native_events().voice, serde_json::json!({"type":"subscription.smoke"})).map_err(|_| "Native subscription probe failed".into())
    }
}
#[tauri::command]
async fn restart_backend(app: tauri::AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<NativeState>();
        let mut backend = state.backend.lock().map_err(|_| "Runtime unavailable")?;
        api(&backend.info, "/api/runtime/stop");
        terminate(&mut backend);
        backend.info.restarts += 1;
        launch(&mut backend)
    })
    .await
    .map_err(|_| "Restart failed")?
}

fn main() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            let _ = set_mode(app, "pet");
        }))
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .macos_launcher(tauri_plugin_autostart::MacosLauncher::LaunchAgent)
                .args(["--background"])
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, shortcut, event| {
                    if shortcut.to_string() == "Escape" {
                        if event.state() == ShortcutState::Pressed {
                            control(app, "stop");
                        }
                    } else {
                        let _ = app.emit(
                            native_events().hotkey,
                            if event.state() == ShortcutState::Pressed {
                                "pressed"
                            } else {
                                "released"
                            },
                        );
                    }
                })
                .build(),
        )
        .invoke_handler(tauri::generate_handler![
            runtime_info,
            open_browser_ui,
            native_preferences,
            window_mode,
            start_drag,
            open_panel,
            quit_app,
            configure_native,
            interaction_active,
            stop_runtime,
            frontend_status,
            permission_diagnostics,
            event_subscription_probe,
            restart_backend,
            voice_status,
            voice_command
        ])
        .setup(|app| {
            let data = app.path().app_data_dir()?;
            fs::create_dir_all(data.join("backend/data"))?;
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                fs::set_permissions(&data, fs::Permissions::from_mode(0o700))?;
            }
            let resources = app.path().resource_dir()?.join("runtime");
            let executable = packaged_backend_path().map_err(std::io::Error::other)?;
            let preferences = fs::read(data.join("native-preferences.json"))
                .ok()
                .and_then(|data| serde_json::from_slice(&data).ok())
                .unwrap_or_default();
            let mut backend = Backend {
                child: None,
                info: RuntimeInfo {
                    base_url: PACKAGED_BACKEND_URL.into(),
                    session: format!("{}{}", uuid::Uuid::new_v4(), uuid::Uuid::new_v4()),
                    status: "starting".into(),
                    restarts: 0,
                    pid: None,
                    data_directory: data.to_string_lossy().into(),
                    event_smoke: std::env::args().any(|arg| arg == "--event-smoke"),
                },
                executable,
                resources,
                data,
                preferences,
            };
            launch(&mut backend).map_err(std::io::Error::other)?;
            write_ui_status(&backend, false, false, false, "OFF");
            let preferences = backend.preferences.clone();
            app.manage(NativeState {
                backend: Mutex::new(backend),
                quitting: AtomicBool::new(false),
                busy: AtomicBool::new(false),
            });
            app.manage(NativeVoice {
                child: Mutex::new(None),
            });
            let initial_mode = if std::env::args().any(|arg| arg == "--background") {
                "background"
            } else {
                "pet"
            };
            let _ = set_mode(app.handle(), initial_mode);
            if preferences.hotkey_enabled {
                app.global_shortcut()
                    .register(preferences.hotkey.as_str())?;
            }
            if let Some(window) = app.get_webview_window("companion") {
                window.set_always_on_top(preferences.always_on_top)?;
                if let Some([x, y]) = preferences.position {
                    let _ = window.set_position(tauri::Position::Physical(tauri::PhysicalPosition::new(x, y)));
                }
            }
            let open = MenuItem::with_id(app, "open", "Open NOVA", true, None::<&str>)?;
            let panel = MenuItem::with_id(app, "panel", "Control Panel", true, None::<&str>)?;
            let pause = MenuItem::with_id(app, "pause", "Pause Agent", true, None::<&str>)?;
            let stop = MenuItem::with_id(app, "stop", "Stop Everything", true, None::<&str>)?;
            let wake = MenuItem::with_id(
                app,
                "wake",
                "Enable / Disable Wake Word",
                true,
                None::<&str>,
            )?;
            let connections =
                MenuItem::with_id(app, "connections", "Connections", true, None::<&str>)?;
            let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
            let hide = MenuItem::with_id(app, "hide", "Background Mode", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit NOVA", true, None::<&str>)?;
            let menu = Menu::with_items(
                app,
                &[
                    &open,
                    &panel,
                    &pause,
                    &stop,
                    &wake,
                    &connections,
                    &settings,
                    &hide,
                    &quit,
                ],
            )?;
            TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .icon_as_template(true)
                .tooltip("NOVA · your AI companion")
                .menu(&menu)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => {
                        let _ = set_mode(app, "pet");
                    }
                    "panel" => {
                        let _ = set_mode(app, "control");
                    }
                    "hide" => {
                        let _ = set_mode(app, "background");
                    }
                    "pause" => control(app, "pause"),
                    "stop" => control(app, "stop"),
                    "quit" => {
                        app.state::<NativeState>()
                            .quitting
                            .store(true, Ordering::SeqCst);
                        app.exit(0);
                    }
                    id => {
                        let _ = set_mode(app, "control");
                        let _ = app.emit(native_events().panel, id);
                    }
                })
                .build(app)?;
            let handle = app.handle().clone();
            thread::spawn(move || {
                let mut misses = 0;
                let mut ticks = 0;
                loop {
                    thread::sleep(Duration::from_secs(2));
                    ticks += 1;
                    let state = handle.state::<NativeState>();
                    if state.quitting.load(Ordering::SeqCst) {
                        break;
                    }
                    let info = state.backend.lock().unwrap().info.clone();
                    let available = healthy(&info);
                    let mut backend = state.backend.lock().unwrap();
                    if available {
                        misses = 0;
                        ticks = 20;
                        backend.info.status = "ready".into();
                    } else {
                        misses += 1;
                        if ticks < 20 {
                            continue;
                        }
                        backend.info.status = "recovering".into();
                    }
                    if misses >= 5 {
                        terminate(&mut backend);
                        if backend.info.restarts >= 5 {
                            backend.info.status = "failed".into();
                            write_status(&backend);
                            let _ = handle.emit(native_events().runtime, &backend.info);
                            break;
                        }
                        backend.info.restarts += 1;
                        let _ = launch(&mut backend);
                        misses = 0;
                        ticks = 0;
                    }
                    write_status(&backend);
                    let _ = handle.emit(native_events().runtime, &backend.info);
                }
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
            if window.label() == "companion" {
                if let tauri::WindowEvent::Moved(position) = event {
                let state = window.app_handle().state::<NativeState>();
                if let Ok(mut backend) = state.backend.lock() {
                    if !backend.preferences.lock_position {
                        backend.preferences.position = Some([position.x, position.y]);
                        save_preferences(&backend);
                    }
                };
                }
            }
        })
        .build(native_context())
        .expect("NOVA native initialization failed");
    app.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            let _ = voice_command(app.clone(), serde_json::json!({"action":"stop"}));
            let state = app.state::<NativeState>();
            state.quitting.store(true, Ordering::SeqCst);
            if let Ok(mut backend) = state.backend.lock() {
                api(&backend.info, "/api/runtime/stop");
                terminate(&mut backend);
                backend.info.status = "stopped".into();
                write_status(&backend);
                write_ui_status(&backend, false, false, false, "OFF");
            };
        }
    });
}

#[cfg(test)]
mod tests {
    use super::PACKAGED_BACKEND_URL;

    #[test]
    fn packaged_backend_uses_fixed_local_port() {
        assert_eq!(PACKAGED_BACKEND_URL, "http://127.0.0.1:8742");
    }
}
