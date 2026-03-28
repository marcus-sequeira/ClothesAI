# run_server.py
# Servidor Flask que expõe a API REST do sistema de identificação de peças de roupa.
# Fornece dois endpoints principais: /api/identify (identificar peça) e /api/import (cadastrar peça).

import os
import base64
import tempfile
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from google.cloud import storage, firestore

# Funções de pipeline do projeto
from pipeline_one_at_a_time import identify_garment, import_garment

# ==============================
# 🪵 RASTREAMENTO E CONFIGURAÇÕES
# ==============================
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

GCP_PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud

# Inicializa o aplicativo Flask e habilita CORS para requisições de diferentes origens
app = Flask(__name__)
CORS(app)

# Extensões de arquivo aceitas pelo servidor (outras serão rejeitadas)
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

# Inicializa os clientes do Google Cloud para uso nas rotas da API
storage_client = storage.Client(project=GCP_PROJECT_ID)
firestore_client = firestore.Client(project=GCP_PROJECT_ID)

# ==============================
# 🔧 FUNÇÕES AUXILIARES
# ==============================
def allowed_file(filename):
    """
    Verifica se a extensão do arquivo enviado é aceita pelo servidor.
    Retorna True para extensões em ALLOWED_EXTENSIONS, False caso contrário.
    """
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def gcs_to_base64(gcs_uri: str) -> str | None:
    """
    Faz download de uma imagem do GCS e a converte para uma string Base64.
    O resultado pode ser embutido diretamente em respostas JSON para exibição no front-end.

    Parâmetros:
        gcs_uri: URI gs:// da imagem no Google Cloud Storage

    Retorno:
        String no formato "data:<mime_type>;base64,<dados>" ou None em caso de erro
    """
    # Valida o formato do URI antes de tentar qualquer operação
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        return None

    try:
        # Extrai o nome do bucket e o caminho do blob a partir do URI
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        # Verifica se o arquivo existe antes de tentar baixar
        if not blob.exists():
            logger.warning(f"⚠️ Imagem não encontrada no Cloud Storage: {gcs_uri}")
            return None

        # Faz download dos bytes e codifica em Base64 para envio via JSON
        image_bytes = blob.download_as_bytes()
        encoded = base64.b64encode(image_bytes).decode('utf-8')

        # Determina o MIME type correto baseado na extensão do arquivo
        ext = blob_name.rsplit('.', 1)[-1].lower()
        mime = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'webp': 'image/webp'
        }.get(ext, 'image/jpeg')  # Padrão: image/jpeg se extensão não reconhecida

        # Retorna a string no formato de Data URI (pronto para usar no HTML)
        return f"data:{mime};base64,{encoded}"

    except Exception as e:
        logger.error(f"❌ Falha ao converter imagem do GCS para base64: {e}")
        return None


# ==============================
# 🌐 ROTAS DA API
# ==============================
@app.route('/health', methods=['GET'])
def health_check():
    """
    Endpoint de verificação de saúde da API.
    Usado por sistemas de monitoramento para confirmar que o servidor está ativo.
    Retorna HTTP 200 com status "healthy".
    """
    return jsonify({
        "success": True,
        "status": "healthy",
        "message": "A API Clothes Identifier está online!"
    }), 200


