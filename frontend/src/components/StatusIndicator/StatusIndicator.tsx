import type { PetState } from "../../types/nova";

interface StatusIndicatorProps {
  state: PetState;
}

const labels: Record<PetState, string> = {
  idle: "Ready when you are",
  listening: "Listening",
  thinking: "Thinking",
  speaking: "Speaking",
  happy: "Feeling good",
  excited: "Excited",
  confused: "A little confused",
  sleeping: "Taking a tiny break",
  warning: "Needs your attention",
  error: "Something went wrong",
  working: "Working on it",
  success: "Done",
};

export function StatusIndicator({ state }: StatusIndicatorProps) {
  return (
    <div className="status-indicator" aria-live="polite">
      <span className={`status-indicator__dot status-indicator__dot--${state}`} />
      <span>{labels[state]}</span>
    </div>
  );
}
