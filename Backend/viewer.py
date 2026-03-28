import tkinter as tk
from tkinter import messagebox
from io import BytesIO
from PIL import Image, ImageTk

from google.cloud import storage, firestore

# ==============================
# 🔧 CONFIGURAÇÃO
# ==============================
GCP_PROJECT_ID = "clothesidentifierapp"  # ID do projeto no Google Cloud
IMAGE_SIZE = (320, 320)                  # Tamanho padrão das imagens na interface

# ==============================
# ☁️ CLIENTES DO GOOGLE CLOUD
# ==============================
# Inicializa os clientes do GCS e Firestore para uso na interface
storage_client = storage.Client(project=GCP_PROJECT_ID)
firestore_client = firestore.Client(project=GCP_PROJECT_ID)


# ==============================
# 📋 ÁREA DE TRANSFERÊNCIA (CLIPBOARD)
# ==============================
def copy_to_clipboard(text: str, root):
    """
    Copia um texto para a área de transferência do sistema operacional.
    Exibe uma mensagem de confirmação quando a cópia for bem-sucedida.

    Parâmetros:
        text: Texto a copiar (normalmente um URI gs://)
        root: Janela Tkinter root ou toplevel para acessar o clipboard
    """
    try:
        root.clipboard_clear()   # Limpa o conteúdo atual do clipboard
        root.clipboard_append(text)  # Adiciona o novo texto
        root.update()            # Garante que o clipboard é atualizado imediatamente
        messagebox.showinfo("Copiado", text)
    except Exception as e:
        print(f"❌ Erro ao copiar para o clipboard: {e}")


# ==============================
# 📥 CARREGAMENTO DE DADOS DO FIRESTORE
# ==============================
def load_results_from_firestore():
    """
    Busca os resultados das correspondências da coleção 'garments_matches' no Firestore.
    Retorna um dicionário no formato {doc_id: dados_do_match} ou None em caso de erro.
    """
    try:
        docs = firestore_client.collection("garments_matches").stream()
        results = {doc.id: doc.to_dict() for doc in docs}
        return results
    except Exception as e:
        print(f"❌ Falha ao carregar resultados do Firestore: {e}")
        return None


def get_image_uri_from_firestore(collection_name: str, doc_id: str) -> str:
    """
    Busca o URI gs:// de uma imagem específica a partir de um documento do Firestore.
    Usado para encontrar as imagens de input e de correspondência (match) para exibição.

    Parâmetros:
        collection_name: Nome da coleção no Firestore ('garments_taken_photos' ou 'garments_master')
        doc_id:          ID do documento cujo URI de imagem queremos recuperar

    Retorno:
        String com o URI gs:// da imagem, ou None se não encontrado
    """
    try:
        doc = firestore_client.collection(collection_name).document(doc_id).get()
        if doc.exists:
            return doc.to_dict().get("image_gcs_uri")  # Retorna o campo de URI da imagem
    except Exception as e:
        print(f"❌ Erro ao buscar documento {doc_id}: {e}")
    return None


# ==============================
# 🖼️ CARREGAMENTO DE IMAGEM DO GCS
# ==============================
def load_image_from_gcs_uri(gcs_uri: str):
    """
    Faz download de uma imagem do GCS usando seu URI exato e a converte para
    um objeto PhotoImage compatível com Tkinter para exibição na interface gráfica.

    Parâmetros:
        gcs_uri: URI gs:// completo da imagem no Google Cloud Storage

    Retorno:
        Objeto ImageTk.PhotoImage pronto para usar em widgets Tkinter, ou None em caso de erro
    """
    if not gcs_uri:
        return None  # URI inválido ou ausente

    try:
        # Extrai o bucket e o blob name a partir do URI gs://
        bucket_name, blob_name = gcs_uri.replace("gs://", "").split("/", 1)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)

        # Verifica se o arquivo existe antes de tentar baixar
        if not blob.exists():
            return None

        # Faz download dos bytes e converte para objeto PIL Image
        image_bytes = blob.download_as_bytes()
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        img = img.resize(IMAGE_SIZE)  # Redimensiona para o tamanho padrão da UI

        # Converte para o formato que o Tkinter entende
        return ImageTk.PhotoImage(img)

    except Exception as e:
        print(f"❌ Erro ao carregar imagem {gcs_uri}: {e}")
        return None


