"use strict";
(() => {
  const $ = (id) => document.getElementById(id);

  const account = { connected: false, scopes: [], audited: false };
  let mediaId = null;
  let jobId = null;
  let idempotencyKey = null;
  let sending = false;
  let selectedFile = null;

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
      let reason = "Request failed (" + response.status + ")";
      try {
        const data = await response.json();
        reason = data.detail || reason;
      } catch (_) {}
      throw new Error(typeof reason === "string" ? reason : "Request rejected.");
    }
    return response.json();
  }

  function report(message, isError = false) {
    text("status", message);
    $("status").classList.toggle("error", isError);
  }

  function mode() {
    return $("mode-direct")?.checked ? "direct" : "draft";
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

  function buildSummary() {
    const card = $("summary");
    if (!card) return;

    if (!mediaId) {
      card.textContent = "Upload an MP4 first.";
      return;
    }

    const parts = [];
    parts.push("Video ready in workspace");
    parts.push(`Mode · ${mode() === "draft" ? "Draft (finish inside TikTok)" : "Direct Post"}`);

    if (mode() === "direct") {
      const privacy = $("privacy")?.value;
      parts.push(`Visibility · ${labels[privacy] || privacy || "—"}`);

      const disclosures = [];
      if ($("own-brand")?.checked) disclosures.push("Own business");
      if ($("paid-brand")?.checked) disclosures.push("Paid partnership");
      if ($("ai-content")?.checked) disclosures.push("AI-generated");
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
    $("direct-options")?.classList.toggle("hidden", mode() !== "direct");

    const ready =
      Boolean(mediaId) &&
      Boolean($("consent")?.checked) &&
      !sending;

    const publish = $("publish");
    if (publish) {
      const directBlocked =
        mode() === "direct" &&
        (!$("privacy")?.value ||
          !account.scopes.includes("video.publish") ||
          Boolean($("mode-direct")?.disabled));

      publish.disabled = !ready || directBlocked;
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
      text(
        "creator-message",
        "Direct Post requires the video.publish scope. Reconnect TikTok with this permission."
      );
      review();
      return;
    }

    try {
      const info = await api("/api/creator-info");
      const select = $("privacy");
      select.replaceChildren();

      const choices = info.privacy_level_options.filter(
        (value) => account.audited || value === "SELF_ONLY"
      );

      for (const value of choices) {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = labels[value] || value;
        select.append(option);
      }

      select.disabled = choices.length === 0;
      setCreatorFlag("disable-comment", info.comment_disabled);
      setCreatorFlag("disable-duet", info.duet_disabled);
      setCreatorFlag("disable-stitch", info.stitch_disabled);

      text(
        "creator-message",
        "Connected: " +
          (info.nickname || info.username || "TikTok creator") +
          (account.audited ? "." : " · Unaudited app: Direct Post is limited to Only me.") +
          (info.max_video_post_duration_sec
            ? " Max video duration: " + info.max_video_post_duration_sec + " seconds."
            : "")
      );

      if (!choices.length) $("mode-direct").disabled = true;
    } catch (err) {
      if ($("mode-direct")) $("mode-direct").disabled = true;
      text("creator-message", "Creator options unavailable: " + err.message);
    }
    review();
  }

  function setFile(file) {
    selectedFile = file || null;
    mediaId = null;
    jobId = null;
    idempotencyKey = null;
    $("refresh-status").disabled = true;
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
      // Initialize optional i18n (non-blocking, with fallback).
      if (typeof i18n !== "undefined" && i18n) {
        try {
          if (typeof i18n.init === "function") await i18n.init();
        } catch (i18nErr) {
          console.warn("i18n init failed, continuing without translations:", i18nErr);
        }
        if (typeof document.querySelectorAll === "function") {
          document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.getAttribute('data-i18n');
            if (key && typeof i18n.t === "function") el.textContent = i18n.t(key);
          });
          document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
            const key = el.getAttribute('data-i18n-placeholder');
            if (key && typeof i18n.t === "function") el.placeholder = i18n.t(key);
          });
        }
        const langSelect = $('lang-select');
        if (langSelect && typeof i18n.getLocale === "function") {
          langSelect.value = i18n.getLocale();
          if (typeof i18n.setLocale === "function") {
            langSelect.addEventListener('change', async (e) => {
              await i18n.setLocale(e.target.value);
              location.reload();
            });
          }
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
        if ($("mode-draft").disabled && !$("mode-direct").disabled) {
          $("mode-direct").checked = true;
        }
        await Promise.all([loadProfile(), loadCreator()]);
      }
    } catch (err) {
      text("connection", err.message);
    }
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
      mediaId = asset.media_id;
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

  $("caption").addEventListener("input", updateCaptionCount);

  for (const id of ["mode-draft", "mode-direct", "privacy", "consent",
                    "own-brand", "paid-brand", "ai-content"]) {
    const el = $(id);
    if (el) {
      el.addEventListener("change", () => {
        if (id.startsWith("mode")) idempotencyKey = null;
        review();
      });
    }
  }

  $("publish").addEventListener("click", async () => {
    if (sending || !mediaId || !$("consent").checked) return;
    if (!idempotencyKey) idempotencyKey = crypto.randomUUID();

    sending = true;
    review();
    report("Sending your confirmed request to TikTok…");

    const payload = {
      media_id: mediaId,
      mode: mode(),
      idempotency_key: idempotencyKey,
      caption: $("caption").value,
      consent: true,
      privacy: mode() === "direct" ? $("privacy").value : null,
      disable_comment: $("disable-comment").checked,
      disable_duet: $("disable-duet").checked,
      disable_stitch: $("disable-stitch").checked,
      brand_organic_toggle: $("own-brand").checked,
      brand_content_toggle: $("paid-brand").checked,
      is_aigc: $("ai-content").checked
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
      report(
        "Request not confirmed: " +
          err.message +
          ". If the result is uncertain, retry with the same request key or contact support.",
        true
      );
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