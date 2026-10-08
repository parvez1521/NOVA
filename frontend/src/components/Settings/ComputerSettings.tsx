import { useEffect, useState } from "react";
import { computerApi } from "../../services/computer";
import type { ComputerSettings as Options, SystemPermissions } from "../../types/computer";

export function ComputerSettings({ onClose, onSave }: { onClose: () => void; onSave: (settings: Options) => void }) {
  const [options, setOptions] = useState<Options | null>(null);
  const [permissions, setPermissions] = useState<SystemPermissions | null>(null);
  const [microphone, setMicrophone] = useState("browser-controlled");
  const [error, setError] = useState(""); const [saving, setSaving] = useState(false);
  const [history, setHistory] = useState<{ id: string; goal: string; status: string; summary: string }[]>([]);
  useEffect(() => {
    void Promise.all([computerApi.settings(), computerApi.permissions(), computerApi.history()]).then(([settings, permissions, history]) => { setOptions(settings); setPermissions(permissions); setHistory(history); }).catch((error) => setError(error.message));
    void navigator.permissions?.query({ name: "microphone" as PermissionName }).then((result) => setMicrophone(result.state)).catch(() => undefined);
  }, []);
  const update = <K extends keyof Options>(key: K, value: Options[K]) => setOptions((current) => current && ({ ...current, [key]: value }));
  return <section className="voice-settings computer-settings" aria-label="Computer use settings">
    <header><h2>Computer use</h2><button onClick={onClose} aria-label="Close computer settings">×</button></header>
    <p>Task planning, observed actions, verification, and human control. Escape stops the task.</p>
    {options && <form onSubmit={async (event) => {
      event.preventDefault(); setSaving(true); setError("");
      try { const settings = await computerApi.save(options); onSave(settings); }
      catch (error) { setError(error instanceof Error ? error.message : "Save failed."); }
      finally { setSaving(false); }
    }}>
      {(["enabled", "simulation", "screen_access", "accessibility_access", "browser_access", "filesystem_access", "terminal_access", "vision_enabled", "require_confirmation", "allow_cloud_planning", "watch_nova"] as const).map((key) => <label className="voice-settings__toggle" key={key}>
        <span>{{ enabled: "Computer Use Enabled", simulation: "Simulation Mode", screen_access: "Screen Access", accessibility_access: "Accessibility Access", browser_access: "Browser Access", filesystem_access: "Filesystem Access", terminal_access: "Terminal Access (read-only)", vision_enabled: "Vision Enabled", require_confirmation: "Confirm every action", allow_cloud_planning: "Allow cloud task planning", watch_nova: "Watch NOVA · allow local task previews" }[key]}</span>
        <input type="checkbox" checked={options[key]} onChange={(event) => update(key, event.target.checked)} />
      </label>)}
      {!options.simulation && <p className="computer-real-note">Real mode operates on this Mac. Risky actions always require confirmation, in every mode.</p>}
      <label>Agent mode<select value={options.mode} onChange={(event) => update("mode", event.target.value as Options["mode"])}>
        <option value="ASSISTED">Assisted</option><option value="SEMI_AUTONOMOUS">Semi-autonomous</option><option value="AUTONOMOUS">Autonomous</option>
      </select></label>
      <label>Task timeout (seconds)<input type="number" min="10" max="3600" value={options.task_timeout_seconds} onChange={(event) => update("task_timeout_seconds", Number(event.target.value))} /></label>
      <label>Cursor visibility<select value={options.action_visibility_mode} onChange={event => update("action_visibility_mode",event.target.value as Options["action_visibility_mode"])}><option value="FAST">Fast</option><option value="NORMAL">Normal · smooth</option><option value="HUMAN_VISIBLE">Human visible · slower</option></select></label>
      <p>Watch NOVA captures only while a real task is active and its view-only preview is open. Native cursor tools move smoothly; browser DOM operations currently use the existing fixed CDP path.</p>
      <label>Maximum task steps<input type="number" min="1" max="100" value={options.max_task_steps} onChange={(event) => update("max_task_steps", Number(event.target.value))} /></label>
      <label>Optional installed local vision model<input value={options.local_vision_model} onChange={(event) => update("local_vision_model", event.target.value)} placeholder="No model downloaded automatically" /></label>
      <button disabled={saving}>{saving ? "Saving…" : "Save computer settings"}</button>
    </form>}
    <h3>System permissions</h3>
    <dl className="permission-list"><dt>Microphone</dt><dd>{microphone}</dd><dt>Accessibility</dt><dd>{permissions?.permission_state?.accessibility === "GRANTED" ? "Granted" : permissions?.permission_state?.accessibility === "DISABLED_IN_NOVA" ? "Disabled in NOVA settings" : "Required for mouse/keyboard"}</dd>
      <dt>Screen Recording</dt><dd>{permissions?.permission_state?.screen_recording === "GRANTED" ? "Granted" : permissions?.permission_state?.screen_recording === "DISABLED_IN_NOVA" ? "Disabled in NOVA settings" : "Required for screenshots"}</dd>
      <dt>Browser Access</dt><dd>{permissions?.permission_state?.browser_access === "ENABLED" ? "Enabled" : "Disabled in NOVA settings"}</dd>
      <dt>Filesystem Access</dt><dd>{permissions?.permission_state?.filesystem_access === "ENABLED" ? "Enabled" : "Disabled in NOVA settings"}</dd>
      <dt>Automation</dt><dd>{permissions?.automation ?? "Not required"}</dd></dl>
    <p>Add <strong>NOVA Computer Access.app</strong> under macOS Privacy & Security → Accessibility and Screen & System Audio Recording. Path: <code>{permissions?.helper_path ?? "backend/data/computer/NOVA Computer Access.app"}</code>. Use +, then Cmd+Shift+G.</p>
    <p>When launched from Terminal, macOS may attribute Screen Recording to Terminal. Enable the responsible launcher shown by the macOS prompt, then refresh permissions.</p>
    <div className="persistence-actions"><button onClick={() => { void computerApi.permissions().then(setPermissions).catch((error) => setError(error.message)); }}>Refresh permissions</button>
      <button onClick={() => { void computerApi.requestPermission("accessibility").catch((error) => setError(error.message)); }}>Request Accessibility</button>
      <button onClick={() => { void computerApi.requestPermission("screen_recording").catch((error) => setError(error.message)); }}>Request Screen Access</button>
    </div>
    <h3>Recent tasks</h3>{history.map((task) => <article className="memory-item" key={task.id}><p>{task.goal}</p><small>{task.status}</small><p>{task.summary}</p></article>)}
    {error && <p role="alert">{error}</p>}
  </section>;
}
