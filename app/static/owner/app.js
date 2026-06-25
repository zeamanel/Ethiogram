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

    // active agents
    $("agents").innerHTML = (ov.active_agents && ov.active_agents.length)
      ? ov.active_agents.map(a => {
          const meta = a.status === "trial" && a.days_left != null
            ? `${esc(a.category)} · Trial · ${a.days_left} days left`
            : esc(a.category);
          const pill = a.status === "unlocked"
            ? `<span class="lpill pl-live">Active</span>`
            : `<span class="lpill pl-sync">Trial</span>`;
          return litem(agentIc(a.category), "ic-blue", esc(a.display_name || a.category), meta, pill);
        }).join("")
      : empty("No agents deployed yet.");

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
