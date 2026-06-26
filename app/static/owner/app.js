// Ethiogram Owner Console — loads data and renders the dashboard.
(function () {
  const $ = (id) => document.getElementById(id);
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
    try {
      await Eth.login();
    } catch (e) {
      return fail("We couldn't verify your Telegram session. Open this from the bot's menu button.");
    }
    let businesses;
    try {
      businesses = await Eth.get("/dashboard/businesses");
    } catch (e) {
      return fail("Couldn't load your account.");
    }
    if (!businesses || businesses.length === 0) {
      hide("loading"); setupWizard(); show("onboarding");
      return;
    }
    const biz = businesses[0];

    // overview is required; orders/docs/wallet are best-effort enrichment.
    let overview;
    try {
      overview = await Eth.get(`/dashboard/overview/${biz.id}`);
    } catch (e) {
      return fail("Couldn't load your dashboard.");
    }
    const [ordersR, docsR, walletR] = await Promise.allSettled([
      Eth.get(`/dashboard/orders/${biz.id}`),
      Eth.get(`/knowledge/${biz.id}/documents`),
      Eth.get(`/billing/wallet/${biz.id}`),
    ]);
    const orders = ordersR.status === "fulfilled" ? ordersR.value : [];
    const docs = docsR.status === "fulfilled" ? docsR.value : [];
    const wallet = walletR.status === "fulfilled" ? walletR.value : null;

    render(overview, orders, docs, wallet);
    hide("loading"); show("dashboard");
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
    $("se-tagline").value = cfg.tagline || "";
    $("se-hours").value = cfg.hours || "";

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
        hours: $("se-hours").value.trim(),
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
    $("btn-recharge").onclick = () => notify("Recharge is coming soon.");

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
    $("bots").innerHTML = botList + connectRow;
    $("bots-connect").onclick = () =>
      connectBotFlow(b.id, () => { hide("wiz-bot"); show("dashboard"); });
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

  boot();
})();
