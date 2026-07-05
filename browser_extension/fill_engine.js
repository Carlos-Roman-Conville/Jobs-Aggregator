/**
 * Job application autofill engine — label/name/placeholder matching for ATS forms.
 * Loaded in content scripts (all frames). Exposes window.JobPipelineAutofill.
 */
(function (global) {
  "use strict";

  const SKIP_TEXT_TYPES = new Set(["hidden", "submit", "button", "image", "reset", "file", "radio", "checkbox"]);

  const CONTACT_RULES = [
    { key: "first_name", patterns: [/first\s*name/i, /\bfname\b/i, /given\s*name/i, /forename/i] },
    { key: "middle_name", patterns: [/middle\s*name/i, /\bmname\b/i, /middle\s*initial/i] },
    { key: "last_name", patterns: [/last\s*name/i, /\blname\b/i, /family\s*name/i, /surname/i] },
    { key: "full_name", patterns: [/full\s*name/i, /candidate\s*name/i, /your\s*name/i, /^name$/i] },
    { key: "email", patterns: [/e-?mail/i, /email\s*address/i] },
    { key: "phone", patterns: [/phone/i, /mobile/i, /telephone/i, /\btel\b/i, /cell/i] },
    // Street address — MUST appear before the generic `location` rule so
    // "Street Address" / "Address Line 1" inputs grab the actual street,
    // not the "City, State" location string.
    { key: "street_address", patterns: [/street\s*address/i, /address\s*line\s*1/i, /\baddress\s*1\b/i, /mailing\s*address/i, /home\s*address/i, /^address$/i] },
    { key: "city", patterns: [/\bcity\b/i, /municipality/i] },
    { key: "state", patterns: [/\bstate\b/i, /province/i, /region/i] },
    { key: "postal_code", patterns: [/postal\s*code/i, /zip\s*code/i, /\bzipcode\b/i, /\bzip\b/i, /postcode/i, /\bpostal\b/i] },
    { key: "location", patterns: [/current\s*location/i, /your\s*location/i, /location/i] },
    { key: "country", patterns: [/country/i] },
    { key: "linkedin", patterns: [/linkedin/i] },
    { key: "github", patterns: [/github/i] },
    { key: "website", patterns: [/website/i, /portfolio/i, /personal\s*site/i] },
  ];

  const SUMMARY_RULES = [
    { key: "summary", patterns: [/professional\s*summary/i, /candidate\s*summary/i, /\bsummary\b/i, /about\s*you/i, /\bprofile\b/i, /\bobjective\b/i, /\bbio\b/i] },
  ];

  const COVER_RULES = [
    { key: "cover_letter", patterns: [/cover\s*letter/i, /why\s*are\s*you\s*interested/i, /why\s*this\s*role/i, /tell\s*us\s*about\s*yourself/i] },
  ];

  // Tailored content rules — populated when profile.tailored is present.
  // These fields ask for JD-specific answers; using the per-job tailored
  // skills/summary is markedly better than the static profile.
  const TAILORED_TOOLS_RULES = [
    {
      key: "skills_technical",
      patterns: [
        /tools.*systems.*familiar/i,
        /tools\s*and\s*technolog/i,
        /technologies.*familiar/i,
        /tech\s*stack/i,
        /technical\s*skills/i,
        /software\s*you.*used/i,
        /software\s*tools/i,
        /relevant\s*(technical\s*)?skills/i,
        /describe\s*your\s*(technical|relevant)\s*experience/i,
      ],
    },
  ];

  const TAILORED_SUMMARY_RULES = [
    {
      key: "summary",
      patterns: [
        /professional\s*summary/i,
        /candidate\s*summary/i,
        /about\s*you/i,
        /tell\s*us\s*about\s*yourself/i,
        /why\s*are\s*you\s*a\s*good\s*fit/i,
        /why\s*this\s*role/i,
        /describe\s*yourself/i,
      ],
    },
  ];

  const EXP_RULES = [
    { key: "company", patterns: [/company/i, /employer/i, /organization/i, /organisation/i] },
    { key: "title", patterns: [/job\s*title/i, /\bposition\b/i, /\brole\b/i, /\btitle\b/i, /occupation/i] },
    { key: "location", patterns: [/work\s*location/i, /location/i] },
    { key: "start_display", patterns: [/start\s*date/i, /\bfrom\b/i, /\bbegin\b/i] },
    { key: "end_display", patterns: [/end\s*date/i, /\bto\b/i, /through/i, /\buntil\b/i] },
    { key: "start_month", patterns: [/start.*month/i, /from.*month/i] },
    { key: "start_year", patterns: [/start.*year/i, /from.*year/i] },
    { key: "end_month", patterns: [/end.*month/i, /to.*month/i] },
    { key: "end_year", patterns: [/end.*year/i, /to.*year/i] },
    { key: "description", patterns: [/description/i, /responsibilit/i, /duties/i, /accomplish/i, /details/i] },
  ];

  const EDU_RULES = [
    { key: "school", patterns: [/school/i, /university/i, /institution/i, /college/i] },
    { key: "degree", patterns: [/degree/i, /qualification/i, /diploma/i] },
    { key: "field_of_study", patterns: [/field\s*of\s*study/i, /major/i, /discipline/i] },
    { key: "graduation_display", patterns: [/graduat/i, /completion/i, /year\s*obtained/i] },
    { key: "details", patterns: [/details/i, /honors/i, /gpa/i, /notes/i] },
  ];

  const REF_RULES = [
    { key: "name", patterns: [/reference\s*name/i, /referrer\s*name/i, /referee/i, /full\s*name/i, /^name$/i] },
    { key: "first_name", patterns: [/reference\s*first/i, /referrer\s*first/i, /ref.*first\s*name/i] },
    { key: "last_name", patterns: [/reference\s*last/i, /referrer\s*last/i, /ref.*last\s*name/i] },
    { key: "title", patterns: [/reference\s*title/i, /referrer\s*title/i, /job\s*title/i, /position/i] },
    { key: "company", patterns: [/reference\s*company/i, /referrer\s*company/i, /organization/i, /employer/i] },
    { key: "relationship", patterns: [/relationship/i, /relation/i, /how\s*do\s*you\s*know/i] },
    { key: "email", patterns: [/reference\s*email/i, /referrer\s*email/i, /ref.*email/i, /e-?mail/i] },
    { key: "phone", patterns: [/reference\s*phone/i, /referrer\s*phone/i, /ref.*phone/i, /phone/i, /mobile/i] },
  ];

  // Screening questions: each rule matches a question's text and resolves to an answer in profile.screening.
  // valueRef: dotted path inside profile.screening (e.g. "work_authorization.requires_sponsorship").
  // kind: "yesno" (radio/select Yes/No), "choice" (best-text-match radio/select), "text" (typed input), "number" (typed number).
  const SCREENING_RULES = [
    { id: "work_auth", patterns: [/authorized\s*to\s*work/i, /legally\s*authorized/i, /\bwork\s*authorization\b/i, /eligible\s*to\s*work\s*in\s*the\s*(us|u\.s\.|united\s*states)/i], valueRef: "work_authorization.authorized_to_work_us", kind: "yesno" },
    { id: "sponsor_now", patterns: [/require\s*sponsorship/i, /need\s*sponsorship/i, /now\s*or\s*in\s*the\s*future.*sponsor/i, /\bvisa\s*sponsor/i], valueRef: "work_authorization.requires_sponsorship", kind: "yesno" },
    { id: "sponsor_future", patterns: [/future.*sponsor/i, /sponsor.*future/i], valueRef: "work_authorization.requires_sponsorship_future", kind: "yesno" },
    { id: "us_citizen", patterns: [/u\.?s\.?\s*citizen/i, /united\s*states\s*citizen/i, /citizenship/i], valueRef: "work_authorization.us_citizen", kind: "yesno" },
    { id: "clearance_current", patterns: [/active\s*(security\s*)?clearance/i, /current\s*(security\s*)?clearance/i, /hold.*clearance/i], valueRef: "work_authorization.security_clearance_current", kind: "yesno" },
    { id: "clearance_eligible", patterns: [/eligible.*clearance/i, /clearance.*eligible/i, /able\s*to\s*obtain.*clearance/i], valueRef: "work_authorization.security_clearance_eligible", kind: "yesno" },
    { id: "relocate", patterns: [/willing\s*to\s*relocate/i, /open\s*to\s*relocat/i, /\brelocat/i], valueRef: "logistics.willing_to_relocate", kind: "yesno" },
    { id: "travel", patterns: [/willing\s*to\s*travel/i, /travel\s*required/i, /comfortable\s*traveling/i], valueRef: "logistics.willing_to_travel", kind: "yesno" },
    { id: "remote", patterns: [/work\s*remote/i, /remote\s*work/i, /remotely/i], valueRef: "logistics.remote_only", kind: "yesno" },
    { id: "start_date", patterns: [/earliest\s*start/i, /available\s*start/i, /when\s*can\s*you\s*start/i, /start\s*date/i, /notice\s*period/i], valueRef: "logistics.available_start_date", kind: "text" },
    { id: "desired_salary_num", patterns: [/desired\s*salary/i, /expected\s*salary/i, /salary\s*expectation/i, /salary\s*requirement/i], valueRef: "compensation.desired_salary_number", kind: "number" },
    { id: "min_salary", patterns: [/minimum\s*salary/i, /salary\s*minimum/i], valueRef: "compensation.minimum_salary_number", kind: "number" },
    { id: "hourly_rate", patterns: [/hourly\s*rate/i, /\$.*hour/i, /rate\s*per\s*hour/i], valueRef: "compensation.hourly_rate_text", kind: "text" },
    { id: "veteran", patterns: [/veteran\s*status/i, /protected\s*veteran/i, /are\s*you\s*a\s*veteran/i], valueRef: "veteran.veteran_status", kind: "choice" },
    { id: "gender", patterns: [/\bgender\b/i], valueRef: "eeo.gender", kind: "choice" },
    { id: "race", patterns: [/race.*ethnicity/i, /ethnicity/i, /\brace\b/i], valueRef: "eeo.race_ethnicity", kind: "choice" },
    { id: "hispanic", patterns: [/hispanic\s*or\s*latino/i, /\bhispanic\b/i, /\blatino\b/i, /\blatinx\b/i], valueRef: "eeo.hispanic_or_latino", kind: "yesno" },
    { id: "disability", patterns: [/disability\s*status/i, /\bdisability\b/i], valueRef: "eeo.disability_status", kind: "choice" },
    { id: "transgender", patterns: [/transgender/i], valueRef: "eeo.transgender", kind: "choice" },
    { id: "felony", patterns: [/felony/i, /convicted/i, /criminal\s*record/i], valueRef: "background.felony_conviction", kind: "yesno" },
    { id: "drug_test", patterns: [/drug\s*test/i, /drug\s*screen/i], valueRef: "background.drug_test_consent", kind: "yesno" },
    { id: "background_check", patterns: [/background\s*check/i], valueRef: "background.background_check_consent", kind: "yesno" },
    { id: "how_heard", patterns: [/how\s*did\s*you\s*hear/i, /how\s*did\s*you\s*find/i, /referral\s*source/i, /source\s*of\s*application/i], valueRef: "source.how_did_you_hear", kind: "choice" },
    { id: "referred_by", patterns: [/who\s*referred/i, /referred\s*by/i, /referrer\s*name/i], valueRef: "source.referred_by", kind: "text" },
    { id: "years_exp", patterns: [/years\s*of\s*experience/i, /how\s*many\s*years/i], valueRef: "experience_flags.years_of_experience", kind: "number" },
    { id: "highest_ed", patterns: [/highest\s*(level\s*of\s*)?education/i, /education\s*level/i], valueRef: "experience_flags.highest_education", kind: "choice" },

    // Physical capability — common in field-tech, warehouse, industrial,
    // delivery, and skilled-trades ATS forms (Pinpoint, JazzHR, Greenhouse).
    { id: "phys_lift", patterns: [/lift\s*(and\s*)?carry/i, /\d+\s*[-\s]?pound\s*(load|weight)/i, /lift.*\d+\s*lbs?/i, /able\s*to\s*lift/i], valueRef: "physical.lift_carry_heavy_loads", kind: "yesno" },
    { id: "phys_climb", patterns: [/climb.*ladder/i, /\d+\s*[-\s]?foot\s*ladder/i, /climb.*a\s*frame/i, /work\s*on\s*ladder/i], valueRef: "physical.climb_ladders", kind: "yesno" },
    { id: "phys_height", patterns: [/elevated\s*height/i, /work.*at\s*height/i, /comfortable.*heights/i, /work.*man[-\s]?lift/i], valueRef: "physical.work_elevated_heights", kind: "yesno" },
    { id: "phys_stand", patterns: [/stand\s*(on\s*your\s*)?(feet|all\s*day)/i, /stand\s*for\s*(extended|long|\d+)/i, /\d+\s*hours?\s*on\s*your\s*feet/i, /prolonged\s*standing/i], valueRef: "physical.stand_long_periods", kind: "yesno" },
    { id: "phys_temp", patterns: [/temperature\s*extremes/i, /hot\s*and\s*cold/i, /extreme\s*(weather|temperatures?|cold|heat)/i, /work\s*outdoors?\s*in\s*all/i], valueRef: "physical.temperature_extremes", kind: "yesno" },
    { id: "phys_industrial", patterns: [/industrial\s*environment/i, /personal\s*protective\s*equipment/i, /\bPPE\b/, /hazardous?\s*conditions?/i, /work.*hazard/i], valueRef: "physical.industrial_environment_ppe", kind: "yesno" },
    { id: "phys_drive_long", patterns: [/operate\s*a\s*motor\s*vehicle/i, /drive\s*for\s*(an\s*)?(extended|long)/i, /driving\s*for\s*\d+/i, /extended\s*periods?\s*of\s*driving/i], valueRef: "physical.drive_extended_periods", kind: "yesno" },
    { id: "phys_air", patterns: [/travel\s*by\s*airplane/i, /\bair\s*travel\b/i, /fly\s*(for\s*work|frequently)/i, /willing.*fly/i], valueRef: "physical.air_travel_extended", kind: "yesno" },

    // Logistics — passport, driver's license, residence state.
    { id: "log_passport", patterns: [/\bpassport\b/i, /ability\s*to\s*acquire.*passport/i], valueRef: "logistics.passport_or_can_acquire", kind: "yesno" },
    { id: "log_license", patterns: [/valid\s*driver/i, /driver['']s?\s*license/i, /commercial\s*driver/i, /\bCDL\b/], valueRef: "logistics.valid_drivers_license", kind: "yesno" },
    { id: "log_states", patterns: [/currently\s*live\s*in.*following\s*states?/i, /reside\s*in\s*one\s*of\s*the\s*following/i, /live\s*in\s*(any\s*of\s*)?(these|the\s*following)\s*states/i], valueRef: "logistics.lives_in_listed_states", kind: "yesno" },
    { id: "log_overnight", patterns: [/overnight\s*travel/i, /open\s*to.*travel.*(overnight|long)/i, /willing.*overnight/i], valueRef: "logistics.open_to_overnight_travel", kind: "yesno" },
    { id: "log_transport", patterns: [/reliable\s*transportation/i, /own\s*transportation/i, /access\s*to.*vehicle/i], valueRef: "logistics.reliable_transportation", kind: "yesno" },
  ];

  function dotted(obj, path) {
    if (!obj || !path) return undefined;
    return path.split(".").reduce((acc, key) => (acc && key in acc ? acc[key] : undefined), obj);
  }

  function visible(el) {
    if (!el || el.disabled) return false;
    if (el.type && el.type !== "radio" && el.type !== "checkbox" && el.readOnly) return false;
    const style = global.getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (el.offsetParent === null && style.position !== "fixed") return false;
    return true;
  }

  function visibleTextInput(el) {
    if (!visible(el)) return false;
    if (el.type && SKIP_TEXT_TYPES.has(el.type.toLowerCase())) return false;
    return true;
  }

  function fieldText(el) {
    const bits = [
      el.name,
      el.id,
      el.getAttribute("aria-label"),
      el.getAttribute("placeholder"),
      el.getAttribute("autocomplete"),
      el.getAttribute("data-field"),
    ];
    const id = el.id;
    if (id) {
      try {
        const label = el.ownerDocument.querySelector(`label[for="${CSS.escape(id)}"]`);
        if (label) bits.push(label.textContent);
      } catch (_e) { /* malformed id */ }
    }
    const wrapLabel = el.closest("label");
    if (wrapLabel) bits.push(wrapLabel.textContent);
    const labelled = el.getAttribute("aria-labelledby");
    if (labelled) {
      labelled.split(/\s+/).forEach((lid) => {
        const n = el.ownerDocument.getElementById(lid);
        if (n) bits.push(n.textContent);
      });
    }
    let parent = el.parentElement;
    for (let i = 0; i < 3 && parent; i += 1) {
      const legend = parent.querySelector("legend");
      if (legend) bits.push(legend.textContent);
      parent = parent.parentElement;
    }
    return bits.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function formatPhoneForField(el, profile) {
    const contact = profile.contact || {};
    const digits = contact.phone_digits || (contact.phone || "").replace(/\D/g, "");
    const formatted = contact.phone || digits;
    if (!digits) return "";
    const maxLen = parseInt(el.getAttribute("maxlength") || "0", 10);
    const inputMode = (el.getAttribute("inputmode") || "").toLowerCase();
    const pattern = el.getAttribute("pattern") || "";
    const typeAttr = (el.type || "").toLowerCase();
    if (typeAttr === "tel" && (inputMode === "numeric" || /^\\?d/.test(pattern))) return digits;
    if (maxLen && maxLen > 0 && maxLen < formatted.length) return digits;
    return formatted;
  }

  function setNativeValue(el, value) {
    if (value == null || value === "") return false;
    const str = String(value);
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "select") {
      const opts = Array.from(el.options || []);
      const lower = str.toLowerCase();
      const match =
        opts.find((o) => (o.value || "").toLowerCase() === lower) ||
        opts.find((o) => (o.textContent || "").trim().toLowerCase() === lower) ||
        opts.find((o) => (o.textContent || "").toLowerCase().includes(lower)) ||
        opts.find((o) => lower.includes((o.textContent || "").trim().toLowerCase()));
      if (match) {
        el.value = match.value;
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }
      return false;
    }
    if (tag === "textarea" || tag === "input") {
      const proto = tag === "textarea" ? global.HTMLTextAreaElement.prototype : global.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, "value");
      if (setter && setter.set) setter.set.call(el, str);
      else el.value = str;
      el.dispatchEvent(new Event("input", { bubbles: true }));
      el.dispatchEvent(new Event("change", { bubbles: true }));
      el.dispatchEvent(new Event("blur", { bubbles: true }));
      return true;
    }
    return false;
  }

  function clickRadioByValue(group, desiredValue) {
    if (!group.length || !desiredValue) return false;
    const lower = String(desiredValue).toLowerCase().trim();
    const candidates = group.map((el) => {
      const labelText = (() => {
        if (el.id) {
          try {
            const lbl = el.ownerDocument.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (lbl) return lbl.textContent;
          } catch (_e) { /* skip */ }
        }
        const wrap = el.closest("label");
        if (wrap) return wrap.textContent;
        const next = el.nextElementSibling;
        if (next && next.tagName === "LABEL") return next.textContent;
        return "";
      })();
      const valText = ((el.value || "") + " " + (labelText || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase().trim();
      return { el, valText };
    });
    let best = candidates.find((c) => c.valText === lower);
    if (!best) best = candidates.find((c) => c.valText.split(/\s+/).includes(lower));
    if (!best) best = candidates.find((c) => c.valText.includes(lower));
    if (!best && /^(yes|no)$/i.test(lower)) {
      best = candidates.find((c) => new RegExp(`\\b${lower}\\b`, "i").test(c.valText));
    }
    if (!best) return false;
    best.el.checked = true;
    best.el.dispatchEvent(new Event("input", { bubbles: true }));
    best.el.dispatchEvent(new Event("change", { bubbles: true }));
    best.el.click();
    return true;
  }

  function scoreField(el, patterns, hay) {
    if (!hay) return 0;
    let score = 0;
    for (const re of patterns) {
      if (re.test(hay)) score += 10;
      if (re.test(el.name || "")) score += 8;
      if (re.test(el.id || "")) score += 8;
    }
    return score;
  }

  function collectTextFields(root) {
    return Array.from(root.querySelectorAll("input, textarea, select")).filter(visibleTextInput);
  }

  function collectAllFormControls(root) {
    return Array.from(root.querySelectorAll("input, textarea, select")).filter(visible);
  }

  function bestField(fields, patterns, used, minScore) {
    let best = null;
    let bestScore = 0;
    for (const el of fields) {
      if (used.has(el)) continue;
      const hay = fieldText(el);
      const s = scoreField(el, patterns, hay);
      if (s > bestScore) {
        bestScore = s;
        best = el;
      }
    }
    return bestScore >= (minScore || 8) ? best : null;
  }

  function fillRuleSet(fields, rules, data, used, report, prefix, profile) {
    for (const rule of rules) {
      let val = data[rule.key];
      if (val == null || val === "") continue;
      const el = bestField(fields, rule.patterns, used, rule.minScore);
      if (!el) continue;
      if (rule.key === "phone" && profile) val = formatPhoneForField(el, profile);
      if (setNativeValue(el, val)) {
        used.add(el);
        report.push(`${prefix}${rule.key}`);
      }
    }
  }

  function sectionRoots(root, keywords) {
    const all = Array.from(root.querySelectorAll("section, fieldset, div, form, li, article"));
    const hits = all.filter((node) => {
      const t = (node.textContent || "").slice(0, 500).toLowerCase();
      return keywords.some((k) => t.includes(k));
    });
    return hits.length ? hits : [root];
  }

  function clickAddButtons(root, count, scopeRegex) {
    const buttons = Array.from(root.querySelectorAll("button, a, input[type=button], input[type=submit], [role=button]"));
    const adders = buttons.filter((el) => {
      const t = ((el.textContent || "") + " " + (el.value || "") + " " + (el.getAttribute("aria-label") || "")).toLowerCase();
      if (!/add|another|new|more/.test(t)) return false;
      return scopeRegex.test(t);
    });
    let clicked = 0;
    for (let i = 0; i < count && i < adders.length; i += 1) {
      adders[i].click();
      clicked += 1;
    }
    return clicked;
  }

  function fillExperience(root, profile, used, report) {
    const sections = sectionRoots(root, ["work experience", "employment", "professional experience", "experience"]);
    const entries = profile.experience || [];
    if (!entries.length) return;
    clickAddButtons(root, Math.max(0, entries.length - 1), /experience|employment|position|job|work/);
    entries.forEach((exp, idx) => {
      const prefix = `experience[${idx}].`;
      for (const section of sections) {
        const fields = collectTextFields(section);
        fillRuleSet(fields, EXP_RULES, exp, used, report, prefix, profile);
      }
      const containers = Array.from(root.querySelectorAll("[class*='experience' i], [id*='experience' i], [data-test*='experience' i]"));
      if (containers[idx]) {
        fillRuleSet(collectTextFields(containers[idx]), EXP_RULES, exp, used, report, prefix, profile);
      }
    });
  }

  function fillEducation(root, profile, used, report) {
    const entries = profile.education || [];
    if (!entries.length) return;
    clickAddButtons(root, Math.max(0, entries.length - 1), /education|school|degree/);
    const sections = sectionRoots(root, ["education", "school", "university", "degree"]);
    entries.forEach((edu, idx) => {
      const prefix = `education[${idx}].`;
      for (const section of sections) {
        fillRuleSet(collectTextFields(section), EDU_RULES, edu, used, report, prefix, profile);
      }
    });
  }

  function fillReferences(root, profile, used, report) {
    const entries = profile.references || [];
    if (!entries.length) return;
    clickAddButtons(root, Math.max(0, entries.length - 1), /reference|referrer/);
    const sections = sectionRoots(root, ["reference", "references", "professional reference", "referrer"]);
    const searchRoots = sections.length ? sections : [root];
    entries.forEach((ref, idx) => {
      const prefix = `references[${idx}].`;
      for (const section of searchRoots) {
        fillRuleSet(collectTextFields(section), REF_RULES, ref, used, report, prefix, profile);
      }
      const containers = Array.from(root.querySelectorAll("[class*='reference' i], [id*='reference' i], [data-test*='reference' i]"));
      if (containers[idx]) {
        fillRuleSet(collectTextFields(containers[idx]), REF_RULES, ref, used, report, prefix, profile);
      }
    });
  }

  function fillContactAndSummary(root, profile, used, report) {
    const contact = profile.contact || {};
    const textFields = collectTextFields(root);
    fillRuleSet(textFields, CONTACT_RULES, contact, used, report, "contact.", profile);

    // Tailored content has highest priority — it's JD-specific and replaces
    // the static profile values for "tools/systems" and summary-style fields.
    const tailored = profile.tailored || {};
    if (tailored.skills_technical) {
      fillRuleSet(
        textFields,
        TAILORED_TOOLS_RULES,
        { skills_technical: tailored.skills_technical },
        used,
        report,
        "tailored.",
        profile,
      );
    }
    if (tailored.summary) {
      fillRuleSet(
        textFields,
        TAILORED_SUMMARY_RULES,
        { summary: tailored.summary },
        used,
        report,
        "tailored.",
        profile,
      );
    }

    // Static profile summary fills any summary-style field the tailored
    // pass didn't already claim.
    if (profile.summary) {
      fillRuleSet(textFields, SUMMARY_RULES, { summary: profile.summary }, used, report, "", profile);
    }
    if (profile.cover_letter) {
      fillRuleSet(textFields, COVER_RULES, { cover_letter: profile.cover_letter }, used, report, "", profile);
    }
  }

  // Identify a "question container" — a fieldset, role=group/radiogroup, or div with a legend/heading + radios.
  function questionContainers(root) {
    const seen = new Set();
    const results = [];
    const candidates = Array.from(root.querySelectorAll(
      "fieldset, [role='radiogroup'], [role='group'], div, section"
    ));
    for (const node of candidates) {
      if (seen.has(node)) continue;
      const radios = node.querySelectorAll("input[type=radio]");
      const selects = node.querySelectorAll("select");
      const checkboxes = node.querySelectorAll("input[type=checkbox]");
      const hasControl = radios.length > 0 || selects.length === 1 || checkboxes.length > 0;
      if (!hasControl) continue;
      // For radios: only keep the smallest containing fieldset/group.
      if (radios.length > 0) {
        const inner = Array.from(node.querySelectorAll("fieldset, [role='radiogroup']")).find(
          (n) => n !== node && n.contains(radios[0])
        );
        if (inner) continue;
      }
      results.push(node);
      seen.add(node);
    }
    return results;
  }

  function questionText(container) {
    const bits = [];
    const legend = container.querySelector("legend");
    if (legend) bits.push(legend.textContent);
    const heading = container.querySelector("h1, h2, h3, h4, h5, h6");
    if (heading) bits.push(heading.textContent);
    const aria = container.getAttribute("aria-label");
    if (aria) bits.push(aria);
    const labelled = container.getAttribute("aria-labelledby");
    if (labelled) {
      labelled.split(/\s+/).forEach((lid) => {
        const n = container.ownerDocument.getElementById(lid);
        if (n) bits.push(n.textContent);
      });
    }
    // Fallback: first ~200 chars of container text (excluding option labels).
    if (!bits.length) {
      const txt = (container.textContent || "").slice(0, 200);
      bits.push(txt);
    }
    return bits.filter(Boolean).join(" ").replace(/\s+/g, " ").trim();
  }

  function matchScreeningRule(qText) {
    if (!qText) return null;
    let best = null;
    let bestHits = 0;
    for (const rule of SCREENING_RULES) {
      let hits = 0;
      for (const pat of rule.patterns) {
        if (pat.test(qText)) hits += 1;
      }
      if (hits > bestHits) {
        bestHits = hits;
        best = rule;
      }
    }
    return best;
  }

  function fillScreening(root, profile, used, report) {
    const screening = profile.screening || {};
    if (!Object.keys(screening).length) return;
    const containers = questionContainers(root);
    for (const container of containers) {
      const qText = questionText(container);
      const rule = matchScreeningRule(qText);
      if (!rule) continue;
      const answer = dotted(screening, rule.valueRef);
      if (answer == null || answer === "") continue;

      const radios = Array.from(container.querySelectorAll("input[type=radio]")).filter((el) => visible(el) && !used.has(el));
      if (radios.length) {
        if (clickRadioByValue(radios, answer)) {
          radios.forEach((r) => used.add(r));
          report.push(`screening.${rule.id}`);
          continue;
        }
      }
      const selects = Array.from(container.querySelectorAll("select")).filter((el) => visible(el) && !used.has(el));
      if (selects.length === 1) {
        if (setNativeValue(selects[0], answer)) {
          used.add(selects[0]);
          report.push(`screening.${rule.id}`);
          continue;
        }
      }
      const checkboxes = Array.from(container.querySelectorAll("input[type=checkbox]")).filter((el) => visible(el) && !used.has(el));
      if (checkboxes.length === 1 && /^(yes|true|1)$/i.test(String(answer))) {
        if (!checkboxes[0].checked) checkboxes[0].click();
        used.add(checkboxes[0]);
        report.push(`screening.${rule.id}`);
        continue;
      }
      // Text/number fallback inside the question container.
      const textEls = collectTextFields(container).filter((el) => !used.has(el));
      if (textEls.length === 1) {
        if (setNativeValue(textEls[0], answer)) {
          used.add(textEls[0]);
          report.push(`screening.${rule.id}`);
        }
      }
    }
  }

  // -------------------------------------------------------------------
  // Account-creation autofill (password + security Q&A).
  //
  // ATS sites force users to create an account before applying. The
  // create-account form is its own pain (password rules, security
  // questions, confirm-password). This block detects that page and
  // fills it from profile.ats_account so the user only has to click
  // Submit + verify their email.
  // -------------------------------------------------------------------

  // Detects whether the current root looks like an account-creation form.
  // Conservative: 2+ visible password inputs is a near-certain signal;
  // 1 password + a signup keyword in the page text covers single-pw flows
  // like Workday step 1.
  function isAccountCreateContext(root) {
    const doc = root.ownerDocument || (root.documentElement ? root : null);
    const pws = Array.from((doc || root).querySelectorAll('input[type="password"]')).filter(visible);
    if (pws.length >= 2) return true;
    if (pws.length === 1) {
      const body = (doc && doc.body) ? doc.body.textContent : (root.textContent || "");
      const text = (body || "").slice(0, 10000).toLowerCase();
      return /create\s+(an?\s+|your\s+)?(account|profile|login)|sign\s*up\b|register\b|new\s+(account|user)\b/.test(text);
    }
    return false;
  }

  // Heuristically detect <select> dropdowns that list security questions
  // (rather than, say, country or state). A real security-question dropdown
  // mentions multiple personal-history topics in its options.
  const _SQ_INDICATORS = [
    "pet", "maiden", "born", "school", "teacher", "favorite color",
    "first car", "street", "grandmother", "grandfather", "best friend",
    "middle name", "wedding", "graduate", "favorite food", "favorite movie",
  ];
  function findSecurityQuestionSelects(root) {
    return Array.from(root.querySelectorAll("select")).filter((sel) => {
      if (!visible(sel)) return false;
      const optsText = Array.from(sel.options || [])
        .map((o) => (o.textContent || "").toLowerCase())
        .join(" ");
      let hits = 0;
      for (const ind of _SQ_INDICATORS) if (optsText.includes(ind)) hits += 1;
      return hits >= 2;
    });
  }

  function matchSecurityAnswer(question, qaList, fallback) {
    const q = String(question || "").toLowerCase();
    if (!q) return fallback || "";
    for (const entry of qaList || []) {
      const kws = (entry && entry.keywords) || [];
      for (const kw of kws) {
        if (kw && q.includes(String(kw).toLowerCase())) {
          return entry.answer || "";
        }
      }
    }
    return fallback || "";
  }

  function findAnswerInputForQuestionSelect(sel, used) {
    let parent = sel.parentElement;
    for (let i = 0; i < 4 && parent; i += 1) {
      const inputs = parent.querySelectorAll('input[type="text"], input:not([type]), input[type="search"]');
      for (const inp of inputs) {
        if (!visible(inp) || used.has(inp)) continue;
        const hay = fieldText(inp).toLowerCase();
        if (/answer|response/.test(hay)) return inp;
      }
      parent = parent.parentElement;
    }
    let next = sel.nextElementSibling;
    while (next) {
      const inps = next.querySelectorAll('input[type="text"], input:not([type]), input[type="search"]');
      for (const inp of inps) {
        if (visible(inp) && !used.has(inp)) return inp;
      }
      next = next.nextElementSibling;
    }
    return null;
  }

  function fillAccountCreation(root, profile, used, report) {
    const acct = profile && profile.ats_account;
    if (!acct || !acct.password) return;
    if (!isAccountCreateContext(root)) return;

    // Password + confirm-password fields. Fill every visible password input
    // we can find — confirm-password is just the password again, and ATS
    // confirm-password fields don't typically have a distinguishing name we
    // can use to do anything different.
    const pws = Array.from(root.querySelectorAll('input[type="password"]'))
      .filter((el) => visible(el) && !used.has(el));
    for (const pw of pws) {
      if (setNativeValue(pw, acct.password)) {
        used.add(pw);
        report.push("ats_account.password");
      }
    }

    // Email-confirm fields (Workday has "Email Address" + "Verify Email").
    // Find any input with name/label suggesting verify/confirm + email and
    // fill it with the contact email so it matches.
    const emailVal = (profile.contact && profile.contact.email) || "";
    if (emailVal) {
      const verifyEmails = Array.from(root.querySelectorAll('input'))
        .filter((el) => visible(el) && !used.has(el))
        .filter((el) => {
          const hay = fieldText(el).toLowerCase();
          return /(verify|confirm|retype|re-?enter).*email|email.*(verify|confirm|retype|re-?enter)/.test(hay);
        });
      for (const el of verifyEmails) {
        if (setNativeValue(el, emailVal)) {
          used.add(el);
          report.push("ats_account.verify_email");
        }
      }
    }

    // Security question selects + their answer inputs.
    //
    // Workday and many other ATSes require DIFFERENT questions across the
    // 2-3 security slots AND reject duplicate answers. So we track which
    // qaList entries we've already consumed and skip them on subsequent
    // selects. If we run out of matched entries we fall back to picking a
    // distinct first-available option per slot with a placeholder answer.
    const qaList = acct.security_qa || [];
    const fallback =
      acct.default_security_answer ||
      (qaList[0] && qaList[0].answer) ||
      "Philadelphia";
    const consumedAnswers = new Set();
    const consumedOptValues = new Set();
    const selects = findSecurityQuestionSelects(root);
    for (const sel of selects) {
      if (used.has(sel)) continue;

      let chosenOpt = null;
      let chosenAnswer = "";

      // Walk options in DOM order; pick the first option whose question text
      // maps to an answer we haven't already used.
      for (const opt of sel.options || []) {
        const optVal = (opt.value || "").trim();
        if (!optVal) continue;
        if (consumedOptValues.has(optVal)) continue;
        const ans = matchSecurityAnswer(opt.textContent || "", qaList, "");
        if (ans && !consumedAnswers.has(ans)) {
          chosenOpt = opt;
          chosenAnswer = ans;
          break;
        }
      }

      // Fallback: first unused non-empty option, generated unique answer
      // so the ATS doesn't reject for duplication.
      if (!chosenOpt) {
        for (const opt of sel.options || []) {
          const optVal = (opt.value || "").trim();
          if (!optVal) continue;
          if (consumedOptValues.has(optVal)) continue;
          chosenOpt = opt;
          break;
        }
        // Ensure unique answer (append index suffix if needed).
        let candidate = fallback;
        let suffix = 1;
        while (consumedAnswers.has(candidate)) {
          candidate = `${fallback}${suffix}`;
          suffix += 1;
        }
        chosenAnswer = candidate;
      }

      if (chosenOpt) {
        sel.value = chosenOpt.value;
        sel.dispatchEvent(new Event("input", { bubbles: true }));
        sel.dispatchEvent(new Event("change", { bubbles: true }));
        used.add(sel);
        consumedOptValues.add((chosenOpt.value || "").trim());
        report.push("ats_account.security_question");

        const ansInput = findAnswerInputForQuestionSelect(sel, used);
        if (ansInput && setNativeValue(ansInput, chosenAnswer)) {
          used.add(ansInput);
          consumedAnswers.add(chosenAnswer);
          report.push("ats_account.security_answer");
        }
      }
    }

    // Bare "security answer" text inputs not paired with a select dropdown
    // (some ATSes prompt a single fixed question with a free-text answer).
    const answerInputs = Array.from(
      root.querySelectorAll('input[type="text"], input:not([type]), input[type="search"]'),
    ).filter((el) => {
      if (!visible(el) || used.has(el)) return false;
      const hay = fieldText(el).toLowerCase();
      return /security\s*(answer|response)|secret\s*answer|^answer$/.test(hay);
    });
    for (const inp of answerInputs) {
      if (setNativeValue(inp, fallback)) {
        used.add(inp);
        report.push("ats_account.security_answer");
      }
    }
  }

  function fillKnownAtsNames(root, profile, used, report) {
    const c = profile.contact || {};
    const phoneForFn = (el) => formatPhoneForField(el, profile);
    const map = {
      // Greenhouse (legacy + modern React forms)
      "job_application[first_name]": c.first_name,
      "job_application[last_name]": c.last_name,
      "job_application[email]": c.email,
      "job_application[phone]": (el) => phoneForFn(el),
      "first_name": c.first_name,
      "last_name": c.last_name,
      // Lever
      "name": c.full_name,
      "email": c.email,
      "phone": (el) => phoneForFn(el),
      "org": (profile.experience && profile.experience[0]?.company) || "",
      "urls[LinkedIn]": c.linkedin,
      "urls[GitHub]": c.github,
      "urls[Portfolio]": c.website,
      // Ashby
      "_systemfield_name": c.full_name,
      "_systemfield_email": c.email,
      "_systemfield_phoneNumber": (el) => phoneForFn(el),
      // Workable
      "candidate[firstname]": c.first_name,
      "candidate[lastname]": c.last_name,
      "candidate[email]": c.email,
      "candidate[phone]": (el) => phoneForFn(el),
    };
    Object.entries(map).forEach(([name, raw]) => {
      const el = root.querySelector(`[name="${name}"]`);
      if (!el || !visible(el) || used.has(el)) return;
      const val = typeof raw === "function" ? raw(el) : raw;
      if (!val) return;
      if (setNativeValue(el, val)) {
        used.add(el);
        report.push(`ats.${name}`);
      }
    });
  }

  // Detect file inputs that look like resume / CV uploads. Browsers block
  // extensions from setting a file input from disk, so the next-best UX is
  // (1) finding them, (2) highlighting them so the user sees where to attach,
  // and (3) reporting them so the popup can show the right filename.
  //
  // Note: file inputs are commonly visually hidden behind styled triggers
  // (JazzHR's "Attach resume" link, Greenhouse's drop zone). We DO NOT apply
  // the standard offsetParent visibility check here — instead we accept any
  // non-disabled input[type=file] whose label/name/id suggests "resume".
  function findResumeFileInputs(root) {
    const inputs = Array.from(root.querySelectorAll("input[type=file]")).filter(
      (el) => el && !el.disabled
    );
    const matched = [];
    for (const el of inputs) {
      const hay = fieldText(el).toLowerCase();
      const accept = (el.getAttribute("accept") || "").toLowerCase();
      const looksLikeResume =
        /\b(resume|c\.?v\.?|curriculum)\b/.test(hay) ||
        /\battach\s*(resume|cv)\b/.test(hay) ||
        /\bupload\s*(your\s*)?(resume|cv)\b/.test(hay) ||
        /resumator/.test(el.name || "") ||
        /resumator/.test(el.id || "");
      const acceptsPdfOnly = accept.includes("pdf") || accept.includes("doc");
      if (looksLikeResume || (acceptsPdfOnly && hay.includes("attach"))) {
        matched.push({
          el,
          label: hay.slice(0, 80) || (el.id || el.name || "(unlabeled file input)"),
          accept,
        });
      }
    }
    return matched;
  }

  function highlightResumeField(el) {
    if (!el) return false;
    // The actual input is often invisible; highlight whatever VISIBLE
    // container the user can see — usually the closest label / parent block.
    let target = el;
    let parent = el.parentElement;
    for (let i = 0; i < 5 && parent; i += 1) {
      if (parent.offsetParent !== null) {
        target = parent;
        break;
      }
      parent = parent.parentElement;
    }
    const prev = target.style.outline;
    const prevBox = target.style.boxShadow;
    target.style.outline = "3px solid #f59e0b";
    target.style.outlineOffset = "3px";
    target.style.boxShadow = "0 0 0 6px rgba(245, 158, 11, 0.25)";
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    setTimeout(() => {
      target.style.outline = prev;
      target.style.boxShadow = prevBox;
    }, 6000);
    return true;
  }

  // Expose a highlight helper the popup can call via runtime message.
  global.__JPA_highlightResumeField = function () {
    const matched = findResumeFileInputs(document);
    if (matched.length) {
      highlightResumeField(matched[0].el);
      return { ok: true, count: matched.length, label: matched[0].label };
    }
    return { ok: false, error: "no_resume_file_input_found" };
  };

  function fillProfile(profile, root) {
    const used = new Set();
    const filled = [];
    if (!profile || typeof profile !== "object") {
      return { filled, error: "missing_profile" };
    }
    fillContactAndSummary(root, profile, used, filled);
    // Account-creation runs BEFORE the experience/education/references pass
    // because those passes have overly-broad email/name patterns that, on
    // pages without their dedicated section, can hijack verify-email and
    // similar account-create fields. Claiming password / verify-email /
    // security inputs first prevents that.
    fillAccountCreation(root, profile, used, filled);
    fillScreening(root, profile, used, filled);
    fillExperience(root, profile, used, filled);
    fillEducation(root, profile, used, filled);
    fillReferences(root, profile, used, filled);
    fillKnownAtsNames(root, profile, used, filled);

    // Surface file inputs needing manual user action. We don't try to set
    // them — browser security forbids it — but we report them so the popup
    // can tell the user which resume filename to attach.
    const resumeInputs = findResumeFileInputs(root).map((m) => ({
      label: m.label,
      accept: m.accept,
    }));
    return {
      filled,
      count: filled.length,
      resume_file_inputs: resumeInputs,
      is_account_create: isAccountCreateContext(root),
      page_url: (root.defaultView || global).location ? (root.defaultView || global).location.href : "",
    };
  }

  global.JobPipelineAutofill = {
    fillProfile,
    setNativeValue,
    fieldText,
    matchScreeningRule,
    questionContainers,
    isAccountCreateContext,
    findSecurityQuestionSelects,
  };
})(typeof window !== "undefined" ? window : self);
