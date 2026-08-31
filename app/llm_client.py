"""
Client responsável por conversar com a API do modelo de linguagem (LLM)
e pedir sugestões de casos de teste para funções alteradas em um PR.

Usa o SDK oficial do Google (google-genai). Inclui controle de taxa de
requisições (rate limiting) porque o nível gratuito do Gemini tem um
limite baixo de chamadas por minuto e por dia.
"""
import json
import time

from google import genai
from google.genai import types

from app.config import settings
from app.code_analyzer import FunctionInfo


SYSTEM_PROMPT = """Você é um assistente de QA especializado em revisão de código.
Dado o código de uma função alterada em um Pull Request, sua tarefa é:

1. Identificar se a função parece ter cobertura de teste adequada.
2. Sugerir de 1 a 4 casos de teste relevantes (incluindo cenários de erro/borda).
3. Apontar riscos específicos (ex: falta de tratamento de exceção, complexidade alta).

Você pode receber um "Contexto adicional" com duas informações extras:
- O código de funções auxiliares chamadas pela função analisada (dependências),
  para você entender o comportamento completo, não só um pedaço isolado.
- Se já existem arquivos de teste no repositório que mencionam essa função.
  Quando isso acontecer, NÃO repita cenários que provavelmente já estão
  cobertos — foque em lacunas reais (casos de borda, erros, tipos inválidos)
  que ainda não parecem testados.

Responda SOMENTE em JSON válido, no seguinte formato, sem nenhum texto adicional
e sem usar blocos de código markdown (```):

{
  "risk_level": "baixo" | "medio" | "alto",
  "risk_reason": "string curta explicando o risco",
  "suggested_tests": [
    {"title": "string", "description": "string"}
  ]
}
"""


class LLMClient:

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ):
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model

        self.client = genai.Client(api_key=self.api_key)

        # Limite gratuito do Gemini:
        # 5 requisições por minuto.
        #
        # Usamos 15 segundos entre chamadas para manter
        # uma margem de segurança.
        self.min_request_interval = 15
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

    def _build_user_prompt(
        self,
        function: FunctionInfo,
        filename: str,
        extra_context: str | None = None,
    ) -> str:
        context_block = f"\nContexto adicional: {extra_context}\n" if extra_context else ""
        return f"""Arquivo: {filename}
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

                response = self.client.models.generate_content(
                    model=self.model,
                    contents=self._build_user_prompt(
                        function,
                        filename,
                        extra_context,
                    ),
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        max_output_tokens=2048,
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
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass

        # Tentativa 2: JSON dentro de algum texto
        start = cleaned.find("{")
        end = cleaned.rfind("}")

        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass

        return {
            "risk_level": "desconhecido",
            "risk_reason": "Não foi possível interpretar a resposta da IA.",
            "suggested_tests": [],
            "raw_response": raw_text,
        }