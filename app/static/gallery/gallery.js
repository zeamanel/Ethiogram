// Ethiogram Bot Gallery — public directory. No auth: fetches /api/v1/gallery
// directly. Visible to everyone (customers + owners).
(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const debounce = (fn, ms) => { let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); }; };

  let CATEGORY = null;

  function openLink(url, telegram) {
    if (telegram && tg && tg.openTelegramLink) return tg.openTelegramLink(url);
    if (tg && tg.openLink) return tg.openLink(url);
    window.open(url, "_blank");
  }

  async function getJSON(path) {
    const res = await fetch("/api/v1" + path);
    if (!res.ok) throw new Error(path + " -> " + res.status);
    return res.json();
  }

  async function loadCategories() {
    let cats = [];
    try { cats = await getJSON("/gallery/categories"); } catch (e) { /* no chips */ }
    const all = [{ k: null, label: "All" }].concat(cats.map(c => ({ k: c, label: c })));
    $("g-chips").innerHTML = all.map(c =>
      `<div class="g-chip${(c.k === CATEGORY) ? " active" : ""}" data-cat="${esc(c.k == null ? "" : c.k)}">${esc(c.label)}</div>`
    ).join("");
    $("g-chips").querySelectorAll(".g-chip").forEach(chip => chip.onclick = () => {
      CATEGORY = chip.dataset.cat || null;
      $("g-chips").querySelectorAll(".g-chip").forEach(c =>
        c.classList.toggle("active", (c.dataset.cat || null) === CATEGORY));
      loadGallery();
    });
  }

  async function loadGallery() {
    const q = $("g-search").value.trim();
    const params = new URLSearchParams();
    if (q) params.set("search", q);
    if (CATEGORY) params.set("category", CATEGORY);
    $("g-loading").classList.remove("hidden");
    $("g-list").innerHTML = "";
    let res;
    try { res = await getJSON("/gallery?" + params.toString()); }
    catch (e) { $("g-loading").classList.add("hidden"); $("g-list").innerHTML = `<div class="lempty">Couldn't load businesses.</div>`; return; }
    $("g-loading").classList.add("hidden");
    if (!res.items.length) { $("g-list").innerHTML = `<div class="lempty">No businesses found. Try another search.</div>`; return; }

    $("g-list").innerHTML = res.items.map(b => {
      const initial = (b.name || "?").trim()[0].toUpperCase();
      const logo = b.logo_url ? `<img src="${esc(b.logo_url)}" alt="">` : esc(initial);
      const cat = b.category ? `<div class="g-cat">${esc(b.category)}</div>` : "";
      const tag = b.tagline ? `<div class="g-tag">${esc(b.tagline)}</div>` : "";
      return `<div class="g-card">
        <div class="g-row">
          <div class="g-logo">${logo}</div>
          <div class="g-info"><div class="g-name">${esc(b.name)}</div>${cat}</div>
        </div>
        ${tag}
        <div class="g-actions">
          <button class="g-btn g-btn-chat" data-chat="${esc(b.chat_url)}">💬 Chat</button>
          <button class="g-btn g-btn-store" data-store="${esc(b.store_url)}">🛍 Store</button>
        </div></div>`;
    }).join("");

    $("g-list").querySelectorAll("[data-chat]").forEach(btn =>
      btn.onclick = () => openLink(btn.dataset.chat, true));
    $("g-list").querySelectorAll("[data-store]").forEach(btn =>
      btn.onclick = () => openLink(location.origin + btn.dataset.store, false));
  }

  async function boot() {
    if (tg) { try { tg.ready(); tg.expand(); } catch (e) {} }
    $("g-search").oninput = debounce(loadGallery, 300);
    await loadCategories();
    await loadGallery();
  }

  boot();
})();
