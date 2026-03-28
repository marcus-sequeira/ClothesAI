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
GCP_PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud
BUCKET_NAME = "clothes_app"              # Bucket principal no GCS

# Diretórios locais das imagens (antes do upload)
MASTER_FOLDER = "images/image_database/master"        # Pasta local com imagens de referência
INPUT_FOLDER = "images/image_database/input_queries"  # Pasta local com fotos de consulta

# Prefixos no GCS para organizar as imagens
GCS_INPUT_PREFIX = "image_input/"     # Pasta no GCS para imagens de consulta
GCS_OUTPUT_PREFIX = "image_clean/"   # Pasta no GCS para imagens com fundo removido

# Configura o sistema de logs para acompanharmos o que está acontecendo
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def process_background_removal(
        client: storage.Client,
        bucket_name: str,
        input_prefixes: list[str],  # Lista de pastas no GCS para processar
        output_prefix: str          # Pasta de saída das imagens limpas
) -> None:
    """
    Percorre as imagens em múltiplos diretórios no GCS, remove o fundo de cada uma
    e salva o resultado em um diretório de saída unificado.

    Parâmetros:
        client:          Cliente do GCS inicializado
        bucket_name:     Nome do bucket no GCS
        input_prefixes:  Lista de prefixos (pastas) com as imagens a processar
        output_prefix:   Prefixo de saída para as imagens com fundo removido
    """
    bucket = client.bucket(bucket_name)
    count = 0  # Contador de imagens processadas com sucesso

    for prefix in input_prefixes:
        logger.info(f"⏳ Iniciando a remoção de fundo em gs://{bucket_name}/{prefix}...")
        blobs = bucket.list_blobs(prefix=prefix)

        for blob in blobs:
            # Ignora entradas que representam "pastas" no GCS (terminam com '/')
            if blob.name == prefix or blob.name.endswith('/'):
                continue

            filename = os.path.basename(blob.name)     # Nome do arquivo sem o caminho completo
            input_uri = f"gs://{bucket_name}/{blob.name}"          # URI original
            output_uri = f"gs://{bucket_name}/{output_prefix}{filename}"  # URI de saída

            try:
                # Processa a remoção de fundo desta imagem específica
                isolate_clothing_in_gcs(
                    input_uri=input_uri,
                    output_uri=output_uri,
                    is_fuzzy_item=False,  # Não é um item com bordas difusas
                    show_popup=False,     # Não exibir prévia durante o processamento
                )
                logger.info(f"⭐ Imagem processada: {filename} vinda de {prefix}")
                count += 1
            except Exception as e:
                logger.error(f"⚠️ Falha ao processar {filename} de {prefix}: {e}")

    logger.info(f"🏁 Remoção de fundo concluída. Total de imagens limpas: {count}")


