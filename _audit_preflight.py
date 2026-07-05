"""Pre-flight audit before next ingest cycle.

Runs 10 sections of checks. Exits 0 if all FAIL=0 (warnings OK).
"""
import ast, json, os, sys
from pathlib import Path
from dotenv import load_dotenv; load_dotenv()

fails = []
warnings = []
def fail(s): fails.append(s); print(f'  FAIL  {s}')
def warn(s): warnings.append(s); print(f'  WARN  {s}')
def ok(s): print(f'  OK    {s}')


# ============================================================
print('\n=== 1. SYNTAX / IMPORT SANITY ===')
files = [
    'job_dashboard.py',
    'api_server.py',
    'job_pipeline/ingest.py',
    'job_pipeline/summarize.py',
    'job_pipeline/search_preferences.py',
    'job_pipeline/resume_tailor.py',
    'job_pipeline/learning_gaps.py',
    'job_pipeline/regression_check.py',
    'job_pipeline/cache_prefix.py',
    'job_pipeline/claude_client.py',
    'job_pipeline/anti_fluff.py',
    'job_pipeline/integrity_guards.py',
    'job_pipeline/service.py',
    'job_pipeline/domain_fit.py',
    'job_pipeline/sources/hire_heroes_usa.py',
    'job_pipeline/sources/feeds_source.py',
    'job_pipeline/sources/hn_whoishiring.py',
    'job_pipeline/sources/usajobs_source.py',
    'job_pipeline/sources/jobspy_source.py',
]
for fp in files:
    try:
        with open(fp, encoding='utf-8') as f:
            ast.parse(f.read())
        ok(fp)
    except SyntaxError as e:
        fail(f'{fp}: SyntaxError line {e.lineno}: {e.msg}')
    except FileNotFoundError:
        fail(f'{fp}: NOT FOUND')


# ============================================================
print('\n=== 2. CONFIG SANITY ===')
try:
    cfg_raw = json.loads(open('job_pipeline_config.json', encoding='utf-8').read())
    ok('job_pipeline_config.json is valid JSON')
except Exception as e:
    fail(f'job_pipeline_config.json invalid: {e}')
    sys.exit(1)

from job_pipeline.ingest import load_pipeline_config
cfg = load_pipeline_config()

enabled = {
    'greenhouse':       cfg.get('_greenhouse_enabled'),
    'lever':            cfg.get('_lever_enabled'),
    'indeed':           cfg.get('_indeed_enabled'),
    'jobspy':           cfg.get('_jobspy_enabled'),
    'usajobs':          cfg.get('_usajobs_enabled'),
    'hn_whoishiring':   cfg.get('_hn_whoishiring_enabled'),
    'hire_heroes_usa':  cfg.get('_hire_heroes_usa_enabled'),
}
for src, en in enabled.items():
    (ok if en else warn)(f'source {"enabled" if en else "DISABLED"}: {src}')

feeds = cfg.get('_feeds_cfg') or {}
feed_on = [k for k, v in feeds.items() if isinstance(v, dict) and v.get('enabled')]
print(f'  feeds enabled ({len(feed_on)}): {feed_on}')

tokens = cfg.get('greenhouse_board_tokens') or []
levers = cfg.get('lever_companies') or []
print(f'  greenhouse tokens: {len(tokens)}')
print(f'  lever companies:   {len(levers)}')
if len(tokens) < 5: warn(f'greenhouse: only {len(tokens)} tokens')
if len(levers) < 5: warn(f'lever: only {len(levers)} companies')

js_sites = (cfg.get('_jobspy_cfg') or {}).get('site_names') or []
print(f'  jobspy sites: {js_sites}')
if 'linkedin' not in js_sites: warn('jobspy missing LinkedIn')
if 'zip_recruiter' not in js_sites: warn('jobspy missing ZipRecruiter')