@app.route('/api/identify', methods=['POST'])
def identify_endpoint():
    """
    Endpoint principal de identificação de peça de roupa.
    Recebe uma imagem via multipart/form-data e retorna o dono e a pontuação da melhor correspondência.

    Parâmetros do formulário:
        image (file):      Arquivo de imagem da peça a identificar (obrigatório)
        remove_bg (str):   'true'/'1'/'yes' para ativar remoção de fundo (padrão: false)

    Retorno (200 OK):
        {
            "success": true,
            "results": [{
                "owner": "Nome do Dono",
                "probability": 85,
                "image": "data:image/jpeg;base64,...",
                "matched_id": "abc123"
            }]
        }
    """
    # Verifica se algum arquivo foi enviado na requisição
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "Nenhum arquivo de imagem foi enviado."}), 400

    file = request.files['image']

    # Verifica se o usuário selecionou um arquivo (nome não pode ser vazio)
    if file.filename == '':
        return jsonify({"success": False, "error": "Nenhum arquivo foi selecionado."}), 400

    if file and allowed_file(file.filename):
        # Sanitiza o nome do arquivo para evitar path traversal e outros ataques
        filename = secure_filename(file.filename)

        # Usa um diretório temporário que é automaticamente limpo após o bloco 'with'
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, filename)
            file.save(temp_file_path)  # Salva o arquivo recebido no diretório temporário

            logger.info(f"📥 Recebido: {filename}")

            # 🔴 Remoção de fundo desativada por padrão (pode impactar a velocidade)
            remove_bg_raw = request.form.get('remove_bg', 'false').strip().lower()
            remove_bg = remove_bg_raw in ('true', '1', 'yes')

            logger.info(f"🔧 Remoção de fundo: {'LIGADA' if remove_bg else 'DESLIGADA'}")

            try:
                # Executa o pipeline completo de identificação da peça
                results = identify_garment(
                    temp_file_path,
                    sync_masters=False,   # Não sincroniza o banco mestre nesta chamada
                    remove_bg=remove_bg
                )

                # Nenhum resultado retornado = nenhuma correspondência encontrada
                if not results:
                    return jsonify({"success": False, "error": "Nenhuma correspondência encontrada."}), 404

                # Extrai os dados do primeiro (e único) resultado desta execução
                input_key = list(results.keys())[0]
                match_data = results[input_key]

                best_match_id = match_data.get("best_match")
                best_score = match_data.get("best_score", 0)

                # Se o melhor match for None, nenhuma correspondência válida foi encontrada
                if not best_match_id:
                    return jsonify({"success": False, "error": "Nenhuma correspondência válida encontrada."}), 404

                # Busca os detalhes da peça mestre correspondente no Firestore
                master_doc = firestore_client.collection("garments_master").document(best_match_id).get()

                if not master_doc.exists:
                    return jsonify({"success": False, "error": "Dados da correspondência ausentes."}), 404

                master_info = master_doc.to_dict()

                # Extrai as informações relevantes para a resposta ao front-end
                owner_name = master_info.get("owner_name") or "Desconhecido"
                image_gcs_uri = master_info.get("image_gcs_uri")

                # Converte a imagem da peça mestre para Base64 para incluir na resposta JSON
                base64_image = gcs_to_base64(image_gcs_uri)

                # Retorna os resultados com dono, probabilidade e imagem da peça encontrada
                return jsonify({
                    "success": True,
                    "message": "Processamento concluído com sucesso.",
                    "results": [{
                        "owner": owner_name,
                        "probability": best_score,
                        "image": base64_image,
                        "matched_id": best_match_id
                    }]
                }), 200

            except Exception as e:
                logger.error(f"💥 Ocorreu um erro na requisição: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

    # Tipo de arquivo não permitido
    return jsonify({"success": False, "error": "Tipo de arquivo inválido. Formatos aceitos: PNG, JPG, JPEG, WEBP."}), 400


@app.route('/api/import', methods=['POST'])
def import_endpoint():
    """
    Endpoint para cadastrar uma nova peça de roupa como referência no sistema.
    Recebe a imagem e o nome do proprietário e adiciona ao banco de dados mestre.

    Parâmetros do formulário:
        image (file):       Arquivo de imagem da peça a cadastrar (obrigatório)
        owner_name (str):   Nome do proprietário da peça (obrigatório)
        remove_bg (str):    'true'/'1'/'yes' para ativar remoção de fundo (padrão: false)

    Retorno (200 OK):
        {
            "success": true,
            "message": "Peça de roupa importada com sucesso.",
            "owner": "Nome do Dono",
            "doc_id": "abc123"
        }
    """
    # Verifica se algum arquivo foi enviado na requisição
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "Nenhum arquivo de imagem foi fornecido."}), 400

    file = request.files['image']

    # Verifica se o usuário selecionou um arquivo
    if file.filename == '':
        return jsonify({"success": False, "error": "Nenhum arquivo foi selecionado na requisição."}), 400

    # O nome do dono é obrigatório para cadastrar a peça corretamente
    owner_name = request.form.get('owner_name', '').strip()
    if not owner_name:
        return jsonify({"success": False, "error": "O campo owner_name é obrigatório."}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)

        # Usa diretório temporário para armazenar o arquivo durante o processamento
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, filename)
            file.save(temp_file_path)

            logger.info(f"📥 Arquivo recebido para importação: {filename} (dono registrado: {owner_name})")

            # 🔴 Remoção de fundo desativada por padrão para agilizar o cadastro
            remove_bg_raw = request.form.get('remove_bg', 'false').strip().lower()
            remove_bg = remove_bg_raw in ('true', '1', 'yes')

            logger.info(f"🔧 Remoção de fundo: {'LIGADA' if remove_bg else 'DESLIGADA'}")

            try:
                # Executa o pipeline de importação de nova peça mestre
                result = import_garment(
                    temp_file_path,
                    owner_name,
                    remove_bg=remove_bg
                )

                # Verifica se a importação foi concluída com sucesso
                if not result.get("success"):
                    return jsonify({"success": False, "error": result.get("error")}), 500

                # Retorna confirmação com o ID do documento criado no Firestore
                return jsonify({
                    "success": True,
                    "message": "Peça de roupa importada com sucesso.",
                    "owner": owner_name,
                    "doc_id": result.get("doc_id"),  # ID único gerado para esta peça
                }), 200

            except Exception as e:
                logger.error(f"💥 Erro na importação: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

    # Tipo de arquivo não permitido pelo servidor
    return jsonify({"success": False, "error": "Tipo de arquivo inválido. Verifique os formatos aceitos."}), 400


if __name__ == '__main__':
    # Inicia o servidor Flask em modo de desenvolvimento
    # host='0.0.0.0' permite conexões de qualquer IP (necessário para containers/cloud)
    # debug=True ativa o recarregamento automático e mensagens de erro detalhadas
    app.run(host='0.0.0.0', port=8080, debug=True)
