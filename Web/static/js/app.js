import { api, buildFrameUrl, filterLinesByLevel, go2rtcBaseUrl, initToast } from "./shared.js";
import { initStatusTab } from "./tabs/status.js";
import { initConfigTab } from "./tabs/config.js";
import { initLogsTab } from "./tabs/logs.js";
import { initWebLogsTab } from "./tabs/web-logs.js";
import { initCamerasTab } from "./tabs/cameras.js";
import { initDashboardTab } from "./tabs/dashboard.js";
import { initRuntimeTab } from "./tabs/runtime.js";

const toast = initToast();
const shared = {
  api,
  toast,
  filterLinesByLevel,
  go2rtcBaseUrl,
  buildFrameUrl,
};

const tabModules = {
  status: initStatusTab,
  config: initConfigTab,
  logs: initLogsTab,
  "web-logs": initWebLogsTab,
  cameras: initCamerasTab,
  dashboard: initDashboardTab,
  runtime: initRuntimeTab,
};

const tabControllers = new Map();
let activeTabId = null;
const tabLinks = Array.from(document.querySelectorAll('a[data-bs-toggle="tab"]'));

async function loadPartialForTab(tabId) {
  const pane = document.getElementById(tabId);
  if (!pane) return null;
  if (pane.dataset.loaded === "true") return pane;
  const partial = pane.dataset.partial;
  if (!partial) {
    pane.dataset.loaded = "true";
    return pane;
  }
  try {
    const res = await fetch(partial, { cache: "no-cache" });
    if (!res.ok) {
      pane.innerHTML = '<div class="text-danger small">Failed to load tab content.</div>';
    } else {
      pane.innerHTML = await res.text();
    }
  } catch (err) {
    pane.innerHTML = '<div class="text-danger small">Failed to load tab content.</div>';
  }
  pane.dataset.loaded = "true";
  return pane;
}

async function ensureTabInitialized(tabId) {
  const pane = await loadPartialForTab(tabId);
  if (!pane) return null;
  if (!tabControllers.has(tabId) && tabModules[tabId]) {
    tabControllers.set(tabId, tabModules[tabId](pane, shared));
  }
  return pane;
}

function deactivateOtherPanes(targetId) {
  document.querySelectorAll(".tab-pane.show").forEach((pane) => {
    if (pane.id !== targetId) {
      pane.classList.remove("show", "active");
    }
  });
}

async function showTab(targetId) {
  if (!targetId) return;
  const tabId = targetId.replace("#", "");
  await ensureTabInitialized(tabId);
  deactivateOtherPanes(tabId);
  if (activeTabId && activeTabId !== tabId) {
    tabControllers.get(activeTabId)?.stop?.();
  }
  activeTabId = tabId;
  tabControllers.get(tabId)?.start?.();
}

function getInitialTabId() {
  const activeLink = document.querySelector('a[data-bs-toggle="tab"].active');
  const target = activeLink?.getAttribute("data-bs-target") || activeLink?.getAttribute("href");
  return target || "#dashboard";
}

function syncActiveLinks(targetId) {
  tabLinks.forEach((link) => {
    const linkTarget = link.getAttribute("data-bs-target") || link.getAttribute("href");
    const isActive = linkTarget === targetId;
    link.classList.toggle("active", isActive);
    link.setAttribute("aria-selected", isActive ? "true" : "false");
  });
}

tabLinks.forEach((tab) => {
  tab.addEventListener("shown.bs.tab", (e) => {
    const target = e.target.getAttribute("data-bs-target") || e.target.getAttribute("href");
    showTab(target);
    if (target) syncActiveLinks(target);
  });
});

const initialTarget = getInitialTabId();
showTab(initialTarget);
syncActiveLinks(initialTarget);

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    if (activeTabId) tabControllers.get(activeTabId)?.stop?.();
  } else if (activeTabId) {
    tabControllers.get(activeTabId)?.start?.();
  }
});
