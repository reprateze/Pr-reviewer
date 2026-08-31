# PR Reviewer AI

Ferramenta que se integra ao GitHub e, automaticamente, analisa Pull Requests
para sugerir casos de teste e apontar riscos em funções alteradas.

## Como funciona

1. Um Pull Request é aberto ou atualizado.
2. Uma GitHub Action dispara uma chamada para esta API.
3. A API busca os arquivos alterados do PR (via API do GitHub).
4. Cada arquivo `.py` alterado é analisado com AST para extrair as funções.
5. Cada função relevante é enviada para um LLM, que sugere casos de teste
   e aponta riscos (ex: falta de tratamento de exceção, alta complexidade).
6. Um comentário é postado automaticamente no PR com as sugestões.

## Estrutura do projeto

```
pr-reviewer-ai/
├── app/
│   ├── main.py              # API FastAPI (endpoint /review)
│   ├── code_analyzer.py     # Análise estática com AST
│   ├── llm_client.py        # Chamadas ao modelo de linguagem
│   ├── github_client.py     # Chamadas à API do GitHub
│   ├── comment_formatter.py # Monta o Markdown do comentário
│   └── config.py            # Variáveis de ambiente
├── tests/
│   └── test_code_analyzer.py
├── .github/workflows/
│   └── pr-review.yml        # Workflow que dispara a análise
├── requirements.txt
└── .env.example
```

## Rodando localmente

```bash
# 1. Criar e ativar um ambiente virtual (opcional, mas recomendado)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. Instalar dependências
pip install -r requirements.txt

# 3. Configurar variáveis de ambiente
cp .env.example .env
# edite o .env e preencha GITHUB_TOKEN e LLM_API_KEY

# 4. Rodar a API
uvicorn app.main:app --reload

# 5. Rodar os testes
pytest tests/ -v
```

A API sobe em `http://localhost:8000`. Documentação automática (Swagger) em
`http://localhost:8000/docs`.

## Testando o endpoint manualmente

```bash
curl -X POST http://localhost:8000/review \
  -H "Content-Type: application/json" \
  -d '{
    "owner": "seu-usuario",
    "repo": "seu-repositorio",
    "pr_number": 1,
    "head_ref": "nome-da-branch-ou-sha"
  }'
```

## Configurando no GitHub Actions

1. Faça o deploy da API em algum lugar acessível publicamente (Render,
   Railway, Fly.io, ou um servidor próprio).
2. No repositório onde a ferramenta vai atuar, vá em
   `Settings > Secrets and variables > Actions` e crie o secret
   `REVIEWER_API_URL` apontando para a URL do deploy.
3. Copie o arquivo `.github/workflows/pr-review.yml` para esse repositório.
4. Configure `GITHUB_TOKEN` e `LLM_API_KEY` como variáveis de ambiente
   no serviço onde a API está rodando (não no workflow).

## Próximos passos (MVP em progresso)

- [ ] Deploy da API (Render/Railway)
- [ ] Testar o fluxo completo em um repositório real
- [ ] Rodar o experimento comparando sugestões da IA vs. testes escritos por humanos
- [ ] Escrever a seção de metodologia do TCC com os resultados

## Ideias de expansão (pós-MVP)

Ver a seção "Possíveis expansões" no documento de planejamento do projeto —
inclui suporte a outras linguagens, dashboard, classificação de prioridade,
aprendizado com feedback, entre outras.
