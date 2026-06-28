// Ethiogram Customer Storefront — public, themed-per-business, data-driven.
// Fetches GET /api/v1/miniapp/{slug}, applies the theme, and renders the
// sections in payload order. No auth (public catalog); ordering deep-links to
// the business bot; cart is localStorage only.
(function () {
  const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;
  const $ = (id) => document.getElementById(id);
  const show = (id) => $(id).classList.remove("hidden");
  const hide = (id) => $(id).classList.add("hidden");
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  let DATA = null, SLUG = null, FILTER = null;

  // ---- slug: Telegram deep-link start_param, else ?s=, else /store/<slug> ----
  function resolveSlug() {
    const sp = tg && tg.initDataUnsafe ? tg.initDataUnsafe.start_param : null;
    if (sp) return sp;
    const q = new URLSearchParams(location.search).get("s");
    if (q) return q;
    const m = location.pathname.split("/store/")[1];
    return m ? m.replace(/\/+$/, "") : "";
  }

  // ---- theme: write the controlled CSS vars; the rest derive via color-mix ----
  function applyTheme(t) {
    if (!t) return;
    const r = document.documentElement.style;
    const set = (k, v) => { if (v) r.setProperty(k, v); };
    set("--brand", t.primary); set("--accent", t.accent);
    set("--bg", t.bg); set("--surface", t.surface); set("--border", t.border);
    set("--text", t.text); set("--radius", t.radius);
    if (t.font_heading) set("--font-heading", `'${t.font_heading}'`);
    if (t.font_body) set("--font-body", `'${t.font_body}'`);
    if (t.bg) document.body.style.background = t.bg;
  }

  // ---- deep link to the business bot ----
  function botLink(startPayload) {
    const u = DATA.business.bot_username;
    if (!u) return null;
    let url = "https://t.me/" + u;
    if (startPayload) url += "?start=" + encodeURIComponent(startPayload);
    return url;
  }
  function openBot(startPayload) {
    const url = botLink(startPayload);
    if (!url) { alert("This store isn't connected to a bot yet."); return; }
    if (tg && tg.openTelegramLink) tg.openTelegramLink(url);
    else window.open(url, "_blank");
  }

  // ---- cart (localStorage, per slug) ----
  function cartKey() { return "eth_cart_" + SLUG; }
  function getCart() { try { return JSON.parse(localStorage.getItem(cartKey()) || "[]"); } catch (e) { return []; } }
  function setCart(c) { localStorage.setItem(cartKey(), JSON.stringify(c)); renderCart(); }
  function addToCart(item) {
    const c = getCart();
    c.push({ id: item.id, title: item.title, price: item.price || null });
    setCart(c);
    if (tg && tg.HapticFeedback) try { tg.HapticFeedback.impactOccurred("light"); } catch (e) {}
  }
  function renderCart() {
    const c = getCart();
    if (!c.length) { hide("cart-bar"); return; }
    $("cart-count").textContent = c.length + (c.length === 1 ? " item" : " items") + " in cart";
    $("cart-total").textContent = "Ready to order";
    show("cart-bar");
  }

  // ---- section renderers (return HTML string; wiring done after insert) ----
  function elFor(html) { const d = document.createElement("div"); d.innerHTML = html; return d.firstElementChild; }

  function renderHero(c) {
    const h = c.hero || {};
    const node = elFor(`<div class="hero">
      <div class="hero-title">${esc(h.title || DATA.business.name)}</div>
      ${h.subtitle ? `<div class="hero-sub">${esc(h.subtitle)}</div>` : ""}
      <button class="hero-cta">${esc(h.cta || "Order on Telegram")} →</button>
    </div>`);
    node.querySelector(".hero-cta").onclick = () => openBot();
    return node;
  }

  function renderCategories(c) {
    const cats = c.categories || [];
    if (!cats.length) return null;
    const wrap = elFor(`<div><div class="sec-head"><div class="sec-title">Categories</div></div>
      <div class="chips"></div></div>`);
    const chips = wrap.querySelector(".chips");
    const all = ["All"].concat(cats);
    all.forEach(cat => {
      const active = (cat === "All" && !FILTER) || cat === FILTER;
      const chip = elFor(`<div class="chip ${active ? "active" : ""}">${esc(cat)}</div>`);
      chip.onclick = () => { FILTER = (cat === "All") ? null : cat; renderAll(); };
      chips.appendChild(chip);
    });
    return wrap;
  }

  function renderProducts(c) {
    let products = c.products || [];
    if (!products.length) return null;
    if (FILTER) products = products.filter(p => p.category === FILTER);
    const wrap = elFor(`<div><div class="sec-head"><div class="sec-title">Products</div></div>
      <div class="pgrid"></div></div>`);
    const grid = wrap.querySelector(".pgrid");
    if (!products.length) { grid.outerHTML = `<div class="lempty">No products in this category.</div>`; return wrap; }
    products.forEach(p => {
      const card = elFor(`<div class="pcard">
        <div class="pimg">${p.image_url ? `<img src="${esc(p.image_url)}" style="width:100%;height:100%;object-fit:cover">` : "🛍️"}</div>
        <div class="pinfo">
          <div class="pname">${esc(p.title)}</div>
          <div class="psub">${esc(p.body || "")}</div>
          <div class="pfoot">
            <div class="pprice">${esc(p.price || "")}</div>
            <button class="add-btn" title="Add">＋</button>
          </div>
        </div></div>`);
      card.querySelector(".add-btn").onclick = (e) => { e.stopPropagation(); addToCart(p); };
      card.onclick = () => openBot("p_" + p.id);
      grid.appendChild(card);
    });
    return wrap;
  }

  function renderServices(c) {
    const services = c.services || [];
    if (!services.length) return null;
    const wrap = elFor(`<div><div class="sec-head"><div class="sec-title">Services</div></div>
      <div class="lcard"></div></div>`);
    const list = wrap.querySelector(".lcard");
    services.forEach(s => {
      const meta = [s.body, s.duration].filter(Boolean).join(" · ");
      const row = elFor(`<div class="lrow"><div class="lic">🛠️</div>
        <div class="linfo"><div class="lname">${esc(s.title)}</div>
          <div class="lmeta">${esc(meta)}</div></div>
        <div class="lprice">${esc(s.price || "")}</div></div>`);
      row.onclick = () => openBot("s_" + s.id);
      list.appendChild(row);
    });
    return wrap;
  }

  function renderMenu(c) {
    const groups = c.menu || [];
    if (!groups.length) return null;
    const wrap = elFor(`<div><div class="sec-head"><div class="sec-title">Menu</div></div></div>`);
    groups.forEach(g => {
      wrap.appendChild(elFor(`<div class="menu-group-title">${esc(g.category)}</div>`));
      const list = elFor(`<div class="lcard"></div>`);
      (g.items || []).forEach(m => {
        list.appendChild(elFor(`<div class="lrow"><div class="lic">🍽️</div>
          <div class="linfo"><div class="lname">${esc(m.title)}</div>
            <div class="lmeta">${esc(m.body || "")}</div></div>
          <div class="lprice">${esc(m.price || "")}</div></div>`));
      });
      wrap.appendChild(list);
    });
    return wrap;
  }

  function renderFaqs(c) {
    const faqs = c.faqs || [];
    if (!faqs.length) return null;
    const wrap = elFor(`<div><div class="sec-head"><div class="sec-title">FAQ</div></div></div>`);
    faqs.forEach(f => wrap.appendChild(elFor(
      `<div class="faq"><div class="faq-q">${esc(f.q)}</div><div class="faq-a">${esc(f.a || "")}</div></div>`)));
    return wrap;
  }

  function renderHours(c) {
    if (!c.hours) return null;
    return elFor(`<div><div class="sec-head"><div class="sec-title">Hours</div></div>
      <div class="info-block"><div class="info-line"><span class="ic">🕒</span><span>${esc(c.hours)}</span></div></div></div>`);
  }

  function renderContact(c) {
    const ct = c.contact || {};
    if (!ct.phone && !ct.address && !ct.directions_url) return null;
    const lines = [];
    if (ct.phone) lines.push(`<a class="info-line" href="tel:${esc(ct.phone)}" style="color:inherit;text-decoration:none"><span class="ic">📞</span><span>${esc(ct.phone)}</span></a>`);
    if (ct.address) lines.push(`<div class="info-line"><span class="ic">📍</span><span>${esc(ct.address)}</span></div>`);
    if (ct.directions_url) lines.push(`<a class="info-line directions" href="${esc(ct.directions_url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none"><span class="ic">🧭</span><span>Get directions</span></a>`);
    return elFor(`<div><div class="sec-head"><div class="sec-title">Contact</div></div>
      <div class="info-block">${lines.join("")}</div></div>`);
  }

  function renderChat(c) {
    const node = elFor(`<div class="chat-widget">
      <div class="chat-icon">💬</div>
      <div><div class="chat-title">Ask our AI assistant</div>
        <div class="chat-sub">Available 24/7 · replies in seconds</div></div>
      <div class="chat-arrow">›</div></div>`);
    node.onclick = () => openBot();
    return node;
  }

  const RENDERERS = {
    hero: renderHero, categories: renderCategories, products: renderProducts,
    services: renderServices, menu: renderMenu, faqs: renderFaqs,
    hours: renderHours, contact: renderContact, chat: renderChat,
  };

  function renderAll() {
    const root = $("sections");
    root.innerHTML = "";
    const sections = (DATA.layout && DATA.layout.sections) || [];
    sections.forEach(s => {
      const fn = RENDERERS[s.type];
      if (!fn) return;
      const node = fn(DATA.content || {});
      if (node) { root.appendChild(node); root.appendChild(elFor(`<div class="sec-gap"></div>`)); }
    });
    renderCart();
  }

  function renderHeader() {
    const b = DATA.business;
    const logo = $("s-logo");
    if (b.logo_url) logo.innerHTML = `<img src="${esc(b.logo_url)}" alt="">`;
    else logo.textContent = (b.name || "S").trim()[0].toUpperCase();
    $("s-hname").textContent = b.name || "Store";
    $("s-hsub").textContent = b.category || (b.bot_username ? "@" + b.bot_username : "");
  }

  function fail(title, msg) {
    hide("loading");
    if (title) $("error-title").textContent = title;
    if (msg) $("error-msg").textContent = msg;
    show("error");
  }

  async function boot() {
    if (tg) { try { tg.ready(); tg.expand(); } catch (e) {} }
    SLUG = resolveSlug();
    if (!SLUG) return fail("No store selected", "Open this from a business's Telegram bot.");

    let resp;
    try { resp = await fetch("/api/v1/miniapp/" + encodeURIComponent(SLUG)); }
    catch (e) { return fail("Couldn't load", "Check your connection and try again."); }
    if (resp.status === 404) return fail("Store not found", "This storefront isn't available.");
    if (!resp.ok) return fail("Couldn't load", "Something went wrong. Try again later.");

    DATA = await resp.json();
    applyTheme(DATA.theme);
    renderHeader();
    renderAll();
    $("cart-btn").onclick = () => openBot();
    hide("loading"); show("store");
  }

  boot();
})();
