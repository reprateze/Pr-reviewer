from unittest.mock import MagicMock

from sqlmodel import SQLModel, create_engine

import app.db as db
from app.experiment import run_experiment
from app.models import Suggestion


def _fresh_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    SQLModel.metadata.create_all(engine)
    return engine


def _github_com_prs(quantidade: int):
    github = MagicMock()
    github.list_pull_requests.return_value = [
        {"number": n, "head": {"sha": f"sha{n}"}} for n in range(1, quantidade + 1)
    ]
    return github


def _analyze_falso(registro: list):
    """Substitui a análise real: só registra o que foi chamado."""
    def analyze(owner, repo, pr_number, head_ref, use_rag):
        registro.append((pr_number, use_rag))
        return {"functions_analyzed": 2}
    return analyze


def test_alterna_condicao_entre_com_e_sem_rag(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))
    chamadas = []

    resumo = run_experiment(
        _github_com_prs(4), "o", "r", max_prs=4, analyze=_analyze_falso(chamadas)
    )

    # Alternado: PR1 com, PR2 sem, PR3 com, PR4 sem
    assert [usou_rag for _, usou_rag in chamadas] == [True, False, True, False]
    assert resumo["por_condicao"] == {"com_rag": 2, "sem_rag": 2}
    assert resumo["prs_processados"] == 4
    assert resumo["funcoes_analisadas"] == 8


def test_fixed_rag_ignora_alternancia(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))
    chamadas = []

    run_experiment(
        _github_com_prs(3), "o", "r", max_prs=3,
        fixed_rag=False, analyze=_analyze_falso(chamadas),
    )

    assert [usou_rag for _, usou_rag in chamadas] == [False, False, False]


def test_pula_pr_ja_analisado_para_permitir_retomada(tmp_path, monkeypatch):
    """
    Uma rodada longa pode cair no meio. Ao rodar de novo, PRs que já têm
    sugestão salva não podem ser reprocessados (gastaria cota de LLM à toa).
    """
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    db.save_suggestion(
        Suggestion(
            owner="o", repo="r", pr_number=2, commit_sha="sha2",
            filename="a.py", function_name="f", risk_level="baixo", risk_reason="-",
        )
    )

    chamadas = []
    resumo = run_experiment(
        _github_com_prs(3), "o", "r", max_prs=3, analyze=_analyze_falso(chamadas)
    )

    assert [pr for pr, _ in chamadas] == [1, 3]  # o PR 2 foi pulado
    assert resumo["prs_pulados_ja_analisados"] == 1
    assert resumo["prs_processados"] == 2


def test_falha_em_um_pr_nao_derruba_a_rodada(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))
    chamadas = []

    def analyze_que_falha_no_segundo(owner, repo, pr_number, head_ref, use_rag):
        if pr_number == 2:
            raise RuntimeError("cota estourada")
        chamadas.append(pr_number)
        return {"functions_analyzed": 1}

    resumo = run_experiment(
        _github_com_prs(3), "o", "r", max_prs=3, analyze=analyze_que_falha_no_segundo
    )

    assert chamadas == [1, 3]  # seguiu depois da falha
    assert resumo["prs_com_falha"] == 1
    assert resumo["prs_processados"] == 2


def test_pr_sem_sha_conta_como_falha(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "engine", _fresh_engine(tmp_path))

    github = MagicMock()
    github.list_pull_requests.return_value = [{"number": 1, "head": {}}]

    resumo = run_experiment(
        github, "o", "r", max_prs=1, analyze=_analyze_falso([])
    )

    assert resumo["prs_com_falha"] == 1
    assert resumo["prs_processados"] == 0
