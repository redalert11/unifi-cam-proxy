export function initLogsTab(root, { api, toast, filterLinesByLevel }) {
  const logLinesInput = root.querySelector("#logLines");
  const logRefreshBtn = root.querySelector("#logRefresh");
  const logBox = root.querySelector("#logBox");
  const logPathBadge = root.querySelector("#logPathBadge");
  const logDownload = root.querySelector("#logDownload");
  const logLevelSelect = root.querySelector("#logLevel");
  const logFollowBtn = root.querySelector("#logFollow");

  if (!logRefreshBtn || !logLinesInput || !logBox) {
    return { start() {}, stop() {} };
  }

  let logFollowTimer = null;

  async function loadLogs({ scroll = false } = {}) {
    let lines = parseInt(logLinesInput.value, 10);
    if (Number.isNaN(lines)) lines = 200;
    lines = Math.min(2000, Math.max(10, lines));
    logLinesInput.value = lines;

    const resetBtn = () => (logRefreshBtn.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>Refresh');
    logRefreshBtn.disabled = true;
    logRefreshBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const data = await api(`/api/go2rtc/logs?lines=${lines}`);
      const filtered = filterLinesByLevel(data?.lines || [], logLevelSelect?.value || "ALL");
      logBox.textContent = filtered.length ? filtered.join("\n") : "(empty)";
      if (scroll) {
        logBox.scrollTop = logBox.scrollHeight;
      }
      if (logPathBadge && (data?.path_rel || data?.path)) {
        logPathBadge.textContent = `path: ${data.path_rel || data.path}`;
        if (logDownload) logDownload.href = "/api/go2rtc/logs/download";
      }
    } catch (err) {
      logBox.textContent = "Failed to load logs.";
      toast(err.message || "Log fetch failed", "error");
    } finally {
      logRefreshBtn.disabled = false;
      resetBtn();
    }
  }

  logRefreshBtn.addEventListener("click", () => loadLogs());

  if (logLevelSelect) {
    logLevelSelect.addEventListener("change", () => {
      localStorage.setItem("logLevel", logLevelSelect.value);
      loadLogs();
    });
  }

  const toggleLogFollow = () => {
    const enable = !logFollowTimer;
    if (enable) {
      logFollowBtn?.classList.replace("btn-outline-success", "btn-success");
      if (logFollowBtn) logFollowBtn.innerHTML = '<i class="bi bi-stop-fill me-1"></i>Stop';
      loadLogs({ scroll: true });
      logFollowTimer = setInterval(() => loadLogs({ scroll: true }), 4000);
    } else {
      logFollowBtn?.classList.replace("btn-success", "btn-outline-success");
      if (logFollowBtn) logFollowBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Follow';
      clearInterval(logFollowTimer);
      logFollowTimer = null;
    }
  };

  if (logFollowBtn) {
    logFollowBtn.addEventListener("click", toggleLogFollow);
  }

  if (logLevelSelect) {
    const saved = localStorage.getItem("logLevel");
    if (saved) logLevelSelect.value = saved;
  }

  function stopFollow() {
    if (logFollowTimer) {
      clearInterval(logFollowTimer);
      logFollowTimer = null;
    }
    if (logFollowBtn) {
      logFollowBtn.classList.remove("btn-success");
      logFollowBtn.classList.add("btn-outline-success");
      logFollowBtn.innerHTML = '<i class="bi bi-play-fill me-1"></i>Follow';
    }
  }

  return {
    start() {},
    stop() {
      stopFollow();
    },
  };
}
