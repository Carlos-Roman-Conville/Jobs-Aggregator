/* eslint-disable no-console */
// Smoke test for the account-creation fill path in fill_engine.js.
// Run: node browser_extension/test_account_create.js
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const ENGINE = fs.readFileSync(
  path.join(__dirname, "fill_engine.js"),
  "utf8",
);
const PROFILE = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, "..", "job_pipeline", "autofill_profile.json"),
    "utf8",
  ),
);

function run(name, html, expect) {
  const dom = new JSDOM(html, {
    url: "https://example.workday.com/createAccount",
    runScripts: "outside-only",
  });
  const { window } = dom;
  // Ensure both global aliases exist for the IIFE's `(window||self)` guard.
  window.self = window;
  // jsdom doesn't compute offsetParent for static layouts the way Chrome does.
  // Force visibility for the test by stubbing offsetParent to return body for
  // every element that's not display:none / visibility:hidden.
  Object.defineProperty(window.HTMLElement.prototype, "offsetParent", {
    get() {
      const style = window.getComputedStyle(this);
      if (style.display === "none" || style.visibility === "hidden") return null;
      return this.parentNode;
    },
    configurable: true,
  });
  window.eval(ENGINE);
  const api = window.JobPipelineAutofill;
  const isCreate = api.isAccountCreateContext(window.document);
  const result = api.fillProfile(PROFILE, window.document);

  console.log(`\n=== ${name} ===`);
  console.log(`  isAccountCreate: ${isCreate}`);
  console.log(`  filled: ${result.filled.join(", ") || "(none)"}`);

  let pass = true;
  for (const exp of expect.contains || []) {
    if (!result.filled.includes(exp)) {
      console.log(`  ✗ missing: ${exp}`);
      pass = false;
    }
  }
  for (const exp of expect.not || []) {
    if (result.filled.includes(exp)) {
      console.log(`  ✗ should-not-have: ${exp}`);
      pass = false;
    }
  }
  if (expect.is_account_create !== undefined && isCreate !== expect.is_account_create) {
    console.log(`  ✗ expected isAccountCreate=${expect.is_account_create}, got ${isCreate}`);
    pass = false;
  }
  // Inspect actual field values for password / security_qa
  // Validate that multi-select security questions chose DIFFERENT options
  // (Workday rejects duplicate answers across questions).
  if (expect.unique_security_questions) {
    const selects = Array.from(window.document.querySelectorAll("select"))
      .filter((s) => s.options && Array.from(s.options).some((o) => /pet|maiden|color|born|school/.test((o.textContent || "").toLowerCase())));
    const picked = selects.map((s) => (s.options[s.selectedIndex] || {}).value);
    const answers = Array.from(window.document.querySelectorAll('input[name*="securityAnswer" i]')).map((i) => i.value);
    const uniqQ = new Set(picked.filter(Boolean));
    const uniqA = new Set(answers.filter(Boolean));
    if (selects.length >= 2 && (uniqQ.size < selects.length || uniqA.size < answers.length)) {
      console.log(`  ✗ duplicate security Q/A picked. questions=${picked.join("|")} answers=${answers.join("|")}`);
      pass = false;
    } else {
      console.log(`  ✓ ${selects.length} distinct security questions+answers picked`);
    }
  }
  if (expect.password_count !== undefined) {
    const pws = Array.from(window.document.querySelectorAll('input[type="password"]'));
    const filled = pws.filter((p) => p.value).length;
    if (filled !== expect.password_count) {
      console.log(`  ✗ expected ${expect.password_count} pw inputs filled, got ${filled}`);
      pass = false;
    } else {
      console.log(`  ✓ ${filled}/${pws.length} password inputs filled with the shared password`);
    }
  }
  console.log(`  ${pass ? "PASS" : "FAIL"}`);
  return pass;
}

