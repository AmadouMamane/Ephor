"""Rouge d'abord : chaque critère doit échouer AVANT le travail, et le juge le vérifie.

Un critère déjà vert sur la branche principale fait déclarer l'agent VÉRIFIÉ sans qu'il ait
rien fait. `make task-check` lance donc chaque critère une fois, sur le code actuel, et
n'écrit sa trace (`tasks/<ID>/rouge.json`) que si tous échouent. Le juge la lit sur la base
et refuse une tâche sans elle, ou dont une preuve a changé depuis.
"""

from __future__ import annotations

import json
import time
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


# ------------------------------------------------ audit du 26/09/2026 : chaque défaut reproduit


def test_relancer_sans_rien_changer_ne_reecrit_pas_la_trace(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Relancer, même juste après avoir commité la trace, réécrivait sa date et son commit :
    un changement à recommiter, à chaque passage. Elle ne bouge plus tant que les preuves
    sont les mêmes et que rien n'a changé hors de tasks/ depuis son commit."""
    git(repo, "switch", "-q", "main")
    trace = repo / "tasks" / "T-001" / judge.ROUGE
    avant = trace.read_text(encoding="utf-8")
    assert task_check(repo, tmp_path / "work") == 0
    assert "déjà à jour" in capsys.readouterr().out
    assert trace.read_text(encoding="utf-8") == avant
    # Le code a bougé sur la branche principale : la trace suit, avec le nouveau commit.
    (repo / "src" / "autre.py").write_text("X = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "autre travail")
    assert task_check(repo, tmp_path / "work") == 0
    apres = json.loads(trace.read_text(encoding="utf-8"))
    assert apres["commit"] == git(repo, "rev-parse", "HEAD").strip()


def test_une_preuve_introuvable_est_refusee_avant_de_rien_lancer(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un chemin de preuve mal écrit faisait échouer le critère sur « fichier absent » : un
    rouge pour une mauvaise raison, et une trace écrite quand même."""
    contrat = contrat_de(repo)
    contrat["criteria"][1]["check"] = ["{python}", "tasks/T-001/check_c2_faute.py"]
    sur_main(repo, contrat, {})
    assert task_check(repo, tmp_path / "work") == 1
    sortie = capsys.readouterr().out
    assert "C2 : preuve introuvable, tasks/T-001/check_c2_faute.py" in sortie
    assert "contrôle rouge : chaque critère" not in sortie, "rien n'a été lancé"
    assert not (repo / "tasks" / "T-001" / judge.ROUGE).exists()


def test_un_test_pytest_mal_nomme_est_un_faux_rouge(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Mesuré avec pytest : un nom de test mal écrit laisse un rapport sans aucun test. Un
    test qui importe une fonction pas encore écrite, lui, est un vrai rouge."""
    test = (
        "import sys\nsys.path.insert(0, '.')\nfrom src.metrics import accuracy\n\n\n"
        "def test_lots():\n    assert abs(accuracy([(9, 10), (1, 100)]) - 10 / 110) < 1e-12\n"
    )
    a_venir = "from src.metrics import precision\n\n\ndef test_precision():\n    assert precision\n"
    contrat = contrat_de(repo)
    contrat["criteria"] = [
        {
            "id": "C1",
            "description": "nom de test mal écrit",
            "check": ["{python}", "-m", "pytest", "-q", "tasks/T-001/test_acc.py::test_lot"],
        },
        {
            "id": "C2",
            "description": "fonction pas encore écrite",
            "check": [
                "{python}",
                "-m",
                "pytest",
                "-q",
                "tasks/T-001/test_venir.py::test_precision",
            ],
        },
    ]
    sur_main(repo, contrat, {"tasks/T-001/test_acc.py": test, "tasks/T-001/test_venir.py": a_venir})
    assert task_check(repo, tmp_path / "work") == 1
    sortie = capsys.readouterr().out
    assert "FAUX   C1" in sortie and "test désigné jamais lancé" in sortie
    assert "rouge  C2" in sortie, "l'import d'une fonction à venir est un vrai rouge"
    # Corrigé, le contrat passe.
    contrat["criteria"][0]["check"][-1] = "tasks/T-001/test_acc.py::test_lots"
    sur_main(repo, contrat, {})
    assert task_check(repo, tmp_path / "work") == 0


def test_la_raison_d_un_script_muet_n_est_pas_la_commande(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le juge recopie la commande en tête du journal, amorce comprise, sur plusieurs lignes :
    un script muet affichait un morceau de cette amorce en guise de raison."""
    sur_main(repo, None, {"tasks/T-001/check_c2.py": "raise SystemExit(3)\n"})
    assert task_check(repo, tmp_path / "work") == 0
    assert "-> aucune sortie, code de retour 3" in capsys.readouterr().out


def test_un_dossier_de_preuve_ouvert_a_l_agent_est_refuse_des_task_check(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contrat = contrat_de(repo)
    contrat["criteria"][0]["check"] = ["{python}", "-m", "pytest", "-q", "tests/"]
    sur_main(repo, contrat, {})
    assert task_check(repo, tmp_path / "work") == 1
    assert "le dossier tests/, cité par C1, est ouvert" in capsys.readouterr().out


def test_un_makefile_d_avant_la_1_1_recoit_la_commande_directe(
    repo: Path, run_judge: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un projet mis à jour sans reprendre le nouveau Makefile lance encore `validate` sous
    le nom de `make task-check`. Les deux messages donnent la commande qui écrit la trace."""
    assert judge.main(["validate", "--task-id", "T-001", "--repo", str(repo)]) == 0
    assert "python3 .ephor/judge.py rouge --task-id T-001" in capsys.readouterr().out
    git(repo, "switch", "-q", "main")
    git(repo, "rm", "-q", f"tasks/T-001/{judge.ROUGE}")
    git(repo, "commit", "-qm", "sans trace")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    _, report = run_judge(repo)
    assert any("python3 .ephor/judge.py rouge --task-id T-001" in r for r in report["reasons"])


def test_les_empreintes_d_un_grand_dossier_restent_rapides(repo: Path) -> None:
    """Un processus git par fichier : 7 s pour un dossier de 400 fichiers, à chaque
    jugement (mesuré le 26/09/2026). Une seule lecture groupée maintenant."""
    git(repo, "switch", "-q", "main")
    for i in range(400):
        (repo / "tasks" / "T-001" / f"donnee_{i}.txt").write_text(f"{i}\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "données")
    contrat = judge.load_contract(repo / "tasks" / "T-001" / "contract.json", "T-001")
    debut = time.monotonic()
    empreintes = judge.preuves_rouges(repo, "HEAD", contrat, "T-001")
    assert time.monotonic() - debut < 3
    assert len(empreintes) == 403
    assert empreintes["tasks/T-001/donnee_7.txt"] == judge.sha256(b"7\n")


def test_un_dossier_pytest_sans_test_est_un_faux_rouge(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contrat = contrat_de(repo)
    contrat["criteria"][0]["check"] = ["{python}", "-m", "pytest", "-q", "tasks/T-001/vide/"]
    sur_main(repo, contrat, {})
    (repo / "tasks" / "T-001" / "vide").mkdir()
    (repo / "tasks" / "T-001" / "vide" / "LISEZMOI.md").write_text("pas de test ici\n")
    assert task_check(repo, tmp_path / "work") == 1
    assert "pytest n'a lancé aucun test" in capsys.readouterr().out
