import { useEffect, useState } from "react";
import { localFetch } from "../../services/api";

export function LivePreview({ taskId }: { taskId: string }) {
  const [image, setImage] = useState("");
  const [notice, setNotice] = useState("Checking local screen access…");
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let url = ""; let timer: ReturnType<typeof setTimeout>;
    async function update() {
      try {
        const response = await localFetch(`/api/computer/tasks/${taskId}/preview`, { signal: controller.signal, cache: "no-store" });
        if (response.ok) {
          const next = URL.createObjectURL(await response.blob()); if (controller.signal.aborted) { URL.revokeObjectURL(next); return; }
          if (url) URL.revokeObjectURL(url); url = next; setImage(next); setNotice("");
        } else {
          if (url) URL.revokeObjectURL(url); url = ""; setImage(""); const problem = await response.json(); setNotice(problem.detail ?? "Preview paused or unavailable.");
        }
      } catch { if (!controller.signal.aborted) { setImage(""); setNotice("Preview unavailable."); } }
      finally { if (!controller.signal.aborted) timer = setTimeout(() => void update(), 1100); }
    }
    void update();
    return () => { controller.abort(); clearTimeout(timer); if (url) URL.revokeObjectURL(url); };
  }, [taskId]);
  return <div className={`live-preview ${expanded ? "live-preview--expanded" : ""}`}><header><span>WATCH NOVA · VIEW ONLY</span><button onClick={() => setExpanded(value => !value)}>{expanded ? "Compact" : "Expand"}</button></header>
    {image ? <img src={image} alt="Local active-window preview, view only" draggable={false} /> : <p>{notice}</p>}
  </div>;
}
