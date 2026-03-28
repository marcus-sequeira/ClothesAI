import logging
import os
from pathlib import Path
from google.cloud import storage
import io
import hashlib
from PIL import Image

# Módulos internos do projeto
from removeClothingBackground import isolate_clothing_in_gcs
from gcs_functions import bulk_upload_to_bucket
from garment_analyzer import bulk_describe_garments
from comparison_functions import fetch_collection_data, compare_all, save_results_to_firestore
from viewer import main as viewer_gui

# ==============================
# 🔧 CONFIGURAÇÕES GERAIS
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud
BUCKET_NAME = "clothes_app"              # Bucket principal no GCS

MASTER_FOLDER = "images/image_database/master"   # Pasta local das imagens de referência

# Prefixos no GCS para organizar os arquivos por tipo
GCS_INPUT_PREFIX = "image_input/"                           # Fotos de consulta originais
GCS_OUTPUT_PREFIX = "image_clean/processed_image_input/"    # Fotos de consulta com fundo removido

# ==============================
# 🪵 RASTREAMENTO E LOGS
# ==============================
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def get_bytes_md5(data: bytes) -> str:
    """
    Calcula o hash MD5 de um conteúdo em bytes na memória.
    Usado para gerar nomes únicos de arquivos e detectar duplicatas.
    """
    return hashlib.md5(data).hexdigest()


def upload_single_image(client: storage.Client, local_path: str, bucket_name: str, prefix: str) -> str:
    """
    Processa, redimensiona e faz upload de um único arquivo de imagem para o GCS.
    O nome do arquivo no GCS é gerado a partir do hash MD5 dos bytes, garantindo unicidade.

    Parâmetros:
        client:       Cliente do GCS inicializado
        local_path:   Caminho local da imagem a enviar
        bucket_name:  Nome do bucket de destino no GCS
        prefix:       Prefixo (pasta) dentro do bucket

    Retorno:
        Nome do blob criado no GCS (ex: 'image_input/abc123def456.jpg')
    """
    if not os.path.exists(local_path):
        raise FileNotFoundError(f"Arquivo local não encontrado no caminho: {local_path}")

    bucket = client.bucket(bucket_name)
    MEDIUM_SIZE = (1024, 1024)  # Tamanho máximo para redimensionamento

    # Passo 1: Abre e redimensiona a imagem na memória (sem salvar em disco)
    with Image.open(local_path) as img:
        # Converte formatos com transparência ou paleta para RGB puro
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        # Redimensiona mantendo proporção, respeitando o tamanho máximo
        img.thumbnail(MEDIUM_SIZE, Image.Resampling.LANCZOS)

        # Passo 2: Guarda a imagem redimensionada em um buffer de memória
        temp_buffer = io.BytesIO()
        img.save(temp_buffer, format="JPEG", quality=85)
        resized_bytes = temp_buffer.getvalue()

    # Passo 3: Gera um ID único baseado nos bytes da imagem para evitar conflitos de nome
    file_hash = get_bytes_md5(resized_bytes)
    blob_name = f"{prefix}{file_hash}.jpg"
    blob = bucket.blob(blob_name)

    # Passo 4: Verifica se a imagem já existe no GCS para evitar reenvio desnecessário
    if blob.exists():
        logger.info(f"⏭️  Ignorado (já existe na nuvem): gs://{bucket_name}/{blob_name}")
    else:
        logger.info(f"☁️  Enviando: {os.path.basename(local_path)} → gs://{bucket_name}/{blob_name}")
        blob.upload_from_string(resized_bytes, content_type="image/jpeg")

    return blob_name  # Retorna o nome do blob (novo ou existente)

def process_single_image(client: storage.Client, bucket_name: str, input_blob_name: str, output_prefix: str) -> str:
    """
    Remove o fundo de uma imagem específica já armazenada no GCS.
    Salva a imagem processada (sem fundo) em uma pasta separada.

    Parâmetros:
        client:           Cliente do GCS
        bucket_name:      Nome do bucket no GCS
        input_blob_name:  Caminho do blob de entrada (dentro do bucket)
        output_prefix:    Prefixo da pasta de saída para a imagem limpa

    Retorno:
        URI gs:// da imagem processada, ou None em caso de falha
    """
    filename = os.path.basename(input_blob_name)               # Nome do arquivo sem o caminho
    input_uri = f"gs://{bucket_name}/{input_blob_name}"        # URI de entrada no GCS
    output_uri = f"gs://{bucket_name}/{output_prefix}{filename}"  # URI de saída no GCS

    try:
        # Executa a remoção de fundo usando o módulo especializado
        isolate_clothing_in_gcs(
            input_uri=input_uri,
            output_uri=output_uri,
            is_fuzzy_item=False,  # Não é um item com bordas difusas (ex: cabelo)
            show_popup=False      # Não exibe prévia durante o processamento
        )
        logger.info(f"⭐ Fundo removido corretamente: {filename}")
        return output_uri
    except Exception as e:
        logger.error(f"⚠️ Não foi possível processar {filename}: {e}")
        return None  # Retorna None para indicar falha


