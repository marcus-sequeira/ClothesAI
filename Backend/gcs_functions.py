import json
import hashlib
import logging
import io
from pathlib import Path
from typing import List
from PIL import Image, ImageTk
from google.cloud import storage

# ==============================
# 🔧 CONFIGURAÇÃO
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud
BUCKET_NAME = "clothes_app"              # Nome do bucket principal no GCS

# Diretórios locais para as imagens antes do upload
MASTER_FOLDER = "images/image_database/master"        # Pasta local das imagens de referência
INPUT_FOLDER = "images/image_database/input_queries"  # Pasta local das fotos de consulta

# Configurações de processamento de imagem
MEDIUM_SIZE = (1024, 1024)       # Tamanho máximo para redimensionamento (preserva proporção)
IMAGE_DISPLAY_SIZE = (320, 320)  # Tamanho para prévia na interface gráfica

# ==============================
# 🪵 LOGS
# ==============================
# Configura o sistema de logging para exibir mensagens no terminal
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


# ==============================
# 🔐 HASH E NOMENCLATURA
# ==============================
def get_bytes_md5(data: bytes) -> str:
    """
    Calcula o hash MD5 de um conteúdo em bytes na memória.
    Usado para gerar nomes únicos e identificar duplicatas antes do upload.
    """
    return hashlib.md5(data).hexdigest()


def get_hashed_blob_name(original_ext: str, file_hash: str, remote_prefix: str = "") -> str:
    """
    Constrói o caminho remoto no GCS usando o hash do arquivo e sua extensão original.
    Exemplo: get_hashed_blob_name('.jpg', 'abc123', 'image_master') → 'image_master/abc123.jpg'
    """
    return f"{remote_prefix}/{file_hash}{original_ext}".replace("\\", "/")


# ==============================
# 📤 LÓGICA PRINCIPAL DE UPLOAD
# ==============================
def bulk_upload_to_bucket(
        client: storage.Client,        # Cliente do GCS (passado como parâmetro para reutilização)
        local_directory: str | Path,   # Pasta local com as imagens a enviar
        bucket_name: str,              # Nome do bucket destino no GCS
        remote_prefix: str = "",       # Prefixo (subpasta) dentro do bucket
) -> List[str]:
    """
    Redimensiona as imagens locais, gera um hash para cada uma e faz upload para o GCS.
    Imagens duplicadas (mesmo hash) são ignoradas automaticamente.

    Parâmetros:
        client:           Cliente inicializado do google.cloud.storage
        local_directory:  Pasta local cujas imagens serão enviadas
        bucket_name:      Bucket GCS de destino
        remote_prefix:    Pasta dentro do bucket (ex: "image_master")

    Retorno:
        Lista de URIs gs:// para cada imagem processada (nova ou já existente)
    """
    bucket = client.bucket(bucket_name)
    local_path = Path(local_directory)

    # Apenas extensões de imagem suportadas serão processadas
    valid_extensions = {'.jpg', '.jpeg', '.png', '.webp'}
    uploaded_uris = []

    # Verifica se o diretório local existe antes de tentar processar
    if not local_path.exists():
        logger.warning(f"⚠️ Diretório não encontrado: {local_path}")
        return []

    logger.info(f"🚀 Processando e enviando de {local_directory} → {remote_prefix}")

    # Percorre recursivamente todos os arquivos na pasta local
    for file_path in local_path.rglob("*"):
        if file_path.is_file() and file_path.suffix.lower() in valid_extensions:
            try:
                # 1. Abre e redimensiona a imagem na memória (sem salvar em disco)
                with Image.open(file_path) as img:
                    # Converte formatos com transparência (RGBA, paleta) para RGB
                    if img.mode in ("RGBA", "P"):
                        img = img.convert("RGB")
                    # Redimensiona mantendo proporção, sem ultrapassar MEDIUM_SIZE
                    img.thumbnail(MEDIUM_SIZE, Image.Resampling.LANCZOS)

                    # 2. Salva a imagem redimensionada num buffer em memória (sem disco)
                    temp_buffer = io.BytesIO()
                    img.save(temp_buffer, format="JPEG", quality=85)
                    resized_bytes = temp_buffer.getvalue()

                # 3. Gera o hash MD5 dos bytes redimensionados para usar como nome do arquivo
                # Isso garante que imagens idênticas nunca sejam enviadas duas vezes
                file_hash = get_bytes_md5(resized_bytes)
                blob_path = get_hashed_blob_name(".jpg", file_hash, remote_prefix)
                blob = bucket.blob(blob_path)
                gcs_uri = f"gs://{bucket_name}/{blob_path}"

                # 4. Verifica se o arquivo já existe no GCS antes de enviar (evita duplicatas)
                if blob.exists():
                    logger.info(f"⏭️  Ignorado (já existe): {file_path.name}")
                else:
                    logger.info(f"☁️  Enviando: {file_path.name} → {blob_path}")
                    blob.upload_from_string(resized_bytes, content_type="image/jpeg")

                # Adiciona o URI à lista de processados (seja novo ou já existente)
                uploaded_uris.append(gcs_uri)

            except Exception as e:
                logger.error(f"❌ Erro ao processar {file_path.name}: {e}")

    return uploaded_uris


