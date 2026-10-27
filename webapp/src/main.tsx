import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles.css";
import { tg } from "./telegram";
import { applyTheme, watchTelegramTheme } from "./theme";

applyTheme();
watchTelegramTheme();
tg?.ready();
tg?.expand();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
