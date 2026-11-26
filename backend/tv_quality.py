
"""TAM Live TV basic photograph quality screening."""

from PIL import Image, ImageOps, ImageStat, ImageFilter
from io import BytesIO

MAX_IMAGE_PIXELS = 60_000_000

MIN_LONG_EDGE = 640
MIN_SHORT_EDGE = 360

PROJECTION_SIZE = (1920, 1080)


def screen_photo(image_bytes):

    try:

        with Image.open(BytesIO(image_bytes)) as source:

            if source.width * source.height > MAX_IMAGE_PIXELS:
                return {
                    "passed": False,
                    "reason": "Image exceeds processing limit",
                    "score": 0
                }

            image = ImageOps.exif_transpose(source).convert("RGB")

            width, height = image.size

            if (
                max(width, height) < MIN_LONG_EDGE
                or min(width, height) < MIN_SHORT_EDGE
            ):
                return {
                    "passed": False,
                    "reason": "Low resolution",
                    "score": 0
                }

            sample = image.copy()
            sample.thumbnail((480, 480))

            gray = ImageOps.grayscale(sample)

            brightness = ImageStat.Stat(gray).mean[0]

            histogram = gray.histogram()

            total = sum(histogram)

            dark_fraction = sum(histogram[:20]) / total

            bright_fraction = sum(histogram[245:]) / total

            # Exclude artificial edges at the image boundary.

            edges = gray.filter(ImageFilter.FIND_EDGES)

            if edges.width > 4 and edges.height > 4:
                edges = edges.crop((
                    2, 2,
                    edges.width - 2,
                    edges.height - 2
                ))

            sharpness = ImageStat.Stat(edges).stddev[0]

            reasons = []

            if brightness < 28 or dark_fraction > 0.90:
                reasons.append("Excessively dark")

            if brightness > 235 or bright_fraction > 0.90:
                reasons.append("Excessively bright")

            if sharpness < 4:
                reasons.append("Insufficient visible detail")

            score = round(
                max(0, min(
                    100,
                    100 - len(reasons) * 35
                )),
                2
            )

            return {
                "passed": not reasons,
                "reason": "; ".join(reasons) if reasons else "Passed",
                "score": score,
                "width": width,
                "height": height,
                "brightness": round(brightness, 2),
                "sharpness": round(sharpness, 2)
            }

    except Exception:

        return {
            "passed": False,
            "reason": "Unreadable or unsupported photograph",
            "score": 0
        }


def create_projection_image(image_bytes):

    with Image.open(BytesIO(image_bytes)) as source:

        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds processing limit")

        image = ImageOps.exif_transpose(source).convert("RGB")

        image.thumbnail(
            PROJECTION_SIZE,
            Image.Resampling.LANCZOS
        )

        output = BytesIO()

        image.save(
            output,
            format="JPEG",
            quality=88,
            optimize=True
        )

        return output.getvalue()
