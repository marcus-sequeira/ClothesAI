import json
import logging
from google.cloud import firestore

# ==============================
# 🔧 CONFIGURAÇÃO
# ==============================
# ID do projeto no Google Cloud Platform
GCP_PROJECT_ID = "clothesidentifierapp"

# ==============================
# 🎨 FAMÍLIAS DE CORES (Para Correspondência Inteligente)
# ==============================
# Define quais cores são consideradas "próximas" o suficiente para evitar a penalidade severa.
# Exemplo: "azul" e "marinho" são da mesma família, então não penalizamos a comparação.
COLOR_FAMILIES = {
    "red": ["red", "orange", "burgundy", "maroon", "coral"],
    "orange": ["orange", "red", "yellow", "coral", "peach"],
    "blue": ["blue", "navy", "cyan", "teal", "light blue", "denim"],
    "navy": ["navy", "blue", "black", "dark purple"],
    "black": ["black", "charcoal", "navy", "dark grey"],
    "white": ["white", "cream", "ivory", "off-white"],
    "green": ["green", "olive", "khaki", "mint", "emerald"],
    "yellow": ["yellow", "mustard", "gold", "orange"],
    "pink": ["pink", "rose", "magenta", "fuchsia", "salmon"],
    "purple": ["purple", "lilac", "violet", "lavender", "plum"],
    "grey": ["grey", "gray", "silver", "charcoal", "slate"],
    "brown": ["brown", "tan", "beige", "camel", "chocolate", "khaki"]
}

# ==============================
# 🪵 SISTEMA DE LOGS
# ==============================
# Configura o sistema de logging para exibir mensagens de nível INFO no terminal
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Inicializa o cliente do Firestore para se comunicar com o banco de dados
firestore_client = firestore.Client(project=GCP_PROJECT_ID)


