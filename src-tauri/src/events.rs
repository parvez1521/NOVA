use serde::Deserialize;
use std::sync::OnceLock;

#[derive(Deserialize)]
pub struct NativeEvents {
    pub voice: &'static str,
    pub hotkey: &'static str,
    pub control: &'static str,
    pub mode: &'static str,
    pub panel: &'static str,
    pub runtime: &'static str,
}

pub fn native_events() -> &'static NativeEvents {
    static EVENTS: OnceLock<NativeEvents> = OnceLock::new();
    EVENTS.get_or_init(|| {
        serde_json::from_str(include_str!("../../frontend/src/services/nativeEvents.json"))
            .expect("Invalid native event contract")
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::{Emitter, Manager};

    fn listen_request(name: &str) -> tauri::webview::InvokeRequest {
        tauri::webview::InvokeRequest {
            cmd: "plugin:event|listen".into(),
            callback: tauri::ipc::CallbackFn(0),
            error: tauri::ipc::CallbackFn(1),
            url: "tauri://localhost".parse().unwrap(),
            body: tauri::ipc::InvokeBody::Json(serde_json::json!({"event":name,"target":{"kind":"Any"},"handler":0})),
            headers: Default::default(),
            invoke_key: tauri::test::INVOKE_KEY.to_string(),
        }
    }

    #[test]
    fn shared_event_contract_is_valid_for_tauri() {
        let events = native_events();
        let app = tauri::test::mock_app();
        for name in [events.voice, events.hotkey, events.control, events.mode, events.panel, events.runtime] {
            assert!(app.emit(name, ()).is_ok(), "Invalid event: {name}");
        }
    }

    #[test]
    #[allow(deprecated)]
    fn exact_previous_wake_event_is_rejected_by_tauri() {
        let mut app = tauri::test::mock_builder().build(crate::native_context()).unwrap();
        app.run_iteration(|_, _| {});
        let webview = app.get_webview_window("main").unwrap();
        let response = tauri::test::get_ipc_response(&webview, listen_request("native.voice"));
        let error = response.err().expect("The previous name must fail");
        assert!(error.as_str().unwrap().contains("invalid args `event` for command `listen`"), "{error}");
        assert!(error.as_str().unwrap().contains("Event name must include only alphanumeric characters"), "{error}");
    }

    #[test]
    #[allow(deprecated)]
    fn shared_names_register_through_native_listen_ipc() {
        let mut app = tauri::test::mock_builder().build(crate::native_context()).unwrap();
        app.run_iteration(|_, _| {});
        let webview = app.get_webview_window("main").unwrap();
        let events = native_events();
        for name in [events.voice, events.hotkey, events.control, events.mode, events.panel, events.runtime] {
            assert!(tauri::test::get_ipc_response(&webview, listen_request(name)).is_ok(), "Invalid subscription: {name}");
        }
    }
}