def identify_garment(local_image_path: str, sync_masters: bool = False, remove_bg: bool = True) -> dict:
    """
    Pipeline completo de identificação de peça de roupa a partir de uma imagem local.
    Faz upload da imagem, remove o fundo (opcional), analisa com IA e compara
    contra todas as peças cadastradas no banco de dados.

    Parâmetros:
        local_image_path (str): Caminho local da imagem a identificar
        sync_masters (bool):    Se True, sincroniza as imagens mestre antes da identificação
        remove_bg (bool):       Se True, remove o fundo da imagem antes da análise

    Retorno:
        dict: Resultados da comparação com as peças mestre, ou {} em caso de erro
    """
    client = storage.Client(project=GCP_PROJECT_ID)

    # (Opcional) Sincroniza as imagens mestre com o GCS quando solicitado
    if sync_masters:
        logger.info("📤 Sincronizando o banco de imagens mestre...")
        Path(MASTER_FOLDER).mkdir(parents=True, exist_ok=True)
        bulk_upload_to_bucket(client, MASTER_FOLDER, BUCKET_NAME, "image_master")

    # 1️⃣ Faz upload da imagem de consulta para o GCS
    blob_name = upload_single_image(client, local_image_path, BUCKET_NAME, GCS_INPUT_PREFIX)
    selected_filename = os.path.basename(blob_name)        # Ex: "abc123.jpg"
    selected_name_no_ext = os.path.splitext(selected_filename)[0]  # Ex: "abc123"

    # 2️⃣ Remove o fundo da imagem (se a opção estiver ativada)
    if remove_bg:
        processed_uri = process_single_image(client, BUCKET_NAME, blob_name, GCS_OUTPUT_PREFIX)
        if not processed_uri:
            # Falha crítica: sem a imagem processada, não há como continuar
            logger.critical("Houve uma falha fatal no processamento, retornando respostas vazias...")
            return {}
        # Usa o caminho da imagem limpa para a etapa de descrição
        describe_prefix = f"{GCS_OUTPUT_PREFIX}{selected_filename}"
    else:
        logger.info("⏭️  Pulando a remoção de fundos das fotos (remove_bg=False).")
        describe_prefix = blob_name  # Usa a imagem original sem processamento

    # 3️⃣ Analisa a imagem com IA e extrai os atributos visuais (salva no Firestore)
    logger.info(f"🚀 Iniciando a descrição da imagem selecionada: {selected_filename}...")
    bulk_describe_garments(
        bucket_name=BUCKET_NAME,
        input_prefix=describe_prefix,
        image_type="photo",
        collection_name="garments_taken_photos",
        owner_name=None,
        skip_existing=False  # Sempre reanalisa (não pula mesmo se já existir)
    )

    # 4️⃣ Busca todos os dados analisados do Firestore para comparação
    master_data = fetch_collection_data("garments_master")       # Peças de referência
    all_input_data = fetch_collection_data("garments_taken_photos")  # Fotos analisadas

    if not master_data or not all_input_data:
        logger.warning("⚠️ Alguma informação está faltando nos bancos! Verifique o estado do Firestore.")
        return {}

    # Filtra apenas o documento correspondente à imagem que acabamos de processar
    single_input_data = {
        k: v for k, v in all_input_data.items()
        if selected_name_no_ext in k  # Busca pela chave que contém o hash da imagem
    }

    if not single_input_data:
        logger.warning(f"⚠️ Não encontrei o registro com nome base de arquivo: {selected_filename} nos bancos.")
        return {}

    # 5️⃣ Compara esta imagem de consulta contra TODAS as peças mestre cadastradas
    logger.info(f"🚀 Comparando {selected_filename} com as {len(master_data)} referências registradas...")
    final_results = compare_all(master_data, single_input_data)

    # Exibe os resultados de forma organizada no terminal para depuração
    logger.info("\n📊 RESULTADOS DA VERIFICAÇÃO DE SUA IMAGEM:")
    for photo_id, res in final_results.items():
        logger.info(f"🔹 Imagem pesquisada: {photo_id}")
        logger.info(f"🏆 Melhor chance com: {res.get('best_match', 'N/A')} ({res.get('best_score', 0)}%)")

        # Lista todas as comparações em ordem decrescente de pontuação
        all_comps = res.get('all_comparisons', [])
        if all_comps:
            sorted_comps = sorted(all_comps, key=lambda x: x.get('score', 0), reverse=True)
            for comp in sorted_comps:
                logger.info(f"   -> {comp.get('master', 'Desconhecido')}: {comp.get('score', 0)}%")
        else:
            logger.info("   (⚠️ Detalhes insuficientes de processamento.)")

    # 6️⃣ Salva os resultados no Firestore para acesso pela API ou interface visual
    save_results_to_firestore(final_results, "garments_matches")

    # Retorna o resultado para ser consumido pelo back-end (Flask, React, etc.)
    return final_results


