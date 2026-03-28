import json
import logging
from pathlib import Path

from google.cloud import storage, firestore
from google import genai
from google.genai import types

# ==============================
# 🔧 CONFIGURAÇÕES GERAIS
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud
LOCATION = "us-central1"                 # Região onde o Vertex AI está hospedado

BUCKET_NAME = "clothes_app"              # Nome do bucket no Google Cloud Storage

GEMINI_MODEL = "gemini-2.5-flash-lite"  # Modelo de IA do Gemini a ser utilizado

# ==============================
# 🪵 RASTREAMENTO E LOGS
# ==============================
# Configura o logging para exibir mensagens informativas no terminal
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# ==============================
# ☁️ CLIENTES DO GOOGLE CLOUD
# ==============================
# Inicializa o cliente do Google Cloud Storage para acessar os buckets
storage_client = storage.Client(project=GCP_PROJECT_ID)

# Inicializa o cliente do Firestore para acessar o banco de dados NoSQL
firestore_client = firestore.Client(project=GCP_PROJECT_ID)

try:
    # Tenta inicializar o cliente do Vertex AI / GenAI para usar os modelos de IA
    genai_client = genai.Client(vertexai=True, project=GCP_PROJECT_ID, location=LOCATION)
    logger.info("✅ Cliente do Vertex AI / GenAI inicializado com sucesso.")
except Exception as e:
    # Se falhar, registra o erro e define o cliente como None para tratar depois
    logger.error(f"❌ Não foi possível inicializar o cliente do GenAI: {e}")
    genai_client = None


# ==============================
# 🔧 FUNÇÕES AUXILIARES
# ==============================
def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """
    Faz o parsing de um URI do Google Cloud Storage (gs://).
    Retorna uma tupla com (nome_do_bucket, caminho_do_blob).
    Exemplo: 'gs://meu-bucket/pasta/arquivo.jpg' → ('meu-bucket', 'pasta/arquivo.jpg')
    """
    if not uri.startswith("gs://"):
        raise ValueError(f"Esperava um URI do tipo gs://, mas recebi: {uri}")
    bucket, blob = uri[5:].split("/", 1)
    return bucket, blob


