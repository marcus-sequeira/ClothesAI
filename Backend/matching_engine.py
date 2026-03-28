import json
from google.cloud import storage

# ==============================
# 🔧 CONFIGURAÇÃO E INICIALIZAÇÃO
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"     # ID do projeto no Google Cloud
BUCKET_NAME = "clothes_app"                  # Nome do bucket no GCS
BASE_PREFIX = "jsons_data/"                  # Pasta no GCS onde estão os JSONs de atributos
OUTPUT_PATH = "results/matching_results.json"  # Caminho de saída para os resultados

MIN_SCORE_THRESHOLD = 50  # Pontuação mínima para considerar uma correspondência válida
TOP_K = 3                 # Número de melhores correspondências a retornar por consulta

# Inicializa o cliente do Google Cloud Storage para acessar os buckets
storage_client = storage.Client(project=GCP_PROJECT_ID)

# ==============================
# ☁️ FUNÇÕES DE ACESSO AO GCS
# ==============================
def load_and_split_jsons():
    """
    Carrega todos os arquivos JSON do bucket GCS e os separa em dois grupos:
    - master_data: JSONs de peças de referência (banco de dados)
    - input_data:  JSONs de fotos enviadas para identificação

    A separação é feita com base no caminho do arquivo:
    - Caminhos com '/master/' → vão para master_data
    - Caminhos com '/input/'  → vão para input_data

    Retorno:
        Tupla (master_data, input_data) como dicionários {caminho: dados_json}
    """
    bucket = storage_client.bucket(BUCKET_NAME)
    blobs = bucket.list_blobs(prefix=BASE_PREFIX)  # Lista todos os blobs dentro da pasta

    master_data = {}  # Dicionário para armazenar as peças de referência
    input_data = {}   # Dicionário para armazenar as fotos de consulta

    for blob in blobs:
        # Processa apenas arquivos JSON (ignora pastas e outros formatos)
        if not blob.name.endswith('.json'):
            continue

        try:
            # Faz download e parsing do conteúdo JSON do arquivo
            content = blob.download_as_text(encoding='utf-8')
            json_data = json.loads(content)

            # Classifica o JSON com base no seu caminho dentro do bucket
            if "/master/" in blob.name:
                master_data[blob.name] = json_data
                print(f"📥 MESTRE: {blob.name}")

            elif "/input/" in blob.name:
                input_data[blob.name] = json_data
                print(f"📥 CONSULTA: {blob.name}")

        except Exception as e:
            print(f"❌ Erro ao carregar {blob.name}: {e}")

    return master_data, input_data

def save_results(results):
    """
    Serializa os resultados das correspondências em JSON e salva no GCS.
    O arquivo de saída pode ser consumido por outros sistemas ou visualizadores.
    """
    bucket = storage_client.bucket(BUCKET_NAME)
    blob = bucket.blob(OUTPUT_PATH)

    # Salva os resultados como JSON formatado (indentado para legibilidade)
    blob.upload_from_string(
        json.dumps(results, indent=2, ensure_ascii=False),  # ensure_ascii=False para preservar acentos
        content_type='application/json'
    )
    print(f"\n💾 Resultados salvos em gs://{BUCKET_NAME}/{OUTPUT_PATH}")

# ==============================
# 🧠 MOTOR DE CORRESPONDÊNCIA
# ==============================
def extract_analysis(data):
    """
    Extrai o campo 'analysis' de um documento JSON, se existir.
    Alguns documentos armazenam os atributos dentro de uma chave 'analysis';
    outros os colocam diretamente no nível raiz.
    """
    return data.get("analysis", data)