# ==============================
# 🪟 POPUP DE COMPARAÇÃO LADO A LADO
# ==============================
def open_popup(input_id: str, match_id: str, score: int):
    """
    Abre uma janela popup mostrando a foto de consulta (input) ao lado da peça correspondente (match).
    Exibe a pontuação de similaridade e botões para copiar os URIs das imagens.

    Parâmetros:
        input_id: ID do documento de consulta (foto tirada)
        match_id: ID do documento mestre (peça correspondente)
        score:    Pontuação de similaridade entre as duas peças (0-100)
    """
    popup = tk.Toplevel()
    popup.title(f"Comparação: {input_id} vs {match_id}")

    # 1. Busca os URIs exatos das imagens no Firestore
    input_path = get_image_uri_from_firestore("garments_taken_photos", input_id)
    match_path = get_image_uri_from_firestore("garments_master", match_id)

    # 2. Baixa as imagens do GCS e converte para formato Tkinter
    input_img = load_image_from_gcs_uri(input_path)
    match_img = load_image_from_gcs_uri(match_path)

    # --- Layout da interface do popup ---
    tk.Label(popup, text=f"INPUT: {input_id}", font=("Arial", 12, "bold")).grid(row=0, column=0)
    tk.Label(popup, text=f"MATCH: {match_id}", font=("Arial", 12, "bold")).grid(row=0, column=1)

    # Exibe a imagem de consulta (ou mensagem de erro se não disponível)
    if input_img:
        lbl1 = tk.Label(popup, image=input_img)
        lbl1.image = input_img  # ⚠️ IMPORTANTE: mantém referência para evitar garbage collection
        lbl1.grid(row=1, column=0)
    else:
        tk.Label(popup, text="Imagem não encontrada").grid(row=1, column=0)

    # Exibe a imagem da peça correspondente (ou mensagem de erro se não disponível)
    if match_img:
        lbl2 = tk.Label(popup, image=match_img)
        lbl2.image = match_img  # ⚠️ IMPORTANTE: mantém referência para evitar garbage collection
        lbl2.grid(row=1, column=1)
    else:
        tk.Label(popup, text="Imagem não encontrada").grid(row=1, column=1)

    # 📋 Botões para copiar os URIs gs:// das imagens para o clipboard
    if input_path:
        tk.Button(
            popup, text="Copiar URI gs:// do Input",
            command=lambda: copy_to_clipboard(input_path, popup)
        ).grid(row=2, column=0, pady=5)

    if match_path:
        tk.Button(
            popup, text="Copiar URI gs:// do Match",
            command=lambda: copy_to_clipboard(match_path, popup)
        ).grid(row=2, column=1, pady=5)

    # Exibe a pontuação de similaridade com cor indicativa (verde ≥ 70%, laranja < 70%)
    tk.Label(
        popup,
        text=f"Pontuação: {score}",
        font=("Arial", 14, "bold"),
        fg="green" if score >= 70 else "orange"
    ).grid(row=3, column=0, columnspan=2, pady=10)


