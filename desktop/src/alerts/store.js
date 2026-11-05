// Tiny JSON store under app.getPath("userData").
//
// Replaces the extension's chrome.storage.local. Written atomically (tmp +
// rename) because the alternative — a truncated settings file after a crash —
// would reset `seeded` and fire a hundred notifications on next launch.

import fs from "node:fs";
import path from "node:path";

export class JsonStore {
  constructor(file, defaults = {}) {
    this.file = file;
    this.defaults = defaults;
    this.data = { ...defaults };
    this.load();
  }

  load() {
    try {
      const raw = fs.readFileSync(this.file, "utf8");
      this.data = { ...this.defaults, ...JSON.parse(raw) };
    } catch {
      // Missing or corrupt — defaults are the right answer either way.
      this.data = { ...this.defaults };
    }
    return this.data;
  }

  get(key) {
    return this.data[key];
  }

  set(patch) {
    this.data = { ...this.data, ...patch };
    const tmp = `${this.file}.tmp`;
    try {
      fs.mkdirSync(path.dirname(this.file), { recursive: true });
      fs.writeFileSync(tmp, JSON.stringify(this.data, null, 2), "utf8");
      fs.renameSync(tmp, this.file);
    } catch (err) {
      // Losing a write is survivable; crashing the main process is not.
      console.error("settings write failed:", err);
    }
    return this.data;
  }
}
