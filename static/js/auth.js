/** Shared login flow for both Mini App pages. */

import { api, ApiError } from "./api.js";
import { initTelegram } from "./tg.js";

/**
 * Ensure there is an authenticated session.
 *
 * 1. Try the current session (GET /api/me/).
 * 2. Inside Telegram: exchange initData for a session.
 * 3. In a plain browser: offer the development login when enabled.
 *
 * Returns { user } on success, { needsLogin: true, devEnabled } otherwise.
 */
export async function ensureAuth() {
  const tgInfo = initTelegram();

  try {
    const data = await api.me();
    return { user: data.user };
  } catch {
    /* not logged in yet — fall through */
  }

  if (tgInfo.isTelegram && tgInfo.initData) {
    try {
      const data = await api.telegramLogin(tgInfo.initData);
      return { user: data.user };
    } catch (err) {
      if (err instanceof ApiError && err.code === "telegram_not_configured") {
        // Bot token missing on the server: fall back to dev login if allowed.
      } else {
        return { needsLogin: true, error: err, devEnabled: false };
      }
    }
  }

  let devEnabled = false;
  let telegramConfigured = false;
  try {
    const config = await api.authConfig();
    devEnabled = config.dev_auth_enabled;
    telegramConfigured = config.telegram_configured;
  } catch {
    /* config unavailable — assume no dev login */
  }

  return { needsLogin: true, devEnabled, telegramConfigured, tgInfo };
}

export async function devLogin(username = "devuser") {
  const data = await api.devLogin(username);
  return data.user;
}
