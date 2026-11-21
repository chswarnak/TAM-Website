
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont, ImageOps



def add_contributor_watermark(
    image_bytes: bytes,
    contributor_name: str,
    event_name: str = "TAM Diwali 2026"
) -> bytes:
    """
    Add a very small, transparent contributor watermark
    at the bottom-left corner.

    Original S3 photograph remains unchanged.
    """

    if not contributor_name or not contributor_name.strip():
        raise ValueError("Contributor name is required.")

    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGBA")

    width, height = image.size

    # Small but visible italic font.
    font_size = max(12, int(width * 0.0225))

    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf",
            font_size
        )
    except OSError:
        font = ImageFont.load_default()

    text = contributor_name.strip()

    overlay = Image.new(
        "RGBA",
        image.size,
        (0, 0, 0, 0)
    )

    draw = ImageDraw.Draw(overlay)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_height = bbox[3] - bbox[1]

    # Small margin from left and bottom edges.
    x = max(6, width // 100)
    y = max(6, height - text_height - max(8, height // 100))

    # Subtle shadow for visibility on light backgrounds.
    draw.text(
        (x + 1, y - bbox[1] + 1),
        text,
        font=font,
        fill=(0, 0, 0, 90)
    )

    # Small italic contributor name, more visible.
    draw.text(
        (x, y - bbox[1]),
        text,
        font=font,
        fill=(255, 255, 255, 160)
    )

    result = Image.alpha_composite(image, overlay)

    output = BytesIO()

    result.convert("RGB").save(
        output,
        format="JPEG",
        quality=90,
        optimize=True
    )

    return output.getvalue()



def create_guest_thumbnail(
    image_bytes: bytes,
    contributor_name: str,
) -> bytes:
    """
    Create a lightweight, watermarked guest thumbnail.

    Original S3 photograph remains unchanged.
    """

    if not contributor_name or not contributor_name.strip():
        raise ValueError("Contributor name is required.")

    with Image.open(BytesIO(image_bytes)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")

        # Resize before watermarking to reduce processing cost.
        image.thumbnail((480, 480))

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=82)

    return add_contributor_watermark(
        buffer.getvalue(),
        contributor_name
    )