def import_garment(local_image_path: str, owner_name: str, remove_bg: bool = True) -> dict:
    """
    Importa uma nova peça de roupa como referência (Mestre) no sistema.
    Faz upload, remove o fundo (opcional), analisa com IA e salva no banco de dados mestre.

    Parâmetros:
        local_image_path (str): Caminho local da imagem da peça a cadastrar
        owner_name (str):       Nome do proprietário da peça (obrigatório)
        remove_bg (bool):       Se True, remove o fundo antes de cadastrar

    Retorno:
        dict: {"success": True, "doc_id": "..."} em caso de sucesso,
              {"success": False, "error": "..."} em caso de falha
    """
    client = storage.Client(project=GCP_PROJECT_ID)

    # Prefixos no GCS para as imagens mestre (original e processada)
    GCS_MASTER_PREFIX = "image_master/"
    GCS_CLEAN_MASTER_PREFIX = "image_clean/processed_image_master/"

    try:
        # 1️⃣ Faz upload da imagem bruta para a pasta de mestres no GCS
        blob_name = upload_single_image(client, local_image_path, BUCKET_NAME, GCS_MASTER_PREFIX)
        master_filename = os.path.basename(blob_name)       # Ex: "abc123.jpg"
        doc_id = os.path.splitext(master_filename)[0]       # Ex: "abc123" (sem extensão)

        logger.info(f"📤 Fez upload correto pro storage mestre: {blob_name}")

        # 2️⃣ Remove o fundo da imagem mestre (se solicitado)
        if remove_bg:
            processed_uri = process_single_image(client, BUCKET_NAME, blob_name, GCS_CLEAN_MASTER_PREFIX)
            if not processed_uri:
                return {"success": False, "error": "Infelizmente houve um colapso na etapa de remoção de fundo."}
            # Usa a versão limpa (sem fundo) para a análise com IA
            describe_prefix = f"{GCS_CLEAN_MASTER_PREFIX}{master_filename}"
        else:
            logger.info("⏭️  Sem necessidade de remoção de fundo. Usando diretamente o Mestre novo!")
            describe_prefix = blob_name  # Usa a imagem original

        # 3️⃣ Analisa a imagem com IA (Gemini) e salva no banco de dados mestre
        logger.info(f"🚀 Extraindo conteúdo visual deste mestre: {master_filename}...")
        bulk_describe_garments(
            bucket_name=BUCKET_NAME,
            input_prefix=describe_prefix,
            image_type="master",
            collection_name="garments_master",  # Salva na coleção de referências
            owner_name=owner_name,              # Registra o dono da peça
            skip_existing=False                 # Sempre reanalisa (garante dados atualizados)
        )

        logger.info(f"✅ Peça indexada! Identificador gerado={doc_id}, Pertence a={owner_name}")
        return {"success": True, "doc_id": doc_id}

    except Exception as e:
        logger.error(f"💥 Rolou falhas nessa importação global. Erro: {e}")
        return {"success": False, "error": str(e)}


# Bloco de teste para executar o pipeline de identificação diretamente pela linha de comando
if __name__ == '__main__':
    # Caminho de uma imagem de teste local (ajuste conforme necessário)
    test_image_path = "images/image_database/input_queries/Captura de Tela 2026-03-25 às 16.10.02.png"

    # Executa o pipeline completo de identificação sem sincronizar mestres
    results = identify_garment(test_image_path, sync_masters=False)

    print("\n--- INFORMAÇÕES QUE RETORNARAM E ESTÃO PRONTAS PRO AMBIENTE DE APP ---")
    print(results)
