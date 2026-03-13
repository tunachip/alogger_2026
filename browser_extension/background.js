const api = globalThis.browser ?? globalThis.chrome;

const BRIDGE_ENDPOINT = "http://127.0.0.1:17373/api/open";
const MENU_PAGE = "open-in-alogger-page";
const MENU_LINK = "open-in-alogger-link";

function normalizeYouTubeUrl(raw) {
  if (!raw) {
    return null;
  }

  let parsed;
  try {
    parsed = new URL(raw);
  } catch (_err) {
    return null;
  }

  const host = parsed.hostname.toLowerCase();
  const isYouTubeHost =
    host === "youtube.com" ||
    host === "www.youtube.com" ||
    host === "m.youtube.com" ||
    host === "youtu.be";
  if (!isYouTubeHost) {
    return null;
  }

  if (host === "youtu.be") {
    const id = parsed.pathname.replace(/^\//, "").trim();
    if (!id) {
      return null;
    }
    const canonical = new URL("https://www.youtube.com/watch");
    canonical.searchParams.set("v", id);
    return canonical.toString();
  }

  if (parsed.pathname === "/watch") {
    const id = parsed.searchParams.get("v");
    if (!id) {
      return null;
    }
    const canonical = new URL("https://www.youtube.com/watch");
    canonical.searchParams.set("v", id);
    return canonical.toString();
  }

  if (parsed.pathname.startsWith("/shorts/")) {
    const id = parsed.pathname.split("/")[2] || "";
    if (!id) {
      return null;
    }
    const canonical = new URL("https://www.youtube.com/watch");
    canonical.searchParams.set("v", id);
    return canonical.toString();
  }

  return raw;
}

async function openInAlogger(rawUrl) {
  const url = normalizeYouTubeUrl(rawUrl);
  if (!url) {
    throw new Error("This is not a supported YouTube link.");
  }

  const response = await fetch(BRIDGE_ENDPOINT, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      url,
      autoplay: true
    })
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload && payload.error ? String(payload.error) : `HTTP ${response.status}`;
    throw new Error(detail);
  }

  return payload;
}

function setBadge(text, color) {
  if (!api.action || !api.action.setBadgeText) {
    return;
  }
  api.action.setBadgeBackgroundColor({ color }).catch(() => {});
  api.action.setBadgeText({ text }).catch(() => {});
  setTimeout(() => {
    api.action.setBadgeText({ text: "" }).catch(() => {});
  }, 2200);
}

async function handleUrl(rawUrl) {
  try {
    await openInAlogger(rawUrl);
    setBadge("OK", "#2e7d32");
  } catch (_err) {
    setBadge("ERR", "#b71c1c");
  }
}

function createMenus() {
  if (!api.contextMenus || !api.contextMenus.create) {
    return;
  }

  api.contextMenus.removeAll(() => {
    api.contextMenus.create({
      id: MENU_PAGE,
      title: "Open This Video In Alogger",
      contexts: ["page"],
      documentUrlPatterns: [
        "https://www.youtube.com/*",
        "https://youtube.com/*",
        "https://m.youtube.com/*",
        "https://youtu.be/*"
      ]
    });

    api.contextMenus.create({
      id: MENU_LINK,
      title: "Open Link In Alogger",
      contexts: ["link"],
      targetUrlPatterns: [
        "https://www.youtube.com/*",
        "https://youtube.com/*",
        "https://m.youtube.com/*",
        "https://youtu.be/*"
      ]
    });
  });
}

if (api.runtime && api.runtime.onInstalled) {
  api.runtime.onInstalled.addListener(() => {
    createMenus();
  });
}

if (api.runtime && api.runtime.onStartup) {
  api.runtime.onStartup.addListener(() => {
    createMenus();
  });
}

if (api.action && api.action.onClicked) {
  api.action.onClicked.addListener((tab) => {
    handleUrl(tab && tab.url ? String(tab.url) : "");
  });
}

if (api.contextMenus && api.contextMenus.onClicked) {
  api.contextMenus.onClicked.addListener((info, tab) => {
    if (info.menuItemId === MENU_LINK) {
      handleUrl(info.linkUrl ? String(info.linkUrl) : "");
      return;
    }
    if (info.menuItemId === MENU_PAGE) {
      handleUrl(tab && tab.url ? String(tab.url) : "");
    }
  });
}
