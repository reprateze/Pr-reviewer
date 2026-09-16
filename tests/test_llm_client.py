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
