"""Audit what's being rejected by the narrow filter — find tech we may have lost."""
import os
import re
from collections import Counter

os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5433")
os.environ.setdefault("POSTGRES_USER", "postgres")
os.environ.setdefault("POSTGRES_DB", "postgres")
os.environ.setdefault("POSTGRES_PASSWORD", "yourpassword")

from job_pipeline.db import pg_connect
from job_pipeline.search_preferences import passes_target_title_filter, load_search_preferences

load_search_preferences(reload=True)

# Tech-flavored signals — words that suggest legit IT / support / network roles
TECH_KEYWORDS = [
    "it ", "tech support", "support specialist", "support technician", "support analyst",
    "support engineer", "noc ", "help desk", "helpdesk", "service desk", "desktop",
    "customer support", "customer success", "operations manager", "ops manager",
    "systems administrator", "sysadmin", "system support", "systems support",
    "network admin", "network technician", "network analyst", "implementation",
    "field service", "field tech", "computer", "endpoint", "workstation",
    "deskside", "user support", "client support", "infrastructure",
    "soc analyst", "security analyst", "grc", "compliance analyst",
    "information technology", "automation support",
    "biomedical", "av tech", "audio visual",
]

OPS_KEYWORDS = [
    "operations manager", "ops manager", "office manager", "store manager",
    "general manager", "shift manager", "floor manager", "facilities manager",
    "service manager", "support manager", "it manager", "help desk manager",
]

conn = pg_connect()
with conn.cursor() as cur:
    cur.execute("""
        SELECT p.title
          FROM job_postings p
          JOIN job_pipeline_items i ON i.posting_id = p.id
         WHERE i.status = 'ingested'
    """)
    rows = [r[0] or "" for r in cur.fetchall()]

print(f"Total unsummarized backlog: {len(rows)}\n")

# Bucket all rejected titles by category
rejects = [t for t in rows if not passes_target_title_filter(t)]
passes_ = [t for t in rows if passes_target_title_filter(t)]

print(f"PASSES: {len(passes_)}")
print(f"REJECTS: {len(rejects)}\n")

print("=" * 78)
print("OPS-MANAGER-FLAVORED titles in PASSES (sanity — should be present):")
print("=" * 78)
ops_passes = [t for t in passes_ if any(k in t.lower() for k in OPS_KEYWORDS)]
print(f"Count: {len(ops_passes)}")
for t in ops_passes[:25]:
    print(f"  PASS  {t}")

print()
print("=" * 78)
print("TECH-FLAVORED REJECTS — would Carlos want any of these back?")
print("=" * 78)
tech_rejects = []
for t in rejects:
    tl = t.lower()
    if any(k in tl for k in TECH_KEYWORDS):
        tech_rejects.append(t)

# Categorize the tech rejects by reason — bin into buckets
buckets = Counter()
def bucket_reason(title: str) -> str:
    tl = title.lower()
    if re.search(r"\b(?:ii|iii|2|3)\b", tl):
        return "Tier II/III / Level 2-3 (correct reject — over-leveled)"
    if re.search(r"\b(?:senior|sr\.?|principal|staff|lead|supervisor|supervisory|director|vp|head of|chief)\b", tl):
        return "Senior/Lead/Director/Supervisor (correct reject)"
    if re.search(r"\b(?:engineer|developer|architect|scientist)\b", tl) and not re.search(r"customer support engineer", tl):
        return "Engineer/Developer/Architect (correct reject — wrong field)"
    if re.search(r"\b(?:devops|sre|site reliability|cloud|backend|frontend|full[\s-]?stack|software)\b", tl):
        return "DevOps/SRE/Cloud/Software (correct reject)"
    if re.search(r"\b(?:sales|account exec|business development|bdr|sdr)\b", tl):
        return "Sales (correct reject)"
    if re.search(r"\b(?:security|cyber|soc|grc|compliance|risk)\b", tl):
        return "Security/SOC/GRC (DEFERRED — Oct trigger gated on Security+)"
    if re.search(r"\b(?:network|sysadmin|systems? admin)", tl):
        return "Network/Sysadmin (DEFERRED — gated on AD lab + Network+)"
    if re.search(r"\b(?:project manager|program manager|product manager|pm)\b", tl):
        return "Project/Program/Product Manager (correct reject)"
    if re.search(r"\b(?:field service|field technician|field tech|on[-\s]?site tech)", tl):
        return "Field Service / Hands-on (excluded by your narrow targeting)"
    if re.search(r"\b(?:implementation|integration|onboarding)\b", tl):
        return "Implementation Specialist (excluded — was a Tier 2 overshoot before)"
    if re.search(r"\b(?:biomedical|clinical|medical|nurse|surgical|veterinary)\b", tl):
        return "Healthcare/biomedical (correct reject — not your lane)"
    if "automation" in tl:
        return "Automation (specialty — likely correct reject)"
    if re.search(r"\b(?:tier|level)\s*(?:1|i|one)\b", tl):
        return "Possible Tier-1 leak (worth checking individually)"
    if re.search(r"\b(?:system|systems)\s+(?:support|technician|analyst)\b", tl):
        return "Systems Support Technician/Analyst (POSSIBLE LANE — Carlos's profile fits)"
    if re.search(r"\b(?:client|user|end[\s-]?user)\s+support\b", tl):
        return "Client/End-user Support (POSSIBLE LANE — basically help desk)"
    return f"OTHER: {title}"

for t in tech_rejects:
    buckets[bucket_reason(t)] += 1

print("Tech-flavored rejects, bucketed:\n")
for cat, n in sorted(buckets.items(), key=lambda x: -x[1])[:40]:
    print(f"  [{n:>3}]  {cat}")

print()
print("=" * 78)
print("DEFERRED-CATEGORY REJECTS — confirm these were correctly excluded:")
print("=" * 78)
sec_rejects = [t for t in rejects if re.search(r"\b(?:security|cyber|soc|grc|compliance|risk)\b", t.lower())]
net_rejects = [t for t in rejects if re.search(r"\b(?:network|sysadmin|systems? admin)", t.lower())]
print(f"Security/SOC/GRC count: {len(sec_rejects)}  (deferred to Oct 2026)")
for t in sec_rejects[:12]:
    print(f"   {t}")
print()
print(f"Network/Sysadmin count: {len(net_rejects)}  (deferred to AD lab + Network+)")
for t in net_rejects[:12]:
    print(f"   {t}")

conn.close()
