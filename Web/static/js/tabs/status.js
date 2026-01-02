export function initStatusTab(root, { api, toast }) {
  const servicesList = root.querySelector("#servicesList");
  if (!servicesList) {
    return { start() {}, stop() {} };
  }

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  const services = [
    {
      id: "go2rtc",
      label: "go2rtc",
      fetchStatus: () => api("/api/go2rtc/status"),
      start: () => api("/api/go2rtc/start", { method: "POST" }),
      stop: () => api("/api/go2rtc/stop", { method: "POST" }),
      reset: async () => {
        const pollStatus = async (tries = 3, delay = 300) => {
          let last;
          for (let i = 0; i < tries; i++) {
            last = await api("/api/go2rtc/status");
            if (last.running) return last;
            await sleep(delay);
          }
          return last;
        };

        const res = await api("/api/go2rtc/reload", { method: "POST" });
        await sleep(300);
        let status = await pollStatus();
        if (status?.running) return status;

        await api("/api/go2rtc/start", { method: "POST" });
        await sleep(400);
        status = await pollStatus(5, 400);
        return status;
      },
      autostartKey: "autostart_go2rtc",
    },
  ];

  const serviceRowRefs = {};
  let servicesInitialized = false;
  let serviceRefreshTimer = null;
  let webSettings = {};

  async function loadWebSettings() {
    try {
      webSettings = await api("/api/settings");
    } catch {
      webSettings = {};
    }
  }

  async function saveAutostart(def, enabled) {
    if (!def.autostartKey) return;
    try {
      await api("/api/settings", { method: "PUT", body: JSON.stringify({ [def.autostartKey]: enabled }) });
      webSettings[def.autostartKey] = enabled;
    } catch (err) {
      toast(err.message || "Failed to save autostart", "error");
    }
  }

  function buildServiceRow(def) {
    const row = document.createElement("div");
    row.className = "list-group-item py-2";
    row.innerHTML = `
      <div class="d-grid align-items-center" style="grid-template-columns: 1fr 70px 90px 80px 90px; gap: 0.35rem;">
        <div class="fw-semibold">${def.label}</div>
        <div class="text-center">
          <span class="badge text-bg-secondary service-badge">unknown</span>
        </div>
        <div class="text-center">
          <div class="form-check form-switch d-inline-flex align-items-center">
            <input class="form-check-input" type="checkbox" role="switch">
          </div>
        </div>
        <div class="text-center">
          <button class="btn btn-sm btn-outline-secondary"><i class="bi bi-arrow-repeat"></i></button>
        </div>
        <div class="text-center">
          <div class="form-check form-switch d-inline-flex align-items-center">
            <input class="form-check-input" type="checkbox" role="switch">
          </div>
        </div>
      </div>
      <div class="text-muted small mt-1"></div>
      <div class="text-muted small"></div>
    `;

    const toggle = row.querySelector(".form-check-input");
    const badge = row.querySelector(".service-badge");
    const msg = row.querySelectorAll(".text-muted.small")[0];
    const details = row.querySelectorAll(".text-muted.small")[1];
    const resetBtn = row.querySelector("button");
    const autostartToggle = row.querySelectorAll(".form-check-input")[1];

    toggle.addEventListener("change", async () => {
      toggle.disabled = true;
      resetBtn.disabled = true;
      try {
        if (toggle.checked) {
          await def.start();
          await refreshService(def);
        } else {
          await def.stop();
          await refreshService(def);
        }
      } catch (err) {
        toggle.checked = !toggle.checked;
        toast(`${def.label}: ${err.message}`, "error");
      } finally {
        toggle.disabled = false;
        resetBtn.disabled = false;
      }
    });

    resetBtn.addEventListener("click", async () => {
      if (!def.reset) return;
      resetBtn.disabled = true;
      resetBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
      try {
        await def.reset();
        const data = await refreshService(def);
        if (def.onUpdate) def.onUpdate(data);
        toast(`${def.label} reloaded`);
      } catch (err) {
        toast(`${def.label}: ${err.message}`, "error");
      } finally {
        resetBtn.disabled = false;
        resetBtn.innerHTML = '<i class="bi bi-arrow-repeat"></i>';
      }
    });

    if (!def.reset) {
      resetBtn.disabled = true;
      resetBtn.title = "Reload not available";
    }

    if (autostartToggle && def.autostartKey) {
      autostartToggle.addEventListener("change", (e) => {
        saveAutostart(def, e.target.checked);
      });
    }

    serviceRowRefs[def.id] = { row, toggle, badge, msg, details, resetBtn, autostartToggle };
    servicesList.appendChild(row);
  }

  function updateServiceRow(def, data) {
    const ref = serviceRowRefs[def.id];
    if (!ref) return;
    const running = !!data.running;
    ref.toggle.checked = running;
    ref.badge.textContent = running ? "running" : "stopped";
    ref.badge.className = running ? "badge text-bg-success service-badge" : "badge text-bg-secondary service-badge";
    ref.msg.textContent = "";
    const pidText = data.pid ? `pid ${data.pid}` : "pid: -";
    const binText = data.binary_path ? `bin:${data.binary_path}` : "bin:missing";
    const cfgText = data.config_path ? `config:${data.config_path}${data.config_exists ? "" : " (missing)"}` : "config:missing";
    const streamsText = data.streams_total != null ? `streams:${data.streams_total}` : "";
    ref.details.innerHTML = `
      <i class="bi bi-cpu me-1"></i>${pidText}
      &bull; <i class="bi bi-hdd-stack me-1"></i>${binText}
      &bull; <i class="bi bi-gear-wide-connected me-1"></i>${cfgText}
      ${streamsText ? `&bull; <i class="bi bi-camera-video me-1"></i>${streamsText}` : ""}
    `;
    if (ref.autostartToggle && def.autostartKey) {
      const desired = webSettings[def.autostartKey];
      ref.autostartToggle.checked = !!desired;
    }
  }

  async function refreshService(def) {
    const data = await def.fetchStatus();
    updateServiceRow(def, data);
    if (def.onUpdate) def.onUpdate(data);
    return data;
  }

  async function refreshAllServices() {
    await Promise.all(
      services.map((def) =>
        refreshService(def).catch((err) => toast(`${def.label} status failed: ${err.message}`, "error"))
      )
    );
  }

  function ensureInitialized() {
    if (servicesInitialized) return;
    services.forEach(buildServiceRow);
    servicesInitialized = true;
  }

  async function start() {
    ensureInitialized();
    await loadWebSettings();
    refreshAllServices();
    if (!serviceRefreshTimer) {
      serviceRefreshTimer = setInterval(refreshAllServices, 4000);
    }
  }

  function stop() {
    if (serviceRefreshTimer) {
      clearInterval(serviceRefreshTimer);
      serviceRefreshTimer = null;
    }
  }

  return { start, stop };
}
