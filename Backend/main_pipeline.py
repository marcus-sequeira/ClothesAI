import logging
import os
from pathlib import Path
from google.cloud import storage
from removeClothingBackground import isolate_clothing_in_gcs
from gcs_functions import bulk_upload_to_bucket
from garment_analyzer import bulk_describe_garments
from comparison_functions import fetch_collection_data, compare_all, save_results_to_firestore
from viewer import main as viewer_gui

# ==============================
# 🔧 CONFIGURAÇÕES PRINCIPAIS
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"
BUCKET_NAME = "clothes_app"

MASTER_FOLDER = "images/image_database/master"
INPUT_FOLDER = "images/image_database/input_queries"

GCS_INPUT_PREFIX = "image_input/"
GCS_OUTPUT_PREFIX = "image_clean/"

# Configurando o sistema de logs para acompanharmos o que está acontecendo
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def process_background_removal(
        client: storage.Client,
        bucket_name: str,
        input_prefixes: list[str],
        output_prefix: str
) -> None:
    """
    Percorre as imagens em múltiplos diretórios no Google Cloud Storage,
    remove o fundo delas e salva o resultado em um diretório de saída unificado.
    """
    bucket = client.bucket(bucket_name)
    count = 0

    for prefix in input_prefixes:
        logger.info(f"⏳ Iniciando a remoção de fundo em gs://{bucket_name}/{prefix}...")
        blobs = bucket.list_blobs(prefix=prefix)

        for blob in blobs:
            if blob.name == prefix or blob.name.endswith('/'):
                continue

            filename = os.path.basename(blob.name)
            input_uri = f"gs://{bucket_name}/{blob.name}"
            output_uri = f"gs://{bucket_name}/{output_prefix}{filename}"

            try:
                isolate_clothing_in_gcs(
                    input_uri=input_uri,
                    output_uri=output_uri,
                    is_fuzzy_item=False,
                    show_popup=False,
                )
                logger.info(f"⭐ Imagem processada: {filename} vinda de {prefix}")
                count += 1
            except Exception as e:
                logger.error(f"⚠️ Falha ao processar {filename} de {prefix}: {e}")

    logger.info(f"🏁 Remoção de fundo concluída. Total de imagens limpas: {count}")


