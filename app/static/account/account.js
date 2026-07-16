// My Account — loads the end-user profile (name, balance, referral, businesses).
// Auth reuses the shared owner client (Telegram initData -> JWT).
(function () {
  const $ = (id) => document.getElementById(id);
  const LANG = { en: "English", es: "Español", fr: "Français", ru: "Русский",
                 zh: "中文", ar: "العربية", am: "አማርኛ" };

  function show(id) {
    ["loading", "error", "account"].forEach((x) =>
      $(x).classList.toggle("hidden", x !== id));
  }

  function fail(msg) {
    $("error-text").textContent = msg || "Couldn't load your account.";
    show("error");
  }

  function render(a) {
    $("a-name").textContent = a.name || "there";
    $("a-username").textContent = a.username ? "@" + a.username : "";
    $("a-avatar").textContent = (a.name || "🙂").trim().charAt(0).toUpperCase() || "🙂";
    $("a-balance").textContent = (a.etg_balance || 0).toLocaleString();
    $("a-lang").textContent = LANG[a.language_code] || a.language_code || "English";

    $("a-ref-link").textContent = a.referral_link;
    $("a-copy").onclick = () => {
      const done = () => { $("a-copy").textContent = "✓ Copied"; };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(a.referral_link).then(done, done);
      } else if (window.Telegram && Telegram.WebApp && Telegram.WebApp.openTelegramLink) {
        done();
      } else { done(); }
    };

    const list = $("a-biz-list");
    list.innerHTML = "";
    if (!a.businesses || !a.businesses.length) {
      list.innerHTML = '<div class="a-muted">No business yet — open the Dashboard to create one.</div>';
    } else {
      a.businesses.forEach((b) => {
        const row = document.createElement("a");
        row.className = "a-biz";
        row.href = b.store_url;
        row.target = "_blank";
        row.rel = "noopener";
        row.innerHTML = '<span class="a-biz-name">' + (b.name || b.slug) +
          '</span><span class="a-biz-go">View store ›</span>';
        list.appendChild(row);
      });
    }
    $("a-dashboard").onclick = () => {
      const url = "../owner/";
      if (window.Telegram && Telegram.WebApp) location.href = url; else location.href = url;
    };

    show("account");
  }

  async function boot() {
    Eth.initTelegram();
    try {
      await Eth.login();
      const a = await Eth.get("/account/me");
      render(a);
    } catch (e) {
      fail("Please open this from the Ethiogram bot.");
    }
  }

  boot();
})();
