export function initCamerasTab(root, { api, toast, buildFrameUrl }) {
  const camNameList = root.querySelector("#camNameList");
  const camStreamsContainer = root.querySelector("#camStreamsContainer");
  const camFriendlyInput = root.querySelector("#camFriendly");
  const camAddExtraBtn2 = root.querySelector("#camAddExtraBtn2");
  const camAddAllBtn = root.querySelector("#camAddAllBtn");
  const camPersistBtnOnvif = root.querySelector("#camPersistBtnOnvif");
  const camPersistBtn = root.querySelector("#camPersistBtn");
  const camModeOnvifBtn = root.querySelector("#camModeOnvifBtn");
  const camModeTapoBtn = root.querySelector("#camModeTapoBtn");
  const camModeRtspBtn = root.querySelector("#camModeRtspBtn");
  const camOnvifSection = root.querySelector("#camOnvifSection");
  const camTapoSection = root.querySelector("#camTapoSection");
  const camRtspSection = root.querySelector("#camRtspSection");
  const camOnvifScanBtn = root.querySelector("#camOnvifScanBtn");
  const camOnvifScanMsg = root.querySelector("#camOnvifScanMsg");
  const camOnvifSelect = root.querySelector("#camOnvifSelect");
  const camOnvifUser = root.querySelector("#camOnvifUser");
  const camOnvifPass = root.querySelector("#camOnvifPass");
  const camOnvifAddBtn = root.querySelector("#camOnvifAddBtn");
  const camOnvifMsg = root.querySelector("#camOnvifMsg");
  const camOnvifProfiles = root.querySelector("#camOnvifProfiles");
  const camTapoHost = root.querySelector("#camTapoHost");
  const camTapoCloudPass = root.querySelector("#camTapoCloudPass");
  const camTapoAccountPass = root.querySelector("#camTapoAccountPass");
  const camTapoAccountUser = root.querySelector("#camTapoAccountUser");
  const camTapoAuthCloud = root.querySelector("#camTapoAuthCloud");
  const camTapoAuthHash = root.querySelector("#camTapoAuthHash");
  const camTapoHash = root.querySelector("#camTapoHash");
  const camTapoUser = root.querySelector("#camTapoUser");
  const camTapoSubtype = root.querySelector("#camTapoSubtype");
  const camTapoPreview = root.querySelector("#camTapoPreview");
  const camTapoBuildBtn = root.querySelector("#camTapoBuildBtn");
  const camTapoAddBtn = root.querySelector("#camTapoAddBtn");
  const camTapoPersistBtn = root.querySelector("#camTapoPersistBtn");
  const camTapoMsg = root.querySelector("#camTapoMsg");
  const camStep1Status = root.querySelector("#camStep1Status");
  const camProbeBtn = root.querySelector("#camProbeBtn");
  const camProbeMsg = root.querySelector("#camProbeMsg");
  const camCompatibility = root.querySelector("#camCompatibility");
  const camStep2Status = root.querySelector("#camStep2Status");
  const camStep3Status = root.querySelector("#camStep3Status");
  const camForceTranscodeBtn = root.querySelector("#camForceTranscodeBtn");
  const camOnvifUpdateBtn = root.querySelector("#camOnvifUpdateBtn");
  const camProbePreviewSelect = root.querySelector("#camProbePreviewSelect");
  const camProbePreviewCard = root.querySelector("#camProbePreviewCard");
  const camProbeOutput = root.querySelector("#camProbeOutput");
  const camProbeResult = root.querySelector("#camProbeResult");
  const camProbeFullToggle = root.querySelector("#camProbeFullToggle");
  const camModelInput = root.querySelector("#camModelInput");
  const camModelValue = root.querySelector("#camModelValue");
  const camModelDropdown = root.querySelector("#camModelDropdown");
  const camStepHint = root.querySelector("#camStepHint");
  const camStream1Select = root.querySelector("#camStream1Select");
  const camStream2Select = root.querySelector("#camStream2Select");
  const camStream3Select = root.querySelector("#camStream3Select");
  const camGenerateSettingsBtn = root.querySelector("#camGenerateSettingsBtn");
  const camGenerateMacToggle = root.querySelector("#camGenerateMacToggle");
  const camGenerateProgress = root.querySelector("#camGenerateProgress");
  const camGenerateProgressLabel = root.querySelector("#camGenerateProgressLabel");
  const camDeleteSettingsBtn = root.querySelector("#camDeleteSettingsBtn");

  let webSettings = {};
  let streamsMap = {};
  let discoveredOnvif = [];
  let onvifGroupSources = [];
  let onvifGroupName = "";
  let initialized = false;
  let cameraModelMap = new Map();
  let cameraModelOptions = [];
  let resolvedSettingsFile = null;
  let lastProbeData = null;
  let lastProbeSelection = null;
  const stepHints = {
    camStep1Pane: "Add camera streams or load profiles to continue.",
    camStep2Pane: "Select a stream, then run the probe.",
    camStep3Pane: "G4 Dome camera loaded by default. Select a different model or map camera streams.",
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function setStepStatus(el, status, kind = "secondary") {
    if (el) {
      if (!status || status.toLowerCase() === "pending") {
        el.textContent = "";
        el.className = "text-muted small";
        return;
      }
      const normalized = String(status);
      const lowered = normalized.toLowerCase();
      const label =
        lowered === "passed" || lowered === "failed" || lowered === "pending"
          ? lowered.charAt(0).toUpperCase() + lowered.slice(1)
          : normalized;
      el.textContent = label;
      el.className = `text-muted small badge text-bg-${kind}`;
    }
  }

  function setStepHint(text) {
    if (!camStepHint) return;
    if (!text) {
      camStepHint.classList.add("d-none");
      camStepHint.textContent = "";
      return;
    }
    camStepHint.textContent = text;
    camStepHint.className = "border rounded p-2 bg-body-tertiary small mb-3";
  }

  function setForceTranscodeEnabled(enabled) {
    if (!camForceTranscodeBtn) return;
    camForceTranscodeBtn.disabled = !enabled;
  }

  function setOnvifUpdateEnabled(enabled) {
    if (!camOnvifUpdateBtn) return;
    camOnvifUpdateBtn.disabled = !enabled;
  }

  function describeCompatibility(info, srcHint = "") {
    if (!info) return { ok: false, message: "No probe data", kind: "warning" };

    const tracks = [];
    const pushTrack = (t) => t && tracks.push(t);
    let sawVideoHint = false;
    let sawAudioHint = false;

    const parseMediaString = (str) => {
      if (typeof str !== "string") return;
      const lower = str.toLowerCase();
      const isVideo = lower.includes("video");
      const isAudio = lower.includes("audio");
      const hasH264 = lower.includes("h264");
      const hasPcm = lower.includes("pcm") || lower.includes("mulaw");
      if (isVideo) sawVideoHint = true;
      if (isAudio) sawAudioHint = true;
      if (isVideo || isAudio) {
        pushTrack({
          codec_name: hasH264 ? "h264" : "",
          codec_type: isVideo ? "video" : "audio",
          profile: "",
          level: null,
          raw: str,
        });
      } else if (hasH264 || hasPcm) {
        pushTrack({
          codec_name: hasH264 ? "h264" : hasPcm ? "pcm_mulaw" : "",
          codec_type: hasH264 ? "video" : "audio",
          raw: str,
        });
      }
    };

    const walk = (obj, depth = 0) => {
      if (!obj || depth > 5) return;
      if (Array.isArray(obj)) {
        obj.forEach((v) => walk(v, depth + 1));
        return;
      }
      if (typeof obj === "string") {
        parseMediaString(obj);
        return;
      }
      if (typeof obj !== "object") return;

      if (obj.codec_name || obj.codec_type || obj.codecs || obj.codec || obj.mime) {
        pushTrack(obj);
      }
      if (obj.codec && typeof obj.codec === "object") pushTrack(obj.codec);

      const medias = [].concat(obj.media || []).concat(obj.medias || []);
      medias.forEach((m) => parseMediaString(m));

      ["tracks", "receivers", "senders", "producers", "streams"].forEach((key) => {
        if (Array.isArray(obj[key])) obj[key].forEach((v) => walk(v, depth + 1));
      });

      Object.values(obj).forEach((v) => {
        if (v && typeof v === "object" && !Array.isArray(v)) walk(v, depth + 1);
      });
    };

    walk(info, 0);

    const pickTrack = (kindWanted) =>
      tracks.find((t) => {
        const codecType = String(
          t.codec_type || t.type || t.kind || (t.codec && (t.codec.codec_type || t.codec.type || t.codec.kind)) || ""
        ).toLowerCase();
        const codecName = String(
          t.codec_name || t.codecs || t.codec || t.mime || (t.codec && (t.codec.codec_name || t.codec.codecs || t.codec.codec))
        ).toLowerCase();
        const raw = typeof t.raw === "string" ? t.raw.toLowerCase() : "";
        if (kindWanted === "video") {
          return codecType.includes("video") || codecName.includes("h264") || raw.includes("video");
        }
        return codecType.includes("audio") || codecName.includes("pcm") || codecName.includes("mulaw") || raw.includes("audio");
      });

    const video = pickTrack("video");
    const audio = pickTrack("audio");

    const videoCodecRaw =
      (video && (video.codec_name || video.codecs || video.codec || video.mime)) ||
      (video && video.codec && (video.codec.codec_name || video.codec.codec || video.codec.codecs));
    const videoCodec = typeof videoCodecRaw === "string" ? videoCodecRaw.toUpperCase() : "";
    const videoProfile =
      video && (video.profile || video.codec_profile || (video.codec && video.codec.profile))
        ? String(video.profile || video.codec_profile || (video.codec && video.codec.profile)).toUpperCase()
        : "";
    const videoLevel =
      video && (video.level || video.codec_level || (video.codec && video.codec.level)) != null
        ? Number(video.level || video.codec_level || (video.codec && video.codec.level))
        : null;

    let videoOk = false;
    let videoMsg = "No video track found";
    let videoKind = "warning";
    if (video) {
      if (!videoCodec.includes("H264")) {
        videoMsg = `Video codec ${videoCodec || "unknown"} (need H.264 Baseline)`;
        videoKind = "danger";
      } else {
        const isBaseline = videoProfile.includes("BASELINE");
        const levelOk = videoLevel === null || videoLevel <= 41;
        videoOk = isBaseline && levelOk;
        const levelText =
          videoLevel == null
            ? ""
            : videoLevel >= 10 && videoLevel % 10 === 0
            ? `@L${(videoLevel / 10).toFixed(1)}`
            : `@L${videoLevel}`;
        videoMsg = `Video: H.264 ${videoProfile || ""}${levelText}`;
        if (!isBaseline) {
          videoMsg += " (needs Baseline profile)";
          videoKind = "warning";
        } else if (!levelOk) {
          videoMsg += " (level high)";
          videoKind = "warning";
        } else {
          videoKind = "success";
        }
      }
    }

    const audioCodecRaw = audio && (audio.codec_name || audio.codecs || audio.codec || audio.mime || "");
    const audioCodec = typeof audioCodecRaw === "string" ? audioCodecRaw.toLowerCase() : "";
    const audioOk = audioCodec.includes("pcm") || audioCodec.includes("mulaw");
    let audioMsg = audio ? `Audio: ${audioCodec || "unknown"}` : sawAudioHint ? "Audio track present (codec unknown)" : "No audio track found";
    let audioKind = audioOk ? "success" : audio || sawAudioHint ? "warning" : "warning";
    if (!audioOk) audioMsg += " (target pcm_mulaw mono 8kHz)";

    const findResolution = (arr) => {
      if (!Array.isArray(arr)) return null;
      for (const t of arr) {
        if (t && typeof t === "object") {
          const w = t.width || t.coded_width || (t.codec && t.codec.width) || null;
          const h = t.height || t.coded_height || (t.codec && t.codec.height) || null;
          if (w || h) return { width: Number(w) || null, height: Number(h) || null };
        }
      }
      return null;
    };

    const res = findResolution(tracks);
    let resLabel = "";
    if (res && res.width) {
      if (res.width >= 3500) resLabel = "4K";
      else if (res.width >= 1900) resLabel = "HD";
      else if (res.width >= 900) resLabel = "SD";
      else resLabel = "low-res";
    }
    const hasAudio = !!audio;
    const isSnapshot = typeof srcHint === "string" && srcHint.toLowerCase().includes("snapshot");
    let streamLabel = isSnapshot ? "snapshot" : resLabel || (video ? "video" : "unknown");
    if (!isSnapshot) {
      streamLabel += hasAudio ? " (video+audio)" : video ? " (video only)" : "";
    }

    const formatName = (info && (info.format_name || (info.format && info.format.format_name))) || "";
    const container = typeof formatName === "string" ? formatName.toLowerCase() : "";

    const ok = videoOk && audioOk;
    const kind = videoKind === "danger" || audioKind === "danger" ? "danger" : ok ? "success" : "warning";
    const message = `${videoMsg}; ${audioMsg}`;
    const checklist = [
      {
        label: videoMsg,
        status: videoKind === "danger" ? "danger" : videoOk ? "success" : "warning",
      },
      {
        label: audioMsg,
        status: audioOk ? "success" : "warning",
      },
    ];
    if (resLabel || hasAudio || isSnapshot) {
      checklist.push({
        label: `Stream: ${streamLabel}`,
        status: "info",
      });
    }
    if (container) {
      checklist.push({
        label: `Container: ${container}`,
        status: container.includes("flv") ? "success" : "warning",
      });
    }

    return { ok, message, kind, streamLabel, checklist };
  }

  function renderFlightCheckList(data, level = 0) {
    if (data == null) return "";
    if (typeof data !== "object") {
      return `<span>${String(data)}</span>`;
    }
    if (Array.isArray(data)) {
      if (!data.length) return "<span>[]</span>";
      return `<ul class="mb-0 ps-${Math.min(4, level + 2)}">${data
        .map((item) => `<li>${renderFlightCheckList(item, level + 1)}</li>`)
        .join("")}</ul>`;
    }
    const entries = Object.entries(data);
    if (!entries.length) return "<span>{}</span>";
    return `<ul class="mb-0 ps-${Math.min(4, level + 2)}">${entries
      .map(([key, value]) => {
        const rendered = renderFlightCheckList(value, level + 1);
        return `<li><span class="fw-semibold">${key}:</span> ${rendered}</li>`;
      })
      .join("")}</ul>`;
  }

  function formatCameraModel(model) {
    if (!model) return "";
    const trimmed = model.replace(/^UVC_/, "");
    return trimmed
      .split("_")
      .filter(Boolean)
      .map((part) => {
        if (/^G\d+$/i.test(part)) return part.toUpperCase();
        if (part.toUpperCase() === part && part.length <= 4) return part;
        return part.charAt(0).toUpperCase() + part.slice(1).toLowerCase();
      })
      .join(" ");
  }

  function parseStreamSelection(value) {
    if (!value) return { name: "", channel: null };
    const marker = "::";
    const idx = value.lastIndexOf(marker);
    if (idx === -1) return { name: value, channel: null };
    const name = value.slice(0, idx);
    const channel = value.slice(idx + marker.length);
    return { name, channel: channel === "" ? null : Number(channel) };
  }

  function renderPreviewCard(selectionValue) {
    if (!camProbePreviewCard) return;
    const { name, channel } = parseStreamSelection(selectionValue);
    if (!name || !buildFrameUrl) {
      camProbePreviewCard.innerHTML = "";
      return;
    }
    const imgUrl = buildFrameUrl(name, true, channel);
    const linkUrl = buildFrameUrl(name, false, channel);
    const label = (webSettings[name] && webSettings[name].friendly) || name;
    const channelLabel = channel == null ? "" : ` (channel ${channel})`;
    camProbePreviewCard.innerHTML = `
      <div class="col-12 col-md-10 col-xl-8">
        <div class="card shadow-sm h-100">
          <div class="card-header d-flex align-items-center justify-content-between">
            <span class="fw-semibold">${label}${channelLabel}</span>
            <a class="small text-decoration-none" href="${linkUrl}" target="_blank" rel="noopener">Snapshot</a>
          </div>
          <div class="card-body">
            <div class="ratio ratio-16x9 bg-body-tertiary rounded overflow-hidden">
              <img src="${imgUrl}" alt="${label} snapshot" class="w-100 h-100 object-fit-cover" loading="lazy" onerror="this.style.display='none'; this.closest('.card-body').querySelector('.snapshot-fallback').classList.remove('d-none');">
              <div class="snapshot-fallback text-muted small d-none d-flex align-items-center justify-content-center">Snapshot unavailable</div>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  function updatePreviewSelect(selectedValue = "") {
    if (!camProbePreviewSelect) return;
    const current = selectedValue || camProbePreviewSelect.value || "";
    camProbePreviewSelect.innerHTML = '<option value="">Select a stream…</option>';
    const options = [];
    Object.keys(streamsMap || {}).forEach((name) => {
      const info = streamsMap[name] || {};
      const producers = Array.isArray(info.producers) ? info.producers : [];
      const friendly = (webSettings[name] && webSettings[name].friendly) || name;
      if (producers.length > 1) {
        producers.forEach((_, idx) => {
          options.push({
            value: `${name}::${idx}`,
            label: `${friendly} (channel ${idx})`,
          });
        });
      } else {
        options.push({ value: name, label: friendly });
      }
    });
    options.forEach((optItem) => {
      const opt = document.createElement("option");
      opt.value = optItem.value;
      opt.textContent = optItem.label;
      camProbePreviewSelect.appendChild(opt);
    });
    if (current) {
      camProbePreviewSelect.value = current;
    }
    if (camStream1Select) populateStreamSelect(camStream1Select);
    if (camStream2Select) populateStreamSelect(camStream2Select);
    if (camStream3Select) populateStreamSelect(camStream3Select);
    renderPreviewCard(camProbePreviewSelect.value);
  }

  function populateStreamSelect(selectEl) {
    const current = selectEl.value || "";
    selectEl.innerHTML = '<option value="">Select a stream…</option>';
    const options = Array.from(camProbePreviewSelect?.querySelectorAll("option") || []).filter(
      (opt) => opt.value
    );
    options.forEach((opt) => {
      const clone = document.createElement("option");
      clone.value = opt.value;
      clone.textContent = opt.textContent;
      selectEl.appendChild(clone);
    });
    if (current) selectEl.value = current;
  }

  async function probeRow(row) {
    const nameInput = row.querySelector(".stream-name");
    const msg = row.querySelector(".stream-msg");
    const rtspInput = row.querySelector(".stream-rtsp");
    const name = nameInput?.value.trim();
    if (!name) return null;
    try {
      if (msg) {
        msg.textContent = "Probing…";
        msg.className = "stream-msg text-muted small";
      }
      const data = await api(`/api/flightcheck/${encodeURIComponent(name)}`);
      const errs = [].concat(data.transport?.errors || []).concat(data.video?.errors || []).concat(data.audio?.errors || []);
      const warns = [].concat(data.transport?.warnings || []).concat(data.video?.warnings || []).concat(data.audio?.warnings || []);
      const goods = [].concat(data.transport?.ok || []).concat(data.video?.ok || []).concat(data.audio?.ok || []);
      const reports = [].concat(data.report || []);

      const checklist = describeCompatibility(data.probe, name).checklist
        .concat(goods.map((g) => ({ label: g, status: "success" })))
        .concat(errs.map((e) => ({ label: e, status: "warning" })))
        .concat(reports.map((r) => ({ label: r, status: "secondary" })));

      const html = `
        <div class="border rounded p-2 mb-2">
          <div class="d-flex justify-content-between align-items-center">
            <span class="fw-semibold">${name}</span>
            <span class="text-muted small">${rtspInput?.value ? "rtsp" : "onvif"}</span>
          </div>
          <div class="d-flex flex-wrap gap-2 mt-2">
            ${checklist
              .map(
                (c) => `
              <span class="badge text-bg-${
                c.status === "success"
                  ? "success"
                  : c.status === "danger"
                  ? "danger"
                  : c.status === "warning"
                  ? "warning"
                  : "secondary"
              }">${c.label}</span>`
              )
              .join("")}
          </div>
        </div>
      `;
      return { ok: errs.length === 0, warns: warns.length > 0, html };
    } catch (err) {
      if (msg) {
        msg.textContent = "Probe failed.";
        msg.className = "stream-msg text-danger small";
      }
      throw err;
    }
  }

  async function probeAllStreams() {
    if (!camCompatibility) return;
    camCompatibility.innerHTML = "";
    const rows = camStreamsContainer?.querySelectorAll(".stream-row") || [];
    let anyErr = false;
    let anyWarn = false;
    let anyOk = false;
    for (const row of rows) {
      const res = await probeRow(row);
      if (!res) continue;
      camCompatibility.insertAdjacentHTML("beforeend", res.html);
      if (!res.ok) anyErr = true;
      if (res.warns) anyWarn = true;
      if (res.ok && !res.warns) anyOk = true;
    }
    if (anyErr) setStepStatus(camStep2Status, "failed", "danger");
    else if (anyWarn) setStepStatus(camStep2Status, "needs attention", "warning");
    else if (anyOk) setStepStatus(camStep2Status, "ok", "success");
    if (anyErr) return "fail";
    if (anyWarn) return "warn";
    if (anyOk) return "pass";
    return "none";
  }

  function addStreamRow(initialName = "", initialRtsp = "") {
    if (!camStreamsContainer) return;
    const row = document.createElement("div");
    row.className = "row g-2 align-items-end stream-row";
    row.innerHTML = `
      <div class="col-md-4">
        <label class="form-label small mb-1">Stream name</label>
        <input class="form-control stream-name" list="camNameList" placeholder="frontdoor" value="${initialName}">
      </div>
      <div class="col-md-6">
        <label class="form-label small mb-1">RTSP URL</label>
        <input class="form-control stream-rtsp" placeholder="rtsp://user:pass@host:554/stream" value="${initialRtsp}">
      </div>
      <div class="col-md-2">
        <div class="stream-msg text-muted small"></div>
      </div>
    `;
    camStreamsContainer.appendChild(row);
  }

  camProbeBtn?.addEventListener("click", async () => {
    if (!camProbeBtn || !camProbeMsg || !camCompatibility) return;
    camProbeBtn.disabled = true;
    camProbeMsg.textContent = "Probing stream…";
    camProbeMsg.className = "text-muted small";
    camCompatibility.innerHTML = "";
    setForceTranscodeEnabled(false);
    setOnvifUpdateEnabled(false);
    lastProbeData = null;
    lastProbeSelection = null;
    if (camProbeOutput) camProbeOutput.value = "";
    if (camProbeResult) camProbeResult.innerHTML = "";
    try {
      await probeAllStreams();
      if (camProbePreviewSelect?.value) {
        const { name, channel } = parseStreamSelection(camProbePreviewSelect.value);
        const params = new URLSearchParams();
        if (channel != null) {
          params.set("channel", String(channel));
        }
        if (camProbeFullToggle?.checked) {
          params.set("full", "true");
        }
        const query = params.toString();
        const data = await api(`/api/flightcheck/${encodeURIComponent(name)}${query ? `?${query}` : ""}`);
        lastProbeData = data;
        lastProbeSelection = { name, channel };
        if (camProbeOutput) {
          camProbeOutput.value = JSON.stringify(data, null, 2);
        }
        if (camProbeResult) {
          const passed = data && (data["All checks passed"] === true || data.ok === true);
          setForceTranscodeEnabled(!passed);
          setOnvifUpdateEnabled(true);
          if (passed) {
            camProbeResult.innerHTML =
              '<svg width="20" height="20" viewBox="0 0 20 20" aria-label="Passed" role="img" class="text-success"><circle cx="10" cy="10" r="9" fill="currentColor"></circle><path d="M6 10.5l2.2 2.3L14 7.8" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path></svg><span class="text-success small fw-semibold">Passed</span>';
          } else {
            camProbeResult.innerHTML =
              '<svg width="20" height="20" viewBox="0 0 20 20" aria-label="Failed" role="img" class="text-danger"><circle cx="10" cy="10" r="9" fill="currentColor"></circle><path d="M6.5 6.5l7 7M13.5 6.5l-7 7" stroke="#fff" stroke-width="2" stroke-linecap="round"></path></svg><span class="text-danger small fw-semibold">Failed</span>';
          }
          setStepStatus(camStep2Status, passed ? "passed" : "failed", passed ? "success" : "danger");
          if (camStepHint) {
            stepHints.camStep2Pane = passed
              ? "Flight check passed. Continue to Step 3 or test another stream."
              : "Flight check failed: update camera settings and try again.";
            const activePane = root.querySelector(".tab-pane.active");
            if (activePane?.id === "camStep2Pane") {
              setStepHint(stepHints.camStep2Pane);
            }
          }
        }
      }
      camProbeMsg.textContent = "";
      camProbeMsg.className = "text-muted small";
    } catch (err) {
      camCompatibility.innerHTML = "";
      camProbeMsg.textContent = err.message || "Probe failed.";
      camProbeMsg.className = "text-danger small";
      setStepStatus(camStep2Status, "failed", "danger");
      setForceTranscodeEnabled(false);
      setOnvifUpdateEnabled(false);
      if (camProbeOutput) camProbeOutput.value = err.message || "Probe failed.";
      if (camProbeResult) camProbeResult.innerHTML = "";
    } finally {
      camProbeBtn.disabled = false;
    }
  });

  camProbePreviewSelect?.addEventListener("change", (event) => {
    renderPreviewCard(event.target.value);
    setForceTranscodeEnabled(false);
    setOnvifUpdateEnabled(false);
    lastProbeData = null;
    lastProbeSelection = null;
  });

  function parseRtspCredentials(url) {
    if (!url) return null;
    try {
      const parsed = new URL(url);
      if (!parsed.username || !parsed.password || !parsed.hostname) return null;
      return { host: parsed.hostname, username: parsed.username, password: parsed.password };
    } catch {
      return null;
    }
  }

  async function fetchGo2rtcConfig() {
    try {
      const res = await api("/api/go2rtc/config");
      return res?.content || "";
    } catch {
      return "";
    }
  }

  function normalizeStreamName(value) {
    if (!value) return "";
    const marker = "::";
    const idx = value.lastIndexOf(marker);
    return idx === -1 ? value : value.slice(0, idx);
  }

  function parseConfigForStream(content, streamName) {
    if (!content || !streamName) return null;
    const normalized = normalizeStreamName(streamName);
    const lines = content.split("\n");
    let currentComment = "";
    let fallback = null;
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const trimmed = line.trim();
      if (trimmed.startsWith("#")) {
        currentComment = trimmed.replace(/^#\s?/, "");
        continue;
      }
      if (!trimmed || !trimmed.includes(":")) continue;
      const sep = trimmed.indexOf(":");
      const key = trimmed.slice(0, sep).trim();
      const rest = trimmed.slice(sep + 1);
      if (key !== normalized) {
        if (!fallback && normalized && key.endsWith(`_${normalized}`)) {
          const src = rest.trim().replace(/^["']|["']$/g, "");
          fallback = { comment: currentComment, src };
        }
        continue;
      }
      const src = rest.trim().replace(/^["']|["']$/g, "");
      return { comment: currentComment, src };
    }
    return fallback;
  }

  function parseCameraAccountFromComment(comment) {
    if (!comment) return null;
    const userMatch = comment.match(/camera_account_username=([^\s|]+)/i);
    const passMatch = comment.match(/camera_account_password=([^\s|]+)/i);
    if (!userMatch || !passMatch) return null;
    return { username: userMatch[1], password: passMatch[1] };
  }

  function parseTapoHost(src) {
    if (!src || !src.startsWith("tapo://")) return null;
    try {
      const parsed = new URL(src);
      return parsed.hostname || null;
    } catch {
      return null;
    }
  }

  async function resolveOnvifTarget() {
    const selection = lastProbeSelection?.name;
    if (!selection) return null;
    const content = await fetchGo2rtcConfig();
    const entry = parseConfigForStream(content, selection);
    if (entry?.src?.startsWith("tapo://")) {
      const host = parseTapoHost(entry.src);
      const creds = parseCameraAccountFromComment(entry.comment);
      if (host && creds) {
        return { host, username: creds.username, password: creds.password, port: 2020, isTapo: true };
      }
      return null;
    }
    if (entry?.src) {
      const rtspCreds = parseRtspCredentials(entry.src);
      if (rtspCreds) {
        return { host: rtspCreds.host, username: rtspCreds.username, password: rtspCreds.password, port: 80, isTapo: false };
      }
    }
    return null;
  }

  camOnvifUpdateBtn?.addEventListener("click", async () => {
    if (!lastProbeSelection || !lastProbeData) {
      toast("Run a probe first.", "error");
      return;
    }
    const target = await resolveOnvifTarget();
    if (!target) {
      const selection = lastProbeSelection?.name || "(none)";
      const content = await fetchGo2rtcConfig();
      const entry = parseConfigForStream(content, selection);
      const debug = entry ? `found=${selection} src=${entry.src || ""}` : `missing=${selection}`;
      toast(`Missing ONVIF credentials in go2rtc config (${debug})`, "error");
      return;
    }
    const ok = window.confirm("Apply ONVIF encoder settings for this stream?");
    if (!ok) return;
    camOnvifUpdateBtn.disabled = true;
    camOnvifUpdateBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      await api("/api/onvif/encoder/apply-max", {
        method: "POST",
        body: JSON.stringify({
          host: target.host,
          port: target.port,
          username: target.username,
          password: target.password,
          is_tapo: target.isTapo,
          profile: "Main",
        }),
      });
      toast("ONVIF settings applied (max resolution). Re-run the probe.");
    } catch (err) {
      toast(err.message || "ONVIF update failed.", "error");
    } finally {
      camOnvifUpdateBtn.disabled = false;
      camOnvifUpdateBtn.innerHTML = '<i class="bi bi-sliders me-1"></i>Update camera settings via ONVIF';
    }
  });

  camForceTranscodeBtn?.addEventListener("click", async () => {
    if (!lastProbeSelection) {
      toast("Select a stream and run a probe first.", "error");
      return;
    }
    const label = camProbePreviewSelect?.selectedOptions?.[0]?.textContent || lastProbeSelection.name;
    const ok = window.confirm(`Force transcode for ${label}? This will update go2rtc and reload it.`);
    if (!ok) return;
    camForceTranscodeBtn.disabled = true;
    camForceTranscodeBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const summary = lastProbeData?.summary || {};
      const payload = {
        name: lastProbeSelection.name,
        channel: lastProbeSelection.channel,
        summary,
      };
      const res = await api("/api/go2rtc/streams/force-transcode", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      toast(`Transcode enabled for ${res.name}. go2rtc reloaded.`);
      setForceTranscodeEnabled(false);
    } catch (err) {
      toast(err.message || "Force transcode failed.", "error");
      setForceTranscodeEnabled(true);
    } finally {
      camForceTranscodeBtn.disabled = false;
      camForceTranscodeBtn.innerHTML = '<i class="bi bi-lightning-charge me-1"></i>Force transcode';
    }
  });

  function openModelDropdown() {
    if (!camModelDropdown) return;
    camModelDropdown.classList.add("show");
  }

  function closeModelDropdown() {
    if (!camModelDropdown) return;
    camModelDropdown.classList.remove("show");
  }

  camModelInput?.addEventListener("click", (event) => {
    if (camModelValue && camModelValue.value) {
      camModelInput.dataset.prevValue = camModelInput.value;
      camModelInput.placeholder = formatCameraModel(camModelValue.value);
    } else {
      camModelInput.dataset.prevValue = camModelInput.value;
    }
    camModelInput.value = "";
    renderModelDropdown("");
    openModelDropdown();
  });

  camStream1Select?.addEventListener("change", updateStep3Hint);
  camStream2Select?.addEventListener("change", updateStep3Hint);
  camStream3Select?.addEventListener("change", updateStep3Hint);

  camGenerateSettingsBtn?.addEventListener("click", async () => {
    const model = camModelValue?.value || "";
    const stream1 = camStream1Select?.value || "";
    const stream2 = camStream2Select?.value || "";
    const stream3 = camStream3Select?.value || "";
    if (!model) {
      setStepHint("Select a camera model first.");
      return;
    }
    if (!stream1 || !stream2 || !stream3) {
      setStepHint("Select Stream 1, Stream 2, and Stream 3 first.");
      return;
    }
    const updateProgress = (pct, label, kind = "info") => {
      if (!camGenerateProgress) return;
      camGenerateProgress.style.width = `${pct}%`;
      const textTone = kind === "success" || kind === "danger" ? "text-white" : "text-dark";
      camGenerateProgress.className = `progress-bar bg-${kind} ${textTone}`;
      if (camGenerateProgressLabel) {
        camGenerateProgress.textContent = "";
        camGenerateProgressLabel.textContent = label;
        camGenerateProgressLabel.className = `cam-progress-label ${textTone}`;
      } else {
        camGenerateProgress.textContent = label;
      }
    };
    const setDeleteButtonVisible = (visible) => {
      if (!camDeleteSettingsBtn) return;
      camDeleteSettingsBtn.classList.toggle("d-none", !visible);
    };
    setDeleteButtonVisible(false);
    updateProgress(5, "Preparing…", "secondary");
    if (camGenerateSettingsBtn) {
      camGenerateSettingsBtn.disabled = true;
      camGenerateSettingsBtn.textContent = "Generating…";
    }
    const getSelectLabel = (selectEl, value) => {
      if (!selectEl) return value;
      const option = Array.from(selectEl.options || []).find((opt) => opt.value === value);
      return option?.textContent || value;
    };
    try {
      const macMode = camGenerateMacToggle?.checked ? "random" : "lookup";
      const selections = [
        { value: stream1, select: camStream1Select },
        { value: stream2, select: camStream2Select },
        { value: stream3, select: camStream3Select },
      ].map(({ value, select }) => {
        const parsed = parseStreamSelection(value);
        return { ...parsed, value, label: getSelectLabel(select, value) };
      });
      const stream1Selection = selections[0] || { name: "", channel: null };
      updateProgress(10, macMode === "random" ? "Generating MAC…" : "Retrieving MAC…", "info");
      const macRes = await api("/api/unifi/settings/resolve-mac", {
        method: "POST",
        body: JSON.stringify({
          stream: { name: stream1Selection.name, channel: stream1Selection.channel },
          macMode,
        }),
      });
      resolvedSettingsFile = macRes.filename || null;
      updateProgress(20, "Checking for existing file…", "info");
      if (macRes.exists) {
        setDeleteButtonVisible(true);
        throw new Error("File already exists");
      }
      const resolvedMac = macRes.mac;
      const summaries = [];
      const reportCache = new Map();
      const streams = selections.map((item) => ({ name: item.name, channel: item.channel }));
      for (let i = 0; i < selections.length; i += 1) {
        const selection = selections[i];
        const { name, channel } = selection;
        const params = new URLSearchParams();
        if (channel != null) params.set("channel", String(channel));
        params.set("full", "true");
        const label = selection.label || name || `Stream ${i + 1}`;
        const cacheKey = `${name}::${channel ?? ""}`;
        if (reportCache.has(cacheKey)) {
          updateProgress(30 + i * 20, `Reusing results from ${label}…`, "info");
          const cached = reportCache.get(cacheKey);
          if (cached && cached["All checks passed"] === false) {
            throw new Error(`${label} failed flight check. Run Step 2 and try again.`);
          }
          summaries.push((cached && cached.summary) || {});
          updateProgress(40 + i * 20, `Retrieved settings from ${label}`, "info");
          continue;
        }
        updateProgress(30 + i * 20, `Retrieving settings from ${label}…`, "info");
        const report = await api(`/api/flightcheck/${encodeURIComponent(name)}?${params.toString()}`);
        reportCache.set(cacheKey, report);
        if (report && report["All checks passed"] === false) {
          throw new Error(`${label} failed flight check. Run Step 2 and try again.`);
        }
        summaries.push(report.summary || {});
        updateProgress(40 + i * 20, `Retrieved settings from ${label}`, "info");
      }
      const payload = {
        model,
        mac: resolvedMac,
        macMode,
        streams,
        streamSummaries: summaries,
      };
      const res = await api("/api/unifi/settings/generate", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      updateProgress(90, `Creating file ${res.filename}…`, "info");
      updateProgress(100, `Saved ${res.filename}`, "success");
      setStepHint(`Settings generated: ${res.filename}`);
      setStepStatus(camStep3Status, "Complete", "success");
      if (camStep3Status) {
        camStep3Status.textContent = "Complete";
        camStep3Status.className = "ms-2 text-muted small badge text-bg-success";
      }
    } catch (err) {
      updateProgress(100, err.message || "Failed to generate settings.", "danger");
      setStepHint(err.message || "Failed to generate settings.");
      if (String(err.message || "").includes("File already exists")) {
        setDeleteButtonVisible(true);
      }
    } finally {
      if (camGenerateSettingsBtn) {
        camGenerateSettingsBtn.disabled = false;
        camGenerateSettingsBtn.innerHTML = '<i class="bi bi-gear me-1"></i>Generate stream settings';
      }
    }
  });

  camDeleteSettingsBtn?.addEventListener("click", async () => {
    if (!resolvedSettingsFile) {
      toast("No existing settings file to delete.", "error");
      return;
    }
    const ok = window.confirm(`Delete ${resolvedSettingsFile}? This cannot be undone.`);
    if (!ok) return;
    camDeleteSettingsBtn.disabled = true;
    camDeleteSettingsBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      await api("/api/unifi/settings/delete", {
        method: "DELETE",
        body: JSON.stringify({ filename: resolvedSettingsFile }),
      });
      toast(`Deleted ${resolvedSettingsFile}`);
      resolvedSettingsFile = null;
      camDeleteSettingsBtn.classList.add("d-none");
    } catch (err) {
      toast(err.message || "Delete failed", "error");
    } finally {
      camDeleteSettingsBtn.disabled = false;
      camDeleteSettingsBtn.innerHTML = '<i class="bi bi-trash me-1"></i>Delete existing settings file';
    }
  });

  camModelInput?.addEventListener("input", (event) => {
    const value = String(event.target.value || "");
    renderModelDropdown(value);
    openModelDropdown();
    const key = value.trim().toLowerCase();
    if (camModelValue) {
      camModelValue.value = cameraModelMap.get(key) || "";
    }
    updateStep3Hint();
    if (camStepHint) {
      stepHints.camStep3Pane = camModelValue?.value
        ? "Model selected. Continue to Step 2."
        : "G4 Dome camera loaded by default. Select a different model or map camera streams.";
      const activePane = root.querySelector(".tab-pane.active");
      if (activePane?.id === "camStep3Pane") {
        setStepHint(stepHints.camStep3Pane);
      }
    }
  });

  camModelDropdown?.addEventListener("click", (event) => {
    const target = event.target.closest(".cam-model-option");
    if (!target || !target.dataset.value) return;
    const display = target.textContent || "";
    const value = target.dataset.value;
    if (camModelInput) camModelInput.value = display;
    if (camModelValue) camModelValue.value = value;
    if (camModelInput) camModelInput.placeholder = display;
    closeModelDropdown();
    updateStep3Hint();
  });

  camModelInput?.addEventListener("blur", () => {
    if (!camModelInput) return;
    const raw = camModelInput.value.trim();
    if (!raw && camModelValue && camModelValue.value) {
      const display = formatCameraModel(camModelValue.value);
      camModelInput.value = display;
      camModelInput.placeholder = display;
    }
    if (!raw && camModelInput.dataset.prevValue && !camModelValue?.value) {
      camModelInput.value = camModelInput.dataset.prevValue;
    }
    updateStep3Hint();
  });

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!camModelDropdown || !camModelInput) return;
    if (camModelDropdown.contains(target) || camModelInput.contains(target)) return;
    closeModelDropdown();
  });

  camAddExtraBtn2?.addEventListener("click", () => {
    addStreamRow();
    setStepStatus(camStep1Status, "pending", "secondary");
  });

  function updateCameraSelection() {
    if (!camOnvifSelect) return;
    camOnvifSelect.innerHTML = '<option value="">Select a camera…</option>';
    const parseHost = (dev) => {
      if (!dev) return "";
      if (dev.host) return dev.host;
      if (dev.ip) return dev.ip;
      if (dev.addr) return dev.addr;
      const xaddrs = Array.isArray(dev.xaddrs) ? dev.xaddrs : [];
      const url = dev.url || xaddrs[0] || "";
      try {
        const parsed = new URL(url);
        return parsed.hostname || "";
      } catch {
        return "";
      }
    };
    discoveredOnvif.forEach((dev, idx) => {
      const host = parseHost(dev);
      const opt = document.createElement("option");
      opt.value = host;
      opt.dataset.idx = idx;
      opt.textContent = `${dev.name || host || "camera"} (${host || "?"})`;
      camOnvifSelect.appendChild(opt);
    });
  }

  async function scanOnvif() {
    if (!camOnvifScanBtn) return;
    camOnvifScanBtn.disabled = true;
    camOnvifScanBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    if (camOnvifScanMsg) {
      camOnvifScanMsg.textContent = "Scanning…";
      camOnvifScanMsg.className = "text-muted small";
    }
    try {
      const data = await api("/api/go2rtc/onvif/discover");
      discoveredOnvif = data?.devices || [];
      updateCameraSelection();
      if (camOnvifScanMsg) {
        camOnvifScanMsg.textContent = discoveredOnvif.length
          ? `Found ${discoveredOnvif.length} camera(s).`
          : "No ONVIF cameras found.";
        camOnvifScanMsg.className = discoveredOnvif.length ? "text-success small" : "text-warning small";
      }
    } catch (err) {
      if (camOnvifScanMsg) {
        const msg = err.message || "Scan failed.";
        camOnvifScanMsg.textContent = msg;
        camOnvifScanMsg.className = msg.includes("not available") ? "text-warning small" : "text-danger small";
      }
    } finally {
      camOnvifScanBtn.disabled = false;
      camOnvifScanBtn.innerHTML = '<i class="bi bi-search me-1"></i>Scan network';
    }
  }

  camOnvifScanBtn?.addEventListener("click", scanOnvif);

  camOnvifSelect?.addEventListener("change", () => {
    const host = camOnvifSelect.value;
    const idx = parseInt(camOnvifSelect.selectedOptions[0]?.dataset?.idx || "-1", 10);
    const dev = discoveredOnvif[idx] || {};
    if (dev.name && camFriendlyInput && !camFriendlyInput.value) camFriendlyInput.value = dev.name;
    if (host && camOnvifMsg) {
      camOnvifMsg.textContent = "";
      camOnvifMsg.className = "text-muted small";
    }
  });

  async function addOnvifStream() {
    if (!camOnvifAddBtn) return;
    const host = camOnvifSelect?.value?.trim();
    const username = camOnvifUser?.value?.trim() || "";
    const password = camOnvifPass?.value ?? "";
    const friendly = camFriendlyInput?.value?.trim() || "";
    if (!friendly) {
      if (camOnvifMsg) {
        camOnvifMsg.textContent = "Enter a friendly name first.";
        camOnvifMsg.className = "text-danger small";
      }
      if (camFriendlyInput) {
        camFriendlyInput.classList.add("is-invalid");
        setTimeout(() => camFriendlyInput.classList.remove("is-invalid"), 500);
        setTimeout(() => camFriendlyInput.classList.add("is-invalid"), 700);
        setTimeout(() => camFriendlyInput.classList.remove("is-invalid"), 1200);
      }
      return;
    }
    if (!host) {
      if (camOnvifMsg) {
        camOnvifMsg.textContent = "Select a discovered camera first.";
        camOnvifMsg.className = "text-danger small";
      }
      return;
    }

    camOnvifAddBtn.disabled = true;
    camOnvifAddBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    if (camOnvifMsg) {
      camOnvifMsg.textContent = "Loading profiles via ONVIF…";
      camOnvifMsg.className = "text-muted small";
    }
    try {
      const payload = { host, username, password };
      const res = await api("/api/go2rtc/onvif/profiles", { method: "POST", body: JSON.stringify(payload) });
      const sources = res?.sources || [];
      const srcUsed = res?.src || "";
      if (!sources.length) {
        if (camOnvifMsg) {
          camOnvifMsg.textContent = "No profiles returned.";
          camOnvifMsg.className = "text-warning small";
        }
        return;
      }
      onvifGroupSources = sources;
      onvifGroupName = friendly.replace(/\s+/g, "_");
      if (camOnvifProfiles) {
        camOnvifProfiles.innerHTML =
          (srcUsed ? `<div class="small text-muted mb-1">ONVIF src: ${srcUsed}</div>` : "") +
          sources
            .map((s, idx) => {
              const url = s.url || "";
              const streamUri = s.stream_uri || "";
              const displayUrl = streamUri || url;
              const name = s.name || `profile ${idx + 1}`;
              const tokenMatch = url.match(/subtype=([^&]+)/i);
              const profileId = tokenMatch ? tokenMatch[1] : "";
              const profileBadge = profileId ? `<span class="badge text-bg-secondary ms-2">${profileId}</span>` : "";
              const srcBadge = `<span class="badge text-bg-info ms-2">src=${idx}</span>`;
              const onvifNote = streamUri && url ? `<div class="text-body-secondary small">ONVIF: ${url}</div>` : "";
              const selectedUrl = streamUri || url;
              return `<label class="small text-muted d-flex align-items-center gap-2 mb-1">
                <input class="form-check-input m-0 onvif-profile-check" type="checkbox" data-idx="${idx}" data-url="${selectedUrl}" data-profile="${profileId}" data-label="${name}" checked>
                <span><i class="bi bi-camera-video me-1"></i>${name} — <span class="text-body-secondary">${displayUrl}</span> ${srcBadge}${profileBadge}${onvifNote}</span>
              </label>`;
            })
            .join("");
      }
      const onvifPersistWrap = root.querySelector("#camPersistWrapperOnvif");
      if (onvifPersistWrap) onvifPersistWrap.classList.remove("d-none");
      if (camStreamsContainer) camStreamsContainer.innerHTML = "";
      if (camOnvifMsg) {
        camOnvifMsg.textContent = `Loaded ${sources.length} profile(s). Save to config to add a grouped stream.`;
        camOnvifMsg.className = "text-success small";
      }
      setStepStatus(camStep1Status, "pending", "secondary");
    } catch (err) {
      if (camOnvifMsg) {
        camOnvifMsg.textContent = err.message || "Add failed.";
        camOnvifMsg.className = "text-danger small";
      }
    } finally {
      camOnvifAddBtn.disabled = false;
      camOnvifAddBtn.innerHTML = '<i class="bi bi-plus-circle me-1"></i>Load profiles';
    }
  }

  camOnvifAddBtn?.addEventListener("click", addOnvifStream);
  camPersistBtnOnvif?.addEventListener("click", async () => {
    if (!onvifGroupSources.length) {
      toast("Load ONVIF profiles first", "error");
      return;
    }
    const name = onvifGroupName || camFriendlyInput?.value?.trim().replace(/\s+/g, "_");
    if (!name) {
      toast("Enter a friendly name first", "error");
      return;
    }
    const selected = Array.from(root.querySelectorAll(".onvif-profile-check:checked"))
      .map((input) => ({
        idx: input.dataset.idx,
        url: input.dataset.url,
        profile: input.dataset.profile,
        label: input.dataset.label,
      }))
      .filter((item) => item.url);
    if (!selected.length) {
      toast("Select at least one profile to save", "error");
      return;
    }
    const normalizeSuffix = (value) =>
      String(value || "")
        .trim()
        .replace(/\s+/g, "_")
        .replace(/[^a-zA-Z0-9_-]/g, "_");
    const streams = selected.map((item) => {
      const suffix = item.profile || `stream${item.idx}`;
      const safeSuffix = normalizeSuffix(suffix);
      return { name: `${name}_${safeSuffix}`, src: item.url };
    });
    camPersistBtnOnvif.disabled = true;
    camPersistBtnOnvif.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      await api("/api/go2rtc/streams/persist", { method: "POST", body: JSON.stringify({ streams }) });
      await api("/api/go2rtc/reload", { method: "POST" });
      toast(`Saved ${streams.length} ONVIF stream(s) and reloaded go2rtc`);
      setStepStatus(camStep1Status, "Complete", "success");
    } catch (err) {
      toast(err.message || "Save failed", "error");
    } finally {
      camPersistBtnOnvif.disabled = false;
      camPersistBtnOnvif.innerHTML = '<i class="bi bi-save me-1"></i>Save to config & reload';
    }
  });

  async function sha256Hex(value) {
    if (window.crypto?.subtle && window.isSecureContext) {
      const bytes = new TextEncoder().encode(value);
      const hash = await crypto.subtle.digest("SHA-256", bytes);
      const hex = Array.from(new Uint8Array(hash))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      return hex.toUpperCase();
    }
    const res = await api("/api/hash/sha256", { method: "POST", body: JSON.stringify({ value }) });
    return String(res?.hash || "").toUpperCase();
  }

  async function buildTapoUrl() {
    const host = camTapoHost?.value?.trim() || "";
    const cloudPass = camTapoCloudPass?.value ?? "";
    const subtype = camTapoSubtype?.value || "0";
    const useHash = camTapoAuthHash?.checked;
    const hashVal = camTapoHash?.value?.trim() || "";
    const username = camTapoUser?.value?.trim() || "admin";

    if (!host) throw new Error("Enter a camera IP/host.");
    if (!cloudPass && !useHash) throw new Error("Enter your app password.");

    let auth = "";
    if (useHash) {
      if (!hashVal) throw new Error("Provide a pre-hashed password (MD5/SHA-256).");
      auth = `${username}:${hashVal}@`;
    } else {
      const hash = await sha256Hex(cloudPass);
      auth = `admin:${hash}@`;
    }

    const url = `tapo://${auth}${host}?subtype=${encodeURIComponent(subtype)}`;
    return url;
  }

  async function refreshTapoPreview() {
    if (!camTapoPreview) return;
    try {
      const url = await buildTapoUrl();
      camTapoPreview.textContent = url;
      if (camTapoMsg) {
        camTapoMsg.textContent = "Tapo URL ready.";
        camTapoMsg.className = "text-success small";
      }
      return url;
    } catch (err) {
      camTapoPreview.textContent = "Build to preview…";
      if (camTapoMsg) {
        camTapoMsg.textContent = err.message || "Build failed.";
        camTapoMsg.className = "text-danger small";
      }
      throw err;
    }
  }

  async function addTapoStream(persist) {
    if (!camTapoAddBtn || !camTapoPersistBtn) return;
    const friendly = camFriendlyInput?.value?.trim() || "";
    const host = camTapoHost?.value?.trim() || "";
    const accountPass = camTapoAccountPass?.value?.trim() || "";
    const accountUser = camTapoAccountUser?.value?.trim() || "";
    const base = friendly || host || "";
    const name = base ? base.replace(/\s+/g, "_") : "tapo_camera";
    const btn = persist ? camTapoPersistBtn : camTapoAddBtn;
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      const url = await buildTapoUrl();
      const note = `http://localhost:1984/webrtc.html?src=${encodeURIComponent(name)}&media=video+audio+microphone`;
      if (persist) {
        let comment = note;
        if (accountUser) comment += ` | camera_account_username=${accountUser}`;
        if (accountPass) comment += ` | camera_account_password=${accountPass}`;
        await api("/api/go2rtc/streams/persist", { method: "POST", body: JSON.stringify({ streams: [{ name, src: url, comment }] }) });
        await api("/api/go2rtc/reload", { method: "POST" });
        toast("Saved to config and reloaded go2rtc");
      } else {
        await api("/api/go2rtc/streams", { method: "POST", body: JSON.stringify({ name, src: url }) });
        toast("Stream added");
      }
      setStepStatus(camStep1Status, "Complete", "success");
    } catch (err) {
      toast(err.message || "Add failed", "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = persist
        ? '<i class="bi bi-save me-1"></i>Save to config & reload'
        : '<i class="bi bi-plus-circle me-1"></i>Add stream';
    }
  }

  camTapoAuthCloud?.addEventListener("change", () => {
    if (camTapoAuthCloud.checked) {
      camTapoHash.disabled = true;
      camTapoUser.disabled = true;
    }
  });

  camTapoAuthHash?.addEventListener("change", () => {
    if (camTapoAuthHash.checked) {
      camTapoHash.disabled = false;
      camTapoUser.disabled = false;
    }
  });

  camTapoBuildBtn?.addEventListener("click", refreshTapoPreview);
  camTapoAddBtn?.addEventListener("click", () => addTapoStream(false));
  camTapoPersistBtn?.addEventListener("click", () => addTapoStream(true));

  async function persistStreams() {
    const rows = camStreamsContainer?.querySelectorAll(".stream-row") || [];
    if (!rows.length) {
      toast("No streams to save", "error");
      return;
    }
    const streams = [];
    rows.forEach((row) => {
      const nameInput = row.querySelector(".stream-name");
      const rtspInput = row.querySelector(".stream-rtsp");
      const name = nameInput?.value?.trim();
      const src = rtspInput?.value?.trim();
      if (name && src) streams.push({ name, src });
    });
    if (!streams.length) {
      toast("No valid streams to save", "error");
      return;
    }
    camPersistBtn.disabled = true;
    camPersistBtn.innerHTML = '<span class="spinner-border spinner-border-sm" role="status" aria-hidden="true"></span>';
    try {
      await api("/api/go2rtc/streams/persist", { method: "POST", body: JSON.stringify({ streams }) });
      await api("/api/go2rtc/reload", { method: "POST" });
      toast("Saved to config and reloaded go2rtc");
      setStepStatus(camStep1Status, "Complete", "success");
    } catch (err) {
      toast(err.message || "Save failed", "error");
    } finally {
      camPersistBtn.disabled = false;
      camPersistBtn.innerHTML = '<i class="bi bi-save me-1"></i>Save to config & reload';
    }
  }

  camPersistBtn?.addEventListener("click", persistStreams);

  function setCamMode(mode) {
    const onvifActive = mode === "onvif";
    const tapoActive = mode === "tapo";
    const rtspActive = mode === "rtsp";
    if (camModeOnvifBtn && camModeTapoBtn && camModeRtspBtn) {
      camModeOnvifBtn.classList.toggle("active", onvifActive);
      camModeTapoBtn.classList.toggle("active", tapoActive);
      camModeRtspBtn.classList.toggle("active", rtspActive);
    }
    if (camOnvifSection && camTapoSection && camRtspSection) {
      camOnvifSection.classList.toggle("d-none", !onvifActive);
      camTapoSection.classList.toggle("d-none", !tapoActive);
      camRtspSection.classList.toggle("d-none", !rtspActive);
    }
  }

  camModeOnvifBtn?.addEventListener("click", () => setCamMode("onvif"));
  camModeTapoBtn?.addEventListener("click", () => setCamMode("tapo"));
  camModeRtspBtn?.addEventListener("click", () => setCamMode("rtsp"));

  camAddAllBtn?.addEventListener("click", async () => {
    const rows = camStreamsContainer?.querySelectorAll(".stream-row") || [];
    if (!rows.length) {
      addStreamRow();
      return;
    }
    let anyAdded = false;
    let anyFailed = false;
    const friendly = camFriendlyInput?.value?.trim() || "";
    for (const row of rows) {
      const nameInput = row.querySelector(".stream-name");
      const rtspInput = row.querySelector(".stream-rtsp");
      const msg = row.querySelector(".stream-msg");
      const name = nameInput?.value.trim();
      const src = rtspInput?.value.trim();
      if (!name) {
        msg.textContent = "Name required.";
        msg.className = "stream-msg text-danger small";
        anyFailed = true;
        continue;
      }
      if (streamsMap[name]) {
        msg.textContent = "Existing stream; skipped.";
        msg.className = "stream-msg text-muted small";
        continue;
      }
      if (!src) {
        msg.textContent = "RTSP URL required.";
        msg.className = "stream-msg text-danger small";
        anyFailed = true;
        continue;
      }
      msg.textContent = "Adding…";
      msg.className = "stream-msg text-muted small";
      try {
        await api("/api/go2rtc/streams", { method: "POST", body: JSON.stringify({ name, src }) });
        msg.textContent = "Added.";
        msg.className = "stream-msg text-success small";
        anyAdded = true;
        if (friendly) {
          webSettings[name] = webSettings[name] || {};
          webSettings[name].friendly = friendly;
          api("/api/settings", { method: "PUT", body: JSON.stringify(webSettings) }).catch(() => {});
        }
      } catch (err) {
        msg.textContent = err.message || "Failed.";
        msg.className = "stream-msg text-danger small";
        anyFailed = true;
      }
    }
    if (anyAdded) {
      const streams = await loadStreamsList();
      streamsMap = streams || {};
      updateCameraSelection();
      await probeAllStreams();
      setStepStatus(camStep1Status, anyFailed ? "partial" : "ok", anyFailed ? "warning" : "success");
    } else if (anyFailed) {
      setStepStatus(camStep1Status, "failed", "danger");
    }
  });

  async function loadWebSettings() {
    try {
      webSettings = await api("/api/settings");
    } catch {
      webSettings = {};
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
      streamsMap = streams;
      if (camNameList) {
        camNameList.innerHTML = "";
        Object.keys(streams).forEach((name) => {
          const opt = document.createElement("option");
          opt.value = name;
          camNameList.appendChild(opt);
        });
      }
      updatePreviewSelect();
      return streams;
    } catch {
      streamsMap = {};
      updatePreviewSelect();
      return {};
    }
  }

  async function loadCameraModels() {
    if (!camModelDropdown) return;
    try {
      const data = await api("/api/unifi/camera-models");
      const models = Array.isArray(data?.models) ? data.models : [];
      cameraModelMap = new Map();
      cameraModelOptions = models.map((model) => {
        const display = formatCameraModel(model);
        cameraModelMap.set(display.toLowerCase(), model);
        return { display, value: model };
      });
      const defaultModel = "UVC_G4_DOME";
      if (camModelInput) {
        const defaultDisplay = formatCameraModel(defaultModel);
        camModelInput.value = defaultDisplay;
        camModelInput.placeholder = defaultDisplay;
        if (camModelValue) camModelValue.value = defaultModel;
        renderModelDropdown(defaultDisplay);
      }
    } catch (err) {
      if (camStepHint) {
        camStepHint.textContent = err.message || "Failed to load models.";
        camStepHint.className = "border rounded p-2 bg-body-tertiary small text-danger mb-3";
      }
    }
  }

  function renderModelDropdown(filterText = "") {
    if (!camModelDropdown) return;
    const needle = filterText.trim().toLowerCase();
    const filtered = cameraModelOptions.filter((opt) => opt.display.toLowerCase().includes(needle));
    camModelDropdown.innerHTML = "";
    if (!filtered.length) {
      camModelDropdown.innerHTML = '<div class="cam-model-option text-muted">No matches</div>';
      return;
    }
    filtered.slice(0, 50).forEach((opt) => {
      const item = document.createElement("div");
      item.className = "cam-model-option";
      item.textContent = opt.display;
      item.dataset.value = opt.value;
      camModelDropdown.appendChild(item);
    });
  }

  function updateStep3Hint() {
    if (!camStepHint) return;
    const modelSet = !!(camModelValue && camModelValue.value);
    const streamsSet =
      !!(camStream1Select && camStream1Select.value) &&
      !!(camStream2Select && camStream2Select.value) &&
      !!(camStream3Select && camStream3Select.value);
    if (modelSet && streamsSet) {
      stepHints.camStep3Pane = "Click Generate stream settings.";
    } else {
      stepHints.camStep3Pane = "G4 Dome camera loaded by default. Select a different model or map camera streams.";
    }
    const activePane = root.querySelector(".tab-pane.active");
    if (activePane?.id === "camStep3Pane") {
      setStepHint(stepHints.camStep3Pane);
    }
  }

  function syncFriendlyFromSettings(name) {
    if (webSettings[name] && webSettings[name].friendly && camFriendlyInput) {
      camFriendlyInput.value = webSettings[name].friendly;
    }
  }

  async function updateExistingStreamState() {
    const rows = camStreamsContainer?.querySelectorAll(".stream-row") || [];
    rows.forEach((row) => {
      const nameInput = row.querySelector(".stream-name");
      const msg = row.querySelector(".stream-msg");
      const name = nameInput?.value.trim();
      const stream = name ? streamsMap[name] : null;
      if (stream) {
        msg.textContent = "Existing stream.";
        msg.className = "stream-msg text-muted small";
        syncFriendlyFromSettings(name);
        setStepStatus(camStep1Status, "existing", "secondary");
      } else if (msg) {
        msg.textContent = "";
      }
    });
  }

  return {
    async start() {
      if (!initialized) {
        setCamMode("onvif");
        addStreamRow();
        const firstStepTab = root.querySelector("#camStep1Tab");
        const firstStepPane = root.querySelector("#camStep1Pane");
        if (firstStepTab) {
          root.querySelectorAll("#camSetupSteps .nav-link").forEach((el) => {
            el.classList.toggle("active", el === firstStepTab);
            el.setAttribute("aria-selected", el === firstStepTab ? "true" : "false");
          });
        }
        if (firstStepPane) {
          root.querySelectorAll("#camStep1Pane, #camStep2Pane, #camStep3Pane").forEach((pane) => {
            pane.classList.toggle("show", pane === firstStepPane);
            pane.classList.toggle("active", pane === firstStepPane);
          });
        }
        if (window.bootstrap?.Tab && firstStepTab) {
          const tab = window.bootstrap.Tab.getOrCreateInstance(firstStepTab);
          tab.show();
        }
        root.querySelectorAll('#camSetupSteps [data-bs-toggle="tab"]').forEach((tab) => {
          tab.addEventListener("shown.bs.tab", (event) => {
            const targetId = event.target.getAttribute("data-bs-target")?.replace("#", "");
            const hint = targetId ? stepHints[targetId] : "";
            setStepHint(hint);
          });
        });
        setStepHint(stepHints.camStep1Pane);
        initialized = true;
      }
      await loadWebSettings();
      await loadStreamsList();
      await loadCameraModels();
      updateStep3Hint();
      updateCameraSelection();
      updateExistingStreamState();
      await probeAllStreams();
      updatePreviewSelect();
      camFriendlyInput?.focus();
    },
    stop() {},
  };
}
