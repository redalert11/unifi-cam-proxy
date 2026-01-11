export function initRuntimeTab(root, { api }) {
  const runtimeStartBtn = root.querySelector("#runtimeStartBtn");
  const runtimeStopBtn = root.querySelector("#runtimeStopBtn");
  const runtimeDiscoveryStartBtn = root.querySelector("#runtimeDiscoveryStartBtn");
  const runtimeDiscoveryStopBtn = root.querySelector("#runtimeDiscoveryStopBtn");
  const runtimeMsg = root.querySelector("#runtimeMsg");
  const runtimeApiStatus = root.querySelector("#runtimeApiStatus");
  const runtimeDiscoveryStatus = root.querySelector("#runtimeDiscoveryStatus");
  const runtimeUploadStatus = root.querySelector("#runtimeUploadStatus");
  const runtimeWssStatus = root.querySelector("#runtimeWssStatus");
  const runtimeWssStartBtn = root.querySelector("#runtimeWssStartBtn");
  const runtimeWssStopBtn = root.querySelector("#runtimeWssStopBtn");
  const runtimeWssMsg = root.querySelector("#runtimeWssMsg");
  const runtimeWssDetail = root.querySelector("#runtimeWssDetail");

  function setStatus(el, text) {
    if (!el) return;
    el.textContent = text;
  }

  function renderStatus(status) {
    if (!status) return;
    const apiStatus = status.api || {};
    const discovery = status.discovery || {};
    const upload = status.upload || {};
    const wss = status.wss || {};

    setStatus(
      runtimeApiStatus,
      `API: ${apiStatus.running ? "running" : "stopped"} on ${apiStatus.port || "?"} (${apiStatus.use_ssl ? "https" : "http"})`
    );
    setStatus(
      runtimeDiscoveryStatus,
      `Discovery: ${discovery.running ? "running" : "stopped"} | canAdopt=${discovery.can_adopt ? "true" : "false"}`
    );
    setStatus(runtimeUploadStatus, `Upload: ${upload.running ? "running" : "stopped"} on ${upload.port || "?"}`);
    setStatus(
      runtimeWssStatus,
      `WSS: ${wss.running ? "running" : "stopped"} | token=${wss.token_present ? "yes" : "no"} | host=${wss.host || "?"}`
    );
    setStatus(
      runtimeWssDetail,
      `WSS: ${wss.running ? "running" : "stopped"} | token=${wss.token_present ? "yes" : "no"} | host=${wss.host || "?"}`
    );
  }

  async function refreshStatus() {
    try {
      const status = await api("/api/unifi/status");
      renderStatus(status);
      return status;
    } catch (err) {
      setStatus(runtimeApiStatus, "API: unavailable");
      setStatus(runtimeDiscoveryStatus, "Discovery: unavailable");
      setStatus(runtimeUploadStatus, "Upload: unavailable");
      setStatus(runtimeWssStatus, "WSS: unavailable");
      setStatus(runtimeWssDetail, "WSS: unavailable");
      if (runtimeMsg) {
        runtimeMsg.textContent = err.message || "UniFi status unavailable.";
        runtimeMsg.className = "text-danger small";
      }
      return null;
    }
  }

  runtimeStartBtn?.addEventListener("click", async () => {
    if (runtimeMsg) {
      runtimeMsg.textContent = "Starting UniFi services…";
      runtimeMsg.className = "text-muted small";
    }
    try {
      await api("/api/unifi/runtime/start", { method: "POST" });
      await refreshStatus();
      if (runtimeMsg) {
        runtimeMsg.textContent = "UniFi services started.";
        runtimeMsg.className = "text-success small";
      }
    } catch (err) {
      if (runtimeMsg) {
        runtimeMsg.textContent = err.message || "Failed to start services.";
        runtimeMsg.className = "text-danger small";
      }
    }
  });

  runtimeStopBtn?.addEventListener("click", async () => {
    if (runtimeMsg) {
      runtimeMsg.textContent = "Stopping UniFi services…";
      runtimeMsg.className = "text-muted small";
    }
    try {
      await api("/api/unifi/runtime/stop", { method: "POST" });
      await refreshStatus();
      if (runtimeMsg) {
        runtimeMsg.textContent = "UniFi services stopped.";
        runtimeMsg.className = "text-success small";
      }
    } catch (err) {
      if (runtimeMsg) {
        runtimeMsg.textContent = err.message || "Failed to stop services.";
        runtimeMsg.className = "text-danger small";
      }
    }
  });

  runtimeDiscoveryStartBtn?.addEventListener("click", async () => {
    if (runtimeMsg) {
      runtimeMsg.textContent = "Starting discovery…";
      runtimeMsg.className = "text-muted small";
    }
    try {
      await api("/api/unifi/discovery/start", { method: "POST" });
      await refreshStatus();
      if (runtimeMsg) {
        runtimeMsg.textContent = "Discovery started.";
        runtimeMsg.className = "text-success small";
      }
    } catch (err) {
      if (runtimeMsg) {
        runtimeMsg.textContent = err.message || "Failed to start discovery.";
        runtimeMsg.className = "text-danger small";
      }
    }
  });

  runtimeDiscoveryStopBtn?.addEventListener("click", async () => {
    if (runtimeMsg) {
      runtimeMsg.textContent = "Stopping discovery…";
      runtimeMsg.className = "text-muted small";
    }
    try {
      await api("/api/unifi/discovery/stop", { method: "POST" });
      await refreshStatus();
      if (runtimeMsg) {
        runtimeMsg.textContent = "Discovery stopped.";
        runtimeMsg.className = "text-success small";
      }
    } catch (err) {
      if (runtimeMsg) {
        runtimeMsg.textContent = err.message || "Failed to stop discovery.";
        runtimeMsg.className = "text-danger small";
      }
    }
  });

  runtimeWssStartBtn?.addEventListener("click", async () => {
    if (runtimeWssMsg) {
      runtimeWssMsg.textContent = "Starting WSS…";
      runtimeWssMsg.className = "text-muted small";
    }
    try {
      await api("/api/unifi/wss/start", { method: "POST" });
      await refreshStatus();
      if (runtimeWssMsg) {
        runtimeWssMsg.textContent = "WSS started.";
        runtimeWssMsg.className = "text-success small";
      }
    } catch (err) {
      if (runtimeWssMsg) {
        runtimeWssMsg.textContent = err.message || "Failed to start WSS.";
        runtimeWssMsg.className = "text-danger small";
      }
    }
  });

  runtimeWssStopBtn?.addEventListener("click", async () => {
    if (runtimeWssMsg) {
      runtimeWssMsg.textContent = "Stopping WSS…";
      runtimeWssMsg.className = "text-muted small";
    }
    try {
      await api("/api/unifi/wss/stop", { method: "POST" });
      await refreshStatus();
      if (runtimeWssMsg) {
        runtimeWssMsg.textContent = "WSS stopped.";
        runtimeWssMsg.className = "text-success small";
      }
    } catch (err) {
      if (runtimeWssMsg) {
        runtimeWssMsg.textContent = err.message || "Failed to stop WSS.";
        runtimeWssMsg.className = "text-danger small";
      }
    }
  });

  return {
    async start() {
      await refreshStatus();
    },
    stop() {},
  };
}
