"use strict";

const browserApi = typeof browser !== "undefined" ? browser : chrome;
const DEFAULT_API = "http://127.0.0.1:8000/autofill/profile";

async function seedDefaultProfileIfEmpty() {
  const data = await browserApi.storage.local.get(["profile"]);
  if (data.profile) return data.profile;
  try {
    const url = browserApi.runtime.getURL("default_profile.json");
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) return null;
    const profile = await res.json();
    await browserApi.storage.local.set({
      profile,
      profileSyncedAt: new Date().toISOString(),
      profileSource: "bundled_default",
    });
    return profile;
  } catch (_err) {
    return null;
  }
}

async function getSettings() {
  const data = await browserApi.storage.local.get(["apiBase", "profile"]);
  let profile = data.profile || null;
  if (!profile) profile = await seedDefaultProfileIfEmpty();
  return {
    apiBase: data.apiBase || DEFAULT_API,
    profile,
  };
}

async function syncProfileFromPipeline(apiBase) {
  const url = (apiBase || DEFAULT_API).replace(/\/+$/, "");
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const body = await res.json();
  const profile = body.profile || body;
  await browserApi.storage.local.set({
    profile,
    profileSyncedAt: new Date().toISOString(),
    profileSource: "pipeline_api",
  });
  return profile;
}

browserApi.runtime.onInstalled.addListener(() => {
  seedDefaultProfileIfEmpty();
});

browserApi.runtime.onStartup.addListener(() => {
  seedDefaultProfileIfEmpty();
});

seedDefaultProfileIfEmpty();

browserApi.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg?.type === "SYNC_PROFILE") {
    (async () => {
      const settings = await getSettings();
      const profile = await syncProfileFromPipeline(msg.apiBase || settings.apiBase);
      sendResponse({ ok: true, profile });
    })().catch((err) => sendResponse({ ok: false, error: String(err) }));
    return true;
  }
  return false;
});