# ==============================
# 🧠 ALGORITMO DE COMPARAÇÃO
# ==============================
def avaliar_match_chave_a_chave(master, input_):
    """
    Compara dois dicionários de atributos de peças (mestre vs. input) campo a campo.
    Retorna um dicionário com a pontuação final e os detalhes de cada campo avaliado.
    """
    score = 0.0       # Pontuação acumulada durante a comparação
    max_score = 0.0   # Pontuação máxima possível com os campos disponíveis
    details = {}      # Registro detalhado do resultado de cada campo

    # 🎯 PESOS BASE POR CAMPO
    # Cada campo tem um peso que determina o quanto ele impacta na pontuação final.
    # Campos mais importantes têm pesos maiores.
    BASE_WEIGHTS = {
        "specific_type": 20,      # Tipo específico da peça (ex: camiseta, calça)
        "item_category": 15,      # Categoria geral (ex: top, bottom)
        "primary_color": 20,      # Cor principal
        "secondary_colors": 5,    # Cores secundárias
        "fit_silhouette": 10,     # Caimento e silhueta (ex: slim, oversized)
        "fabric_material": 10,    # Tecido ou material (ex: algodão, poliéster)
        "aesthetic_style": 10,    # Estilo estético (ex: casual, streetwear)
        "typography_text": 10,    # Texto ou tipografia presente na peça
        "neckline_collar": 5,     # Tipo de gola ou decote
        "sleeve_style": 5,        # Estilo da manga
    }

    # 🧠 AJUSTE DINÂMICO DE PESOS
    # Copiamos os pesos base para poder ajustá-los sem alterar o original
    dynamic_weights = BASE_WEIGHTS.copy()

    # Verificamos as cores antes para decidir a estratégia de redistribuição
    master_color = str(master.get("primary_color", "")).strip().lower()
    input_color = str(input_.get("primary_color", "")).strip().lower()

    redistribute_pool = 0  # Pontos a serem redistribuídos para outros campos

    if master_color and input_color and master_color == input_color:
        # 🎨 Se as cores já são iguais, reduzimos o peso da cor para evitar que
        # ela domine excessivamente a pontuação e redistribuímos o peso extra.
        reduction_factor = 0.5
        original = dynamic_weights["primary_color"]
        reduced = original * reduction_factor
        redistribute_pool = original - reduced
        dynamic_weights["primary_color"] = reduced

    # 🧠 REDISTRIBUI os pontos "livres" para os atributos mais importantes
    if redistribute_pool > 0:
        dynamic_weights["specific_type"] += redistribute_pool * 0.4   # 40% vai pro tipo
        dynamic_weights["item_category"] += redistribute_pool * 0.3   # 30% vai pra categoria
        dynamic_weights["fit_silhouette"] += redistribute_pool * 0.3  # 30% vai pro caimento

    # 🚫 Remove o peso de campos ausentes nos dois lados (não faz sentido comparar)
    for field in list(dynamic_weights.keys()):
        if not master.get(field) and not input_.get(field):
            dynamic_weights[field] = 0

    # ---------------- FUNÇÕES DE PONTUAÇÃO ---------------- #

    def add_exact(field):
        """Comparação exata (case-insensitive): ganha o peso total ou nada."""
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return  # Campo sem peso, ignoramos

        max_score += weight
        a, b = master.get(field), input_.get(field)

        if a and b and str(a).lower() == str(b).lower():
            score += weight
            details[field] = "exact_match"
        else:
            details[field] = "no_match"

    def add_partial_text(field):
        """Comparação parcial de texto: ganha o peso total se um contém o outro."""
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a, b = master.get(field), input_.get(field)

        if a and b:
            a, b = str(a).lower(), str(b).lower()
            if a in b or b in a:
                score += weight
                details[field] = "partial_match"
            else:
                details[field] = "no_match"
        else:
            details[field] = "missing"

    def add_color(field):
        """
        Comparação inteligente de cores:
        - Correspondência exata: peso total
        - Mesma família de cor: metade do peso
        - Cores incompatíveis: nada (e aplica penalidade depois)
        """
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight

        a = str(master.get(field, "")).strip().lower()
        b = str(input_.get(field, "")).strip().lower()

        if a and b and a != "none" and b != "none":
            if a == b:
                # Cor idêntica: pontuação máxima
                score += weight
                details[field] = "match"
            elif b in COLOR_FAMILIES.get(a, []) or a in COLOR_FAMILIES.get(b, []):
                # Cor próxima (mesma família): metade da pontuação
                score += (weight * 0.5)
                details[field] = "partial_match"
            else:
                # Cores completamente diferentes: zero pontos
                details[field] = "mismatch"
        else:
            details[field] = "missing"

    def add_list_overlap(field):
        """
        Compara listas calculando a proporção de sobreposição (interseção / união).
        Útil para campos como 'aesthetic_style' que podem ter vários valores.
        """
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a_list = set(master.get(field, []))
        b_list = set(input_.get(field, []))

        if a_list and b_list:
            overlap = len(a_list.intersection(b_list))  # Quantos itens em comum
            total = len(a_list.union(b_list))            # Total de itens únicos
            ratio = overlap / total if total > 0 else 0  # Proporção de sobreposição
            score += weight * ratio
            details[field] = {"overlap": overlap, "ratio": round(ratio, 2)}
        else:
            details[field] = "missing"

    def add_fit(field):
        """
        Compara apenas a primeira palavra do campo de caimento/silhueta.
        Exemplo: "slim fit" vs "slim relaxed" → considera como correspondência por 'slim'.
        """
        nonlocal score, max_score
        weight = dynamic_weights[field]
        if weight == 0:
            return

        max_score += weight
        a, b = master.get(field), input_.get(field)

        if a and b and str(a).strip() and str(b).strip():
            if str(a).split()[0].lower() == str(b).split()[0].lower():
                score += weight
                details[field] = "fit_match"
            else:
                details[field] = "fit_mismatch"
        else:
            details[field] = "missing"

    # ---------------- APLICANDO A PONTUAÇÃO CAMPO A CAMPO ---------------- #

    add_exact("specific_type")        # Tipo exato da peça
    add_exact("item_category")        # Categoria geral
    add_color("primary_color")        # Cor principal (com lógica de família)
    add_exact("secondary_colors")     # Cores secundárias
    add_fit("fit_silhouette")         # Caimento (compara só a primeira palavra)
    add_exact("fabric_material")      # Material/tecido
    add_list_overlap("aesthetic_style")  # Estilos estéticos (sobreposição de listas)
    add_partial_text("typography_text")  # Texto impresso na peça
    add_exact("neckline_collar")      # Tipo de gola
    add_exact("sleeve_style")         # Estilo de manga

    # ---------------- CÁLCULO DA PONTUAÇÃO FINAL ---------------- #

    # Converte a pontuação bruta para uma escala de 0 a 100
    final_score = int((score / max_score) * 100) if max_score > 0 else 0

    # 🚫 PENALIDADE SEVERA: se as cores forem completamente incompatíveis,
    # a pontuação final é reduzida para 20% do valor calculado.
    # Isso evita que uma peça de cor completamente diferente seja considerada como match.
    if details.get("primary_color") == "mismatch":
        final_score = int(final_score * 0.2)

    return {
        "score": final_score,                                          # Pontuação final (0-100)
        "raw_score": round(score, 2),                                  # Pontuação bruta acumulada
        "max_score": round(max_score, 2),                              # Pontuação máxima possível
        "details": details,                                            # Detalhes campo a campo
        "weights_used": {k: round(v, 2) for k, v in dynamic_weights.items()}  # Pesos utilizados
    }

