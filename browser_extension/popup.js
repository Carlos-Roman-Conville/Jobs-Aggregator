"use strict";

const browserApi = typeof browser !== "undefined" ? browser : chrome;
const DEFAULT_API = "http://127.0.0.1:8000/autofill/profile";

function setStatus(text, isError) {
  const el = document.getElementById("status");
  el.textContent = text;
  el.style.color = isError ? "#b91c1c" : "#374151";
}

async function loadProfile() {
  const data = await browserApi.storage.local.get(["profile", "profileSyncedAt"]);
  if (data.profile) {
    const when = data.profileSyncedAt ? ` (synced ${new Date(data.profileSyncedAt).toLocaleString()})` : "";
    setStatus(`Ready: ${data.profile.contact?.full_name || "profile loaded"}${when}`);
    return data.profile;
  }
  setStatus("No profile in extension storage. Click Sync profile from pipeline.", true);
  return null;
}

async function syncProfile() {
  setStatus("Syncing from pipeline API…");
  const stored = await browserApi.storage.local.get(["apiBase"]);
  const apiBase = stored.apiBase || DEFAULT_API;
  try {
    const res = await browserApi.runtime.sendMessage({ type: "SYNC_PROFILE", apiBase });
    if (!res?.ok) throw new Error(res?.error || "sync failed");
    setStatus(`Synced ${res.profile?.contact?.full_name || "profile"} — ${res.profile?.experience?.length || 0} jobs`);
    return res.profile;
  } catch (err) {
    setStatus(`Sync failed: ${err}. Start API (port 8000) or import JSON in extension Options.`, true);
    return null;
  }
}

function apiBaseRoot(apiBase) {
  try {
    const u = new URL(apiBase);
    return `${u.protocol}//${u.host}`;
  } catch (_e) {
    return "http://127.0.0.1:8000";
  }
}

async function fetchRecentResumes() {
  const stored = await browserApi.storage.local.get(["apiBase"]);
  const root = apiBaseRoot(stored.apiBase || DEFAULT_API);
  try {
    const res = await fetch(`${root}/autofill/recent_resumes?limit=12`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = await res.json();
    return body.resumes || [];
  } catch (_e) {
    return null;
  }
}

function hostFromUrl(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (_e) {
    return "";
  }
}

async function fetchSavedCredential(host) {
  if (!host) return null;
  const stored = await browserApi.storage.local.get(["apiBase"]);
  const root = apiBaseRoot(stored.apiBase || DEFAULT_API);
  try {
    const res = await fetch(`${root}/autofill/credentials?domain=${encodeURIComponent(host)}`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = await res.json();
    return body.credential || null;
  } catch (_e) {
    return null;
  }
}

async function saveCredentialToLedger(host, email, password, applicationUrl) {
  if (!host || !email || !password) return null;
  const stored = await browserApi.storage.local.get(["apiBase"]);
  const root = apiBaseRoot(stored.apiBase || DEFAULT_API);
  try {
    const res = await fetch(`${root}/autofill/credentials`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ domain: host, email, password, application_url: applicationUrl || "" }),
    });
    if (!res.ok) return null;
    return await res.json();
  } catch (_e) {
    return null;
  }
}

async function detectAccountCreate(tabId) {
  if (!tabId) return false;
  try {
    if (browserApi.webNavigation && browserApi.webNavigation.getAllFrames) {
      const frames = await browserApi.webNavigation.getAllFrames({ tabId });
      for (const f of frames) {
        try {
          const r = await browserApi.tabs.sendMessage(tabId, { type: "CHECK_ACCOUNT_CREATE" }, { frameId: f.frameId });
          if (r?.ok && r.is_account_create) return true;
        } catch (_e) { /* skip frame */ }
      }
      return false;
    }
    const r = await browserApi.tabs.sendMessage(tabId, { type: "CHECK_ACCOUNT_CREATE" });
    return !!(r?.ok && r.is_account_create);
  } catch (_e) {
    return false;
  }
}

