export function initDashboardTab(root, { api, buildFrameUrl }) {
  const camCards = root.querySelector("#camCards");
  const camCardsRefresh = root.querySelector("#camCardsRefresh");
  if (!camCards) {
    return { start() {}, stop() {} };
  }

  async function loadStreamsList() {
    try {
      const data = await api("/api/go2rtc/streams");
      let streams = {};
      if (data && data.streams) {
        if (Array.isArray(data.streams)) {
          data.streams.forEach((name) => (streams[name] = {}));
        } else if (typeof data.streams === "object") {
          streams = data.streams;
        }
      }
      return streams;
    } catch {
      return {};
    }
  }

  async function renderCameraCards() {
    camCards.innerHTML = '<div class="text-muted small">Loading cameras…</div>';
    const streams = await loadStreamsList();
    const names = Object.keys(streams || {});
    if (!names.length) {
      camCards.innerHTML = '<div class="text-muted small">No streams configured in go2rtc.</div>';
      return;
    }
    camCards.innerHTML = "";
    names.forEach((name) => {
      const card = document.createElement("div");
      card.className = "col-12 col-md-6 col-xl-4";
      const imgUrl = buildFrameUrl(name, true);
      const linkUrl = buildFrameUrl(name, false);
      card.innerHTML = `
        <div class="card shadow-sm h-100">
          <div class="card-header d-flex align-items-center justify-content-between">
            <span class="fw-semibold">${name}</span>
            <a class="small text-decoration-none" href="${linkUrl}" target="_blank" rel="noopener">Snapshot</a>
          </div>
          <div class="card-body">
            <div class="ratio ratio-16x9 bg-body-tertiary rounded overflow-hidden">
              <img src="${imgUrl}" alt="${name} snapshot" class="w-100 h-100 object-fit-cover" loading="lazy" onerror="this.style.display='none'; this.closest('.card-body').querySelector('.snapshot-fallback').classList.remove('d-none');">
              <div class="snapshot-fallback text-muted small d-none d-flex align-items-center justify-content-center">Snapshot unavailable</div>
            </div>
          </div>
        </div>
      `;
      camCards.appendChild(card);
    });
  }

  camCardsRefresh?.addEventListener("click", renderCameraCards);

  return {
    start() {
      renderCameraCards();
    },
    stop() {},
  };
}
