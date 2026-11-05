// Parameterized by SD_PASS, because an extension needs three different output
// shapes from one source tree (see scripts/build.js):
//
//   pages      - popup.html + options.html, normal ESM, code-splitting is fine
//   background - ONE file. A module service worker's imports resolve against the
//                extension root, so rollup chunking would scatter them.
//   content    - ONE file, IIFE. Chrome content scripts cannot be ES modules.
//
// SD_OUT selects the output directory so the same passes can target dist-chrome
// and dist-firefox.
//
// The pages pass sets `root: src/` and keeps the two HTML files directly in it,
// so they land at the output root as popup.html / options.html — the manifest
// references them as bare names.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

const pass = process.env.SD_PASS || "pages";
const here = import.meta.dirname;
const outDir = resolve(here, process.env.SD_OUT || "dist-chrome");

const common = {
  outDir,
  // Only the first pass clears the directory; later passes append to it.
  emptyOutDir: pass === "pages",
  minify: false, // Store reviewers read this source; keep it legible.
  target: "es2022",
  sourcemap: false,
};

export default defineConfig(() => {
  if (pass === "background" || pass === "content") {
    const isContent = pass === "content";
    return {
      publicDir: false,
      build: {
        ...common,
        cssCodeSplit: false,
        lib: {
          entry: resolve(here, `src/${pass}/index.js`),
          formats: [isContent ? "iife" : "es"],
          name: isContent ? "StockSignalContent" : undefined,
          fileName: () => `${pass}.js`,
        },
      },
    };
  }

  return {
    root: resolve(here, "src"),
    publicDir: resolve(here, "public"),
    plugins: [react()],
    build: {
      ...common,
      rollupOptions: {
        input: {
          popup: resolve(here, "src/popup.html"),
          options: resolve(here, "src/options.html"),
        },
      },
    },
  };
});
