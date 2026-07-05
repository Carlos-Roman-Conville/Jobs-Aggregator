# A Learning Roadmap for Better Vibe Coding

*Built for: a non-coder learning C to understand systems well enough to direct AI better.*
*Date: May 2026*

---

## The thing you actually discovered

You noticed that **granularity is discovered, not inherent** — you keep finding small requirements (grammar checks, validations, conventions) that you didn't know to specify until something went wrong.

That isn't a beginner mistake. It is the single oldest, most studied problem in software engineering. It has names, and learning the names lets you front-load the work instead of rediscovering it each time:

- **The specification gap / requirements elicitation.** You can't fully state what you want up front; requirements *emerge* through building. Professional teams budget for this rather than expecting to nail it in one shot.
- **Tacit knowledge** (Michael Polanyi: *"we know more than we can tell"*). An experienced dev carries thousands of unspoken expectations — like "of course it validates empty input." The AI has none of yours unless you say them. You only *notice* a tacit rule when it's broken (your grammar-check moment).
- **The happy-path fallacy.** Beginners specify the success case. Most real software is the *unhappy* paths: empty input, huge input, malformed input, failures, permissions, weird formats. Experts spend most of their effort there.
- **A program is its own only complete specification.** Anything shorter than the finished code leaves gaps, and *someone* fills those gaps with assumptions. With vibe coding, that someone is the AI. The skill is closing the gaps *that matter* before the AI guesses.

The current professional answer to your exact problem even has a buzzword: **Spec-Driven Development (SDD)**. Andrej Karpathy, who coined "vibe coding" in Feb 2025, said a year later the pure-vibe era is giving way to *agentic engineering* — orchestrating AI against **detailed specs** with human oversight. You arrived at the frontier on your own.

---

## The reframe

You're not "missing things." You're doing **requirements engineering**, and there are two skills to build:

1. **Make the implicit explicit faster** — learn the *categories* of requirements that almost always matter, so you scan for them up front (a mental checklist).
2. **Capture it so you never rediscover it** — turn each thing you learn into a reusable rule in a project rules file (`CLAUDE.md`, Cursor rules, `AGENTS.md`). Your hard-won grammar check becomes a permanent line, not a recurring surprise.

---

## The "what am I missing?" checklist

Before you start a feature, run down this list and decide *explicitly* on each — even if the decision is "don't care." These are the things that are almost always implicit:

- **Inputs & edge cases** — empty, null, huge, duplicate, malformed, wrong type. What happens for each?
- **Error & failure behavior** — when something breaks, does it crash, retry, log, show a message? Which?
- **Output format & style** — formatting, tone, grammar, units, rounding, sort order, naming conventions.
- **Definition of done** — concrete acceptance criteria. "Done" means *what, exactly?*
- **Non-goals & constraints** — what it should *not* do; libraries it must *not* use; things to leave alone.
- **State & persistence** — what's saved, where, what survives a restart.
- **Security & secrets** — auth, input sanitization, where keys live.
- **Scale & performance assumptions** — 10 items or 10 million? One user or many?
- **Dependencies** — what it's allowed to depend on, and what versions.

Writing tests or acceptance criteria *first* is the cheat code: it forces you to enumerate the granular expectations before any code exists.

---

## Resources, tiered

### Start here (directly about your problem)

- **Addy Osmani — *Beyond Vibe Coding: From Coder to AI-Era Developer* (O'Reilly, 2025).** The single best fit. It's literally about formulating clear goals/constraints, reviewing AI output critically, and "context engineering" (always assume the AI knows nothing about your project — give it architecture notes, snippets, exact errors). Free to read online: https://beyond.addy.ie/
- **DeepLearning.AI — *Claude Code: A Highly Agentic Coding Assistant* (free short course).** By Andrew Ng + Anthropic's Elie Schoppik. Practical: how to give clear context, point to the right files, describe features precisely, extend with tools. https://learn.deeplearning.ai/courses/claude-code-a-highly-agentic-coding-assistant/
- **Anthropic's own free courses** — *Claude Code 101* and *Claude Code in Action*: https://anthropic.skilljar.com/

### Deepen the craft (vibe coding done seriously)

- **Gene Kim & Steve Yegge — *Vibe Coding: Building Production-Grade Software with GenAI, Chat, Agents, and Beyond* (IT Revolution, Oct 2025).** Won the 2026 Axiom Gold. The "how do real teams ship this responsibly" book. https://itrevolution.com/product/vibe-coding-book/
- **Coursera — *Vibe Coding Essentials: Build Apps with AI* (by Scrimba).** Built for complete beginners; you build something fast.
- **Zero To Mastery — *The Vibe Coding Bootcamp* (158 lessons, ~18 hrs, paid).** The most thorough end-to-end option; 10+ projects across Cursor, Copilot, Claude, etc. https://zerotomastery.io/courses/learn-vibe-coding/

### Timeless software wisdom (this is where the real edge is)

- **Andy Hunt & Dave Thomas — *The Pragmatic Programmer*.** The mindset of good software: orthogonality, "tracer bullets," good-enough software, DRY. Ages perfectly.
- **Atul Gawande — *The Checklist Manifesto*.** Not a coding book — it's about turning discovered, expert knowledge into reusable checklists. Almost a manual for your exact realization.
- **Karl Wiegers — *Software Requirements*.** The bible of "how do you figure out what to actually build." Heavier, but it *is* your problem in book form.

### For your systems / C track (understand the machine you're directing)

- **Harvard CS50 (free).** Starts with C and builds your model of how computers actually work. The best on-ramp for a non-coder who wants real foundations.
- **Charles Petzold — *Code: The Hidden Language of Computer Hardware and Software*.** Builds a working computer from light switches up. Perfect companion to learning C.
- **Nand2Tetris — *Build a Modern Computer from First Principles* (Coursera / book *The Elements of Computing Systems*).** You construct a whole computer from NAND gates to a working program. Unbeatable for "how systems work."
- **Ben Eater (YouTube).** Builds an 8-bit computer on breadboards. Hands-on, mesmerizing, deepens intuition.

### Stay current (this field moves monthly)

- Anthropic's docs: prompt engineering + Claude Code best practices (docs.claude.com).
- Simon Willison's blog (simonwillison.net) — sharp, current writing on AI-assisted dev.
- Communities: r/ChatGPTCoding, Cursor / Claude Discords for tool-specific tips.

---

## A suggested order

1. **Now:** Read Addy Osmani's *Beyond Vibe Coding* (free) + take the DeepLearning.AI Claude Code course. These attack the granularity problem head-on.
2. **In parallel:** Keep going on C, and add CS50 + Petzold's *Code* so your mental model of the machine gets richer — that's what lets you spot what the AI is glossing over.
3. **Build the habit:** Start a `CLAUDE.md` / rules file for every project. Every time you discover a new "I shouldn't have had to say that" requirement, add a line. Run the checklist above before each feature.
4. **Later:** *The Pragmatic Programmer* + *The Checklist Manifesto* to mature the instinct, and the Gene Kim/Yegge book when you want production-grade discipline.

The goal isn't to memorize everything up front — it's to shrink the gap between "what I meant" and "what I said," and to make sure you only discover each missing requirement *once*.
