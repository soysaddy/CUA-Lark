from pathlib import Path

from PIL import Image, ImageFilter


def sanitize_image(image: Image.Image, blur_radius: float = 1.5) -> Image.Image:
    return image.filter(ImageFilter.GaussianBlur(radius=blur_radius))


def safe_filename(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in name)


def ensure_parent(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
