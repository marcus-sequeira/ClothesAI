import io
import logging
from typing import Optional

from google.cloud import storage
from rembg import remove, new_session
from PIL import Image, ImageFilter

# ==============================
# 🪵 LOGGING
# ==============================
logger = logging.getLogger(__name__)

# ==============================
# 🧠 INICIALIZAÇÃO DO MODELO (GLOBAL)
# ==============================
# Carrega o modelo ISNet de remoção de fundo uma única vez no início do programa.
# ISNet é um modelo de segmentação de imagem otimizado para objetos gerais.
# Manter o modelo em escopo global evita recarregamento a cada chamada da função.
logger.info("🧠 Carregando modelo de produção (ISNet)...")
session = new_session("isnet-general-use")


# ==============================
# ☁️ UTILITÁRIO DO GCS
# ==============================
def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """
    Faz o parsing de um URI do Google Cloud Storage no formato gs://.
    Retorna uma tupla com (nome_do_bucket, caminho_do_blob).

    Exemplo: 'gs://meu-bucket/pasta/imagem.jpg' → ('meu-bucket', 'pasta/imagem.jpg')
    """
    if not uri.startswith("gs://"):
        raise ValueError(f"URI inválido do GCS: {uri}")
    parts = uri[5:].split("/", 1)
    return parts[0], parts[1]


# ==============================
# ✨ REFINAMENTO DA MÁSCARA ALPHA
# ==============================
def refine_alpha(img: Image.Image) -> Image.Image:
    """
    Melhora as bordas da máscara de transparência (canal alpha) sem escurecer a imagem.
    Aplica um leve suavizamento nas bordas e aumenta levemente a opacidade geral.

    O canal alpha controla a transparência: 0 = totalmente transparente, 255 = totalmente opaco.
    """
    # Separa os 4 canais da imagem RGBA
    r, g, b, a = img.split()

    # Suaviza as bordas da máscara alpha com um leve blur gaussiano
    # Isso remove serrilhados e artefatos nas bordas da roupa
    a = a.filter(ImageFilter.GaussianBlur(1))

    # Aumenta levemente a opacidade da máscara (sem escurecer os pixels RGB)
    # Fator 1.15 = aumenta 15% a opacidade, limitando ao máximo de 255
    a = a.point(lambda p: min(255, int(p * 1.15)))

    # Reconstrói a imagem RGBA com o canal alpha refinado
    return Image.merge("RGBA", (r, g, b, a))


# ==============================
# 📦 RECORTE AUTOMÁTICO + PADDING
# ==============================
def crop_and_center(img: Image.Image, padding_ratio=0.08) -> Image.Image:
    """
    Recorta a imagem ao redor do objeto detectado e adiciona um espaço em branco nas bordas.
    Isso centraliza e dá "respiro" à peça de roupa na imagem final.

    Parâmetros:
        img:           Imagem RGBA com o fundo removido
        padding_ratio: Proporção de espaço a adicionar em cada lado (padrão: 8%)
    """
    # getbbox() retorna a caixa delimitadora dos pixels não-transparentes
    bbox = img.getbbox()
    if bbox is None:
        return img  # Se não há pixels visíveis, retorna a imagem sem alteração

    # Recorta a imagem ao mínimo bounding box do objeto
    img = img.crop(bbox)

    # Calcula o padding em pixels baseado no tamanho do objeto recortado
    w, h = img.size
    pad_w = int(w * padding_ratio)  # Padding horizontal
    pad_h = int(h * padding_ratio)  # Padding vertical

    # Cria uma nova imagem maior com fundo transparente e cola a peça no centro
    new_img = Image.new("RGBA", (w + pad_w * 2, h + pad_h * 2), (0, 0, 0, 0))
    new_img.paste(img, (pad_w, pad_h))

    return new_img


# ==============================
# 🧼 REMOÇÃO DE ARTEFATOS PEQUENOS
# ==============================
def remove_small_islands(img: Image.Image, min_size=500):
    """
    Remove detecções muito pequenas que provavelmente são artefatos de remoção de fundo.
    Se o objeto detectado for menor que min_size pixels quadrados, descartamos a imagem.

    Parâmetros:
        img:      Imagem RGBA com o fundo removido
        min_size: Área mínima aceitável em pixels (padrão: 500)

    Retorno:
        A imagem original se a área for suficiente, None se for muito pequena
    """
    alpha = img.split()[-1]  # Extrai apenas o canal de transparência
    bbox = alpha.getbbox()   # Obtém os limites do objeto visível

    if bbox is None:
        return None  # Nenhum pixel visível → descarta

    # Calcula a área do bounding box
    area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
    if area < min_size:
        return None  # Objeto muito pequeno → provavelmente é um artefato → descarta

    return img  # Objeto tem tamanho suficiente → mantém


