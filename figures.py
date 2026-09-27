#!/usr/bin/env python3
"""figures.py -- turn figure PDFs into web images, at a resolution that
is actually justified by what is inside the PDF.

    pip install pymupdf pillow

    python3 tools/figures.py --info paper/fig_bridge_qual.pdf
    python3 tools/figures.py paper/fig_bridge_qual.pdf
    python3 tools/figures.py --width 4000 paper/*.pdf
    python3 tools/figures.py --svg paper/fig_method.pdf

WHY A PNG OFF A PDF CAN LOOK BLURRY
-----------------------------------
There are two separate causes and they need opposite fixes, so always
run --info first. It prints both numbers.

1. The raster came out too small.
   Rendering dpi is dpi *of the page*, so a 5.5-inch-wide figure at
   200 dpi is only 1100 px. The page shows figures up to 880 px wide
   and the click-to-enlarge view up to 1600 px, which is 3200 device
   pixels on any modern laptop screen. A 1100 px file is then being
   stretched about 3x. Fix: render wider. This script takes a target
   pixel width (--width, default 3000) and works the dpi out from the
   page size, instead of you guessing a dpi.

2. The bitmaps inside the PDF are already small.
   This one is not fixable here, and it is the more common cause with
   matplotlib. plt.savefig() resamples every imshow down to the
   figure's physical size at the save dpi, which defaults to 100. A
   256x256 frame drawn 0.9 inches wide is written into the PDF as an
   87x87 bitmap, and those pixels are gone. Rendering the page at
   600 dpi then upscales 87 px to 540 px and looks worse, not better.

   The fix is upstream, where the figure is made:

       plt.savefig("fig.pdf", dpi=300)     # or higher

   Enough dpi that (panel width in inches) x dpi >= the frame's real
   pixel width. For a 0.9-inch panel showing a 256 px frame, that is
   285 dpi, so 300 is the round number. Check it worked by running
   --info again: the embedded bitmap should now be >= 256 px.

   If you cannot re-run the figure, --clamp renders at native
   resolution rather than producing a large soft file.
"""

import argparse
import sys
from pathlib import Path

try:
    import pymupdf
except ImportError:  # pymupdf <1.24 shipped as fitz
    try:
        import fitz as pymupdf
    except ImportError:
        sys.exit("pymupdf is not installed.  pip install pymupdf")

HERE = Path(__file__).resolve().parent
OUT_DEFAULT = HERE.parent


def survey(page):
    """Page size, and how big the embedded bitmaps really are."""
    w_in = page.rect.width / 72.0
    h_in = page.rect.height / 72.0

    native_dpi = None            # dpi at which the tightest bitmap is 1:1
    worst = None
    for img in page.get_images(full=True):
        xref, _, px_w, px_h = img[0], img[1], img[2], img[3]
        rects = page.get_image_rects(xref)
        if not rects:
            continue
        drawn_in = max(r.width for r in rects) / 72.0
        if drawn_in <= 0:
            continue
        d = px_w / drawn_in
        if native_dpi is None or d < native_dpi:
            native_dpi = d
            worst = (px_w, px_h, drawn_in)
    return w_in, h_in, native_dpi, worst


def report(path, page):
    w_in, h_in, native_dpi, worst = survey(page)
    print(f"\n{path}")
    print(f"  page            {w_in:.2f} x {h_in:.2f} in "
          f"({page.rect.width:.0f} x {page.rect.height:.0f} pt)")
    n = len(page.get_images(full=True))
    if not n:
        print("  bitmaps         none -- pure vector, render at any size you like")
        return None
    px_w, px_h, drawn_in = worst
    print(f"  bitmaps         {n}, tightest is {px_w}x{px_h} px "
          f"drawn {drawn_in:.2f} in wide")
    print(f"  native dpi      {native_dpi:.0f}  "
          f"(past this the bitmaps are being upscaled)")
    print(f"  sharp up to     {int(w_in * native_dpi)} px wide output")
    if native_dpi < 220:
        print("\n  ^ This figure is bitmap-limited. Re-export it from the")
        print("    source with a higher save dpi (see the header of this")
        print("    file); no rendering setting here can add detail back.")
    return native_dpi