# ============================================================
print('\n=== 3. ENV / CREDENTIALS ===')
for var in ['OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'APIFY_TOKEN', 'APIFY_API_TOKEN',
            'USAJOBS_API_KEY', 'USAJOBS_EMAIL', 'OPENAI_JOB_SUMMARY_MODEL']:
    v = os.getenv(var) or ''
    if v.strip():
        if var.endswith('_KEY') or var.endswith('_TOKEN'):
            ok(f'{var} is set ({len(v)} chars)')
        else:
            ok(f'{var} = {v}')
    else:
        if var in ('APIFY_TOKEN', 'APIFY_API_TOKEN'):
            warn(f'{var} NOT set (Indeed-Apify ingest will skip)')
        elif var in ('USAJOBS_API_KEY', 'USAJOBS_EMAIL'):
            warn(f'{var} NOT set (USAJOBS may rate-limit)')
        elif var == 'ANTHROPIC_API_KEY':
            warn(f'{var} NOT set (resume/cover builds will fail later)')
        elif var == 'OPENAI_JOB_SUMMARY_MODEL':
            print(f'  (default) OPENAI_JOB_SUMMARY_MODEL = uses summarize.py default')
        else:
            fail(f'{var} NOT set (summarize will fail)')


# ============================================================
print('\n=== 4. SUMMARIZE PROMPT / COST OPTIMIZATIONS ===')
sm = open('job_pipeline/summarize.py', encoding='utf-8').read()
checks = [
    ('gpt-4.1-nano default',                  'gpt-4.1-nano' in sm),
    ('gpt-4.1-mini gone',                     'gpt-4.1-mini' not in sm),
    ('desc truncated to 4000',                'desc[:4000]' in sm),
    ('desc[:8000] removed',                   'desc[:8000]' not in sm),
    ('key_requirements asked in prompt',      'key_requirements (array of 3-7' in sm),
    ('key_requirements in card output',       '"key_requirements":' in sm),
    ('headline_one_line not asked from LLM',  'headline_one_line (string' not in sm),
    ('why_match not asked from LLM',          'why_match (string' not in sm),
    ('learning_gaps hook present',            'update_learning_gaps(' in sm),
]
for label, passed in checks:
    (ok if passed else fail)(label)


# ============================================================
print('\n=== 5. FILTER REGEX BEHAVIOR (target outcomes) ===')
from job_pipeline.search_preferences import load_search_preferences, score_posting_against_preferences
load_search_preferences(reload=True)

target = [
    ('Help Desk Analyst I',                   'PASS',   'IT-IC Tier 1'),
    ('Desktop Support Technician',            'PASS',   'IT-IC Tier 1'),
    ('NOC Technician',                        'PASS',   'IT-IC Tier 1'),
    ('CCTV Technician',                       'PASS',   'Hands-on tech'),
    ('Bench Technician',                      'PASS',   'Hands-on tech'),
    ('AV Installation Technician',            'PASS',   'Hands-on tech'),
    ('Low Voltage Technician',                'PASS',   'Hands-on tech'),
    ('Operations Manager',                    'PASS',   'Ops/Mgmt'),
    ('IT Manager',                            'PASS',   'Ops/Mgmt'),
    ('Office Manager',                        'PASS',   'Ops/Mgmt'),
    ('Shift Lead',                            'PASS',   'Ops/Mgmt'),
    ('Policy Assistant',                      'PASS',   'Political'),
    ('Legislative Aide',                      'PASS',   'Political'),
    ('Field Director',                        'PASS',   'Political (mgmt)'),
    ('Volunteer Coordinator',                 'PASS',   'Political (mgmt)'),
    ('Civic Technology Engineer',             'PASS',   'Civic-tech'),
    ('GovTech Specialist',                    'PASS',   'Civic-tech'),
    ('Voter File Administrator',              'PASS',   'Civic-tech'),
    ('Level 2 IT Support Technician',         'REJECT', 'Tier 2 IT-IC cap'),
    ('Implementation Specialist II',          'REJECT', 'Tier 2 IT-IC cap'),
    ('Senior Network Engineer',               'REJECT', 'Senior cap'),
    ('Engineering Manager',                   'REJECT', 'Eng mgr too senior'),
    ('Director of Operations',                'REJECT', 'Director-of'),
    ('Canvasser',                             'REJECT', 'IC canvassing'),
    ('Field Organizer',                       'REJECT', 'IC canvassing'),
    ('Phone Repair Technician',               'REJECT', 'Not grounded'),
    ('Security Alarm Installer',              'REJECT', 'Not grounded'),
    ('Tech Lead Engineer',                    'REJECT', 'Lead engineer'),
    ('Network Engineer III',                  'REJECT', 'Eng II/III'),
]
ffails = 0
for title, expected, family in target:
    r = score_posting_against_preferences({
        'title': title, 'description_text': 'Remote.',
        'location': 'Remote', 'salary_text': '$55,000',
    })
    closed = r.get('auto_close_reason')
    got = 'REJECT' if closed else 'PASS'
    if got != expected:
        fail(f'[{family}] {title!r} expected {expected} got {got} ({closed or ""})')
        ffails += 1
