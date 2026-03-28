import vertexai
from vertexai.preview.vision_models import Image, MultiModalEmbeddingModel
import numpy as np

# =========================
# CONFIG
# =========================
PROJECT_ID = "clothesidentifierapp"
LOCATION = "us-central1"

IMAGE_1_URI = "gs://clothes_app/image_input/4f3c6194314fbe9b58897a55e1543be4.png"
RIGHT = "gs://clothes_app/image_master/f6da395d6a5a8aab58498fec5fcbe92a.png"
WRONG = "gs://clothes_app/image_master/1ab770a4b3355bd91faa0d8589f886ba.png"

# =========================
# INIT
# =========================
vertexai.init(project=PROJECT_ID, location=LOCATION)

model = MultiModalEmbeddingModel.from_pretrained("multimodalembedding@001")

# =========================
# GET EMBEDDING FROM GCS
# =========================
def get_embedding_from_gcs(gcs_uri):
    image = Image.load_from_file(gcs_uri)  # 🔥 aceita gs:// direto
    embedding = model.get_embeddings(image=image).image_embedding
    return np.array(embedding)

# =========================
# COSINE SIMILARITY
# =========================
def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# =========================
# MAIN
# =========================
emb1 = get_embedding_from_gcs(IMAGE_1_URI)
emb2 = get_embedding_from_gcs(RIGHT)
emb3 = get_embedding_from_gcs(WRONG)

score1 = cosine_similarity(emb1, emb2)

print(f"Similarity of the right image: {score1:.4f}")

print("\n")

score2 = cosine_similarity(emb1, emb3)

print(f"Similarity of the wrong image: {score2:.4f}")
