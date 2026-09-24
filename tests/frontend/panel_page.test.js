// Runtime test of frontend/index.html: requests, rendering, accounts, filters, modals, escaping, missing Chart.js,
// receipt details, offers, leaflets, search and export.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "..", "custom_components", "lidl_plus", "frontend", "index.html"), "utf8");
const VERSION = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "..", "custom_components", "lidl_plus", "manifest.json"), "utf8")).version;
const ORIGIN = "http://homeassistant.local:8123";
const json = (value) => JSON.stringify(value);

function openPage({ withChart = true, version = VERSION } = {}) {
  const parentMessages = [];
  const charts = [];
  const configs = [];
  const errors = [];
  const dom = new JSDOM(html, {
    url: `${ORIGIN}/lidl_plus_frontend/index.html?v=${version}`,
    runScripts: "dangerously",
    beforeParse(window) {
      // The vendor scripts are not loaded by jsdom, a stub records the charts
      if (withChart) {
        window.Chart = class {
          constructor(canvas, config) { charts.push(config.type); configs.push(config); }
          destroy() {}
        };
      }
      const parent = { postMessage: (msg, origin) => parentMessages.push({ msg: JSON.parse(json(msg)), origin }) };
      Object.defineProperty(window, "parent", { value: parent });
      window.addEventListener("error", (e) => errors.push(e.error || e.message));
      window.console.error = () => {};
    },
  });
  const { window } = dom;
  const send = (data) =>
    window.dispatchEvent(new window.MessageEvent("message", { data, origin: ORIGIN, source: window.parent }));
  return { window, document: window.document, parentMessages, charts, configs, errors, send };
}

const now = new Date();
const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
const accounts = [{ entry_id: "e1", title: "Lidl Plus (DE)" }, { entry_id: "e2", title: "Lidl <Plus> (AT)" }];
const data = {
  entry_id: "e1",
  accounts,
  receipts: [
    { id: "t3", date: `${thisMonth}-12T18:30:00+02:00`, store: "Lidl <b>Musterstadt</b>", total: 20,
      items: [{ id: "a", name: "Milch <img src=x onerror=alert(1)>", unit_price: "1,29", quantity: 1 }, { id: "c", name: "Kaffee", unit_price: "5,99", quantity: 0.856 }] },
    { id: "t2", date: "2026-05-02T10:00:00+02:00", store: "Lidl Nord", total: 7.5, items: [] },
  ],
  products: [
    { id: "a'\"><x", name: "Milch <img src=x onerror=alert(1)>", purchase_count: 3, total_quantity: 4, total_spent: 5.66, avg_price: 1.42,
      last_price: 1.29, last_date: `${thisMonth}-12T18:30:00+02:00`, last_store: "Lidl",
      price_history: [{ date: "2026-04-10", price: 1.09, store: "A" }, { date: "2026-05-02", price: 1.19, store: "B" }, { date: "2026-05-12", price: 1.29, store: "C" }] },
    { id: "c", name: "Kaffee", purchase_count: 1, total_quantity: 1, total_spent: 5.99, avg_price: 5.99, last_price: 5.99, last_date: "2026-05-12", last_store: "", price_history: [] },
  ],
  last_sync: "2026-05-15T12:00:00+00:00",
  last_error: null,
  spending_by_month: { "2026-04": 12.5, [thisMonth]: 27.5 },
  spending_by_store: { "Lidl Musterstadt": 32.5, "Lidl Nord": 7.5 },
  food_total: 13.14, nonfood_total: 3.95, total_tickets: 120, total_spent: 1234.5, avg_basket: 10.29, current_month: 27.5,
};

