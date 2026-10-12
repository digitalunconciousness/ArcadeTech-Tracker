"""Draw the app's home-screen icons (app/static/icons/icon-192.png, icon-512.png): a
joystick in the synthwave colors. Maskable, so everything that matters sits inside the
middle 80%. Run once after changing it; the PNGs are committed.

    python scripts/make_icons.py
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "icons"
BG, BG2 = (0x15, 0x0A, 0x28), (0x1F, 0x0F, 0x3D)
MAGENTA, CYAN, BORDER = (0xFF, 0x2E, 0x9F), (0x7D, 0xFA, 0xFF), (0x3D, 0x1E, 0x6B)
SIZE = 512


def draw():
    img = Image.new("RGB", (SIZE, SIZE), BG)
    d = ImageDraw.Draw(img)
    for y in range(SIZE):  # the app's vertical gradient, bg2 at the top
        t = y / (SIZE - 1)
        shade = tuple(round(a + (b - a) * t) for a, b in zip(BG2, BG, strict=True))
        d.line([(0, y), (SIZE, y)], fill=shade)
    for y in range(0, SIZE, 8):  # scanlines
        d.line([(0, y), (SIZE, y)], fill=(0x12, 0x08, 0x22))
    # base plate
    d.rounded_rectangle([136, 330, 376, 390], radius=22, fill=BORDER, outline=CYAN, width=6)
    # shaft
    d.rounded_rectangle([244, 190, 268, 340], radius=10, fill=CYAN)
    # ball top, with a glow ring
    d.ellipse([186, 100, 326, 240], outline=(0x80, 0x17, 0x50), width=10)
    d.ellipse([196, 110, 316, 230], fill=MAGENTA)
    d.ellipse([222, 128, 262, 160], fill=(0xFF, 0x9C, 0xD0))
    # two buttons
    d.ellipse([150, 300, 186, 336], fill=CYAN)
    d.ellipse([326, 300, 362, 336], fill=MAGENTA)
    return img


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    img = draw()
    for n in (192, 512):
        img.resize((n, n), Image.LANCZOS).save(OUT / f"icon-{n}.png", optimize=True)
        print(OUT / f"icon-{n}.png")


if __name__ == "__main__":
    main()
