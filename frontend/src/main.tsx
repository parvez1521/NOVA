import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles.css";
import { initializeNative } from "./services/native";

initializeNative().then(() => createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)).catch(() => { document.getElementById("root")!.textContent = "NOVA could not start its local runtime. Quit and reopen the app."; });
