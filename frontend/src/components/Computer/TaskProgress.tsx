import type { TaskProgress as Progress } from "../../types/computer";
import { useState } from "react";
import { LivePreview } from "./LivePreview";

export function TaskProgress({ task, onControl, watchEnabled }: { task: Progress; onControl: (type: string, payload?: Record<string, unknown>) => void; watchEnabled?: boolean }) {
  const [preview, setPreview] = useState(false);
  const ended = ["COMPLETED", "FAILED", "CANCELLED", "INTERRUPTED"].includes(task.status);
  const queued = task.status === "QUEUED";
  return <section className="task-progress" aria-label="Computer task progress">
    <header><span>{task.simulation ? "SIMULATION" : "COMPUTER TASK"}</span><small>{task.mode.replaceAll("_", " ")}</small></header>
    <strong>{task.goal}</strong><p className={queued ? "task-queued" : undefined} aria-live="polite">{queued ? "Queued — waiting for NOVA’s computer-control slot." : task.currentStep}</p>
     {watchEnabled && !task.simulation && !ended && <button onClick={() => setPreview(value => !value)}>{preview ? "Hide preview" : "Watch NOVA"}</button>}
     {Boolean(task.plan?.length) && <details className="task-plan"><summary>Goal plan · {task.plan?.length} steps</summary><ol>{task.plan?.map((step, index) => <li key={`${index}-${step}`}>{step}</li>)}</ol></details>}
     {preview && watchEnabled && !ended && <LivePreview taskId={task.id} />}
    <ul>{task.completed.slice(-5).map((step, index) => <li key={index}>✓ {step}</li>)}
      {!ended && <li>→ Step {task.stepIndex + 1} · {task.currentStep}</li>}</ul>
    {Boolean(task.timeline?.length) && <details className="task-timeline"><summary>Activity · {task.timeline?.length} events</summary><ol>{task.timeline?.slice(-30).map((event, index) => <li key={`${event.timestamp}-${index}`}><time>{new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time><span>{event.summary}</span></li>)}</ol></details>}
    {task.status === "WAITING_FOR_CONFIRMATION" && <div className="task-approval"><p>Confirm this exact action?</p>
      <code>{task.confirmation?.tool}</code><pre>{JSON.stringify(task.confirmation?.arguments, null, 2)}</pre>
      <button onClick={() => onControl("task.confirm", { confirmed: true })}>Confirm action</button>
      <button onClick={() => onControl("task.confirm", { confirmed: false })}>Cancel</button>
    </div>}
    {!ended && <div className="persistence-actions">
      <button onClick={() => onControl(task.status === "PAUSED" ? "task.resume" : "task.pause")}>{task.status === "PAUSED" ? "Continue" : "Pause"}</button>
      <button onClick={() => onControl("task.stop")}>Stop task · Esc</button>
    </div>}
  </section>;
}