function renderCredsSection({ saved, profile, isAccountCreate, host }) {
  const section = document.getElementById("creds-section");
  if (!section) return;
  const acct = (profile && profile.ats_account) || {};
  const email = (profile && profile.contact && profile.contact.email) || "";
  // Show the section when: (a) we have a saved credential for this host, OR
  // (b) the page looks like an account-create page and we have a shared
  // password ready to use.
  const hasShared = !!acct.password;
  if (!saved && !(isAccountCreate && hasShared)) {
    section.classList.add("hidden");
    return;
  }
  const display = saved
    ? { email: saved.email, password: saved.password, note: `Saved for ${host} on ${new Date(saved.created_at || Date.now()).toLocaleDateString()}` }
    : { email, password: acct.password, note: `New account-create detected on ${host}. Will be saved after Fill.` };
  document.getElementById("cred-email").textContent = display.email || "(no email)";
  document.getElementById("cred-password").textContent = display.password ? "•".repeat(Math.min(20, display.password.length)) + ` (${display.password.length} chars)` : "(no password)";
  document.getElementById("cred-email").dataset.value = display.email || "";
  document.getElementById("cred-password").dataset.value = display.password || "";
  document.getElementById("cred-note").textContent = display.note;
  document.getElementById("creds-title").textContent = saved ? "🔐 Saved credentials" : "🔐 Account creation detected";
  section.classList.remove("hidden");
}

async function fetchTailoredForUrl(tabUrl) {
  const stored = await browserApi.storage.local.get(["apiBase"]);
  const root = apiBaseRoot(stored.apiBase || DEFAULT_API);
  try {
    const q = tabUrl ? `?url=${encodeURIComponent(tabUrl)}` : "";
    const res = await fetch(`${root}/autofill/tailored${q}`, { cache: "no-store" });
    if (!res.ok) return null;
    const body = await res.json();
    return body.match || null;
  } catch (_e) {
    return null;
  }
}

function pickResumeForActiveTab(resumes, tabUrl) {
  if (!Array.isArray(resumes) || !resumes.length) return null;
  // Prefer PDF over MD.
  const pdfs = resumes.filter((r) => r.filename.toLowerCase().endsWith(".pdf"));
  const pool = pdfs.length ? pdfs : resumes;
  // Try to match the host's company-ish tokens against filenames.
  if (tabUrl) {
    try {
      const host = new URL(tabUrl).hostname.toLowerCase();
      // Strip common suffixes / subdomains: "infinx.applytojob.com" → ["infinx", "applytojob"]
      const tokens = host.replace(/\.(com|net|org|io|co|us|ai)$/, "").split(".").filter(Boolean);
      for (const tok of tokens) {
        if (tok.length < 3 || /^(www|jobs|careers|apply|app)$/i.test(tok)) continue;
        const hit = pool.find((r) => r.filename.toLowerCase().includes(tok));
        if (hit) return hit;
      }
    } catch (_e) { /* fall through */ }
  }
  // Default: most-recent.
  return pool[0] || null;
}

async function showResumeSection(resumes, tabUrl, fileInputCount) {
  const picked = pickResumeForActiveTab(resumes || [], tabUrl);
  const section = document.getElementById("resume-section");
  if (!picked) {
    section.classList.add("hidden");
    return;
  }
  document.getElementById("resume-filename").textContent = picked.filename;
  document.getElementById("copy-filename").dataset.value = picked.filename;
  document.getElementById("copy-path").dataset.value = picked.path;
  const highlightBtn = document.getElementById("highlight-field");
  highlightBtn.classList.toggle("hidden", !fileInputCount);
  section.classList.remove("hidden");
}

async function copyToClipboard(value, source) {
  try {
    await navigator.clipboard.writeText(value);
    setStatus(`Copied: ${value.length > 40 ? value.slice(0, 37) + "…" : value}`);
  } catch (err) {
    setStatus(`Copy failed: ${err}`, true);
  }
}

