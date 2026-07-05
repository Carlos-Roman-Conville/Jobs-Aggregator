# System Optimization Master Reference

Generated: May 16, 2026

This document consolidates all decisions from the system review session — software cleanup, security stack, install tally, and desktop organization plan.

---

## 1. Install Tally — Programs to Download

### Adobe Alternatives (creative stack)

| Program | Purpose | Source |
|---------|---------|--------|
| Krita | Digital painting (better than GIMP for this) | krita.org |
| Darktable | Photo library + RAW processing (Lightroom replacement) | darktable.org |
| Figma | UI/web design (browser or desktop app) | figma.com |
| Kdenlive | Lighter video editor (optional if DaVinci installed) | kdenlive.org |
| Scribus | Page layout, publishing (InDesign replacement) | scribus.net |
| XnViewMP | Image browser/organizer (Bridge replacement) | xnview.com |

### System Cleanup / Replacements

| Program | Replaces | Source |
|---------|----------|--------|
| DaVinci Resolve (full app) | — (only had control panels) | blackmagicdesign.com |
| Audacity | — (was missing) | audacityteam.org (NOT Muse Hub) |
| AdwCleaner | Malwarebytes (less nag) | malwarebytes.com/adwcleaner |
| yt-dlp | 4K Video Downloader | github.com/yt-dlp/yt-dlp |
| Stacher | (yt-dlp GUI, optional) | stacher.io |
| Mod Organizer 2 | Vortex (Bethesda modding) | nexusmods.com (optional) |
| ExifTool | — (companion to ExifCleaner) | exiftool.org |
| Sysinternals Suite | — (Process Explorer, Autoruns, etc.) | learn.microsoft.com/sysinternals |
| BCUninstaller | Built-in uninstaller (for stuck removals) | github.com/Klocman/Bulk-Crap-Uninstaller |

### Security Stack — Core

| Program | Purpose | Source |
|---------|---------|--------|
| Firefox | Daily-driver browser (full uBO support) | mozilla.org |
| Brave | Risky-only browser (Sandboxie-launched only) | brave.com |
| uBlock Origin | Ad/malware blocker (Raymond Hill / gorhill ONLY) | Firefox Add-ons |
| Firefox Multi-Account Containers | Cookie/context separation | Firefox Add-ons |
| Bitwarden | Password manager | bitwarden.com |
| NextDNS | DNS-level filtering (use official Windows app) | nextdns.io |
| Sandboxie Plus (Personal) | Browser sandboxing | github.com/sandboxie-plus/Sandboxie |
| Windows Sandbox | One-off file checks (Win Pro/Enterprise/Education only) | Enable in Windows Features |
| VirtualBox | Home-edition substitute for Windows Sandbox; full VM isolation | Already installed (Oracle VirtualBox 7.1.4) |
| Veeam Agent for MS Windows Free | System imaging backup | veeam.com (requires free account) |

**Note on Windows Sandbox vs VirtualBox:** Windows Sandbox requires Windows 11 Pro/Enterprise/Education SKU. If on Home edition, use the already-installed VirtualBox to create a disposable Windows VM instead. VirtualBox setup is heavier (needs Windows ISO, initial install) but gives stronger isolation and works on any Windows edition. Use Sandboxie Plus for everyday risky-browser sessions regardless of SKU — it's lighter weight than either Sandbox option.

### Security Stack — Bookmarks (not installs)

| Service | Purpose | URL |
|---------|---------|-----|
| VirusTotal | First-check file scanning (70+ engines) | virustotal.com |
| Any.Run | Cloud sandbox behavior analysis | any.run |
| Hybrid Analysis | Alternative cloud sandbox | hybrid-analysis.com |

**Critical rule:** NEVER upload private, work, financial, or sensitive files to public sandboxes. Those go through Windows Sandbox or local VM only.

### Explicitly Rejected / Deferred

- **Malwarebytes** — dropped (nag screens, prompt fatigue)
- **Privacy Badger** — dropped (EFF maintenance slowdown, redundant with uBO)
- **OSArmor** — declined (prompt fatigue risk)
- **Macrium Reflect Free** — discontinued for new users, Veeam used instead
- **Norton 360** — being removed
- **YubiKey (hardware 2FA)** — deferred until baseline stable
- **Stardock Fences** — fallback only; Rainmeter chosen as primary

---

## 2. Programs to Uninstall

