export function initToast() {
  const toastEl = document.getElementById("toast");
  if (!toastEl) return () => {};
  const bsToast = new bootstrap.Toast(toastEl, { delay: 2200 });
  const toastBody = toastEl.querySelector(".toast-body");
  return (msg, kind = "info") => {
    toastBody.textContent = msg;
    toastEl.classList.toggle("text-bg-danger", kind === "error");
    toastEl.classList.toggle("text-bg-dark", kind !== "error");
    bsToast.show();
  };
}

export async function api(path, opts = {}) {
  const res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(msg || res.statusText);
  }
  if (res.status === 204) return null;
  const text = await res.text();
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export function filterLinesByLevel(lines, level) {
  if (!Array.isArray(lines) || level === "ALL") return lines;
  const order = ["DEBUG", "INFO", "WARN", "ERROR"];
  const minIdx = order.indexOf(level);
  return lines.filter((line) => {
    const match = line.match(/\[(DEBUG|INFO|WARN|ERROR)\]/);
    if (!match) return level === "ERROR" ? false : true;
    return order.indexOf(match[1]) >= minIdx;
  });
}

export function go2rtcBaseUrl() {
  return `http://${location.hostname}:1984`;
}

export function buildFrameUrl(name, cacheBust = false, channel = null) {
  const base = `${go2rtcBaseUrl()}/api/frame.jpeg?src=${encodeURIComponent(name)}`;
  const withChannel = channel == null ? base : `${base}&channel=${encodeURIComponent(channel)}`;
  if (!cacheBust) return withChannel;
  return `${withChannel}&t=${Date.now()}`;
}