// ── Normal page ───────────────────────────────────────────────────────────────
{
  const { window, document, parentMessages, charts, errors, send } = openPage();

  // 1. The page asks for data right away
  assert.strictEqual(json(parentMessages[0]), json({ msg: { type: "lidl-plus:request", entry_id: null, known_sync: null }, origin: ORIGIN }));

  // 2. Messages from other sources are ignored
  window.dispatchEvent(new window.MessageEvent("message", { data: { type: "lidl-plus:error", message: "x" }, origin: "http://evil", source: null }));
  assert.ok(document.getElementById("errorBanner").classList.contains("hidden"));

  // 3. Data message renders everything
  send({ type: "lidl-plus:data", data });
  assert.deepStrictEqual(errors, []);
  const cards = document.getElementById("overviewCards").textContent.replace(/\s+/g, " ");
  assert.ok(cards.includes("120"), "receipt count comes from total_tickets: " + cards);
  assert.ok(cards.includes("1.234,50"), "German currency format and total_spent: " + cards);
  assert.deepStrictEqual(charts, ["bar", "doughnut", "bar"]);
  assert.ok(document.getElementById("syncInfo").textContent.startsWith("Stand: "));

  // 4. Two accounts: the selector is shown and its titles are escaped
  const select = document.getElementById("accountSelect");
  assert.ok(!select.classList.contains("hidden"));
  assert.strictEqual(select.options.length, 2);
  assert.strictEqual(select.options[1].textContent, "Lidl <Plus> (AT)");
  assert.strictEqual(select.value, "e1");

  // 5. Receipts: escaped store, items rendered lazily when opened
  const list = document.getElementById("receiptList");
  assert.strictEqual(list.className, "");
  assert.strictEqual(list.querySelectorAll("img").length, 0);
  assert.ok(list.innerHTML.includes("Lidl &lt;b&gt;Musterstadt&lt;/b&gt;"));
  const header = list.querySelector("[data-receipt-id='t3']");
  assert.strictEqual(header.nextElementSibling.innerHTML, "");
  header.click();
  const table = header.nextElementSibling;
  assert.ok(table.classList.contains("open"));
  assert.strictEqual(table.querySelectorAll("tbody tr").length, 2);
  assert.strictEqual(table.querySelectorAll("img").length, 0, "item names are escaped");
  assert.ok(table.textContent.includes("0,856"), "German quantity format");

  // 6. Filters
  document.getElementById("receiptSearch").value = "kaffee";
  window.filterReceipts();
  assert.strictEqual(list.querySelectorAll("[data-receipt-id]").length, 1);
  document.getElementById("receiptSearch").value = "";
  document.getElementById("receiptStore").value = "Lidl Nord";
  window.filterReceipts();
  assert.strictEqual(list.querySelector("[data-receipt-id]").dataset.receiptId, "t2");
  document.getElementById("receiptStore").value = "";
  window.filterReceipts();

  // 7. Articles: until the article database answers the bought articles are shown; a card with a key that needs
  // escaping opens the dialog right away with the purchases, the details are requested; Escape closes it
  const grid = document.getElementById("productGrid");
  assert.strictEqual(grid.querySelectorAll("img").length, 0);
  const card = [...grid.querySelectorAll("[data-article-key]")].find((el) => el.dataset.articleKey === "nr:a'\"><x");
  card.querySelector("div").click();
  const modal = document.getElementById("productModal");
  assert.ok(modal.classList.contains("open"));
  assert.strictEqual(document.getElementById("modalTitle").textContent, "Milch <img src=x onerror=alert(1)>");
  assert.ok(document.getElementById("modalStats").textContent.includes("3×"));
  assert.strictEqual(json(parentMessages.at(-1).msg.request), json({ type: "lidl_plus/article", key: "nr:a'\"><x", entry_id: "e1" }));
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));
  assert.ok(!modal.classList.contains("open"));

  // 8. Next request sends the rendered sync; "unchanged" keeps opened receipts and updates the warning
  window.requestData();
  assert.strictEqual(json(parentMessages[parentMessages.length - 1].msg), json({ type: "lidl-plus:request", entry_id: "e1", known_sync: data.last_sync }));
  list.querySelector("[data-receipt-id='t3']").click();
  send({ type: "lidl-plus:data", data: { entry_id: "e1", accounts, last_sync: data.last_sync, last_error: "Timeout", unchanged: true } });
  assert.ok(list.querySelector("[data-receipt-id='t3']").nextElementSibling.classList.contains("open"));
  const warning = document.getElementById("syncWarning");
  assert.ok(!warning.classList.contains("hidden") && warning.textContent.includes("Timeout"));

  // 9. Switching the account requests all data of it, a late answer for the old account is ignored
  select.value = "e2";
  select.dispatchEvent(new window.Event("change"));
  assert.strictEqual(json(parentMessages[parentMessages.length - 1].msg), json({ type: "lidl-plus:request", entry_id: "e2", known_sync: null }));
  send({ type: "lidl-plus:data", data: { ...data, receipts: [] } });
  assert.strictEqual(list.querySelectorAll("[data-receipt-id]").length, 2, "answer for e1 ignored");
  send({ type: "lidl-plus:data", data: { ...data, entry_id: "e2", receipts: [], last_sync: "2026-05-15T13:00:00+00:00" } });
  assert.strictEqual(list.textContent.trim(), "Keine Kassenbons gefunden.");
  assert.ok(warning.classList.contains("hidden"));

  // 10. An error keeps the data and shows the banner
  send({ type: "lidl-plus:error", message: "Verbindung verloren" });
  assert.ok(document.getElementById("errorBanner").textContent.includes("Verbindung verloren"));
  assert.ok(document.getElementById("productGrid").querySelectorAll("[data-article-key]").length > 0);

  // 11. Menu button
  send({ type: "lidl-plus:menu", visible: true });
  const menu = document.getElementById("menuBtn");
  assert.ok(!menu.classList.contains("hidden"));
  menu.click();
  assert.strictEqual(json(parentMessages[parentMessages.length - 1].msg), json({ type: "lidl-plus:toggle-menu" }));
  send({ type: "lidl-plus:menu", visible: false });
  assert.ok(menu.classList.contains("hidden"));

  assert.deepStrictEqual(errors, []);
  window.close(); // stops the refresh interval of the page
}