if __name__ == '__main__':
    # Pegando as preferências do usuário no terminal
    re_describe_input = input(
        "Você deseja descrever novamente as imagens que já foram processadas? (s/N): ").strip().lower()

    # Se a resposta for 's', refazemos o processo. Caso contrário, pulamos as existentes.
    skip_existing_flag = not (re_describe_input == 's')

    remove_bg_input = input(
        "Você deseja remover o fundo das imagens? (s/N): ").strip().lower()
    remove_bg = (remove_bg_input == 's')

    try:
        # --------------------------------------------------------
        # ETAPA 1 - UPLOAD EM LOTE PARA O BUCKET DO GCLOUD
        # --------------------------------------------------------
        client = storage.Client(project=GCP_PROJECT_ID)

        Path(MASTER_FOLDER).mkdir(parents=True, exist_ok=True)
        Path(INPUT_FOLDER).mkdir(parents=True, exist_ok=True)

        logger.info("📤 Enviando imagens mestre...")
        master_uris = bulk_upload_to_bucket(client, MASTER_FOLDER, BUCKET_NAME, "image_master")

        logger.info("📤 Enviando imagens de pesquisa (queries)...")
        input_uris = bulk_upload_to_bucket(client, INPUT_FOLDER, BUCKET_NAME, "image_input")


        # --------------------------------------------------------
        # ETAPA 2 - REMOÇÃO DE FUNDO (Opcional)
        # --------------------------------------------------------
        if remove_bg:
            def is_already_processed(client, bucket_name, output_prefix):
                bucket = client.bucket(bucket_name)
                blobs = bucket.list_blobs(prefix=output_prefix, max_results=1)
                for _ in blobs:
                    return True
                return False

            # Processando as imagens de pesquisa
            input_folders_query = ["image_input/"]
            output_folder_query = "image_clean/processed_image_input/"

            if not is_already_processed(client, BUCKET_NAME, output_folder_query):
                logger.info(f"Processando as imagens de pesquisa para {output_folder_query}...")
                process_background_removal(client, BUCKET_NAME, input_folders_query, output_folder_query)
            else:
                logger.info(f"Imagens de pesquisa ignoradas: {output_folder_query} já existe na nuvem.")

            # Processando as imagens mestre
            input_folders_master = ["image_master/"]
            output_folder_master = "image_clean/processed_image_master/"

            if not is_already_processed(client, BUCKET_NAME, output_folder_master):
                logger.info(f"Processando as imagens mestre para {output_folder_master}...")
                process_background_removal(client, BUCKET_NAME, input_folders_master, output_folder_master)
            else:
                logger.info(f"Imagens mestre ignoradas: {output_folder_master} já existe na nuvem.")

            # Usando os caminhos das imagens limpas para a próxima etapa
            master_describe_prefix = "image_clean/processed_image_master/"
            input_describe_prefix = "image_clean/processed_image_input/"
        else:
            logger.info("⏭️ Pulando a remoção de fundo (remove_bg=False).")
            # Mantendo os caminhos originais
            master_describe_prefix = "image_master/"
            input_describe_prefix = "image_input/"

        # --------------------------------------------------------
        # ETAPA 3 - DESCREVER AS IMAGENS COM IA
        # --------------------------------------------------------
        logger.info("🚀 Iniciando o lote de imagens MESTRE...")
        bulk_describe_garments(
            bucket_name=BUCKET_NAME,
            input_prefix=master_describe_prefix,
            image_type="master",
            collection_name="garments_master",
            owner_name="Marcus",
            skip_existing=skip_existing_flag,
        )

        logger.info("🚀 Iniciando o lote de FOTOS TIRADAS...")
        bulk_describe_garments(
            bucket_name=BUCKET_NAME,
            input_prefix=input_describe_prefix,
            image_type="photo",
            collection_name="garments_taken_photos",
            owner_name="Unknown",
            skip_existing=skip_existing_flag,
        )

        # --------------------------------------------------------
        # ETAPA 4 - COMPARAÇÃO DOS DADOS
        # --------------------------------------------------------
        logger.info("📥 Buscando os dados Mestre no Firestore...")
        master_data = fetch_collection_data("garments_master")

        logger.info("📥 Buscando os dados das Fotos no Firestore...")
        input_data = fetch_collection_data("garments_taken_photos")

        if not master_data or not input_data:
            logger.warning("⚠️ Dados ausentes! Certifique-se de que ambas as coleções possuem documentos ('garments_master' e 'garments_photos').")
        else:
            logger.info(f"🚀 Iniciando a comparação em lote: {len(input_data)} fotos vs {len(master_data)} imagens mestre...\n")

            final_results = compare_all(master_data, input_data)

            print("\n📊 RESUMO DAS CORRESPONDÊNCIAS:")
            for photo_id, res in final_results.items():
                print(f" - {photo_id}  -->  {res['best_match']} ({res['best_score']}%)")

            print("\n")
            save_results_to_firestore(final_results, "garments_matches")

        # --------------------------------------------------------
        # ETAPA 5 - ABRIR O VISUALIZADOR
        # --------------------------------------------------------
        gui_option = input("Você gostaria de abrir a interface visual? (s/N): ").strip().lower()
        while True:
            if gui_option == "s":
                viewer_gui()
                break  # Evita um loop infinito após fechar a interface
            elif gui_option == "n" or gui_option == "":
                print("Encerrando o pipeline. Até logo!")
                break
            else:
                gui_option = input("Opção incorreta. Você gostaria de abrir a interface visual? (s/N): ").strip().lower()

    except Exception as e:
        logger.critical(f"💥 O Pipeline Falhou: {e}")