/**
 * The halving countdown rolls into the next 210,000-block epoch.
 * Runs the real app.js boot against a stub DOM.
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

test("blocksUntilNextHalving matches the pipeline rule", async () => {
  const src = fs.readFileSync(path.join(__dirname, "../../assets/app.js"), "utf8");
  assert.equal(src.includes("NEXT_HALVING_HEIGHT"), false);
  assert.match(src, /HALVING_INTERVAL_BLOCKS = 210_000/);

  const el = () => ({
    textContent: "",
    dataset: {},
    querySelector: () => ({ textContent: "" }),
    querySelectorAll: () => [],
  });
  const document = {
    readyState: "complete",
    getElementById: () => el(),
    addEventListener: () => {},
    dispatchEvent: () => {},
  };
  const window = {
    BER: {},
    matchMedia: () => ({ matches: true }),
    localStorage: { removeItem() {} },
    document,
    addEventListener: () => {},
  };
  const context = {
    window,
    document,
    console: { warn() {}, log() {} },
    CustomEvent: class CustomEvent {
      constructor(type) {
        this.type = type;
      }
    },
    localStorage: window.localStorage,
    fetch: async () => {
      throw new Error("offline");
    },
    performance: { now: () => 0 },
    requestAnimationFrame: (fn) => fn(),
    setTimeout,
    clearTimeout,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(src, context);
  await new Promise((resolve) => setImmediate(resolve));

  const blocks = window.BER.blocksUntilNextHalving;
  assert.equal(typeof blocks, "function");
  assert.equal(blocks(0), 210000);
  assert.equal(blocks(1049000), 1000);
  assert.equal(blocks(1049999), 1);
  assert.equal(blocks(1050000), 210000);
  assert.equal(blocks(1060000), 200000);
  assert.equal(blocks(-1), null);
  assert.equal(blocks("nope"), null);

  const ordinal = window.BER.ordinalPercentile;
  assert.equal(ordinal(3.74), "3.7th");
  assert.equal(ordinal(3.1), "3.1th");
  assert.equal(ordinal(1), "1st");
  assert.equal(ordinal(2), "2nd");
  assert.equal(ordinal(3), "3rd");
  assert.equal(ordinal(11), "11th");
  assert.equal(ordinal(22), "22nd");
});
