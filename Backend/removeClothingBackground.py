import io
import logging
from typing import Optional

from google.cloud import storage
from rembg import remove, new_session
from PIL import Image, ImageFilter

# ==============================
# 🪵 LOGGING
# ==============================
logger = logging.getLogger(__name__)

# ==============================
# 🧠 MODEL INIT (GLOBAL)
# ==============================
logger.info("🧠 Loading production model (ISNet)...")
session = new_session("isnet-general-use")


# ==============================
# ☁️ GCS HELPER
# ==============================
def parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Invalid GCS URI: {uri}")
    parts = uri[5:].split("/", 1)
    return parts[0], parts[1]


# ==============================
# ✨ MASK REFINEMENT (FIXED)
# ==============================
def refine_alpha(img: Image.Image) -> Image.Image:
    """
    Clean edges WITHOUT darkening the image.
    """
    r, g, b, a = img.split()

    # Smooth edges (safe)
    a = a.filter(ImageFilter.GaussianBlur(1))

    # Slight alpha boost (DOES NOT darken image)
    a = a.point(lambda p: min(255, int(p * 1.15)))

    return Image.merge("RGBA", (r, g, b, a))


# ==============================
# 📦 AUTO CROP + PADDING
# ==============================
def crop_and_center(img: Image.Image, padding_ratio=0.08) -> Image.Image:
    bbox = img.getbbox()
    if bbox is None:
        return img

    img = img.crop(bbox)

    w, h = img.size
    pad_w = int(w * padding_ratio)
    pad_h = int(h * padding_ratio)

    new_img = Image.new("RGBA", (w + pad_w * 2, h + pad_h * 2), (0, 0, 0, 0))
    new_img.paste(img, (pad_w, pad_h))

    return new_img


# ==============================
# 🧼 REMOVE TINY ARTIFACTS
# ==============================
def remove_small_islands(img: Image.Image, min_size=500):
    alpha = img.split()[-1]
    bbox = alpha.getbbox()

    if bbox is None:
        return None

    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    if area < min_size:
        return None

    return img


# ==============================
# 🎨 OPTIONAL WHITE BACKGROUND
# ==============================
def add_white_background(img: Image.Image) -> Image.Image:
    bg = Image.new("RGB", img.size, (255, 255, 255))
    bg.paste(img, mask=img.split()[-1])
    return bg


# ==============================
# ✂️ MAIN FUNCTION (FINAL)
# ==============================
def isolate_clothing_in_gcs(
    input_uri: str,
    output_uri: str,
    is_fuzzy_item: bool = False,   # kept for compatibility
    show_popup: bool = False,      # kept for compatibility
    make_white_bg: bool = False,
    client: Optional[storage.Client] = None
) -> None:

    if client is None:
        client = storage.Client()

    in_bucket, in_blob = parse_gcs_uri(input_uri)
    out_bucket, out_blob = parse_gcs_uri(output_uri)

    # ==============================
    # 1. DOWNLOAD
    # ==============================
    logger.debug(f"📥 Downloading: {input_uri}")
    input_bytes = client.bucket(in_bucket).blob(in_blob).download_as_bytes()

    # ==============================
    # 2. REMOVE BACKGROUND
    # ==============================
    logger.debug("🤖 Running background removal...")
    output_bytes = remove(
        input_bytes,
        session=session,
        alpha_matting=True
    )

    img = Image.open(io.BytesIO(output_bytes)).convert("RGBA")

    # ==============================
    # 3. REFINE (FIXED - NO DARKENING)
    # ==============================
    img = refine_alpha(img)

    # ==============================
    # 4. REMOVE ARTIFACTS
    # ==============================
    img = remove_small_islands(img)
    if img is None:
        logger.warning(f"⚠️ No valid clothing detected in {input_uri}")
        return

    # ==============================
    # 5. CROP + CENTER
    # ==============================
    img = crop_and_center(img)

    # ==============================
    # 6. OPTIONAL WHITE BG (IMPORTANT)
    # ==============================
    if make_white_bg:
        img = add_white_background(img)

    # ==============================
    # 7. SAVE
    # ==============================
    byte_io = io.BytesIO()

    if make_white_bg:
        img.save(byte_io, format="JPEG", quality=95, subsampling=0)
        content_type = "image/jpeg"
    else:
        img.save(byte_io, format="PNG", optimize=True)
        content_type = "image/png"

    if show_popup:
        img.show()

    # ==============================
    # 8. UPLOAD
    # ==============================
    logger.debug(f"📤 Uploading: {output_uri}")
    client.bucket(out_bucket).blob(out_blob).upload_from_string(
        byte_io.getvalue(),
        content_type=content_type
    )

    logger.debug("✅ Done.")