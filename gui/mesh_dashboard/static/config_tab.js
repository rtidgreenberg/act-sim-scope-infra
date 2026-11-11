(function () {
  "use strict";

  const status = document.getElementById("config-status");
  const summary = document.getElementById("config-summary");
  const sections = document.getElementById("config-sections");
  const refresh = document.getElementById("config-refresh");
  let request = null;

  function element(tag, text) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = String(text);
    return node;
  }

  function render(snapshot) {
    if (typeof snapshot.available !== "boolean" || !Array.isArray(snapshot.sections)) {
      throw new Error("Invalid configuration snapshot");
    }
    for (const section of snapshot.sections) {
      if (typeof section.title !== "string" || !Array.isArray(section.columns) ||
          !section.columns.every(column => typeof column === "string") ||
          !Array.isArray(section.rows) || !section.rows.every(row =>
            Array.isArray(row) && row.length === section.columns.length &&
            row.every(value => typeof value === "string" ||
              (typeof value === "number" && Number.isFinite(value))))) {
        throw new Error("Invalid configuration table");
      }
    }
    const loaded = snapshot.loaded_at_ms ? new Date(snapshot.loaded_at_ms).toLocaleString() : "not available";
    status.dataset.error = String(!snapshot.available);
    status.textContent = snapshot.available
      ? `Configuration loaded at dashboard startup: ${loaded}. This is the supplied YAML and resolved QoS, ` +
        "not per-node runtime introspection. Refresh view re-reads this snapshot; it does not reload routers."
      : snapshot.reason || "Router configuration unavailable. Known dashboard settings are shown below.";
    sections.replaceChildren();
    summary.replaceChildren();
    const presence = snapshot.sections.find(section => section.id === "presence");
    for (const label of ["ALIVE to STALE", "STALE to DEAD", "DDS endpoint liveliness lease"]) {
      const row = presence?.rows.find(value => value[0] === label);
      if (!row) continue;
      const card = element("article");
      card.append(element("h2", label), element("strong", row[1]));
      summary.append(card);
    }
    for (const section of snapshot.sections) {
      const container = element("section");
      container.className = "config-section";
      container.dataset.section = section.id;
      container.append(element("h2", section.title), element("p", section.description));
      const wrapper = element("div");
      wrapper.className = "config-table-wrap";
      const table = element("table");
      table.className = `config-table ${section.columns.length === 3 ? "simple" : "wide"}`;
      const caption = element("caption", section.title);
      caption.hidden = true;
      const head = element("thead");
      const headings = element("tr");
      for (const label of section.columns) {
        const heading = element("th", label);
        heading.scope = "col";
        headings.append(heading);
      }
      head.append(headings);
      const body = element("tbody");
      for (const values of section.rows) {
        const row = element("tr");
        for (const value of values) row.append(element("td", value));
        body.append(row);
      }
      table.append(caption, head, body);
      wrapper.append(table);
      container.append(wrapper);
      sections.append(container);
    }
  }

  function load() {
    if (request) return request;
    refresh.disabled = true;
    status.dataset.error = "false";
    status.textContent = "Loading system configuration...";
    request = fetch("/api/system_config", {cache: "no-store"})
      .then(response => {
        if (!response.ok) throw new Error(`Configuration API returned HTTP ${response.status}`);
        return response.json();
      })
      .then(render)
      .catch(error => {
        sections.replaceChildren();
        summary.replaceChildren();
        status.dataset.error = "true";
        status.textContent = `System configuration unavailable: ${error.message}. Use Refresh view to retry.`;
        console.warn("System configuration request failed", error);
      })
      .finally(() => { request = null; refresh.disabled = false; });
    return request;
  }

  window.addEventListener("act-workspace-change", event => {
    if (event.detail.name === "config") load();
  });
  refresh.addEventListener("click", load);
})();
