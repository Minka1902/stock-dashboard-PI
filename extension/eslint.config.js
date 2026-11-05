import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["dist-chrome", "dist-firefox", "node_modules"] },
  {
    files: ["**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: "latest",
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.webextensions,
        chrome: "readonly",
      },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    rules: {
      ...js.configs.recommended.rules,
      // JSX components read as unused to the base rule.
      "no-unused-vars": ["error", { varsIgnorePattern: "^[A-Z_]", argsIgnorePattern: "^_" }],
    },
  },
  {
    // Node context: build tooling and tests.
    files: ["scripts/**/*.js", "tests/**/*.js", "*.config.js"],
    languageOptions: { globals: { ...globals.node } },
  },
];
