import app.llm_client as llm_client_module
from app.llm_client import LLMClient


def test_parse_valid_json():
    client = LLMClient.__new__(LLMClient)

    raw_response = """
    {
        "risk_level": "alto",
        "risk_reason": "A função possui múltiplos cenários de erro.",
        "suggested_tests": [
            {
                "title": "Valor inválido",
                "description": "Testar valores menores ou iguais a zero."
            },
            {
                "title": "Percentual inválido",
                "description": "Testar percentuais negativos ou acima de 100."
            }
        ]
    }
    """

    result = client._parse_response(raw_response)

    assert result["risk_level"] == "alto"
    assert len(result["suggested_tests"]) == 2


def test_parse_response_strips_emojis_from_text_fields():
    client = LLMClient.__new__(LLMClient)

    raw_response = """
    {
        "risk_level": "alto",
        "risk_reason": "Risco alto \U0001F525 por falta de tratamento de erro.",
        "suggested_tests": [
            {"title": "✅ Caso feliz", "description": "Testar o fluxo normal."}
        ]
    }
    """

    result = client._parse_response(raw_response)

    assert "\U0001F525" not in result["risk_reason"]
    assert "Risco alto" in result["risk_reason"]
    assert "✅" not in result["suggested_tests"][0]["title"]
    assert "Caso feliz" in result["suggested_tests"][0]["title"]


def test_parse_response_keeps_suggested_improvements():
    client = LLMClient.__new__(LLMClient)

    raw_response = """
    {
        "risk_level": "medio",
        "risk_reason": "-",
        "suggested_tests": [],
        "suggested_improvements": [
            {"issue": "isinstance aceita bool", "suggestion": "Excluir bool explicitamente."}
        ]
    }
    """

    result = client._parse_response(raw_response)

    assert result["suggested_improvements"] == [
        {"issue": "isinstance aceita bool", "suggestion": "Excluir bool explicitamente."}
    ]


def test_seleciona_o_prompt_pela_versao_configurada(monkeypatch):
    """
    As duas versões precisam continuar acessíveis: a comparação entre elas é
    o experimento (a v1 detectou 0 de 6 defeitos reais; a v2 existe para
    medir se busca ativa muda esse número).
    """
    from app.llm_client import SYSTEM_PROMPT_V1, SYSTEM_PROMPT_V2

    assert LLMClient(api_key="fake", prompt_version="v1").system_prompt == SYSTEM_PROMPT_V1
    assert LLMClient(api_key="fake", prompt_version="v2").system_prompt == SYSTEM_PROMPT_V2


def test_versao_invalida_de_prompt_cai_na_v2(monkeypatch):
    from app.llm_client import SYSTEM_PROMPT_V2

    cliente = LLMClient(api_key="fake", prompt_version="inexistente")

    assert cliente.system_prompt == SYSTEM_PROMPT_V2


def test_v2_pede_busca_ativa_de_defeito():
    """
    A diferença de fundo entre as versões: a v1 pedia sugestão de teste e
    citava defeito em quarto lugar; a v2 põe procurar defeito como tarefa
    principal e proíbe a resposta que só descreve a função — que foi
    exatamente o modo de falha observado na medição.
    """
    from app.llm_client import SYSTEM_PROMPT_V2

    assert "tarefa PRINCIPAL é procurar defeitos" in SYSTEM_PROMPT_V2
    assert "Descrever o que a função faz não é" in SYSTEM_PROMPT_V2


def test_min_request_interval_comes_from_settings(monkeypatch):
    """
    O intervalo de rate limit precisa ser configurável (não mais fixo em
    15s) — modelos diferentes têm RPM diferentes (ex: "Flash Lite" costuma
    permitir bem mais requisições por minuto que o "Flash" comum).
    """
    monkeypatch.setattr(
        llm_client_module.settings, "llm_min_request_interval_seconds", 4
    )

    client = LLMClient(api_key="fake-key")

    assert client.min_request_interval == 4


# --- Resiliência a falhas da camada de LLM -------------------------------
#
# Numa rodada real no QA-e2e-lab, 4 de 8 análises voltaram "desconhecido":
# três por 503 (sobrecarga do Gemini) e uma por resposta ilegível. Cada uma
# dessas consumiu cota e não produziu nada. Os testes abaixo cobrem os dois
# caminhos de recuperação.


class _RespostaFake:
    def __init__(self, text):
        self.text = text


def _funcao_de_exemplo():
    from app.code_analyzer import CodeAnalyzer

    return CodeAnalyzer().analyze_source("def somar(a, b):\n    return a + b")[0]


def _cliente_com_respostas(monkeypatch, respostas):
    """
    LLMClient cujo generate_content devolve (ou levanta) os itens de
    `respostas`, um por chamada. Sem espera real entre tentativas.
    """
    client = LLMClient(api_key="fake-key")

    chamadas = []

    def fake_generate_content(**kwargs):
        chamadas.append(kwargs)
        item = respostas[len(chamadas) - 1]
        if isinstance(item, Exception):
            raise item
        return _RespostaFake(item)

    monkeypatch.setattr(
        client.client.models, "generate_content", fake_generate_content
    )
    monkeypatch.setattr(client, "_wait_for_rate_limit", lambda: None)

    return client, chamadas


JSON_VALIDO = '{"risk_level": "alto", "risk_reason": "x", "suggested_tests": []}'


def test_resposta_ilegivel_e_retentada(monkeypatch):
    """
    Antes, `_parse_response` DEVOLVIA o fallback em vez de levantar. Como
    esse return acontecia dentro do `try`, o laço de retry concluía que a
    tentativa tinha dado certo e parava — a cota era gasta e a análise,
    descartada, sem nenhuma nova tentativa.
    """
    esperas = []
    monkeypatch.setattr(llm_client_module.time, "sleep", esperas.append)

    client, chamadas = _cliente_com_respostas(
        monkeypatch, ["isso não é json", "nem isso", JSON_VALIDO]
    )

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "alto"
    assert len(chamadas) == 3


def test_resposta_ilegivel_esgotada_preserva_o_texto_bruto(monkeypatch):
    """
    Esgotadas as tentativas, o resultado ainda precisa carregar a resposta
    crua — é o que permite descobrir DEPOIS por que o parse falhou (JSON
    cortado por limite de tokens? texto solto?).
    """
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    client, chamadas = _cliente_com_respostas(
        monkeypatch, ["lixo", "lixo", "lixo"]
    )

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "desconhecido"
    assert resultado["raw_response"] == "lixo"
    assert len(chamadas) == 3


def test_backoff_do_503_cresce_exponencialmente(monkeypatch):
    """
    O backoff linear anterior (5s, 10s) esgotava as três tentativas em 15
    segundos — pouco para um pico de demanda do lado do Google, que foi a
    causa mais frequente de análise perdida na medição.
    """
    esperas = []
    monkeypatch.setattr(llm_client_module.time, "sleep", esperas.append)
    monkeypatch.setattr(
        llm_client_module.settings, "llm_overload_backoff_seconds", 10
    )

    erro = Exception("503 UNAVAILABLE: model is overloaded")
    client, chamadas = _cliente_com_respostas(monkeypatch, [erro, erro, JSON_VALIDO])

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "alto"
    assert esperas == [10, 20]
    assert len(chamadas) == 3