async function fillActiveTab(profile) {
  const [tab] = await browserApi.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) throw new Error("no active tab");

  // Merge tailored content (per-job skills + summary) into the profile
  // BEFORE sending to the content script. The engine reads profile.tailored
  // and prefers it over the static profile for JD-specific textareas.
  const tailored = await fetchTailoredForUrl(tab.url);
  const profileWithTailored = tailored
    ? { ...profile, tailored: {
        skills_technical: tailored.skills_technical || "",
        skills_soft: tailored.skills_soft || "",
        summary: tailored.summary || "",
        source_filename: tailored.filename || "",
      } }
    : profile;

  const results = [];
  async function fillFrame(frameId) {
    try {
      const opts = frameId == null ? {} : { frameId };
      return await browserApi.tabs.sendMessage(tab.id, { type: "FILL_PAGE", profile: profileWithTailored }, opts);
    } catch (_e) {
      return null;
    }
  }

  if (browserApi.webNavigation && browserApi.webNavigation.getAllFrames) {
    const frames = await browserApi.webNavigation.getAllFrames({ tabId: tab.id });
    for (const frame of frames) {
      const resp = await fillFrame(frame.frameId);
      if (resp?.count || resp?.resume_file_inputs?.length) results.push(resp);
    }
  } else {
    const resp = await fillFrame(undefined);
    if (resp?.count || resp?.resume_file_inputs?.length) results.push(resp);
  }

  if (!results.length) {
    throw new Error("Could not reach page. Reload the application tab and try again.");
  }
  const total = results.reduce((n, r) => n + (r.count || 0), 0);
  const sample = results.flatMap((r) => r.filled || []).slice(0, 6);
  const fileInputCount = results.reduce((n, r) => n + ((r.resume_file_inputs || []).length), 0);
  const tailoredHits = sample.filter((s) => s.startsWith("tailored.")).length;
  const tailNote = tailored ? ` (tailored: ${tailored.filename}, ${tailoredHits} JD-aware fills)` : "";
  setStatus(`Filled ${total} fields across ${results.length} frame(s)${tailNote}.\n${sample.join("\n")}`);

  // After fill, surface the resume PDF the user should attach.
  const resumes = await fetchRecentResumes();
  await showResumeSection(resumes, tab.url, fileInputCount);

  // If any frame just filled an account-create form, persist the cred to
  // the local ledger so future visits to this domain show "saved login".
  const filledAccountCreate = results.some(
    (r) => r.is_account_create && (r.filled || []).some((f) => f.startsWith("ats_account.")),
  );
  if (filledAccountCreate) {
    const host = hostFromUrl(tab.url);
    const email = profileWithTailored.contact?.email || "";
    const password = profileWithTailored.ats_account?.password || "";
    if (host && email && password) {
      const saved = await saveCredentialToLedger(host, email, password, tab.url);
      if (saved?.ok) {
        renderCredsSection({ saved: saved.credential, profile: profileWithTailored, isAccountCreate: true, host });
      }
    }
  }
}

async function highlightResumeFieldOnPage() {
  const [tab] = await browserApi.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) return;
  try {
    if (browserApi.webNavigation && browserApi.webNavigation.getAllFrames) {
      const frames = await browserApi.webNavigation.getAllFrames({ tabId: tab.id });
      for (const f of frames) {
        try {
          const r = await browserApi.tabs.sendMessage(tab.id, { type: "HIGHLIGHT_RESUME_FIELD" }, { frameId: f.frameId });
          if (r?.ok) {
            setStatus(`Highlighted resume field (${r.label || ""}).`);
            return;
          }
        } catch (_e) { /* skip frame */ }
      }
    } else {
      const r = await browserApi.tabs.sendMessage(tab.id, { type: "HIGHLIGHT_RESUME_FIELD" });
      if (r?.ok) {
        setStatus(`Highlighted resume field (${r.label || ""}).`);
        return;
      }
    }
    setStatus("No resume file input found on this page.", true);
  } catch (err) {
    setStatus(`Highlight failed: ${err}`, true);
  }
}

document.getElementById("sync").addEventListener("click", () => {
  syncProfile();
});

document.getElementById("fill").addEventListener("click", async () => {
  try {
    let profile = (await browserApi.storage.local.get(["profile"])).profile;
    if (!profile) profile = await syncProfile();
    if (!profile) return;
    setStatus("Filling…");
    await fillActiveTab(profile);
  } catch (err) {
    setStatus(String(err), true);
  }
});

document.getElementById("copy-filename").addEventListener("click", (e) => {
  copyToClipboard(e.currentTarget.dataset.value || "", "filename");
});

document.getElementById("copy-path").addEventListener("click", (e) => {
  copyToClipboard(e.currentTarget.dataset.value || "", "path");
});

document.getElementById("highlight-field").addEventListener("click", () => {
  highlightResumeFieldOnPage();
});

document.getElementById("copy-email").addEventListener("click", (e) => {
  copyToClipboard(document.getElementById("cred-email").dataset.value || "", "email");
});

document.getElementById("copy-password").addEventListener("click", (e) => {
  copyToClipboard(document.getElementById("cred-password").dataset.value || "", "password");
});

(async function init() {
  const profile = await loadProfile();
  // Preview the resume the user is likely to attach even before they click Fill.
  const [tab] = await browserApi.tabs.query({ active: true, currentWindow: true });
  const resumes = await fetchRecentResumes();
  if (resumes && resumes.length) {
    await showResumeSection(resumes, tab?.url || "", 0);
  }

  // Surface ATS credentials section: if we have a saved cred for this host
  // OR the page is an account-create page, show password + email so user
  // can copy them if extension fill doesn't reach a field.
  const host = hostFromUrl(tab?.url || "");
  const saved = host ? await fetchSavedCredential(host) : null;
  const isCreate = await detectAccountCreate(tab?.id);
  renderCredsSection({ saved, profile, isAccountCreate: isCreate, host });
})();
