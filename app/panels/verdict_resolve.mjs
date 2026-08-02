// D10 gate — the client RENDERS, never DECIDES. Loads the real index.html in
// jsdom and asserts resolveEgressVerdict() never softens a server 'prohibited',
// lets the reserved-by-law floor only TIGHTEN a more-permissive verdict, and
// fails CLOSED (most-restrictive) on an unknown/missing verdict. No serve.py
// needed — this exercises the pure client verdict-resolution rule.
// Usage: node verdict_resolve.mjs
import { JSDOM } from "jsdom";
import { composeStatic } from "../harness/render_harness.mjs";

const html = composeStatic(new URL("../src/index.html", import.meta.url));
const fail = (m) => { console.log("FAIL: " + m); process.exit(1); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const dom = new JSDOM(html, {
  runScripts: "dangerously",
  beforeParse(window) {
    window.__WORKSPACES_HTTP__ = "http://127.0.0.1:1/tool";   // unreachable; not used here
    window.fetch = () => Promise.reject(new Error("offline"));
  },
});
const { window } = dom;

async function main() {
  for (let i = 0; i < 50 && typeof window.resolveEgressVerdict !== "function"; i++) await sleep(20);
  const R = window.resolveEgressVerdict;
  if (typeof R !== "function") fail("resolveEgressVerdict is not defined on the page");

  // [serverVerdict, reservedFloor, expected]
  const cases = [
    ["prohibited", true,  "prohibited"],  // D10: floor must NOT soften a refusal
    ["prohibited", false, "prohibited"],
    ["auto",       true,  "reserved"],    // floor lifts a permissive verdict
    ["auto",       false, "auto"],
    ["human",      true,  "reserved"],
    ["human",      false, "human"],
    ["reserved",   false, "reserved"],
    ["reserved",   true,  "reserved"],   // floor never weakens an already-reserved verdict
    ["refused",    false, "refused"],
    ["bogus",      false, "prohibited"],  // unknown → fail-closed (not permissive)
    ["bogus",      true,  "prohibited"],
    ["",           true,  "prohibited"],
    [undefined,    false, "prohibited"],
  ];
  for (const [v, f, exp] of cases) {
    const got = R(v, f);
    if (got !== exp) fail(`resolveEgressVerdict(${JSON.stringify(v)}, ${f}) = ${got}; expected ${exp}`);
  }
  console.log("PASS: client never softens a server 'prohibited'; floor only tightens; unknown fails closed (D10)");
  process.exit(0);
}
main();
