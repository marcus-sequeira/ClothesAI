import vertexai
from vertexai.preview.vision_models import Image, MultiModalEmbeddingModel
import numpy as np

# =========================
# CONFIGURAÇÃO
# =========================
PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud
LOCATION = "us-central1"             # Região do Vertex AI

# URIs das imagens de teste no Google Cloud Storage
IMAGE_1_URI = "gs://clothes_app/image_input/4f3c6194314fbe9b58897a55e1543be4.png"   # Imagem de consulta
RIGHT = "gs://clothes_app/image_master/f6da395d6a5a8aab58498fec5fcbe92a.png"       # Imagem correta (deve ter alta similaridade)
WRONG = "gs://clothes_app/image_master/1ab770a4b3355bd91faa0d8589f886ba.png"       # Imagem errada (deve ter baixa similaridade)

# =========================
# INICIALIZAÇÃO
# =========================
# Inicializa o Vertex AI com o projeto e região configurados
vertexai.init(project=PROJECT_ID, location=LOCATION)

# Carrega o modelo de embeddings multimodal pré-treinado do Google
# Esse modelo converte imagens em vetores numéricos (embeddings) para comparação
model = MultiModalEmbeddingModel.from_pretrained("multimodalembedding@001")

# =========================
# GERAÇÃO DE EMBEDDING A PARTIR DO GCS
# =========================
def get_embedding_from_gcs(gcs_uri):
    """
    Carrega uma imagem direto do GCS usando o URI gs:// e gera seu vetor de embedding.
    O embedding é uma representação numérica da imagem que captura suas características visuais.

    Retorno:
        numpy array com o vetor de embedding da imagem
    """
    # Carrega a imagem diretamente do GCS (aceita URIs gs:// nativamente)
    image = Image.load_from_file(gcs_uri)

    # Gera o embedding visual da imagem usando o modelo multimodal
    embedding = model.get_embeddings(image=image).image_embedding

    # Converte para numpy array para facilitar operações matemáticas
    return np.array(embedding)

# =========================
# SIMILARIDADE DE COSSENO
# =========================
def cosine_similarity(a, b):
    """
    Calcula a similaridade de cosseno entre dois vetores de embedding.
    O resultado varia de -1 (completamente opostos) a 1 (idênticos).
    Valores próximos de 1 indicam imagens visualmente semelhantes.

    Parâmetros:
        a: primeiro vetor de embedding (numpy array)
        b: segundo vetor de embedding (numpy array)

    Retorno:
        float entre -1 e 1 representando a similaridade visual
    """
    # Fórmula: produto escalar dividido pelo produto das normas (magnitudes)
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))

# =========================
# EXECUÇÃO PRINCIPAL
# =========================
# Gera os embeddings para cada uma das três imagens
emb1 = get_embedding_from_gcs(IMAGE_1_URI)  # Embedding da imagem de consulta
emb2 = get_embedding_from_gcs(RIGHT)         # Embedding da imagem correta
emb3 = get_embedding_from_gcs(WRONG)         # Embedding da imagem errada

# Calcula a similaridade entre a imagem de consulta e a imagem correta
# Esperamos um valor ALTO (próximo de 1) indicando grande semelhança visual
score1 = cosine_similarity(emb1, emb2)
print(f"Similaridade com a imagem CORRETA: {score1:.4f}")

print("\n")

# Calcula a similaridade entre a imagem de consulta e a imagem errada
# Esperamos um valor BAIXO indicando pouca semelhança visual
score2 = cosine_similarity(emb1, emb3)
print(f"Similaridade com a imagem ERRADA: {score2:.4f}")
