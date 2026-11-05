import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["node_modules", "out", "dist"] },
  {
    // Electron main process: Node globals, ESM.
    files: ["src/**/*.js", "tests/**/*.js", "*.config.js"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: { ...globals.node },
    },
    rules: {
      ...js.configs.recommended.rules,
      "no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
    },
  },
  {
    // Preload is CommonJS on purpose: a sandboxed Electron preload cannot use
    // ESM imports. It also runs in a renderer-adjacent context, so it sees
    // browser globals rather than Node's.
    files: ["src/**/*.cjs"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "commonjs",
      globals: { ...globals.browser, ...globals.node },
    },
    rules: { ...js.configs.recommended.rules },
  },
];
