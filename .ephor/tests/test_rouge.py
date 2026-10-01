"""Rouge d'abord : chaque critère doit échouer AVANT le travail, et le juge le vérifie.

Un critère déjà vert sur la branche principale fait déclarer l'agent VÉRIFIÉ sans qu'il ait
rien fait. `make task-check` lance donc chaque critère une fois, sur le code commité, et
n'écrit sa trace (`tasks/<ID>/rouge.json`) que si tous échouent. Le juge la lit sur la base
et refuse une tâche sans elle, ou dont une preuve a changé depuis.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import operateur
from conftest import CHECK_C1, CHECK_C2, FIX, ROOT, WRONG, contract, git, judge, trace_rouge

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
    change = "contrôle rouge périmé : tasks/T-001/check_c2.py a changé depuis (modifié par "
    assert any(
        raison.startswith(change)
        and "« preuve retouchée sans refaire le contrôle rouge »" in raison
        for raison in report["reasons"]
    ), report["reasons"]
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


# ------------------------------------------------ audit du 27/09/2026 : chaque défaut reproduit


def test_un_travail_en_cours_non_commite_ne_fait_pas_le_rouge(
    repo: Path, run_judge: Runner, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le contrôle rouge tournait sur le dossier TEL QUEL, travail en cours compris. Un
    refactor pas commité qui cassait la fonction faisait échouer un critère que la base
    satisfaisait déjà : l'opérateur commitait la tâche seule, et l'agent sortait VÉRIFIÉ
    sans rien faire (reproduit le 27/09/2026). Il tourne maintenant sur le code commité ;
    seules les preuves de la tâche sont prises dans le dossier."""
    git(repo, "switch", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "la fonction existe déjà")
    (repo / "src" / "metrics.py").write_text(WRONG)  # refactor en cours, pas commité
    (repo / "src" / "brouillon.py").write_text("X = 1\n")  # nouveau, pas commité non plus
    (repo / "tasks" / "T-001" / "check_c2.py").write_text(CHECK_C2 + "print('fin')\n")
    assert task_check(repo, tmp_path / "work") == 1
    sortie = capsys.readouterr().out
    assert "VERT   C1" in sortie and "VERT   C2" in sortie, sortie
    assert "src/brouillon.py" in sortie and "src/metrics.py" in sortie, "ce qui est écarté est dit"
    # Le travail en cours n'a pas été touché.
    assert (repo / "src" / "metrics.py").read_text() == WRONG


def test_le_rouge_ne_se_lance_pas_sur_une_branche_de_tache(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Relancé sur la branche de la tâche, le travail de l'agent faisait passer les critères :
    la trace commitée était retirée du dossier, et le juge refusait ensuite la tâche pour une
    zone de contrôle modifiée."""
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "travail de l'agent")
    assert task_check(repo, tmp_path / "work") == 2
    assert "task/T-001" in capsys.readouterr().out
    assert (repo / "tasks" / "T-001" / judge.ROUGE).is_file()
    assert git(repo, "status", "--porcelain") == ""


def test_le_squelette_de_task_new_n_est_pas_approuvable(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le squelette laissé tel quel passait : `pytest.fail("critère à écrire")` échoue, donc
    « rouge », et la trace s'écrivait pour un contrat « A ECRIRE » (vu sur un dépôt d'essai)."""
    git(repo, "switch", "-q", "main")
    assert judge.main(["new", "--task-id", "T-002", "--repo", str(repo)]) == 0
    capsys.readouterr()
    code = judge.main(
        ["rouge", "--task-id", "T-002", "--repo", str(repo), "--workdir", str(tmp_path / "w")]
    )
    assert code == 1
    sortie = capsys.readouterr().out
    assert "squelette de make task-new" in sortie and "objective" in sortie
    assert not (repo / "tasks" / "T-002" / judge.ROUGE).exists()
    # Textes écrits, test laissé vide : le test est refusé à son tour, lui seul.
    contrat = json.loads((repo / "tasks" / "T-002" / "contract.json").read_text())
    contrat["objective"] = "Un objectif enfin rédigé par l'opérateur."
    contrat["criteria"][0]["description"] = "un critère rédigé"
    contrat["criteria"][0]["check"] = ["{python}", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    contrat["criteria"][0]["check"].append("tasks/T-002/test_t_002.py::test_c1")
    contrat.pop("setup")
    (repo / "tasks" / "T-002" / "contract.json").write_text(json.dumps(contrat))
    code = judge.main(
        ["rouge", "--task-id", "T-002", "--repo", str(repo), "--workdir", str(tmp_path / "w")]
    )
    assert code == 1
    sortie = capsys.readouterr().out
    assert "FAUX   C1" in sortie and "test encore à écrire" in sortie


def test_une_commande_introuvable_est_un_faux_rouge(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Code 127 : la commande n'existe pas, le critère échoue avant de rien tester. C'était un
    « rouge » comme un autre, et la trace s'écrivait."""
    contrat = contrat_de(repo)
    contrat["criteria"][0]["check"] = ["bash", "tasks/T-001/verifie.sh"]
    contrat["criteria"][1]["check"] = ["commande-ephor-qui-n-existe-pas", "--vite"]
    sur_main(repo, contrat, {})
    assert task_check(repo, tmp_path / "work") == 1
    sortie = capsys.readouterr().out
    assert "FAUX   C1" in sortie and "FAUX   C2" in sortie
    assert "commande introuvable" in sortie
    assert not (repo / "tasks" / "T-001" / judge.ROUGE).exists()


def test_un_controle_fait_ailleurs_que_sur_la_base_est_signale(
    repo: Path, run_judge: Runner
) -> None:
    """Le contrôle lancé sur une autre branche, puis la tâche reportée sur la principale : le
    juge ne pouvait rien dire du code vu échouer, et se taisait."""
    git(repo, "switch", "-q", "main")
    chemin = repo / "tasks" / "T-001" / judge.ROUGE
    trace = json.loads(chemin.read_text(encoding="utf-8"))
    trace["commit"] = "0123456789abcdef0123456789abcdef01234567"
    chemin.write_text(json.dumps(trace, indent=2) + "\n")
    git(repo, "commit", "-qam", "trace venue d'une autre branche")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, _ = run_judge(repo)
    assert code == 0
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert "absent de l'histoire de la base (0123456)" in resume


def pre_commit_factice(repo: Path, dossier: Path, journal: Path) -> None:
    """pre-commit en miniature, installé comme le vrai : sa configuration, le hook de git qui
    l'appelle au commit, et un seul correcteur, celui de la fin de fichier (end-of-file-fixer).
    Il sort avec 1 quand il a réécrit un fichier : au commit, celui-ci est alors refusé."""
    (repo / ".pre-commit-config.yaml").write_text("repos: []\n")
    dossier.mkdir()
    outil = dossier / "pre-commit"
    outil.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$*" >> "{journal}"\n'
        'case "$1" in\n'
        '  run) [ "$2" = --files ] || exit 2; shift 2 ;;\n'
        "  hook-impl) set -- $(git diff --cached --name-only --diff-filter=ACM) ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
        "code=0\n"
        'for f in "$@"; do\n'
        '  [ -s "$f" ] || continue\n'
        '  if [ "$(tail -c1 "$f" | wc -l | tr -d \' \')" = 0 ]; then\n'
        "    printf '\\n' >> \"$f\"; code=1\n"
        "  fi\n"
        "done\n"
        "exit $code\n"
    )
    outil.chmod(0o755)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        "#!/usr/bin/env bash\n# File generated by pre-commit: https://pre-commit.com\n"
        'exec pre-commit hook-impl --hook-type=pre-commit -- "$@"\n'
    )
    hook.chmod(0o755)


def commit_de_l_operateur(repo: Path, message: str) -> int:
    """`git add -A` puis `git commit`, hooks compris, comme l'opérateur ; rend le code du
    premier essai, et recommence une fois si les hooks ont réécrit des fichiers."""
    git(repo, "add", "-A")
    premier = subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", message], capture_output=True, check=False
    ).returncode
    if premier != 0:
        git(repo, "add", "-A")
        git(repo, "commit", "-qm", message)
    return premier