if __name__ == '__main__':
    # --------------------------------------------------------
    # PRÉ-PROCESSAMENTO: Coleta as preferências do usuário
    # --------------------------------------------------------

    # Pergunta se deve re-descrever imagens já analisadas anteriormente
    re_describe_input = input(
        "Você deseja descrever novamente as imagens que já foram processadas? (s/N): ").strip().lower()

    # Se 's', reprocessa tudo; caso contrário, pula as imagens existentes
    skip_existing_flag = not (re_describe_input == 's')

    # Pergunta se deve remover o fundo das imagens antes da análise
    remove_bg_input = input(
        "Você deseja remover o fundo das imagens? (s/N): ").strip().lower()
    remove_bg = (remove_bg_input == 's')

    try:
        # --------------------------------------------------------
        # ETAPA 1 - UPLOAD EM LOTE PARA O BUCKET DO GCLOUD
        # --------------------------------------------------------
        # Inicializa o cliente do GCS para toda a execução do pipeline
        client = storage.Client(project=GCP_PROJECT_ID)

        # Garante que as pastas locais existam antes de tentar fazer upload
        Path(MASTER_FOLDER).mkdir(parents=True, exist_ok=True)
        Path(INPUT_FOLDER).mkdir(parents=True, exist_ok=True)

        # Envia as imagens de referência (mestre) para o GCS
        logger.info("📤 Enviando imagens mestre...")
        master_uris = bulk_upload_to_bucket(client, MASTER_FOLDER, BUCKET_NAME, "image_master")

        # Envia as fotos de consulta para o GCS
        logger.info("📤 Enviando imagens de pesquisa (queries)...")
        input_uris = bulk_upload_to_bucket(client, INPUT_FOLDER, BUCKET_NAME, "image_input")


        # --------------------------------------------------------
        # ETAPA 2 - REMOÇÃO DE FUNDO (Opcional)
        # --------------------------------------------------------
        if remove_bg:
            def is_already_processed(client, bucket_name, output_prefix):
                """
                Verifica se a pasta de saída já tem alguma imagem processada.
                Isso evita reprocessar todas as imagens desnecessariamente.
                """
                bucket = client.bucket(bucket_name)
                blobs = bucket.list_blobs(prefix=output_prefix, max_results=1)
                for _ in blobs:
                    return True  # Encontrou pelo menos um arquivo → já processado
                return False

            # --- Processando as imagens de CONSULTA ---
            input_folders_query = ["image_input/"]
            output_folder_query = "image_clean/processed_image_input/"

            # Só processa se a pasta de saída ainda não existir no GCS
            if not is_already_processed(client, BUCKET_NAME, output_folder_query):
                logger.info(f"Processando as imagens de pesquisa para {output_folder_query}...")
                process_background_removal(client, BUCKET_NAME, input_folders_query, output_folder_query)
            else:
                logger.info(f"Imagens de pesquisa ignoradas: {output_folder_query} já existe na nuvem.")

            # --- Processando as imagens MESTRE ---
            input_folders_master = ["image_master/"]
            output_folder_master = "image_clean/processed_image_master/"

            # Só processa se a pasta de saída ainda não existir no GCS
            if not is_already_processed(client, BUCKET_NAME, output_folder_master):
                logger.info(f"Processando as imagens mestre para {output_folder_master}...")
                process_background_removal(client, BUCKET_NAME, input_folders_master, output_folder_master)
            else:
                logger.info(f"Imagens mestre ignoradas: {output_folder_master} já existe na nuvem.")

            # Na etapa de descrição, usamos as versões limpas (sem fundo)
            master_describe_prefix = "image_clean/processed_image_master/"
            input_describe_prefix = "image_clean/processed_image_input/"
        else:
            logger.info("⏭️ Pulando a remoção de fundo (remove_bg=False).")
            # Sem remoção de fundo, usamos as imagens originais diretamente
            master_describe_prefix = "image_master/"
            input_describe_prefix = "image_input/"

        # --------------------------------------------------------
        # ETAPA 3 - DESCREVER AS IMAGENS COM IA (GEMINI)
        # --------------------------------------------------------
        # Analisa cada imagem mestre e extrai seus atributos visuais para o Firestore
        logger.info("🚀 Iniciando o lote de imagens MESTRE...")
        bulk_describe_garments(
            bucket_name=BUCKET_NAME,
            input_prefix=master_describe_prefix,
            image_type="master",
            collection_name="garments_master",  # Salva na coleção de referências
            owner_name="Marcus",
            skip_existing=skip_existing_flag,
        )

        # Analisa cada foto de consulta e extrai seus atributos visuais para o Firestore
        logger.info("🚀 Iniciando o lote de FOTOS TIRADAS...")
        bulk_describe_garments(
            bucket_name=BUCKET_NAME,
            input_prefix=input_describe_prefix,
            image_type="photo",
            collection_name="garments_taken_photos",  # Salva na coleção de consultas
            owner_name="Unknown",
            skip_existing=skip_existing_flag,
        )

        # --------------------------------------------------------
        # ETAPA 4 - COMPARAÇÃO DOS DADOS
        # --------------------------------------------------------
        # Busca os atributos das peças mestre do Firestore
        logger.info("📥 Buscando os dados Mestre no Firestore...")
        master_data = fetch_collection_data("garments_master")

        # Busca os atributos das fotos de consulta do Firestore
        logger.info("📥 Buscando os dados das Fotos no Firestore...")
        input_data = fetch_collection_data("garments_taken_photos")

        # Verifica se há dados em ambas as coleções antes de prosseguir
        if not master_data or not input_data:
            logger.warning("⚠️ Dados ausentes! Certifique-se de que ambas as coleções possuem documentos ('garments_master' e 'garments_photos').")
        else:
            logger.info(f"🚀 Iniciando a comparação em lote: {len(input_data)} fotos vs {len(master_data)} imagens mestre...\n")

            # Compara cada foto de consulta contra todas as peças mestre
            final_results = compare_all(master_data, input_data)

            # Exibe o resumo das correspondências encontradas
            print("\n📊 RESUMO DAS CORRESPONDÊNCIAS:")
            for photo_id, res in final_results.items():
                print(f" - {photo_id}  -->  {res['best_match']} ({res['best_score']}%)")

            # Salva os resultados no Firestore para acesso posterior (ex: pela interface visual)
            print("\n")
            save_results_to_firestore(final_results, "garments_matches")

        # --------------------------------------------------------
        # ETAPA 5 - ABRIR O VISUALIZADOR GRÁFICO
        # --------------------------------------------------------
        # Pergunta se o usuário deseja abrir a interface visual para revisar os resultados
        gui_option = input("Você gostaria de abrir a interface visual? (s/N): ").strip().lower()
        while True:
            if gui_option == "s":
                viewer_gui()  # Abre a interface gráfica Tkinter
                break         # Encerra o loop após fechar a interface
            elif gui_option == "n" or gui_option == "":
                print("Encerrando o pipeline. Até logo!")
                break
            else:
                # Opção inválida: repergunta
                gui_option = input("Opção incorreta. Você gostaria de abrir a interface visual? (s/N): ").strip().lower()

    except Exception as e:
        logger.critical(f"💥 O Pipeline Falhou: {e}")
