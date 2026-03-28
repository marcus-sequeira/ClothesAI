import json
import hashlib
import logging
import io
from pathlib import Path
from typing import List
from PIL import Image, ImageTk
from google.cloud import storage

# ==============================
# 🔧 CONFIGURATION
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"
BUCKET_NAME = "clothes_app"

# Local directories
MASTER_FOLDER = "images/image_database/master"
INPUT_FOLDER = "images/image_database/input_queries"

# Processing settings
MEDIUM_SIZE = (1024, 1024)       # Max width/height for resizing
IMAGE_DISPLAY_SIZE = (320, 320)  # Size for UI preview

# ==============================
# 🪵 LOGGING
# ==============================
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# ==============================
# 🔐 HASHING & NAMING
# ==============================
def get_bytes_md5(data: bytes) -> str:
    """Calculates MD5 hash from bytes in memory."""
    return hashlib.md5(data).hexdigest()


def get_hashed_blob_name(original_ext: str, file_hash: str, remote_prefix: str = "") -> str:
    """Constructs the remote path using the hash and original extension."""
    return f"{remote_prefix}/{file_hash}{original_ext}".replace("\\", "/")


# ==============================
# 📤 CORE UPLOAD LOGIC
# ==============================
def bulk_upload_to_bucket(
        client: storage.Client,        # ← now an explicit parameter
        local_directory: str | Path,
        bucket_name: str,
        remote_prefix: str = "",
) -> List[str]:
    """
    Resizes images to medium, hashes the result, and uploads to GCS.

    Args:
        client:           An initialised google.cloud.storage.Client.
        local_directory:  Local folder whose images will be uploaded.
        bucket_name:      Target GCS bucket.
        remote_prefix:    Prefix (folder) inside the bucket, e.g. "image_master".

    Returns:
        List of gs:// URIs for every image that was processed.
    """
    bucket = client.bucket(bucket_name)
    local_path = Path(local_directory)
    valid_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
    uploaded_uris = []

    if not local_path.exists():
        logger.warning(f"⚠️ Directory not found: {local_path}")
        return []

    logger.info(f"🚀 Processing & Uploading from {local_directory} → {remote_prefix}")

    for file_path in local_path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in valid_extensions:
            try:
                # 1. Process & Resize image in memory
                with Image.open(file_path) as img:
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    img.thumbnail(MEDIUM_SIZE, Image.Resampling.LANCZOS)

                    # 2. Save resized image to a buffer
                    temp_buffer = io.BytesIO()
                    img.save(temp_buffer, format="JPEG", quality=85)
                    resized_bytes = temp_buffer.getvalue()

                # 3. Hash the resized bytes for the filename
                file_hash = get_bytes_md5(resized_bytes)
                blob_path = get_hashed_blob_name(".jpg", file_hash, remote_prefix)
                blob = bucket.blob(blob_path)
                gcs_uri = f"gs://{bucket_name}/{blob_path}"

                # 4. Skip duplicates, otherwise upload
                if blob.exists():
                    logger.info(f"⏭️  Skipped (exists): {file_path.name}")
                else:
                    logger.info(f"☁️  Uploading: {file_path.name} → {blob_path}")
                    blob.upload_from_string(resized_bytes, content_type="image/jpeg")

                uploaded_uris.append(gcs_uri)

            except Exception as e:
                logger.error(f"❌ Error processing {file_path.name}: {e}")

    return uploaded_uris


# ==============================
# 📥 GCS UTILITIES
# ==============================
def load_json_from_bucket(client: storage.Client, bucket_name: str, blob_path: str):
    """Downloads and parses a JSON blob from GCS. Returns None if not found."""
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return None
        return json.loads(blob.download_as_text())
    except Exception as e:
        logger.error(f"❌ Failed to load JSON: {e}")
        return None


def load_image_from_gcs(client: storage.Client, uri: str):
    """Downloads an image from GCS and returns a PhotoImage for Tkinter."""
    try:
        parts = uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])

        image_bytes = blob.download_as_bytes()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize(IMAGE_DISPLAY_SIZE)

        return ImageTk.PhotoImage(img)
    except Exception as e:
        logger.error(f"❌ Image load error: {uri} → {e}")
        return None


# ==============================
# 🚀 MAIN EXECUTION
# ==============================
if __name__ == "__main__":
    Path(MASTER_FOLDER).mkdir(parents=True, exist_ok=True)
    Path(INPUT_FOLDER).mkdir(parents=True, exist_ok=True)

    gcs_client = storage.Client(project=GCP_PROJECT_ID)

    try:
        master_uris = bulk_upload_to_bucket(gcs_client, MASTER_FOLDER, BUCKET_NAME, "image_master")
        input_uris  = bulk_upload_to_bucket(gcs_client, INPUT_FOLDER,  BUCKET_NAME, "image_input")

        logger.info(
            f"✅ Finished. Processed {len(master_uris)} master "
            f"and {len(input_uris)} input images."
        )
    except Exception as e:
        logger.critical(f"💥 Pipeline Failed: {e}")