const TESTS = [
  // Workday-style: email + verify email + pw + verify pw + 3 security Q/A pairs
  {
    name: "Workday account-create",
    html: `<html><body>
      <form>
        <h1>Create Account</h1>
        <label>Email Address<input type="email" id="email1" name="email"></label>
        <label>Verify Email Address<input type="email" id="email2" name="verifyEmail"></label>
        <label>Password<input type="password" id="pw1" name="password"></label>
        <label>Verify New Password<input type="password" id="pw2" name="verifyPassword"></label>

        <fieldset>
          <legend>Security Question 1</legend>
          <select id="sq1" name="securityQuestion1">
            <option value="">--Select--</option>
            <option value="pet">What is the name of your first pet?</option>
            <option value="city">What city were you born in?</option>
            <option value="school">What was the name of your elementary school?</option>
            <option value="color">What is your favorite color?</option>
            <option value="maiden">What is your mother's maiden name?</option>
          </select>
          <label>Answer<input type="text" id="sa1" name="securityAnswer1"></label>
        </fieldset>

        <fieldset>
          <legend>Security Question 2</legend>
          <select id="sq2" name="securityQuestion2">
            <option value="">--Select--</option>
            <option value="pet">What is the name of your first pet?</option>
            <option value="city">What city were you born in?</option>
            <option value="school">What was the name of your elementary school?</option>
            <option value="color">What is your favorite color?</option>
            <option value="maiden">What is your mother's maiden name?</option>
          </select>
          <label>Answer<input type="text" id="sa2" name="securityAnswer2"></label>
        </fieldset>
      </form>
    </body></html>`,
    expect: {
      is_account_create: true,
      contains: [
        "ats_account.password",
        "ats_account.verify_email",
        "ats_account.security_question",
        "ats_account.security_answer",
      ],
      password_count: 2,
      unique_security_questions: true,
    },
  },
  // Greenhouse/Lever-style: just email + password + confirm
  {
    name: "Simple signup (password + confirm)",
    html: `<html><body>
      <form>
        <h1>Sign Up</h1>
        <label>Email<input type="email" name="email"></label>
        <label>Password<input type="password" name="password"></label>
        <label>Confirm Password<input type="password" name="confirm_password"></label>
        <button type="submit">Create Account</button>
      </form>
    </body></html>`,
    expect: {
      is_account_create: true,
      contains: ["ats_account.password"],
      password_count: 2,
    },
  },
  // Single password on a LOGIN page — should NOT fill.
  {
    name: "Login page (single password, no signup wording)",
    html: `<html><body>
      <form>
        <h1>Sign In</h1>
        <label>Email<input type="email" name="email"></label>
        <label>Password<input type="password" name="password"></label>
        <button type="submit">Log In</button>
      </form>
    </body></html>`,
    expect: {
      is_account_create: false,
      not: ["ats_account.password"],
      password_count: 0,
    },
  },
  // Single password + sign-up wording — should fill.
  {
    name: "Single-password signup (sign up wording present)",
    html: `<html><body>
      <form>
        <h1>Create your account</h1>
        <label>Email<input type="email" name="email"></label>
        <label>Choose a password<input type="password" name="password"></label>
        <button type="submit">Register</button>
      </form>
    </body></html>`,
    expect: {
      is_account_create: true,
      contains: ["ats_account.password"],
      password_count: 1,
    },
  },
  // Bare "Security Answer" text input, no select.
  {
    name: "Fixed-question security answer (no dropdown)",
    html: `<html><body>
      <form>
        <h1>Create Account</h1>
        <label>Email<input type="email" name="email"></label>
        <label>Password<input type="password" name="password"></label>
        <label>Confirm Password<input type="password" name="confirm"></label>
        <p>Pick a memorable answer to a security question:</p>
        <label>Security Answer<input type="text" name="security_answer"></label>
      </form>
    </body></html>`,
    expect: {
      is_account_create: true,
      contains: ["ats_account.password", "ats_account.security_answer"],
      password_count: 2,
    },
  },
];

let allPass = true;
for (const t of TESTS) {
  if (!run(t.name, t.html, t.expect)) allPass = false;
}
console.log("\n" + (allPass ? "ALL PASS" : "FAILURES — see above"));
process.exit(allPass ? 0 : 1);
