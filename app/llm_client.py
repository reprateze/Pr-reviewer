"""
Client responsável por conversar com a API do modelo de linguagem (LLM)
e pedir sugestões de casos de teste para funções alteradas em um PR.

Usa o SDK oficial do Google (google-genai). Inclui controle de taxa de
requisições (rate limiting) porque o nível gratuito do Gemini tem um
limite baixo de chamadas por minuto e por dia.
"""
import json
import re
import time

from google import genai
from google.genai import types

from app.config import settings
from app.code_analyzer import FunctionInfo


# Cobre a maioria dos emojis comuns (pictogramas, símbolos, dingbats, bandeiras
# e o seletor de variação que às vezes vem grudado neles). O Gemini às vezes
# solta emoji em campos de texto mesmo sem o prompt pedir — isso limpa depois
# da resposta, sem depender só de instrução no prompt.
_EMOJI_PATTERN = re.compile(
    "["
    "\U0001F300-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "️"
    "]+",
    flags=re.UNICODE,
)


def _strip_emojis(value):
    """Remove emojis recursivamente de strings, listas e dicts."""
    if isinstance(value, str):
        return _EMOJI_PATTERN.sub("", value).strip()
    if isinstance(value, list):
        return [_strip_emojis(v) for v in value]
    if isinstance(value, dict):
        return {k: _strip_emojis(v) for k, v in value.items()}
    return value


SYSTEM_PROMPT_V1 = """Você é um assistente de QA especializado em revisão de código.
Dado o código de uma função alterada em um Pull Request, sua tarefa é:

1. Identificar se a função parece ter cobertura de teste adequada.
2. Sugerir de 1 a 4 casos de teste relevantes (incluindo cenários de erro/borda).
3. Apontar riscos específicos (ex: falta de tratamento de exceção, complexidade alta).
4. Quando identificar um problema concreto no código (não só um cenário sem
   teste, mas um bug real ou uma forma claramente melhor de implementar algo),
   aponte o problema E como corrigir — com um trecho de código sugerido quando
   fizer sentido. NÃO invente melhorias só para preencher; se o código está
   bem escrito, deixe a lista vazia.

Não use emojis em nenhum campo de texto da resposta.

Você pode receber um "Contexto adicional" com duas informações extras:
- O código de funções auxiliares chamadas pela função analisada (dependências),
  para você entender o comportamento completo, não só um pedaço isolado.
- Se já existem arquivos de teste no repositório que mencionam essa função.
  Quando isso acontecer, NÃO repita cenários que provavelmente já estão
  cobertos — foque em lacunas reais (casos de borda, erros, tipos inválidos)
  que ainda não parecem testados.

IMPORTANTE sobre origem do problema: se o risco real está numa função
AUXILIAR (dependência) usada pela função analisada — não na função analisada
em si — deixe isso EXPLÍCITO no texto: diga qual função tem o problema de
verdade (ex: "O problema está em validar_numero(), chamada por esta função:
..."). O comentário fica ancorado na função analisada (é o que mudou no PR),
então sem essa indicação fica ambíguo qual código o dev precisa editar.

Responda SOMENTE em JSON válido, no seguinte formato, sem nenhum texto adicional
e sem usar blocos de código markdown (```):

{
  "risk_level": "baixo" | "medio" | "alto",
  "risk_reason": "string curta explicando o risco",
  "suggested_tests": [
    {"title": "string", "description": "string"}
  ],
  "suggested_improvements": [
    {"issue": "string curta descrevendo o problema (cite a função certa se for numa dependência)", "suggestion": "string explicando a correção, pode incluir um trecho de código"}
  ]
}
"""


