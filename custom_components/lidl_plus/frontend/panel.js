// Sidebar panel of the Lidl Plus integration.
//
// Home Assistant passes the authenticated `hass` object to this element. The
// page in the iframe asks for data via postMessage, this element loads it
// through the websocket API and sends it back, so no data file has to be
// served without login.
//
// Sizing note: `ha-panel-custom` gives itself `display: block` plus the
// safe-area insets as padding (frontend 20260729.x, "Add safe area handling and
// opt-out to custom panels and apps"), but it never sets a height. A percentage
// height in here therefore has no containing block to resolve against and falls
// back to `auto`, which collapsed the iframe to its default 150px. Before that
// change `ha-panel-custom` was still an inline element, so `height: 100%`
// skipped past it up to the app shell and happened to give us the full viewport.
//
// So we size against the viewport ourselves and subtract whatever the host puts
// around us: its offset from the top of the page plus its vertical padding.
// That keeps the panel exactly full-height whether or not Home Assistant adds
// the safe-area padding (`handle_safe_area`), so the insets stay handled in
// exactly one place.

const HOST_OFFSET = "--lidl-panel-host-offset";
const PAGE_URL = new URL("index.html", import.meta.url);
PAGE_URL.search = new URL(import.meta.url).search; // keep the ?v= cache buster
// Websocket commands the page may send: every command of the integration, so an element that is still loaded after
// an update passes on the commands of the newer page as well, and the zone around the store for the shopping
// duration (Home Assistant allows this to administrators only)
const PAGE_COMMAND_PREFIX = "lidl_plus/";
const OTHER_PAGE_COMMANDS = new Set(["zone/create"]);
const EXPORT_PATH = "/api/lidl_plus/export";
// Messages of the Home Assistant app that end a scan of its barcode scanner
const SCAN_RESULT = "bar_code/scan_result";
const SCAN_ABORTED = "bar_code/aborted";
// Version of this file, told to the page. The address of this file (?v=) keeps the version of the start of Home
// Assistant until it is restarted, while the file itself may be a newer one already
const PANEL_VERSION = "1.3.2";

class LidlPlusPanel extends HTMLElement {
  constructor() {
    super();
    this._hass = null;
    this._narrow = false;
    this._iframe = null;
    this._request = null;
    this._menuVisible = undefined;
    this._hostOffset = undefined;
    this._resizeObserver = undefined;
    this._scanListener = null;
    this._onMessage = this._onMessage.bind(this);
    this._updateHostOffset = this._updateHostOffset.bind(this);
  }

  set hass(hass) {
    this._hass = hass;
    this._updateMenu();
    this._sendRequest(); // a request of the page may be waiting for hass
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._updateMenu();
  }

  connectedCallback() {
    this.style.cssText = "display:block;box-sizing:border-box;width:100%;max-height:100%;overflow:hidden;";
    // `dvh` follows collapsing mobile browser chrome; the `vh` line stays put on
    // engines that don't understand `dvh`.
    this.style.height = `calc(100vh - var(${HOST_OFFSET}, 0px))`;
    this.style.height = `calc(100dvh - var(${HOST_OFFSET}, 0px))`;

    if (!this._iframe) {
      this._iframe = document.createElement("iframe");
      this._iframe.src = PAGE_URL.href;
      this._iframe.title = "Lidl Plus";
      this._iframe.setAttribute("loading", "eager");
      this._iframe.style.cssText = "width:100%;height:100%;border:none;display:block;";
      this.appendChild(this._iframe);
    }

    // The cssText above also removed the offset property, so it has to be set again
    this._hostOffset = undefined;
    this._updateHostOffset();
    window.addEventListener("resize", this._updateHostOffset);
    window.addEventListener("orientationchange", this._updateHostOffset);
    if (window.ResizeObserver && this.parentElement) {
      this._resizeObserver = new ResizeObserver(this._updateHostOffset);
      this._resizeObserver.observe(this.parentElement);
    }

    window.addEventListener("message", this._onMessage);
  }

