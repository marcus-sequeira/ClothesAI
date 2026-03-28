# Clothes Identifier App

Este repositório contém o back-end e os pipelines de inteligência artificial de um sistema de identificação e correspondência de roupas. O sistema é capaz de receber imagens de roupas, opcionalmente remover o fundo (plano de fundo), descrever visualmente cada peça utilizando a IA generativa (Gemini) e encontrar correspondências de alto grau de semelhança num banco de roupas mestre (referência).

## 📂 Estrutura de Arquivos e Componentes

Abaixo está descrita a finalidade de cada um dos principais arquivos do projeto, organizados pelo papel fundamental na aplicação.

### 🚀 Serviços e Pipelines Principais
*   **`run_server.py`**: Ponto de entrada principal para a comunicação entre a aplicação front-end (ex: celular) e a nuvem. Inicia uma API REST usando **Flask**, mapeando endpoints fundamentais (`/api/identify` e `/api/import`) para receber, processar de forma síncrona, coordenar e responder chamadas com base em imagens submetidas em tempo real.
*   **`main_pipeline.py`**: Script mestre orquestrador para processamento "em lote" (bulk). Coordenador integral capaz de upar pastas para nuvem, chamar a limpeza dos fundos, solicitar analises estruturais ao Gemini e fazer cruzamentos (Matches) gerando JSONs.
*   **`pipeline_one_at_a_time.py`**: Versão simplificada do pipeline principal, projetada para processar uma única imagem por vez. Ideal para testes rápidos ou integração direta com a API REST, permitindo uma abordagem mais controlada e iterativa.
### 🧠 Visão Computacional e Motores de Inteligência Artificial
*   **`garment_analyzer.py`**: Responsável por "olhar" os pixels visuais e traduzir em características descritas por máquinas através do uso de IA multimodal da Google **(Gemini via Vertex AI)**. Ele decodifica a roupa lendo estilo, silhueta formal, variações tipográficas do tecido ou estampas, devolvendo um resultado em JSON estruturado a ser salvo na nuvem Firestore.
*   **`comparison_functions.py`**: Módulo dedicado exclusivamente a funções de comparação e cálculo de similaridade. Ele contém algoritmos para comparar as características extraídas das roupas (descrições textuais e vetoriais) e calcular escores de correspondência, utilizando técnicas como similaridade de cossenos para vetores de embeddings.
*   **`matching_engine.py`**: Motor de correspondência central que integra os resultados do `garment_analyzer` e as funções de comparação para gerar uma lista ordenada de correspondências. Ele consulta o banco de dados mestre, calcula os escores de similaridade e retorna as melhores correspondências para cada peça de roupa analisada.
*   **`image_vector_embeddings_comparison.py`**: Módulo ainda incompleto, destinado a implementar a comparação de vetores de embeddings de imagens. Ele será responsável por extrair características visuais das roupas e comparar essas características usando técnicas avançadas de machine learning para melhorar a precisão das correspondências.
### ☁️ Armazenamento, Limpeza de Dados e Utilitários
*   **`gcs_functions.py`**: Conjunto de funções utilitárias para interagir com o Google Cloud Storage (GCS). Ele inclui funcionalidades para upload, download, listagem e gerenciamento de arquivos e pastas no GCS, facilitando a integração do sistema com a nuvem.
*   **`removeClothingBackground.py`**: Script dedicado à remoção de fundos de imagens de roupas. Ele utiliza técnicas avançadas de segmentação de imagem para isolar a peça de roupa do plano de fundo, com intuito de melhorar a qualidade dos dados para análise e correspondência. (Esse módulo não aumentou a assertividade do modelo, mas é mantido para fins de comparação e possível uso futuro em casos específicos onde o fundo possa interferir na análise).

### 🖥️  Interface Gráfica e Planejamento
*   **`viewer.py`**: Interface gráfica simples para visualização de imagens e resultados de análise. Ele pode ser usado para exibir as roupas processadas, suas descrições e as correspondências encontradas, facilitando a validação visual dos resultados.
*   **`TODO.ipynb`**: Notebook de planejamento e brainstorming, onde são listadas ideias, tarefas futuras e melhorias a serem implementadas no sistema. Ele serve como um guia para o desenvolvimento contínuo do projeto, permitindo que a equipe organize e priorize as próximas etapas de trabalho.

## 🛠️ Tecnologias Utilizadas

* 🐍 **Python + Flask** → API que recebe as imagens e orquestra todo o fluxo 
* ☁️ **Google Cloud (GCS + Firestore)** → armazenamento das imagens e metadados das peças
* 🤖 **IA / Visão Computacional** →
* rembg (ISNet) para remoção de fundo
* modelos vision (Gemini) para extrair atributos da roupa
* 🖼️ **Pillow (PIL)** → tratamento das imagens (resize, crop, refinamento)
* 📱 **React Native** → app mobile consumindo a API
* ⚙️ **Extras** → hashing (MD5) pra evitar duplicados + processamento em memória pra performance.
* Uso de Embeddings e técnicas de NLP para comparação textual.








