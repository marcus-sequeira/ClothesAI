import logging
import os
from pathlib import Path
from google.cloud import storage
import io
import hashlib
from PIL import Image

# Nossos próprios módulos
from removeClothingBackground import isolate_clothing_in_gcs
from gcs_functions import bulk_upload_to_bucket
from garment_analyzer import bulk_describe_garments
from comparison_functions import fetch_collection_data, compare_all, save_results_to_firestore
from viewer import main as viewer_gui

# ==============================
# 🔧 CONFIGURAÇÕES GERAIS
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"
BUCKET_NAME = "clothes_app"

MASTER_FOLDER = "images/image_database/master"
GCS_INPUT_PREFIX = "image_input/"
GCS_OUTPUT_PREFIX = "image_clean/processed_image_input/"

# ==============================
# 🪵 RASTREAMENTO E LOGS
# ==============================
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_bytes_md5(data: bytes) -> str:
    """Calcula o hash MD5 a partir de bytes em memória."""
    return hashlib.md5(data).hexdigest()


def upload_single_image(client: storage.Client, local_path: str, bucket_name: str, prefix: str) -> str:
    """
    Processa, redimensiona, gera um hash e envia um único arquivo para o GCS.
    Retorna o nome final do arquivo que foi salvo na nuvem (ex: 'image_input/12efa40...1ac.jpg').
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Arquivo local não encontrado no caminho: {local_path}")

    bucket = client.bucket(bucket_name)
    MEDIUM_SIZE = (1024, 1024)

    # Passo 1: Processar e redimensionar a imagem ainda na memória
    with Image.open(local_path) as img:
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.thumbnail(MEDIUM_SIZE, Image.Resampling.LANCZOS)

        # Passo 2: Guarda a versão redimensionada em um buffer
        temp_buffer = io.BytesIO()
        img.save(temp_buffer, format="JPEG", quality=85)
        resized_bytes = temp_buffer.getvalue()

    # Passo 3: Criar um ID único baseado nos dados da própria imagem para evitar sobreposição
    file_hash = get_bytes_md5(resized_bytes)
    blob_name = f"{prefix}{file_hash}.jpg"
    blob = bucket.blob(blob_name)

    # Passo 4: Ignorar caso a imagem já exista na nuvem, ou seguir com o upload
    if blob.exists():
        logger.info(f"⏭️  Ignorado (já existe na nuvem): gs://{bucket_name}/{blob_name}")
    else:
        logger.info(f"☁️  Enviando: {os.path.basename(local_path)} → gs://{bucket_name}/{blob_name}")
        blob.upload_from_string(resized_bytes, content_type="image/jpeg")

    return blob_name

def process_single_image(client: storage.Client, bucket_name: str, input_blob_name: str, output_prefix: str) -> str:
    """
    Remove o fundo de uma imagem no GCS em específico.
    Retorna também a URL (URI) final da imagem já processada.
    """
    bucket = client.bucket(bucket_name)
    filename = os.path.basename(input_blob_name)
    input_uri = f"gs://{bucket_name}/{input_blob_name}"
    output_uri = f"gs://{bucket_name}/{output_prefix}{filename}"

    try:
        isolate_clothing_in_gcs(
            input_uri=input_uri,
            output_uri=output_uri,
            is_fuzzy_item=False,
            show_popup=False
        )
        logger.info(f"⭐ Fundo removido corretamente: {filename}")
        return output_uri
    except Exception as e:
        logger.error(f"⚠️ Não foi possível processar {filename}: {e}")
        return None


def identify_garment(local_image_path: str, sync_masters: bool = False, remove_bg: bool = True) -> dict:
    """
    Este é o ponto de entrada principal. Recebe uma imagem, envia pra base,
    retira fundo, processa, descreve e compara contra todos as referências.

    Parâmetros:
        local_image_path (str): O caminho no PC ou dispositivo para a imagem.
        sync_masters (bool): Decide se queremos ressincronizar os dados de referência.
        remove_bg (bool): Determina se vamos remover os fundos antes da IA descrever a foto.

    Retorno:
        dict: O resultado final detalhado ou vazio se acontecer algo errado no caminho.
    """
    client = storage.Client(project=GCP_PROJECT_ID)

    # (Opcional) Podemos sincronizar a pasta master apenas quando solicitado
    if sync_masters:
        logger.info("📤 Sincronizando o banco de imagens mestre...")
        Path(MASTER_FOLDER).mkdir(parents=True, exist_ok=True)
        bulk_upload_to_bucket(client, MASTER_FOLDER, BUCKET_NAME, "image_master")

    # 1️⃣ Subir a nossa imagem de requisição
    blob_name = upload_single_image(client, local_image_path, BUCKET_NAME, GCS_INPUT_PREFIX)
    selected_filename = os.path.basename(blob_name)
    selected_name_no_ext = os.path.splitext(selected_filename)[0]

    # 2️⃣ Remover Fundo (se escolhido)
    if remove_bg:
        processed_uri = process_single_image(client, BUCKET_NAME, blob_name, GCS_OUTPUT_PREFIX)
        if not processed_uri:
            logger.critical("Houve uma falha fatal no processamento, retornando respostas vazias...")
            return {}
        describe_prefix = f"{GCS_OUTPUT_PREFIX}{selected_filename}"
    else:
        logger.info("⏭️  Pulando a remoção de fundos das fotos (remove_bg=False).")
        describe_prefix = blob_name  # Usar a imagem intocada original

    # 3️⃣ Fazer a descrição usando a IA
    logger.info(f"🚀 Iniciando a descrição da imagem selecionada: {selected_filename}...")
    bulk_describe_garments(
        bucket_name=BUCKET_NAME,
        input_prefix=describe_prefix,
        image_type="photo",
        collection_name="garments_taken_photos",
        owner_name=None,
        skip_existing=False
    )

    # 4️⃣ Pegar todos os dados que já analisamos do Firestore
    master_data = fetch_collection_data("garments_master")
    all_input_data = fetch_collection_data("garments_taken_photos")

    if not master_data or not all_input_data:
        logger.warning("⚠️ Alguma informação está faltando nos bancos! Verifique o estado do Firestore.")
        return {}

    # Filtrar apenas os dados correspondentes a nossa requisição exata
    single_input_data = {
        k: v for k, v in all_input_data.items()
        if selected_name_no_ext in k
    }

    if not single_input_data:
        logger.warning(f"⚠️ Não encontrei o registro com nome base de arquivo: {selected_filename} nos bancos.")
        return {}

    # 5️⃣ Comparar essa imagem de consulta contra TODAS as imagens mestres registradas
    logger.info(f"🚀 Comparando {selected_filename} com as {len(master_data)} referências registradas...")
    final_results = compare_all(master_data, single_input_data)

    # Exportando num formato agradável para fácil entendimento de eventuais depurações locais
    logger.info("\n📊 RESULTADOS DA VERIFICAÇÃO DE SUA IMAGEM:")
    for photo_id, res in final_results.items():
        logger.info(f"🔹 Imagem pesquisada: {photo_id}")
        logger.info(f"🏆 Melhor chance com: {res.get('best_match', 'N/A')} ({res.get('best_score', 0)}%)")

        all_comps = res.get('all_comparisons', [])
        if all_comps:
            sorted_comps = sorted(all_comps, key=lambda x: x.get('score', 0), reverse=True)
            for comp in sorted_comps:
                logger.info(f"   -> {comp.get('master', 'Desconhecido')}: {comp.get('score', 0)}%")
        else:
            logger.info("   (⚠️ Detalhes insuficientes de processamento.)")

    # 6️⃣ Salvando resultados
    save_results_to_firestore(final_results, "garments_matches")

    # O objeto final do processo, que deve ser capturado pelo back-end em consumo (ex: Flask, React)
    return final_results


def import_garment(local_image_path: str, owner_name: str, remove_bg: bool = True) -> dict:
    """
    Importa a foto de uma peça e aloca isso como Mestre (referência final) pro sistema.
    Faz upload e tenta remover o fundo (se desejado), descreve ele e injeta os achados
    no DB (`garments_master`).

    Parâmetros:
        local_image_path (str): Caminho local do dispositivo do arquivo para envio.
        owner_name (str): Quem vai ser marcado como proprietário da peça?
        remove_bg (bool): Limpar plano de fundo da peça antes das etapas da IA.

    Retorno:
        dict: Se deu certo com o Doc_ID { "success": True, "doc_id": "..." } ou o erro correspondente.
    """
    client = storage.Client(project=GCP_PROJECT_ID)

    GCS_MASTER_PREFIX = "image_master/"
    GCS_CLEAN_MASTER_PREFIX = "image_clean/processed_image_master/"

    try:
        # 1️⃣ Upar o arquivo cru como um Mestre/Referência na respectiva pasta
        blob_name = upload_single_image(client, local_image_path, BUCKET_NAME, GCS_MASTER_PREFIX)
        master_filename = os.path.basename(blob_name)
        doc_id = os.path.splitext(master_filename)[0]

        logger.info(f"📤 Fez upload correto pro storage mestre: {blob_name}")

        # 2️⃣ Verificando Remoção de Fundo
        if remove_bg:
            processed_uri = process_single_image(client, BUCKET_NAME, blob_name, GCS_CLEAN_MASTER_PREFIX)
            if not processed_uri:
                return {"success": False, "error": "Infelizmente houve um colapso na etapa de remoção de fundo."}
            describe_prefix = f"{GCS_CLEAN_MASTER_PREFIX}{master_filename}"
        else:
            logger.info("⏭️  Sem necessidade de remoção de fundo. Usando diretamente o Mestre novo!")
            describe_prefix = blob_name

        # 3️⃣ Descrição através IA (Gemini) - Gerando e inserindo no banco 'mensageiro'
        logger.info(f"🚀 Extraindo conteúdo visual deste mestre: {master_filename}...")
        bulk_describe_garments(
            bucket_name=BUCKET_NAME,
            input_prefix=describe_prefix,
            image_type="master",
            collection_name="garments_master",
            owner_name=owner_name,
            skip_existing=False
        )

        logger.info(f"✅ Peça indexada! Identificador gerado={doc_id}, Pertence a={owner_name}")
        return {"success": True, "doc_id": doc_id}

    except Exception as e:
        logger.error(f"💥 Rolou falhas nessa importação global. Erro: {e}")
        return {"success": False, "error": str(e)}


# Se você chamar apenas este arquivo por linha de comando de modo isolado isso vai processar uma entrada de forma "fake".
if __name__ == '__main__':
    test_image_path = "images/image_database/input_queries/Captura de Tela 2026-03-25 às 16.10.02.png"
    results = identify_garment(test_image_path, sync_masters=False)
    print("\n--- INFORMAÇÕES QUE RETORNARAM E ESTÃO PRONTAS PRO AMBIENTE DE APP ---")
    print(results)