export function initDashboardTab(root, { api, buildFrameUrl }) {
  const camCards = root.querySelector("#camCards");
  const camCardsRefresh = root.querySelector("#camCardsRefresh");
  const camGridSize = root.querySelector("#camGridSize");
  if (!camCards) {
    return { start() {}, stop() {} };
  }
  const storageKey = "camCardOrder";
  const gridStorageKey = "camGridSize";
  let sortableInstance = null;

  function loadGridSize() {
    const saved = localStorage.getItem(gridStorageKey);
    return saved || "3";
  }

  function gridClassesFor(size) {
    switch (size) {
      case "1":
        return ["col-12"];
      case "2":
        return ["col-12", "col-md-6"];
      case "4":
        return ["col-12", "col-md-6", "col-xl-3"];
      case "6":
        return ["col-12", "col-md-6", "col-lg-4", "col-xl-2"];
      case "3":
      default:
        return ["col-12", "col-md-6", "col-xl-4"];
    }
  }

  function applyGridSize(size) {
    const classes = gridClassesFor(size);
    camCards.querySelectorAll(".cam-card").forEach((card) => {
      card.className = "cam-card";
      classes.forEach((cls) => card.classList.add(cls));
    });
    if (camGridSize) {
      camGridSize.value = size;
    }
    localStorage.setItem(gridStorageKey, size);
  }

  function loadOrder() {
    try {
      const raw = localStorage.getItem(storageKey);
      const parsed = raw ? JSON.parse(raw) : [];
      return Array.isArray(parsed) ? parsed : [];
    } catch {
      return [];
    }
  }

  function saveOrder() {
    try {
      const orderedNames = Array.from(
        camCards.querySelectorAll("[data-cam-name]")
      ).map((el) => el.dataset.camName);
      localStorage.setItem(storageKey, JSON.stringify(orderedNames));
    } catch {
      // Ignore storage failures (private mode, quota, etc.)
    }
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
    const savedOrder = loadOrder();
    const nameSet = new Set(names);
    const orderedNames = [
      ...savedOrder.filter((name) => nameSet.has(name)),
      ...names.filter((name) => !savedOrder.includes(name)),
    ];
    camCards.innerHTML = "";
    orderedNames.forEach((name) => {
      const card = document.createElement("div");
      card.className = "cam-card";
      card.dataset.camName = name;
      const imgUrl = buildFrameUrl(name, true);
      const linkUrl = buildFrameUrl(name, false);
      card.innerHTML = `
        <div class="card shadow-sm h-100">
          <div class="card-header d-flex align-items-center justify-content-between">
            <span class="fw-semibold d-inline-flex align-items-center">
              <span class="cam-drag-handle me-2" title="Drag to reorder" aria-label="Drag to reorder" role="button">
                <svg class="cam-drag-icon" width="12" height="18" viewBox="0 0 12 18" aria-hidden="true" focusable="false">
                  <circle cx="3" cy="3" r="1.5"></circle>
                  <circle cx="9" cy="3" r="1.5"></circle>
                  <circle cx="3" cy="9" r="1.5"></circle>
                  <circle cx="9" cy="9" r="1.5"></circle>
                  <circle cx="3" cy="15" r="1.5"></circle>
                  <circle cx="9" cy="15" r="1.5"></circle>
                </svg>
              </span>
              ${name}
            </span>
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
    applyGridSize(loadGridSize());

    if (window.Sortable) {
      if (sortableInstance) {
        sortableInstance.destroy();
      }
      sortableInstance = new window.Sortable(camCards, {
        animation: 150,
        draggable: ".cam-card",
        ghostClass: "sortable-ghost",
        chosenClass: "sortable-chosen",
        onEnd: saveOrder,
      });
    }
  }

  camCardsRefresh?.addEventListener("click", renderCameraCards);
  camGridSize?.addEventListener("change", (event) => {
    applyGridSize(event.target.value);
  });

  return {
    start() {
      if (camGridSize) {
        camGridSize.value = loadGridSize();
      }
      renderCameraCards();
    },
    stop() {},
  };
}
