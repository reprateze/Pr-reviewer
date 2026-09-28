import pytest

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
    # Sem reservas: estes testes exercitam o laço de tentativas de UM modelo.
    # A cadeia tem teste próprio, mais abaixo.
    client.models_chain = [client.model]

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


# --- Cadeia de modelos -----------------------------------------------------
#
# Medição que motivou a cadeia: num mesmo minuto, gemini-3.6-flash devolveu
# 503 e gemini-3.5-flash respondeu detectando o defeito real. Dois dias antes
# a situação era inversa. A indisponibilidade do nível gratuito é sorteio por
# momento, então insistir num único modelo perde análises que outro faria.


def test_modelo_de_reserva_assume_quando_o_principal_esta_indisponivel(monkeypatch):
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    erro503 = Exception("503 UNAVAILABLE: model is overloaded")
    client, chamadas = _cliente_com_respostas(
        monkeypatch, [erro503, erro503, erro503, JSON_VALIDO]
    )
    client.models_chain = ["modelo-principal", "modelo-reserva"]

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "alto"
    # 3 tentativas no principal, e a 4ª chamada já é no de reserva.
    assert len(chamadas) == 4
    assert chamadas[2]["model"] == "modelo-principal"
    assert chamadas[3]["model"] == "modelo-reserva"


def test_registra_o_modelo_que_de_fato_respondeu(monkeypatch):
    """
    Sem isso o banco gravaria o modelo principal mesmo quando a resposta veio
    de um reserva — e os dados do experimento, que comparam modelos, ficariam
    ininterpretáveis.
    """
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    erro503 = Exception("503 UNAVAILABLE")
    client, _ = _cliente_com_respostas(
        monkeypatch, [erro503, erro503, erro503, JSON_VALIDO]
    )
    client.models_chain = ["modelo-principal", "modelo-reserva"]

    client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert client.last_model_used == "modelo-reserva"


def test_sem_reserva_disponivel_devolve_desconhecido(monkeypatch):
    monkeypatch.setattr(llm_client_module.time, "sleep", lambda _: None)

    erro503 = Exception("503 UNAVAILABLE")
    client, chamadas = _cliente_com_respostas(monkeypatch, [erro503] * 6)
    client.models_chain = ["modelo-a", "modelo-b"]

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "desconhecido"
    assert len(chamadas) == 6  # 3 tentativas em cada um dos dois modelos


def test_cadeia_nao_repete_o_modelo_principal(monkeypatch):
    monkeypatch.setattr(
        llm_client_module.settings, "llm_model_fallbacks",
        ("modelo-x", "modelo-y"),
    )

    client = LLMClient(api_key="fake-key", model="modelo-x")

    assert client.models_chain == ["modelo-x", "modelo-y"]


def test_prazo_total_impede_a_cadeia_de_se_arrastar(monkeypatch):
    """
    A cadeia multiplica o pior caso: 3 modelos x 3 tentativas, e uma falha do
    provedor chegou a levar 94s para retornar. Sem teto, a análise de uma
    função passava de dez minutos e a execução parecia travada.

    Aqui o relógio é simulado: cada espera "consome" tempo, e o prazo de 25s
    estoura antes de a cadeia percorrer tudo.
    """
    agora = {"t": 1000.0}
    monkeypatch.setattr(llm_client_module.time, "time", lambda: agora["t"])
    monkeypatch.setattr(
        llm_client_module.time, "sleep",
        lambda s: agora.__setitem__("t", agora["t"] + s),
    )
    monkeypatch.setattr(
        llm_client_module.settings, "llm_total_deadline_seconds", 25
    )
    monkeypatch.setattr(
        llm_client_module.settings, "llm_overload_backoff_seconds", 10
    )

    erro503 = Exception("503 UNAVAILABLE")
    client, chamadas = _cliente_com_respostas(monkeypatch, [erro503] * 9)
    client.models_chain = ["modelo-a", "modelo-b", "modelo-c"]

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "desconhecido"
    # Sem prazo seriam 9 chamadas; o teto corta antes de percorrer a cadeia.
    assert len(chamadas) < 9
    assert agora["t"] - 1000.0 <= 25


# --- Provedor trocável -----------------------------------------------------
#
# A indisponibilidade do nível gratuito do Gemini chegou a inviabilizar metade
# das análises por dias. Poder apontar para outro serviço sem mexer em código
# é o que impede o projeto de ficar refém de um provedor.


class _MensagemFake:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _RespostaOpenAIFake:
    def __init__(self, content):
        self.choices = [_MensagemFake(content)]


def _cliente_openai(monkeypatch, respostas):
    monkeypatch.setattr(llm_client_module.settings, "llm_provider", "openai")
    monkeypatch.setattr(
        llm_client_module.settings, "llm_base_url", "https://exemplo.invalido/v1"
    )

    client = LLMClient(api_key="fake-key", model="modelo-aberto")
    chamadas = []

    def fake_create(**kwargs):
        chamadas.append(kwargs)
        item = respostas[len(chamadas) - 1]
        if isinstance(item, Exception):
            raise item
        return _RespostaOpenAIFake(item)

    monkeypatch.setattr(client.client.chat.completions, "create", fake_create)
    monkeypatch.setattr(client, "_wait_for_rate_limit", lambda: None)
    client.models_chain = [client.model]

    return client, chamadas


def test_provedor_compativel_com_openai_monta_system_e_user(monkeypatch):
    client, chamadas = _cliente_openai(monkeypatch, [JSON_VALIDO])

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "alto"
    mensagens = chamadas[0]["messages"]
    assert mensagens[0]["role"] == "system"
    assert "defeitos" in mensagens[0]["content"]      # é o prompt de sistema
    assert mensagens[1]["role"] == "user"
    assert "def somar" in mensagens[1]["content"]     # é o código da função
    assert chamadas[0]["model"] == "modelo-aberto"


def test_provedor_sem_modo_json_repete_sem_response_format(monkeypatch):
    """
    Parte dos serviços compatíveis não implementa `response_format`. Repetir
    sem ele preserva a análise — o parser já tolera JSON com texto em volta.
    """
    erro = Exception("unsupported parameter: response_format")
    client, chamadas = _cliente_openai(monkeypatch, [erro, JSON_VALIDO])

    resultado = client.suggest_tests_for_function(_funcao_de_exemplo(), "app/x.py")

    assert resultado["risk_level"] == "alto"
    assert "response_format" in chamadas[0]
    assert "response_format" not in chamadas[1]


def test_provider_openai_sem_base_url_falha_cedo(monkeypatch):
    monkeypatch.setattr(llm_client_module.settings, "llm_provider", "openai")
    monkeypatch.setattr(llm_client_module.settings, "llm_base_url", "")

    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        LLMClient(api_key="fake-key")


def test_provider_desconhecido_falha_cedo(monkeypatch):
    monkeypatch.setattr(llm_client_module.settings, "llm_provider", "vertex")

    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        LLMClient(api_key="fake-key")
