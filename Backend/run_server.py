# run_server.py

import os
import base64
import tempfile
import logging
from flask import Flask, request, jsonify
from flask_cors import CORS
from werkzeug.utils import secure_filename
from google.cloud import storage, firestore

from pipeline_one_at_a_time import identify_garment, import_garment

# ==============================
# 🪵 RASTREAMENTO E CONFIGURAÇÕES
# ==============================
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

GCP_PROJECT_ID = "clothesidentifierapp"

app = Flask(__name__)
CORS(app)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp'}

# Inicializando os clientes do Google Cloud
storage_client = storage.Client(project=GCP_PROJECT_ID)
firestore_client = firestore.Client(project=GCP_PROJECT_ID)

# ==============================
# 🔧 FUNÇÕES AUXILIARES
# ==============================
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def gcs_to_base64(gcs_uri: str) -> str | None:
    if not gcs_uri or not gcs_uri.startswith("gs://"):
        return None

    try:
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        if not blob.exists():
            logger.warning(f"⚠️ Imagem não encontrada no Cloud Storage: {gcs_uri}")
            return None

        image_bytes = blob.download_as_bytes()
        encoded = base64.b64encode(image_bytes).decode('utf-8')

        ext = blob_name.rsplit('.', 1)[-1].lower()
        mime = {
            'png': 'image/png',
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'webp': 'image/webp'
        }.get(ext, 'image/jpeg')

        return f"data:{mime};base64,{encoded}"

    except Exception as e:
        logger.error(f"❌ Falha ao converter imagem do GCS para base64: {e}")
        return None


# ==============================
# 🌐 ROTAS DA API
# ==============================
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({
        "success": True,
        "status": "healthy",
        "message": "A API Clothes Identifier está online!"
    }), 200


@app.route('/api/identify', methods=['POST'])
def identify_endpoint():
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "Nenhum arquivo de imagem foi enviado."}), 400

    file = request.files['image']

    if file.filename == '':
        return jsonify({"success": False, "error": "Nenhum arquivo foi selecionado."}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, filename)
            file.save(temp_file_path)

            logger.info(f"📥 Recebido: {filename}")

            # 🔴 O padrão agora é Falso (Desligado)
            remove_bg_raw = request.form.get('remove_bg', 'false').strip().lower()
            remove_bg = remove_bg_raw in ('true', '1', 'yes')

            logger.info(f"🔧 Remoção de fundo: {'LIGADA' if remove_bg else 'DESLIGADA'}")

            try:
                results = identify_garment(
                    temp_file_path,
                    sync_masters=False,
                    remove_bg=remove_bg
                )

                if not results:
                    return jsonify({"success": False, "error": "Nenhuma correspondência encontrada."}), 404

                input_key = list(results.keys())[0]
                match_data = results[input_key]

                best_match_id = match_data.get("best_match")
                best_score = match_data.get("best_score", 0)

                if not best_match_id:
                    return jsonify({"success": False, "error": "Nenhuma correspondência válida encontrada."}), 404

                master_doc = firestore_client.collection("garments_master").document(best_match_id).get()

                if not master_doc.exists:
                    return jsonify({"success": False, "error": "Dados da correspondência ausentes."}), 404

                master_info = master_doc.to_dict()

                owner_name = master_info.get("owner_name") or "Desconhecido"
                image_gcs_uri = master_info.get("image_gcs_uri")

                base64_image = gcs_to_base64(image_gcs_uri)

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

    return jsonify({"success": False, "error": "Tipo de arquivo inválido. Formatos aceitos: PNG, JPG, JPEG, WEBP."}), 400


@app.route('/api/import', methods=['POST'])
def import_endpoint():
    if 'image' not in request.files:
        return jsonify({"success": False, "error": "Nenhum arquivo de imagem foi fornecido."}), 400

    file = request.files['image']

    if file.filename == '':
        return jsonify({"success": False, "error": "Nenhum arquivo foi selecionado na requisição."}), 400

    owner_name = request.form.get('owner_name', '').strip()
    if not owner_name:
        return jsonify({"success": False, "error": "O campo owner_name é obrigatório."}), 400

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_file_path = os.path.join(temp_dir, filename)
            file.save(temp_file_path)

            logger.info(f"📥 Arquivo recebido para importação: {filename} (dono registrado: {owner_name})")

            # 🔴 O padrão agora é Falso (Desligado)
            remove_bg_raw = request.form.get('remove_bg', 'false').strip().lower()
            remove_bg = remove_bg_raw in ('true', '1', 'yes')

            logger.info(f"🔧 Remoção de fundo: {'LIGADA' if remove_bg else 'DESLIGADA'}")

            try:
                result = import_garment(
                    temp_file_path,
                    owner_name,
                    remove_bg=remove_bg
                )

                if not result.get("success"):
                    return jsonify({"success": False, "error": result.get("error")}), 500

                return jsonify({
                    "success": True,
                    "message": "Peça de roupa importada com sucesso.",
                    "owner": owner_name,
                    "doc_id": result.get("doc_id"),
                }), 200

            except Exception as e:
                logger.error(f"💥 Erro na importação: {e}")
                return jsonify({"success": False, "error": str(e)}), 500

    return jsonify({"success": False, "error": "Tipo de arquivo inválido. Verifique os formatos aceitos."}), 400


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8080, debug=True)