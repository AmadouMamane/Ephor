"""Rouge d'abord : chaque critère doit échouer AVANT le travail, et le juge le vérifie.

Un critère déjà vert sur la branche principale fait déclarer l'agent VÉRIFIÉ sans qu'il ait
rien fait. `make task-check` lance donc chaque critère une fois, sur le code actuel, et
n'écrit sa trace (`tasks/<ID>/rouge.json`) que si tous échouent. Le juge la lit sur la base
et refuse une tâche sans elle, ou dont une preuve a changé depuis.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import FIX, ROOT, git, judge, trace_rouge

Runner = Callable[..., tuple[int, dict[str, Any]]]


def task_check(repo: Path, work: Path) -> int:
    return judge.main(["rouge", "--task-id", "T-001", "--repo", str(repo), "--workdir", str(work)])


def sur_main(repo: Path, contrat: dict[str, Any] | None, fichiers: dict[str, str]) -> None:
    """L'opérateur, sur la branche principale, avant de commiter : contrat et tests posés."""
    git(repo, "switch", "-q", "main")
    tache = repo / "tasks" / "T-001"
    if contrat is not None:
        (tache / "contract.json").write_text(json.dumps(contrat, indent=2) + "\n")
    for nom, contenu in fichiers.items():
        (repo / nom).write_text(contenu)


def contrat_de(repo: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (repo / "tasks" / "T-001" / "contract.json").read_text(encoding="utf-8")
    )
    return data


def test_task_check_ecrit_la_trace_que_le_juge_accepte(
    repo: Path, run_judge: Runner, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le circuit complet : un critère ajouté, pas encore commité, vu échouer pour sa vraie
    raison ; la trace commitée avec lui ; puis le juge, qui l'accepte et vérifie le correctif."""
    contrat = contrat_de(repo)
    contrat["criteria"].append(
        {"id": "C3", "description": "un seul lot", "check": ["{python}", "tasks/T-001/c3.py"]}
    )
    sur_main(
        repo,
        contrat,
        {
            "tasks/T-001/c3.py": "import sys\nsys.path.insert(0, '.')\n"
            "from src.metrics import accuracy\n"
            "assert accuracy([(1, 4)]) == 0.25, 'un lot'\n"
            "assert accuracy([]) is None, 'lot vide'\n"
        },
    )
    assert task_check(repo, tmp_path / "work") == 0
    sortie = capsys.readouterr().out
    assert "rouge  C1" in sortie and "rouge  C3" in sortie
    assert "ZeroDivisionError" in sortie, "la raison est montrée à l'opérateur"
    trace = json.loads((repo / "tasks" / "T-001" / judge.ROUGE).read_text(encoding="utf-8"))
    assert [c["id"] for c in trace["criteres"]] == ["C1", "C2", "C3"]
    assert "tasks/T-001/c3.py" in trace["preuves"], "le test pas encore commité compte"
    assert (repo / ".verify" / "rouge" / "T-001" / "logs" / "C3.log").read_text() != ""
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "contrat approuvé")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report
    assert report["rouge"]["commit"] == trace["commit"]


