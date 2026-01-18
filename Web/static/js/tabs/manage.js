export function initManageTab(root, { api }) {
  const summaryEl = root.querySelector("#manageSummary");
  const msgEl = root.querySelector("#manageMsg");
  const tableBody = root.querySelector("#manageCameraTable");
  const adoptionSelect = root.querySelector("#manageAdoptionCamera");
  const discoveryStatus = root.querySelector("#manageDiscoveryStatus");
  const discoveryStartBtn = root.querySelector("#manageDiscoveryStart");
  const discoveryStopBtn = root.querySelector("#manageDiscoveryStop");
  const apiStatus = root.querySelector("#manageApiStatus");
  const apiStartBtn = root.querySelector("#manageApiStart");
  const apiStopBtn = root.querySelector("#manageApiStop");

  function setMsg(text, kind = "muted") {
    if (!msgEl) return;
    msgEl.textContent = text;
    msgEl.className = `small text-${kind}`;
  }

  function formatDiscovery(discovery, management) {
    if (!discovery) return "Discovery: unknown";
    const adoptLabel = management?.initialized ? "seen" : "pending";
    const base = discovery.running ? "Discovery: running" : "Discovery: idle";
    const cam = discovery.running && discovery.camera ? ` (${discovery.camera})` : "";
    return `${base}${cam} | adoption=${adoptLabel}`;
  }

  function renderSummary(status, cameras) {
    if (!summaryEl) return;
    const active = status?.active_settings ? status.active_settings.split("/").pop() : "none";
    const discoveryText = formatDiscovery(status?.discovery_lock, status?.management);
    const countText = `Cameras: ${cameras.length}`;
    summaryEl.innerHTML = "";
    [countText, `Active: ${active}`, discoveryText].forEach((text) => {
      const span = document.createElement("span");
      span.textContent = text;
      summaryEl.appendChild(span);
    });
  }

  function deriveFriendlyName(streamName) {
    if (!streamName) return "";
    const mediaProfile = streamName.match(/^(.*)_MediaProfile\d+$/);
    if (mediaProfile) return mediaProfile[1];
    const streamSuffix = streamName.match(/^(.*)_stream\d+$/i);
    if (streamSuffix) return streamSuffix[1];
    return streamName;
  }

  function extractHostFromSrc(src) {
    if (!src) return "";
    let target = src;
    if (src.startsWith("exec:")) {
      const inputMatch = src.match(/-i\s+("([^"]+)"|'([^']+)'|(\S+))/);
      if (inputMatch) {
        target = inputMatch[2] || inputMatch[3] || inputMatch[4] || target;
      }
    }
    const urlMatch = target.match(/([a-zA-Z][a-zA-Z0-9+.-]*:\/\/[^\s"]+)/);
    if (urlMatch) {
      target = urlMatch[1];
    }
    try {
      const parsed = new URL(target);
      return parsed.hostname || "";
    } catch {
      if (target.includes("@")) {
        const afterAt = target.split("@").pop() || "";
        return afterAt.split(/[/?\s]/)[0] || "";
      }
      if (target.includes("://")) {
        return target.split("://")[1]?.split(/[/?\s]/)[0] || "";
      }
      return "";
    }
  }

  function parseGo2rtcStreams(content) {
    if (!content) return new Map();
    const lines = content.split("\n");
    let inStreams = false;
    let streamsIndent = null;
    let currentComment = "";
    let pendingName = "";
    const map = new Map();

    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const trimmed = line.trim();
      if (!inStreams) {
        if (trimmed === "streams:") {
          inStreams = true;
        }
        continue;
      }
      if (!line.startsWith(" ") && trimmed) {
        break;
      }
      if (!trimmed) continue;
      if (trimmed.startsWith("#")) {
        currentComment = trimmed.replace(/^#\s?/, "");
        continue;
      }
      const indent = line.match(/^(\s*)/)?.[1]?.length || 0;
      if (streamsIndent === null && trimmed.includes(":")) {
        streamsIndent = indent;
      }
      if (streamsIndent !== null && indent < streamsIndent) break;

      if (trimmed.startsWith("-")) {
        if (pendingName && !map.has(pendingName)) {
          const src = trimmed.replace(/^-/, "").trim().replace(/^["']|["']$/g, "");
          map.set(pendingName, { src, comment: currentComment });
        }
        continue;
      }

      if (!trimmed.includes(":")) continue;
      const sep = trimmed.indexOf(":");
      const key = trimmed.slice(0, sep).trim();
      const rest = trimmed.slice(sep + 1).trim();
      pendingName = key;
      if (rest) {
        const src = rest.replace(/^["']|["']$/g, "");
        map.set(key, { src, comment: currentComment });
      }
    }
    return map;
  }

  function renderRow(cam, status) {
    const tr = document.createElement("tr");
    const isActive = status?.active_settings && status.active_settings === cam.path;
    const discovery = status?.discovery_lock || {};
    const discoveryBusy = discovery.running && discovery.camera && discovery.camera !== cam.id;
    const canAdopt = cam.canAdopt ? "Needs adoption" : "Adopted";
    const stateText = cam.wssRunning ? "Running" : "Stopped";
    const displayName = cam.friendlyName || cam.name || cam.id;

    tr.innerHTML = `
      <td>${displayName}</td>
      <td>${cam.marketName || cam.model || "-"}</td>
      <td>${cam.mac || "-"}</td>
      <td>${cam.ip || "-"}</td>
      <td>${canAdopt}</td>
      <td>${stateText}</td>
      <td class="text-end"></td>
    `;

    const actions = tr.querySelector("td:last-child");
    if (!actions) return tr;

    const actionWrap = document.createElement("div");
    actionWrap.className = "d-flex justify-content-end gap-2 flex-wrap";

    if (cam.wssRunning) {
      const stopBtn = document.createElement("button");
      stopBtn.className = "btn btn-sm btn-outline-danger";
      stopBtn.textContent = "Stop WSS";
      stopBtn.addEventListener("click", () => stopWss(cam));
      actionWrap.appendChild(stopBtn);
    } else {
      const startBtn = document.createElement("button");
      startBtn.className = "btn btn-sm btn-primary";
      startBtn.textContent = "Start WSS";
      startBtn.addEventListener("click", () => startWss(cam));
      actionWrap.appendChild(startBtn);
    }

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn-danger btn-sm";
    deleteBtn.innerHTML = '<i class="bi bi-trash me-1"></i>Delete';
    deleteBtn.addEventListener("click", () => deleteSettings(cam));
    actionWrap.appendChild(deleteBtn);

    actions.appendChild(actionWrap);

    return tr;
  }

  function renderTable(cameras, status) {
    if (!tableBody) return;
    tableBody.innerHTML = "";
    if (!cameras.length) {
      const tr = document.createElement("tr");
      tr.innerHTML = '<td colspan="7" class="text-muted small">No settings files found yet.</td>';
      tableBody.appendChild(tr);
      return;
    }
    cameras.forEach((cam) => tableBody.appendChild(renderRow(cam, status)));
  }

  function renderAdoptionControls(status, cameras) {
    if (!adoptionSelect || !discoveryStatus || !apiStatus) return;
    adoptionSelect.innerHTML = "";
    cameras.forEach((cam) => {
      const opt = document.createElement("option");
      opt.value = cam.id;
      opt.textContent = cam.friendlyName ? `${cam.friendlyName} (${cam.id})` : cam.id;
      if (status?.active_settings && status.active_settings.endsWith(cam.id)) {
        opt.selected = true;
      }
      adoptionSelect.appendChild(opt);
    });

    const discovery = status?.discovery || {};
    discoveryStatus.textContent = discovery.running ? "running" : "idle";
    discoveryStatus.className = `badge ${discovery.running ? "text-bg-success" : "text-bg-secondary"}`;
    apiStatus.textContent = status?.api?.running ? "running" : "stopped";
    apiStatus.className = `badge ${status?.api?.running ? "text-bg-success" : "text-bg-secondary"}`;
  }

  async function refresh() {
    try {
      const [cameraRes, statusRes, configRes] = await Promise.allSettled([
        api("/api/unifi/cameras"),
        api("/api/unifi/status"),
        api("/api/go2rtc/config"),
      ]);
      if (cameraRes.status !== "fulfilled") throw cameraRes.reason;
      const cameras = cameraRes.value?.cameras || [];
      const status = statusRes.status === "fulfilled" ? statusRes.value : null;
      const config = configRes.status === "fulfilled" ? configRes.value : null;
      const streamMap = parseGo2rtcStreams(config?.content || "");
      cameras.forEach((cam) => {
        const channel = cam.go2rtcChannel || "";
        const entry = streamMap.get(channel) || null;
        const friendlyName = deriveFriendlyName(channel);
        cam.friendlyName = friendlyName;
        cam.ip = entry ? extractHostFromSrc(entry.src) : "";
      });
      renderSummary(status, cameras);
      renderAdoptionControls(status, cameras);
      renderTable(cameras, status);
      return { cameras, status };
    } catch (err) {
      setMsg(err.message || "Failed to load camera list.", "danger");
      return null;
    }
  }

  async function startWss(cam) {
    setMsg(`Starting WSS for ${cam.name || cam.id}…`, "muted");
    try {
      await api("/api/unifi/wss/start", { method: "POST", body: JSON.stringify({ settings: cam.id }) });
      setMsg(`WSS started for ${cam.name || cam.id}.`, "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to start WSS.", "danger");
    }
  }

  async function stopWss(cam) {
    setMsg(`Stopping WSS for ${cam.name || cam.id}…`, "muted");
    try {
      await api("/api/unifi/wss/stop", { method: "POST", body: JSON.stringify({ settings: cam.id }) });
      setMsg(`WSS stopped for ${cam.name || cam.id}.`, "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to stop WSS.", "danger");
    }
  }

  async function deleteSettings(cam) {
    const ok = window.confirm(`Delete settings file ${cam.id}? This cannot be undone.`);
    if (!ok) return;
    setMsg(`Deleting ${cam.id}…`, "muted");
    try {
      await api("/api/unifi/settings/delete", { method: "DELETE", body: JSON.stringify({ filename: cam.id }) });
      setMsg(`${cam.id} deleted.`, "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to delete settings file.", "danger");
    }
  }

  async function startDiscovery() {
    if (!adoptionSelect) return;
    const settings = adoptionSelect.value;
    if (!settings) return;
    setMsg("Starting discovery…", "muted");
    try {
      await api("/api/unifi/discovery/start", { method: "POST", body: JSON.stringify({ settings }) });
      setMsg("Discovery started.", "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to start discovery.", "danger");
    }
  }

  async function stopDiscovery() {
    setMsg("Stopping discovery…", "muted");
    try {
      await api("/api/unifi/discovery/stop", { method: "POST" });
      setMsg("Discovery stopped.", "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to stop discovery.", "danger");
    }
  }

  async function startApi() {
    if (!adoptionSelect) return;
    const settings = adoptionSelect.value;
    setMsg("Starting API server…", "muted");
    try {
      await api("/api/unifi/api/start", { method: "POST", body: JSON.stringify({ settings }) });
      setMsg("API server started.", "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to start API server.", "danger");
    }
  }

  async function stopApi() {
    setMsg("Stopping API server…", "muted");
    try {
      await api("/api/unifi/api/stop", { method: "POST" });
      setMsg("API server stopped.", "success");
      await refresh();
    } catch (err) {
      setMsg(err.message || "Failed to stop API server.", "danger");
    }
  }

  discoveryStartBtn?.addEventListener("click", startDiscovery);
  discoveryStopBtn?.addEventListener("click", stopDiscovery);
  apiStartBtn?.addEventListener("click", startApi);
  apiStopBtn?.addEventListener("click", stopApi);

  return {
    async start() {
      await refresh();
    },
    stop() {},
  };
}
