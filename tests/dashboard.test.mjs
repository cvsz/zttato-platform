import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { runInNewContext } from "node:vm";

const html = readFileSync(new URL("../web/dashboard.html", import.meta.url), "utf8");
const javascript = readFileSync(new URL("../web/dashboard.js", import.meta.url), "utf8");

function dashboard({
  missing = [],
  photoTransferReady = true,
  scopes = ["user.info.basic", "video.upload", "video.publish"],
  publishError = null,
  publishResponse = null,
  jobResponse = null,
} = {}) {
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
  const elements = new Map();
  const listeners = new Map();
  for (const id of ids) {
    if (missing.includes(id)) continue;
    const classes = new Set();
    elements.set(id, {
      id,
      value: "",
      checked: false,
      disabled: false,
      textContent: "",
      files: [],
      classList: {
        add(name) { classes.add(name); },
        remove(name) { classes.delete(name); },
        toggle(name, force) {
          if (force ?? !classes.has(name)) classes.add(name);
          else classes.delete(name);
        },
        contains(name) { return classes.has(name); },
      },
      addEventListener(type, callback) {
        const key = `${id}:${type}`;
        listeners.set(key, [...(listeners.get(key) || []), callback]);
      },
      replaceChildren() { this.options = []; },
      append(item) {
        this.options = [...(this.options || []), item];
        if (!this.value && item.value) this.value = item.value;
      },
      removeAttribute(name) { delete this[name]; },
    });
  }
  const requests = [];
  const responses = {
    "/api/session": {
      connected: true,
      scopes,
      audited: false,
      photo_transfer_ready: photoTransferReady,
    },
    "/api/profile": {
      display_name: "Fixture Creator",
      avatar_url: "https://p16-sign.tiktokcdn-us.com/fixture.jpeg",
    },
    "/api/creator-info": {
      nickname: "Fixture Creator",
      username: "fixture_creator",
      privacy_level_options: ["SELF_ONLY"],
      comment_disabled: false,
      duet_disabled: false,
      stitch_disabled: false,
    },
    "/api/media/photo": {
      media_id: "photo-media-fixture",
      filename: "photo_set.json",
      size: 64,
    },
    "/api/publish": publishResponse || {
      job_id: "photo-job-fixture",
      status: "PROCESSING",
      note: "Fixture only",
    },
  };
  const requestDetails = [];
  let publishAttempts = 0;
  const fetch = async (url, options = {}) => {
    requests.push(url);
    requestDetails.push({ url, options });
    if (url === "/api/publish") {
      publishAttempts += 1;
      if (publishError && publishAttempts === 1) {
        return { ok: false, status: 502, json: async () => ({ detail: publishError }) };
      }
    }
    if (url.startsWith("/api/jobs/")) {
      return {
        ok: true,
        json: async () => jobResponse || {
          job_id: "photo-job-fixture",
          status: "PROCESSING",
          fail_reason: null,
        },
      };
    }
    if (!(url in responses)) throw new Error(`Unexpected request: ${url}`);
    return { ok: true, json: async () => responses[url] };
  };
  const document = {
    cookie: "zttato_csrf=fixture-csrf",
    getElementById: (id) => elements.get(id) || null,
    createElement: (tag) => ({ tag, textContent: "", value: "" }),
  };
  runInNewContext(javascript, {
    document, fetch, decodeURIComponent,
    URL,
    confirm: () => false, crypto: { randomUUID: () => "fixture-uuid" },
    location: { reload() {}, href: "" },
  }, { filename: "dashboard.js" });
  const trigger = async (id, type = "click", event = {}) => {
    for (const callback of listeners.get(`${id}:${type}`) || []) {
      await callback({ target: elements.get(id), ...event });
    }
  };
  return { elements, requests, requestDetails, trigger };
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

test("authorized basic profile renders independently from creator posting options", async () => {
  const { elements, requests } = dashboard();
  await flush();
  assert.equal(elements.get("connection").textContent, "Your TikTok account is connected.");
  assert.equal(elements.get("profile-name").textContent, "Fixture Creator");
  assert.equal(elements.get("profile-avatar").src, "https://p16-sign.tiktokcdn-us.com/fixture.jpeg");
  assert.ok(requests.includes("/api/profile"));
  assert.ok(requests.includes("/api/creator-info"));
});

test("missing optional interaction control fails closed without hiding connected profile", async () => {
  const { elements } = dashboard({ missing: ["disable-stitch"] });
  await flush();
  assert.equal(elements.get("profile-name").textContent, "Fixture Creator");
  assert.equal(elements.get("mode-direct").disabled, true);
  assert.match(elements.get("creator-message").textContent, /Dashboard UI version mismatch/);
});

test("photo draft flow validates URLs, prepares a set and submits the selected caption after consent", async () => {
  const { elements, requests, requestDetails, trigger } = dashboard();
  await flush();

  elements.get("photo-urls").value = "https://media.example/photo-01.webp";
  await trigger("photo-urls", "input");
  assert.equal(elements.get("photo-upload").disabled, false);

  await trigger("photo-upload");
  assert.equal(elements.get("photo-result").textContent, "1 photos are ready in your workspace.");
  assert.ok(requests.includes("/api/media/photo"));

  elements.get("photo-caption").value = "Photo caption 😀";
  await trigger("photo-caption", "input");
  elements.get("photo-mode-draft").checked = true;
  await trigger("photo-mode-draft", "change");
  elements.get("consent").checked = true;
  await trigger("consent", "change");
  assert.equal(elements.get("publish").disabled, false);

  await trigger("publish");
  const publish = requestDetails.find((item) => item.url === "/api/publish");
  assert.ok(publish);
  const payload = JSON.parse(publish.options.body);
  assert.equal(payload.media_id, "photo-media-fixture");
  assert.equal(payload.media_type, "photo");
  assert.equal(payload.mode, "draft");
  assert.equal(payload.caption, "Photo caption 😀");
  assert.equal(payload.consent, true);
  assert.equal(payload.privacy, null);
  assert.equal(payload.disable_duet, false);
  assert.equal(payload.disable_stitch, false);
});

test("photo direct post uses current creator privacy and photo-specific disclosures", async () => {
  const { elements, requestDetails, trigger } = dashboard();
  await flush();

  elements.get("photo-urls").value = "https://media.example/photo-01.jpg";
  await trigger("photo-urls", "input");
  await trigger("photo-upload");
  elements.get("photo-caption").value = "Photo disclosure test";
  await trigger("photo-caption", "input");
  elements.get("photo-mode-direct").checked = true;
  await trigger("photo-mode-direct", "change");
  elements.get("photo-own-brand").checked = true;
  await trigger("photo-own-brand", "change");
  elements.get("photo-ai-content").checked = true;
  await trigger("photo-ai-content", "change");
  elements.get("consent").checked = true;
  await trigger("consent", "change");
  assert.equal(elements.get("photo-privacy").value, "SELF_ONLY");
  assert.equal(elements.get("publish").disabled, false);

  await trigger("publish");
  const publish = requestDetails.find((item) => item.url === "/api/publish");
  const payload = JSON.parse(publish.options.body);
  assert.equal(payload.media_type, "photo");
  assert.equal(payload.mode, "direct");
  assert.equal(payload.privacy, "SELF_ONLY");
  assert.equal(payload.disable_comment, false);
  assert.equal(payload.brand_organic_toggle, true);
  assert.equal(payload.brand_content_toggle, false);
  assert.equal(payload.is_aigc, true);
});

test("photo direct post can be prepared with video.publish when video.upload is not granted", async () => {
  const { elements, requestDetails, trigger } = dashboard({ scopes: ["user.info.basic", "video.publish"] });
  await flush();

  elements.get("photo-urls").value = "https://media.example/photo-01.webp";
  await trigger("photo-urls", "input");
  assert.equal(elements.get("photo-upload").disabled, false);
  await trigger("photo-upload");

  elements.get("photo-mode-direct").checked = true;
  await trigger("photo-mode-direct", "change");
  elements.get("consent").checked = true;
  await trigger("consent", "change");
  assert.equal(elements.get("photo-mode-draft").disabled, true);
  assert.equal(elements.get("publish").disabled, false);

  await trigger("publish");
  const publish = requestDetails.find((item) => item.url === "/api/publish");
  assert.equal(JSON.parse(publish.options.body).mode, "direct");
});

test("photo post stays disabled until a TikTok-verified URL prefix is configured", async () => {
  const { elements } = dashboard({ photoTransferReady: false });
  await flush();
  assert.equal(elements.get("photo-upload").disabled, true);
  assert.match(elements.get("photo-result").textContent, /TikTok verifies and configures/);
});

test("uncertain publish errors expose the saved job and retries reuse its idempotency key", async () => {
  const failReason = "Keep the request key unchanged and refresh this job.";
  const { elements, requestDetails, trigger } = dashboard({
    publishError: {
      message: "TikTok did not confirm the publish request.",
      job_id: "uncertain-job-fixture",
      job_status: "INITIATION_UNCERTAIN",
      fail_reason: failReason,
    },
    publishResponse: {
      job_id: "uncertain-job-fixture",
      status: "INITIATION_UNCERTAIN",
      idempotent_replay: true,
    },
    jobResponse: {
      job_id: "uncertain-job-fixture",
      status: "INITIATION_UNCERTAIN",
      fail_reason: failReason,
    },
  });
  await flush();

  elements.get("photo-urls").value = "https://media.example/photo-01.jpg";
  await trigger("photo-urls", "input");
  await trigger("photo-upload");
  elements.get("photo-mode-draft").checked = true;
  await trigger("photo-mode-draft", "change");
  elements.get("consent").checked = true;
  await trigger("consent", "change");

  await trigger("publish");
  assert.equal(elements.get("refresh-status").disabled, false);
  assert.match(elements.get("status").textContent, /INITIATION_UNCERTAIN/);
  assert.match(elements.get("status").textContent, new RegExp(failReason));

  await trigger("refresh-status");
  assert.match(elements.get("status").textContent, /INITIATION_UNCERTAIN/);

  await trigger("publish");
  const publishes = requestDetails.filter((item) => item.url === "/api/publish");
  assert.equal(publishes.length, 2);
  const first = JSON.parse(publishes[0].options.body);
  const retry = JSON.parse(publishes[1].options.body);
  assert.equal(retry.idempotency_key, first.idempotency_key);
  assert.match(elements.get("status").textContent, /same request, not duplicated/);
});
