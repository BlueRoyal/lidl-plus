// Runtime test of frontend/panel.js: sizing, requests and calls of the page, export, barcode scanner of the app,
// menu rule and event.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const MODULE_URL = "http://ha.local:8123/lidl_plus_frontend/panel.js?v=1.2.0";
// jsdom cannot run ES modules, import.meta.url is the only module feature used
const code = fs.readFileSync(path.join(__dirname, "..", "..", "custom_components", "lidl_plus", "frontend", "panel.js"), "utf8").replaceAll("import.meta.url", JSON.stringify(MODULE_URL));
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

const dom = new JSDOM("<!DOCTYPE html><body></body>", { url: "http://ha.local:8123/lidl-plus", runScripts: "outside-only" });
const { window } = dom;
const { document } = window;
window.eval(code);

(async () => {
  // Host like ha-panel-custom of frontend 20260729+: block with safe-area padding, no height
  const host = document.createElement("div");
  host.style.cssText = "display:block;box-sizing:border-box;padding-top:20px;padding-bottom:14px";
  document.body.appendChild(host);

  const panel = document.createElement("lidl-plus-panel");
  panel.narrow = false;
  host.appendChild(panel);

  // Sizing
  assert.strictEqual(panel.style.display, "block");
  assert.ok(panel.style.height.startsWith("calc(100"), panel.style.height);
  assert.strictEqual(panel.style.getPropertyValue("--lidl-panel-host-offset"), "34px");
  const iframe = panel.querySelector("iframe");
  assert.strictEqual(iframe.getAttribute("src"), "http://ha.local:8123/lidl_plus_frontend/index.html?v=1.2.0");

  const posted = [];
  iframe.contentWindow.postMessage = (msg, origin) => posted.push({ msg, origin });
  const fromPage = (data, source = iframe.contentWindow) =>
    window.dispatchEvent(new window.MessageEvent("message", { data, source, origin: "http://ha.local:8123" }));

  // A request of the page waits until Home Assistant sets hass
  fromPage({ type: "lidl-plus:request", entry_id: "e2", known_sync: "2026-05-15T12:00:00+00:00" });
  await tick();
  assert.deepStrictEqual(posted.map((p) => [p.msg.type, p.msg.visible]), [["lidl-plus:menu", false]]);

  // Calls of the page before Home Assistant set hass get an error
  fromPage({ type: "lidl-plus:call", id: 1, request: { type: "lidl_plus/search", query: "kaffee" } });
  await tick();
  assert.deepStrictEqual([posted.at(-1).msg.type, posted.at(-1).msg.id, posted.at(-1).msg.error], ["lidl-plus:result", 1, "Home Assistant is not ready"]);

  const calls = [];
  const hass = {
    kioskMode: false,
    dockedSidebar: "docked",
    callWS: async (msg) => {
      calls.push(msg);
      if (msg.entry_id === "broken") throw new Error("Connection lost");
      if (msg.type === "auth/sign_path") return { path: `${msg.path}&authSig=signed` };
      return { entry_id: msg.entry_id || "e1", unchanged: Boolean(msg.known_sync) };
    },
  };
  panel.hass = hass;
  await tick();
  assert.strictEqual(JSON.stringify(calls), JSON.stringify([{ type: "lidl_plus/panel_data", entry_id: "e2", known_sync: "2026-05-15T12:00:00+00:00" }]));
  assert.strictEqual(posted[posted.length - 1].msg.type, "lidl-plus:data");
  assert.strictEqual(posted[posted.length - 1].msg.data.entry_id, "e2");
  assert.ok(posted.every((p) => p.origin === "http://ha.local:8123"));

  // Frequent hass updates neither repeat the request nor the menu state
  const count = posted.length;
  panel.hass = { ...hass };
  panel.hass = { ...hass };
  await tick();
  assert.strictEqual(calls.length, 1);
  assert.strictEqual(posted.length, count);

  // Messages from other windows are ignored
  fromPage({ type: "lidl-plus:request" }, window);
  await tick();
  assert.strictEqual(calls.length, 1);

  // Without entry_id/known_sync only the type is sent, errors reach the page
  fromPage({ type: "lidl-plus:request", entry_id: null, known_sync: null });
  await tick();
  assert.strictEqual(JSON.stringify(calls[1]), JSON.stringify({ type: "lidl_plus/panel_data" }));
  fromPage({ type: "lidl-plus:request", entry_id: "broken" });
  await tick();
  assert.deepStrictEqual([posted[posted.length - 1].msg.type, posted[posted.length - 1].msg.message], ["lidl-plus:error", "Connection lost"]);

  // Calls of the page: only the commands of the integration (and the zone of the store) are passed on
  fromPage({ type: "lidl-plus:call", id: 7, request: { type: "lidl_plus/leaflet", leaflet_id: "l1", entry_id: "e1" } });
  await tick();
  assert.strictEqual(JSON.stringify(calls.at(-1)), JSON.stringify({ type: "lidl_plus/leaflet", leaflet_id: "l1", entry_id: "e1" }));
  assert.deepStrictEqual([posted.at(-1).msg.type, posted.at(-1).msg.id, posted.at(-1).msg.result.entry_id], ["lidl-plus:result", 7, "e1"]);
  const callCount = calls.length;
  fromPage({ type: "lidl-plus:call", id: 8, request: { type: "config/auth/delete", user_id: "u1" } });
  fromPage({ type: "lidl-plus:call", id: 9 });
  await tick();
  assert.strictEqual(calls.length, callCount, "other commands are not sent to Home Assistant");
  assert.deepStrictEqual(posted.slice(-2).map((p) => [p.msg.id, p.msg.error]), [[8, "Unknown command"], [9, "Unknown command"]]);
  fromPage({ type: "lidl-plus:call", id: 10, request: { type: "lidl_plus/search", query: "x", entry_id: "broken" } });
  await tick();
  assert.deepStrictEqual([posted.at(-1).msg.id, posted.at(-1).msg.error], [10, "Connection lost"]);
  for (const type of ["lidl_plus/articles", "lidl_plus/article", "lidl_plus/article_save", "lidl_plus/article_delete",
    "lidl_plus/barcode", "lidl_plus/article_image", "lidl_plus/article_image_delete", "zone/create"]) {
    fromPage({ type: "lidl-plus:call", id: 20, request: { type } });
    await tick();
    assert.strictEqual(calls.at(-1).type, type);
  }

  // Barcode scanner: without the Home Assistant app the page scans itself
  fromPage({ type: "lidl-plus:scan", id: 30 });
  await tick();
  // Objects of the page window are compared as JSON, they have other prototypes
  const same = (actual, expected) => assert.strictEqual(JSON.stringify(actual), JSON.stringify(expected));
  same([posted.at(-1).msg.id, posted.at(-1).msg.result], [30, { unsupported: true }]);
  // In the app: its scanner, the results are read before the frontend handles them
  const fired = [];
  const handled = [];
  const frontendBus = (msg) => handled.push(msg);
  window.externalBus = frontendBus;
  panel.hass = { ...hass, auth: { external: { config: { hasBarCodeScanner: 1 }, fireMessage: (msg) => fired.push(msg) } } };
  fromPage({ type: "lidl-plus:scan", id: 31 });
  await tick();
  assert.strictEqual(fired[0].type, "bar_code/scan");
  assert.ok(fired[0].payload.title && fired[0].payload.alternative_option_label);
  assert.notStrictEqual(window.externalBus, frontendBus);
  // Other messages of the app are passed on unchanged
  window.externalBus({ id: 1, type: "command", command: "sidebar/show" });
  const result = { id: 2, type: "command", command: "bar_code/scan_result", payload: { rawValue: "4056489123453", format: "ean_13" } };
  window.externalBus(result);
  await tick();
  same(posted.at(-1).msg, { type: "lidl-plus:result", id: 31, result: { code: "4056489123453", format: "ean_13" } });
  assert.deepStrictEqual(fired.map((msg) => msg.type), ["bar_code/scan", "bar_code/close"]);
  assert.deepStrictEqual(handled.map((msg) => msg.command), ["sidebar/show", "bar_code/scan_result"]);
  assert.strictEqual(window.externalBus, frontendBus, "the bus of the frontend is restored");
  // Typing the barcode instead, and messages as JSON text
  fromPage({ type: "lidl-plus:scan", id: 32 });
  await tick();
  window.externalBus(JSON.stringify({ id: 3, type: "command", command: "bar_code/aborted", payload: { reason: "alternative_options" } }));
  await tick();
  same(posted.at(-1).msg.result, { cancelled: true, reason: "alternative_options" });
  assert.strictEqual(window.externalBus, frontendBus);
  // A new scan replaces a running one
  fromPage({ type: "lidl-plus:scan", id: 33 });
  await tick();
  fromPage({ type: "lidl-plus:scan", id: 34 });
  await tick();
  same([posted.at(-1).msg.id, posted.at(-1).msg.result], [33, { cancelled: true, reason: "restarted" }]);
  window.externalBus({ id: 4, type: "command", command: "bar_code/aborted", payload: { reason: "canceled" } });
  await tick();
  same([posted.at(-1).msg.id, posted.at(-1).msg.result], [34, { cancelled: true, reason: "canceled" }]);
  panel.hass = { ...hass };

  // Export: the page has no token, the download gets a signed address and starts in the Home Assistant window
  const downloads = [];
  panel._download = (address) => downloads.push(address);
  fromPage({ type: "lidl-plus:export", id: 11, dataset: "items", format: "csv", entry_id: "e1" });
  await tick();
  assert.strictEqual(JSON.stringify(calls.at(-1)), JSON.stringify({ type: "auth/sign_path", path: "/api/lidl_plus/export?dataset=items&format=csv&entry_id=e1" }));
  assert.deepStrictEqual(downloads, ["/api/lidl_plus/export?dataset=items&format=csv&entry_id=e1&authSig=signed"]);
  assert.deepStrictEqual([posted.at(-1).msg.id, posted.at(-1).msg.result], [11, true]);
  fromPage({ type: "lidl-plus:export", id: 12 });
  await tick();
  assert.strictEqual(calls.at(-1).path, "/api/lidl_plus/export?dataset=all");
  delete panel._download;
  // The real download clicks a hidden link and removes it again
  const clicked = [];
  window.HTMLAnchorElement.prototype.click = function () { clicked.push([this.getAttribute("href"), this.hasAttribute("download")]); };
  fromPage({ type: "lidl-plus:export", id: 13, dataset: "all" });
  await tick();
  assert.deepStrictEqual(clicked, [["/api/lidl_plus/export?dataset=all&authSig=signed", true]]);
  assert.strictEqual(document.querySelectorAll("a").length, 0);

  // Menu rule of the built-in panels: narrow or sidebar always hidden, never in kiosk mode
  const menuStates = () => posted.filter((p) => p.msg.type === "lidl-plus:menu").map((p) => p.msg.visible);
  const before = menuStates().length;
  panel.narrow = true;
  panel.hass = { ...hass, kioskMode: true };
  panel.hass = { ...hass, kioskMode: false, dockedSidebar: "always_hidden" };
  panel.narrow = false;
  panel.hass = { ...hass };
  assert.deepStrictEqual(menuStates().slice(before), [true, false, true, false]);

  let toggled = 0;
  document.addEventListener("hass-toggle-menu", () => toggled++);
  fromPage({ type: "lidl-plus:toggle-menu" });
  assert.strictEqual(toggled, 1);

  // Detached and attached again: the offset property is set again, no second iframe, listeners cleaned up,
  // a running scan of the app ends
  panel.hass = { ...hass, auth: { external: { config: { hasBarCodeScanner: 1 }, fireMessage: () => {} } } };
  fromPage({ type: "lidl-plus:scan", id: 40 });
  await tick();
  assert.notStrictEqual(window.externalBus, frontendBus);
  host.removeChild(panel);
  assert.strictEqual(window.externalBus, frontendBus);
  fromPage({ type: "lidl-plus:toggle-menu" });
  assert.strictEqual(toggled, 1, "no listener while disconnected");
  host.appendChild(panel);
  assert.strictEqual(panel.style.getPropertyValue("--lidl-panel-host-offset"), "34px");
  assert.strictEqual(panel.querySelectorAll("iframe").length, 1);

  // A second definition of the module (other ?v=) does not throw
  window.eval(code);
  console.log("panel.js runtime test passed");
  window.close();
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
