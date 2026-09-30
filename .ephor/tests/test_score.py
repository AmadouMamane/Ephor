"""Le tableau de bord : ce qu'il calcule, et ce qu'il refuse d'inventer."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import EPHOR

sys.path.insert(0, str(EPHOR))

import score

JOURNAL = [
    # FEAT-001 jugée trois fois : la dépense en tokens est cumulée, pas additionnable.
    {
        "task_id": "FEAT-001",
        "status": "verification_failed",
        "criteria_passed": 1,
        "criteria_total": 3,
        "seconds": 12.0,
        "billable_tokens": 40_000,
        "finished_at": "2026-09-20T09:00:00+00:00",
    },
    {
        "task_id": "FEAT-001",
        "status": "verification_failed",
        "criteria_passed": 2,
        "criteria_total": 3,
        "seconds": 11.0,
        "billable_tokens": 70_000,
        "finished_at": "2026-09-20T10:00:00+00:00",
    },
    {
        "task_id": "FEAT-001",
        "status": "verified_needs_review",
        "criteria_passed": 3,
        "criteria_total": 3,
        "seconds": 10.0,
        "billable_tokens": 90_000,
        "finished_at": "2026-09-20T11:00:00+00:00",
    },
    # FEAT-002 refusée : contrat invalide, rien n'a été exécuté.
    {
        "task_id": "FEAT-002",
        "status": "rejected",
        "criteria_passed": 0,
        "criteria_total": 0,
        "seconds": 0.0,
        "billable_tokens": None,
        "finished_at": "2026-09-21T09:00:00+00:00",
    },
]


def write_journal(repo: Path, entries: list[dict[str, object]]) -> None:
    folder = repo / ".verify"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "journal.jsonl").write_text(
        "".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8"
    )


def test_without_any_judgement_it_says_what_to_run(tmp_path: Path) -> None:
    text = score.report(score.read_journal(tmp_path))
    assert "make judge" in text
    assert ".ephor/docs/09" in text


def test_the_four_indicators_come_from_the_journal(tmp_path: Path) -> None:
    write_journal(tmp_path, JOURNAL)
    text = score.report(score.read_journal(tmp_path))
    assert "4 jugement(s) sur 2 tâche(s)" in text
    # Des TÂCHES, d'après le dernier jugement de chacune : FEAT-001 a fini vérifiée, FEAT-002
    # refusée. Compter les jugements (1 / 4) mesurait l'activité, que la doc 09 écarte : une
    # tâche rejugée trois fois pesait comme trois tâches.
    assert "tâches vérifiées        1 / 2" in text
    assert "3 / 3" in text  # critères satisfaits, au dernier jugement de chaque tâche
    # Coût : le dernier relevé de chaque tâche (90 000), divisé par ses critères satisfaits.
    assert "30 000 tokens" in text
    # Ce que le journal ne sait pas, il ne l'invente pas.
    assert "make mutants" in text
    assert "étage 3 de la CI" in text


def test_the_trend_is_readable_at_a_glance(tmp_path: Path) -> None:
    write_journal(tmp_path, JOURNAL)
    assert "✗✗✓·" in score.report(score.read_journal(tmp_path))


def test_nothing_to_judge_is_not_counted_as_a_failure(tmp_path: Path) -> None:
    """Un dépôt inchangé ou un contrat refusé ne dit pas la même chose qu'un critère raté."""
    write_journal(tmp_path, [*JOURNAL, {**JOURNAL[0], "status": "no_change"}])
    text = score.report(score.read_journal(tmp_path))
    assert "~" in text
    assert "1 refusé(s)" in text
    assert "1 sans changement" in text


def test_a_damaged_line_does_not_hide_the_others(tmp_path: Path) -> None:
    write_journal(tmp_path, JOURNAL)
    path = tmp_path / ".verify" / "journal.jsonl"
    path.write_text("{pas du json\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    assert len(score.read_journal(tmp_path)) == len(JOURNAL)


def test_one_task_can_be_looked_at_alone(tmp_path: Path) -> None:
    write_journal(tmp_path, JOURNAL)
    entries = score.read_journal(tmp_path, "FEAT-001")
    assert len(entries) == 3
    assert "1 tâche(s)" in score.report(entries)


def test_une_tache_rejugee_ne_fait_pas_baisser_le_debit(tmp_path: Path) -> None:
    """Deux tâches, toutes deux finies vérifiées, l'une après deux essais : 2 / 2, pas 2 / 4."""
    autre = {**JOURNAL[2], "task_id": "FEAT-003"}
    write_journal(tmp_path, [*JOURNAL[:3], autre])
    text = score.report(score.read_journal(tmp_path))
    assert "tâches vérifiées        2 / 2    100 %" in text
    assert "✗✗✓✓" in text, "les jugements restent visibles, en tendance"


def test_un_identifiant_abime_ne_fausse_pas_le_detail(tmp_path: Path) -> None:
    write_journal(tmp_path, [{**JOURNAL[2], "task_id": 5}])
    assert "5" + " " * 15 + "1 jugement(s)   1 vérifié(s)" in score.report(
        score.read_journal(tmp_path)
    )


def test_le_score_compare_les_niveaux_d_implementation() -> None:
    """#24 : le niveau du contrat (rapide, standard, fort) dit quel modèle écrit la tâche. Avant
    d'en faire une règle, on mesure ce que chacun coûte, sur de vraies tâches."""
    journal = [
        {**JOURNAL[2], "niveau": "fort"},
        {
            "task_id": "FEAT-003",
            "status": "verified_needs_review",
            "criteria_passed": 2,
            "criteria_total": 2,
            "seconds": 5.0,
            "billable_tokens": 20_000,
            "finished_at": "2026-09-22T09:00:00+00:00",
            "niveau": "rapide",
        },
        JOURNAL[3],
    ]
    texte = score.report(journal)
    assert "par niveau d'implémentation" in texte
    assert "fort       1 / 1   vérifiée(s) · 30 000 tokens par critère" in texte
    assert "rapide     1 / 1   vérifiée(s) · 10 000 tokens par critère" in texte
    assert "par niveau" not in score.report(JOURNAL), "rien à dire sans niveau"