# ==============================
# 🪟 LISTA DE TODAS AS CORRESPONDÊNCIAS
# ==============================
def open_all_matches(input_id: str, comparisons: list):
    """
    Abre uma janela listando todas as correspondências encontradas para uma foto de consulta,
    ordenadas da maior para a menor pontuação. Cada linha tem um botão para abrir o popup
    de comparação detalhada.

    Parâmetros:
        input_id:    ID da foto de consulta
        comparisons: Lista de dicionários com 'master' (ID) e 'score' (pontuação)
    """
    window = tk.Toplevel()
    window.title(f"Todas as correspondências para: {input_id}")

    # Ordena os resultados da maior para a menor pontuação para facilitar a leitura
    sorted_matches = sorted(comparisons, key=lambda x: x["score"], reverse=True)

    row = 0
    for item in sorted_matches:
        match_id = item["master"]
        score = item["score"]

        # Coluna 0: ID da peça mestre correspondente
        tk.Label(window, text=match_id, width=30, anchor="w").grid(row=row, column=0, padx=5, pady=2)

        # Coluna 1: Pontuação colorida (verde ≥ 70, laranja < 70)
        tk.Label(
            window, text=str(score),
            fg="green" if score >= 70 else "orange"
        ).grid(row=row, column=1, padx=5, pady=2)

        # Coluna 2: Botão para abrir o popup de comparação detalhada
        tk.Button(
            window, text="Ver",
            command=lambda i=input_id, m=match_id, s=score: open_popup(i, m, s)
        ).grid(row=row, column=2, padx=5, pady=2)

        row += 1


# ==============================
# 🖥️ INTERFACE GRÁFICA PRINCIPAL
# ==============================
def build_ui(results: dict):
    """
    Constrói e exibe a janela principal da interface gráfica.
    Lista todas as fotos de consulta com seus melhores matches e opções de ação.

    Parâmetros:
        results: Dicionário com os resultados das correspondências (carregado do Firestore)
    """
    root = tk.Tk()
    root.title("Visualizador do Clothes Matcher (Firestore)")

    # Cria um canvas com scrollbar vertical para suportar muitos resultados
    canvas = tk.Canvas(root)
    scrollbar = tk.Scrollbar(root, orient="vertical", command=canvas.yview)
    container = tk.Frame(canvas)  # Frame interno onde os itens são inseridos

    # Configura o canvas para atualizar a área de scroll quando o conteúdo mudar
    container.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=container, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)

    # Posiciona o canvas e a scrollbar na janela principal
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    row = 0
    for input_id, data in results.items():
        best_match = data.get("best_match")     # ID da melhor correspondência
        score = data.get("best_score", 0)       # Pontuação do melhor match
        comparisons = data.get("all_comparisons", [])  # Lista com todos os matches

        # Ignora entradas sem correspondência válida
        if not best_match:
            continue

        # Coluna 0: ID da foto de consulta
        tk.Label(container, text=input_id, width=30, anchor="w").grid(row=row, column=0, padx=5, pady=5)

        # Coluna 1: Pontuação do melhor match com formatação colorida
        tk.Label(
            container, text=f"{score}%",
            fg="green" if score >= 70 else "orange", font=("Arial", 10, "bold")
        ).grid(row=row, column=1, padx=5, pady=5)

        # Coluna 2: Botão para ver a melhor correspondência em detalhe (popup)
        tk.Button(
            container, text="Melhor Match",
            command=lambda i=input_id, m=best_match, s=score: open_popup(i, m, s)
        ).grid(row=row, column=2, padx=5, pady=5)

        # Coluna 3: Botão para ver todas as correspondências ordenadas por pontuação
        tk.Button(
            container, text="Todos os Matches",
            command=lambda n=input_id, c=comparisons: open_all_matches(n, c)
        ).grid(row=row, column=3, padx=5, pady=5)

        row += 1

    # Define um tamanho inicial razoável para a janela principal
    root.geometry("600x400")
    root.mainloop()  # Inicia o loop de eventos da interface gráfica


# ==============================
# 🚀 PONTO DE ENTRADA PRINCIPAL
# ==============================
def main():
    """
    Ponto de entrada da interface visual. Carrega os resultados do Firestore
    e inicializa a janela principal do visualizador.
    """
    try:
        print("📥 Buscando resultados do Firestore...")
        results = load_results_from_firestore()

        # Verifica se há resultados para exibir
        if not results:
            messagebox.showerror(
                "Erro",
                "Nenhum resultado encontrado na coleção 'garments_matches' do Firestore. "
                "Execute o script de comparação em lote primeiro!"
            )
            return

        print("🎨 Construindo a interface...")
        build_ui(results)  # Constrói e exibe a janela principal

    except Exception as e:
        messagebox.showerror("Erro", str(e))


if __name__ == "__main__":
    main()
