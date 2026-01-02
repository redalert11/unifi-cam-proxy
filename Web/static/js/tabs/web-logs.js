export function initWebLogsTab(root, { api, toast, filterLinesByLevel }) {
  const webLogLinesInput = root.querySelector("#webLogLines");
  const webLogRefreshBtn = root.querySelector("#webLogRefresh");
  const webLogBox = root.querySelector("#webLogBox");
  const webLogPathBadge = root.querySelector("#webLogPathBadge");
  const webLogDownload = root.querySelector("#webLogDownload");
  const webLogLevelSelect = root.querySelector("#webLogLevel");
  const webLogFollowBtn = root.querySelector("#webLogFollow");

  if (!webLogRefreshBtn || !webLogLinesInput || !webLogBox) {
    return { start() {}, stop() {} };
  }

  let webLogFollowTimer = null;

  async function loadWebLogs({ scroll = false } = {}) {
    let lines = parseInt(webLogLinesInput.value, 10);
    if (Number.isNaN(lines)) lines = 200;
    lines = Math.min(2000, Math.max(10, lines));
    webLogLinesInput.value = lines;

    const resetBtn = () => (webLogRefreshBtn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Refresh');
    webLogRefreshBtn.disabled = true;
    webLogRefreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const data = await api(`/api/web/logs?lines=${lines}`);
      const filtered = filterLinesByLevel(data?.lines || [], webLogLevelSelect?.value || "ALL");
      webLogBox.textContent = filtered.length ? filtered.join("\n") : "(empty)";
      if (scroll) {
        webLogBox.scrollTop = webLogBox.scrollHeight;
      }
      if (webLogPathBadge && (data?.path_rel || data?.path)) {
        webLogPathBadge.textContent = `path: ${data.path_rel || data.path}`;
        if (webLogDownload) webLogDownload.href = "/api/web/logs/download";
      }
    } catch (err) {
      webLogBox.textContent = "Failed to load logs.";
      toast(err.message || "Log fetch failed", "error");
    } finally {
      webLogRefreshBtn.disabled = false;
      resetBtn();
    }
  }

  webLogRefreshBtn.addEventListener("click", () => loadWebLogs());

  if (webLogLevelSelect) {
    webLogLevelSelect.addEventListener("change", () => {
      localStorage.setItem("webLogLevel", webLogLevelSelect.value);
      loadWebLogs();
    });
  }

  const toggleWebLogFollow = () => {
    const enable = !webLogFollowTimer;
    if (enable) {
      webLogFollowBtn?.classList.replace("btn-outline-success", "btn-success");
      if (webLogFollowBtn) webLogFollowBtn.innerHTML = '<i class="bi bi-stop-fill me-1"></i>Stop';
      loadWebLogs({ scroll: true });
      webLogFollowTimer = setInterval(() => loadWebLogs({ scroll: true }), 4000);
    } else {
      webLogFollowBtn?.classList.replace("btn-success", "btn-outline-success");
      if (webLogFollowBtn) webLogFollowBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Follow';
      clearInterval(webLogFollowTimer);
      webLogFollowTimer = null;
    }
  };

  if (webLogFollowBtn) {
    webLogFollowBtn.addEventListener("click", toggleWebLogFollow);
  }

  if (webLogLevelSelect) {
    const saved = localStorage.getItem("webLogLevel");
    if (saved) webLogLevelSelect.value = saved;
  }

  function stopFollow() {
    if (webLogFollowTimer) {
      clearInterval(webLogFollowTimer);
      webLogFollowTimer = null;
    }
    if (webLogFollowBtn) {
      webLogFollowBtn.classList.remove("btn-success");
      webLogFollowBtn.classList.add("btn-outline-success");
      webLogFollowBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Follow';
    }
  }

  return {
    start() {},
    stop() {
      stopFollow();
    },
  };
}