def save_pixmap(pm, dest, fmt, quality):
    """PNG through PyMuPDF; anything lossy through Pillow."""
    if fmt == "png":
        pm.save(dest)
        return
    try:
        from PIL import Image
    except ImportError:
        sys.exit("pillow is needed for webp/jpeg output.  pip install pillow")
    im = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
    if fmt == "webp":
        im.save(dest, "WEBP", quality=quality, method=6)
    else:
        im.save(dest, "JPEG", quality=quality, subsampling=0,
                optimize=True, progressive=True)


def render(path, out_dir, target_w, dpi_override, clamp, svg, retina,
           fmt="png", quality=90):
    doc = pymupdf.open(path)
    page = doc[0]
    stem = Path(path).stem
    out_dir.mkdir(parents=True, exist_ok=True)

    if svg:
        dest = out_dir / f"{stem}.svg"
        dest.write_text(page.get_svg_image())
        print(f"  -> {dest.relative_to(out_dir.parent.parent)}  "
              f"({dest.stat().st_size / 1024:.0f} KB)")
        return

    w_in, _h_in, native_dpi, _worst = survey(page)

    def emit(width_px, suffix=""):
        dpi = dpi_override if dpi_override else width_px / w_in
        note = ""
        if clamp and native_dpi and dpi > native_dpi:
            dpi = native_dpi
            note = "  (clamped to native)"
        pm = page.get_pixmap(dpi=round(dpi), alpha=False)
        dest = out_dir / f"{stem}{suffix}.{fmt}"
        save_pixmap(pm, dest, fmt, quality)
        kb = dest.stat().st_size / 1024
        size = f"{kb:.0f} KB" if kb < 1024 else f"{kb / 1024:.1f} MB"
        up = ""
        if native_dpi and dpi > native_dpi * 1.05:
            up = f"  [bitmaps upscaled {dpi / native_dpi:.1f}x]"
        print(f"  -> {dest.name}  {pm.width}x{pm.height}  "
              f"{round(dpi)} dpi  {size}{note}{up}")

    if retina:
        emit(target_w // 2)
        emit(target_w, "@2x")
    else:
        emit(target_w)


def main():
    ap = argparse.ArgumentParser(
        description="Render figure PDFs to web-ready PNG or SVG.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with --info first; it tells you whether a bigger "
               "render will help at all.")
    ap.add_argument("pdfs", nargs="+", type=Path)
    ap.add_argument("--info", action="store_true",
                    help="report what is inside the PDF and write nothing")
    ap.add_argument("--width", type=int, default=3000,
                    help="target output width in pixels (default 3000, "
                         "which is 2x the widest the page ever shows a "
                         "figure)")
    ap.add_argument("--dpi", type=int,
                    help="set the render dpi directly, ignoring --width")
    ap.add_argument("--clamp", action="store_true",
                    help="never render past the resolution of the bitmaps "
                         "inside the PDF (smaller file, no added blur)")
    ap.add_argument("--retina", action="store_true",
                    help="also write name@2x.png, so the page can load the "
                         "small one and the enlarge view the big one")
    ap.add_argument("--format", choices=("png", "webp", "jpeg"),
                    default="png",
                    help="png is lossless and right for diagrams and "
                         "plots; webp is several times smaller at the "
                         "same visual quality and is the better choice "
                         "for a figure made mostly of photographs")
    ap.add_argument("--quality", type=int, default=92,
                    help="quality for webp/jpeg (default 92)")
    ap.add_argument("--svg", action="store_true",
                    help="vector output; only for figures with no "
                         "photographs in them")
    ap.add_argument("-o", "--out", type=Path, default=OUT_DEFAULT)
    a = ap.parse_args()

    for pdf in a.pdfs:
        if not pdf.exists():
            print(f"skip: {pdf} not found", file=sys.stderr)
            continue
        doc = pymupdf.open(pdf)
        report(str(pdf), doc[0])
        if not a.info:
            render(pdf, a.out, a.width, a.dpi, a.clamp, a.svg, a.retina,
                   a.format, a.quality)
    print()


if __name__ == "__main__":
    main()
