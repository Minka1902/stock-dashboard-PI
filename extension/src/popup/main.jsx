import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import Popup from "./Popup.jsx";
import { getSettings } from "../lib/storage.js";
import "../styles/popup.css";

// The dashboard persists its theme to localStorage, which extension pages cannot
// read, so the extension keeps its own copy in storage.sync.
getSettings().then(({ theme }) => {
  document.documentElement.dataset.theme = theme;
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Popup />
  </StrictMode>,
);