def test_un_critere_deja_vert_est_refuse_et_la_trace_retiree(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contrat = contrat_de(repo)
    contrat["criteria"].append(
        {"id": "C3", "description": "trop facile", "check": ["{python}", "tasks/T-001/c3.py"]}
    )
    sur_main(repo, contrat, {"tasks/T-001/c3.py": "print('rien à prouver')\n"})
    assert (repo / "tasks" / "T-001" / judge.ROUGE).is_file()
    assert task_check(repo, tmp_path / "work") == 1
    sortie = capsys.readouterr().out
    assert "VERT   C3" in sortie and "VÉRIFIÉ sans rien faire" in sortie
    assert not (repo / "tasks" / "T-001" / judge.ROUGE).exists(), (
        "une trace qui ne dit plus la vérité ne reste pas prête à être commitée"
    )


def test_une_installation_en_echec_ne_laisse_pas_de_trace(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Une installation cassée fait échouer tous les critères, pour une mauvaise raison."""
    contrat = contrat_de(repo)
    contrat["setup"] = ["{python}", "-c", "raise SystemExit('dépôt de paquets injoignable')"]
    sur_main(repo, contrat, {})
    assert task_check(repo, tmp_path / "work") == 1
    sortie = capsys.readouterr().out
    assert "installation du contrat échoue" in sortie and "injoignable" in sortie
    assert "rouge  C1" not in sortie
    assert not (repo / "tasks" / "T-001" / judge.ROUGE).exists()


def test_le_juge_refuse_une_tache_sans_controle_rouge(repo: Path, run_judge: Runner) -> None:
    git(repo, "switch", "-q", "main")
    git(repo, "rm", "-q", f"tasks/T-001/{judge.ROUGE}")
    git(repo, "commit", "-qm", "contrat approuvé sans contrôle rouge")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 2 and report["status"] == "rejected"
    assert any("contrôle rouge absent de la base" in r for r in report["reasons"])
    assert any("make task-check ID=T-001" in r for r in report["reasons"])
    assert report["steps"] == [], "rien n'a été exécuté"


def test_une_preuve_changee_apres_le_controle_rouge_est_refusee(
    repo: Path, run_judge: Runner
) -> None:
    """La preuve affaiblie après coup sur la branche principale : ce qui a été vu échouer
    n'est plus ce qui sera jugé."""
    git(repo, "switch", "-q", "main")
    (repo / "tasks" / "T-001" / "check_c2.py").write_text("print('plus rien')\n")
    (repo / "tasks" / "T-001" / "aide.py").write_text("VALEUR = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "preuve retouchée sans refaire le contrôle rouge")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 2
    assert "contrôle rouge périmé : tasks/T-001/check_c2.py a changé depuis" in report["reasons"]
    assert "contrôle rouge périmé : tasks/T-001/aide.py ajouté depuis" in report["reasons"]
    # Refait, il redevient valable.
    git(repo, "checkout", "-q", "--", "src/metrics.py")
    git(repo, "switch", "-q", "main")
    trace_rouge(repo)
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report


def test_la_trace_se_lit_sur_la_base_pas_sur_la_branche(repo: Path, run_judge: Runner) -> None:
    """L'agent qui écrit lui-même la trace manquante, sur sa branche, n'y gagne rien."""
    git(repo, "switch", "-q", "main")
    trace = (repo / "tasks" / "T-001" / judge.ROUGE).read_text(encoding="utf-8")
    git(repo, "rm", "-q", f"tasks/T-001/{judge.ROUGE}")
    git(repo, "commit", "-qm", "sans trace")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "tasks" / "T-001" / judge.ROUGE).write_text(trace)
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 2
    assert any("contrôle rouge absent de la base" in r for r in report["reasons"])


def test_le_code_change_sur_la_base_depuis_le_controle_est_signale(
    repo: Path, run_judge: Runner
) -> None:
    """Le juge ne rejoue pas les critères sur la base : il dit au relecteur quand le code a
    bougé entre le contrôle rouge et le départ de la tâche."""
    git(repo, "switch", "-q", "main")
    (repo / "src" / "outils.py").write_text("VERSION = 2\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "autre travail fusionné entre-temps")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, _ = run_judge(repo)
    assert code == 0
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "1 fichier(s) hors tasks/ ont changé sur la base depuis le contrôle rouge" in resume


def test_l_exemple_porte_une_trace_a_jour() -> None:
    """EXEMPLE-001 est le modèle qu'on recopie : sa trace doit dire la vérité sur ses
    fichiers tels qu'ils sont, ou le juge refuserait l'exemple lui-même."""
    tache = ROOT / "tasks" / "EXEMPLE-001"
    trace = json.loads((tache / judge.ROUGE).read_text(encoding="utf-8"))
    contrat = judge.load_contract(tache / "contract.json", "EXEMPLE-001")
    assert [c["id"] for c in trace["criteres"]] == [c.id for c in contrat.criteria]
    assert {c["resultat"] for c in trace["criteres"]} == {"rouge"}
    presents = {
        f"tasks/EXEMPLE-001/{f.name}"
        for f in tache.iterdir()
        if f.is_file() and f.name != judge.ROUGE
    }
    assert set(trace["preuves"]) == presents
    for nom, empreinte in trace["preuves"].items():
        assert judge.sha256((ROOT / nom).read_bytes()) == empreinte, nom