# Versão 2: busca ativa de defeito, não só sugestão de teste.
#
# Motivação, medida e não suposta: rodando a V1 contra defeitos reais
# reconstituídos do histórico do scrapy, a taxa de detecção foi 0 de 6. As
# respostas descreviam a função ("realiza autenticação básica", "lógica
# simples de limite de itens") em vez de procurar o erro — num dos casos, a
# V1 afirmou que o tratamento estava "adequado" exatamente na função que
# tinha o defeito.
#
# Duas mudanças de fundo em relação à V1:
# 1. Procurar defeito vira a tarefa NÚMERO UM, não a quarta.
# 2. A lista de verificação não é genérica: cada item corresponde a uma
#    categoria de defeito observada nos bugs reais que serviram de medição.
SYSTEM_PROMPT_V2 = """Você é um revisor de código experiente analisando uma função
alterada em um Pull Request.

Sua tarefa PRINCIPAL é procurar defeitos. Descrever o que a função faz não é
revisar — se sua resposta apenas resume o comportamento do código, ela não
serve. Assuma que pode haver um erro ali e vá atrás dele.

Verifique explicitamente, um a um:

1. CONDIÇÕES DE BORDA — o que acontece com entrada vazia, zero, None, um
   único elemento, valor negativo, coleção sem o item esperado? Alguma
   dessas quebra ou produz resultado errado?
2. SUPOSIÇÕES NÃO VERIFICADAS — o código assume formato, presença ou
   estrutura da entrada sem checar? (ex: assumir que um host sempre tem
   ponto, que um dicionário sempre tem certa chave, que uma lista não é
   vazia)
3. ORDEM DE OPERAÇÕES COM ESTADO — quando o código altera estado
   (dicionário, lista, atributo), a ordem está certa? Remover antes de
   inserir, atualizar antes de validar e afins produzem efeito errado?
4. VARIÁVEL OU ARGUMENTO TROCADO — o código usa a variável certa em cada
   ponto? Há uma variável parecida por perto que deveria estar sendo usada
   no lugar?
5. CAMINHOS DE SAÍDA E RECURSOS — em todos os caminhos (inclusive erro e
   saída antecipada), conexões/arquivos/locks são liberados? Exceções são
   tratadas ou vazam?
6. LIMITES E COMPARAÇÕES — índices, fatias e comparações estão corretos?
   Há erro de um a mais/menos, ou `<` onde deveria ser `<=`?

Depois disso:
- Sugira de 1 a 4 casos de teste relevantes, priorizando os cenários que
  exercitam os pontos frágeis que você encontrou.
- Para cada defeito concreto encontrado, descreva o problema E a correção,
  com trecho de código quando fizer sentido.

Sobre honestidade: NÃO invente defeito para preencher a lista. Código correto
existe. Mas também não afirme que algo está "adequado" ou "bem tratado" sem
ter verificado os pontos acima — na dúvida, aponte a dúvida.

Não use emojis em nenhum campo de texto da resposta.

Você pode receber um "Contexto adicional" com informações extras:
- O código de funções auxiliares chamadas pela função analisada, para você
  entender o comportamento completo, não só um pedaço isolado.
- Trechos semelhantes do próprio repositório, para você perceber padrões já
  adotados no projeto e notar quando esta função destoa deles ou reimplementa
  algo que já existe.
- Arquivos de teste que já mencionam essa função. Quando isso acontecer, NÃO
  repita cenários já cobertos — foque nas lacunas.

IMPORTANTE sobre origem do problema: se o defeito está numa função AUXILIAR
(dependência) e não na função analisada, deixe isso EXPLÍCITO: diga qual
função tem o problema de verdade (ex: "O problema está em validar_numero(),
chamada por esta função: ..."). O comentário fica ancorado na função
analisada, então sem essa indicação o dev não sabe onde mexer.

Responda SOMENTE em JSON válido, no seguinte formato, sem nenhum texto adicional
e sem usar blocos de código markdown (```):

{
  "risk_level": "baixo" | "medio" | "alto",
  "risk_reason": "string curta: o defeito encontrado, ou por que o código parece correto depois da verificação",
  "suggested_tests": [
    {"title": "string", "description": "string"}
  ],
  "suggested_improvements": [
    {"issue": "string curta descrevendo o defeito (cite a função certa se for numa dependência)", "suggestion": "string explicando a correção, pode incluir um trecho de código"}
  ]
}
"""


PROMPTS = {"v1": SYSTEM_PROMPT_V1, "v2": SYSTEM_PROMPT_V2}


