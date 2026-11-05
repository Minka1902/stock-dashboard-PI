// Builds the extension for Chrome and/or Firefox.
//
//   node scripts/build.js            -> both
//   node scripts/build.js chrome     -> dist-chrome only
//   node scripts/build.js firefox    -> dist-firefox only
//
// Each target runs three Vite passes (see vite.config.js) into its own dist
// directory, then gets a generated manifest.json. We build per-target rather than
// building once and copying, because it keeps the passes and the manifest in one
// obvious sequence and a full build takes a couple of seconds.
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { manifestFor } from "../manifest.config.js";

const ROOT = resolve(import.meta.dirname, "..");
const PASSES = ["pages", "background", "content"];

function vite(pass, outDir) {
  execFileSync("npx", ["vite", "build"], {
    cwd: ROOT,
    stdio: "inherit",
    env: { ...process.env, SD_PASS: pass, SD_OUT: outDir },
  });
}

/**
 * Chrome refuses to load a content script containing ESM syntax, and the failure
 * is a silent no-op rather than an error — so assert the shape at build time
 * instead of discovering it on a live page.
 */
function assertContentIsClassic(outDir) {
  const file = resolve(ROOT, outDir, "content.js");
  const src = readFileSync(file, "utf8");
  if (/^\s*(import|export)\s/m.test(src)) {
    throw new Error(
      `${outDir}/content.js contains ESM syntax; Chrome content scripts must be classic scripts. ` +
        "Check the IIFE lib config in vite.config.js.",
    );
  }
}

function build(target) {
  const outDir = `dist-${target}`;
  console.log(`\n=== building ${target} -> ${outDir} ===`);
  for (const pass of PASSES) vite(pass, outDir);
  assertContentIsClassic(outDir);

  const dir = resolve(ROOT, outDir);
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    resolve(dir, "manifest.json"),
    JSON.stringify(manifestFor(target), null, 2) + "\n",
  );
  console.log(`wrote ${outDir}/manifest.json`);
}

const arg = process.argv[2];
const targets = arg ? [arg] : ["chrome", "firefox"];
for (const t of targets) {
  if (t !== "chrome" && t !== "firefox") {
    console.error(`unknown target "${t}" (expected chrome or firefox)`);
    process.exit(1);
  }
  build(t);
}
