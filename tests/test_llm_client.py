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