# ==============================
# 📥 UTILITÁRIOS DO GCS
# ==============================
def load_json_from_bucket(client: storage.Client, bucket_name: str, blob_path: str):
    """
    Faz download e faz o parsing de um arquivo JSON armazenado no GCS.
    Retorna None se o arquivo não for encontrado ou se ocorrer algum erro.
    """
    try:
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        if not blob.exists():
            return None  # Arquivo não encontrado no bucket
        return json.loads(blob.download_as_text())
    except Exception as e:
        logger.error(f"❌ Falha ao carregar JSON: {e}")
        return None


def load_image_from_gcs(client: storage.Client, uri: str):
    """
    Faz download de uma imagem do GCS e retorna um objeto PhotoImage compatível com Tkinter.
    Utilizado para exibir imagens na interface gráfica do visualizador.

    Retorna None se ocorrer qualquer erro durante o download ou processamento.
    """
    try:
        # Extrai o nome do bucket e o caminho do blob a partir do URI gs://
        parts = uri.replace("gs://", "").split("/", 1)
        bucket = client.bucket(parts[0])
        blob = bucket.blob(parts[1])

        # Faz download dos bytes da imagem e abre com PIL
        image_bytes = blob.download_as_bytes()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        img = img.resize(IMAGE_DISPLAY_SIZE)  # Redimensiona para o tamanho de exibição

        # Converte para o formato que o Tkinter entende
        return ImageTk.PhotoImage(img)
    except Exception as e:
        logger.error(f"❌ Erro ao carregar imagem: {uri} → {e}")
        return None


# ==============================
# 🚀 EXECUÇÃO PRINCIPAL
# ==============================
if __name__ == "__main__":
    # Garante que os diretórios locais existam antes de tentar processar
    Path(MASTER_FOLDER).mkdir(parents=True, exist_ok=True)
    Path(INPUT_FOLDER).mkdir(parents=True, exist_ok=True)

    # Inicializa o cliente do GCS
    gcs_client = storage.Client(project=GCP_PROJECT_ID)

    try:
        # Envia as imagens de referência (mestre) para o bucket no GCS
        master_uris = bulk_upload_to_bucket(gcs_client, MASTER_FOLDER, BUCKET_NAME, "image_master")

        # Envia as imagens de consulta (input) para o bucket no GCS
        input_uris = bulk_upload_to_bucket(gcs_client, INPUT_FOLDER, BUCKET_NAME, "image_input")

        logger.info(
            f"✅ Concluído. Processadas {len(master_uris)} imagens mestre "
            f"e {len(input_uris)} imagens de consulta."
        )
    except Exception as e:
        logger.critical(f"💥 O Pipeline falhou: {e}")
