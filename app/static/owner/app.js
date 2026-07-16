// Ethiogram Owner Console — loads data and renders the dashboard.
(function () {
  const $ = (id) => document.getElementById(id);
  let IS_ADMIN = false;
  const show = (id) => $(id).classList.remove("hidden");
  const hide = (id) => $(id).classList.add("hidden");
  const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString("en-US"));
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function timeAgo(iso) {
    if (!iso) return "";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m";
    if (s < 86400) return Math.floor(s / 3600) + "h";
    return Math.floor(s / 86400) + "d";
  }
  function notify(msg) {
    if (Eth.tg && Eth.tg.showAlert) Eth.tg.showAlert(msg); else alert(msg);
  }
  function confirmAction(msg) {
    return new Promise((resolve) => {
      if (Eth.tg && Eth.tg.showConfirm) Eth.tg.showConfirm(msg, (ok) => resolve(!!ok));
      else resolve(window.confirm(msg));
    });
  }

  async function boot() {
    Eth.initTelegram();
    let auth;
    try {
      auth = await Eth.login();
    } catch (e) {
      return fail("We couldn't verify your Telegram session. Open this from the bot's menu button.");
    }
    // Signal the UI that Chapa payments are available for authenticated owners.
    try { window.CHAPA_ENABLED = true; Eth.CHAPA_ENABLED = true; } catch (e) { /* no-op in restrictive env */ }
    IS_ADMIN = !!(auth && auth.is_admin);
    REFERRAL = { link: auth && auth.referral_link, bonus: (auth && auth.referral_bonus) || 0 };
    let businesses;
    try {
      businesses = await Eth.get("/dashboard/businesses");
    } catch (e) {
      return fail("Couldn't load your account.");
    }
    if (!businesses || businesses.length === 0) {
      hide("loading");
      if (IS_ADMIN) return bootAdmin();         // admin + no businesses → admin console
      setupWizard(); show("onboarding");
      return;
    }

    ALL_BUSINESSES = businesses;
    const biz = pickBusiness(businesses);
    renderSwitcher(businesses, biz.id);
    await loadDashboard(biz);
  }

  // --- business switcher (only matters when the owner has >1 business) ---
  const BIZ_KEY = "eth_selected_biz";
  let ALL_BUSINESSES = [];
  let REFERRAL = { link: null, bonus: 0 };

  function pickBusiness(businesses) {
    let saved = null;
    try { saved = localStorage.getItem(BIZ_KEY); } catch (e) { /* private mode */ }
    return businesses.find((b) => b.id === saved) || businesses[0];
  }

  function rememberBusiness(id) {
    try { localStorage.setItem(BIZ_KEY, id); } catch (e) { /* ignore */ }
  }

  function renderSwitcher(businesses, selectedId) {
    const wrap = $("biz-switcher");
    if (!businesses || businesses.length < 2) { hide("biz-switcher"); return; }
    const current = businesses.find((b) => b.id === selectedId) || businesses[0];
    $("biz-switch-name").textContent = current.name || "Business";

    const menu = $("biz-switch-menu");
    menu.innerHTML = businesses.map((b) =>
      `<button type="button" class="biz-switch-item${b.id === selectedId ? " active" : ""}" data-biz="${esc(b.id)}">`
      + `${esc(b.name || "Business")}${b.id === selectedId ? " ✓" : ""}</button>`).join("");
    menu.querySelectorAll("[data-biz]").forEach((btn) => {
      btn.onclick = () => {
        hide("biz-switch-menu");
        const id = btn.dataset.biz;
        if (id === selectedId) return;
        switchBusiness(id);
      };
    });

    $("biz-switch-btn").onclick = () => $("biz-switch-menu").classList.toggle("hidden");
    show("biz-switcher");
  }

  async function switchBusiness(id) {
    const biz = ALL_BUSINESSES.find((b) => b.id === id);
    if (!biz) return;
    rememberBusiness(id);
    renderSwitcher(ALL_BUSINESSES, id);
    hide("dashboard"); show("loading");
    await loadDashboard(biz);
  }

  async function loadDashboard(biz) {
    rememberBusiness(biz.id);
    // overview is required; orders/docs/wallet are best-effort enrichment.
    let overview;
    try {
      overview = await Eth.get(`/dashboard/overview/${biz.id}`);
    } catch (e) {
      return fail("Couldn't load your dashboard.");
    }
    const [ordersR, docsR, walletR, apptsR] = await Promise.allSettled([
      Eth.get(`/dashboard/orders/${biz.id}`),
      Eth.get(`/knowledge/${biz.id}/documents`),
      Eth.get(`/billing/wallet/${biz.id}`),
      Eth.get(`/dashboard/appointments/${biz.id}`),
    ]);
    const orders = ordersR.status === "fulfilled" ? ordersR.value : [];
    const docs = docsR.status === "fulfilled" ? docsR.value : [];
    const wallet = walletR.status === "fulfilled" ? walletR.value : null;
    const appts = apptsR.status === "fulfilled" ? apptsR.value : [];

    render(overview, orders, docs, wallet);
    renderAppointments(biz.id, appts);
    renderIncoming();
    hide("loading"); show("dashboard");
  }

  // ---- appointments (native booking engine) ----
  function apptWhen(iso) {
    const d = new Date(iso);
    if (isNaN(d)) return iso;
    return d.toLocaleString(undefined, { weekday: "short", day: "numeric", month: "short",
                                         hour: "2-digit", minute: "2-digit" });
  }

  function renderAppointments(bizId, appts) {
    const el = $("appointments");
    if (!appts || !appts.length) {
      el.innerHTML = empty("No upcoming appointments.");
      return;
    }
    el.innerHTML = appts.slice(0, 12).map(a => {
      const cal = a.synced_to_calendar ? " · 📅" : "";
      const price = a.price ? `<span class="lamt">${esc(a.price)}</span>` : "";
      const cancel = `<button class="appt-x" data-cancel="${esc(a.id)}" title="Cancel">✕</button>`;
      return `<div class="litem"><div class="lic ic-blue">📅</div>
        <div class="linfo"><div class="lname">${esc(a.customer_name || "Customer")} · ${esc(a.service_name || "Appointment")}</div>
        <div class="lmeta">${esc(apptWhen(a.starts_at))}${cal}</div></div>
        <div class="appt-right">${price}${cancel}</div></div>`;
    }).join("");
    el.querySelectorAll("[data-cancel]").forEach(btn => {
      btn.onclick = async () => {
        if (!(await confirmAction("Cancel this appointment?"))) return;
        try {
          await Eth.patch(`/dashboard/appointments/${bizId}/${btn.dataset.cancel}`, { status: "cancelled" });
          const appts2 = await Eth.get(`/dashboard/appointments/${bizId}`);
          renderAppointments(bizId, appts2);
        } catch (e) { notify("Couldn't cancel — please try again."); }
      };
    });
  }

  function fail(msg) {
    hide("loading"); $("error-msg").textContent = msg; show("error");
  }

  function showErr(id, msg) { const e = $(id); e.textContent = msg; e.classList.remove("hidden"); }
  function setBtn(id, busy, label) { const b = $(id); b.disabled = busy; b.textContent = label; }

  // ---- onboarding wizard (step 1: business details) ----
  function setupWizard() {
    const showOnly = (id) => ["onboarding", "wiz-business", "wiz-bot"].forEach(x => x === id ? show(x) : hide(x));
    $("onb-start").onclick = () => { showOnly("wiz-business"); $("wb-name").focus(); };

    $("wb-continue").onclick = async () => {
      const name = $("wb-name").value.trim();
      const cat = $("wb-cat").value.trim();
      hide("wb-err");
      if (name.length < 2) return showErr("wb-err", "Please enter a business name (at least 2 characters).");
      setBtn("wb-continue", true, "Creating…");
      try {
        const biz = await Eth.post("/businesses", { name, category: cat || null });
        // step 2; skipping reloads into the new (bot-less) dashboard.
        connectBotFlow(biz.id, () => location.reload());
      } catch (e) {
        showErr("wb-err", e.detail || "Couldn't create your business. Try again.");
        setBtn("wb-continue", false, "Continue →");
      }
    };
  }

  // ---- connect-a-bot flow (shared by onboarding step 2 AND the dashboard "My Bots") ----
  function connectBotFlow(businessId, onCancel) {
    ["loading", "error", "onboarding", "wiz-business", "dashboard"].forEach(hide);
    $("wb-token").value = ""; hide("wt-err");
    setBtn("wt-connect", false, "Connect bot →");
    show("wiz-bot"); $("wb-token").focus();

    $("wt-connect").onclick = async () => {
      const token = $("wb-token").value.trim();
      hide("wt-err");
      if (!/^\d+:[A-Za-z0-9_-]{30,}$/.test(token))
        return showErr("wt-err", "That doesn't look like a bot token — copy the full token from @BotFather.");
      setBtn("wt-connect", true, "Connecting…");
      try {
        await Eth.post("/bots", { token, business_id: businessId });
        location.reload();          // bot live → reload into the dashboard
      } catch (e) {
        showErr("wt-err", e.detail || "Couldn't connect the bot. Check the token and try again.");
        setBtn("wt-connect", false, "Connect bot →");
      }
    };
    $("wt-skip").onclick = onCancel;
  }

  // ---- Business Brain manager (Phase A: documents) ----
  async function openBrainManager(bizId) {
    hide("dashboard"); show("brain-manager");
    $("bm-back").onclick = () => { hide("brain-manager"); show("dashboard"); };
    $("bm-refresh").onclick = () => loadDocs(bizId);
    $("bm-settings-link").onclick = () => openBrainSettings(bizId);
    $("bm-catalog-link").onclick = () => openCatalog(bizId);
    $("bm-upload").onclick = () => $("bm-file").click();
    $("bm-file").onchange = async () => {
      const file = $("bm-file").files[0];
      if (!file) return;
      hide("bm-err");
      if (file.size > 50 * 1024 * 1024) {
        $("bm-file").value = ""; return showErr("bm-err", "That file is over the 50 MB limit.");
      }
      setBtn("bm-upload", true, "Uploading…");
      try {
        await Eth.upload(`/knowledge/${bizId}/documents`, file);
        $("bm-file").value = "";
        await loadDocs(bizId);
      } catch (e) {
        showErr("bm-err", e.detail || "Upload failed. Check the file type and try again.");
      } finally {
        setBtn("bm-upload", false, "＋ Upload document");
      }
    };
    await loadDocs(bizId);
  }

  // ---- Brain settings editor (Phase B: persona/tone/fallback/RAG tuning) ----
  async function openBrainSettings(bizId) {
    hide("brain-manager"); show("brain-settings");
    $("bs-back").onclick = () => { hide("brain-settings"); show("brain-manager"); };

    let cfg;
    try { cfg = await Eth.get(`/businesses/${bizId}/brain`); }
    catch (e) { return showErr("bs-err", "Couldn't load settings."); }

    $("bs-persona").value = cfg.persona_name || "";
    $("bs-tone").value = cfg.persona_tone || "";
    $("bs-extra").value = cfg.system_prompt_extra || "";
    $("bs-fallback").value = cfg.fallback_message || "";
    $("bs-thresh").value = cfg.rag_similarity_threshold;
    $("bs-topk").value = cfg.rag_top_k;

    $("bs-save").onclick = async () => {
      hide("bs-err");
      const persona = $("bs-persona").value.trim();
      const thresh = parseFloat($("bs-thresh").value);
      const topk = parseInt($("bs-topk").value, 10);
      if (!persona) return showErr("bs-err", "Bot name can't be empty.");
      if (isNaN(thresh) || thresh < 0 || thresh > 1) return showErr("bs-err", "Match strictness must be between 0 and 1.");
      if (isNaN(topk) || topk < 1 || topk > 20) return showErr("bs-err", "Snippets per reply must be between 1 and 20.");
      const body = {
        persona_name: persona,
        persona_tone: $("bs-tone").value.trim() || "friendly",
        system_prompt_extra: $("bs-extra").value.trim() || null,
        fallback_message: $("bs-fallback").value.trim() || undefined,
        rag_similarity_threshold: thresh,
        rag_top_k: topk,
      };
      setBtn("bs-save", true, "Saving…");
      try {
        await Eth.patch(`/businesses/${bizId}/brain`, body);
        hide("brain-settings"); show("brain-manager");   // saved → back to Brain
      } catch (e) {
        showErr("bs-err", e.detail || "Couldn't save your changes.");
      } finally {
        setBtn("bs-save", false, "Save changes");
      }
    };
  }

  // ---- Catalog manager (Phase C: structured knowledge_items) ----
  async function openCatalog(bizId) {
    hide("brain-manager"); show("catalog-manager");
    let editingId = null;
    let formData = {};       // the item's full data — preserves keys we don't render
    let imageUrl = null;     // current image (data.image_url) for this form

    const renderImage = () => {
      const prev = $("cat-image-preview");
      if (imageUrl) {
        prev.innerHTML = `<img src="${esc(imageUrl)}" alt="">`;
        $("cat-image-btn").textContent = "Replace image";
        $("cat-image-remove").classList.remove("hidden");
      } else {
        prev.innerHTML = "";
        $("cat-image-btn").textContent = "Upload image";
        $("cat-image-remove").classList.add("hidden");
      }
    };

    const closeForm = () => {
      hide("cat-form"); show("cat-add"); hide("cat-err");
      editingId = null; formData = {}; imageUrl = null;
      $("cat-title").value = ""; $("cat-body").value = ""; $("cat-price").value = "";
      $("cat-category").value = ""; $("cat-type").value = "product";
      $("cat-image-file").value = ""; renderImage();
    };
    const openForm = (item) => {
      editingId = item ? item.id : null;
      formData = (item && item.data) ? Object.assign({}, item.data) : {};
      imageUrl = formData.image_url || null;
      $("cat-type").value = item ? item.item_type : "product";
      $("cat-title").value = item ? (item.title || "") : "";
      $("cat-body").value = item ? (item.body || "") : "";
      $("cat-price").value = formData.price || "";
      $("cat-category").value = formData.category || "";
      $("cat-image-file").value = ""; renderImage();
      hide("cat-err"); hide("cat-add"); show("cat-form");
      $("cat-save").textContent = item ? "Update item" : "Save item";
      $("cat-title").focus();
    };

    $("cat-back").onclick = () => { hide("catalog-manager"); show("brain-manager"); };
    $("cat-refresh").onclick = () => loadItems(bizId);
    $("cat-add").onclick = () => openForm(null);
    $("cat-cancel").onclick = closeForm;

    $("cat-image-btn").onclick = () => $("cat-image-file").click();
    $("cat-image-remove").onclick = () => { imageUrl = null; $("cat-image-file").value = ""; renderImage(); };
    $("cat-image-file").onchange = async () => {
      const file = $("cat-image-file").files[0];
      if (!file) return;
      hide("cat-err");
      if (file.size > 5 * 1024 * 1024) { $("cat-image-file").value = ""; return showErr("cat-err", "Image is over the 5 MB limit."); }
      setBtn("cat-image-btn", true, "Uploading…");
      try {
        const res = await Eth.upload(`/knowledge/${bizId}/items/image`, file);
        imageUrl = res.image_url; renderImage();
      } catch (e) {
        showErr("cat-err", e.detail || "Couldn't upload the image.");
      } finally {
        $("cat-image-btn").disabled = false; renderImage();
      }
    };

    const setOrDel = (obj, key, val) => { if (val) obj[key] = val; else delete obj[key]; };

    $("cat-save").onclick = async () => {
      hide("cat-err");
      const title = $("cat-title").value.trim();
      if (!title) return showErr("cat-err", "Please enter a title.");
      const data = Object.assign({}, formData);      // keep unrendered keys (e.g. duration)
      setOrDel(data, "price", $("cat-price").value.trim());
      setOrDel(data, "category", $("cat-category").value.trim());
      setOrDel(data, "image_url", imageUrl);
      const body = {
        item_type: $("cat-type").value,
        title,
        body: $("cat-body").value.trim() || null,
        data: Object.keys(data).length ? data : null,
      };
      setBtn("cat-save", true, "Saving…");
      try {
        if (editingId) await Eth.patch(`/knowledge/${bizId}/items/${editingId}`, body);
        else await Eth.post(`/knowledge/${bizId}/items`, body);
        closeForm();
        await loadItems(bizId);
      } catch (e) {
        showErr("cat-err", e.detail || "Couldn't save the item.");
      } finally {
        setBtn("cat-save", false, editingId ? "Update item" : "Save item");
      }
    };

    // expose the row-edit handler to loadItems via closure
    openCatalog._edit = openForm;
    await loadItems(bizId);
  }

  async function loadItems(bizId) {
    let items = [];
    try { items = await Eth.get(`/knowledge/${bizId}/items`); } catch (e) { /* show empty */ }
    if (!items.length) {
      $("cat-list").innerHTML = empty("No items yet. Add a product, service or FAQ.");
      return;
    }
    $("cat-list").innerHTML = items.map(it => {
      const d = it.data || {};
      const bits = [esc(it.item_type)];
      if (d.category) bits.push(esc(d.category));
      if (d.price) bits.push(esc(d.price));
      if (!it.is_active) bits.push("hidden");
      const dim = it.is_active ? "" : ' style="opacity:.5"';
      const icon = d.image_url
        ? `<div class="lic cat-thumb"><img src="${esc(d.image_url)}" alt=""></div>`
        : `<div class="lic ic-amber">${catIc(it.item_type)}</div>`;
      return `<div class="litem"${dim}>
        ${icon}
        <div class="linfo"><div class="lname">${esc(it.title)}</div>
          <div class="lmeta">${bits.join(" · ")}</div></div>
        <button class="ldel" data-edit="${esc(it.id)}" title="Edit">✎</button>
        <button class="ldel" data-del="${esc(it.id)}" title="Delete">✕</button></div>`;
    }).join("");
    const byId = {};
    items.forEach(it => { byId[it.id] = it; });
    $("cat-list").querySelectorAll("[data-edit]").forEach(btn => {
      btn.onclick = () => openCatalog._edit(byId[btn.dataset.edit]);
    });
    $("cat-list").querySelectorAll("[data-del]").forEach(btn => {
      btn.onclick = async () => {
        btn.disabled = true;
        try { await Eth.del(`/knowledge/${bizId}/items/${btn.dataset.del}`); await loadItems(bizId); }
        catch (e) { btn.disabled = false; }
      };
    });
  }

  function catIc(t) {
    if (t === "faq") return "❓";
    if (t === "policy") return "📋";
    if (t === "service") return "🛠";
    if (t === "menu_item") return "🍽";
    if (t === "general") return "ℹ️";
    return "🏷";
  }

  // ---- Agent manager (Phase D: manage a deployed child agent) ----
  // Resolve the editable field list from the author's child_schema, falling
  // back to whatever keys the saved child_data already has.
  function agentFieldDefs(schema, data) {
    let defs = [];
    if (schema && Array.isArray(schema.fields)) {
      defs = schema.fields.map(f => ({
        key: f.key, label: f.label || f.key, type: f.type || "text",
        placeholder: f.placeholder || "", help: f.help || "",
      }));
    } else if (schema && typeof schema === "object") {
      defs = Object.keys(schema).map(k => ({
        key: k, label: (schema[k] && schema[k].label) || k,
        type: (schema[k] && schema[k].type) || "text",
        placeholder: (schema[k] && schema[k].placeholder) || "", help: "",
      }));
    }
    // include any saved keys the schema didn't declare (never drop owner data)
    const known = new Set(defs.map(d => d.key));
    Object.keys(data || {}).forEach(k => {
      if (!known.has(k)) defs.push({ key: k, label: humanizeKey(k), type: "text", placeholder: "", help: "" });
    });
    return defs;
  }
  function humanizeKey(k) {
    return String(k).replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
  }
  // child_data values may be scalars or structured. Scalars edit as text;
  // arrays/objects edit as JSON in a textarea so they round-trip safely.
  function valueToField(v) {
    if (v == null) return { str: "", json: false };
    if (Array.isArray(v) || typeof v === "object") return { str: JSON.stringify(v, null, 2), json: true };
    return { str: String(v), json: false };
  }
  function fieldToValue(raw, wasJson, original) {
    if (wasJson) { try { return JSON.parse(raw); } catch (e) { return raw; } }
    if (typeof original === "number") { const n = Number(raw); return isNaN(n) ? raw : n; }
    return raw;
  }
  // Render a child_schema-driven form into `containerId` (ids `${prefix}-${i}`),
  // and collect it back into a child_data object. Shared by deploy + manage.
  function renderSchemaFields(containerId, defs, data, prefix) {
    if (!defs.length) { $(containerId).innerHTML = empty("This agent has no editable settings."); return; }
    $(containerId).innerHTML = defs.map((d, i) => {
      const fv = valueToField(data[d.key]);
      const ctrl = (d.type === "multiline" || d.type === "textarea" || fv.json)
        ? `<textarea id="${prefix}-${i}" rows="${fv.json ? 4 : 3}" placeholder="${esc(d.placeholder)}">${esc(fv.str)}</textarea>`
        : `<input id="${prefix}-${i}" type="text" placeholder="${esc(d.placeholder)}" value="${esc(fv.str)}">`;
      const help = d.help ? `<div class="mgr-note" style="text-align:left;margin-top:6px">${esc(d.help)}</div>` : "";
      return `<div class="field"><label for="${prefix}-${i}">${esc(d.label)}</label>${ctrl}${help}</div>`;
    }).join("");
  }
  function collectSchemaFields(defs, data, prefix) {
    const out = Object.assign({}, data);
    defs.forEach((d, i) => {
      const el = $(`${prefix}-${i}`);
      if (el) out[d.key] = fieldToValue(el.value, valueToField(data[d.key]).json, data[d.key]);
    });
    return out;
  }
  // Sensitive fields (calendar creds etc). Declared in child_schema.secret_fields.
  // Values are write-only — never sent back from the server — so inputs are
  // always blank and saving is all-or-nothing (the encrypted blob is replaced
  // whole, so a partial entry can't be merged).
  function secretFieldDefs(schema) {
    if (schema && Array.isArray(schema.secret_fields)) {
      return schema.secret_fields.map(f => ({
        key: f.key, label: f.label || f.key, type: f.type || "text",
        placeholder: f.placeholder || "", help: f.help || "",
      }));
    }
    return [];
  }
  function collectSecrets(defs, prefix) {
    const vals = {}; let filled = 0;
    defs.forEach((d, i) => {
      const el = $(`${prefix}-${i}`);
      const v = el ? el.value.trim() : "";
      if (v) { vals[d.key] = v; filled++; }
    });
    return { vals, filled, total: defs.length };
  }

  async function openAgentManager(bizId, childId) {
    hide("dashboard"); show("agent-manager");
    $("ag-back").onclick = () => { hide("agent-manager"); show("dashboard"); };
    $("ag-refresh").onclick = () => openAgentManager(bizId, childId);
    $("ag-fields").innerHTML = "<div class='lempty'>Loading…</div>";
    hide("ag-err"); hide("ag-guide"); hide("ag-secrets-sec");

    let a;
    try { a = await Eth.get(`/agents/child/${childId}`); }
    catch (e) { $("ag-fields").innerHTML = ""; return showErr("ag-err", "Couldn't load this agent."); }

    const statusTxt = a.status === "unlocked" ? "Unlocked"
      : (a.days_left != null ? `Trial · ${a.days_left} days left` : "Trial");
    $("ag-head").innerHTML = `<div class="ag-name">${esc(a.agent_name)}</div>
      <div class="ag-cat">${esc(a.category)} · ${statusTxt}</div>`;
    if (a.setup_guide) { $("ag-guide").textContent = a.setup_guide; show("ag-guide"); }

    $("ag-active").checked = !!a.is_active;
    $("ag-name").value = a.display_name || "";

    // "Runs on" bot binding — only worth showing when there's a choice to make.
    hide("ag-bot-row");
    let botRowShown = false;
    try {
      const bots = await Eth.get(`/bots?business_id=${bizId}`);
      if (bots && bots.length > 1) {
        $("ag-bot").innerHTML = `<option value="">All bots</option>` + bots.map(bt =>
          `<option value="${esc(bt.id)}">@${esc(bt.bot_username || bt.bot_display_name || "bot")}</option>`).join("");
        $("ag-bot").value = a.assigned_bot_id || "";
        show("ag-bot-row"); botRowShown = true;
      }
    } catch (e) { /* bots list is enrichment — manage still works without it */ }

    const data = a.child_data || {};
    const defs = agentFieldDefs(a.child_schema, data);
    renderSchemaFields("ag-fields", defs, data, "agf");

    // credentials editor (write-only) — only if the agent declares secret_fields
    const sdefs = secretFieldDefs(a.child_schema);
    if (sdefs.length) {
      show("ag-secrets-sec");
      $("ag-secrets-status").textContent = a.has_secrets
        ? "🔒 Connected. Leave blank to keep them, or fill all fields to replace."
        : "Not connected. Fill all fields to enable.";
      renderSchemaFields("ag-secret-fields", sdefs, {}, "ags");
      // disconnect is only meaningful when something is connected
      const disc = $("ag-secrets-disconnect");
      disc.classList.toggle("hidden", !a.has_secrets);
      disc.onclick = async () => {
        const ok = await confirmAction("Disconnect these credentials? The agent will stop using them until you reconnect.");
        if (!ok) return;
        try {
          await Eth.del(`/agents/child/${childId}/secrets`);
          openAgentManager(bizId, childId);   // reload — now shows "Not connected"
        } catch (e) {
          showErr("ag-err", e.detail || "Couldn't disconnect the credentials.");
        }
      };
    }

    $("ag-save").onclick = async () => {
      hide("ag-err");
      const child_data = collectSchemaFields(defs, data, "agf");
      const body = {
        is_active: $("ag-active").checked,
        display_name: $("ag-name").value.trim() || null,
        child_data,
      };
      if (botRowShown) body.assigned_bot_id = $("ag-bot").value;   // "" = all bots
      if (sdefs.length) {
        const s = collectSecrets(sdefs, "ags");
        if (s.filled > 0 && s.filled < s.total)
          return showErr("ag-err", "Enter all credential fields — they're saved together.");
        if (s.filled === s.total) body.child_secrets = s.vals;   // replace the whole blob
      }
      setBtn("ag-save", true, "Saving…");
      try {
        await Eth.patch(`/agents/child/${childId}`, body);
        hide("agent-manager"); show("dashboard");
        boot();   // refresh the dashboard so the row reflects the new state
      } catch (e) {
        showErr("ag-err", e.detail || "Couldn't save your changes.");
        setBtn("ag-save", false, "Save changes");
      }
    };
  }

  // ---- Browse the marketplace and deploy an agent (start a free trial) ----
  function openAgentsBrowse(bizId, deployedAgentIds) {
    hide("dashboard"); show("agents-browse");
    $("ab-back").onclick = () => { hide("agents-browse"); show("dashboard"); };
    $("ab-refresh").onclick = () => loadBrowse(bizId, deployedAgentIds);
    loadBrowse(bizId, deployedAgentIds);
  }

  async function loadBrowse(bizId, deployedAgentIds) {
    $("ab-list").innerHTML = "<div class='lempty'>Loading…</div>";
    hide("ab-err");
    let agents = [];
    try { agents = await Eth.get("/agents"); }
    catch (e) { $("ab-list").innerHTML = ""; return showErr("ab-err", "Couldn't load available agents."); }
    if (!agents.length) {
      $("ab-list").innerHTML = empty("No agents available yet. Check back soon.");
      return;
    }
    const deployed = new Set(deployedAgentIds || []);
    $("ab-list").innerHTML = agents.map(a => {
      const price = a.price_etg > 0 ? `${fmt(a.price_etg)} ETG` : "Free";
      const right = deployed.has(a.id)
        ? `<span class="lpill pl-live">Deployed</span>`
        : `<button class="ab-deploy" data-id="${esc(a.id)}">Deploy</button>`;
      return `<div class="litem">
        <div class="lic ic-blue">${agentIc(a.category)}</div>
        <div class="linfo"><div class="lname">${esc(a.name)}</div>
          <div class="lmeta">${esc(a.tagline || a.category)} · ${price}</div></div>
        ${right}</div>`;
    }).join("");
    $("ab-list").querySelectorAll(".ab-deploy").forEach(btn => {
      btn.onclick = () => openAgentDeploy(bizId, btn.dataset.id);
    });
  }

  async function openAgentDeploy(bizId, agentId) {
    hide("agents-browse"); show("agent-deploy");
    $("ad-back").onclick = () => { hide("agent-deploy"); show("agents-browse"); };
    $("ad-fields").innerHTML = "<div class='lempty'>Loading…</div>";
    hide("ad-err"); hide("ad-guide");

    let a;
    try { a = await Eth.get(`/agents/${agentId}`); }
    catch (e) { $("ad-fields").innerHTML = ""; return showErr("ad-err", "Couldn't load this agent."); }

    const price = a.price_etg > 0 ? `${fmt(a.price_etg)} ETG to unlock` : "Free trial";
    $("ad-head").innerHTML = `<div class="ag-name">${esc(a.name)}</div>
      <div class="ag-cat">${esc(a.category)} · ${price}</div>
      <div class="mgr-note" style="text-align:left;margin-top:8px">${esc(a.tagline || "")}</div>`;
    if (a.setup_guide) { $("ad-guide").textContent = a.setup_guide; show("ad-guide"); }
    $("ad-name").value = "";

    const defs = agentFieldDefs(a.child_schema, {});
    renderSchemaFields("ad-fields", defs, {}, "adf");

    $("ad-deploy").onclick = async () => {
      hide("ad-err");
      const child_data = collectSchemaFields(defs, {}, "adf");
      setBtn("ad-deploy", true, "Deploying…");
      try {
        const res = await Eth.post(`/agents/${agentId}/trial`, { business_id: bizId, child_data });
        const name = $("ad-name").value.trim();
        if (name && res && res.child_agent_id) {
          try { await Eth.patch(`/agents/child/${res.child_agent_id}`, { display_name: name }); } catch (e) { /* non-fatal */ }
        }
        hide("agent-deploy"); show("dashboard");
        boot();   // refresh — the agent now appears under Active Agents
      } catch (e) {
        showErr("ad-err", e.detail || "Couldn't deploy. It may already be deployed to this business.");
        setBtn("ad-deploy", false, "Start free trial");
      }
    };
  }

  // ---- Storefront editor (customer Mini App config) ----
  let SF_SECTIONS = [];

  function setColor(id, hex) {
    // <input type=color> needs #rrggbb; ignore rgba()/short forms
    if (/^#[0-9a-fA-F]{6}$/.test(hex || "")) $(id).value = hex;
  }
  function setSelect(id, value) {
    const el = $(id);
    if (value && Array.from(el.options).some(o => o.value === value)) el.value = value;
  }

  function moveSf(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= SF_SECTIONS.length) return;
    [SF_SECTIONS[i], SF_SECTIONS[j]] = [SF_SECTIONS[j], SF_SECTIONS[i]];
    renderSfSections();
  }

  function renderSfSections() {
    $("se-sections").innerHTML = SF_SECTIONS.map((s, i) => `<div class="litem">
      <div class="linfo"><div class="lname">${esc(s.label)}</div></div>
      <button class="se-move" data-up="${i}" ${i === 0 ? "disabled" : ""} title="Move up">▲</button>
      <button class="se-move" data-down="${i}" ${i === SF_SECTIONS.length - 1 ? "disabled" : ""} title="Move down">▼</button>
      <label class="switch switch-sm"><input type="checkbox" data-vis="${i}" ${s.visible ? "checked" : ""}><span class="slider"></span></label>
    </div>`).join("");
    $("se-sections").querySelectorAll("[data-up]").forEach(b => b.onclick = () => moveSf(+b.dataset.up, -1));
    $("se-sections").querySelectorAll("[data-down]").forEach(b => b.onclick = () => moveSf(+b.dataset.down, 1));
    $("se-sections").querySelectorAll("[data-vis]").forEach(c => c.onchange = () => { SF_SECTIONS[+c.dataset.vis].visible = c.checked; });
  }

  async function openStorefront(bizId) {
    hide("dashboard"); show("storefront-editor");
    $("se-back").onclick = () => { hide("storefront-editor"); show("dashboard"); };
    hide("se-err");

    let cfg;
    try { cfg = await Eth.get(`/businesses/${bizId}/storefront`); }
    catch (e) { return showErr("se-err", "Couldn't load your storefront."); }

    $("se-view").onclick = () => {
      const url = location.origin + cfg.store_url;
      if (Eth.tg && Eth.tg.openLink) Eth.tg.openLink(url); else window.open(url, "_blank");
    };
    $("se-published").checked = !!cfg.is_published;

    // live link + BotFather guide, revealed while Published is ON (works pre-save)
    const fullUrl = location.origin + cfg.store_url;
    $("se-store-url").value = fullUrl;
    const togglePublishInfo = () => $("se-publish-info").classList.toggle("hidden", !$("se-published").checked);
    $("se-published").onchange = togglePublishInfo;
    togglePublishInfo();
    $("se-copy").onclick = async () => {
      try {
        await navigator.clipboard.writeText(fullUrl);
        $("se-copy").textContent = "Copied ✓";
      } catch (e) {
        $("se-store-url").select();
        try { document.execCommand("copy"); $("se-copy").textContent = "Copied ✓"; } catch (e2) {}
      }
      setTimeout(() => { $("se-copy").textContent = "Copy"; }, 1600);
    };

    setColor("se-primary", cfg.theme.primary);
    setColor("se-accent", cfg.theme.accent);
    setColor("se-bg", cfg.theme.bg);
    setColor("se-text", cfg.theme.text);
    setSelect("se-font-heading", cfg.font_heading || cfg.theme.font_heading);
    setSelect("se-font-body", cfg.font_body || cfg.theme.font_body);
    $("se-tagline").value = cfg.tagline || "";
    $("se-about").value = cfg.about || "";
    $("se-cta").value = cfg.cta || "";
    $("se-hours").value = cfg.hours || "";
    $("se-address").value = cfg.address || "";
    $("se-map").value = (cfg.latitude != null && cfg.longitude != null)
      ? `${cfg.latitude}, ${cfg.longitude}` : "";
    $("se-vibe").value = cfg.ui_child_prompt || "";

    // AI generation — "Create my page" (full: theme + copy + layout from the
    // owner's brief), or "Only theme" / "Only text" for a partial refresh.
    // Applies suggestions to the editor fields; the owner reviews and Saves.
    async function runGenerate(kind, btnId) {
      const btn = $(btnId), note = $("se-ai-note"), label = btn.textContent;
      btn.disabled = true; btn.textContent = "Generating…"; note.classList.add("hidden");
      try {
        const vibe = $("se-vibe").value.trim();
        const res = await Eth.post(`/businesses/${bizId}/storefront/generate`,
          { kind, vibe: vibe || null });
        const t = res.theme || {};
        setColor("se-primary", t.primary); setColor("se-accent", t.accent);
        setColor("se-bg", t.bg); setColor("se-text", t.text);
        setSelect("se-font-heading", t.font_heading); setSelect("se-font-body", t.font_body);
        if (res.tagline) $("se-tagline").value = res.tagline;
        if (res.about) $("se-about").value = res.about;
        if (res.cta) $("se-cta").value = res.cta;
        if (res.hours) $("se-hours").value = res.hours;
        if (res.sections && res.sections.length) {   // "full" also lays out the page
          SF_SECTIONS = res.sections.map(s => ({ type: s.type, label: s.label, visible: s.visible }));
          renderSfSections();
        }
        const cost = res.charged ? ` (−${res.charged} ETG)` : "";
        note.textContent = res.source === "ai"
          ? `✨ Generated — review and tap Save to apply.${cost}`
          : "Used a starter suggestion (AI was busy) — tweak and Save.";
        note.classList.remove("hidden");
      } catch (e) {
        note.textContent = e.detail || "Couldn't generate — please try again.";
        note.classList.remove("hidden");
      } finally { btn.disabled = false; btn.textContent = label; }
    }
    $("se-gen-full").onclick = () => runGenerate("full", "se-gen-full");
    $("se-gen-page").onclick = () => runGenerate("page", "se-gen-page");
    $("se-gen-content").onclick = () => runGenerate("content", "se-gen-content");

    // logo upload (sets Business.logo_url; the store header + landing use it)
    let logoUrl = cfg.logo_url || null;
    const renderLogo = () => {
      const prev = $("se-logo-preview");
      if (logoUrl) {
        prev.innerHTML = `<img src="${esc(logoUrl)}" alt="">`;
        $("se-logo-btn").textContent = "Replace logo";
        $("se-logo-remove").classList.remove("hidden");
      } else {
        prev.innerHTML = ""; $("se-logo-btn").textContent = "Upload logo";
        $("se-logo-remove").classList.add("hidden");
      }
    };
    renderLogo();
    $("se-logo-btn").onclick = () => $("se-logo-file").click();
    $("se-logo-remove").onclick = () => { logoUrl = null; $("se-logo-file").value = ""; renderLogo(); };
    $("se-logo-file").onchange = async () => {
      const file = $("se-logo-file").files[0];
      if (!file) return;
      hide("se-err");
      if (file.size > 5 * 1024 * 1024) { $("se-logo-file").value = ""; return showErr("se-err", "Logo is over the 5 MB limit."); }
      setBtn("se-logo-btn", true, "Uploading…");
      try { const res = await Eth.upload(`/knowledge/${bizId}/items/image`, file); logoUrl = res.image_url; }
      catch (e) { showErr("se-err", e.detail || "Couldn't upload the logo."); }
      finally { $("se-logo-btn").disabled = false; renderLogo(); }
    };

    SF_SECTIONS = (cfg.sections || []).map(s => ({ type: s.type, label: s.label, visible: s.visible }));
    renderSfSections();

    $("se-save").onclick = async () => {
      hide("se-err");
      const body = {
        is_published: $("se-published").checked,
        theme: {
          primary: $("se-primary").value, accent: $("se-accent").value,
          bg: $("se-bg").value, text: $("se-text").value,
        },
        tagline: $("se-tagline").value.trim(),
        about: $("se-about").value.trim(),
        cta: $("se-cta").value.trim(),
        hours: $("se-hours").value.trim(),
        address: $("se-address").value.trim(),
        map_pin: $("se-map").value.trim(),
        logo_url: logoUrl,
        font_heading: $("se-font-heading").value,
        font_body: $("se-font-body").value,
        ui_child_prompt: $("se-vibe").value.trim(),
        sections: SF_SECTIONS.map((s, i) => ({ type: s.type, visible: s.visible, order: i })),
      };
      setBtn("se-save", true, "Saving…");
      try {
        await Eth.patch(`/businesses/${bizId}/storefront`, body);
        hide("storefront-editor"); show("dashboard");
      } catch (e) {
        showErr("se-err", e.detail || "Couldn't save your storefront.");
        setBtn("se-save", false, "Save changes");
      }
    };
  }

  // ---- Website editor (SEO landing page config) ----
  async function openWebsite(bizId) {
    hide("dashboard"); show("website-editor");
    $("we-back").onclick = () => { hide("website-editor"); show("dashboard"); };
    hide("we-err");

    let cfg;
    try { cfg = await Eth.get(`/businesses/${bizId}/website`); }
    catch (e) { return showErr("we-err", "Couldn't load your website settings."); }

    const fullUrl = location.origin + cfg.website_url;
    $("we-view").onclick = () => {
      if (Eth.tg && Eth.tg.openLink) Eth.tg.openLink(fullUrl); else window.open(fullUrl, "_blank");
    };
    $("we-url").value = fullUrl;
    const togglePub = () => $("we-publish-info").classList.toggle("hidden", !$("we-published").checked);
    $("we-published").checked = !!cfg.is_published;
    $("we-published").onchange = togglePub;
    togglePub();
    $("we-copy").onclick = async () => {
      try { await navigator.clipboard.writeText(fullUrl); $("we-copy").textContent = "Copied ✓"; }
      catch (e) { $("we-url").select(); try { document.execCommand("copy"); $("we-copy").textContent = "Copied ✓"; } catch (e2) {} }
      setTimeout(() => { $("we-copy").textContent = "Copy"; }, 1600);
    };

    $("we-title").value = cfg.title || "";
    $("we-meta").value = cfg.meta_description || "";
    $("we-headline").value = cfg.hero_headline || "";
    $("we-sub").value = cfg.hero_subheadline || "";
    $("we-keywords").value = (cfg.seo_keywords || []).join(", ");

    // AI "Generate SEO" — fills title/meta/hero/keywords; owner reviews & Saves.
    $("we-gen-seo").onclick = async () => {
      const btn = $("we-gen-seo"), note = $("we-ai-note"), label = btn.textContent;
      btn.disabled = true; btn.textContent = "Generating…"; note.classList.add("hidden");
      try {
        const brief = $("we-vibe").value.trim();
        const res = await Eth.post(`/businesses/${bizId}/website/generate`,
          brief ? { vibe: brief } : {});
        if (res.title) $("we-title").value = res.title;
        if (res.meta_description) $("we-meta").value = res.meta_description;
        if (res.hero_headline) $("we-headline").value = res.hero_headline;
        if (res.hero_subheadline) $("we-sub").value = res.hero_subheadline;
        if (res.keywords && res.keywords.length) $("we-keywords").value = res.keywords.join(", ");
        const cost = res.charged ? ` (−${res.charged} ETG)` : "";
        note.textContent = res.source === "ai"
          ? `✨ Generated — review and tap Save to apply.${cost}`
          : "Used a starter suggestion (AI was busy) — tweak and Save.";
        note.classList.remove("hidden");
      } catch (e) {
        note.textContent = e.detail || "Couldn't generate — please try again.";
        note.classList.remove("hidden");
      } finally { btn.disabled = false; btn.textContent = label; }
    };

    let ogUrl = cfg.og_image_url || null;
    const renderOg = () => {
      const prev = $("we-og-preview");
      if (ogUrl) {
        prev.innerHTML = `<img src="${esc(ogUrl)}" alt="">`;
        $("we-og-btn").textContent = "Replace image";
        $("we-og-remove").classList.remove("hidden");
      } else {
        prev.innerHTML = ""; $("we-og-btn").textContent = "Upload image";
        $("we-og-remove").classList.add("hidden");
      }
    };
    renderOg();
    $("we-og-btn").onclick = () => $("we-og-file").click();
    $("we-og-remove").onclick = () => { ogUrl = null; $("we-og-file").value = ""; renderOg(); };
    $("we-og-file").onchange = async () => {
      const file = $("we-og-file").files[0];
      if (!file) return;
      hide("we-err");
      if (file.size > 5 * 1024 * 1024) { $("we-og-file").value = ""; return showErr("we-err", "Image is over the 5 MB limit."); }
      setBtn("we-og-btn", true, "Uploading…");
      try { const res = await Eth.upload(`/knowledge/${bizId}/items/image`, file); ogUrl = res.image_url; }
      catch (e) { showErr("we-err", e.detail || "Couldn't upload the image."); }
      finally { $("we-og-btn").disabled = false; renderOg(); }
    };

    $("we-save").onclick = async () => {
      hide("we-err");
      const body = {
        is_published: $("we-published").checked,
        title: $("we-title").value.trim(),
        meta_description: $("we-meta").value.trim(),
        hero_headline: $("we-headline").value.trim(),
        hero_subheadline: $("we-sub").value.trim(),
        seo_keywords: $("we-keywords").value.split(",").map(s => s.trim()).filter(Boolean),
        og_image_url: ogUrl,
      };
      setBtn("we-save", true, "Saving…");
      try {
        await Eth.patch(`/businesses/${bizId}/website`, body);
        hide("website-editor"); show("dashboard");
      } catch (e) {
        showErr("we-err", e.detail || "Couldn't save your website.");
        setBtn("we-save", false, "Save changes");
      }
    };

    await loadDomain(bizId);
  }

  // ---- custom domain (within the website editor) ----
  async function loadDomain(bizId) {
    let dom = { domain: null };
    try { dom = await Eth.get(`/businesses/${bizId}/domain`); } catch (e) { /* show connect form */ }
    renderDomain(bizId, dom);
  }

  function dnsRow(r) {
    return `<div class="dns-rec">
      <div class="dns-top"><span class="dns-type">${esc(r.type)}</span><span class="dns-purpose">${esc(r.purpose)}</span></div>
      <div class="dns-kv"><span class="dns-k">Host</span><code>${esc(r.host)}</code><button class="dns-copy" data-c="${esc(r.host)}" title="Copy">⧉</button></div>
      <div class="dns-kv"><span class="dns-k">Value</span><code>${esc(r.value)}</code><button class="dns-copy" data-c="${esc(r.value)}" title="Copy">⧉</button></div>
    </div>`;
  }

  function renderDomain(bizId, dom) {
    const el = $("dom-panel");
    if (!dom || !dom.domain) {
      el.innerHTML = `<div class="mgr-note" style="text-align:left;margin-top:0">Connect your own domain (e.g. <b>yourbrand.com</b>) so customers reach your site at your brand.</div>
        <div class="field" style="margin-top:10px"><input id="dom-input" type="text" placeholder="shop.yourbrand.com" autocapitalize="off" spellcheck="false"></div>
        <button class="onb-btn" id="dom-connect">Connect domain</button>`;
      $("dom-connect").onclick = async () => {
        const d = $("dom-input").value.trim();
        if (!d) return;
        hide("we-err"); setBtn("dom-connect", true, "Connecting…");
        try { renderDomain(bizId, await Eth.post(`/businesses/${bizId}/domain`, { domain: d })); }
        catch (e) { showErr("we-err", e.detail || "Couldn't add that domain."); setBtn("dom-connect", false, "Connect domain"); }
      };
      return;
    }
    const pill = dom.is_verified
      ? `<span class="lpill pl-live">✓ Verified</span>`
      : `<span class="lpill pl-sync">Pending DNS</span>`;
    let html = `<div class="dom-head"><code class="dom-name">${esc(dom.domain)}</code>${pill}</div>`;
    if (dom.is_verified) {
      html += `<div class="mgr-note" style="text-align:left">Your site is live at <b>${esc(dom.live_url)}</b> once the certificate finishes provisioning (usually a few minutes).</div>`;
    } else {
      html += `<div class="mgr-note" style="text-align:left;margin-top:0">Add these records at your domain registrar, then tap Verify:</div>`;
      html += (dom.dns_records || []).map(dnsRow).join("");
      html += `<div class="steps-note" style="margin-top:10px">We enable HTTPS for your domain after verification — it goes live automatically, usually within an hour.</div>`;
      html += `<button class="onb-btn" id="dom-verify" style="margin-top:10px">I've added the records — Verify</button>`;
    }
    html += `<button class="onb-btn-ghost" id="dom-remove" style="margin-top:8px">Remove domain</button>`;
    el.innerHTML = html;

    el.querySelectorAll(".dns-copy").forEach(b => b.onclick = () => {
      if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.c);
      b.textContent = "✓"; setTimeout(() => { b.textContent = "⧉"; }, 1200);
    });
    if ($("dom-verify")) $("dom-verify").onclick = async () => {
      hide("we-err"); setBtn("dom-verify", true, "Checking DNS…");
      try {
        const res = await Eth.post(`/businesses/${bizId}/domain/verify`, {});
        if (!res.is_verified) showErr("we-err", "We couldn't find the TXT record yet. DNS changes can take a few minutes — try again shortly.");
        renderDomain(bizId, res);
      } catch (e) { showErr("we-err", e.detail || "Verification failed."); setBtn("dom-verify", false, "I've added the records — Verify"); }
    };
    $("dom-remove").onclick = async () => {
      const ok = await confirmAction("Remove this custom domain? Your site stays available at its Ethiogram link.");
      if (!ok) return;
      try { await Eth.del(`/businesses/${bizId}/domain`); renderDomain(bizId, { domain: null }); }
      catch (e) { showErr("we-err", e.detail || "Couldn't remove the domain."); }
    };
  }

  // ---- Billing editor (who pays, free cap, pricing + per-customer usage) ----
  async function openBilling(bizId) {
    hide("dashboard"); show("billing-editor");
    $("bl-back").onclick = () => { hide("billing-editor"); show("dashboard"); };
    hide("bl-err");

    let cfg;
    try { cfg = await Eth.get(`/businesses/${bizId}/billing`); }
    catch (e) { return showErr("bl-err", "Couldn't load billing settings."); }

    $("bl-policy").value = cfg.billing_policy;
    $("bl-limit").value = cfg.per_user_monthly_limit == null ? "" : cfg.per_user_monthly_limit;
    $("bl-action").value = cfg.per_user_limit_action;
    $("bl-price").value = cfg.service_price;
    $("bl-markup").value = cfg.business_markup;

    $("bl-save").onclick = async () => {
      hide("bl-err");
      const limStr = $("bl-limit").value.trim();
      const body = {
        billing_policy: $("bl-policy").value,
        per_user_monthly_limit: limStr === "" ? null : Math.max(0, parseInt(limStr, 10) || 0),
        per_user_limit_action: $("bl-action").value,
        service_price: Math.max(0, parseInt($("bl-price").value || "0", 10) || 0),
        business_markup: Math.max(0, parseInt($("bl-markup").value || "0", 10) || 0),
      };
      setBtn("bl-save", true, "Saving…");
      try {
        await Eth.patch(`/businesses/${bizId}/billing`, body);
        hide("billing-editor"); show("dashboard");
      } catch (e) {
        showErr("bl-err", e.detail || "Couldn't save billing settings.");
        setBtn("bl-save", false, "Save changes");
      }
    };

    $("bl-usage-search").oninput = admDebounce(() => loadBillingUsage(bizId), 300);
    await loadBillingUsage(bizId);
  }

  async function loadBillingUsage(bizId) {
    const q = $("bl-usage-search").value.trim();
    $("bl-usage-list").innerHTML = `<div class="lempty">Loading…</div>`;
    let res;
    try { res = await Eth.get(`/businesses/${bizId}/users/usage` + (q ? "?search=" + encodeURIComponent(q) : "")); }
    catch (e) { $("bl-usage-list").innerHTML = empty("Couldn't load usage."); return; }
    if (!res.items.length) { $("bl-usage-list").innerHTML = empty("No customer activity yet."); return; }
    $("bl-usage-list").innerHTML = res.items.map(u => {
      const name = u.customer_name || u.customer_id;
      const meta = `${fmt(u.monthly_etg_used)} ETG this month · bal ${fmt(u.etg_balance)}`;
      return `<div class="litem">
        <div class="lic ic-blue">👤</div>
        <div class="linfo"><div class="lname">${esc(name)}</div>
          <div class="lmeta">${meta}</div></div>
        <button class="adm-act" data-credit="${esc(u.conversation_id)}" data-name="${esc(name)}">＋ Credit</button></div>`;
    }).join("");
    $("bl-usage-list").querySelectorAll("[data-credit]").forEach(btn =>
      btn.onclick = () => openCredit(bizId, btn.dataset.credit, btn.dataset.name));
  }

  function openCredit(bizId, convId, name) {
    $("bl-credit-name").textContent = "Add credit · " + name;
    $("bl-credit-amount").value = "";
    hide("bl-credit-err"); show("bl-credit");
    try { $("bl-credit-amount").focus(); } catch (e) {}
    $("bl-credit-cancel").onclick = () => hide("bl-credit");
    $("bl-credit-save").onclick = async () => {
      hide("bl-credit-err");
      const amt = parseInt($("bl-credit-amount").value, 10);
      if (!amt || amt <= 0) { showErr("bl-credit-err", "Enter an amount greater than 0."); return; }
      $("bl-credit-save").disabled = true;
      try {
        await Eth.post(`/businesses/${bizId}/users/${convId}/credit`, { amount: amt });
        hide("bl-credit");
        await loadBillingUsage(bizId);
      } catch (e) {
        showErr("bl-credit-err", e.detail || "Couldn't add credit.");
      } finally {
        $("bl-credit-save").disabled = false;
      }
    };
  }

  async function loadDocs(bizId) {
    let docs = [];
    try { docs = await Eth.get(`/knowledge/${bizId}/documents`); } catch (e) { /* show empty */ }
    if (!docs.length) {
      $("bm-list").innerHTML = empty("No documents yet. Upload a menu, price list, FAQ or policy.");
      return;
    }
    $("bm-list").innerHTML = docs.map(d => `<div class="litem">
      <div class="lic ${brainIc(d.status)}">📄</div>
      <div class="linfo"><div class="lname">${esc(d.filename)}</div>
        <div class="lmeta">${esc(d.status)} · ${fmt(d.chunk_count)} chunks</div></div>
      ${brainPill(d.status)}
      <button class="ldel" data-id="${esc(d.id)}" title="Delete">✕</button></div>`).join("");
    $("bm-list").querySelectorAll(".ldel").forEach(btn => {
      btn.onclick = async () => {
        btn.disabled = true;
        try { await Eth.del(`/knowledge/${bizId}/documents/${btn.dataset.id}`); await loadDocs(bizId); }
        catch (e) { btn.disabled = false; }
      };
    });
  }

  function render(ov, orders, docs, wallet) {
    const b = ov.business;

    // greeting
    $("greet-hi").textContent = `Selam, ${Eth.firstName()} 👋`;
    $("greet-sub").textContent = b.name;

    // wallet + runway
    const balance = b.etg_balance || 0;
    const dailyBurn = (ov.total_etg_7d || 0) / 7;
    const runwayDays = dailyBurn > 0 ? Math.round(balance / dailyBurn) : null;
    $("w-bal").textContent = fmt(balance);
    $("w-runway").innerHTML = runwayDays != null
      ? `≈ <b>${runwayDays} days</b> of runway · ~${Math.round(dailyBurn)} ETG/day`
      : "No usage in the last 7 days";
    $("w-fill").style.width = (runwayDays != null ? Math.min(100, runwayDays / 30 * 100) : 100) + "%";
    if (balance < 500 || (runwayDays != null && runwayDays <= 7)) show("low-balance");
    $("btn-auto").textContent = wallet
      ? `Auto-recharge: ${wallet.auto_recharge_enabled ? "On" : "Off"}`
      : "Auto-recharge";
    $("btn-recharge").onclick = () => openRecharge(b.id);

    // quick stats
    const messagesToday = (ov.bots || []).reduce((s, x) => s + (x.messages_24h || 0), 0);
    $("qstats").innerHTML = [
      qstat("ic-amber", "💬", messagesToday, "Messages today"),
      qstat("ic-green", "🛍", ov.total_orders, `Orders · ${ov.pending_orders} pending`),
      qstat("ic-blue", "👥", ov.total_customers_7d, "Customers · 7d"),
      qstat("ic-amber", "💸", ov.total_etg_7d, "ETG spent · 7d"),
    ].join("");

    // usage by type (7d totals — not per-day; see backend note)
    renderUsage(ov.usage_7d || []);

    // recent orders
    $("orders").innerHTML = orders.length
      ? orders.slice(0, 5).map(o => litem("🧾", o.payment_status === "completed" ? "ic-green" : "ic-amber",
          esc(o.order_number),
          `${esc(o.customer_name || "Customer")} · ${esc(o.payment_status)} · ${timeAgo(o.created_at)}`,
          `<span class="lamt" style="color:${o.payment_status === "completed" ? "var(--green)" : "var(--amber-deep)"}">${fmt(o.total)} ${esc(o.currency)}</span>`
        )).join("")
      : empty("No orders yet.");

    // business brain
    const bs = ov.brain_summary || {};
    let brainHtml = litem("🧠", "ic-amber", `Persona: ${esc(bs.persona_name || "default")}`,
      `${fmt(bs.total_items || 0)} items · ${fmt(bs.total_chunks || 0)} chunks embedded`, "");
    brainHtml += docs.length
      ? docs.slice(0, 6).map(d => litem("📄", brainIc(d.status), esc(d.filename),
          `${esc(d.status)} · ${fmt(d.chunk_count)} chunks`, brainPill(d.status))).join("")
      : empty("No documents uploaded yet.");
    $("brain").innerHTML = brainHtml;
    $("brain-manage").onclick = () => openBrainManager(b.id);

    // storefront entry
    $("storefront-card").innerHTML = `<div class="litem" id="sf-row" style="cursor:pointer">
      <div class="lic ic-amber">🏪</div>
      <div class="linfo"><div class="lname">Your public store</div>
        <div class="lmeta">Theme, sections, tagline &amp; publish</div></div>
      <span class="lchev">›</span></div>`;
    $("sf-row").onclick = () => openStorefront(b.id);
    $("store-customize").onclick = () => openStorefront(b.id);

    // website (SEO landing) entry
    $("website-card").innerHTML = `<div class="litem" id="web-row" style="cursor:pointer">
      <div class="lic ic-blue">🌐</div>
      <div class="linfo"><div class="lname">Your website</div>
        <div class="lmeta">SEO title, description &amp; publish</div></div>
      <span class="lchev">›</span></div>`;
    $("web-row").onclick = () => openWebsite(b.id);
    $("web-customize").onclick = () => openWebsite(b.id);

    // billing entry
    $("billing-card").innerHTML = `<div class="litem" id="bl-row" style="cursor:pointer">
      <div class="lic ic-green">💳</div>
      <div class="linfo"><div class="lname">Billing &amp; limits</div>
        <div class="lmeta">Who pays · free cap · pricing</div></div>
      <span class="lchev">›</span></div>`;
    $("bl-row").onclick = () => openBilling(b.id);
    $("billing-customize").onclick = () => openBilling(b.id);

    // invite & earn — share the referral link; both sides get ETG on activation
    if (REFERRAL.link) {
      show("invite-sec");
      const shareUrl = "https://t.me/share/url?url=" + encodeURIComponent(REFERRAL.link)
        + "&text=" + encodeURIComponent("Get an AI bot for your business on Ethiogram 🇪🇹");
      $("invite-card").innerHTML = `<div class="litem">
        <div class="lic ic-amber">🎁</div>
        <div class="linfo"><div class="lname">Invite a business, you both get ${fmt(REFERRAL.bonus)} ETG</div>
          <div class="lmeta">Paid when their first bot goes live.</div></div></div>
        <div class="copy-row" style="padding:0 14px 12px">
          <input id="ref-link" type="text" readonly value="${esc(REFERRAL.link)}">
          <button class="copy-btn" id="ref-copy">Copy</button>
          <button class="copy-btn" id="ref-share">Share</button>
        </div>`;
      $("ref-copy").onclick = async () => {
        try { await navigator.clipboard.writeText(REFERRAL.link); $("ref-copy").textContent = "Copied ✓"; }
        catch (e) { $("ref-link").select(); try { document.execCommand("copy"); $("ref-copy").textContent = "Copied ✓"; } catch (e2) {} }
        setTimeout(() => { $("ref-copy").textContent = "Copy"; }, 1600);
      };
      $("ref-share").onclick = () => {
        if (Eth.tg && Eth.tg.openTelegramLink) Eth.tg.openTelegramLink(shareUrl);
        else window.open(shareUrl, "_blank");
      };
    }

    // active agents — each row opens the manage view (pause/rename/config)
    const agents = ov.active_agents || [];
    $("agents").innerHTML = agents.length
      ? agents.map(a => {
          const meta = a.status === "trial" && a.days_left != null
            ? `${esc(a.category)} · Trial · ${a.days_left} days left`
            : esc(a.category);
          const pill = !a.is_active
            ? `<span class="lpill pl-fail">Paused</span>`
            : a.status === "unlocked"
              ? `<span class="lpill pl-live">Active</span>`
              : `<span class="lpill pl-sync">Trial</span>`;
          return `<div class="litem" data-agent="${esc(a.id)}" style="cursor:pointer">
            <div class="lic ic-blue">${agentIc(a.category)}</div>
            <div class="linfo"><div class="lname">${esc(a.display_name || a.category)}</div>
              <div class="lmeta">${meta}</div></div>
            ${pill}<span class="lchev">›</span></div>`;
        }).join("")
      : empty("No agents deployed yet.");
    $("agents").querySelectorAll("[data-agent]").forEach(row => {
      row.onclick = () => openAgentManager(b.id, row.dataset.agent);
    });
    $("agents-browse-link").onclick = () => openAgentsBrowse(b.id, agents.map(a => a.agent_id));

    // bots — list + an always-present "Connect a bot" action (covers the case
    // where the owner skipped bot setup during onboarding).
    const botList = (ov.bots && ov.bots.length)
      ? ov.bots.map(bot => {
          const live = bot.status === "active"
            ? `<span class="live-tag"><span class="live-pulse"></span> Live</span>`
            : `<span class="lpill pl-sync">${esc(bot.status)}</span>`;
          return litem((bot.bot_username || "?")[0].toUpperCase(), "ic-amber",
            "@" + esc(bot.bot_username || "bot"), `${fmt(bot.messages_7d)} msgs · 7d`, live);
        }).join("")
      : empty("No bots connected yet.");
    const connectRow = `<div class="litem" id="bots-connect" style="cursor:pointer">
      <div class="lic ic-amber">＋</div>
      <div class="linfo"><div class="lname" style="color:var(--amber-deep)">Connect a bot</div>
      <div class="lmeta">Add a Telegram bot to this business</div></div></div>`;
    const transferRow = `<div class="litem" id="bots-transfer" style="cursor:pointer">
      <div class="lic ic-blue">⇄</div>
      <div class="linfo"><div class="lname">Transfer business</div>
      <div class="lmeta">Hand ownership to another person</div></div></div>`;
    $("bots").innerHTML = botList + connectRow + transferRow;
    $("bots-connect").onclick = () =>
      connectBotFlow(b.id, () => { hide("wiz-bot"); show("dashboard"); });
    $("bots-transfer").onclick = () => openTransfer(b.id);
  }

  // ---- transfer business (ownership hand-off) ----
  async function openTransfer(bizId) {
    hide("dashboard"); show("transfer-editor");
    hide("tr-err");
    $("tr-back").onclick = () => { hide("transfer-editor"); show("dashboard"); };

    function renderState(st) {
      const pend = $("tr-pending");
      if (st && st.status === "pending") {
        const known = st.recipient_has_account ? " (has an account)" : " — they'll get it when they sign up";
        pend.innerHTML = `<div><b>Pending transfer</b><br>To <b>${esc(st.to_value)}</b>${esc(known)}.</div>
          <button class="onb-btn-ghost" id="tr-cancel" style="margin-top:8px">Cancel transfer</button>`;
        pend.classList.remove("hidden");
        $("tr-cancel").onclick = async () => {
          try { await Eth.del(`/transfers/business/${bizId}`); openTransfer(bizId); }
          catch (e) { notify(e.detail || "Couldn't cancel."); }
        };
      } else {
        pend.classList.add("hidden"); pend.innerHTML = "";
      }
    }

    try { renderState(await Eth.get(`/transfers/business/${bizId}`)); }
    catch (e) { return showErr("tr-err", "Couldn't load transfer status."); }

    $("tr-kind").onchange = () => {
      const k = $("tr-kind").value;
      $("tr-value").placeholder = k === "email" ? "name@example.com" : k === "telegram_id" ? "123456789" : "@username";
    };
    $("tr-send").onclick = async () => {
      hide("tr-err");
      const to_kind = $("tr-kind").value, to_value = $("tr-value").value.trim();
      if (!to_value) return showErr("tr-err", "Enter the recipient.");
      if (!(await confirmAction("Send a transfer request? You'll keep access until they accept."))) return;
      setBtn("tr-send", true, "Sending…");
      try {
        const st = await Eth.post(`/transfers/business/${bizId}`, { to_kind, to_value });
        $("tr-value").value = "";
        renderState(st);
        notify("Transfer request sent. They'll be asked to accept.");
      } catch (e) { showErr("tr-err", e.detail || "Couldn't send the transfer."); }
      finally { setBtn("tr-send", false, "Send transfer request"); }
    };
  }

  // ---- incoming transfers (someone wants to hand a business to me) ----
  async function renderIncoming() {
    const box = $("incoming-transfers");
    if (!box) return;
    let items;
    try { items = await Eth.get("/transfers/incoming"); }
    catch (e) { box.innerHTML = ""; return; }
    if (!items || !items.length) { box.innerHTML = ""; return; }
    box.innerHTML = items.map(t => `<div class="xfer-card" data-id="${esc(t.id)}">
      <div class="xfer-txt">🤝 <b>${esc(t.from_name || "Someone")}</b> wants to transfer <b>${esc(t.business_name)}</b> to you.</div>
      <div class="xfer-actions">
        <button class="onb-btn xfer-accept">Accept</button>
        <button class="onb-btn-ghost xfer-decline">Decline</button>
      </div></div>`).join("");
    box.querySelectorAll(".xfer-card").forEach(card => {
      const id = card.dataset.id;
      card.querySelector(".xfer-accept").onclick = async () => {
        if (!(await confirmAction("Accept this business? It becomes yours."))) return;
        try { await Eth.post(`/transfers/incoming/${id}/accept`, {}); boot(); }
        catch (e) { notify(e.detail || "Couldn't accept."); }
      };
      card.querySelector(".xfer-decline").onclick = async () => {
        try { await Eth.post(`/transfers/incoming/${id}/decline`, {}); renderIncoming(); }
        catch (e) { notify(e.detail || "Couldn't decline."); }
      };
    });
  }

  function renderUsage(rows) {
    const labels = { ai_reply: ["Q&A", "var(--amber)"], ocr_extraction: ["OCR", "var(--blue)"],
      calendar_booking: ["Bookings", "var(--green)"], rag_search: ["Search", "var(--text-3)"] };
    if (!rows.length) { $("usage").innerHTML = empty("No usage in the last 7 days."); return; }
    const max = Math.max(...rows.map(r => r.etg || 0), 1);
    $("usage").innerHTML = rows.map(r => {
      const [lbl, color] = labels[r.action_type] || [r.action_type, "var(--text-3)"];
      const w = Math.max(3, Math.round((r.etg || 0) / max * 100));
      return `<div class="ubar-row"><div class="ubar-lbl">${esc(lbl)}</div>
        <div class="ubar-track"><div class="ubar-fill" style="width:${w}%;background:${color}"></div></div>
        <div class="ubar-val">${fmt(r.etg)} ETG</div></div>`;
    }).join("");
  }

  // ---- tiny view helpers ----
  function qstat(ic, emoji, val, name) {
    return `<div class="qstat"><div class="qstat-top"><div class="qstat-ic ${ic}">${emoji}</div></div>
      <div class="qstat-val">${fmt(val)}</div><div class="qstat-name">${esc(name)}</div></div>`;
  }
  function litem(icon, icClass, name, meta, right) {
    return `<div class="litem"><div class="lic ${icClass}">${icon}</div>
      <div class="linfo"><div class="lname">${name}</div><div class="lmeta">${meta}</div></div>${right || ""}</div>`;
  }
  function empty(msg) { return `<div class="lempty">${esc(msg)}</div>`; }
  function brainIc(s) { return s === "failed" ? "ic-red" : s === "completed" ? "ic-green" : "ic-amber"; }
  function brainPill(s) {
    if (s === "completed") return `<span class="lpill pl-live">Live</span>`;
    if (s === "failed") return `<span class="lpill pl-fail">Failed</span>`;
    return `<span class="lpill pl-sync">Syncing</span>`;
  }
  function agentIc(cat) {
    const c = (cat || "").toLowerCase();
    if (/account|receipt|finance/.test(c)) return "🧾";
    if (/concierge|book|appoint|calendar/.test(c)) return "📅";
    return "🤖";
  }

  // ===================== ADMIN DASHBOARD =====================
  function admDebounce(fn, ms) {
    let t; return () => { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  async function bootAdmin() {
    show("admin");
    document.querySelectorAll(".adm-tab").forEach(t => t.onclick = () => switchAdminTab(t.dataset.tab));
    $("adm-biz-search").oninput = admDebounce(loadAdminBusinesses, 300);
    $("adm-biz-status").onchange = loadAdminBusinesses;
    $("adm-user-search").oninput = admDebounce(loadAdminUsers, 300);
    await loadAdminStats();
  }

  function switchAdminTab(tab) {
    ["overview", "businesses", "users", "agents", "models", "system"].forEach(t =>
      $("adm-" + t).classList.toggle("hidden", t !== tab));
    document.querySelectorAll(".adm-tab").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
    if (tab === "businesses") loadAdminBusinesses();
    else if (tab === "users") loadAdminUsers();
    else if (tab === "agents") loadAdminAgents();
    else if (tab === "models") loadAdminModels();
    else if (tab === "system") loadAdminSystem();
  }

  async function loadAdminModels() {
    $("adm-models-list").innerHTML = `<div class="lempty">Loading…</div>`;
    let models;
    try { models = await Eth.get("/admin/models/pricing"); }
    catch (e) { $("adm-models-list").innerHTML = empty("Couldn't load models."); return; }
    if (!models.length) { $("adm-models-list").innerHTML = empty("No models in the catalog."); return; }
    $("adm-models-list").innerHTML = models.map(m => `<div class="litem adm-model-row" data-model="${esc(m.model_id)}">
      <div class="lic ${m.is_enabled ? "ic-green" : "ic-amber"}">🤖</div>
      <div class="linfo"><div class="lname">${esc(m.display_name)}</div>
        <div class="lmeta">${esc(m.provider)} · ${esc(m.tier)}${m.is_enabled ? "" : " · disabled"}</div></div>
      <div class="adm-price">
        <label>in<input type="number" min="0" class="mp-in" value="${m.etg_cost_per_1k_input}"></label>
        <label>out<input type="number" min="0" class="mp-out" value="${m.etg_cost_per_1k_output}"></label>
        <button class="mp-save">Save</button>
      </div></div>`).join("");
    $("adm-models-list").querySelectorAll(".adm-model-row").forEach(row => {
      const btn = row.querySelector(".mp-save");
      btn.onclick = async () => {
        const inp = parseInt(row.querySelector(".mp-in").value, 10);
        const outp = parseInt(row.querySelector(".mp-out").value, 10);
        if (isNaN(inp) || isNaN(outp) || inp < 0 || outp < 0) return notify("Enter valid prices.");
        btn.disabled = true; btn.textContent = "…";
        try {
          await Eth.patch("/admin/models/pricing", {
            model_id: row.dataset.model, etg_cost_per_1k_input: inp, etg_cost_per_1k_output: outp });
          btn.textContent = "✓";
          setTimeout(() => { btn.textContent = "Save"; btn.disabled = false; }, 1200);
        } catch (e) { btn.textContent = "Save"; btn.disabled = false; notify(e.detail || "Couldn't save pricing."); }
      };
    });
  }

  let ADMIN_MODELS = null;
  async function loadAdminAgents() {
    $("adm-agents-list").innerHTML = `<div class="lempty">Loading…</div>`;
    let agents, models;
    try {
      [agents, models] = await Promise.all([
        Eth.get("/admin/agents"),
        ADMIN_MODELS ? Promise.resolve(ADMIN_MODELS) : Eth.get("/admin/models"),
      ]);
    } catch (e) { $("adm-agents-list").innerHTML = empty("Couldn't load agents."); return; }
    ADMIN_MODELS = models;
    if (!agents.length) { $("adm-agents-list").innerHTML = empty("No marketplace agents yet."); return; }
    const options = (cur) => `<option value="">Platform default</option>` +
      models.map(m => `<option value="${esc(m.model_id)}"${m.model_id === cur ? " selected" : ""}>${esc(m.display_name)}</option>`).join("");
    $("adm-agents-list").innerHTML = agents.map(a => `<div class="litem">
      <div class="lic ic-blue">🧩</div>
      <div class="linfo"><div class="lname">${esc(a.name)}</div>
        <div class="lmeta">${esc(a.category)} · ${fmt(a.deployments)} deployed</div></div>
      <select class="adm-model-select" data-agent="${esc(a.id)}">${options(a.preferred_model_id)}</select></div>`).join("");
    $("adm-agents-list").querySelectorAll(".adm-model-select").forEach(sel => {
      const prev = sel.value;
      sel.onchange = async () => {
        sel.disabled = true;
        try { await Eth.patch(`/admin/agents/${sel.dataset.agent}/model`, { model_id: sel.value || null }); }
        catch (e) { sel.value = prev; notify(e.detail || "Couldn't change the model."); }
        finally { sel.disabled = false; }
      };
    });
  }

  async function loadAdminStats() {
    let s;
    try { s = await Eth.get("/admin/stats"); } catch (e) { return; }
    $("adm-sub").textContent = `${fmt(s.active_businesses)} active · ${fmt(s.suspended_businesses)} suspended · ${fmt(s.deleted_businesses)} deleted`;
    $("adm-stats").innerHTML = [
      qstat("ic-amber", "🏪", s.total_businesses, "Businesses"),
      qstat("ic-blue", "👥", s.total_users, "Users"),
      qstat("ic-green", "🤖", s.total_bots, "Bots"),
      qstat("ic-amber", "🧩", s.total_agents_deployed, "Agents deployed"),
      qstat("ic-red", "💸", s.total_etg_spent, "ETG spent"),
      qstat("ic-green", "⛁", s.total_revenue_etg, "ETG revenue"),
    ].join("");
  }

  async function loadAdminBusinesses() {
    const params = new URLSearchParams();
    const q = $("adm-biz-search").value.trim();
    const st = $("adm-biz-status").value;
    if (q) params.set("search", q);
    if (st) params.set("status", st);
    $("adm-biz-list").innerHTML = `<div class="lempty">Loading…</div>`;
    let res;
    try { res = await Eth.get("/admin/businesses?" + params.toString()); }
    catch (e) { $("adm-biz-list").innerHTML = empty("Couldn't load businesses."); return; }
    if (!res.items.length) { $("adm-biz-list").innerHTML = empty("No businesses found."); return; }
    $("adm-biz-list").innerHTML = res.items.map(b => {
      const pill = b.is_deleted ? `<span class="lpill pl-fail">Deleted</span>`
        : b.is_suspended ? `<span class="lpill pl-fail">Suspended</span>`
        : `<span class="lpill pl-live">Active</span>`;
      const act = b.is_deleted ? ""
        : `<button class="adm-act" data-susp="${esc(b.id)}" data-on="${b.is_suspended ? 1 : 0}">${b.is_suspended ? "Unsuspend" : "Suspend"}</button>
           <button class="ldel" data-del="${esc(b.id)}" data-name="${esc(b.name)}" title="Delete">✕</button>`;
      return `<div class="litem">
        <div class="lic ic-amber">🏪</div>
        <div class="linfo"><div class="lname">${esc(b.name)}</div>
          <div class="lmeta">${esc(b.owner_email || "—")} · ${fmt(b.bot_count)} bots · ${fmt(b.agent_count)} agents</div></div>
        ${pill}${act}</div>`;
    }).join("");
    $("adm-biz-list").querySelectorAll("[data-susp]").forEach(btn => btn.onclick = async () => {
      btn.disabled = true;
      try { await Eth.patch(`/admin/businesses/${btn.dataset.susp}/suspend`, { suspend: btn.dataset.on !== "1" }); await loadAdminBusinesses(); loadAdminStats(); }
      catch (e) { btn.disabled = false; notify(e.detail || "Couldn't update."); }
    });
    $("adm-biz-list").querySelectorAll("[data-del]").forEach(btn => btn.onclick = async () => {
      const ok = await confirmAction(`Permanently delete "${btn.dataset.name}" and all its data? This can't be undone.`);
      if (!ok) return;
      try { await Eth.del(`/admin/businesses/${btn.dataset.del}`); await loadAdminBusinesses(); loadAdminStats(); }
      catch (e) { notify(e.detail || "Couldn't delete."); }
    });
  }

  async function loadAdminUsers() {
    const q = $("adm-user-search").value.trim();
    $("adm-user-list").innerHTML = `<div class="lempty">Loading…</div>`;
    let res;
    try { res = await Eth.get("/admin/users" + (q ? "?search=" + encodeURIComponent(q) : "")); }
    catch (e) { $("adm-user-list").innerHTML = empty("Couldn't load users."); return; }
    if (!res.items.length) { $("adm-user-list").innerHTML = empty("No users found."); return; }
    $("adm-user-list").innerHTML = res.items.map(u => {
      const name = u.email || u.full_name || (u.telegram_id ? "TG " + u.telegram_id : "User");
      const meta = [u.telegram_id ? "TG " + u.telegram_id : null, u.is_admin ? "Admin" : "User"].filter(Boolean).join(" · ");
      return `<div class="litem">
        <div class="lic ${u.is_admin ? "ic-green" : "ic-blue"}">${u.is_admin ? "🛡" : "👤"}</div>
        <div class="linfo"><div class="lname">${esc(name)}</div><div class="lmeta">${esc(meta)}</div></div>
        <label class="switch switch-sm"><input type="checkbox" data-admin="${esc(u.id)}" ${u.is_admin ? "checked" : ""}><span class="slider"></span></label></div>`;
    }).join("");
    $("adm-user-list").querySelectorAll("[data-admin]").forEach(cb => cb.onchange = async () => {
      try { await Eth.patch(`/admin/users/${cb.dataset.admin}/admin`, { is_admin: cb.checked }); }
      catch (e) { cb.checked = !cb.checked; notify(e.detail || "Couldn't change admin rights."); }
    });
  }

  async function loadAdminSystem() {
    $("adm-system-list").innerHTML = `<div class="lempty">Checking…</div>`;
    let s;
    try { s = await Eth.get("/admin/system/status"); }
    catch (e) { $("adm-system-list").innerHTML = empty("Couldn't load system status."); return; }
    const ic = (st) => (st === "ok" || st === "configured") ? "ic-green" : st === "error" ? "ic-red" : "ic-amber";
    const sym = (st) => (st === "ok" || st === "configured") ? "✓" : st === "error" ? "✕" : "!";
    const row = (label, st, detail) => `<div class="litem">
      <div class="lic ${ic(st)}">${sym(st)}</div>
      <div class="linfo"><div class="lname">${esc(label)}</div><div class="lmeta">${esc(detail || st)}</div></div></div>`;
    $("adm-system-list").innerHTML = [
      row("Database", s.database.status, s.database.detail),
      row("Redis", s.redis.status, s.redis.detail),
      row("Storage", s.storage.status, s.storage.bucket),
      row("Workers", s.workers.status, `${(s.workers.jobs || []).join(", ")} · backlog ${fmt(s.workers.embedding_backlog)}`),
    ].join("");
  }

  // ── Recharge sheet ────────────────────────────────────────────────────────
  let _rchBizId = null;
  let _rchPkgs = null;
  let _rchSelected = null;

  async function openRecharge(bizId) {
    _rchBizId = bizId;
    _rchSelected = null;
    $("rch-err").classList.add("hidden");
    $("pkg-list").innerHTML = `<div class="lempty">Loading packages…</div>`;
    show("recharge-sheet");

    if (!_rchPkgs) {
      try { _rchPkgs = await Eth.get("/billing/packages"); }
      catch (e) { $("pkg-list").innerHTML = `<div class="lempty">Couldn't load packages.</div>`; return; }
    }
    _renderPkgs();
  }

  function _renderPkgs() {
    $("pkg-list").innerHTML = (_rchPkgs || []).map(p => {
      const bonus = p.bonus_etg > 0 ? ` <span class="pkg-bonus">+ ${fmt(p.bonus_etg)} bonus</span>` : "";
      return `<div class="pkg-card${_rchSelected === p.id ? " selected" : ""}" data-pid="${esc(p.id)}">
        <div class="pkg-top">
          <span class="pkg-name">${esc(p.name)}</span>
          <span class="pkg-price">${fmt(p.price_etb)} ETB</span>
        </div>
        <div class="pkg-detail">${fmt(p.etg_amount)} ETG${bonus}</div>
      </div>`;
    }).join("") + `<div class="pkg-cta"><button class="onb-btn" id="rch-pay" ${_rchSelected ? "" : "disabled"}>Pay with Chapa →</button></div>`;

    $("pkg-list").querySelectorAll(".pkg-card").forEach(card => {
      card.onclick = () => {
        _rchSelected = card.dataset.pid;
        _renderPkgs();
      };
    });

    const payBtn = $("rch-pay");
    if (payBtn) {
      // Enable the pay button when a package is selected; backend will validate.
      const chapaEnabled = true; // Always enable; backend will validate
      const canPay = !!_rchSelected && chapaEnabled;
      payBtn.disabled = !canPay;
      payBtn.onclick = _submitRecharge;
      // Clear any prior error message
      $("rch-err").classList.add("hidden");
    }
  }

  async function _submitRecharge() {
    if (!_rchSelected || !_rchBizId) return;
    const btn = $("rch-pay");
    btn.disabled = true; btn.textContent = "Redirecting…";
    $("rch-err").classList.add("hidden");
    try {
      const res = await Eth.post(`/billing/recharge/${_rchBizId}`, {
        package_id: _rchSelected,
        payment_provider: "chapa",
      });
        console.log("Recharge response:", res);
      if (res.payment_url) {
        if (Eth.tg && Eth.tg.openLink) Eth.tg.openLink(res.payment_url);
        else window.open(res.payment_url, "_blank");
        hide("recharge-sheet");
      } else {
        // Show backend response for debugging (helps trace missing payment_url)
        $("rch-err").textContent = (res && (res.message || res.detail)) || JSON.stringify(res) || "Payment gateway not configured. Contact support.";
        $("rch-err").classList.remove("hidden");
      }
    } catch (e) {
      $("rch-err").textContent = e.detail || "Couldn't initiate payment. Try again.";
      $("rch-err").classList.remove("hidden");
    } finally {
      btn.disabled = false; btn.textContent = "Pay with Chapa →";
    }
  }

  $("rch-cancel").onclick = () => hide("recharge-sheet");
  $("recharge-sheet").addEventListener("click", e => {
    if (e.target === $("recharge-sheet")) hide("recharge-sheet");
  });

  boot();
})();
