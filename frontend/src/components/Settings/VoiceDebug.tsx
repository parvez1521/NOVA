export function VoiceDebug({ data }: { data: Record<string, unknown> }) {
  return <aside className="voice-debug" aria-label="Voice debug panel"><h2>NOVA · Voice debug</h2><dl>
    {Object.entries(data).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{String(value ?? "—")}</dd></div>)}
  </dl><p>Cmd+Shift+D to close · no raw audio</p></aside>;
}
