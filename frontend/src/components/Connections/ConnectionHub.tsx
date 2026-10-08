import { useEffect, useState } from "react";
import { connectorsApi } from "../../services/connectors";
import type { ConnectorSnapshot, ConnectorState, ConnectorStatus } from "../../types/connectors";

const labels: Record<ConnectorStatus, string> = {
  CONNECTED: "Connected",
  AVAILABLE: "Available",
  NEEDS_SETUP: "Needs setup",
  EXPIRED: "Reconnect required",
  UNAVAILABLE: "Unavailable",
  REQUIRES_REAUTH: "Reconnect required",
  PERMISSION_DENIED: "Permission denied",
  PARTIAL: "Partial access",
  ERROR: "Error",
};

export function ConnectionHub({ onClose }: { onClose: () => void }) {
  const [state, setState] = useState<ConnectorState | null>(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  async function load() {
    try { setState(await connectorsApi.state()); setNotice(""); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Connections are unavailable."); }
  }
  useEffect(() => { void load(); }, []);

  async function action(connector: ConnectorSnapshot, type: "connect" | "disconnect" | "refresh") {
    setBusy(connector.name); setNotice("");
    try {
      if (type === "connect") {
        const result = await connectorsApi.connect(connector.name);
        if (result.authorization_url) {
          window.open(result.authorization_url, "nova-google-authorization", "popup,width=560,height=720");
          setNotice("Authorization opened in a separate window. Return here after approving access.");
        } else setNotice(result.detail || "This connector is not ready for account authorization yet.");
      } else if (type === "disconnect") {
        await connectorsApi.disconnect(connector.name);
        setNotice(`${connector.name} disconnected.`);
      } else {
        await connectorsApi.refresh(connector.name);
        setNotice(`${connector.name} refreshed.`);
      }
      await load();
    } catch (error) { setNotice(error instanceof Error ? error.message : "Connector action failed."); }
    finally { setBusy(""); }
  }

  return <aside className="voice-settings connection-hub" aria-label="Connections">
    <header><div><p className="eyebrow">NOVA</p><h2>Connections</h2></div><button onClick={onClose} aria-label="Close connections">×</button></header>
    <p>Optional services stay separate from NOVA’s local brain. Credentials remain in the backend secure store and never enter the frontend.</p>
    {state && !state.enabled && <p className="computer-real-note">Connections are disabled in the current configuration. Enable <code>CONNECTORS_ENABLED</code> and restart NOVA after choosing the services you want.</p>}
    <div className="connection-grid">
      {state?.connectors.map(connector => <article className="connection-card" key={connector.name}>
        <div className="connection-card__icon" aria-hidden="true">{connector.icon}</div>
        <div className="connection-card__body"><header><h3>{connector.name}</h3><span className={`connection-status connection-status--${connector.status.toLowerCase()}`}>{labels[connector.status]}</span></header>
          <p>{connector.description}</p><small>{connector.detail}</small>
          <div className="connection-card__meta"><span>{connector.auth === "oauth2" ? "OAuth" : connector.auth === "mcp" ? "Explicit server" : connector.auth}</span><span>{connector.capabilities.slice(0, 3).join(" · ")}</span></div>
          <div className="persistence-actions">{connector.connected ? <><button disabled={busy === connector.name} onClick={() => void action(connector, "refresh")}>Refresh</button><button disabled={busy === connector.name} onClick={() => void action(connector, "disconnect")}>Disconnect</button></> : <button disabled={busy === connector.name || connector.status !== "NEEDS_SETUP"} onClick={() => void action(connector, "connect")}>{connector.status === "NEEDS_SETUP" ? "Connect" : "Not available"}</button>}</div>
        </div>
      </article>)}
    </div>
    {!state && !notice && <p role="status">Loading connection services…</p>}
    {notice && <p role="status">{notice}</p>}
    <div className="persistence-actions"><button onClick={() => void load()}>Refresh list</button><button onClick={onClose}>Done</button></div>
  </aside>;
}