if not ffails:
    ok(f'all {len(target)} target-outcome cases pass')


# ============================================================
print('\n=== 6. ALT-TITLE PICKER ROUTING ===')
from job_pipeline.resume_tailor import _pick_btb_title_for_jd
profile_text = open('job_pipeline/career_master.md', encoding='utf-8').read()

picker = [
    ('Help Desk Analyst I',        'Technical Operations Manager — IT & Live Production'),
    ('CCTV Technician',            'CCTV & Security-Camera Operations Technician'),
    ('Bench Technician',           'Hardware Repair Technician — Live Production Facility'),
    ('AV Technician',              'AV / Networked Facility Systems Technician'),
    ('Low Voltage Technician',     'Network / Low-Voltage Technician — Multi-System Facility'),
    ('Operations Manager',         'Operations Manager'),
    ('IT Manager',                 'IT Operations Manager'),
    ('Policy Assistant',           'Operations Manager — Cross-Sector Coordination'),
    ('Legislative Aide',           'Operations Manager — Cross-Sector Coordination'),
    ('Field Director',             'Operations Manager — Cross-Sector Coordination'),
    ('Civic Technology Engineer',  'Technical Operations Manager — Civic / Public-Sector Mission'),
    ('Voter File Administrator',   'Technical Operations Manager — Civic / Public-Sector Mission'),
    ('Code for America Fellow',    'Technical Operations Manager — Civic / Public-Sector Mission'),
]
pfails = 0
for title, want in picker:
    got, score = _pick_btb_title_for_jd(title, '', profile_text)
    if got != want:
        fail(f'{title!r} routed to {got!r} (want {want!r}, score={score})')
        pfails += 1
if not pfails:
    ok(f'all {len(picker)} alt-title routes correct')


# ============================================================
print('\n=== 7. LEARNING GAPS WIRING ===')
try:
    from job_pipeline.learning_gaps import (
        update_learning_gaps, top_gaps, category_counts,
        normalize_keyword, _load_grounded_skill_set,
    )
    grounded = _load_grounded_skill_set(force=True)
    print(f'  grounded skill set size: {len(grounded)}')
    if len(grounded) < 30:
        warn(f'grounded set unusually small ({len(grounded)}) — check career_master parse')
    else:
        ok(f'grounded set size healthy ({len(grounded)} normalized terms)')
    expected_grounded = ['m365', 'salesforce', 'rustdesk', 'veeam', 'spanish']
    for term in expected_grounded:
        if term in grounded:
            ok(f'grounded contains: {term!r}')
        else:
            warn(f'grounded MISSING: {term!r} (would surface as gap)')
    ok('learning_gaps module imports cleanly')
except Exception as e:
    fail(f'learning_gaps module: {e}')


# ============================================================
print('\n=== 8. GROUNDING INTEGRITY ===')
try:
    p = json.loads(open('job_pipeline/consolidated_profile.json', encoding='utf-8').read())
    github = p.get('contact', {}).get('github')
    langs = [l.get('language') for l in p.get('languages', [])]
    certs = [c.get('name') for c in p.get('certifications', [])]
    if github and 'Carlos-Roman-Conville' in github: ok(f'GitHub URL: {github}')
    else: fail(f'GitHub URL missing or wrong: {github!r}')
    if 'Spanish' in langs: ok('Spanish language grounded')
    else: fail('Spanish missing from languages')
    if any('HIPAA' in (c or '') for c in certs): ok('HIPAA cert grounded')
    else: fail('HIPAA missing from certifications')