def load_prompt(path: str = "prompts/describe_garment.txt") -> str:
    """
    Carrega o prompt de descrição de peças de roupa a partir de um arquivo de texto.
    O prompt instrui o modelo de IA sobre como analisar e descrever as imagens.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        raise FileNotFoundError(f"Arquivo de prompt não encontrado no caminho: {path}")


def firestore_doc_exists(collection: str, doc_id: str) -> bool:
    """
    Verifica se um documento já existe em uma coleção específica do Firestore.
    Útil para evitar reprocessar imagens que já foram analisadas anteriormente.
    """
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
    Processa uma única imagem: baixa do GCS, envia para análise do Gemini
    e salva os atributos extraídos no Firestore.

    Parâmetros:
        image_gcs_uri:   URI do tipo gs:// da imagem no Cloud Storage
        prompt:          Texto instrucional para o modelo de IA analisar a peça
        doc_id:          Identificador único do documento no Firestore
        image_type:      Tipo da imagem ('master' = referência, 'photo' = consulta)
        collection_name: Nome da coleção no Firestore onde salvar o resultado
        owner_name:      Nome do dono da peça (opcional)

    Retorno:
        Dicionário com os dados extraídos, ou None em caso de erro
    """
    # Verifica se o cliente de IA está disponível antes de prosseguir
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
    # Enviamos os bytes da imagem junto com o prompt de instrução
    response = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
            prompt,  # Instrução de como descrever a peça
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",  # Pedimos resposta em formato JSON
            temperature=0.0,  # Temperatura 0 = respostas mais determinísticas e precisas
        ),
    )

    # Passo 3: Decodificar o JSON gerado pela IA
    try:
        garment_data = json.loads(response.text)
    except json.JSONDecodeError:
        # Se a IA retornar algo que não é JSON válido, registramos o erro
        logger.error(f"❌ A IA Gemini devolveu um JSON inválido para a imagem {image_gcs_uri}")
        logger.debug(f"A resposta bruta foi: {response.text}")
        return None

    # Passo 4: Montar o documento final para salvar no banco de dados
    # Combinamos os metadados com os campos extraídos pela IA
    document = {
        "owner_name": owner_name,          # Dono da peça (pode ser None)
        "image_gcs_uri": image_gcs_uri,    # URI original da imagem no Cloud Storage
        "doc_id": doc_id,                  # ID do documento para referência cruzada
        "image_type": image_type,          # Tipo: "master" (referência) ou "photo" (consulta)
        **garment_data,                    # Expande todos os campos retornados pela IA
    }

    # Passo 5: Salvar os dados no Firestore com merge=True para não sobrescrever campos existentes
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
    Processa em lote todas as imagens de uma pasta no GCS:
    analisa cada uma com o Gemini e salva os resultados no Firestore.

    Parâmetros:
        bucket_name:      Nome do bucket do GCS onde estão as imagens
        input_prefix:     Prefixo (pasta) dentro do bucket para listar as imagens
        image_type:       Tipo das imagens ('master' ou 'photo')
        collection_name:  Coleção do Firestore onde salvar os resultados
        owner_name:       Nome do dono das peças (opcional)
        skip_existing:    Se True, pula imagens já processadas e salvas no Firestore

    Retorno:
        Dicionário com as listas de sucessos, ignorados e falhas
    """
    bucket = storage_client.bucket(bucket_name)
    blobs = list(bucket.list_blobs(prefix=input_prefix))  # Lista todos os arquivos no prefixo
    prompt = load_prompt()  # Carrega as instruções para o modelo de IA

    # Dicionário para acompanhar os resultados do processamento em lote
    results = {"success": [], "skipped": [], "failed": []}
    total = 0

    for blob in blobs:
        # Ignora entradas que representam "pastas" (nomes terminando com barra)
        if blob.name.endswith("/"):
            continue

        doc_id = Path(blob.name).stem  # Extrai o nome do arquivo sem extensão como ID
        input_uri = f"gs://{bucket_name}/{blob.name}"
        total += 1

        # Verifica se essa imagem já foi processada e salva no Firestore
        if skip_existing and firestore_doc_exists(collection_name, doc_id):
            logger.info(f"⏭️  Ignorado (já consta na coleção {collection_name}): {doc_id}")
            results["skipped"].append(doc_id)
            continue

        logger.info(f"🔍 [{total}] Analisando a imagem: {blob.name}")

        try:
            # Processa a imagem: analisa com IA e salva no Firestore
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
                # Retorno vazio indica falha silenciosa na função de análise
                results["failed"].append(doc_id)

        except Exception as e:
            logger.error(f"❌ Deu erro na imagem {blob.name}: {e}")
            results["failed"].append(doc_id)

    # Exibe o resumo final do processamento em lote
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
    # As imagens mestre são as peças cadastradas que servem como referência para comparação
    logger.info("🚀 Iniciando o processamento do lote de imagens MESTRE...")
    bulk_describe_garments(
        bucket_name=BUCKET_NAME,
        input_prefix="image_clean/processed_image_master/",  # Pasta das imagens mestres limpas
        image_type="master",
        collection_name="garments_master",  # Coleção de referências no Firestore
        owner_name=None,
        skip_existing=True,  # Não reprocessa imagens já salvas
    )

    # --- Passo 2: Processar Fotos Tiradas pelos Usuários ---
    # As fotos de input são imagens capturadas para identificar a qual peça pertencem
    logger.info("🚀 Iniciando o processamento do lote de FOTOS TIRADAS...")
    bulk_describe_garments(
        bucket_name=BUCKET_NAME,
        input_prefix="image_input/",  # Pasta das fotos enviadas pelos usuários
        image_type="photo",
        collection_name="garments_taken_photos",  # Coleção de consultas no Firestore
        owner_name=None,
        skip_existing=True,  # Não reprocessa fotos já analisadas
    )
