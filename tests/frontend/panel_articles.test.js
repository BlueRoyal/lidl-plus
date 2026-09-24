// Runtime test of the article database in frontend/index.html: list, dialog, form, photos, barcode scanner,
// Open Food Facts, offers of the leaflets, shopping duration of the receipts and the zone of the store.
const fs = require("fs");
const path = require("path");
const assert = require("assert");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "..", "..", "custom_components", "lidl_plus", "frontend", "index.html"), "utf8");
const ORIGIN = "http://homeassistant.local:8123";
const json = (value) => JSON.stringify(value);
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const plain = (element) => element.textContent.replace(/\s+/g, " ");

function openPage() {
  const parentMessages = [];
  const errors = [];
  const dom = new JSDOM(html, {
    url: `${ORIGIN}/lidl_plus_frontend/index.html?v=1.3.0`,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(window) {
      window.Chart = class {
        constructor() {}
        destroy() {}
      };
      const parent = { postMessage: (msg, origin) => parentMessages.push({ msg: JSON.parse(json(msg)), origin }) };
      Object.defineProperty(window, "parent", { value: parent });
      window.addEventListener("error", (e) => errors.push(e.error || e.message));
      window.console.error = () => {};
    },
  });
  const { window } = dom;
  // A copy like postMessage makes, the page may change the objects it gets
  const send = (data) =>
    window.dispatchEvent(new window.MessageEvent("message", { data: JSON.parse(json(data)), origin: ORIGIN, source: window.parent }));
  const last = () => parentMessages[parentMessages.length - 1].msg;
  // Answers the last call of the page
  const answer = async (result, error) => {
    const call = last();
    send(error ? { type: "lidl-plus:result", id: call.id, error } : { type: "lidl-plus:result", id: call.id, result });
    await tick();
    await tick();
  };
  return { window, document: window.document, parentMessages, errors, send, last, answer };
}

const IMAGE_A = "a".repeat(32);
const IMAGE_B = "b".repeat(32);
const receiptTime = "2026-05-12T18:30:00+02:00";
const arrived = "2026-05-12T16:12:00+00:00";
const left = "2026-05-12T16:30:00+00:00";
const clock = (value) => new Date(value).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const data = {
  entry_id: "e1",
  accounts: [{ entry_id: "e1", title: "Lidl Plus (DE)" }],
  receipts: [
    { id: "t1", date: receiptTime, store: "Lidl Musterstadt", total: 5.66,
      items: [{ id: "0001", name: "Skyr", unit_price: "1,29", quantity: 1, total: 1.29, discounts: [] },
        { id: "0006151", name: "Pfand", unit_price: "0,25", quantity: 1, total: 0.25, is_deposit: true, discounts: [] }],
      visit: { status: "ok", arrived, left, minutes: 18, checkout_minutes: 14 } },
    { id: "t0", date: "2026-05-02T10:00:00+02:00", store: "Lidl Nord", total: 7.5, items: [], visit: { status: "not_seen" } },
  ],
  products: [
    { id: "0001", name: "Skyr", purchase_count: 2, total_quantity: 2, total_spent: 2.58, avg_price: 1.29, last_price: 1.29,
      last_date: receiptTime, last_store: "Lidl Musterstadt", price_history: [] },
  ],
  offers: [],
  leaflets: [
    { id: "l1", name: "Aktionsprospekt", title: "11.05. – 16.05.", category: "Filial-Angebote", status: "current",
      start: "2026-05-11", end: "2026-05-16", pdf: "", url: "", thumbnail: "", page_count: 2, product_count: 1 },
  ],
  last_sync: "2026-05-15T12:00:00+00:00",
  version: "v1",
  shopping_duration: {
    visits: 2, average_minutes: 18.5, average_checkout_minutes: 14, shortest_minutes: 12, longest_minutes: 25,
    by_weekday: [12, null, 25, null, null, null, null],
    last: { receipt_id: "t1", date: receiptTime, store: "Lidl Musterstadt <b>", arrived, left, minutes: 18, checkout_minutes: 14 },
    entities: ["person.anna"],
    store: { id: "DE1234", name: "Musterstadt", latitude: 50.1, longitude: 8.2, zones: [] },
  },
};
const listEntry = (key, fields = {}) => ({
  key, name: key, brand: "", image: "", sources: [], purchase_count: 0, last_price: null, last_bought: "", last_seen: "2026-05-12",
  unit: "", price_trend: "stable", barcodes: [], has_nutrition: false, has_details: false, image_count: 0, offer: null,
  leaflet: null, receipt_names: [], ...fields,
});
const article = (key, fields = {}) => ({
  ...listEntry(key), lidl_name: key, lidl_brand: "", article_numbers: [], packaging: "", price_per_unit: "", category: "",
  description: "", url: "", package_size: "", nutrition: {}, nutrition_basis: "100g", ingredients: "", notes: "",
  images: [], offers: [], leaflets: [], sources_of_details: {}, product: null, ...fields,
});
const product = {
  source: "Open Food Facts", url: "https://world.openfoodfacts.org/product/4056489123453", code: "4056489123453",
  name: "Skyr Natur", brand: "Milbona", quantity: "500 g", nutrition: { energy_kj: 263, energy_kcal: 63, protein: 11 },
  nutrition_basis: "100g", ingredients: "Magermilch, Milchsäurebakterien", allergens: ["milk"], nutriscore: "a",
  image: "https://images.openfoodfacts.org/skyr.jpg",
};

