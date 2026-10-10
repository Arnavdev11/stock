"use strict";
/* The changes approved for the historical-data handoff and the five-stock research test batch (tests/approved_workflow_changes.json; the Python guards read the same file).
   update.yml may differ from HEAD only by the listed added lines; historical.yml and save_historical.yml must be byte-for-byte the pinned versions. */
const fs = require("fs"), cp = require("child_process"), crypto = require("crypto");
const CFG = JSON.parse(fs.readFileSync(__dirname + "/approved_workflow_changes.json", "utf8"));
const UPD = CFG.update_yml.path, PINNED = CFG.pinned_files, UPDATERS = CFG.updater_changes, files = [UPD].concat(Object.keys(PINNED), Object.keys(UPDATERS));
function isApproved(root, f) {
  if (f === UPD) {
    const d = cp.execSync("git diff -U0 HEAD -- " + UPD, { cwd: root, encoding: "utf8" }).split("\n").filter((l) => /^[+-]/.test(l) && !/^(\+\+\+|---)/.test(l));
    return JSON.stringify(d) === JSON.stringify(CFG.update_yml.added_lines.map((l) => "+" + l));
  }
  if (UPDATERS[f]) {
    const d = cp.execSync("git diff -U0 HEAD -- " + f, { cwd: root, encoding: "utf8" }).split("\n").filter((l) => /^[+-]/.test(l) && !/^(\+\+\+|---)/.test(l));
    return JSON.stringify(d) === JSON.stringify(UPDATERS[f]);
  }
  if (PINNED[f]) { try { return crypto.createHash("sha256").update(fs.readFileSync(root + "/" + f)).digest("hex") === PINNED[f]; } catch (e) { return false; } }
  return false;
}
module.exports = { files, isApproved };
