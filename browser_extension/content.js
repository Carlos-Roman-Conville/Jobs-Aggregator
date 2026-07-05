"use strict";

(function () {
  const browserApi = typeof browser !== "undefined" ? browser : chrome;

  browserApi.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (!msg) return false;
    if (msg.type === "FILL_PAGE") {
      try {
        const result = window.JobPipelineAutofill.fillProfile(msg.profile, document);
        sendResponse({ ok: true, frame: window.location.href, ...result });
      } catch (err) {
        sendResponse({ ok: false, error: String(err), frame: window.location.href });
      }
      return true;
    }
    if (msg.type === "CHECK_ACCOUNT_CREATE") {
      try {
        const api = window.JobPipelineAutofill || {};
        const isCreate = typeof api.isAccountCreateContext === "function"
          ? api.isAccountCreateContext(document)
          : false;
        sendResponse({
          ok: true,
          is_account_create: !!isCreate,
          page_url: window.location.href,
          frame: window.location.href,
        });
      } catch (err) {
        sendResponse({ ok: false, error: String(err), frame: window.location.href });
      }
      return true;
    }
    if (msg.type === "HIGHLIGHT_RESUME_FIELD") {
      try {
        if (typeof window.__JPA_highlightResumeField === "function") {
          const r = window.__JPA_highlightResumeField();
          sendResponse({ ...r, frame: window.location.href });
        } else {
          sendResponse({ ok: false, error: "engine_not_loaded", frame: window.location.href });
        }
      } catch (err) {
        sendResponse({ ok: false, error: String(err), frame: window.location.href });
      }
      return true;
    }
    return false;
  });
})();
