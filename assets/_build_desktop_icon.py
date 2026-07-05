"""Build a square .ico for the Windows Job Pipeline shortcut (center-crop, no stretch)."""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

# Cursor saves pasted chat images here (same slug as repo folder name).
_CURSOR_PROJECT_ASSETS = (
    Path.home() / ".cursor/projects/e-AI-Programs-AI-job-application-pipeline/assets"
)


def sync_latest_png_from_cursor(dest: Path) -> bool:
    """Copy newest *.png from Cursor workspace assets into dest; returns True if copied."""
    if not _CURSOR_PROJECT_ASSETS.is_dir():
        return False
    pngs = sorted(_CURSOR_PROJECT_ASSETS.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not pngs:
        return False
    shutil.copyfile(pngs[0], dest)
    return True


def square_center_crop(im: Image.Image) -> Image.Image:
    """Largest centered square; keeps aspect ratio (no squish), good for small icon sizes."""
    im = im.convert("RGBA")
    w, h = im.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return im.crop((left, top, left + side, top + side))


def main() -> None:
    here = Path(__file__).resolve().parent
    src = here / "pipeline_desktop_icon_source.png"
    # Distinct filename so Explorer picks up updates (same path + rewritten .ico often stays cached).
    out = here / "job_pipeline_shortcut.ico"
    sync_latest_png_from_cursor(src)
    if not src.exists():
        raise SystemExit(f"missing {src}")
    sq = square_center_crop(Image.open(src))
    sizes = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]
    imgs = [sq.resize(s, Image.Resampling.LANCZOS) for s in sizes]
    imgs[0].save(
        out,
        format="ICO",
        sizes=[(a, b) for a, b in sizes],
        append_images=imgs[1:],
    )
    print(out, out.stat().st_size)


if __name__ == "__main__":
    main()
