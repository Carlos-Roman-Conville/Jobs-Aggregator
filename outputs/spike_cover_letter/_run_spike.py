"""One-off spike runner - not part of job_pipeline."""
from pathlib import Path
import json
import subprocess
import shutil

try:
    from job_pipeline.bootstrap_resume_profile import load_consolidated_profile

    prof = load_consolidated_profile()
except Exception:
    prof = {"name": "Carlos Roman-Conville", "contact": {"email": "carlos@example.com", "location": "Philadelphia, PA"}}

name = prof.get("name") or "Carlos Roman-Conville"
contact = prof.get("contact") if isinstance(prof.get("contact"), dict) else {}
email = contact.get("email") or "carlos@example.com"
location = contact.get("location") or "Philadelphia, PA"

p1 = (
    "I am writing to express my interest in the IT Support Specialist position. "
    "Over the past several years I have built hands-on experience supporting end users, "
    "troubleshooting Windows and Linux systems, and maintaining reliable service desk operations."
)
p2 = (
    "In my current technical operations role I manage Linux-based server infrastructure, "
    "deploy and maintain kiosk hardware, and support networked AV and RFID systems. "
    "I am comfortable working through ticketing queues and communicating with non-technical stakeholders."
)
p3 = (
    "I would welcome the opportunity to bring this mix of help desk discipline and systems "
    "troubleshooting to your team. Thank you for considering my application."
)
body = f"{p1}\n\n{p2}\n\n{p3}"

out = Path(__file__).resolve().parent
themes = ["engineeringresumes", "classic", "sb2nov"]
exe = shutil.which("rendercv") or "rendercv"

for theme in themes:
    yaml_text = (
        "cv:\n"
        f"  name: {json.dumps(name)}\n"
        f"  location: {json.dumps(location)}\n"
        f"  email: {json.dumps(email)}\n"
        "  sections:\n"
        "    cover_letter:\n"
        f"      - {json.dumps(body)}\n"
        "design:\n"
        f"  theme: {json.dumps(theme)}\n"
    )
    fn = out / f"spike_{theme}.yaml"
    fn.write_text(yaml_text, encoding="utf-8")
    print("rendering", fn.name)
    r = subprocess.run([exe, "render", str(fn)], capture_output=True, text=True, cwd=str(out))
    print(r.stdout[-500:] if r.stdout else "")
    if r.returncode != 0:
        print("ERR:", (r.stderr or r.stdout)[-800:])