### Bloat / Unnecessary

- **Norton 360** — replaced by Microsoft Defender. Use Norton Remove and Reinstall Tool (NRnR) from norton.com/nrnr in Safe Mode if remnants persist. Choose "Remove Only" not "Remove and Reinstall."
- **uTorrent Web** AND **BitTorrent Web** — duplicates of qBittorrent, both adware-laden
- **Speccy** — HWiNFO64 already does this better
- **ePSXe** — DuckStation is better (already installed)
- **Old Blender versions** — 3 versions installed (3.6.23, 4.1.1, 4.4.0); keep only latest unless old project requires older version
- **Duplicate Notion Calendar** (1.122.0 vs 1.133.0) — remove older
- **Duplicate Azahar** (system + User install) — pick one
- **Duplicate CurseForge** — remove older
- **Java 8 Update 431** — only if FTB App / old Minecraft no longer used
- **Action Replay PowerSaves 3DS** — if no physical 3DS in use

### Decisions to Make

- **TeamViewer vs RustDesk** — RustDesk preferred (free/OSS forever, TeamViewer free tier increasingly restrictive). **Nuance:** RustDesk's default uses its public relay infrastructure for signaling/connection. For most home use this is fine. If your threat model includes account-takeover concerns at the relay provider level, RustDesk supports self-hosting your own relay server (hbbs/hbbr). Default public relay is acceptable for typical use; consider self-hosted setup only if you specifically need that control.
- **OrcaSlicer vs FlashPrint 5** — keep both if FlashForge printer owned; otherwise Orca only
- **Vortex vs Mod Organizer 2** — MO2 is community standard for serious Bethesda modding

### Pre-Uninstall Diagnostic for Duplicates

Before uninstalling duplicates (Notion Calendar, CurseForge, Azahar, Blender multi-versions), verify which install is current/active to avoid removing the wrong one:

```powershell
winget list | findstr /i "notion curseforge azahar blender"
```

Or check Apps & Features (Settings → Apps → Installed apps → sort by install date). Specifically watch for:

