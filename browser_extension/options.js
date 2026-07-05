"use strict";

const browserApi = typeof browser !== "undefined" ? browser : chrome;
const DEFAULT_API = "http://127.0.0.1:8000/autofill/profile";

function msg(text, isError) {
  const el = document.getElementById("msg");
  el.textContent = text;
  el.style.color = isError ? "#b91c1c" : "#065f46";
}

async function load() {
  const data = await browserApi.storage.local.get(["apiBase", "profile"]);
  document.getElementById("apiBase").value = data.apiBase || DEFAULT_API;
  if (data.profile) {
    document.getElementById("profileJson").value = JSON.stringify(data.profile, null, 2);
  }
}

document.getElementById("save").addEventListener("click", async () => {
  const apiBase = document.getElementById("apiBase").value.trim() || DEFAULT_API;
  await browserApi.storage.local.set({ apiBase });
  msg("Saved API URL.");
});

document.getElementById("import").addEventListener("click", async () => {
  try {
    const raw = document.getElementById("profileJson").value.trim();
    const profile = JSON.parse(raw);
    await browserApi.storage.local.set({ profile, profileSyncedAt: new Date().toISOString() });
    msg(`Imported profile for ${profile.contact?.full_name || "candidate"}.`);
  } catch (err) {
    msg(`Import failed: ${err}`, true);
  }
});

load();
