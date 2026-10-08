import { useEffect, useState } from "react";
import { nativeCommand, type NativePreferences } from "../../services/native";
import { enable, disable, isEnabled } from "@tauri-apps/plugin-autostart";
import { isPermissionGranted, requestPermission } from "@tauri-apps/plugin-notification";

export function DesktopSettings({ onClose }: { onClose: () => void }) {
  const [options, setOptions] = useState<NativePreferences | null>(null);
  const [startup, setStartup] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => { void nativeCommand<NativePreferences>("native_preferences").then(setOptions); void isEnabled().then(setStartup); }, []);
  async function save() {
    if (!options) return;
    try { await nativeCommand("configure_native", { preferences: options }); if (startup) await enable(); else await disable(); setNotice("Desktop preferences saved."); }
    catch (error) { setNotice(String(error)); }
  }
  return <aside className="voice-settings" aria-label="Desktop settings"><header><h2>Desktop</h2><button onClick={onClose}>Close</button></header>
    <p>A quiet companion, always within reach.</p>
    {options && <><label className="voice-settings__toggle">Always on top<input type="checkbox" checked={options.always_on_top} onChange={e => setOptions({ ...options, always_on_top: e.target.checked })} /></label>
      <label className="voice-settings__toggle">Lock companion position<input type="checkbox" checked={options.lock_position} onChange={e => setOptions({ ...options, lock_position: e.target.checked })} /></label>
      <label className="voice-settings__toggle">Global hold-to-talk<input type="checkbox" checked={options.hotkey_enabled} onChange={e => setOptions({ ...options, hotkey_enabled: e.target.checked })} /></label>
      <label>Interaction shortcut<select value={options.hotkey} onChange={e => setOptions({ ...options, hotkey: e.target.value })}><option>Alt+Space</option><option>Alt+V</option><option>Control+Shift+Space</option></select></label></>}
    <label className="voice-settings__toggle">Start quietly at login<input type="checkbox" checked={startup} onChange={e => setStartup(e.target.checked)} /></label>
    <button onClick={() => { void (async () => setNotice(await isPermissionGranted() || await requestPermission() === "granted" ? "Notifications enabled." : "Notifications not enabled."))(); }}>Enable notifications</button>
    <div className="persistence-actions"><button onClick={() => void save()}>Save</button><button onClick={() => void nativeCommand("restart_backend").then(() => setNotice("Local runtime restarting. Interrupted tasks will not resume."))}>Restart runtime</button></div>
    {notice && <p role="status">{notice}</p>}
  </aside>;
}
