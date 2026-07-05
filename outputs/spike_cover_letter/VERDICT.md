# Step 0 spike verdict

**Outcome: (a) Acceptable as-is** — rendercv `TextEntry` (plain string list under `sections.cover_letter`) renders three-paragraph prose cleanly. Default theme for cover letters: **`classic`** (matches existing tailored resume default in `rendercv_export.py`).

Notes from spike:
- Omit `cv.phone` when the value fails rendercv's phone validator (e.g. `856-397-9706`); email + location suffice for the header.
- `design.text_alignment: justified` is **not** a valid rendercv 2.3 design key — do not use for leaf (b).
- RenderCV uses Typst internally on this machine (render log shows "Rendering the Typst file to a PDF").

## Renderer availability probe

| Tool | Available |
|------|-----------|
| rendercv | yes (v2.3) |
| typst (CLI) | no on PATH |
| weasyprint (Python) | no (`ModuleNotFoundError`) |
| pandoc | no on PATH |
| xelatex | no on PATH |

If rendercv cover-letter path fails in production, add `weasyprint` to requirements and implement HTML→PDF fallback in `cover_letter_export.py`.

## Artifacts

- YAML: `spike_engineeringresumes.yaml`, `spike_classic.yaml`, `spike_sb2nov.yaml`
- PDFs: produced under each YAML's `rendercv_output/` subfolder by rendercv CLI
