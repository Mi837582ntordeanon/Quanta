"""Create Quanta's small graphite/ice-teal Windows application icon."""
from pathlib import Path

from PIL import Image, ImageDraw


def main() -> None:
    sizes = (16, 24, 32, 48, 64, 128, 256)
    canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((9, 9, 247, 247), radius=58, fill="#1b2420", outline="#3a5148", width=5)
    draw.ellipse((55, 50, 192, 187), outline="#91cfc4", width=18)
    draw.line((151, 145, 204, 202), fill="#91cfc4", width=18)
    output = Path(__file__).resolve().parents[1] / "assets" / "quanta.ico"
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, format="ICO", sizes=[(size, size) for size in sizes])


if __name__ == "__main__":
    main()
