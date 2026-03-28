import json
import logging
from pathlib import Path

from google.cloud import storage, firestore
from google import genai
from google.genai import types

# ==============================
# 🔧 CONFIGURAÇÕES GERAIS
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"
LOCATION = "us-central1"

BUCKET_NAME = "clothes_app"

GEMINI_MODEL = "gemini-2.5-flash-lite"

# ==============================
# 🪵 RASTREAMENTO E LOGS
# ==============================
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ==============================
# ☁️ CLIENTES DO GOOGLE CLOUD
# ==============================
storage_client = storage.Client(project=GCP_PROJECT_ID)
firestore_client = firestore.Client(project=GCP_PROJECT_ID)

try:
    genai_client = genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=LOCATION)
    logger.info("✅ Cliente do Vertex AI / GenAI inicializado com sucesso.")
except Exception as e:
    logger.error(f"❌ Não foi possível inicializar o cliente do GenAI: {e}")
    genai_client = None


# ==============================
# 🔧 FUNÇÕES AUXILIARES
# ==============================
def parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Esperava um URI do tipo gs://, mas recebi: {uri}")
    bucket, blob = uri[5:].split("/", 1)
    return bucket, blob


def load_prompt(path: str = "prompts/describe_garment.txt") -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Arquivo de prompt não encontrado no caminho: {path}")


def firestore_doc_exists(collection: str, doc_id: str) -> bool:
    """Verifica se um documento já existe em uma determinada coleção do Firestore."""
    return firestore_client.collection(collection).document(doc_id).get().exists


# ==============================
# 🤖 IMAGEM ÚNICA → FIRESTORE
# ==============================
def describe_and_store_garment(
        image_gcs_uri: str,
        prompt: str,
        doc_id: str,
        image_type: str,
        collection_name: str,
        owner_name: str | None = None,
) -> dict | None:
    """
    Faz o download de uma única imagem do GCS, pede para o modelo Gemini descrevê-la
    e, em seguida, salva ou atualiza o resultado em um documento do Firestore.
    """
    if not genai_client:
        logger.error("O cliente GenAI não está disponível no momento.")
        return None

    # Passo 1: Fazer download dos bytes da imagem direto do GCS
    img_bucket_name, img_blob_name = parse_gcs_uri(image_gcs_uri)
    image_bytes = (
        storage_client
        .bucket(img_bucket_name)
        .blob(img_blob_name)
        .download_as_bytes()
    )

    # Passo 2: Chamar a IA do Gemini para analisar a imagem
    response = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        ),
    )

    # Passo 3: Decodificar o JSON gerado pela IA
    try:
        garment_data = json.loads(response.text)
    except json.JSONDecodeError:
        logger.error(f"❌ A IA Gemini devolveu um JSON inválido para a imagem {image_gcs_uri}")
        logger.debug(f"A resposta bruta foi: {response.text}")
        return None

    # Passo 4: Montar o documento final para salvar no banco de dados
    document = {
        "owner_name": owner_name,
        "image_gcs_uri": image_gcs_uri,
        "doc_id": doc_id,
        "image_type": image_type,  # Exemplo: pode ser "master" (referência) ou "photo" (consulta)
        **garment_data,  # Expande todos os campos detalhados através do Gemini
    }

    # Passo 5: Salvar os dados de forma dinâmica no Firestore
    firestore_client \
        .collection(collection_name) \
        .document(doc_id) \
        .set(document, merge=True)

    return document


# ==============================
# 🔁 PIPELINE EM LOTE
# ==============================
def bulk_describe_garments(
        bucket_name: str,
        input_prefix: str,
        image_type: str,
        collection_name: str,
        owner_name: str | None = None,
        skip_existing: bool = True,
) -> dict:
    """
    Passa por cada uma das imagens num determinado diretório do storage,
    pede a descrição visual para a IA do Gemini e grava tudo no Firestore.
    """
    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=input_prefix))
    prompt = load_prompt()

    results = {"success": [], "skipped": [], "failed": []}
    total = 0

    for blob in blobs:
        # Pula se for apenas uma pasta (o nome termina com barra)
        if blob.name.endswith("/"):
            continue

        doc_id = Path(blob.name).stem  # Extrai somente o nome do arquivo, ex: "abc123"
        input_uri = f"gs://{bucket_name}/{blob.name}"
        total += 1

        # Pular esta imagem se já estiver armazenada no Firestore
        if skip_existing and firestore_doc_exists(collection_name, doc_id):
            logger.info(f"⏭️  Ignorado (já consta na coleção {collection_name}): {doc_id}")
            results["skipped"].append(doc_id)
            continue

        logger.info(f"🔍 [{total}] Analisando a imagem: {blob.name}")

        try:
            result = describe_and_store_garment(
                image_gcs_uri=input_uri,
                prompt=prompt,
                doc_id=doc_id,
                image_type=image_type,
                collection_name=collection_name,
                owner_name=owner_name,
            )
            if result:
                logger.info(f"✅ Salvo com sucesso: {collection_name}/{doc_id}")
                results["success"].append(doc_id)
            else:
                results["failed"].append(doc_id)

        except Exception as e:
            logger.error(f"❌ Deu erro na imagem {blob.name}: {e}")
            results["failed"].append(doc_id)

    logger.info(
        f"\n📊 Análise em lote concluída para as imagens de tipo: {image_type}.\n"
        f"   ✅ Sucessos  : {len(results['success'])}\n"
        f"   ⏭️  Ignorados : {len(results['skipped'])}\n"
        f"   ❌ Falhas    : {len(results['failed'])}\n"
        f"   📦 Total     : {total}"
    )
    return results


# ==============================
# 🚀 PONTO DE ENTRADA PRINCIPAL
# ==============================
if __name__ == "__main__":
    # --- Passo 1: Processar as Imagens de Referência (Mestre) ---
    logger.info("🚀 Iniciando o processamento do lote de imagens MESTRE...")
    bulk_describe_garments(
        bucket_name=BUCKET_NAME,
        input_prefix="image_clean/processed_image_master/",  # Certifique-se de que a pasta no GCS tem esse nome
        image_type="master",
        collection_name="garments_master",
        owner_name=None,
        skip_existing=True,
    )

    # --- Passo 2: Processar Fotos Tiradas pelos Usuários ---
    logger.info("🚀 Iniciando o processamento do lote de FOTOS TIRADAS...")
    bulk_describe_garments(
        bucket_name=BUCKET_NAME,
        input_prefix="image_input/",  # Certifique-se de que a pasta no GCS tem esse nome
        image_type="photo",
        collection_name="garments_taken_photos",
        owner_name=None,
        skip_existing=True,
    )