// ── After an update: the version of the page is the one of the integration ───
assert.ok(html.includes(`const PAGE_VERSION = '${VERSION}';`), "PAGE_VERSION of index.html is the version of manifest.json");
{
  // Home Assistant still has the panel element of an older version loaded
  const { window, document, parentMessages } = openPage({ version: "1.2.0" });
  const banner = document.getElementById("reloadBanner");
  assert.ok(!banner.classList.contains("hidden"));
  assert.ok(banner.textContent.includes("Strg+F5"));
  // The old element knows no scanner: the page does not wait for it
  const before = parentMessages.length;
  window.openScanner();
  assert.strictEqual(parentMessages.length, before);
  assert.ok(document.getElementById("scanHint").textContent.includes("einmal neu geladen"));
  window.close();
}
{
  // The same version: no hint, until a command is unknown
  const { window, document, parentMessages, send } = openPage();
  const banner = document.getElementById("reloadBanner");
  assert.ok(banner.classList.contains("hidden"));
  send({ type: "lidl-plus:data", data });
  window.searchAll();
  document.getElementById("globalSearch").value = "kaffee";
  window.searchAll();
  const call = parentMessages[parentMessages.length - 1].msg;
  send({ type: "lidl-plus:result", id: call.id, error: "Unknown command" });
  setTimeout(() => {
    assert.ok(!banner.classList.contains("hidden"), "an unknown command shows the hint");
    window.close();
  }, 0);
}

// ── Chart.js missing: receipts and products are still shown ──────────────────
{
  const { window, document, parentMessages, send } = openPage({ withChart: false });
  send({ type: "lidl-plus:data", data: { ...data, accounts: accounts.slice(0, 1) } });
  assert.ok(document.getElementById("accountSelect").classList.contains("hidden"), "one account, no selector");
  assert.strictEqual(document.getElementById("receiptList").querySelectorAll("[data-receipt-id]").length, 2);
  assert.ok(document.getElementById("productGrid").querySelectorAll("[data-article-key]").length === 2);
  const banner = document.getElementById("errorBanner");
  assert.ok(!banner.classList.contains("hidden") && banner.textContent.includes("Übersicht"), banner.textContent);
  // The next request loads everything again instead of sending known_sync
  window.requestData();
  assert.strictEqual(parentMessages[parentMessages.length - 1].msg.known_sync, null);
  window.close();
}

