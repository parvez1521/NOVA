interface Props {
  disabled: boolean;
  listening: boolean;
  onStart: () => void;
  onEnd: () => void;
}

export function VoiceButton({ disabled, listening, onStart, onEnd }: Props) {
  return <button
    className={`icon-button icon-button--primary ${listening ? "is-active" : ""}`}
    type="button" disabled={disabled} aria-label="Hold to talk" aria-pressed={listening}
    title="Hold to talk; release to send. Option+Space while NOVA has focus."
    onPointerDown={(event) => { event.preventDefault(); event.currentTarget.setPointerCapture(event.pointerId); onStart(); }}
    onPointerUp={onEnd} onPointerCancel={onEnd} onLostPointerCapture={onEnd}
    onKeyDown={(event) => { if ([" ", "Enter"].includes(event.key) && !event.repeat) { event.preventDefault(); onStart(); } }}
    onKeyUp={(event) => { if ([" ", "Enter"].includes(event.key)) { event.preventDefault(); onEnd(); } }}
    onBlur={onEnd}
  ><span className="mic-icon" aria-hidden="true" /></button>;
}
