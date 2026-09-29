#!/usr/bin/env python3
"""
Compara modelos (de qualquer provedor) analisando uma função com defeito
CONHECIDO, e relata qual detectou, quanto tempo levou e quanto falhou.

Existe porque escolher modelo por reputação não funcionou: na medição do
projeto, um modelo "lite" respondia 14x mais rápido, devolvia risco alto com
justificativa convincente e NÃO achava o defeito real. Velocidade e texto bem
escrito não são evidência de detecção — só a comparação contra um defeito
conhecido é.

Uso (Gemini, o padrão):
    python scripts/comparar_modelos.py --modelos gemini-3.6-flash,gemini-3.5-flash

Uso (qualquer serviço compatível com a API da OpenAI):
    python scripts/comparar_modelos.py \
        --provider openai \
        --base-url https://api.groq.com/openai/v1 \
        --api-key SUA_CHAVE \
        --modelos modelo-a,modelo-b

Para medir estabilidade (o mesmo modelo acerta sempre?), repita:
    ... --repeticoes 3

Nada é postado em lugar nenhum e nada é gravado no banco: é análise local.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app.llm_client as llm_module  # noqa: E402
from app.code_analyzer import CodeAnalyzer  # noqa: E402
from app.llm_client import LLMClient  # noqa: E402


# Caso padrão: `self.REMOVER` não existe na classe (o atributo certo é
# `self.REMOVER_BUTTON`), então o método estoura AttributeError na primeira
# execução. Defeito real, de um PR real do projeto de testes usado como cobaia.
CODIGO_PADRAO = '''
from pages.base_page import BasePage


class CartPage(BasePage):
    REMOVER_BUTTON = ".cart_quantity_delete"

    def remover_todos_produtos(self):
        while self.page.locator(self.REMOVER_BUTTON).count() > 0:
            quantidade_antes = self.page.locator(self.REMOVER).count()
            self.page.locator(self.REMOVER_BUTTON).first.click()
            self.page.wait_for_function(
                f"document.querySelectorAll('{self.REMOVER_BUTTON}').length < {quantidade_antes}"
            )
'''

FUNCAO_PADRAO = "remover_todos_produtos"

# Sinais de que a resposta falou do defeito certo, e não de outro assunto
# plausível. Propositalmente simples: a conferência final é humana, olhando a
# coluna "motivo" impressa no fim.
PISTAS_PADRAO = ("REMOVER", "atributo", "attribute", "seletor")


def detectou(analise: dict, pistas: tuple) -> bool:
    texto = " ".join(
        [
            analise.get("risk_reason") or "",
            *[
                f"{m.get('issue', '')} {m.get('suggestion', '')}"
                for m in analise.get("suggested_improvements") or []
            ],
        ]
    )
    # Exige duas pistas: uma só (por exemplo "seletor") aparece em respostas
    # genéricas que não identificaram o problema.
    return sum(1 for p in pistas if p.lower() in texto.lower()) >= 2


def main():
    p = argparse.ArgumentParser(description="Compara modelos contra um defeito conhecido.")
    p.add_argument("--modelos", required=True, help="separados por vírgula")
    p.add_argument("--provider", default="gemini", choices=["gemini", "openai"])
    p.add_argument("--base-url", default="", help="obrigatório com --provider openai")
    p.add_argument("--api-key", default="", help="padrão: LLM_API_KEY do ambiente")
    p.add_argument("--arquivo", default="", help=".py com o código a analisar")
    p.add_argument("--funcao", default=FUNCAO_PADRAO)
    p.add_argument("--repeticoes", type=int, default=1)
    p.add_argument(
        "--intervalo", type=int, default=0,
        help="segundos entre chamadas (suba se tomar 429)",
    )
    args = p.parse_args()

    codigo = Path(args.arquivo).read_text(encoding="utf-8") if args.arquivo else CODIGO_PADRAO

    funcoes = [f for f in CodeAnalyzer().analyze_source(codigo) if f.name == args.funcao]
    if not funcoes:
        sys.exit(f"Função {args.funcao!r} não encontrada no código informado.")
    func = funcoes[0]

    llm_module.settings.llm_provider = args.provider
    if args.base_url:
        llm_module.settings.llm_base_url = args.base_url

    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]
    linhas = []

    for modelo in modelos:
        acertos = falhas = 0
        tempos = []
        ultimo_motivo = ""

        for _ in range(args.repeticoes):
            llm = LLMClient(api_key=args.api_key or None, model=modelo)
            # Uma tentativa e um modelo por vez: aqui o objetivo é medir este
            # modelo, não contornar a indisponibilidade dele.
            llm.models_chain = [modelo]
            llm.min_request_interval = args.intervalo

            inicio = time.time()
            analise = llm.suggest_tests_for_function(
                func, "pages/cart_page.py", max_retries=0
            )
            tempos.append(time.time() - inicio)

            if analise.get("risk_level") == "desconhecido":
                falhas += 1
            else:
                ultimo_motivo = analise.get("risk_reason") or ""
                if detectou(analise, PISTAS_PADRAO):
                    acertos += 1

        media = sum(tempos) / len(tempos) if tempos else 0
        linhas.append((modelo, acertos, falhas, media, ultimo_motivo))
        print(
            f"  {modelo:<44} detectou {acertos}/{args.repeticoes}  "
            f"falhou {falhas}  {media:5.1f}s",
            flush=True,
        )

    print("\n" + "=" * 78)
    print(f"{'modelo':<44} {'detectou':>9} {'falhou':>7} {'tempo':>7}")
    print("=" * 78)
    for modelo, acertos, falhas, media, _ in linhas:
        print(f"{modelo:<44} {acertos:>6}/{args.repeticoes} {falhas:>7} {media:6.1f}s")

    print("\nJustificativa de cada modelo (confira você: a heurística de")
    print("detecção erra, e quem decide se é o mesmo defeito é humano):")
    for modelo, _, _, _, motivo in linhas:
        if motivo:
            print(f"\n--- {modelo}\n{motivo[:300]}")


if __name__ == "__main__":
    main()