class LLMClient:

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

        # Qual versão do prompt usar. Existe como parâmetro (e é registrada
        # junto de cada análise) porque a comparação entre versões é um
        # experimento: sem saber qual prompt gerou cada resultado, os dados
        # acumulados no banco ficam ininterpretáveis depois.
        self.prompt_version = prompt_version or settings.prompt_version
        self.system_prompt = PROMPTS.get(self.prompt_version, SYSTEM_PROMPT_V2)

        self.client = genai.Client(api_key=self.api_key)

        # Intervalo mínimo entre chamadas, calibrado conforme o RPM do
        # modelo configurado (ver LLM_MIN_REQUEST_INTERVAL_SECONDS).
        self.min_request_interval = settings.llm_min_request_interval_seconds
        self.last_request_time = 0.0

    def _wait_for_rate_limit(self):
        """
        Garante um intervalo mínimo entre chamadas para a API.
        """
        elapsed = time.time() - self.last_request_time

        if elapsed < self.min_request_interval:
            wait_time = self.min_request_interval - elapsed
            print(
                f"[LLM] Rate limit: aguardando "
                f"{wait_time:.1f}s antes da próxima requisição..."
            )
            time.sleep(wait_time)

        self.last_request_time = time.time()

    def _format_few_shot_examples(self, examples: list[dict]) -> str:
        """Formata sugestões passadas bem avaliadas pelos devs, usadas como
        referência de estilo/qualidade esperado (few-shot)."""
        blocks = []
        for ex in examples:
            tests = "\n".join(
                f"  - {t.get('title', '')}: {t.get('description', '')}"
                for t in ex.get("suggested_tests", [])
            ) or "  - (sem testes sugeridos)"
            blocks.append(
                f"Função: {ex.get('function_name', '?')}\n"
                f"Risco: {ex.get('risk_level', '?')} — {ex.get('risk_reason', '')}\n"
                f"Testes sugeridos:\n{tests}"
            )
        return "\n\n".join(blocks)

    def _build_user_prompt(
        self,
        function: FunctionInfo,
        filename: str,
        extra_context: str | None = None,
        few_shot_examples: list[dict] | None = None,
    ) -> str:
        context_block = f"\nContexto adicional: {extra_context}\n" if extra_context else ""

        few_shot_block = ""
        if few_shot_examples:
            few_shot_block = (
                "\nExemplos de análises anteriores que devs avaliaram como boas "
                "(use como referência de estilo e profundidade, não copie o "
                f"conteúdo):\n{self._format_few_shot_examples(few_shot_examples)}\n"
            )

        return f"""{few_shot_block}Arquivo: {filename}
Função: {function.name}
Linhas: {function.num_lines}
Complexidade aproximada (nº de branches): {function.num_branches}
Possui try/except: {function.has_try_except}
{context_block}
Código:
```python
{function.source}
```
"""

    def suggest_tests_for_function(
        self,
        function: FunctionInfo,
        filename: str,
        max_retries: int = 2,
        extra_context: str | None = None,
        few_shot_examples: list[dict] | None = None,
    ) -> dict:

        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                # Controla o intervalo entre chamadas
                self._wait_for_rate_limit()

                print(
                    f"[LLM] Analisando "
                    f"{filename} → {function.name}()"
                )
                if extra_context:
                    print(f"[LLM] Contexto extra enviado:\n{extra_context}\n")

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=self._build_user_prompt(
                        function,
                        filename,
                        extra_context,
                        few_shot_examples,
                    ),
                    config=types.GenerateContentConfig(
                        system_instruction=self.system_prompt,
                        max_output_tokens=settings.llm_max_output_tokens,
                        response_mime_type="application/json",
                    ),
                )

                raw_text = response.text or ""

                print("\n========== RESPOSTA RAW DO GEMINI ==========")
                print(repr(raw_text))
                print("============================================\n")

                if not raw_text.strip():
                    raise ValueError(
                        "Resposta vazia da IA (possível corte por limite de tokens)"
                    )

                return self._parse_response(raw_text)

            except Exception as exc:
                last_error = exc
                error_message = str(exc).upper()

                is_rate_limited = (
                    "429" in error_message
                    or "RESOURCE_EXHAUSTED" in error_message
                    or "RATE LIMIT" in error_message
                )

                is_overloaded = (
                    "503" in error_message
                    or "UNAVAILABLE" in error_message
                )

                # ----------------------------------------
                # 429 = limite de requisições
                # ----------------------------------------
                if is_rate_limited:
                    if attempt < max_retries:
                        wait_time = 30 * (attempt + 1)
                        print(
                            f"[LLM] Limite da API atingido (429). "
                            f"Aguardando {wait_time}s..."
                        )
                        time.sleep(wait_time)
                        continue

                    print(
                        "[LLM] Limite da API atingido. "
                        "Não foi possível realizar a análise."
                    )
                    break

                # ----------------------------------------
                # 503 = servidor indisponível
                # ----------------------------------------
                if is_overloaded:
                    if attempt < max_retries:
                        wait_time = 5 * (attempt + 1)
                        print(
                            f"[LLM] Gemini indisponível (503). "
                            f"Aguardando {wait_time}s..."
                        )
                        time.sleep(wait_time)
                        continue

                    break

                # ----------------------------------------
                # Outros erros
                # ----------------------------------------
                print(f"[LLM] Erro inesperado: {exc}")
                break

        return {
            "risk_level": "desconhecido",
            "risk_reason": f"Erro ao consultar IA: {last_error}",
            "suggested_tests": [],
        }

    def _parse_response(self, raw_text: str) -> dict:
        """Extrai o JSON da resposta da IA."""
        cleaned = raw_text.strip()

        # Remove possíveis blocos Markdown
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]

        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

        cleaned = cleaned.strip()

        # Tentativa 1: JSON puro
        try:
            return _strip_emojis(json.loads(cleaned))
        except json.JSONDecodeError:
            pass

        # Tentativa 2: JSON dentro de algum texto
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start != -1 and end != -1 and end > start:
            try:
                return _strip_emojis(json.loads(cleaned[start:end + 1]))
            except json.JSONDecodeError:
                pass

        return {
            "risk_level": "desconhecido",
            "risk_reason": "Não foi possível interpretar a resposta da IA.",
            "suggested_tests": [],
            "raw_response": raw_text,
        }