// ── Receipt details, offers, leaflets, search and export ─────────────────────
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
// Also turns the non-breaking space of the currency format into a space
const plain = (element) => element.textContent.replace(/\s+/g, " ");
const details = {
  ...data,
  accounts: accounts.slice(0, 1),
  receipts: [
    {
      id: "t4", date: "2026-05-14T09:00:00+02:00", store: "Lidl Musterstadt", total: 7.7, savings: 0.3, points: 12,
      items: [
        { id: "f", name: "Feldsalat", unit_price: "1,49", quantity: 1, total: 1.49, discount: -0.3,
          discounts: [{ text: "Lidl Plus <b>Rabatt</b>", amount: -0.3 }] },
        { id: "z", name: "Zucchini", unit_price: "1,29", quantity: 0.786, unit: "kg", total: 1.01, discount: 0, discounts: [] },
        { id: "p", name: "Pfand 0,25 EM", unit_price: "0,25", quantity: 6, total: 1.5, is_deposit: true, discounts: [] },
      ],
      deposit_returns: [{ amount: -3.75, tax_type: "B", count: 15, unit_amount: 0.25 }],
      payments: [{ method: "Kreditkarte <i>", amount: 7.7 }],
    },
    // Only returned bottles: the total is negative
    { id: "t5", date: "2026-05-13T09:00:00+02:00", store: "Lidl Musterstadt", total: -0.61, items: [] },
  ],
  products: [...data.products, { id: "k", name: "Kaffee Crema", purchase_count: 2, total_quantity: 2, total_spent: 9.98,
    avg_price: 4.99, last_price: 4.99, last_date: "2026-05-12", last_store: "", savings: 1.2, price_history: [] }],
  savings_total: 12.34,
  savings_by_month: { [thisMonth]: 1.5 },
  stores: [{ id: "DE1234", name: "Lidl Musterstadt", visits: 2 }],
  offer_stores: ["DE1234", "DE9999"],
  offers: [
    { id: "o1", title: "Kaffee <img src=x onerror=alert(1)>", brand: "BELLAROM", image: "javascript:alert(1)", price: 4.44,
      regular_price: 5.55, price_text: "4.44", discount: "-20%", packaging: "Je 500 g", price_per_unit: "1 kg = 8.88",
      status: "current", start: "2026-05-10T22:00:00Z", end: "2026-05-16T21:59:59Z", bought_products: [{ id: "k", name: "Kaffee Crema" }] },
    { id: "o2", title: "Butter", brand: "", image: "https://example.invalid/butter.jpg", price: null, regular_price: null,
      price_text: "-30%", discount: "", status: "upcoming", start: "2026-05-17T22:00:00Z", end: "2026-05-23T21:59:59Z", bought_products: [] },
  ],
  leaflets: [
    { id: "l1", identifier: "aktion-1", name: "Aktionsprospekt", title: "11.05. – 16.05.", category: "Filial-Angebote",
      status: "current", start: "2026-05-11", end: "2026-05-16", pdf: "https://example.invalid/l1.pdf", url: "javascript:alert(1)",
      thumbnail: "https://example.invalid/l1.jpg", page_count: 2, product_count: 1 },
    { id: "l9", name: "Deine Lidl Plus Vorteile", title: "", category: "Filial-Angebote", status: "current",
      start: "2026-08-28", end: "2050-08-28", pdf: "", url: "", thumbnail: "", page_count: 4, product_count: 0 },
  ],
  version: "2026-05-15T12:00:01+00:00",
  leaflet_region: { region: 10, name: "Grevenbroich <b>", store: "DE1234" },
  busy_times: {
    store: { id: "DE1234", name: "Musterstadt <b>", address: "Hauptstraße 1", postal_code: "12345", locality: "Musterstadt" },
    opening_hours: {
      monday: [["07:00", "22:00"]], tuesday: [["07:00", "22:00"]], wednesday: [["07:00", "22:00"]],
      thursday: [["07:00", "22:00"]], friday: [["07:00", "22:00"]], saturday: [["07:00", "22:00"]], sunday: [], special: {},
    },
    // Busyness = hour of the day, so every value is easy to check
    forecast: { venue_name: "Lidl", updated: "2026-05-01T10:00:00+00:00", hours: Array.from({ length: 7 }, () => Array.from({ length: 24 }, (_, hour) => hour * 4)) },
    forecast_error: null,
    own: { receipts: 3, hours: Array.from({ length: 7 }, (_, day) => Array.from({ length: 24 }, (_, hour) => (day === 5 && hour === 17 ? 2 : day === 0 && hour === 9 ? 1 : 0))) },
  },
};

