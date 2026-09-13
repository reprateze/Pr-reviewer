# PR Reviewer AI

Ferramenta que se integra ao GitHub e, automaticamente, analisa Pull Requests
para sugerir casos de teste e apontar riscos em funções alteradas.

## Como funciona

1. Um Pull Request é aberto ou atualizado.
2. Uma GitHub Action dispara uma chamada para esta API.
3. A API busca os arquivos alterados do PR (via API do GitHub).
4. Cada arquivo `.py` alterado é analisado com AST para extrair as funções.
5. Cada função relevante é enviada para um LLM (com contexto de dependências
   — inclusive de outros arquivos do PR — e de testes já existentes), que
   sugere casos de teste e aponta riscos.
6. Um comentário de review é postado **ancorado na linha alterada** de cada
   função (como um revisor humano faria), para permitir reply em thread.
   Quando isso não é possível, a sugestão cai num comentário único de
   fallback no final do PR.
7. O dev pode responder qualquer comentário da IA com `/rate bom` ou
   `/rate ruim` — um webhook captura essa resposta e guarda o feedback no
   banco, que depois é usado como exemplo (few-shot) em análises futuras.

## Estrutura do projeto

```
pr-reviewer-ai/
├── app/
│   ├── main.py              # API FastAPI (/review e /webhook/github)
│   ├── code_analyzer.py     # Análise estática com AST
│   ├── diff_utils.py        # Resolve em quais linhas dá pra ancorar comentário
│   ├── llm_client.py        # Chamadas ao modelo de linguagem
│   ├── github_client.py     # Chamadas à API do GitHub
│   ├── context_gatherer.py  # Busca testes já existentes no repositório
│   ├── comment_formatter.py # Monta o Markdown dos comentários
│   ├── db.py                # Conexão e acesso ao banco (SQLModel)
│   ├── models.py            # Modelo Suggestion (sugestão + feedback)
│   └── config.py            # Variáveis de ambiente
├── tests/
├── .github/workflows/
│   └── pr-review.yml        # Workflow que dispara a análise
├── requirements.txt
└── .env.example
```

## Feedback e aprendizado com o tempo

Cada sugestão é postada como um comentário de review ancorado na linha
alterada (não um bloco único) — isso permite que o dev **responda em
thread** avaliando a sugestão:

```
/rate bom    # ou /rate ruim, ambos aceitam um motivo opcional depois
```

Um webhook (`POST /webhook/github`) recebe essa resposta, identifica de qual
sugestão se trata (via `in_reply_to_id`, que aponta para o comentário
original) e grava o rating no banco. As sugestões marcadas como "bom" são
reaproveitadas como exemplos (few-shot) nas próximas análises — uma forma de
calibrar a qualidade das sugestões com o tempo sem precisar de fine-tuning
(inviável no tier gratuito do Gemini).

Para habilitar isso no repositório onde a ferramenta atua:
`Settings > Webhooks > Add webhook`, com:
- **Payload URL:** `<URL do deploy>/webhook/github`
- **Content type:** `application/json`
- **Secret:** o mesmo valor de `GITHUB_WEBHOOK_SECRET` configurado na API
- **Eventos:** apenas "Pull request review comments"

Quando a linha alterada de uma função não pode ser localizada no diff (ou a
API do GitHub rejeita o comentário por qualquer motivo), a sugestão cai
automaticamente num comentário único no final do PR — sem thread, então sem
feedback rastreável para ela, mas a análise não se perde.

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
4. Configure `GITHUB_TOKEN`, `LLM_API_KEY`, `DATABASE_URL` (Postgres em
   produção — sem isso usa SQLite local, que não persiste entre deploys no
   Render) e `GITHUB_WEBHOOK_SECRET` como variáveis de ambiente no serviço
   onde a API está rodando (não no workflow).
5. Se quiser o fluxo de feedback (`/rate bom`/`/rate ruim`), configure o
   webhook no repositório — ver seção "Feedback e aprendizado com o tempo".

## Próximos passos (MVP em progresso)

- [x] Contexto de dependências entre arquivos do mesmo PR (com orçamento de tokens)
- [x] Comentários de review ancorados na linha alterada + fallback
- [x] Captura de feedback do dev (`/rate bom`/`/rate ruim`) via webhook
- [x] Persistência em banco (SQLite local / Postgres em produção)
- [x] Few-shot com sugestões bem avaliadas em análises futuras
- [ ] Deploy da API com Postgres de verdade (Render/Supabase) — hoje em
      produção ainda está usando o fallback SQLite, que **não persiste**
      entre deploys/restarts do Render
- [ ] Configurar o webhook no(s) repositório(s) onde a ferramenta atua
- [ ] Testar o fluxo completo (sugestão → feedback → few-shot) em um repositório real
- [ ] Rodar o experimento comparando sugestões da IA vs. testes escritos por humanos
- [ ] Escrever a seção de metodologia do TCC com os resultados

## Ideias de expansão (pós-MVP)

- Suporte a outras linguagens além de Python
- Dashboard com o histórico de sugestões e feedback
- Classificação de prioridade das sugestões
- Migrations de schema (ex: Alembic) em vez de `create_all` na inicialização
