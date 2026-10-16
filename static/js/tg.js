/**
 * Telegram Mini App integration.
 * Falls back to a plain-browser mode when not running inside Telegram.
 */

const tg = window.Telegram ? window.Telegram.WebApp : null;

export function initTelegram() {
  if (tg && tg.initData !== undefined) {
    try {
      tg.ready();
      tg.expand();
      if (tg.setHeaderColor) tg.setHeaderColor("#0d0e12");
      if (tg.setBackgroundColor) tg.setBackgroundColor("#0d0e12");
    } catch {
      /* older clients may not support all calls */
    }
  }
  return {
    isTelegram: Boolean(tg && tg.initData !== undefined && tg.initData !== ""),
    initData: tg ? tg.initData : "",
    user: tg && tg.initDataUnsafe ? tg.initDataUnsafe.user || null : null,
  };
}

export function confirmAction(message) {
  return new Promise((resolve) => {
    if (tg && tg.showPopup) {
      tg.showPopup(
        {
          title: "تأیید",
          message,
          buttons: [
            { id: "yes", type: "destructive", text: "بله" },
            { id: "no", type: "cancel", text: "انصراف" },
          ],
        },
        (buttonId) => resolve(buttonId === "yes"),
      );
    } else {
      resolve(window.confirm(message));
    }
  });
}

export function showAlert(message) {
  if (tg && tg.showPopup) {
    tg.showPopup({ title: "BeatLab", message, buttons: [{ type: "ok", text: "باشه" }] });
  } else {
    window.alert(message);
  }
}

export function haptic(style = "light") {
  try {
    if (tg && tg.HapticFeedback) {
      tg.HapticFeedback.impactOccurred(style);
    }
  } catch {
    /* not supported */
  }
}
