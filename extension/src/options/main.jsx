import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import Options from "./Options.jsx";
import { getSettings } from "../lib/storage.js";
import "../styles/popup.css";

getSettings().then(({ theme }) => {
  document.documentElement.dataset.theme = theme;
});

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <Options />
  </StrictMode>,
);