def test_le_commit_ne_perime_plus_le_controle_rouge(
    repo: Path,
    run_judge: Runner,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Vu sept fois dans une même session réelle, rejoué le 30/09/2026 : `task-check` prenait
    l'empreinte des tests tels quels, puis les hooks de pre-commit les réécrivaient au commit
    (fin de fichier, ruff format…). La trace était périmée dès l'approbation, et le juge
    refusait la tâche. task-check passe donc ces hooks lui-même, avant l'empreinte."""
    journal = tmp_path / "pre-commit.txt"
    pre_commit_factice(repo, tmp_path / "bin", journal)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    contrat = contrat_de(repo)
    contrat["criteria"].append(
        {"id": "C3", "description": "un seul lot", "check": ["{python}", "tasks/T-001/c3.py"]}
    )
    sans_fin_de_ligne = (
        "import sys\nsys.path.insert(0, '.')\nfrom src.metrics import accuracy\n"
        "assert accuracy([(1, 4)]) == 0.25, 'un lot'\nassert accuracy([]) is None, 'lot vide'"
    )
    sur_main(repo, contrat, {"tasks/T-001/c3.py": sans_fin_de_ligne})
    assert task_check(repo, tmp_path / "work") == 0
    assert "réécrit par les hooks du commit : tasks/T-001/c3.py" in capsys.readouterr().out
    assert "run --files" in journal.read_text()
    assert commit_de_l_operateur(repo, "contrat approuvé") == 0, "le commit n'a rien à réécrire"
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report["reasons"]


def test_sans_hook_de_commit_task_check_ne_lance_rien(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ni hook installé, ni configuration : le commit ne réécrira rien, rien à lancer."""
    journal = tmp_path / "pre-commit.txt"
    pre_commit_factice(repo, tmp_path / "bin", journal)
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    (repo / ".git" / "hooks" / "pre-commit").unlink()
    sur_main(repo, None, {"tasks/T-001/c3.py": "x = 1"})
    assert task_check(repo, tmp_path / "work") == 0
    assert not journal.exists()
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\npre-commit hook-impl\n")
    (repo / ".git" / "hooks" / "pre-commit").chmod(0o755)
    (repo / ".pre-commit-config.yaml").unlink()
    assert task_check(repo, tmp_path / "work2") == 0
    assert not journal.exists()


def test_des_hooks_qui_echouent_sont_annonces(
    repo: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Un hook qui refuse sans rien corriger (une règle de lint dans un test d'examen) : le
    commit sera refusé, il faudra corriger, et la trace sera à refaire. L'opérateur l'apprend
    ici, pas au commit."""
    pre_commit_factice(repo, tmp_path / "bin", tmp_path / "pre-commit.txt")
    (tmp_path / "bin" / "pre-commit").write_text(
        "#!/usr/bin/env bash\necho 'E501 Line too long (120 > 100)'\nexit 1\n"
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    sur_main(repo, None, {"tasks/T-001/c3.py": "x = 1\n"})
    assert task_check(repo, tmp_path / "work") == 0
    sortie = capsys.readouterr().out
    assert "les hooks du commit échouent encore" in sortie and "E501" in sortie


def test_task_check_sans_id_liste_les_controles_perimes(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un reformatage du dépôt périme d'un coup toutes les traces qu'il touche ; on les
    découvrait une par une, au juge (session réelle, 30/09/2026). `make task-check` sans ID
    les liste toutes, lues sur la branche principale comme le juge les lira."""
    git(repo, "switch", "-q", "main")
    deux = repo / "tasks" / "T-002"
    deux.mkdir()
    contrat = contrat_de(repo)
    contrat["id"] = "T-002"
    for critere in contrat["criteria"]:
        critere["check"] = [a.replace("T-001", "T-002") for a in critere["check"]]
    (deux / "contract.json").write_text(json.dumps(contrat, indent=2) + "\n")
    for nom in ("check_c1.py", "check_c2.py"):
        (deux / nom).write_text((repo / "tasks" / "T-001" / nom).read_text())
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "T-002 approuvé")
    trace_rouge(repo, "T-002")
    assert judge.main(["perimes", "--repo", str(repo)]) == 0
    assert "à jour : 2 tâche(s)" in capsys.readouterr().out
    (deux / "check_c2.py").write_text(CHECK_C2.replace("is None", "is None  # reformaté"))
    git(repo, "commit", "-qam", "reformatage du dépôt")
    assert judge.main(["perimes", "--repo", str(repo)]) == 1
    sortie = capsys.readouterr().out
    assert "PÉRIMÉ  T-002 : tasks/T-002/check_c2.py a changé depuis" in sortie
    assert "à jour : 1 tâche(s)" in sortie
    assert "make task-check ID=T-002" in sortie


def test_le_makefile_liste_les_controles_perimes_sans_id() -> None:
    recette = (ROOT / "Makefile").read_text(encoding="utf-8").split("\ntask-check:")[1]
    recette = recette.split("\n\n")[0]
    assert "judge.py perimes" in recette


def tache_qui_partage(repo: Path, partage: bool = True) -> Path:
    """T-002, sur la branche principale : son propre test, et, si `partage`, celui de T-001."""
    git(repo, "switch", "-q", "main")
    deux = repo / "tasks" / "T-002"
    deux.mkdir(exist_ok=True)
    (deux / "check_c2.py").write_text(CHECK_C2)
    criteres = [
        {"id": "C2", "description": "liste vide", "check": ["{python}", "tasks/T-002/check_c2.py"]}
    ]
    if partage:
        criteres.insert(
            0,
            {"id": "C1", "description": "lots", "check": ["{python}", "tasks/T-001/check_c1.py"]},
        )
    contrat = contrat_de(repo)
    contrat.update(id="T-002", criteria=criteres)
    (deux / "contract.json").write_text(json.dumps(contrat, indent=2) + "\n")
    return deux


def test_task_check_signale_un_test_partage_avec_un_autre_contrat(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Session réelle (30/09/2026) : un test partagé par deux contrats empêchait un verdict
    propre. Le juge de l'un dépend du travail de l'autre, et changer le test périme les deux
    contrôles rouges. On l'apprenait au juge ; on le lit maintenant au moment d'approuver."""
    deux = tache_qui_partage(repo, partage=False)
    rouge = ["rouge", "--task-id", "T-002", "--repo", str(repo)]
    assert judge.main([*rouge, "--workdir", str(tmp_path / "a")]) == 0
    assert "partagée" not in capsys.readouterr().out, "ses propres tests : rien à dire"
    tache_qui_partage(repo)
    assert judge.main([*rouge, "--workdir", str(tmp_path / "b")]) == 0
    sortie = capsys.readouterr().out
    assert "preuve(s) partagée(s)" in sortie
    assert "tasks/T-001/check_c1.py : aussi une preuve de T-001" in sortie
    assert "dans tasks/T-002/" in sortie, "et quoi faire"
    assert (deux / judge.ROUGE).is_file(), "un avertissement, pas un refus"


def test_le_juge_nomme_la_tache_dont_une_preuve_depend(repo: Path, tmp_path: Path) -> None:
    tache_qui_partage(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "T-002 approuvé")
    trace_rouge(repo, "T-002")
    git(repo, "switch", "-qc", "task/T-002")
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "correctif")
    code = judge.main(
        ["check", "--task-id", "T-002", "--repo", str(repo), "--workdir", str(tmp_path / "w")]
    )
    resume = (repo / ".verify" / "judge" / "T-002" / "latest" / "summary.txt").read_text()
    assert code == 0, resume
    assert "info  tasks/T-001/check_c1.py est aussi une preuve de T-001" in resume


def test_un_controle_perime_dit_quel_commit_l_a_perime(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """« tasks/T-001/check_c1.py a changé depuis » ne disait ni qui, ni pourquoi : l'opérateur
    refaisait le contrôle sans comprendre, et recommençait à la fois suivante."""
    tache_qui_partage(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "T-002 approuvé")
    trace_rouge(repo, "T-002")
    test_de_t001 = repo / "tasks" / "T-001" / "check_c1.py"
    test_de_t001.write_text(CHECK_C1 + "# précisé\n")
    git(repo, "commit", "-qam", "précise le test de T-001")
    assert judge.main(["perimes", "--repo", str(repo)]) == 1
    sortie = capsys.readouterr().out
    ligne = next(ligne for ligne in sortie.splitlines() if "PÉRIMÉ  T-002" in ligne)
    assert "tasks/T-001/check_c1.py a changé depuis" in ligne
    assert "« précise le test de T-001 »" in ligne
    assert "preuve aussi de T-001" in ligne


def ranger(repo: Path, lot: str, *ids: str) -> int:
    arguments = ["ranger", "--lot", lot, "--repo", str(repo)]
    for tache in ids:
        arguments += ["--task-id", tache]
    return judge.main(arguments)


def test_une_tache_rangee_dans_un_lot_se_juge_comme_avant(
    repo: Path, run_judge: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cent dossiers de tâches dans l'arborescence de l'éditeur : on ne s'y retrouvait plus
    (30/09/2026, #35). Rangée dans `tasks/<lot>/`, une tâche garde son contrat et son contrôle
    rouge : ils parlent de `tasks/<ID>/…`, et le juge le traduit vers le vrai dossier."""
    git(repo, "switch", "-q", "main")
    assert ranger(repo, "metriques", "T-001") == 0
    assert (repo / "tasks" / "metriques" / "T-001" / "contract.json").is_file()
    assert not (repo / "tasks" / "T-001").exists()
    assert "rangées dans tasks/metriques/" in git(repo, "log", "-1", "--format=%s")
    assert judge.main(["perimes", "--repo", str(repo)]) == 0, "la trace rouge reste valable"
    assert "à jour : 1 tâche(s)" in capsys.readouterr().out
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report
    # Et la preuve, dans son nouveau dossier, reste hors de portée de l'agent.
    preuve = repo / "tasks" / "metriques" / "T-001" / "check_c2.py"
    preuve.write_text("print('plus rien')\n")
    git(repo, "commit", "-qam", "preuve affaiblie")
    code, report = run_judge(repo)
    assert code == 2, report


def test_une_tache_se_cree_dans_son_lot_et_son_identifiant_reste_unique(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    git(repo, "switch", "-q", "main")
    nouveau = ["new", "--repo", str(repo), "--lot", "metriques"]
    assert judge.main([*nouveau, "--task-id", "T-002"]) == 0
    dossier = repo / "tasks" / "metriques" / "T-002"
    assert (dossier / "contract.json").is_file() and (dossier / "test_t_002.py").is_file()
    capsys.readouterr()
    assert judge.main([*nouveau, "--task-id", "T-001"]) == 2, "T-001 existe hors du lot"
    assert "tasks/T-001 existe déjà" in capsys.readouterr().out
    assert judge.main(["new", "--repo", str(repo), "--task-id", "T-002"]) == 2, "et dans un lot"
    assert judge.main([*nouveau[:-1], "Paiement", "--task-id", "T-003"]) == 2, "en minuscules"


def test_le_dossier_d_une_tache_rangee_se_traduit_dans_tout_jeton() -> None:
    """Le contrat d'une tâche rangée parle de `tasks/<ID>` : le dossier se traduit partout où
    il est un chemin, pas seulement devant « / » ; jamais dans un autre nom qui le contient."""
    lot = "tasks/metriques/T-001"

    def traduit(jeton: str) -> str:
        return judge.relocalise(jeton, "T-001", lot)

    assert traduit("tasks/T-001/check_c1.py::test_a") == f"{lot}/check_c1.py::test_a"
    assert traduit("./tasks/T-001/check_c1.py") == f"./{lot}/check_c1.py"
    assert traduit("--rootdir=tasks/T-001") == f"--rootdir={lot}"
    assert traduit("cd tasks/T-001 && pytest -q") == f"cd {lot} && pytest -q"
    assert traduit("tasks/T-001") == lot
    for autre in ("tasks/T-0010/x.py", "soustasks/T-001/x.py", "tasks/T-001.py"):
        assert traduit(autre) == autre, autre


def test_make_taches_dit_ou_en_est_chaque_tache(
    repo: Path, run_judge: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    """Un tableau calculé à partir de git et du journal du juge, jamais tenu à la main."""
    tache_qui_partage(repo, partage=False)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "T-002 approuvé")
    trace_rouge(repo, "T-002")
    assert ranger(repo, "metriques", "T-002") == 0

    def tableau(*options: str) -> str:
        capsys.readouterr()
        assert judge.main(["taches", "--repo", str(repo), *options]) == 0
        return capsys.readouterr().out

    def etat(sortie: str, tache: str) -> str:
        return next(ligne for ligne in sortie.splitlines() if f" {tache} " in ligne)

    sortie = tableau()
    assert "en cours" in etat(sortie, "T-001"), "sa branche existe, pas encore jugée"
    assert "approuvée" in etat(sortie, "T-002") and "metriques" in etat(sortie, "T-002")
    git(repo, "switch", "-q", "task/T-001")
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "correctif")
    assert run_judge(repo)[0] == 0
    assert "vérifiée" in etat(tableau(), "T-001")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'task/T-001' into 'main'", "task/T-001")
    sortie = tableau()
    assert " T-001 " not in sortie and "1 fusionnée(s)" in sortie, "cachée par défaut"
    assert "fusionnée" in etat(tableau("--tout"), "T-001")


def cent_taches(repo: Path, combien: int) -> None:
    """`combien` tâches de plus sur la branche principale, chacune avec sa trace rouge."""
    git(repo, "switch", "-q", "main")
    modele = contrat_de(repo)
    for numero in range(2, combien + 2):
        tache = f"T-{numero:03d}"
        dossier = repo / "tasks" / tache
        dossier.mkdir()
        contrat = json.loads(json.dumps(modele).replace("T-001", tache))
        (dossier / "contract.json").write_text(json.dumps(contrat, indent=2) + "\n")
        for nom in ("check_c1.py", "check_c2.py"):
            (dossier / nom).write_text((repo / "tasks" / "T-001" / nom).read_text())
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "des tâches")
    for numero in range(2, combien + 2):
        contrat = judge.load_contract(repo / "tasks" / f"T-{numero:03d}" / "contract.json")
        trace = judge.trace_rouge(
            contrat.id,
            git(repo, "rev-parse", "HEAD").strip(),
            judge.preuves_rouges(repo, "HEAD", contrat, contrat.id),
            [{"id": c.id, "resultat": "rouge", "raison": "départ"} for c in contrat.criteria],
        )
        (repo / "tasks" / contrat.id / judge.ROUGE).write_text(json.dumps(trace) + "\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "leurs contrôles rouges")


def test_cent_taches_se_lisent_sans_un_processus_git_par_tache(
    repo: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesuré le 30/09/2026, 100 tâches : `make taches` 38 s, `make task-check` sans ID 35 s,
    et 35 s ajoutées à chaque `make task-check ID=…` (la recherche des tests partagés). Un
    processus git par jeton de commande et par tâche : plus de trois mille. Le nombre de
    processus lancés ne dépend plus du nombre de tâches."""
    cent_taches(repo, 30)
    lances: list[list[str]] = []
    vrai = subprocess.run

    def compte(commande: Any, *args: Any, **kwargs: Any) -> Any:
        lances.append(list(commande) if isinstance(commande, (list, tuple)) else [commande])
        return vrai(commande, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", compte)
    judge._fermer_les_lecteurs()
    assert judge.main(["taches", "--repo", str(repo)]) == 0
    assert capsys.readouterr().out.count(" approuvée ") == 30
    assert len(lances) < 20, f"{len(lances)} processus pour 31 tâches"
    lances.clear()
    assert judge.main(["perimes", "--repo", str(repo)]) == 0
    assert "à jour : 31 tâche(s)" in capsys.readouterr().out
    assert len(lances) < 20, f"{len(lances)} processus pour 31 tâches"
    lances.clear()
    contrat = judge.load_contract(repo / "tasks" / "T-001" / "contract.json")
    assert judge.preuves_partagees(repo, "HEAD", contrat, "T-001") == {}
    assert len(lances) < 10, f"{len(lances)} processus pour chercher les tests partagés"


def test_le_lecteur_git_repond_comme_git(tmp_path: Path) -> None:
    """Les réponses du `git cat-file` qui reste ouvert sont celles d'un processus par question,
    noms à espaces compris ; et un dossier effacé puis recréé au même chemin n'est pas lu à
    travers le lecteur de l'ancien."""
    depot = tmp_path / "depot"

    def nouveau(contenu: str) -> None:
        shutil.rmtree(depot, ignore_errors=True)
        (depot / "un dossier").mkdir(parents=True)
        (depot / "un dossier" / "a b.txt").write_text(contenu)
        git(depot, "init", "-q", "-b", "main")
        git(depot, "config", "user.email", "test@example.invalid")
        git(depot, "config", "user.name", "Ephor test")
        git(depot, "add", "-A")
        git(depot, "commit", "-qm", "départ")

    nouveau("premier\n")
    for chemin, attendu in (("un dossier", "tree"), ("un dossier/a b.txt", "blob"), ("rien", None)):
        assert judge.kind_at(depot, "HEAD", chemin) == attendu
    assert judge.contenu_a(depot, "main", "un dossier/a b.txt") == b"premier\n"
    with pytest.raises(judge.JudgeError):
        judge.contenu_a(depot, "main", "un dossier")
    nouveau("second\n")
    assert judge.contenu_a(depot, "main", "un dossier/a b.txt") == b"second\n"


def test_un_verdict_rendu_dans_le_dossier_de_la_tache_se_voit_du_dossier_principal(
    repo: Path, run_judge: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    """Une tâche se juge dans son dossier (#32), et son verdict y restait : du dossier
    principal, `make taches` la disait « en cours », `make score` ne voyait aucun jugement,
    une fusion en avance rapide ne la faisait pas « fusionnée », et `make task-clore`
    effaçait verdicts et mesure avec le dossier (mesuré le 30/09/2026)."""
    import score
    import usage

    git(repo, "switch", "-q", "main")
    dossier = repo.parent / "repo-T-001"
    git(repo, "worktree", "add", "-q", str(dossier), "task/T-001")
    (dossier / "src" / "metrics.py").write_text(FIX)
    git(dossier, "commit", "-qam", "correctif")
    assert run_judge(dossier)[0] == 0
    mesure = usage.somme({"session.jsonl": {**usage.vide("claude-code"), "input": 1200}})
    (dossier / ".verify" / "usage").mkdir(parents=True)
    (dossier / ".verify" / "usage" / "T-001.json").write_text(json.dumps(mesure))

    def tableau() -> str:
        capsys.readouterr()
        assert judge.main(["taches", "--repo", str(repo)]) == 0
        return capsys.readouterr().out

    assert "vérifiée" in next(ligne for ligne in tableau().splitlines() if " T-001 " in ligne)
    assert [e["status"] for e in score.read_journal(repo)] == ["verified_needs_review"]
    assert "T-001" in usage.rapport(repo) and "1 200" in usage.rapport(repo)
    git(repo, "merge", "-q", "--ff-only", "task/T-001")
    assert "1 fusionnée(s)" in tableau(), "vérifiée puis entrée dans main : fusionnée"
    assert usage.rapatrier(dossier, repo) == ["1 jugement(s)", "la mesure de tokens de T-001"]
    assert usage.rapatrier(dossier, repo)[0] == "la mesure de tokens de T-001", "sans doublon"
    git(repo, "worktree", "remove", "--force", str(dossier))
    assert len(score.read_journal(repo)) == 1, "le verdict reste après le dossier"
    assert "1 200" in usage.rapport(repo), "et la mesure aussi, une seule fois"


def test_le_juge_lance_du_dossier_principal_juge_la_tache_dans_son_dossier(
    repo: Path, tmp_path: Path
) -> None:
    """Mesuré le 01/10/2026 : la tâche commitée dans son dossier (#32), `make judge` lancé du
    dossier principal, où est l'opérateur, rendait « rien à juger », et ce verdict plus récent
    refaisait la tâche « en cours » dans `make taches`. La revue : test_scripts.py."""
    git(repo, "switch", "-q", "main")
    dossier = repo.parent / "repo-T-001"
    git(repo, "worktree", "add", "-q", str(dossier), "task/T-001")
    (dossier / "src" / "metrics.py").write_text(FIX)
    git(dossier, "commit", "-qam", "correctif")
    travail = ["--task-id", "T-001", "--workdir", str(tmp_path / "work")]
    assert judge.main(["check", "--repo", str(repo), *travail]) == 0
    resume = dossier / ".verify" / "judge" / "T-001" / "latest" / "summary.txt"
    assert f"jugée dans son dossier, {dossier.resolve()}" in resume.read_text()
    assert not (repo / ".verify" / "judge" / "T-001").exists(), "rien jugé dans le principal"
    # Sans dossier à part, la branche de la tâche est nommée au lieu d'un « rien à juger » nu.
    git(repo, "worktree", "remove", "--force", str(dossier))
    assert judge.main(["check", "--repo", str(repo), *travail]) == 1
    resume = repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt"
    assert "git switch task/T-001" in resume.read_text()


def test_une_approbation_pas_encore_sur_le_serveur_est_signalee(
    repo: Path, run_judge: Runner
) -> None:
    """Audit du 29/09/2026 (F2) : localement, rien n'est hors de portée de l'agent ; le juge
    de la CI lit l'approbation sur la branche protégée du serveur. Le juge local dit quand
    celle qu'il lit n'y est pas encore."""
    (repo / "src" / "metrics.py").write_text(FIX)
    code, _ = run_judge(repo)
    resume = repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt"
    assert code == 0 and "pas encore sur le serveur" not in resume.read_text(), "sans serveur"
    # Le serveur, tel qu'au dernier fetch, n'a pas encore la tâche.
    arbre_vide = git(repo, "hash-object", "-t", "tree", "-w", os.devnull).strip()
    vide = git(repo, "commit-tree", arbre_vide, "-m", "vide")
    git(repo, "update-ref", "refs/remotes/origin/main", vide.strip())
    code, _ = run_judge(repo)
    texte = resume.read_text()
    assert code == 0, "une information, pas un refus : la CI tranchera"
    assert "approbation locale, pas encore sur le serveur (origin/main" in texte
    git(repo, "update-ref", "refs/remotes/origin/main", git(repo, "rev-parse", "main").strip())
    run_judge(repo)
    assert "pas encore sur le serveur" not in resume.read_text(), "poussée : plus rien à dire"


PLAN_EN_SCRIPTS = """Le contrat.

```json
@CONTRAT@
```

La preuve du premier critère, `tasks/FEAT-020/check_c1.py` :

```python
@C1@```

Celle du second, `tasks/FEAT-020/check_c2.py` :

```python
@C2@```
"""


def plan_a_approuver(repo: Path, tmp_path: Path) -> Path:
    """La réponse d'un planner : contrat et preuves, prête pour `make task-approve`."""
    criteres = [
        {
            "id": "C1",
            "description": "lots de tailles différentes",
            "check": ["{python}", "tasks/FEAT-020/check_c1.py"],
        },
        {
            "id": "C2",
            "description": "liste vide donne None",
            "check": ["{python}", "tasks/FEAT-020/check_c2.py"],
        },
    ]
    texte = (
        PLAN_EN_SCRIPTS.replace(
            "@CONTRAT@", json.dumps(contract(id="FEAT-020", criteria=criteres), indent=2)
        )
        .replace("@C1@", CHECK_C1)
        .replace("@C2@", CHECK_C2)
    )
    plan = tmp_path / "plan.md"
    plan.write_text(texte, encoding="utf-8")
    git(repo, "switch", "-q", "main")
    return plan


# Le pseudo-terminal naît dans un processus à part : pytest a plusieurs fils d'exécution, et
# `forkpty` y risque un blocage dans l'enfant (avertissement de Python 3.12).
PILOTE_TERMINAL = """
import os, pty, sys
reponse, argv = sys.argv[1], sys.argv[2:]
pid, fd = pty.fork()
if pid == 0:
    os.execvp(argv[0], argv)
sortie, repondu = b"", False
while True:
    try:
        morceau = os.read(fd, 4096)
    except OSError:
        break
    if not morceau:
        break
    sortie += morceau
    if reponse != "-" and not repondu and "Tapez « oui »".encode() in sortie:
        os.write(fd, (reponse + "\\n").encode())
        repondu = True
_, statut = os.waitpid(pid, 0)
sys.stdout.buffer.write(sortie)
sys.exit(os.waitstatus_to_exitcode(statut))
"""


def dans_un_terminal(argv: list[str], reponse: str | None, cwd: Path) -> tuple[int, str]:
    """Lance la commande dans un vrai pseudo-terminal, comme l'opérateur, et répond à la
    question quand elle arrive. Pas de raccourci pour les tests : la confirmation n'en a pas."""
    env = {k: v for k, v in os.environ.items() if k not in operateur.VARIABLES}
    fait = subprocess.run(
        [sys.executable, "-c", PILOTE_TERMINAL, "-" if reponse is None else reponse, *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        timeout=300,
        check=False,
    )
    return fait.returncode, fait.stdout.decode("utf-8", "replace")


def approuve(plan: Path, repo: Path, *options: str) -> list[str]:
    return [
        sys.executable,
        str(ROOT / ".ephor" / "judge.py"),
        "approuve",
        "--task-id",
        "FEAT-020",
        "--from",
        str(plan),
        "--repo",
        str(repo),
        "--workdir",
        str(repo.parent / "work"),
        *options,
    ]


def test_l_approbation_d_un_seul_geste(repo: Path, tmp_path: Path) -> None:
    """Vu en session réelle : contrats et tests viennent d'un agent, et les gestes de
    l'opérateur (task-new, recopier les tests, task-check, commit) étaient mécaniques. Un seul
    geste les enchaîne, après le résumé et un « oui » tapé dans son terminal."""
    plan = plan_a_approuver(repo, tmp_path)
    avant = git(repo, "rev-parse", "HEAD").strip()
    code, sortie = dans_un_terminal(approuve(plan, repo), "oui", repo)
    assert code == 0, sortie
    assert "liste vide donne None" in sortie and "check_c2.py" in sortie, "le résumé est montré"
    assert "rouge  C1" in sortie and "rouge  C2" in sortie, "le contrôle rouge a tourné"
    assert "FEAT-020 approuvé" in sortie
    assert git(repo, "log", "-1", "--format=%s").strip() == "FEAT-020 : contrat approuvé"
    assert git(repo, "rev-parse", "HEAD~1").strip() == avant
    fichiers = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(fichiers) == [
        "tasks/FEAT-020/check_c1.py",
        "tasks/FEAT-020/check_c2.py",
        "tasks/FEAT-020/contract.json",
        "tasks/FEAT-020/rouge.json",
    ]
    assert git(repo, "status", "--porcelain", "--", "tasks") == ""


def moteur_dans(repo: Path) -> None:
    """Le moteur, là où un projet l'a : le juge, ce qu'il lit, et les scripts de l'opérateur."""
    moteur = ROOT / ".ephor"
    (repo / ".ephor").mkdir(exist_ok=True)
    for nom in ("judge.py", "zones.txt", "operateur.py", "usage.py"):
        shutil.copy(moteur / nom, repo / ".ephor" / nom)
    shutil.copytree(moteur / "scripts", repo / ".ephor" / "scripts")


def test_l_approbation_ouvre_le_dossier_de_la_tache(repo: Path, tmp_path: Path) -> None:
    """Plusieurs tâches dans un même dossier se marchaient dessus (session réelle, #32). Une
    tâche approuvée s'ouvre maintenant dans son dossier, avec la ligne qui y lance l'agent ;
    et se ferme, dossier et branche, une fois fusionnée."""
    moteur_dans(repo)
    plan = plan_a_approuver(repo, tmp_path)
    code, sortie = dans_un_terminal(approuve(plan, repo), "oui", repo)
    assert code == 0, sortie
    dossier = (repo.parent / "repo-FEAT-020").resolve()
    assert git(dossier, "branch", "--show-current").strip() == "task/FEAT-020"
    assert f'cd "{dossier}" && claude "/feature FEAT-020"' in sortie, sortie

    def clore(*options: str) -> subprocess.CompletedProcess[str]:
        env = {k: v for k, v in os.environ.items() if k not in operateur.VARIABLES}
        return subprocess.run(
            ["bash", ".ephor/scripts/clore.sh", "FEAT-020", *options],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    refus = clore()
    assert refus.returncode == 2 and "pas fusionnée" in refus.stdout, refus.stdout
    assert dossier.is_dir(), "une tâche en cours garde son dossier"
    git(dossier, "commit", "-q", "--allow-empty", "-m", "le travail")
    # Le juge a rendu son verdict DANS le dossier de la tâche ; la fusion se fait en avance
    # rapide, sans commit qui nomme la branche. Lu dans le seul dossier principal, le verdict
    # manquait : la tâche restait « en cours », et la fermeture refusée (mesuré le 30/09/2026).
    verdict = {"run_id": "a1b2c3", "task_id": "FEAT-020", "status": "verified_needs_review"}
    (dossier / ".verify").mkdir(exist_ok=True)
    (dossier / ".verify" / "journal.jsonl").write_text(json.dumps(verdict) + "\n")
    git(repo, "merge", "-q", "--ff-only", "task/FEAT-020")
    # Lancé depuis le dossier de la tâche, il se retirait lui-même, et disait « tâche close »
    # avec la branche encore là (audit du 01/10/2026).
    if not (dossier / ".ephor" / "scripts").is_dir():
        shutil.copytree(repo / ".ephor", dossier / ".ephor", dirs_exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in operateur.VARIABLES}
    dedans = subprocess.run(
        ["bash", ".ephor/scripts/clore.sh", "FEAT-020"],
        cwd=dossier,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert dedans.returncode == 2 and "depuis votre copie" in dedans.stdout, dedans.stdout
    assert dossier.is_dir()
    if git(dossier, "status", "--porcelain").strip():
        shutil.rmtree(dossier / ".ephor")  # la copie faite plus haut pour l'essai
    fermee = clore()
    assert fermee.returncode == 0, fermee.stdout + fermee.stderr
    assert not dossier.exists() and "task/FEAT-020" not in git(repo, "branch")
    assert "gardé(s)" in fermee.stdout and "1 jugement(s)" in fermee.stdout
    garde = (repo / ".verify" / "journal.jsonl").read_text(encoding="utf-8")
    assert '"a1b2c3"' in garde, "le verdict reste dans le dossier principal"


def test_l_approbation_sans_dossier_le_dit(repo: Path, tmp_path: Path) -> None:
    moteur_dans(repo)
    plan = plan_a_approuver(repo, tmp_path)
    code, sortie = dans_un_terminal(approuve(plan, repo, "--sans-dossier"), "oui", repo)
    assert code == 0, sortie
    assert not (repo.parent / "repo-FEAT-020").exists()
    assert "make task-worktree ID=FEAT-020" in sortie


@pytest.mark.parametrize("reponse", ["non", "", "o"])
def test_sans_oui_rien_n_est_fait(repo: Path, tmp_path: Path, reponse: str) -> None:
    plan = plan_a_approuver(repo, tmp_path)
    avant = git(repo, "rev-parse", "HEAD").strip()
    code, sortie = dans_un_terminal(approuve(plan, repo), reponse, repo)
    assert code == 1 and "rien n'est fait" in sortie
    assert not (repo / "tasks" / "FEAT-020").exists()
    assert git(repo, "rev-parse", "HEAD").strip() == avant


def test_l_approbation_veut_un_terminal_et_la_branche_principale(
    repo: Path, tmp_path: Path
) -> None:
    """Sans terminal (la CI, un agent), pas de « oui » possible : refus. Et l'approbation se
    commite sur la branche principale, nulle part ailleurs."""
    plan = plan_a_approuver(repo, tmp_path)
    sans_terminal = subprocess.run(
        approuve(plan, repo),
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        timeout=120,
        check=False,
    )
    assert sans_terminal.returncode == 1 and "pas de terminal" in sans_terminal.stdout
    assert not (repo / "tasks" / "FEAT-020").exists()
    git(repo, "switch", "-q", "task/T-001")
    code, sortie = dans_un_terminal(approuve(plan, repo), "oui", repo)
    assert code == 2 and "branche principale" in sortie


def test_un_plan_sans_ses_preuves_est_refuse_avant_la_question(repo: Path, tmp_path: Path) -> None:
    plan = plan_a_approuver(repo, tmp_path)
    plan.write_text(plan.read_text(encoding="utf-8").split("Celle du second")[0], encoding="utf-8")
    code, sortie = dans_un_terminal(approuve(plan, repo), None, repo)
    assert code == 2 and "tasks/FEAT-020/check_c2.py" in sortie and "Tapez" not in sortie


# Un outil de test qui n'est pas pytest (Maven, Jest, Go…), en miniature : il écrit un rapport
# JUnit dans le dossier qu'on lui donne, comme `-Dsurefire.reportsDirectory={rapport}`.
LANCEUR_JUNIT = """import sys
from pathlib import Path

sys.path.insert(0, ".")
dossier, nom = Path(sys.argv[1]), (sys.argv[2] if len(sys.argv) > 2 else "vide")
if nom == "rien":
    sys.exit(1)
try:
    from src.metrics import accuracy

    ok, detail = accuracy([]) is None, "accuracy([]) n'est pas None"
except Exception as exc:
    ok, detail = False, type(exc).__name__
echec = "" if ok else f'<failure message="{detail}"/>'
cas = f'<testcase classname="acme.MetricsTest" name="{nom}">{echec}</testcase>'
(dossier / "TEST-acme.MetricsTest.xml").write_text(f'<testsuite tests="1">{cas}</testsuite>')
sys.exit(0 if ok else 1)
"""


def critere_junit(nom: str = "vide") -> dict[str, Any]:
    return {
        "id": "C3",
        "description": "liste vide, jugée sur un rapport JUnit",
        "check": ["{python}", "tasks/T-001/lanceur.py", "{rapport}", nom],
        "rapport_junit": ["acme.MetricsTest.vide"],
        "preuves": ["tasks/T-001/lanceur.py"],
    }


def test_une_preuve_par_rapport_junit_va_au_bout(
    repo: Path, run_judge: Runner, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Audit du 29/09/2026 (F5) : hors pytest, une preuve n'était jugée que sur son code de
    retour, donc refusée en risque élevé. Un rapport JUnit écrit hors de la copie jugée, où
    chaque test désigné a tourné et réussi, est une preuve qui va au bout."""
    contrat = contrat_de(repo)
    contrat["risk"] = "eleve"
    contrat["criteria"] = [dict(critere_junit(), id="C1")]
    sur_main(repo, contrat, {"tasks/T-001/lanceur.py": LANCEUR_JUNIT})
    assert task_check(repo, tmp_path / "work") == 0, "accepté en risque élevé"
    assert "rouge  C1" in capsys.readouterr().out
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "contrat approuvé")
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, report = run_judge(repo)
    assert code == 0, report["reasons"]
    assert (repo / ".verify" / "judge" / "T-001" / "latest" / "logs" / "C1.junit").is_dir()


@pytest.mark.parametrize(
    ("nom", "attendu"),
    [
        ("autre", "test désigné jamais lancé : acme.MetricsTest.vide"),
        ("rien", None),  # sans rapport, on ne sait pas : l'opérateur lit la raison
    ],
)
def test_le_controle_rouge_lit_le_rapport_junit(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str], nom: str, attendu: str | None
) -> None:
    contrat = contrat_de(repo)
    contrat["criteria"].append(critere_junit(nom))
    sur_main(repo, contrat, {"tasks/T-001/lanceur.py": LANCEUR_JUNIT})
    code = task_check(repo, tmp_path / "work")
    sortie = capsys.readouterr().out
    if attendu is None:
        assert code == 0 and "rouge  C3" in sortie
    else:
        assert code == 1 and attendu in sortie


def test_un_rapport_junit_absent_ou_rate_refuse_le_critere(repo: Path, run_judge: Runner) -> None:
    """Après le travail : sans rapport, ou avec le test désigné absent, le critère échoue ; le
    code de retour seul ne suffit pas."""
    git(repo, "switch", "-q", "main")
    contrat = contrat_de(repo)
    contrat["criteria"].append(critere_junit("autre"))
    (repo / "tasks" / "T-001" / "contract.json").write_text(json.dumps(contrat, indent=2) + "\n")
    (repo / "tasks" / "T-001" / "lanceur.py").write_text(LANCEUR_JUNIT)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "C3 en rapport JUnit")
    trace_rouge(repo)
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "rebase", "-q", "main")
    (repo / "src" / "metrics.py").write_text(FIX)
    code, _ = run_judge(repo)
    resume = (repo / ".verify" / "judge" / "T-001" / "latest" / "summary.txt").read_text()
    assert code != 0
    assert "test désigné absent du rapport : acme.MetricsTest.vide" in resume


@pytest.mark.parametrize(
    ("changement", "motif"),
    [
        (
            {"rapport_junit": ["X.y"], "preuves": ["tasks/T-001/lanceur.py"]},
            "veut {rapport} dans check",
        ),
        (
            {"check": ["{python}", "x.py", "{rapport}"], "rapport_junit": ["X.y"]},
            "veut les fichiers de ses tests dans preuves",
        ),
        ({"check": ["{python}", "x.py", "{rapport}"]}, "{rapport} ne sert qu'avec rapport_junit"),
        (
            {"check": ["{python}", "x.py", "{rapport}"], "rapport_junit": [], "preuves": ["x.py"]},
            "liste non vide",
        ),
    ],
)
def test_un_critere_junit_mal_forme_est_refuse(changement: dict[str, Any], motif: str) -> None:
    critere = {"id": "C1", "description": "un critère", "check": ["{python}", "x.py"], **changement}
    with pytest.raises(judge.JudgeError) as refus:
        judge.parse_contract(contract(id="T-001", criteria=[critere]), "T-001")
    assert any(motif in message for message in refus.value.messages), refus.value.messages


# ------------------------------------------------------ audit du juge du 01/10/2026


def deux_taches_approuvees(repo: Path, partage: bool = False) -> None:
    """T-001 (la fixture) et T-002, toutes deux approuvées sur main, avec leur trace rouge."""
    tache_qui_partage(repo, partage=partage)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "T-002 approuvé")
    trace_rouge(repo, "T-002")


def test_le_rangement_ne_commite_que_les_dossiers_ranges(repo: Path) -> None:
    """`git commit -- tasks` emportait la retouche pas commitée d'un AUTRE contrat : approuvée
    par le commit du rangement sans avoir été relue."""
    deux_taches_approuvees(repo)
    contrat = repo / "tasks" / "T-002" / "contract.json"
    retouche = contrat.read_text().replace('"src/"', '"src/", "docs/"')
    contrat.write_text(retouche)
    assert ranger(repo, "metriques", "T-001") == 0
    fichiers = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert all(f.startswith(("tasks/T-001/", "tasks/metriques/T-001/")) for f in fichiers), fichiers
    assert git(repo, "status", "--porcelain", "--", "tasks/T-002").strip(), "toujours pas commitée"


def test_le_rangement_refuse_de_casser_le_contrat_d_une_autre_tache(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-002 lance le test de T-001 : ranger T-001 rendait sa preuve introuvable, alors que le
    message disait « contrats et contrôles rouges restent valables »."""
    deux_taches_approuvees(repo, partage=True)
    avant = git(repo, "rev-parse", "HEAD")
    assert ranger(repo, "metriques", "T-001") == 2
    assert "le contrat de T-002 cite les fichiers de T-001" in capsys.readouterr().out
    assert git(repo, "rev-parse", "HEAD") == avant and (repo / "tasks" / "T-001").is_dir()


def test_un_lot_ne_s_appelle_pas_tasks_et_un_identifiant_n_a_pas_de_saut_de_ligne(
    repo: Path,
) -> None:
    git(repo, "switch", "-q", "main")
    assert ranger(repo, "tasks", "T-001") == 2
    assert judge.ID_PATTERN.match("FEAT-1\n") is None and judge.ID_PATTERN.match("FEAT-1")
    assert judge.LOT_PATTERN.match("paiement\n") is None and judge.LOT_PATTERN.match("tasksx")


def test_une_branche_sans_commit_n_est_pas_fusionnee(
    repo: Path, run_judge: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le juge rend VÉRIFIÉ sur du travail pas commité (« commitez-le tel quel ») ; une branche
    sans commit à elle est « entrée » dans main : `make taches` la disait fusionnée, et la
    cachait, alors que main ne contenait que son contrat."""
    (repo / "src" / "metrics.py").write_text(FIX)
    assert run_judge(repo)[0] == 0

    def ligne() -> str:
        capsys.readouterr()
        assert judge.main(["taches", "--repo", str(repo), "--tout"]) == 0
        return next(lg for lg in capsys.readouterr().out.splitlines() if " T-001 " in lg)

    assert "vérifiée" in ligne()
    git(repo, "commit", "-qam", "correctif")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--ff-only", "task/T-001")
    assert "fusionnée" in ligne(), "commitée puis entrée dans main en avance rapide"


def test_une_approbation_changee_sur_main_depuis_le_depart_de_la_branche_est_refusee(
    repo: Path, run_judge: Runner
) -> None:
    """Le juge lisait contrat, preuves et contrôle rouge au point de départ de la branche : une
    preuve resserrée ensuite sur main ne comptait pas, et un correctif à moitié juste ressortait
    VÉRIFIÉ ; une tâche retirée de main aussi (audit du 01/10/2026)."""
    git(repo, "switch", "-q", "main")
    preuve = repo / "tasks" / "T-001" / "check_c2.py"
    preuve.write_text(CHECK_C2 + "assert accuracy([(1, 2)]) == 0.5\n")
    git(repo, "commit", "-qam", "preuve resserrée")
    trace_rouge(repo)
    git(repo, "switch", "-q", "task/T-001")
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "correctif")
    code, report = run_judge(repo)
    assert code == 2 and report["status"] == "rejected", report
    assert "tasks/T-001/check_c2.py" in report["reasons"][0]
    assert "git merge main" in report["reasons"][0]
    git(repo, "merge", "-q", "--no-edit", "main")
    assert run_judge(repo)[0] == 0, "main intégrée : jugée contre l'approbation d'aujourd'hui"
    git(repo, "switch", "-q", "main")
    git(repo, "rm", "-rq", "tasks/T-001")
    git(repo, "commit", "-qm", "T-001 abandonnée")
    git(repo, "switch", "-q", "task/T-001")
    code, report = run_judge(repo)
    assert code == 2 and "git merge main" in report["reasons"][0], report


TEST_ACCEPT = (
    "import sys\nimport unittest\n\nsys.path.insert(0, '.')\nfrom src.metrics import accuracy\n\n\n"
    "class Accept(unittest.TestCase):\n    def test_micro(self):\n"
    "        self.assertAlmostEqual(accuracy([(9, 10), (1, 100)]), 10 / 110)\n"
)


@pytest.mark.parametrize(
    "check",
    [
        ["{python}", "-m", "unittest", "tests.test_accept"],
        ["{python}", "-m", "unittest", "-v", "tests.test_accept.Accept.test_micro"],
        ["{python}", "-m", "pytest", "-q", "--pyargs", "tests.test_accept"],
    ],
    ids=["unittest", "unittest-methode", "pytest-pyargs"],
)
def test_une_preuve_designee_par_son_nom_de_module_est_protegee(
    make_repo: Callable[..., Path], run_judge: Runner, check: list[str]
) -> None:
    """`python -m unittest tests.test_accept`, avec `tests/` dans le périmètre : le juge ne
    reconnaissait que des chemins, l'agent vidait le test, et VÉRIFIÉ (audit du 01/10/2026)."""
    repo = make_repo(criteria=[{"id": "C1", "description": "micro", "check": check}])
    git(repo, "switch", "-q", "main")
    (repo / "tests" / "test_accept.py").write_text(TEST_ACCEPT)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "la preuve")
    trace_rouge(repo)
    git(repo, "switch", "-q", "task/T-001")
    git(repo, "merge", "-q", "--no-edit", "main")
    (repo / "tests" / "test_accept.py").write_text("")
    git(repo, "commit", "-qam", "preuve vidée")
    code, report = run_judge(repo)
    assert code == 2 and report["status"] == "rejected", report
    assert "tests/test_accept.py" in " ".join(report["reasons"])
    # Hors d'un lanceur de tests, un nom pointé est le programme essayé, pas la preuve.
    assert judge.modules_cites(["{python}", "-m", "src.metrics"]) == []


def test_une_fusion_annulee_n_est_plus_fusionnee(
    repo: Path, run_judge: Runner, capsys: pytest.CaptureFixture[str]
) -> None:
    """Le commit qui annule la fusion nomme lui aussi `task/<ID>` : la tâche restait
    « fusionnée » pour toujours, cachée de `make taches`, et `make task-clore` la fermait."""
    (repo / "src" / "metrics.py").write_text(FIX)
    git(repo, "commit", "-qam", "correctif")
    assert run_judge(repo)[0] == 0

    def ligne() -> str:
        capsys.readouterr()
        assert judge.main(["taches", "--repo", str(repo), "--tout"]) == 0
        return next(lg for lg in capsys.readouterr().out.splitlines() if " T-001 " in lg)

    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'task/T-001' into 'main'", "task/T-001")
    assert "fusionnée" in ligne()
    git(repo, "revert", "-m", "1", "--no-edit", "HEAD")
    assert "fusionnée" not in ligne() and "vérifiée" in ligne(), "annulée : de nouveau à fusionner"
    git(repo, "revert", "--no-edit", "HEAD")
    assert "fusionnée" in ligne(), "l'annulation annulée : fusionnée de nouveau"
    # Le bouton « Revert » de GitHub passe par une branche `revert-<n>-task/<ID>`.
    git(
        repo,
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "Merge pull request #13 from o/revert-12-task/T-001",
    )
    assert "fusionnée" not in ligne()


def test_un_nom_de_fichier_qui_n_est_pas_de_l_utf8_est_un_refus(
    repo: Path, run_judge: Runner
) -> None:
    """Un `UnicodeDecodeError` faisait planter le juge : « VERDICT : running », et le journal
    gardait ce statut, qui refaisait la tâche « en cours » dans `make taches`."""
    (repo / "src" / "metrics.py").write_text(FIX)
    (repo / os.fsdecode(b"src/caf\xe9.txt")).write_text("x")
    code, report = run_judge(repo)
    assert code == 2 and report["status"] == "rejected", report
    assert "pas de l'UTF-8" in report["reasons"][0]
    journal = (repo / ".verify" / "journal.jsonl").read_text().splitlines()
    assert json.loads(journal[-1])["status"] == "rejected"


def test_un_contrat_qui_n_est_pas_en_utf8_est_refuse_sans_planter(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    contrat = repo / "tasks" / "T-001" / "contract.json"
    contrat.write_bytes(contrat.read_text().replace("micro", "é").encode("latin-1"))
    assert judge.main(["validate", "--task-id", "T-001", "--repo", str(repo)]) == 2
    assert "n'est pas en UTF-8" in capsys.readouterr().out


def test_le_test_partage_d_une_tache_rangee_est_signale(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Rangée, la tâche comparait ses preuves sous leur nom canonique (`tasks/<ID>/…`) à celles
    des autres sous leur vrai chemin : son test partagé n'était jamais signalé."""
    deux_taches_approuvees(repo)
    assert ranger(repo, "metriques", "T-001") == 0
    contrat = repo / "tasks" / "T-002" / "contract.json"
    lu = json.loads(contrat.read_text())
    lu["criteria"].insert(
        0,
        {
            "id": "C1",
            "description": "lots",
            "check": ["{python}", "tasks/metriques/T-001/check_c1.py"],
        },
    )
    contrat.write_text(json.dumps(lu, indent=2) + "\n")
    git(repo, "commit", "-qam", "T-002 lance le test de T-001")
    capsys.readouterr()
    rouge = ["rouge", "--task-id", "T-001", "--repo", str(repo), "--workdir", str(tmp_path / "r")]
    assert judge.main(rouge) == 0
    sortie = capsys.readouterr().out
    assert "tasks/metriques/T-001/check_c1.py : aussi une preuve de T-002" in sortie


def test_l_installation_d_une_tache_rangee_trouve_ses_fichiers(tmp_path: Path) -> None:
    dossier = tmp_path / "tasks" / "metriques" / "T-001"
    dossier.mkdir(parents=True)
    donnees = contract(
        setup=["uv", "pip", "install", "-r", "tasks/T-001/requirements.txt"],
        verify=["bash", "tasks/T-001/verifie.sh"],
    )
    (dossier / "contract.json").write_text(json.dumps(donnees))
    lu = judge.load_contract(dossier / "contract.json", "T-001")
    assert lu.setup == ("uv", "pip", "install", "-r", "tasks/metriques/T-001/requirements.txt")
    assert lu.verify == ("bash", "tasks/metriques/T-001/verifie.sh")


def test_la_branche_que_designe_le_serveur_sert_de_base(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`origin/HEAD` sur `develop` : le juge acceptait d'y approuver et d'y ranger, mais
    `make taches`, `make task-check` sans ID et `make judge` disaient « base introuvable »."""
    git(repo, "branch", "-m", "main", "develop")
    sommet = git(repo, "rev-parse", "develop").strip()
    git(repo, "update-ref", "refs/remotes/origin/develop", sommet)
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/develop")
    assert judge.resolve_base(repo, None)[0] == "develop"
    assert judge.main(["taches", "--repo", str(repo)]) == 0
    assert " T-001 " in capsys.readouterr().out
