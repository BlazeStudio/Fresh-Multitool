"""Рисует assets/multitool.ico: градиентный квадрат со скруглением и символом ❯_."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

SIZE = 256
STOPS = [(255, 46, 136), (167, 139, 250), (27, 163, 156)]


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def gradient(t):
    t = min(max(t, 0), 1) * (len(STOPS) - 1)
    i = min(int(t), len(STOPS) - 2)
    return lerp(STOPS[i], STOPS[i + 1], t - i)


def main():
    bg = Image.new("RGBA", (SIZE, SIZE))
    px = bg.load()
    for y in range(SIZE):
        for x in range(SIZE):
            px[x, y] = (*gradient((x + y) / (2 * SIZE - 2)), 255)

    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((8, 8, SIZE - 9, SIZE - 9), radius=58, fill=255)
    icon = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    icon.paste(bg, mask=mask)

    glyph = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(glyph)
    white = (255, 255, 255, 255)
    d.line([(70, 82), (128, 128), (70, 174)], fill=white, width=26, joint="curve")
    for x, y in [(70, 82), (128, 128), (70, 174)]:
        d.ellipse((x - 13, y - 13, x + 13, y + 13), fill=white)
    d.rounded_rectangle((140, 164, 196, 188), radius=12, fill=white)

    shadow = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    shadow.paste((20, 20, 30, 110), mask=glyph.split()[3])
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    icon.alpha_composite(shadow, (0, 4))
    icon.alpha_composite(glyph)

    out = Path(__file__).resolve().parent.parent / "assets" / "multitool.ico"
    out.parent.mkdir(exist_ok=True)
    icon.save(out, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icon.save(out.with_suffix(".png"))
    print("saved", out)


if __name__ == "__main__":
    main()