def compare_all(master_data, input_data):
    """
    Compara todas as fotos de entrada contra todos os mestres disponíveis.
    Para cada foto, encontra a melhor correspondência no banco de dados mestre.

    Parâmetros:
        master_data: dict com os dados de referência (banco de peças cadastradas)
        input_data:  dict com os dados das fotos tiradas para identificar

    Retorno:
        dict com o melhor match e todas as comparações para cada foto
    """
    results = {}

    for input_name, input_json in input_data.items():
        logger.info(f"🔍 Avaliando foto: {input_name}")
        best_match, best_score = None, -1
        comparisons = []

        for master_name, master_json in master_data.items():
            try:
                # Compara a foto atual com cada peça mestre individualmente
                result = avaliar_match_chave_a_chave(master_json, input_json)
                score = result.get("score", 0)
                comparisons.append({
                    "master": master_name,
                    "score": score,
                    "details": result.get("details")
                })

                # Atualiza o melhor match se essa comparação tiver pontuação maior
                if score > best_score:
                    best_score = score
                    best_match = master_name

            except Exception as e:
                logger.error(f"⚠️ Erro ao comparar {input_name} vs {master_name}: {e}")

        # Armazena os resultados desta foto, incluindo o melhor match e todas as comparações
        results[input_name] = {
            "best_match": best_match,
            "best_score": best_score,
            "all_comparisons": comparisons
        }
        logger.info(f"   ✅ Melhor match: {best_match} (Pontuação: {best_score}%)")

    return results


# ==============================
# ☁️ BUSCA EM LOTE NO FIRESTORE
# ==============================
def fetch_collection_data(collection_name: str) -> dict:
    """
    Busca todos os documentos de uma coleção do Firestore e retorna como dicionário.
    A chave é o ID do documento e o valor é o conteúdo (campos) do documento.
    """
    docs = firestore_client.collection(collection_name).stream()
    data = {}
    for doc in docs:
        data[doc.id] = doc.to_dict()
    return data


def save_results_to_firestore(results: dict, results_collection: str = "match_results"):
    """
    Salva os resultados finais das comparações de volta em uma coleção do Firestore.
    Cada chave do dicionário vira um documento separado na coleção de resultados.
    """
    for input_name, result_data in results.items():
        firestore_client.collection(results_collection).document(input_name).set(result_data)
    logger.info(f"💾 Salvos {len(results)} resultados na coleção Firestore: '{results_collection}'")


# ==============================
# 🚀 EXECUÇÃO PRINCIPAL
# ==============================
if __name__ == '__main__':
    # Busca os dados de referência (peças cadastradas) do Firestore
    logger.info("📥 Buscando dados Mestre do Firestore...")
    master_data = fetch_collection_data("garments_master")

    # Busca os dados das fotos tiradas para identificação
    logger.info("📥 Buscando dados das Fotos do Firestore...")
    input_data = fetch_collection_data("garments_taken_photos")

    # Verifica se há dados em ambas as coleções antes de prosseguir
    if not master_data or not input_data:
        logger.warning("⚠️ Dados ausentes! Verifique se as coleções 'garments_master' e 'garments_taken_photos' têm documentos.")
    else:
        logger.info(f"🚀 Iniciando comparação em lote: {len(input_data)} fotos vs {len(master_data)} mestres...\n")

        # Executa a comparação em lote de todas as fotos contra todos os mestres
        final_results = compare_all(master_data, input_data)

        # Exibe um resumo rápido no terminal
        print("\n📊 RESUMO DA COMPARAÇÃO EM LOTE:")
        for photo_id, res in final_results.items():
            print(f" - {photo_id}  -->  {res['best_match']} ({res['best_score']}%)")

        # Salva os resultados no Firestore para consulta futura
        print("\n")
        save_results_to_firestore(final_results, "garments_matches")
