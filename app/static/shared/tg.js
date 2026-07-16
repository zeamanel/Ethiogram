// Telegram WebApp bootstrap + authenticated API client.
// Used by the OWNER console (master bot). The customer storefront is a SEPARATE
// app with its own auth path — this helper only does owner (/auth/miniapp).
(function (global) {
  const tg = global.Telegram && global.Telegram.WebApp ? global.Telegram.WebApp : null;

  // Server (5xx) errors are not actionable by the user and our generic body
  // ("Unexpected error") reads as scary — show a friendly retry message. 4xx
  // detail (validation, "already deployed", etc.) is meaningful, so keep it.
  function _failDetail(status, data) {
    if (status >= 500) return "Something went wrong. Please try again.";
    return (data && (data.message || data.detail)) || ("Error " + status);
  }

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

    /** Authenticated POST against /api/v1. Throws Error with .detail on failure. */
    async post(path, body) {
      const res = await fetch("/api/v1" + path, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + this.token },
        body: JSON.stringify(body || {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error("POST " + path + " -> " + res.status);
        err.status = res.status;
        err.detail = _failDetail(res.status, data);
        throw err;
      }
      return data;
    },

    /** Authenticated multipart upload (a File) — no JSON content-type. */
    async upload(path, file) {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/v1" + path, {
        method: "POST",
        headers: { "Authorization": "Bearer " + this.token },  // browser sets multipart boundary
        body: fd,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error("upload " + path + " -> " + res.status);
        err.status = res.status;
        err.detail = _failDetail(res.status, data);
        throw err;
      }
      return data;
    },

    /** Authenticated PATCH against /api/v1. */
    async patch(path, body) {
      const res = await fetch("/api/v1" + path, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", "Authorization": "Bearer " + this.token },
        body: JSON.stringify(body || {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const err = new Error("PATCH " + path + " -> " + res.status);
        err.status = res.status;
        err.detail = _failDetail(res.status, data);
        throw err;
      }
      return data;
    },

    /** Authenticated DELETE against /api/v1. */
    async del(path) {
      const res = await fetch("/api/v1" + path, {
        method: "DELETE",
        headers: { "Authorization": "Bearer " + this.token },
      });
      if (!res.ok) throw new Error("DELETE " + path + " -> " + res.status);
      return true;
    },
  };

  global.Eth = Eth;
})(window);
