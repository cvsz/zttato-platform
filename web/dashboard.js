"use strict";
(() => {
  const $ = (id) => document.getElementById(id);

  const account = { connected: false, scopes: [], audited: false, photo_transfer_ready: false };
  const mediaAssets = { video: null, photo: null };
  let activeMediaType = "video";
  let jobId = null;
  let idempotencyKey = null;
  let sending = false;
  let selectedFile = null;
  let photoPreparing = false;

  const labels = {
    PUBLIC_TO_EVERYONE: "Everyone",
    MUTUAL_FOLLOW_FRIENDS: "Friends (mutual follows)",
    FOLLOWER_OF_CREATOR: "Followers",
    SELF_ONLY: "Only me"
  };

  const text = (id, message) => {
    const el = $(id);
    if (el) el.textContent = message;
  };

  function translated(key, fallback) {
    if (typeof i18n !== "undefined" && i18n && typeof i18n.t === "function") {
      const value = i18n.t(key);
      if (value !== key) return value;
    }
    return fallback;
  }

  function csrf() {
    const item = document.cookie
      .split("; ")
      .find((v) => v.startsWith("zttato_csrf="));
    if (!item) throw new Error("Session expired. Reload the dashboard.");
    return decodeURIComponent(item.slice("zttato_csrf=".length));
  }

  async function api(path, options = {}) {
    const opts = { credentials: "same-origin", ...options };
    if (opts.method && opts.method !== "GET") {
      opts.headers = { ...opts.headers, "X-CSRF-Token": csrf() };
    }
    const response = await fetch(path, opts);
    if (!response.ok) {
      let detail = null;
      try {
        const data = await response.json();
        detail = data.detail ?? null;
      } catch (_) {}
      const message = typeof detail === "string"
        ? detail
        : (typeof detail?.message === "string" ? detail.message : "Request failed (" + response.status + ")");
      const error = new Error(message);
      error.status = response.status;
      error.detail = detail && typeof detail === "object" ? detail : null;
      throw error;
    }
    return response.json();
  }

  function report(message, isError = false) {
    text("status", message);
    $("status").classList.toggle("error", isError);
  }

  function mode() {
    const directId = activeMediaType === "photo" ? "photo-mode-direct" : "mode-direct";
    return $(directId)?.checked ? "direct" : "draft";
  }

  function activeMediaId() {
    return mediaAssets[activeMediaType];
  }

  function canPreparePhoto() {
    return account.scopes.includes("video.upload") || account.scopes.includes("video.publish");
  }

  function privacySelect() {
    return $(activeMediaType === "photo" ? "photo-privacy" : "privacy");
  }

  function resetIntent(clearConsent = false) {
    idempotencyKey = null;
    jobId = null;
    $("refresh-status").disabled = true;
    if (clearConsent && $("consent")) $("consent").checked = false;
  }

  function activateMediaType(type) {
    if (activeMediaType !== type) {
      activeMediaType = type;
      resetIntent(true);
    }
  }

  function formatBytes(bytes) {
    if (bytes < 1024) return bytes + " B";
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + " KB";
    return (bytes / 1048576).toFixed(2) + " MiB";
  }

  function updateCaptionCount() {
    const len = $("caption")?.value.length || 0;
    text("caption-count", len + " / 2200");
  }

  function updatePhotoCaptionCount() {
    const len = $("photo-caption")?.value.length || 0;
    text("photo-caption-count", len + " / 4000");
  }

  function photoForm() {
    const raw = $("photo-urls")?.value || "";
    const urls = raw.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
    if (!urls.length) return { urls, error: "photo.error_urls" };
    if (urls.length > 35) return { urls, error: "photo.error_max" };

    for (const value of urls) {
      if (/\s/.test(value)) return { urls, error: "photo.error_url" };
      try {
        const parsed = new URL(value);
        const isIpv4 = /^\d{1,3}(?:\.\d{1,3}){3}$/.test(parsed.hostname);
        const isIpv6 = parsed.hostname.includes(":");
        if (
          parsed.protocol !== "https:" ||
          !parsed.hostname.includes(".") ||
          isIpv4 ||
          isIpv6 ||
          parsed.username ||
          parsed.password ||
          parsed.hash ||
          (parsed.port && parsed.port !== "443")
        ) return { urls, error: "photo.error_https" };
        const suffix = parsed.pathname.match(/\.([^.\/]+)$/)?.[1]?.toLowerCase();
        if (suffix && !["jpg", "jpeg", "webp"].includes(suffix)) {
          return { urls, error: "photo.error_format" };
        }
      } catch (_) {
        return { urls, error: "photo.error_url" };
      }
    }

    const cover = Number($("photo-cover")?.value);
    if (!Number.isInteger(cover) || cover < 0 || cover >= urls.length) {
      return { urls, error: "photo.error_cover" };
    }
    return { urls, cover, error: null };
  }

  function updatePhotoPreparationButton() {
    const button = $("photo-upload");
    if (!button) return;
    if (!account.photo_transfer_ready) {
      button.disabled = true;
      if (account.connected) {
        text("photo-result", translated(
          "dashboard.photo.url_property_missing",
          "Photo Post is disabled until TikTok verifies and configures a media URL prefix."
        ));
      }
      return;
    }
    const { error } = photoForm();
    button.disabled = photoPreparing || !account.connected || !canPreparePhoto() || Boolean(error);
    if (error && $("photo-urls")?.value.trim()) {
      const fallbacks = {
        "photo.error_urls": "Please enter at least one photo URL.",
        "photo.error_max": "Maximum 35 photos allowed.",
        "photo.error_https": "All photo URLs must use HTTPS on a public domain.",
        "photo.error_cover": "Cover index must select one of the supplied photos.",
        "photo.error_url": "Enter a valid photo URL without spaces or redirects.",
        "photo.error_format": "TikTok Photo Post supports JPEG and WebP only."
      };
      text("photo-result", translated(error, fallbacks[error] || "Check the photo URLs."));
    }
  }

  function buildSummary() {
    const card = $("summary");
    if (!card) return;

    if (!activeMediaId()) {
      card.textContent = translated("dashboard.review.summary_none", "Upload or prepare media first.");
      return;
    }

    const isPhoto = activeMediaType === "photo";
    const parts = [];
    parts.push(isPhoto ? "Photo set ready in workspace" : "Video ready in workspace");
    parts.push(`Mode · ${mode() === "draft" ? "Draft (finish inside TikTok)" : "Direct Post"}`);

    if (mode() === "direct") {
      const privacy = privacySelect()?.value;
      parts.push(`Visibility · ${labels[privacy] || privacy || "—"}`);

      const disclosures = [];
      if ($(isPhoto ? "photo-own-brand" : "own-brand")?.checked) disclosures.push("Own business");
      if ($(isPhoto ? "photo-paid-brand" : "paid-brand")?.checked) disclosures.push("Paid partnership");
      if ($(isPhoto ? "photo-ai-content" : "ai-content")?.checked) disclosures.push("AI-generated");
      if (disclosures.length) {
        parts.push(`Disclosures · ${disclosures.join(", ")}`);
      }
    }

    const intro = document.createElement("p");
    intro.className = "note";
    intro.textContent = "Ready to send after consent:";

    const list = document.createElement("ul");
    if (typeof list.append === "function") {
      for (const part of parts) {
        const item = document.createElement("li");
        item.textContent = part;
        list.append(item);
      }
    } else {
      list.textContent = parts.join("\n");
    }

    const footer = document.createElement("p");
    footer.className = "note";
    footer.textContent = "No transfer occurs until you confirm.";

    if (typeof card.replaceChildren === "function") {
      card.replaceChildren(intro, list, footer);
    } else {
      card.textContent = [intro.textContent, list.textContent, footer.textContent].join("\n");
    }
  }

  function review() {
    const direct = mode() === "direct";
    $("direct-options")?.classList.toggle("hidden", !direct || activeMediaType !== "video");
    $("photo-direct-options")?.classList.toggle("hidden", !direct || activeMediaType !== "photo");

    const ready =
      Boolean(activeMediaId()) &&
      Boolean($("consent")?.checked) &&
      !sending;

    const publish = $("publish");
    if (publish) {
      const modeControl = $(activeMediaType === "photo" ? "photo-mode-direct" : "mode-direct");
      const draftControl = $(activeMediaType === "photo" ? "photo-mode-draft" : "mode-draft");
      const directBlocked =
        direct &&
        (!privacySelect()?.value ||
          !account.scopes.includes("video.publish") ||
          Boolean(modeControl?.disabled));
      const draftBlocked = !direct && (
        !account.scopes.includes("video.upload") || Boolean(draftControl?.disabled)
      );

      publish.disabled = !ready || directBlocked || draftBlocked;
    }

    buildSummary();
  }

  function setCreatorFlag(id, disabled) {
    const control = $(id);
    if (!control) throw new Error("Dashboard UI version mismatch; refresh this page.");
    control.checked = Boolean(disabled);
    control.disabled = Boolean(disabled);
  }

  function renderScopeChips(scopes) {
    const container = $("scope-chips");
    if (!container) return;
    container.replaceChildren();
    scopes.forEach((scope) => {
      const chip = document.createElement("span");
      chip.className = "scope-chip";
      chip.textContent = scope;
      container.append(chip);
    });
  }

  async function loadProfile() {
    const card = $("profile");
    const avatar = $("profile-avatar");
    if (!card) {
      text("connection-details", "Dashboard UI version mismatch. Refresh to load your TikTok profile.");
      return;
    }
    card.classList.remove("hidden");
    text("profile-name", "Loading TikTok profile…");

    if (!account.scopes.includes("user.info.basic")) {
      text("profile-name", "TikTok profile unavailable");
      text("profile-message", "Reconnect TikTok and grant user.info.basic.");
      return;
    }

    try {
      const profile = await api("/api/profile");
      text("profile-name", profile.display_name || "TikTok creator");
      text("profile-message", "Basic profile retrieved from your authorized TikTok account.");
      if (avatar && profile.avatar_url) {
        avatar.onerror = () => {
          avatar.classList.add("hidden");
          avatar.removeAttribute("src");
        };
        avatar.src = profile.avatar_url;
        avatar.classList.remove("hidden");
      }
    } catch (err) {
      text("profile-name", "TikTok profile unavailable");
      text("profile-message", err.message + " You can retry by refreshing the dashboard.");
    }
  }

  async function loadCreator() {
    if (!account.scopes.includes("video.publish")) {
      $("mode-direct").disabled = true;
      $("photo-mode-direct").disabled = true;
      text(
        "creator-message",
        "Direct Post requires the video.publish scope. Reconnect TikTok with this permission."
      );
      text("photo-creator-message", "Direct Post requires the video.publish scope. Reconnect TikTok with this permission.");
      review();
      return;
    }

    try {
      const info = await api("/api/creator-info");
      const choices = info.privacy_level_options.filter(
        (value) => account.audited || value === "SELF_ONLY"
      );

      for (const selectId of ["privacy", "photo-privacy"]) {
        const select = $(selectId);
        select.replaceChildren();
        for (const value of choices) {
          const option = document.createElement("option");
          option.value = value;
          option.textContent = labels[value] || value;
          select.append(option);
        }
        select.disabled = choices.length === 0;
      }

      setCreatorFlag("disable-comment", info.comment_disabled);
      setCreatorFlag("disable-duet", info.duet_disabled);
      setCreatorFlag("disable-stitch", info.stitch_disabled);
      setCreatorFlag("photo-disable-comment", info.comment_disabled);

      const creatorMessage =
        "Connected: " +
        (info.nickname || info.username || "TikTok creator") +
        (account.audited ? "." : " · Unaudited app: Direct Post is limited to Only me.");
      const creatorDuration = info.max_video_post_duration_sec;
      const apiDuration = Number.isInteger(creatorDuration) && creatorDuration > 0
        ? Math.min(creatorDuration, 600)
        : 600;
      text("creator-message", creatorMessage + (info.max_video_post_duration_sec
        ? ` Max video duration via this app: ${apiDuration} seconds` +
          (creatorDuration > apiDuration ? ` (TikTok creator limit: ${creatorDuration} seconds).` : ".")
        : ` Max video duration via this app: ${apiDuration} seconds.`));
      text("photo-creator-message", creatorMessage);

      if (!choices.length) {
        $("mode-direct").disabled = true;
        $("photo-mode-direct").disabled = true;
      }
    } catch (err) {
      $("mode-direct").disabled = true;
      $("photo-mode-direct").disabled = true;
      text("creator-message", "Creator options unavailable: " + err.message);
      text("photo-creator-message", "Creator options unavailable: " + err.message);
    }
    review();
  }

  function setFile(file) {
    selectedFile = file || null;
    mediaAssets.video = null;
    activeMediaType = "video";
    resetIntent(true);
    text("media-result", "");

    const preview = $("file-preview");
    const dropZone = $("drop-zone");

    if (file) {
      text("file-name", file.name);
      text("file-size", formatBytes(file.size));
      preview.classList.remove("hidden");
      dropZone.classList.add("hidden");
      $("upload").disabled = false;
    } else {
      preview.classList.add("hidden");
      dropZone.classList.remove("hidden");
      $("upload").disabled = true;
      $("file").value = "";
    }
    review();
  }

  async function boot() {
    try {
      // Initialize i18n (non-blocking, with fallback)
      if (typeof i18n !== "undefined" && i18n) {
        try {
          if (typeof i18n.init === "function") await i18n.init();
        } catch (i18nErr) {
          console.warn('i18n init failed, continuing without translations:', i18nErr);
        }
        // Apply translations to static elements
        if (typeof document.querySelectorAll === "function") {
          document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (key && typeof i18n.t === "function") el.textContent = i18n.t(key);
          });
          // Apply placeholder translations
          document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            if (key && typeof i18n.t === "function") el.placeholder = i18n.t(key);
          });
        }

        // Language selector (real-time, no reload)
        const langSelect = $('lang-select');
        if (langSelect && typeof i18n.getLocale === "function") {
          langSelect.value = i18n.getLocale();
          langSelect.addEventListener('change', async (e) => {
            if (typeof i18n.setLocale === "function") await i18n.setLocale(e.target.value);
            review();
          });
        }
        if (typeof i18n.subscribe === "function") {
          i18n.subscribe(() => {
            review();
          });
        }
      }

      const data = await api("/api/session");
      Object.assign(account, data);

      text(
        "connection",
        data.connected
          ? "Your TikTok account is connected."
          : "Connect your TikTok account to start."
      );
      text(
        "connection-details",
        data.connected
          ? "Granted scopes: " + data.scopes.join(", ")
          : "Authorization uses the official TikTok consent page."
      );

      $("connect").classList.toggle("hidden", data.connected);
      $("disconnect").classList.toggle("hidden", !data.connected);
      $("editor")?.classList.toggle("hidden", !data.connected);
      $("profile")?.classList.toggle("hidden", !data.connected);

      if (data.connected) {
        renderScopeChips(data.scopes);
        $("mode-draft").disabled = !data.scopes.includes("video.upload");
        $("mode-direct").disabled = !data.scopes.includes("video.publish");
        $("photo-mode-draft").disabled = !data.scopes.includes("video.upload");
        $("photo-mode-direct").disabled = !data.scopes.includes("video.publish");
        if ($("mode-draft").disabled && !$("mode-direct").disabled) {
          $("mode-direct").checked = true;
        }
        if ($("photo-mode-draft").disabled && !$("photo-mode-direct").disabled) {
          $("photo-mode-direct").checked = true;
        }
        await Promise.all([loadProfile(), loadCreator()]);
      }
    } catch (err) {
      text("connection", err.message);
    }
    updatePhotoPreparationButton();
    review();
  }

  /* ── Event listeners ─────────────────────────────────────── */

  // File input + drag-and-drop
  const dropZone = $("drop-zone");
  const fileInput = $("file");

  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (file) setFile(file);
  });

  ["dragenter", "dragover"].forEach((evt) => {
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.add("dragover");
    });
  });

  ["dragleave", "drop"].forEach((evt) => {
    dropZone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropZone.classList.remove("dragover");
    });
  });

  dropZone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file && (file.type === "video/mp4" || file.name.toLowerCase().endsWith(".mp4"))) {
      setFile(file);
    } else {
      text("media-result", "Only MP4 files are supported.");
    }
  });

  $("clear-file").addEventListener("click", () => setFile(null));

  $("upload").addEventListener("click", async () => {
    const file = selectedFile || $("file").files[0];
    if (!file) return;

    if (file.size < 12 || file.size > 64 * 1024 * 1024) {
      text("media-result", "The video must be an MP4 under 64 MiB.");
      return;
    }

    $("upload").disabled = true;
    text("media-result", "Uploading to your zTTato workspace…");

    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const asset = await api("/api/media", { method: "POST", body: form });
      activateMediaType("video");
      mediaAssets.video = asset.media_id;
      resetIntent(true);
      text(
        "media-result",
        asset.filename + " · " + (asset.size / 1048576).toFixed(2) + " MiB is ready."
      );
    } catch (err) {
      text("media-result", err.message);
    }
    $("upload").disabled = false;
    review();
  });

  $("photo-urls").addEventListener("input", () => {
    activateMediaType("photo");
    mediaAssets.photo = null;
    resetIntent(true);
    text("photo-result", "");
    updatePhotoPreparationButton();
    review();
  });

  $("photo-cover").addEventListener("input", () => {
    activateMediaType("photo");
    mediaAssets.photo = null;
    resetIntent(true);
    text("photo-result", "");
    updatePhotoPreparationButton();
    review();
  });

  $("photo-upload").addEventListener("click", async () => {
    const { urls, cover, error } = photoForm();
    if (error || photoPreparing || !account.connected || !canPreparePhoto()) return;

    activateMediaType("photo");
    mediaAssets.photo = null;
    resetIntent(true);
    photoPreparing = true;
    updatePhotoPreparationButton();
    text("photo-result", translated("photo.preparing", "Preparing photo set…"));

    try {
      const asset = await api("/api/media/photo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ photo_images: urls, photo_cover_index: cover })
      });
      mediaAssets.photo = asset.media_id;
      text("photo-result", `${urls.length} photos are ready in your workspace.`);
    } catch (err) {
      text("photo-result", err.message);
    }
    photoPreparing = false;
    updatePhotoPreparationButton();
    review();
  });

  $("caption").addEventListener("input", () => {
    activateMediaType("video");
    updateCaptionCount();
    resetIntent(true);
    review();
  });
  $("photo-caption").addEventListener("input", () => {
    activateMediaType("photo");
    updatePhotoCaptionCount();
    resetIntent(true);
    review();
  });

  for (const id of ["mode-draft", "mode-direct", "privacy", "consent",
                    "own-brand", "paid-brand", "ai-content",
                    "photo-mode-draft", "photo-mode-direct", "photo-privacy",
                    "photo-disable-comment", "photo-own-brand", "photo-paid-brand", "photo-ai-content"]) {
    const el = $(id);
    if (el) {
      el.addEventListener("change", () => {
        if (id === "consent") {
          review();
          return;
        }
        activateMediaType(id.startsWith("photo-") ? "photo" : "video");
        resetIntent(true);
        review();
      });
    }
  }

  $("publish").addEventListener("click", async () => {
    const selectedMediaId = activeMediaId();
    if (sending || !selectedMediaId || !$("consent").checked) return;
    if (!idempotencyKey) idempotencyKey = crypto.randomUUID();

    sending = true;
    review();
    report("Queueing your confirmed request securely…");

    const isPhoto = activeMediaType === "photo";
    const payload = {
      media_id: selectedMediaId,
      media_type: activeMediaType,
      mode: mode(),
      idempotency_key: idempotencyKey,
      caption: $(isPhoto ? "photo-caption" : "caption").value,
      consent: true,
      privacy: mode() === "direct" ? privacySelect().value : null,
      disable_comment: $(isPhoto ? "photo-disable-comment" : "disable-comment").checked,
      disable_duet: isPhoto ? false : $("disable-duet").checked,
      disable_stitch: isPhoto ? false : $("disable-stitch").checked,
      brand_organic_toggle: $(isPhoto ? "photo-own-brand" : "own-brand").checked,
      brand_content_toggle: $(isPhoto ? "photo-paid-brand" : "paid-brand").checked,
      is_aigc: $(isPhoto ? "photo-ai-content" : "ai-content").checked
    };

    try {
      const job = await api("/api/publish", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      jobId = job.job_id;
      $("refresh-status").disabled = false;
      report(
        job.status +
          " · " +
          (job.note || "Check status for updates.") +
          (job.idempotent_replay ? " (same request, not duplicated)" : "")
      );
    } catch (err) {
      if (typeof err.detail?.job_id === "string" && err.detail.job_id) {
        jobId = err.detail.job_id;
        $("refresh-status").disabled = false;
        const status = err.detail.job_status || "REQUEST_UNCERTAIN";
        const reason = err.detail.fail_reason || err.message;
        report(status + " · " + reason + " Refresh this saved job before trying again.", true);
      } else {
        report(
          "Request not confirmed: " +
            err.message +
            ". Keep this page open and retry with the same request key; check TikTok status before starting a new request.",
          true
        );
      }
    }
    sending = false;
    review();
  });

  $("refresh-status").addEventListener("click", async () => {
    if (!jobId) return;
    $("refresh-status").disabled = true;
    try {
      const result = await api("/api/jobs/" + encodeURIComponent(jobId));
      report(result.status + (result.fail_reason ? " · " + result.fail_reason : ""));
    } catch (err) {
      report(err.message, true);
    }
    $("refresh-status").disabled = false;
  });

  $("disconnect").addEventListener("click", async () => {
    if (!confirm("Disconnect TikTok from zTTato?")) return;
    try {
      await api("/api/disconnect", { method: "POST" });
      location.reload();
    } catch (err) {
      report(err.message, true);
    }
  });

  $("delete-data").addEventListener("click", async () => {
    if (
      !confirm(
        "Permanently delete your local zTTato account, publishing records and uploaded files?"
      )
    )
      return;
    try {
      await api("/api/my-data", { method: "DELETE" });
      location.href = "/";
    } catch (err) {
      report(err.message, true);
    }
  });

  // Init
  updateCaptionCount();
  boot();
})();