  disconnectedCallback() {
    window.removeEventListener("resize", this._updateHostOffset);
    window.removeEventListener("orientationchange", this._updateHostOffset);
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = undefined;
    }
    window.removeEventListener("message", this._onMessage);
    this._stopScan({ cancelled: true, reason: "closed" });
  }

  // How much of the viewport the panel host takes up around us. The insets can
  // change on rotation, hence the listeners above.
  _updateHostOffset() {
    const host = this.parentElement;
    if (!host) return;
    const hostStyle = getComputedStyle(host);
    const offset =
      Math.max(0, host.getBoundingClientRect().top + window.scrollY) +
      (parseFloat(hostStyle.paddingTop) || 0) +
      (parseFloat(hostStyle.paddingBottom) || 0);
    if (offset !== this._hostOffset) {
      this._hostOffset = offset;
      this.style.setProperty(HOST_OFFSET, `${offset}px`);
    }
  }

  // Same rule as the menu button of the built-in panels
  _updateMenu() {
    const hass = this._hass;
    const visible = Boolean(hass && !hass.kioskMode && (this._narrow || hass.dockedSidebar === "always_hidden"));
    if (visible === this._menuVisible) return;
    this._menuVisible = visible;
    this._post({ type: "lidl-plus:menu", visible });
  }

  _onMessage(event) {
    if (!this._iframe || event.source !== this._iframe.contentWindow) return;
    const message = event.data || {};
    if (message.type === "lidl-plus:request") {
      // The page may have been (re)loaded, so it gets the menu state again
      this._menuVisible = undefined;
      this._updateMenu();
      this._request = { type: "lidl_plus/panel_data" };
      if (message.entry_id) this._request.entry_id = message.entry_id;
      if (message.known_sync) this._request.known_sync = message.known_sync;
      this._sendRequest();
    } else if (message.type === "lidl-plus:call") {
      this._answer(message.id, () => this._call(message.request));
    } else if (message.type === "lidl-plus:export") {
      this._answer(message.id, () => this._export(message));
    } else if (message.type === "lidl-plus:scan") {
      this._answer(message.id, () => this._scan());
    } else if (message.type === "lidl-plus:toggle-menu") {
      // Same event as the menu button of the built-in panels
      this.dispatchEvent(new Event("hass-toggle-menu", { bubbles: true, composed: true }));
    }
  }

  async _sendRequest() {
    if (!this._hass || !this._request) return;
    const request = this._request;
    this._request = null;
    // What the page can use: this version, and the barcode scanner of the Home Assistant app
    this._post({
      type: "lidl-plus:panel",
      version: PANEL_VERSION,
      app: Boolean(this._external()),
      scanner: this._scannerAvailable(),
    });
    try {
      this._post({ type: "lidl-plus:data", data: await this._hass.callWS(request) });
    } catch (err) {
      this._post({ type: "lidl-plus:error", message: (err && err.message) || String(err) });
    }
  }

  // Result or error of a call of the page, sent back with the id of the call
  async _answer(id, action) {
    try {
      if (!this._hass) throw new Error("Home Assistant is not ready");
      this._post({ type: "lidl-plus:result", id, result: await action() });
    } catch (err) {
      this._post({ type: "lidl-plus:result", id, error: (err && err.message) || String(err) });
    }
  }

  _call(request) {
    const type = request && typeof request.type === "string" ? request.type : "";
    if (!type.startsWith(PAGE_COMMAND_PREFIX) && !OTHER_PAGE_COMMANDS.has(type)) throw new Error("Unknown command");
    return this._hass.callWS(request);
  }

  // The page has no access token, so the download gets a signed address, like the backups of Home Assistant
  async _export({ dataset, format, entry_id }) {
    const params = new URLSearchParams({ dataset: dataset || "all" });
    if (format) params.set("format", format);
    if (entry_id) params.set("entry_id", entry_id);
    const { path } = await this._hass.callWS({ type: "auth/sign_path", path: `${EXPORT_PATH}?${params}` });
    this._download(path);
    return true;
  }

  // The barcode scanner of the Home Assistant app (like the one for Matter QR codes). The frontend keeps its
  // listeners for the results to itself, so the messages of the app are read before the frontend handles them.
  // Resolves with {code, format}, {cancelled, reason} or {unsupported} (browser: the page uses the camera).
  _scan() {
    const external = this._external();
    if (!this._scannerAvailable()) return Promise.resolve({ unsupported: true });
    this._stopScan({ cancelled: true, reason: "restarted" });
    return new Promise((resolve) => {
      const original = window.externalBus;
      const listener = (message) => {
        let msg = message;
        try {
          if (typeof msg === "string") msg = JSON.parse(msg);
        } catch (err) {
          msg = null;
        }
        if (msg && msg.type === "command" && msg.command === SCAN_RESULT) {
          external.fireMessage({ type: "bar_code/close" });
          this._stopScan({ code: String((msg.payload && msg.payload.rawValue) || ""), format: msg.payload && msg.payload.format });
        } else if (msg && msg.type === "command" && msg.command === SCAN_ABORTED) {
          this._stopScan({ cancelled: true, reason: msg.payload && msg.payload.reason });
        }
        // The frontend answers the app as before
        return original(message);
      };
      this._scanListener = { listener, original, resolve };
      window.externalBus = listener;
      external.fireMessage({
        type: "bar_code/scan",
        payload: {
          title: "Artikel scannen",
          description: "Halte die Kamera auf den Strichcode (EAN) der Verpackung.",
          alternative_option_label: "Barcode eintippen",
        },
      });
    });
  }

  // The messaging with the Home Assistant app, only inside the app
  _external() {
    return (this._hass && this._hass.auth && this._hass.auth.external) || null;
  }

  _scannerAvailable() {
    const external = this._external();
    return Boolean(external && external.config && external.config.hasBarCodeScanner && typeof window.externalBus === "function");
  }

  _stopScan(result) {
    const scan = this._scanListener;
    if (!scan) return;
    this._scanListener = null;
    // Another script may have wrapped the bus meanwhile, then it keeps calling our listener, which only passes on
    if (window.externalBus === scan.listener) window.externalBus = scan.original;
    scan.resolve(result);
  }

  _download(path) {
    const link = document.createElement("a");
    link.href = path;
    link.download = ""; // the server sends the file name
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    link.remove();
  }

  _post(message) {
    if (this._iframe && this._iframe.contentWindow) {
      this._iframe.contentWindow.postMessage(message, window.location.origin);
    }
  }
}

if (!customElements.get("lidl-plus-panel")) {
  customElements.define("lidl-plus-panel", LidlPlusPanel);
}
