class LidlPlusPanelElement extends HTMLElement {
  connectedCallback() {
    this.style.cssText = "display:block;height:100%;overflow:hidden;";

    const iframe = document.createElement("iframe");
    iframe.src = "/local/lidl_plus/index.html?_=" + Date.now();
    iframe.style.cssText = "width:100%;height:100%;border:none;display:block;";
    iframe.setAttribute("loading", "eager");
    this.appendChild(iframe);
  }
}

customElements.define("lidl-plus-panel-element", LidlPlusPanelElement);