(async () => {
  const { window, document, parentMessages, charts, configs, errors, send } = openPage();
  send({ type: "lidl-plus:data", data: details });
  assert.deepStrictEqual(errors, []);
  const lastMessage = () => parentMessages[parentMessages.length - 1].msg;
  const cards = document.getElementById("overviewCards").textContent.replace(/\s+/g, " ");
  assert.ok(cards.includes("12,34") && cards.includes("Gespart"), cards);

  // Empty amount filters show receipts with a negative total as well
  assert.strictEqual(document.getElementById("receiptList").querySelectorAll("[data-receipt-id]").length, 2);
  document.getElementById("receiptMin").value = "0";
  window.filterReceipts();
  assert.strictEqual(document.getElementById("receiptList").querySelectorAll("[data-receipt-id]").length, 1);
  document.getElementById("receiptMin").value = "";
  window.filterReceipts();

  // Receipt with discounts, weight, deposit, deposit return and payment
  const header = document.querySelector("[data-receipt-id='t4']");
  assert.ok(plain(header).includes("0,30 € gespart"), plain(header));
  header.click();
  const receipt = header.nextElementSibling;
  const text = plain(receipt);
  for (const expected of ["↳ Lidl Plus <b>Rabatt</b>", "-0,30 €", "0,786 kg", "1,29 €/kg", "Pfand", "Pfandrückgabe (15 × 0,25 €)",
    "-3,75 €", "Bezahlt mit Kreditkarte <i>", "Gespart mit Rabatten", "Lidl Plus Punkte", "Summe", "7,70 €"]) {
    assert.ok(text.includes(expected), `${expected} missing in: ${text}`);
  }
  assert.strictEqual(receipt.querySelectorAll("b, i").length, 0, "texts of the receipt are escaped");

  // Offers: escaped, only web addresses as images, filters
  const offerGrid = document.getElementById("offerGrid");
  assert.strictEqual(offerGrid.querySelectorAll("[data-offer-id]").length, 2);
  assert.deepStrictEqual([...offerGrid.querySelectorAll("img")].map((img) => img.getAttribute("src")), ["https://example.invalid/butter.jpg"]);
  assert.strictEqual(offerGrid.querySelector("img").getAttribute("referrerpolicy"), "no-referrer");
  assert.ok(plain(offerGrid).includes("Kaffee <img src=x onerror=alert(1)>"));
  assert.ok(offerGrid.textContent.includes("Schon gekauft: Kaffee Crema"));
  assert.ok(offerGrid.textContent.includes("-30%"), "offers without a fixed price show their text");
  assert.ok(document.getElementById("offerStores").textContent.includes("Lidl Musterstadt (DE1234), DE9999"));
  document.getElementById("offerBought").checked = true;
  window.filterOffers();
  assert.deepStrictEqual([...offerGrid.querySelectorAll("[data-offer-id]")].map((el) => el.dataset.offerId), ["o1"]);
  document.getElementById("offerBought").checked = false;
  document.getElementById("offerStatus").value = "upcoming";
  window.filterOffers();
  assert.deepStrictEqual([...offerGrid.querySelectorAll("[data-offer-id]")].map((el) => el.dataset.offerId), ["o2"]);
  document.getElementById("offerStatus").value = "";
  document.getElementById("offerSearch").value = "bellarom";
  window.filterOffers();
  assert.strictEqual(document.getElementById("offerCount").textContent, "1 von 2 Angeboten");

  // Leaflets: the javascript: address is no link
  const leafletGrid = document.getElementById("leafletGrid");
  const links = [...leafletGrid.querySelectorAll("a")].map((a) => [a.textContent, a.getAttribute("href"), a.getAttribute("rel")]);
  assert.deepStrictEqual(links, [["PDF", "https://example.invalid/l1.pdf", "noopener noreferrer"]]);
  assert.ok(leafletGrid.textContent.includes("2 Seiten · 1 Produkte"));
  // The offer region of the leaflets, escaped
  const regionInfo = document.getElementById("leafletRegion");
  assert.ok(regionInfo.textContent.startsWith("Prospekte der Angebotsregion Grevenbroich <b> (Filiale DE1234)"), regionInfo.textContent);
  assert.strictEqual(regionInfo.querySelectorAll("b").length, 0);
  // Dates of other years show the year
  assert.ok(plain(leafletGrid).includes("28.08.2050"), plain(leafletGrid));

  // The next request sends the version of the data shown
  window.requestData();
  assert.strictEqual(lastMessage().known_sync, "2026-05-15T12:00:01+00:00");

  // Opening a leaflet loads its pages and products through the panel element
  leafletGrid.querySelector("button[data-leaflet-id='l1']").click();
  const modal = document.getElementById("leafletModal");
  assert.ok(modal.classList.contains("open"));
  let call = lastMessage();
  assert.strictEqual(json(call), json({ type: "lidl-plus:call", request: { type: "lidl_plus/leaflet", leaflet_id: "l1", entry_id: "e1" }, id: call.id }));
  send({ type: "lidl-plus:result", id: call.id, result: { ...details.leaflets[0],
    pages: [{ number: 1, image: "https://example.invalid/p1.jpg", thumbnail: "https://example.invalid/p1s.jpg", text: "Kaffee", description: "<b>Seite</b>" }],
    products: [{ id: "100001", title: "Akku <script>x</script>", brand: "PARKSIDE", price: 39.99, image: "https://example.invalid/a.jpg",
      url: "https://www.lidl.de/p/100001", pages: [2] }] } });
  await tick();
  const body = document.getElementById("leafletBody");
  assert.ok(plain(body).includes("Akku <script>x</script>") && plain(body).includes("39,99 €"), plain(body));
  assert.strictEqual(body.querySelectorAll("script, b").length, 0);
  assert.deepStrictEqual([...body.querySelectorAll("img")].map((img) => img.getAttribute("src")),
    ["https://example.invalid/a.jpg", "https://example.invalid/p1s.jpg"]);
  assert.ok(body.textContent.includes("S. 2") && body.textContent.includes("Seite 1"));

  // An error is shown in the dialog, a late answer after closing is ignored
  leafletGrid.querySelector("button[data-leaflet-id='l1']").click();
  send({ type: "lidl-plus:result", id: lastMessage().id, error: "Leaflet not found" });
  await tick();
  assert.ok(body.textContent.includes("Prospekt konnte nicht geladen werden: Leaflet not found"));
  leafletGrid.querySelector("button[data-leaflet-id='l1']").click();
  call = lastMessage();
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));
  assert.ok(!modal.classList.contains("open"));
  send({ type: "lidl-plus:result", id: call.id, result: { pages: [], products: [] } });
  await tick();
  assert.ok(body.textContent.includes("Lade Prospekt"));

  // Search in leaflets, offers and the own purchases
  document.getElementById("globalSearch").value = " kaffee ";
  document.getElementById("searchForm").dispatchEvent(new window.Event("submit", { cancelable: true }));
  call = lastMessage();
  assert.strictEqual(json(call.request), json({ type: "lidl_plus/search", query: "kaffee", entry_id: "e1" }));
  send({ type: "lidl-plus:result", id: call.id, result: {
    products: [{ id: "k", name: "Kaffee Crema", purchase_count: 2, last_date: "2026-05-12", last_price: 4.99 }],
    offers: [details.offers[0]],
    leaflets: [{ leaflet: { id: "l1", name: "Aktionsprospekt", title: "11.05. – 16.05.", status: "current", end: "2026-05-16" },
      pages: [{ number: 1, image: "https://example.invalid/p1.jpg", thumbnail: "", description: "Kaffee" }], products: [] }],
  } });
  await tick();
  const results = document.getElementById("searchResults");
  assert.ok(!results.classList.contains("hidden"));
  const found = plain(results);
  for (const expected of ["In Prospekten", "Angebote deiner Filialen", "In deinen Einkäufen", "Kaffee Crema", "4,44 €", "Seite 1"]) {
    assert.ok(found.includes(expected), `${expected} missing in: ${found}`);
  }
  // The short lists first, the leaflets with their images last
  assert.ok(found.indexOf("Angebote deiner Filialen") < found.indexOf("In deinen Einkäufen"));
  assert.ok(found.indexOf("In deinen Einkäufen") < found.indexOf("In Prospekten"));
  assert.strictEqual(results.querySelectorAll("img[src='https://example.invalid/p1.jpg']").length, 1, "the page without thumbnail shows the image");
  results.querySelector("[data-article-key='nr:k']").click();
  assert.ok(document.getElementById("productModal").classList.contains("open"));
  assert.ok(document.getElementById("modalSavings").textContent.includes("1,20"));
  window.closeProductModal();
  results.querySelector("button[data-leaflet-id='l1']").click();
  assert.strictEqual(lastMessage().request.type, "lidl_plus/leaflet");
  window.closeLeafletModal();

  document.getElementById("globalSearch").value = "gibtsnicht";
  window.searchAll();
  send({ type: "lidl-plus:result", id: lastMessage().id, result: { products: [], offers: [], leaflets: [] } });
  await tick();
  assert.ok(results.textContent.includes("Nichts gefunden für „gibtsnicht“"));
  document.getElementById("globalSearch").value = "";
  window.searchAll();
  assert.ok(results.classList.contains("hidden"));

  // Export menu: the panel element downloads the file, errors are shown
  const menu = document.getElementById("exportMenu");
  document.getElementById("exportBtn").click();
  assert.ok(!menu.classList.contains("hidden"));
  menu.querySelector("[data-dataset='items']").click();
  assert.ok(menu.classList.contains("hidden"));
  call = lastMessage();
  assert.strictEqual(json(call), json({ type: "lidl-plus:export", dataset: "items", format: "csv", entry_id: "e1", id: call.id }));
  send({ type: "lidl-plus:result", id: call.id, error: "Unauthorized" });
  await tick();
  assert.ok(document.getElementById("errorBanner").textContent.includes("Export fehlgeschlagen: Unauthorized"));
  document.getElementById("exportBtn").click();
  document.body.click();
  assert.ok(menu.classList.contains("hidden"), "a click elsewhere closes the menu");
  menu.querySelector("[data-dataset='all']").click();
  assert.strictEqual(json(lastMessage()), json({ type: "lidl-plus:export", dataset: "all", entry_id: "e1", id: lastMessage().id }));

  // Busy hours: drawn when the tab is shown
  const chartCount = charts.length;
  const busyTab = [...document.querySelectorAll(".tab-btn")].find((button) => button.textContent.includes("Stoßzeiten"));
  busyTab.click();
  assert.strictEqual(charts.length, chartCount + 1);
  assert.strictEqual(document.getElementById("busyStore").textContent, "Lidl Musterstadt <b>");
  assert.strictEqual(document.querySelectorAll("#tab-busy b").length, 0);
  const dayButtons = document.querySelectorAll("#busyDays [data-busy-day]");
  assert.strictEqual(dayButtons.length, 7);
  const now = new Date();
  const today = (now.getDay() + 6) % 7;
  assert.ok(dayButtons[today].classList.contains("btn-primary"));
  // Today: the current hour is red, if the store is open now
  const openNow = today < 6 && now.getHours() >= 7 && now.getHours() < 22;
  let config = configs[configs.length - 1];
  if (today < 6) {
    assert.deepStrictEqual([config.data.labels[0], config.data.labels[config.data.labels.length - 1]], ["7 Uhr", "21 Uhr"]);
    const red = config.data.datasets[0].backgroundColor.filter((color) => color === "#e35d4f").length;
    assert.strictEqual(red, openNow ? 1 : 0);
  }
  const badge = document.getElementById("busyNow");
  assert.strictEqual(badge.textContent, openNow ? `Jetzt: ${now.getHours() * 4} % – ${now.getHours() * 4 < 50 ? "mäßig besucht" : now.getHours() * 4 < 75 ? "ziemlich voll" : "sehr voll"}` : "Jetzt geschlossen");

  // Monday: opening hours, forecast and own purchases
  dayButtons[0].click();
  config = configs[configs.length - 1];
  assert.ok(plain(document.getElementById("busyOpening")).endsWith(`${today === 0 ? "heute" : "Mo"}: 07:00–22:00 Uhr`), plain(document.getElementById("busyOpening")));
  assert.ok(plain(document.getElementById("busyOpening")).startsWith("Hauptstraße 1, 12345 Musterstadt"));
  assert.strictEqual(json(config.data.datasets.map((dataset) => dataset.label)), json(["Erwartete Auslastung (%)", "Deine Einkäufe"]));
  assert.strictEqual(config.data.datasets[0].data[0], 28, "7 o'clock");
  assert.strictEqual(config.data.datasets[1].data[2], 1, "a purchase at 9 o'clock");
  const summary = plain(document.getElementById("busySummary"));
  assert.ok(summary.includes("Am ruhigsten um 7 Uhr (28 %), am vollsten um 21 Uhr (84 %)."), summary);
  assert.ok(summary.includes("Du kaufst hier meist samstags um 17 Uhr ein (2 von 3 Kassenbons)."), summary);
  assert.ok(document.getElementById("busySource").textContent.startsWith("Stoßzeiten: Prognose von BestTime.app (Stand "));
  // Sunday: closed
  document.querySelectorAll("#busyDays [data-busy-day]")[6].click();
  assert.ok(!document.getElementById("busyClosed").classList.contains("hidden"));
  assert.strictEqual(document.getElementById("busyClosed").textContent, `${today === 6 ? "Heute" : "So"} geschlossen`);
  assert.strictEqual(document.getElementById("busyChartWrapper").style.display, "none");

  // Without forecast the own purchases are shown, with the reason of a failed forecast
  send({ type: "lidl-plus:data", data: { ...details, version: "v3", busy_times: { ...details.busy_times, forecast: null, forecast_error: "Invalid API key" } } });
  document.querySelectorAll("#busyDays [data-busy-day]")[5].click();
  config = configs[configs.length - 1];
  assert.strictEqual(json(config.data.datasets.map((dataset) => dataset.label)), json(["Deine Einkäufe"]));
  assert.strictEqual(document.getElementById("busySource").textContent, "BestTime.app: Invalid API key");
  assert.ok(document.getElementById("busyNow").classList.contains("hidden") || document.getElementById("busyNow").textContent === "Jetzt geschlossen");
  // Without a store
  send({ type: "lidl-plus:data", data: { ...details, version: "v4", busy_times: null } });
  assert.ok(document.getElementById("busyOpening").textContent.startsWith("Keine Filiale bekannt"));
  assert.strictEqual(document.getElementById("busyChartWrapper").style.display, "none");

  assert.deepStrictEqual(errors, []);
  window.close();
  console.log("index.html runtime test passed");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
