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