(async () => {
  const { window, document, parentMessages, errors, send, last, answer } = openPage();
  send({ type: "lidl-plus:data", data });
  assert.deepStrictEqual(errors, []);

  // ── Receipts: time in the store, articles open the article database ─────────
  const list = document.getElementById("receiptList");
  const header = list.querySelector("[data-receipt-id='t1']");
  assert.ok(plain(header).includes("🕒 18 Min."), plain(header));
  assert.ok(!plain(list.querySelector("[data-receipt-id='t0']")).includes("Min."));
  header.click();
  const receipt = plain(header.nextElementSibling);
  assert.ok(receipt.includes(`Im Markt: ${clock(arrived)}–${clock(left)} Uhr (18 Min., bis zur Kasse 14 Min.)`), receipt);
  // Deposits are no articles
  assert.strictEqual(header.nextElementSibling.querySelectorAll("tr[data-article-key]").length, 1);
  list.querySelector("[data-receipt-id='t0']").click();
  assert.ok(plain(list.querySelector("[data-receipt-id='t0']").nextElementSibling).includes("keinen Standort am Markt"));
  header.nextElementSibling.querySelector("tr[data-article-key='nr:0001'] td").click();
  const modal = document.getElementById("productModal");
  assert.ok(modal.classList.contains("open"));
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article", key: "nr:0001", entry_id: "e1" }));
  // Bought articles are shown with their purchases right away
  assert.ok(!document.getElementById("articlePurchases").classList.contains("hidden"));
  assert.ok(plain(document.getElementById("articleDetails")).includes("Lade Details"));
  window.closeProductModal();

  // ── The list of the article database ────────────────────────────────────────
  const grid = document.getElementById("productGrid");
  assert.strictEqual(grid.querySelectorAll("[data-article-key]").length, 1, "the bought articles until the database answers");
  const tab = [...document.querySelectorAll(".tab-btn")].find((button) => button.textContent.includes("Artikel"));
  tab.click();
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/articles", query: "", kind: "", sort: "count-desc", trend: "",
    bought_since: "", offset: 0, limit: 60, entry_id: "e1" }));
  await answer({
    total: 3, offset: 0, statistics: { articles: 927, bought: 413, with_details: 5, with_nutrition: 3 },
    articles: [
      listEntry("web:100001", { name: "Akku <b>Bohrer</b>", brand: "PARKSIDE", image: "https://example.invalid/a.jpg",
        sources: ["leaflets"], leaflet: { id: "l1", price: 39.99, status: "current" } }),
      listEntry("own:1", { name: "Duschgel", sources: ["own"], has_nutrition: true, image_count: 2, barcodes: ["4056489123453"],
        image: "javascript:alert(1)" }),
    ],
  });
  assert.strictEqual(grid.querySelectorAll("[data-article-key]").length, 2);
  assert.strictEqual(grid.querySelectorAll("b").length, 0, "names are escaped");
  assert.deepStrictEqual([...grid.querySelectorAll("img")].map((img) => img.getAttribute("src")), ["https://example.invalid/a.jpg"]);
  const cards = plain(grid);
  for (const expected of ["Akku <b>Bohrer</b>", "39,99 €", "📰 Prospekt", "✏️ Eigener Artikel", "🥗 Nährwerte", "📷 2", "Barcode"]) {
    assert.ok(cards.includes(expected), `${expected} missing in: ${cards}`);
  }
  assert.ok(plain(document.getElementById("productStats")).includes("927"));
  assert.strictEqual(document.getElementById("productCount").textContent, "2 von 3 Artikeln");
  const more = document.getElementById("productMore");
  assert.ok(!more.classList.contains("hidden"));
  more.click();
  assert.strictEqual(last().request.offset, 2);
  await answer({ total: 3, offset: 2, statistics: { articles: 927 }, articles: [listEntry("nr:0001", { name: "Skyr", purchase_count: 2, last_price: 1.29 })] });
  assert.strictEqual(grid.querySelectorAll("[data-article-key]").length, 3);
  assert.ok(more.classList.contains("hidden"));
  // Search and filters are sent to the database
  document.getElementById("productSearch").value = " duschgel ";
  document.getElementById("productKind").value = "nutrition";
  document.getElementById("productPeriod").value = "30";
  window.loadArticles();
  const search = last().request;
  assert.deepStrictEqual([search.query, search.kind, search.offset], ["duschgel", "nutrition", 0]);
  assert.match(search.bought_since, /^\d{4}-\d{2}-\d{2}$/);
  await answer({ total: 0, statistics: { articles: 927 }, articles: [] });
  assert.strictEqual(grid.textContent, "Keine Artikel gefunden.");
  document.getElementById("productSearch").value = "";
  document.getElementById("productKind").value = "";
  document.getElementById("productPeriod").value = "";
  window.loadArticles();
  await answer({ total: 2, statistics: { articles: 927 }, articles: [listEntry("own:1", { name: "Duschgel", sources: ["own"] }),
    listEntry("nr:0001", { name: "Skyr", purchase_count: 2 })] });

  // ── The dialog of an article added by hand ──────────────────────────────────
  grid.querySelector("[data-article-key='own:1']").click();
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article", key: "own:1", entry_id: "e1" }));
  await answer(article("own:1", {
    name: "Duschgel <b>", brand: "Cien", sources: ["own"], barcodes: ["4056489123453"], package_size: "300 ml",
    nutrition: { energy_kcal: 12.5 }, nutrition_basis: "100ml", ingredients: "Aqua, <i>Parfum</i>", has_details: true,
    has_nutrition: true, sources_of_details: { ingredients: "Open Food Facts" },
    images: [
      { id: IMAGE_A, kind: "ingredients", url: `/api/lidl_plus/images/${IMAGE_A}?authSig=abc.def-ghi_j` },
      { id: IMAGE_B, kind: "front", url: "javascript:alert(1)" },
    ],
  }));
  assert.strictEqual(document.getElementById("modalTitle").textContent, "Duschgel <b>");
  assert.ok(document.getElementById("articlePurchases").classList.contains("hidden"), "never bought");
  const form = document.getElementById("articleForm");
  assert.strictEqual(form.elements.name.value, "Duschgel <b>");
  assert.strictEqual(form.elements.n_energy_kcal.value, "12,5");
  assert.strictEqual(form.elements.nutrition_basis.value, "100ml");
  assert.strictEqual(form.elements.ingredients.value, "Aqua, <i>Parfum</i>");
  const details = document.getElementById("articleDetails");
  assert.ok(plain(details).includes("Quelle: Open Food Facts"));
  assert.strictEqual(details.querySelectorAll("i, b").length, 0);
  // Only the photos of the integration are shown
  assert.deepStrictEqual([...details.querySelectorAll("img")].map((img) => img.getAttribute("src")),
    [`/api/lidl_plus/images/${IMAGE_A}?authSig=abc.def-ghi_j`]);
  assert.ok(plain(document.getElementById("barcodeChips")).includes("4056489123453"));

  // Invalid numbers are not sent
  form.elements.n_fat.value = "3,5";
  form.elements.n_salt.value = "abc";
  const calls = parentMessages.length;
  form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  await tick();
  assert.strictEqual(parentMessages.length, calls);
  assert.ok(document.getElementById("articleFormStatus").textContent.includes("Salz (g): bitte eine Zahl"));
  form.elements.n_salt.value = "0.1";
  // Barcodes: Enter adds one, the old one is removed
  const barcode = document.getElementById("barcodeInput");
  barcode.value = "4012345 678901";
  barcode.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true }));
  assert.strictEqual(parentMessages.length, calls, "Enter in the barcode field does not save");
  document.querySelector("[data-remove-barcode='4056489123453']").click();
  assert.strictEqual(plain(document.getElementById("barcodeChips")).trim().replace(" ✕", ""), "4012345678901");
  barcode.value = "12";
  window.addBarcodeFromInput();
  assert.ok(document.getElementById("articleFormStatus").textContent.includes("8 bis 14 Ziffern"));
  barcode.value = "";
  form.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.strictEqual(json(last().request), json({
    type: "lidl_plus/article_save", name: "Duschgel <b>", brand: "Cien", package_size: "300 ml",
    ingredients: "Aqua, <i>Parfum</i>", notes: "",
    nutrition: { energy_kj: null, energy_kcal: 12.5, fat: 3.5, saturated_fat: null, carbohydrates: null, sugars: null, fiber: null,
      protein: null, salt: 0.1 },
    nutrition_basis: "100ml", barcodes: ["4012345678901"], key: "own:1", entry_id: "e1",
  }));
  await answer(article("own:1", { name: "Duschgel", sources: ["own"], barcodes: ["4012345678901"], has_details: true,
    images: [{ id: IMAGE_A, kind: "ingredients", url: `/api/lidl_plus/images/${IMAGE_A}?authSig=x` }] }));
  assert.strictEqual(document.getElementById("articleFormStatus").textContent, "✓ Gespeichert");
  assert.strictEqual(last().request.type, "lidl_plus/articles", "the list is loaded again");
  await answer({ total: 1, statistics: { articles: 927 }, articles: [listEntry("own:1", { name: "Duschgel", sources: ["own"] })] });

  // Photos: a second click deletes
  const deletePhoto = document.querySelector(`[data-delete-image='${IMAGE_A}']`);
  deletePhoto.click();
  assert.strictEqual(deletePhoto.textContent, "Löschen?");
  assert.notStrictEqual(last().request && last().request.type, "lidl_plus/article_image_delete");
  deletePhoto.click();
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article_image_delete", key: "own:1", image_id: IMAGE_A, entry_id: "e1" }));
  await answer(article("own:1", { name: "Duschgel", sources: ["own"], has_details: true }));
  assert.strictEqual(document.querySelectorAll("[data-delete-image]").length, 0);

  // Photos are made smaller before they are sent
  window.URL.createObjectURL = () => "blob:photo";
  window.URL.revokeObjectURL = () => {};
  window.Image = class {
    set src(value) {
      this.naturalWidth = 4000;
      this.naturalHeight = 3000;
      setTimeout(() => this.onload());
    }
  };
  const drawn = [];
  window.HTMLCanvasElement.prototype.getContext = () => ({ drawImage: (...args) => drawn.push(args.slice(1)) });
  window.HTMLCanvasElement.prototype.toDataURL = (type, quality) => `data:${type};base64,QUJD`;
  document.getElementById("photoKind").value = "nutrition";
  const input = document.querySelector("[data-photo-input][capture]");
  Object.defineProperty(input, "files", { value: [{ name: "label.jpg" }], configurable: true });
  input.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick();
  await tick();
  assert.deepStrictEqual(drawn, [[0, 0, 1600, 1200]]);
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article_image", key: "own:1", kind: "nutrition", data: "QUJD", entry_id: "e1" }));
  await answer(article("own:1", { name: "Duschgel", sources: ["own"], has_details: true,
    images: [{ id: IMAGE_B, kind: "nutrition", url: `/api/lidl_plus/images/${IMAGE_B}?authSig=y` }] }));
  assert.strictEqual(document.getElementById("photoStatus").textContent, "✓ Foto gespeichert");
  // An error of Home Assistant is shown
  const next = document.querySelector("[data-photo-input][capture]");
  Object.defineProperty(next, "files", { value: [{ name: "big.jpg" }], configurable: true });
  next.dispatchEvent(new window.Event("change", { bubbles: true }));
  await tick();
  await tick();
  await answer(null, "Das Foto ist größer als 2,5 MB");
  assert.strictEqual(document.getElementById("photoStatus").textContent, "⚠️ Das Foto ist größer als 2,5 MB");

  // Deleting an article added by hand closes the dialog
  const remove = document.getElementById("articleDelete");
  assert.strictEqual(remove.textContent, "🗑️ Artikel löschen");
  remove.click();
  remove.click();
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article_delete", key: "own:1", entry_id: "e1" }));
  await answer(null);
  assert.ok(!modal.classList.contains("open"));
  await answer({ total: 0, statistics: { articles: 926 }, articles: [] });

  // ── Open Food Facts for a bought article: only empty fields are filled ──────
  window.openArticle("nr:0001");
  await answer(article("nr:0001", { name: "Milbona Skyr", lidl_name: "Milbona Skyr", sources: ["receipts", "offers"],
    purchase_count: 2, barcodes: ["4056489123453"], packaging: "Je 500 g", notes: "lecker",
    product: data.products[0] }));
  assert.strictEqual(document.getElementById("articleForm").elements.name.value, "", "the name of Lidl is the placeholder");
  assert.strictEqual(document.getElementById("articleForm").elements.package_size.placeholder, "Je 500 g");
  document.querySelector("#articleForm button[onclick='lookupForArticle()']").click();
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/barcode", code: "4056489123453", lookup: true, entry_id: "e1" }));
  await answer({ code: "4056489123453", articles: [listEntry("nr:0001")], product, lookup_error: null });
  const filled = document.getElementById("articleForm");
  assert.deepStrictEqual([filled.elements.name.value, filled.elements.package_size.value, filled.elements.n_energy_kcal.value,
    filled.elements.n_fat.value, filled.elements.ingredients.value, filled.elements.notes.value],
  ["", "500 g", "63", "", "Magermilch, Milchsäurebakterien", "lecker"]);
  assert.ok(document.getElementById("articleFormStatus").textContent.startsWith("Von Open Food Facts übernommen: Packungsgröße, Zutaten, Nährwerte"));
  filled.dispatchEvent(new window.Event("submit", { cancelable: true }));
  const saved = last().request;
  assert.deepStrictEqual([saved.key, saved.name, saved.image, saved.nutrition.protein, saved.nutrition.salt],
    ["nr:0001", "", "https://images.openfoodfacts.org/skyr.jpg", 11, null]);
  assert.strictEqual(json(saved.sources), json({ package_size: "Open Food Facts", ingredients: "Open Food Facts",
    nutrition: "Open Food Facts", image: "Open Food Facts" }));
  await answer(null, "Der Barcode 4056489123453 gehört schon zu Duschgel");
  assert.strictEqual(document.getElementById("articleFormStatus").textContent, "⚠️ Der Barcode 4056489123453 gehört schon zu Duschgel");
  window.closeProductModal();

  // ── Scanning in the browser: no scanner of the app, no BarcodeDetector ──────
  const scanModal = document.getElementById("scanModal");
  document.querySelector("button[onclick='openScanner()']").click();
  assert.ok(scanModal.classList.contains("open"));
  assert.strictEqual(last().type, "lidl-plus:scan");
  await answer({ unsupported: true });
  assert.ok(document.getElementById("scanHint").textContent.includes("tippe die Ziffern"));
  document.getElementById("scanInput").value = "4056489 123453";
  document.getElementById("scanForm").dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/barcode", code: "4056489123453", entry_id: "e1" }));
  // Unknown barcode: the product of Open Food Facts and a search for the article it belongs to
  await answer({ code: "4056489123453", articles: [], product, lookup_error: null });
  const scanResult = plain(document.getElementById("scanResult"));
  for (const expected of ["gehört noch zu keinem Artikel", "Skyr Natur", "500 g · 63 kcal pro 100 g · Nutri-Score A", "Gefunden bei Open Food Facts"]) {
    assert.ok(scanResult.includes(expected), `${expected} missing in: ${scanResult}`);
  }
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/articles", query: "Skyr", limit: 15, entry_id: "e1" }));
  await answer({ total: 1, articles: [listEntry("nr:0001", { name: "Milbona Skyr", receipt_names: ["Skyr"], purchase_count: 2 })] });
  document.querySelector("[data-link-key='nr:0001']").click();
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article", key: "nr:0001", entry_id: "e1" }));
  await answer(article("nr:0001", { name: "Milbona Skyr", ingredients: "schon da", image: "https://example.invalid/skyr.jpg" }));
  assert.strictEqual(json(last().request), json({
    type: "lidl_plus/article_save", key: "nr:0001", barcodes: ["4056489123453"], package_size: "500 g",
    nutrition: product.nutrition, nutrition_basis: "100g", sources: { package_size: "Open Food Facts", nutrition: "Open Food Facts" },
    entry_id: "e1",
  }));
  await answer(article("nr:0001", { name: "Milbona Skyr", barcodes: ["4056489123453"] }));
  assert.ok(!scanModal.classList.contains("open"));
  assert.ok(modal.classList.contains("open"));
  assert.strictEqual(document.getElementById("articleFormStatus").textContent,
    "✓ Barcode zugeordnet, von Open Food Facts übernommen: Packungsgröße, Nährwerte");
  window.closeProductModal();

  // ── Scanner of the app: a known barcode opens its article ───────────────────
  window.openScanner();
  await answer({ code: "4056489123453", format: "ean_13" });
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/barcode", code: "4056489123453", entry_id: "e1" }));
  await answer({ code: "4056489123453", articles: [listEntry("nr:0001")], product: null, lookup_error: null });
  assert.ok(!scanModal.classList.contains("open"));
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article", key: "nr:0001", entry_id: "e1" }));
  window.closeProductModal();
  // Closing the scanner of the app closes the dialog, "type the barcode" keeps it open for typing
  window.openScanner();
  await answer({ cancelled: true, reason: "canceled" });
  assert.ok(!scanModal.classList.contains("open"));
  window.openScanner();
  await answer({ cancelled: true, reason: "alternative_options" });
  assert.ok(scanModal.classList.contains("open"));

  // Unknown everywhere: a new article with the barcode, it needs a name
  document.getElementById("scanInput").value = "96385074";
  window.submitBarcode(new window.Event("submit", { cancelable: true }));
  await answer({ code: "96385074", articles: [], product: null, lookup_error: "Open Food Facts: HTTP 503" });
  assert.ok(plain(document.getElementById("scanResult")).includes("nicht erreichbar: Open Food Facts: HTTP 503"));
  document.querySelector("button[onclick='createFromScan()']").click();
  assert.ok(!scanModal.classList.contains("open") && modal.classList.contains("open"));
  assert.strictEqual(document.getElementById("modalTitle").textContent, "Neuer Artikel");
  assert.ok(plain(document.getElementById("barcodeChips")).includes("96385074"));
  assert.ok(plain(document.getElementById("articleDetails")).includes("sobald der Artikel gespeichert ist"));
  const newForm = document.getElementById("articleForm");
  const before = parentMessages.length;
  newForm.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.strictEqual(parentMessages.length, before);
  assert.ok(document.getElementById("articleFormStatus").textContent.includes("braucht einen Namen"));
  newForm.elements.name.value = "Handseife";
  newForm.dispatchEvent(new window.Event("submit", { cancelable: true }));
  const created = last().request;
  assert.deepStrictEqual([created.type, created.key, created.name, created.barcodes], ["lidl_plus/article_save", undefined, "Handseife", ["96385074"]]);
  await answer(article("own:2", { name: "Handseife", sources: ["own"], barcodes: ["96385074"], has_details: true }));
  assert.strictEqual(document.getElementById("modalTitle").textContent, "Handseife");
  assert.ok(document.querySelector("[data-photo-input]"), "photos can be added now");
  // A new article with the values of Open Food Facts
  window.closeProductModal();
  window.openScanner();
  await answer({ unsupported: true });
  window.handleBarcode("4056489123453");
  await answer({ code: "4056489123453", articles: [], product, lookup_error: null });
  await answer({ total: 0, articles: [] });
  window.createFromScan();
  const prefilled = document.getElementById("articleForm");
  assert.deepStrictEqual([prefilled.elements.name.value, prefilled.elements.brand.value, prefilled.elements.n_protein.value],
    ["Skyr Natur", "Milbona", "11"]);
  assert.strictEqual(document.getElementById("articleFormStatus").textContent, "Angaben von Open Food Facts – bitte prüfen und speichern.");
  prefilled.dispatchEvent(new window.Event("submit", { cancelable: true }));
  assert.strictEqual(json(last().request.sources), json({ name: "Open Food Facts", brand: "Open Food Facts",
    package_size: "Open Food Facts", ingredients: "Open Food Facts", image: "Open Food Facts", nutrition: "Open Food Facts" }));
  assert.strictEqual(last().request.image, "https://images.openfoodfacts.org/skyr.jpg");
  await answer(article("own:3", { name: "Skyr Natur", sources: ["own"] }));

  // Scanning from the dialog adds the barcode to the article
  document.querySelector("#articleForm button[onclick='openScanner(true)']").click();
  await answer({ code: "4 056489 123453" });
  assert.ok(!scanModal.classList.contains("open"));
  assert.ok(plain(document.getElementById("barcodeChips")).includes("4056489123453"));
  assert.ok(document.getElementById("articleFormStatus").textContent.includes("Speichern nicht vergessen"));
  window.closeProductModal();

  // ── Leaflets: the offers printed on the pages open their articles ───────────
  document.querySelector("button[data-leaflet-id='l1']").click();
  await answer({ ...data.leaflets[0], pages: [{ number: 2, image: "https://example.invalid/p2.jpg", text: "Skyr" }],
    products: [{ id: "100001", title: "Akku-Bohrer", brand: "PARKSIDE", price: 39.99, pages: [5] }],
    offers: [{ id: "o1", title: "Skyr Natur <b>", brand: "MILBONA", price: 0.99, price_text: "0.99", packaging: "Je 500 g",
      image: "https://example.invalid/skyr.jpg", pages: [2], article_key: "nr:0001" }] });
  const leaflet = document.getElementById("leafletBody");
  assert.ok(plain(leaflet).includes("Angebote der Lidl Plus App in diesem Prospekt (1)"));
  assert.ok(plain(leaflet).includes("Skyr Natur <b>") && plain(leaflet).includes("S. 2") && plain(leaflet).includes("0,99 €"));
  assert.strictEqual(leaflet.querySelectorAll("b").length, 0);
  leaflet.querySelector("[data-article-key='nr:0001']").click();
  assert.ok(modal.classList.contains("open"), "above the leaflet");
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/article", key: "nr:0001", entry_id: "e1" }));
  await answer(article("nr:0001", { name: "Milbona Skyr", leaflets: [{ id: "l1", name: "Aktionsprospekt", title: "11.05. – 16.05.", pages: [2], price: 0.99 }] }));
  // Escape closes the article, the leaflet stays
  document.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));
  assert.ok(!modal.classList.contains("open") && document.getElementById("leafletModal").classList.contains("open"));
  leaflet.querySelector("[data-article-key='web:100001']").click();
  assert.strictEqual(last().request.key, "web:100001");
  await answer(article("web:100001", { name: "Akku-Bohrer", url: "https://www.lidl.de/p/100001", description: "Mit <b>Akku</b>",
    leaflets: [{ id: "l1", name: "Aktionsprospekt", title: "11.05. – 16.05.", pages: [5], price: 39.99 }] }));
  const head = document.getElementById("articleHead");
  assert.ok(plain(head).includes("Mit <b>Akku</b>") && head.querySelector("a[href='https://www.lidl.de/p/100001']"));
  // The leaflet of an article opens the leaflet dialog, below the article dialog: the article dialog is closed
  head.querySelector("[data-leaflet-id='l1']").click();
  assert.ok(!modal.classList.contains("open"));
  assert.strictEqual(json(last().request), json({ type: "lidl_plus/leaflet", leaflet_id: "l1", entry_id: "e1" }));
  await answer({ ...data.leaflets[0], pages: [], products: [], offers: [] });
  assert.ok(plain(leaflet).includes("liefert Lidl keine Produktdaten"));
  window.closeLeafletModal();

  // ── Shopping duration and the zone of the store ─────────────────────────────
  const busyTab = [...document.querySelectorAll(".tab-btn")].find((button) => button.textContent.includes("Stoßzeiten"));
  busyTab.click();
  const duration = document.getElementById("durationBody");
  let text = plain(duration);
  for (const expected of ["Ø 18,5 Min. im Markt, bis zur Kasse Ø 14 Min. (2 Einkäufe gemessen)", "Kürzester Einkauf 12 Min., längster 25 Min.",
    "Nach Wochentag: Mo 12 Min. · Mi 25 Min.", "in Lidl Musterstadt <b>", "(18 Min., bis zur Kasse 14 Min.)", "braucht die Filiale eine eigene Zone"]) {
    assert.ok(text.includes(expected), `${expected} missing in: ${text}`);
  }
  assert.strictEqual(duration.querySelectorAll("b").length, 0);
  document.getElementById("createZone").click();
  // Sent without account: a command of Home Assistant
  assert.strictEqual(json(last()), json({ type: "lidl-plus:call", request: { type: "zone/create", name: "Lidl Musterstadt",
    latitude: 50.1, longitude: 8.2, radius: 100, icon: "mdi:cart", passive: false }, id: last().id }));
  await answer(null, "Unauthorized");
  assert.ok(document.getElementById("zoneStatus").textContent.startsWith("Das hat nicht geklappt (Unauthorized)"));
  assert.ok(!document.getElementById("createZone").disabled);
  document.getElementById("createZone").click();
  await answer({ id: "lidl_musterstadt" });
  text = plain(duration);
  assert.ok(text.includes("Die Zone „Lidl Musterstadt“ wurde angelegt"), text);
  assert.strictEqual(document.getElementById("createZone"), null);
  // Without persons, without data
  send({ type: "lidl-plus:data", data: { ...data, version: "v2", shopping_duration: { ...data.shopping_duration, visits: 0, entities: [] } } });
  assert.ok(plain(duration).includes("Noch kein Einkauf gemessen.") && plain(duration).includes("Wähle unter Einstellungen"));
  send({ type: "lidl-plus:data", data: { ...data, version: "v3", shopping_duration: { ...data.shopping_duration, visits: 0,
    store: { ...data.shopping_duration.store, zones: ["Lidl Musterstadt"] } } } });
  assert.ok(plain(duration).includes("Die Zone „Lidl Musterstadt“ ist eingerichtet"));
  send({ type: "lidl-plus:data", data: { ...data, version: "v4", shopping_duration: null } });
  assert.ok(plain(duration).startsWith("Noch keine Daten"));

  // New data while the article tab is shown loads the list again
  tab.click();
  const loads = parentMessages.filter((entry) => entry.msg.request && entry.msg.request.type === "lidl_plus/articles").length;
  send({ type: "lidl-plus:data", data: { ...data, version: "v5" } });
  assert.strictEqual(parentMessages.filter((entry) => entry.msg.request && entry.msg.request.type === "lidl_plus/articles").length, loads + 1);

  assert.deepStrictEqual(errors, []);
  window.close();
  console.log("article database runtime test passed");
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
