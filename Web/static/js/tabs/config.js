export function initConfigTab(root, { api, toast }) {
  const configBox = root.querySelector("#configBox");
  const loadCfgBtn = root.querySelector("#loadCfgBtn");
  const saveCfgBtn = root.querySelector("#saveCfgBtn");
  if (!configBox || !loadCfgBtn || !saveCfgBtn) {
    return { start() {}, stop() {} };
  }

  let loadedOnce = false;

  async function loadConfig() {
    try {
      const data = await api("/api/go2rtc/config");
      configBox.value = data.content || "";
    } catch (err) {
      toast("No config found yet.", "error");
      configBox.value = "";
    }
  }

  async function saveConfig() {
    const content = configBox.value;
    await api("/api/go2rtc/config", { method: "PUT", body: JSON.stringify({ content }) });
    toast("Config saved");
  }

  loadCfgBtn.addEventListener("click", loadConfig);
  saveCfgBtn.addEventListener("click", async () => {
    try {
      await saveConfig();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  return {
    start() {
      if (!loadedOnce) {
        loadConfig();
        loadedOnce = true;
      }
    },
    stop() {},
  };
}
