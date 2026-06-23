// Telegram WebApp bootstrap + authenticated API client.
// Used by the OWNER console (master bot). The customer storefront is a SEPARATE
// app with its own auth path — this helper only does owner (/auth/miniapp).
(function (global) {
  const tg = global.Telegram && global.Telegram.WebApp ? global.Telegram.WebApp : null;

  const Eth = {
    tg,
    token: null,

    /** Initialise the Telegram WebApp shell (theme, expand). Safe if not in TG. */
    initTelegram() {
      if (!tg) return;
      try { tg.ready(); tg.expand(); } catch (e) { /* not in Telegram */ }
    },

    firstName() {
      const u = tg && tg.initDataUnsafe ? tg.initDataUnsafe.user : null;
      return (u && u.first_name) ? u.first_name : "there";
    },

    /** Exchange Telegram initData for a JWT via the OWNER endpoint. */
    async login() {
      const initData = tg && tg.initData ? tg.initData : "";
      const res = await fetch("/api/v1/auth/miniapp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ init_data: initData }),
      });
      if (!res.ok) throw new Error("auth_failed_" + res.status);
      const data = await res.json();
      this.token = data.access_token;
      return data;
    },

    /** Authenticated GET against /api/v1. */
    async get(path) {
      const res = await fetch("/api/v1" + path, {
        headers: { "Authorization": "Bearer " + this.token },
      });
      if (!res.ok) throw new Error("GET " + path + " -> " + res.status);
      return res.json();
    },
  };

  global.Eth = Eth;
})(window);