except Exception as e:
    fail(f'consolidated_profile.json read failed: {e}')

cm = open('job_pipeline/career_master.md', encoding='utf-8').read().lower()
gchecks = [
    ('through-hole soldering',          'through-hole' in cm),
    ('all-in-one screen repair',        'all-in-one' in cm),
    ('BIOS / CMOS battery',             'bios' in cm or 'cmos' in cm),
    ('LED wiring',                      'led wiring' in cm or 'led installation' in cm),
    ('phone repair NOT in scope',       'phone hardware repair is not' in cm),
    ('Wildwood NJ grounded',            'wildwood' in cm),
    ('Spanish grounded',                'spanish' in cm),
    ('HIPAA training grounded',         'hipaa awareness training' in cm),
]
for label, passed in gchecks:
    (ok if passed else fail)(f'career_master.md: {label}')


# ============================================================
print('\n=== 9. DASHBOARD WIRING ===')
ds = open('job_dashboard.py', encoding='utf-8').read()
dchecks = [
    ('salary chip helper present',         'def _pretty_salary' in ds and 'def _salary_badge_html' in ds),
    ('salary chip in queue card badges',   '_salary_badge_html(c["salary"])' in ds),
    ('key_requirements pulled into card',  'sj.get("key_requirements")' in ds),
    ('key_requirements rendered as chips', 'reqs_line' in ds),
    ('Learning Gaps tab created',          '"Learning Gaps"' in ds),
    ('Learning Gaps tab body wired',       'from job_pipeline.learning_gaps import' in ds),
    ('tabs count = 9 (added Learning Gaps)', ds.count('with tabs[') == 9),
]
for label, passed in dchecks:
    (ok if passed else fail)(label)


# ============================================================
print('\n=== 10. SOURCE / SCRAPER MATRIX ===')
sm_path = Path('job_pipeline/sources')
existing_modules = {f.stem for f in sm_path.glob('*.py') if f.name != '__init__.py'}
if Path('job_pipeline/apify_indeed.py').exists():
    existing_modules.add('apify_indeed')

src_to_module = {
    'greenhouse':       None,  # inline in ingest.py
    'lever':            None,  # inline in ingest.py
    'indeed':           'apify_indeed',
    'jobspy':           'jobspy_source',
    'usajobs':          'usajobs_source',
    'hn_whoishiring':   'hn_whoishiring',
    'hire_heroes_usa':  'hire_heroes_usa',
}
for src, mod in src_to_module.items():
    if enabled.get(src):
        if mod is None:
            ok(f'{src}: inline in ingest.py')
        elif mod in existing_modules:
            ok(f'{src}: module {mod}.py exists')
        else:
            fail(f'{src}: ENABLED but module {mod}.py NOT FOUND')

fsrc = open('job_pipeline/sources/feeds_source.py', encoding='utf-8').read()
expected_feeds = ['ingest_remoteok', 'ingest_arbeitnow', 'ingest_remotive',
                  'ingest_themuse', 'ingest_jobicy', 'ingest_working_nomads',
                  'ingest_weworkremotely_rss']
for fname in expected_feeds:
    if f'def {fname}' in fsrc:
        ok(f'feeds_source: {fname}')
    else:
        fail(f'feeds_source: MISSING function {fname}')


# ============================================================
print('\n' + '=' * 70)
print(f'SUMMARY: {len(fails)} FAIL · {len(warnings)} WARN')
if fails:
    print('\nFAILS:')
    for f in fails:
        print(f'  - {f}')
if warnings:
    print('\nWARNINGS (informational):')
    for w in warnings:
        print(f'  - {w}')
print()
sys.exit(0 if not fails else 1)