# ==============================
# 🎨 FUNDO BRANCO OPCIONAL
# ==============================
def add_white_background(img: Image.Image) -> Image.Image:
    """
    Adiciona um fundo branco sólido atrás da peça de roupa.
    Útil quando o destino não suporta transparência (ex: JPEG, algumas plataformas web).

    Converte o resultado de RGBA (com transparência) para RGB (fundo branco).
    """
    # Cria uma imagem RGB com fundo branco do mesmo tamanho
    bg = Image.new("RGB", img.size, (255, 255, 255))

    # Cola a peça sobre o fundo branco usando o canal alpha como máscara
    bg.paste(img, mask=img.split()[-1])
    return bg


# ==============================
# ✂️ FUNÇÃO PRINCIPAL
# ==============================
def isolate_clothing_in_gcs(
    input_uri: str,
    output_uri: str,
    is_fuzzy_item: bool = False,   # Mantido por compatibilidade, não utilizado ativamente
    show_popup: bool = False,      # Mantido por compatibilidade, exibe prévia se True
    make_white_bg: bool = False,   # Se True, adiciona fundo branco (salva como JPEG)
    client: Optional[storage.Client] = None  # Cliente GCS (criado automaticamente se None)
) -> None:
    """
    Pipeline completo de remoção de fundo de uma imagem armazenada no GCS:
    1. Baixa a imagem do GCS
    2. Remove o fundo com o modelo ISNet
    3. Refina as bordas da máscara
    4. Remove artefatos pequenos
    5. Recorta e centraliza a peça
    6. (Opcional) Adiciona fundo branco
    7. Salva o resultado de volta no GCS

    Parâmetros:
        input_uri:     URI gs:// da imagem de entrada
        output_uri:    URI gs:// onde salvar a imagem processada
        is_fuzzy_item: Reservado para uso futuro (compatibilidade)
        show_popup:    Se True, exibe a imagem processada na tela durante o processamento
        make_white_bg: Se True, salva com fundo branco (JPEG); caso contrário, PNG transparente
        client:        Cliente GCS (opcional; criado automaticamente se não fornecido)
    """

    # Cria um cliente GCS padrão se nenhum foi fornecido
    if client is None:
        client = storage.Client()

    # Extrai o bucket e o blob name dos URIs de entrada e saída
    in_bucket, in_blob = parse_gcs_uri(input_uri)
    out_bucket, out_blob = parse_gcs_uri(output_uri)

    # ==============================
    # 1. DOWNLOAD DA IMAGEM DO GCS
    # ==============================
    logger.debug(f"📥 Baixando: {input_uri}")
    input_bytes = client.bucket(in_bucket).blob(in_blob).download_as_bytes()

    # ==============================
    # 2. REMOÇÃO DO FUNDO COM IA
    # ==============================
    logger.debug("🤖 Executando remoção de fundo...")
    output_bytes = remove(
        input_bytes,
        session=session,      # Usa o modelo ISNet pré-carregado globalmente
        alpha_matting=True    # Ativa alpha matting para bordas mais suaves e precisas
    )

    # Converte os bytes resultantes para objeto PIL Image no modo RGBA
    img = Image.open(io.BytesIO(output_bytes)).convert("RGBA")

    # ==============================
    # 3. REFINAMENTO DAS BORDAS (SEM ESCURECER A IMAGEM)
    # ==============================
    img = refine_alpha(img)

    # ==============================
    # 4. REMOÇÃO DE ARTEFATOS PEQUENOS
    # ==============================
    img = remove_small_islands(img)
    if img is None:
        # Nenhuma roupa detectada de forma confiável nesta imagem → aborta
        logger.warning(f"⚠️ Nenhuma roupa detectada de forma válida em {input_uri}")
        return

    # ==============================
    # 5. RECORTE + CENTRALIZAÇÃO COM PADDING
    # ==============================
    img = crop_and_center(img)

    # ==============================
    # 6. FUNDO BRANCO OPCIONAL
    # ==============================
    if make_white_bg:
        img = add_white_background(img)

    # ==============================
    # 7. SERIALIZAÇÃO PARA BYTES
    # ==============================
    byte_io = io.BytesIO()

    if make_white_bg:
        # Com fundo branco → salva como JPEG (sem transparência, menor tamanho)
        img.save(byte_io, format="JPEG", quality=95, subsampling=0)
        content_type = "image/jpeg"
    else:
        # Sem fundo branco → salva como PNG (mantém transparência)
        img.save(byte_io, format="PNG", optimize=True)
        content_type = "image/png"

    # Exibe a imagem na tela se o modo popup estiver ativado (útil para debug local)
    if show_popup:
        img.show()

    # ==============================
    # 8. UPLOAD DE VOLTA PARA O GCS
    # ==============================
    logger.debug(f"📤 Enviando resultado: {output_uri}")
    client.bucket(out_bucket).blob(out_blob).upload_from_string(
        byte_io.getvalue(),
        content_type=content_type
    )

    logger.debug("✅ Processamento concluído.")
