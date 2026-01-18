export function initUnifiLogsTab(root, { api, toast, filterLinesByLevel }) {
  const unifiLogSource = root.querySelector("#unifiLogSource");
  const unifiLogCamera = root.querySelector("#unifiLogCamera");
  const unifiLogChannel = root.querySelector("#unifiLogChannel");
  const unifiLogLines = root.querySelector("#unifiLogLines");
  const unifiLogLevel = root.querySelector("#unifiLogLevel");
  const unifiLogRefresh = root.querySelector("#unifiLogRefresh");
  const unifiLogFollow = root.querySelector("#unifiLogFollow");
  const unifiLogBox = root.querySelector("#unifiLogBox");

  let unifiFollowTimer = null;

  function stopUnifiFollow() {
    if (unifiFollowTimer) {
      clearInterval(unifiFollowTimer);
      unifiFollowTimer = null;
    }
    if (unifiLogFollow) {
      unifiLogFollow.classList.remove("btn-success");
      unifiLogFollow.classList.add("btn-outline-success");
      unifiLogFollow.innerHTML = '<i class="bi bi-play-fill me-1"></i>Follow';
    }
  }

  function getUnifiSourceValue() {
    const source = unifiLogSource?.value || "main";
    if (source !== "wss") return source;
    const mac = unifiLogCamera?.value;
    if (!mac) return "";
    const channel = unifiLogChannel?.value || "general";
    if (channel === "tcp_in") return `wss.${mac}.tcp_in`;
    if (channel === "tcp_out") return `wss.${mac}.tcp_out`;
    return `wss.${mac}`;
  }

  async function loadUnifiCameras() {
    if (!unifiLogCamera) return;
    try {
      const data = await api("/api/unifi/cameras");
      const cameras = data?.cameras || [];
      unifiLogCamera.innerHTML = "";
      cameras.forEach((cam) => {
        if (!cam.mac) return;
        const opt = document.createElement("option");
        opt.value = cam.mac;
        opt.textContent = cam.name ? `${cam.name} (${cam.mac})` : cam.mac;
        unifiLogCamera.appendChild(opt);
      });
      if (!unifiLogCamera.options.length) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "No cameras";
        unifiLogCamera.appendChild(opt);
      }
    } catch {
      unifiLogCamera.innerHTML = '<option value="">No cameras</option>';
    }
  }

  function syncUnifiSourceUI() {
    if (!unifiLogSource || !unifiLogCamera || !unifiLogChannel) return;
    const isWss = unifiLogSource.value === "wss";
    unifiLogCamera.classList.toggle("d-none", !isWss);
    unifiLogChannel.classList.toggle("d-none", !isWss);
  }

  async function loadUnifiLogs({ scroll = false } = {}) {
    if (!unifiLogBox || !unifiLogLines || !unifiLogRefresh) return;
    let lines = parseInt(unifiLogLines.value, 10);
    if (Number.isNaN(lines)) lines = 200;
    lines = Math.min(2000, Math.max(10, lines));
    unifiLogLines.value = lines;

    const source = getUnifiSourceValue();
    if (!source) {
      unifiLogBox.textContent = "Select a camera for WSS logs.";
      return;
    }

    const resetBtn = () => (unifiLogRefresh.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Refresh');
    unifiLogRefresh.disabled = true;
    unifiLogRefresh.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const data = await api(`/api/unifi/logs?source=${encodeURIComponent(source)}&lines=${lines}`);
      const filtered = filterLinesByLevel(data?.lines || [], unifiLogLevel?.value || "ALL");
      unifiLogBox.textContent = filtered.length ? filtered.join("\n") : "(empty)";
      if (scroll) {
        unifiLogBox.scrollTop = unifiLogBox.scrollHeight;
      }
    } catch (err) {
      unifiLogBox.textContent = "Failed to load UniFi logs.";
      toast(err.message || "UniFi log fetch failed", "error");
    } finally {
      unifiLogRefresh.disabled = false;
      resetBtn();
    }
  }

  if (unifiLogBox) {
    unifiLogBox.addEventListener("keydown", (event) => {
      const isSelectAll = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a";
      if (!isSelectAll) return;
      event.preventDefault();
      const selection = window.getSelection();
      if (!selection) return;
      const range = document.createRange();
      range.selectNodeContents(unifiLogBox);
      selection.removeAllRanges();
      selection.addRange(range);
    });
  }

  if (unifiLogSource) {
    unifiLogSource.addEventListener("change", () => {
      syncUnifiSourceUI();
      loadUnifiLogs();
    });
  }

  if (unifiLogCamera) {
    unifiLogCamera.addEventListener("change", () => loadUnifiLogs());
  }

  if (unifiLogChannel) {
    unifiLogChannel.addEventListener("change", () => loadUnifiLogs());
  }

  if (unifiLogLevel) {
    unifiLogLevel.addEventListener("change", () => loadUnifiLogs());
  }

  if (unifiLogRefresh) {
    unifiLogRefresh.addEventListener("click", () => loadUnifiLogs());
  }

  if (unifiLogFollow) {
    unifiLogFollow.addEventListener("click", () => {
      const enable = !unifiFollowTimer;
      if (enable) {
        unifiLogFollow.classList.replace("btn-outline-success", "btn-success");
        unifiLogFollow.innerHTML = '<i class="bi bi-stop-fill me-1"></i>Stop';
        loadUnifiLogs({ scroll: true });
        unifiFollowTimer = setInterval(() => loadUnifiLogs({ scroll: true }), 4000);
      } else {
        stopUnifiFollow();
      }
    });
  }

  return {
    start() {
      loadUnifiCameras();
      syncUnifiSourceUI();
      loadUnifiLogs();
    },
    stop() {
      stopUnifiFollow();
    },
  };
}
