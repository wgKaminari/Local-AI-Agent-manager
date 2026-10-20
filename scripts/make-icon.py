"""Regenerate the Windows application icon (developer tool, needs Pillow).

Draws the Focus brand mark, a dark "J" on an amber rounded square, at every
size Windows requests, so small taskbar sizes stay sharp instead of being
downscaled. Output: src/norway_job_agent/assets/job-agent.ico.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

AMBER = (242, 181, 68, 255)
INK = (21, 19, 13, 255)
SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
FONTS = ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf")


def font(size):
    for name in FONTS:
        try:
            return ImageFont.truetype(str(Path("C:/Windows/Fonts") / name), size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def mark(size):
    scale = 4  # Draw large, then downsample for clean anti-aliasing.
    big = size * scale
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    inset = 0 if size <= 24 else round(big * 0.04)
    draw.rounded_rectangle((inset, inset, big - inset - 1, big - inset - 1), radius=round(big * 0.24), fill=AMBER)
    letter = font(round(big * (0.78 if size <= 24 else 0.66)))
    box = draw.textbbox((0, 0), "J", font=letter)
    x = (big - (box[2] - box[0])) / 2 - box[0] - big * 0.02
    y = (big - (box[3] - box[1])) / 2 - box[1]
    draw.text((x, y), "J", font=letter, fill=INK)
    return image.resize((size, size), Image.LANCZOS)


def main():
    target = Path(__file__).resolve().parents[1] / "src" / "norway_job_agent" / "assets" / "job-agent.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    images = [mark(size) for size in SIZES]
    images[-1].save(target, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print("Wrote", target.name)


if __name__ == "__main__":
    main()
