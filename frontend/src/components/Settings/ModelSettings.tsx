import { useEffect, useState } from "react";
import { modelCatalog, saveRouting } from "../../services/api";
import type { ModelActivation, ModelDescriptor, RoutingSettings } from "../../types/nova";

export function ModelSettings({ onClose }: { onClose: () => void }) {
  const [models, setModels] = useState<ModelDescriptor[]>([]);
  const [routing, setRouting] = useState<RoutingSettings | null>(null);
  const [activation, setActivation] = useState<ModelActivation | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const load = (refresh = false) => { setBusy(true); void modelCatalog(refresh).then(value => { setModels(value.models); setRouting(value.routing); setActivation(value.activation); }).catch(value => setError(value instanceof Error ? value.message : "Model catalog unavailable.")).finally(() => setBusy(false)); };
  useEffect(() => { load(); }, []);
  const update = async (value: Partial<RoutingSettings>) => { if (!routing) return; setBusy(true); try { setRouting(await saveRouting(value)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Routing settings could not be saved."); } finally { setBusy(false); } };
  return <section className="voice-settings model-settings" aria-label="AI model settings">
    <header><h2>AI Models</h2><button type="button" onClick={onClose} aria-label="Close model settings">×</button></header>
    <p>One model per request. Free-only and zero-budget policies block unknown or paid cloud pricing.</p>
     {routing && <>
      <label>Routing mode<select value={routing.mode} onChange={event => void update({ mode: event.target.value as RoutingSettings["mode"] })}>
        <option>LOCAL_ONLY</option><option>FREE_ONLY</option><option>BALANCED</option><option>BEST_AVAILABLE</option><option>OFFLINE</option>
      </select></label>
      <label className="voice-settings__toggle"><span>Free-only cloud routing</span><input type="checkbox" checked={routing.free_only} onChange={event => void update({ free_only: event.target.checked })} /></label>
      <label className="voice-settings__toggle"><span>Zero-budget enforcement</span><input type="checkbox" checked={routing.zero_budget} onChange={event => void update({ zero_budget: event.target.checked })} /></label>
      <label>Privacy mode<select value={routing.privacy_mode} onChange={event => void update({ privacy_mode: event.target.value as RoutingSettings["privacy_mode"] })}><option>LOCAL_FIRST</option><option>STRICT_LOCAL</option><option>CLOUD_ALLOWED</option></select></label>
     </>}
     {activation && <div className="model-activation" aria-label="Provider activation status">
       <p><strong>Gemini:</strong> {activation.gemini.configured ? "Connected" : "Not configured"} · {activation.gemini.free_eligible_count} free eligible · {activation.gemini.status.replaceAll("_", " ")} · selected {activation.gemini.model}</p>
       <small>{activation.gemini.pricing_status.replaceAll("_", " ")} · {activation.gemini.pricing_sources?.[0] ?? "No verified pricing source"}</small>
       <p><strong>OpenRouter:</strong> {activation.openrouter.configured ? "Connected" : "Not configured"} · {activation.openrouter.free_eligible_count} free eligible · {activation.openrouter.status.replaceAll("_", " ")} · selected {activation.openrouter.model}</p>
       <small>{activation.openrouter.pricing_status.replaceAll("_", " ")} · explicit zero pricing from live catalog</small>
       {activation.openrouter.rate_limit?.rate_limited_models.length ? <p role="status">OpenRouter free routing is temporarily rate-limited for {activation.openrouter.rate_limit.rate_limited_models.length} model(s).</p> : null}
     </div>}
     <div className="model-catalog">{models.map(model => <article key={`${model.provider}:${model.model}`} className="model-card">
      <div><strong>{model.name || model.model}</strong><small>{model.provider} · {model.model}</small></div>
      <span className={`connection-pill connection-pill--${model.availability === "AVAILABLE" ? "online" : "offline"}`}>{model.pricing_state}</span>
      <p>{model.local ? "Local machine" : "Cloud"} · {model.capabilities.vision === true ? "vision" : "text"} · {model.capabilities.tools === true ? "tools" : "tools unknown"}</p>
    </article>)}{!busy && !models.length && <p>No model metadata is currently available. Local Ollama discovery will appear when Ollama is running.</p>}</div>
    {error && <p role="alert">{error}</p>}<button type="button" onClick={() => load(true)} disabled={busy}>{busy ? "Refreshing…" : "Refresh model catalog"}</button>
  </section>;
}