- **MSIX vs EXE** installs of the same app — these are different package types managed differently. Pick the one still receiving updates.
- **Old Blender versions:** confirm which build your installed addons bind to before deleting intermediate versions. Multiple Blender installs often persist precisely because addons are version-locked. Check `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\` for each version before removing.

### Leave Alone

Visual C++ Redistributables (multiple versions) — different programs need different versions; do not touch.

---

## 3. Security Setup — How It Works

### Defense Layers (in order)

1. **DNS filtering (NextDNS)** — blocks malicious domains before traffic leaves machine
2. **Browser layer (Firefox + uBO)** — blocks malvertising and bad ads inside the browser
3. **Sandbox layer (Sandboxie Plus + Windows Sandbox)** — contains anything that actually executes
4. **Antivirus (Microsoft Defender)** — catches signature-identifiable malware
5. **Recovery (Veeam imaging)** — full restore if everything else fails

### Defender Hardening (toggles, no installs)

- Tamper Protection: ON
- Controlled Folder Access: ON (protects Documents/Pictures/Desktop)
- PUA Protection: ON
- Cloud-delivered Protection: ON
- SmartScreen: ON

**Expect CFA friction in the first few weeks.** Controlled Folder Access will block legitimate apps writing to protected folders — Steam saving game progress to Documents, creative apps writing project files to Pictures, mod managers touching Documents/My Games, etc. This is normal. When a trusted app gets blocked: Windows Security → Virus & threat protection → Manage settings → Controlled folder access → Allow an app through Controlled folder access → add the specific executable. Add allowlist entries narrowly, one app at a time. **Do NOT blanket-disable CFA** when frustrated — that defeats the protection entirely. Plan to spend ~10 minutes of allowlist tuning across the first month of use.

### Browser Lanes

- **Daily / banking / work:** Firefox with uBO + Multi-Account Containers + Bitwarden
- **Sketchy browsing:** Brave (Sandboxie-launched ONLY, box name: RiskyWeb)
- **One-off untrusted file (Win Pro+):** Windows Sandbox (drag, run, close, gone)
- **One-off untrusted file (Win Home):** VirtualBox disposable Windows VM (slower setup, same isolation principle)

### Triage Workflow for Unknown Files

1. **VirusTotal first** — 10 seconds, 70+ engines. 0 flags = probably fine; 5+ flags = malware
2. **Any.Run / Hybrid Analysis** — if VT is inconclusive, full behavior report
3. **Windows Sandbox (Pro+) or VirtualBox VM (Home)** — if you actually need to run it, isolate it. Windows Sandbox is faster on supported editions; VirtualBox is the Home-edition equivalent with stronger but heavier isolation.
4. **Never** upload sensitive files to VT/Any.Run/Hybrid Analysis

### Identity / Auth

- Bitwarden master password: long unique passphrase, never reused
- Bitwarden 2FA: authenticator app (not SMS)
- Bitwarden recovery codes: stored offline, paper in locked location
- Browser-saved passwords: DISABLED in Firefox + Brave (use Bitwarden instead)

### Habits (the part that matters most)

- Sketchy → sandboxed Brave, every time, no exceptions
- VirusTotal before running unknown executables
- Never upload sensitive docs to public sandboxes
- Monthly: `winget upgrade --all` in admin PowerShell — **do not run mass updates on a deadline day**. Schedule for a buffer evening so any update breakage (GPU drivers, creative toolchain version bumps that break addons, etc.) doesn't disrupt active work. For reproducibility-critical workflows, skim the `winget upgrade` output before mass-applying and skip specific packages with `winget pin add <id>` if you want to freeze them.

---

## 4. Desktop Organization Plan

### Phase 1 — Quick Wins (today, ~15 min)

**Security ergonomics first:**

1. Rename sandboxed Brave shortcut to `Brave — SANDBOX ONLY`
2. Delete or hide all non-sandboxed Brave shortcuts (desktop AND taskbar)
3. If Brave was pinned to taskbar, unpin it — taskbar Brave is the same trap as desktop Brave

**Then duplicate cleanup:**

4. Delete duplicate Firefox desktop shortcut
5. Pick one location for Sandboxie Plus (taskbar pin recommended), delete other
6. Move security `.url` shortcuts (VirusTotal, Any.Run, Hybrid Analysis, NextDNS, uBO AMO) to Firefox Bookmarks Bar → `Security` folder. Delete from desktop.
7. If Bitwarden is on taskbar, delete desktop Bitwarden duplicate

### Phase 2 — Monitor Roles (5 min)

- **Launcher monitor** (probably Monitor A or B based on natural head-turn): holds grouped shortcuts + Recycle Bin
- **Workspace monitor**: mostly empty, holds current project files only
- Don't split categories across monitors unless it matches how you turn your head

### Phase 3 — Structure Layer

**Chosen path: Rainmeter (Path A)**

Rainmeter is already installed (version 4.5.x). Reasons for Rainmeter over Fences:
- Open-source modular (matches stated preference)
- Already installed (no purchase needed)
- Capable of more than just Fences-style icon containment

Suggested suite: **JaxCore** — modern, well-maintained, includes launcher panels that handle 60+ icon use case.

Setup: budget one focused 1-2 hour evening. Import JaxCore, add one launcher column per category (Games, Emulators, Creative, Utilities, Finance, AI, PDF, Documents). Only disable "Show desktop icons" after comfortable launching from Rainmeter + taskbar.

**Validate high-DPI and multi-monitor scaling early.** Before committing significant config time to any Rainmeter suite, place launcher skins on both monitors and verify they render at correct size, position, and pixel density. Rainmeter handles mixed-DPI multi-monitor setups inconsistently — some skins assume single-DPI, some don't anchor properly when monitors have different scaling factors. Catching this in the first 15 minutes saves abandoning a half-built setup later. If JaxCore mis-scales badly on your setup, try a simpler launcher-focused suite or fall back to Path B/C rather than fighting it.

**Fallback (Path C):** Stardock Fences ($9.99 one-time) if Rainmeter friction wins after honest 2-hour attempt.

### Phase 4 — Taskbar Hygiene

- 6-10 pins total of true dailies
- Examples: Firefox, Discord, Steam, Notion, Bitwarden, File Explorer, Sandboxie Plus
- Unpin Chrome if Firefox is primary (redundant)
- Unpin Brave (security policy)
- Specialty apps (weekly use) → folder/Rainmeter/Start pin, not taskbar

### Phase 5 — Ongoing Discipline

- Every new installer that drops a desktop icon: drag into correct folder/Rainmeter immediately OR delete if Start Menu entry exists
- Uncheck "create desktop shortcut" during installs when offered

### Suggested Categories

- **Browsers & Identity:** Firefox, Brave (SANDBOX ONLY), Bitwarden, Firefox Multi-Account Containers
- **Security & Sandboxing:** Sandboxie Plus
- **Utilities / Hardware:** HWiNFO64, RustDesk, Docker Desktop, File Organizer
- **AI & Creative Pipelines:** ComfyUI, AI Art Pipeline Dashboard, Stacher
- **Image / Photo:** GIMP, Krita, Inkscape, darktable, XnView MP, Figma, ExifTool, ExifCleaner
- **Video / Audio:** DaVinci Resolve, Kdenlive, Audacity
- **3D / Design / Print:** Blender 4.4, OrcaSlicer, Scribus, Azgaar's Fantasy Map Generator
- **Documents & PDF:** Notion, LibreOffice, PDF24 Launcher, PDF24 Toolbox, Stirling-PDF
- **Game Launchers:** Steam, Discord, XIVLauncher, Jagex Launcher, Toontown Rewritten, Arknights Endfield
- **Game Modding:** Mod Organizer, Bethesda mod tools, Advanced Combat Tracker
- **Emulators:** Azahar, RetroArch, Dolphin, DuckStation, PCSX2 (drop ePSXe)
- **Finance:** Webull Desktop

---

## 5. Norton Removal Status & Verification

### Required Steps (if Norton remnants persist)

1. Run Norton Remove and Reinstall Tool (NRnR) from norton.com/nrnr — choose "Remove Only" (not default "Remove and Reinstall")
2. Run in **Safe Mode** if normal removal doesn't finish — Norton's services can't start in Safe Mode, allowing full cleanup
3. Check Task Scheduler for any Norton-related tasks → delete
4. Check Services (services.msc) for Norton services → Stop + Disable
5. Use Autoruns (from Sysinternals) → filter "Norton" → delete all persistence entries
6. Manually delete leftover folders:
   - `C:\Program Files\Norton Security`
   - `C:\Program Files (x86)\Norton Security`
   - `C:\Program Files (x86)\Norton Installer`
   - `C:\ProgramData\Norton`
   - `%LocalAppData%\Norton`
   - `%AppData%\Norton`

### Post-Removal Verification

- Windows Security → Virus & threat protection → confirm Microsoft Defender Antivirus is active provider
- Windows Security → Virus & threat protection → Manage settings → Exclusions → confirm empty (delete any Norton-era exclusions)

---

## 6. Deferred (Out of Initial Scope)

- **YubiKey or hardware security key** — for Bitwarden + primary email, after baseline stable
- **Monthly winget upgrade routine** — calendar reminder for `winget upgrade --all` in admin PowerShell
- **CFA exclusion tuning** — narrow allowlists for trusted apps blocked by Controlled Folder Access. Never blanket-disable CFA.
- **Sandboxie auto-update** — Sandboxie Plus requires manual updates from GitHub releases
- **Browser hardening beyond uBO** — arkenfox user.js for Firefox is too aggressive for most users; skip unless specifically wanted

---

## 7. Microsoft Defender vs Microsoft 365 Defender (clarification)

Two different products both called "Microsoft Defender":

1. **Windows Security / Microsoft Defender Antivirus** — built-in, free, always on. Access via Start → "Windows Security." This is the actual antivirus.
2. **Microsoft Defender app (Microsoft 365 subscription)** — paid cross-device dashboard. Upsell. Click "No thanks" if prompted.

The free one is what's actually protecting the system.

---

## 8. Key Habits Going Forward

1. **Sketchy → sandboxed lane, always.** No exceptions for convenience.
2. **VirusTotal first** for any unknown executable (5 seconds).
3. **Never upload sensitive files to public sandboxes** (VT/Any.Run/Hybrid Analysis).
4. **Monthly updates** via `winget upgrade --all`. Run on a buffer evening, not deadline days.
5. **Quarterly restore test** — verify backups actually work by restoring one file.
6. **Annual boot-from-backup rehearsal** — full recovery media test once a year. The point of imaging backups is recovery from total system failure; if you've never actually booted from your backup, you don't know it works. Boot a spare drive or VM from your Veeam recovery image once a year to confirm.
7. **No browser-saved passwords** — Bitwarden only.

---

*End of reference document. Update as setup evolves.*