def avaliar_match(master, input_):
    """
    Compara um item mestre com um item de consulta e calcula uma pontuação de similaridade.
    A pontuação é baseada em vários atributos com pesos diferentes.

    Parâmetros:
        master: dicionário com os atributos da peça de referência
        input_: dicionário com os atributos da foto de consulta

    Retorno:
        Dicionário com a pontuação normalizada (0-100), pontuação bruta e estilos em comum
    """
    score = 0  # Pontuação acumulada durante a comparação

    def match(a, b, weight):
        """Função interna: retorna o peso se os valores forem iguais, 0 caso contrário."""
        if not a or not b:
            return 0  # Campos ausentes não contribuem para a pontuação
        return weight if str(a).lower() == str(b).lower() else 0

    # Tipo específico da peça (ex: "camiseta", "calça jeans") — maior peso
    score += match(master.get("specific_type"), input_.get("specific_type"), 30)

    # Categoria geral (ex: "top", "bottom", "outerwear")
    score += match(master.get("item_category"), input_.get("item_category"), 15)

    # Comparação de cor principal com lógica de substring (mais flexível que exata)
    if master.get("primary_color") and input_.get("primary_color"):
        if master["primary_color"].lower() in input_["primary_color"].lower() \
           or input_["primary_color"].lower() in master["primary_color"].lower():
            score += 20  # Cor correspondente ou contida uma na outra

    # Caimento/silhueta: compara apenas a primeira palavra (ex: "slim" de "slim fit")
    if master.get("fit_silhouette") and input_.get("fit_silhouette"):
        if master["fit_silhouette"].split()[0].lower() == input_["fit_silhouette"].split()[0].lower():
            score += 10

    # Tecido ou material (ex: "cotton", "polyester")
    score += match(master.get("fabric_material"), input_.get("fabric_material"), 10)

    # Estilos estéticos: calcula a sobreposição de listas (cada estilo em comum vale 5 pts)
    master_styles = set(master.get("aesthetic_style", []))
    input_styles = set(input_.get("aesthetic_style", []))
    overlap = master_styles.intersection(input_styles)  # Estilos que as duas peças têm em comum
    score += len(overlap) * 5  # Cada estilo em comum contribui com 5 pontos

    # Texto/tipografia presente na peça (comparação de substring)
    if master.get("typography_text") and input_.get("typography_text"):
        if master["typography_text"].lower() in input_["typography_text"].lower() \
           or input_["typography_text"].lower() in master["typography_text"].lower():
            score += 15

    # Normaliza a pontuação para uma escala de 0 a 100
    MAX_SCORE = 120  # Pontuação máxima teórica possível com todos os campos correspondendo
    normalized_score = min(int((score / MAX_SCORE) * 100), 100)  # Limita a 100 no máximo

    return {
        "score": normalized_score,          # Pontuação final normalizada (0-100)
        "raw_score": score,                 # Pontuação bruta antes da normalização
        "matched_styles": list(overlap)     # Lista de estilos que coincidiram
    }

def compare_all(master_data, input_data):
    """
    Compara todos os itens de consulta contra todos os itens mestre.
    Para cada consulta, encontra as melhores correspondências ordenadas por pontuação.

    Parâmetros:
        master_data: dicionário de peças de referência
        input_data:  dicionário de fotos de consulta

    Retorno:
        Dicionário com o melhor match e os TOP_K melhores para cada consulta
    """
    results = {}

    for input_name, input_json in input_data.items():
        print(f"\n🔍 Processando: {input_name}")
        input_analysis = extract_analysis(input_json)  # Extrai os atributos do item de consulta
        comparisons = []

        for master_name, master_json in master_data.items():
            try:
                master_analysis = extract_analysis(master_json)  # Extrai os atributos do mestre
                result = avaliar_match(master_analysis, input_analysis)

                comparisons.append({
                    "master": master_name,
                    "score": result["score"]
                })
            except Exception as e:
                print(f"⚠️ Erro ao comparar {input_name} vs {master_name}: {e}")

        # Se não houver nenhum mestre para comparar, pula esta consulta
        if not comparisons:
            print(f"⚠️ Nenhum mestre encontrado para comparar com {input_name}")
            continue

        # Ordena as correspondências da maior para a menor pontuação
        sorted_matches = sorted(comparisons, key=lambda x: x["score"], reverse=True)

        best_match = sorted_matches[0]["master"]
        best_score = sorted_matches[0]["score"]

        # Se a melhor pontuação estiver abaixo do limiar mínimo, não considera como match válido
        if best_score < MIN_SCORE_THRESHOLD:
            best_match = None

        results[input_name] = {
            "best_match": best_match,               # Melhor correspondência (ou None se abaixo do limiar)
            "best_score": best_score,               # Pontuação do melhor match
            "top_matches": sorted_matches[:TOP_K]   # Os TOP_K melhores resultados
        }
        print(f"✅ Melhor: {best_match} ({best_score})")

    return results

# ==============================
# 🚀 PIPELINE PRINCIPAL
# ==============================
if __name__ == '__main__':
    try:
        # Carrega e separa os JSONs de mestre e consulta do GCS
        print("📂 Carregando dados do GCS...")
        master_data, input_data = load_and_split_jsons()

        print(f"\n✅ Mestres carregados: {len(master_data)}")
        print(f"✅ Consultas carregadas: {len(input_data)}")

        # Executa o motor de correspondência para todos os pares
        print("\n🚀 Executando o motor de correspondência...")
        final_results = compare_all(master_data, input_data)

        # Salva os resultados no GCS ou exibe aviso se não houver resultados
        if final_results:
            save_results(final_results)
            print("\n🎯 CONCLUÍDO!")
        else:
            print("\n⚠️ Nenhum resultado foi gerado para salvar.")

    except Exception as e:
        print(f"❌ Erro fatal: {e}")
