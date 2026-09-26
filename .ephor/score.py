#!/usr/bin/env python3
"""Ce qu'Ephor produit vraiment : les quatre indicateurs de `.ephor/docs/09-evaluer-ephor.md`.

Lit le journal des jugements (`.verify/journal.jsonl`, une ligne par `make judge`), qui
survit à `make clean`. Sans lui, le rapport d'une tâche est écrasé à chaque exécution et
aucune tendance n'est observable — or on ne bloque jamais sur une exécution isolée.

Ce qui n'est pas dans le journal n'est pas inventé : la commande dit où le prendre.

Bibliothèque standard uniquement, et syntaxe Python 3.9 : ce script se lance avec le
`python3` du système, pas avec l'environnement du projet.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import usage  # même dossier : les deux modules mesurent, autant partager leurs aides


def entier(valeur: Any) -> int:
    """Un compte qui n'en est pas un vaut zéro : un journal abîmé ne fausse pas le total."""
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else 0


VERIFIED = "verified_needs_review"
# Tout n'est pas un échec de l'agent : un contrat refusé et un dépôt inchangé disent
# autre chose, et les confondre fausserait la tendance qu'on regarde.
SYMBOLS = {VERIFIED: "✓", "rejected": "·", "no_change": "~", "stale": "~"}
ASIDE = {"rejected": "refusé(s)", "no_change": "sans changement", "stale": "périmé(s)"}


def read_journal(repo: Path, task: str | None = None) -> list[dict[str, Any]]:
    path = repo / ".verify" / "journal.jsonl"
    entries: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return entries
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue  # une ligne abîmée ne doit pas masquer les autres
        if isinstance(entry, dict) and (task is None or entry.get("task_id") == task):
            entries.append(entry)
    return entries


def percent(part: int, whole_count: int) -> str:
    return f"{100 * part // whole_count} %" if whole_count else "—"


def cost_per_criterion(entries: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Rend (tokens, critères satisfaits, tâches mesurées).

    La mesure de tokens est cumulée par tâche, pas par exécution : on ne retient donc
    que le dernier jugement de chaque tâche, sinon la même dépense serait comptée deux fois.
    """
    last: dict[str, dict[str, Any]] = {}
    for entry in entries:
        task = str(entry.get("task_id", ""))
        if entier(entry.get("billable_tokens")):
            last[task] = entry
    tokens = sum(entier(entry.get("billable_tokens")) for entry in last.values())
    passed = sum(entier(entry.get("criteria_passed")) for entry in last.values())
    return tokens, passed, len(last)


def report(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return (
            "aucun jugement dans le journal (.verify/journal.jsonl).\n"
            "Lancez `make judge TASK=<ID>` : chaque verdict y laisse une ligne.\n"
            "Méthode et indicateurs : .ephor/docs/09-evaluer-ephor.md."
        )
    tasks = sorted({str(entry.get("task_id", "?")) for entry in entries})
    verified = sum(1 for entry in entries if entry.get("status") == VERIFIED)
    criteria = sum(entier(entry.get("criteria_total")) for entry in entries)
    passed = sum(entier(entry.get("criteria_passed")) for entry in entries)
    tokens, billed_passed, measured = cost_per_criterion(entries)
    trend = "".join(SYMBOLS.get(str(entry.get("status")), "✗") for entry in entries[-12:])
    since = str(entries[0].get("finished_at") or "?")[:10]

    lines = [
        f"score d'Ephor · {len(entries)} jugement(s) sur {len(tasks)} tâche(s) · depuis {since}",
        "",
        f"  tâches vérifiées      {verified:>3} / {len(entries):<3}"
        f"  {percent(verified, len(entries)):<6}  {trend}",
        f"  critères satisfaits   {passed:>3} / {criteria:<3}  {percent(passed, criteria)}",
    ]
    aside = [
        f"{sum(1 for entry in entries if entry.get('status') == status)} {label}"
        for status, label in ASIDE.items()
        if any(entry.get("status") == status for entry in entries)
    ]
    if aside:
        lines.insert(3, "       dont             " + " · ".join(aside))
    if billed_passed:
        lines.append(
            f"  coût par critère      {usage.milliers(tokens // billed_passed):>12} tokens"
            f"  ({measured} tâche(s) mesurée(s))"
        )
    else:
        lines.append("  coût par critère      pas encore mesuré (voir `make usage`)")
    lines += [
        "  score de mutation     hors journal : `make mutants` sur les zones à risque",
        "  régressions           hors journal : étage 3 de la CI, après fusion",
        "",
        "  par tâche",
    ]
    for task in tasks:
        runs = [entry for entry in entries if entry.get("task_id") == task]
        ok = sum(1 for entry in runs if entry.get("status") == VERIFIED)
        seconds = sum(float(entry.get("seconds") or 0) for entry in runs)
        lines.append(
            f"    {task:<14}{len(runs):>3} jugement(s){ok:>4} vérifié(s){round(seconds):>9} s"
        )
    lines += [
        "",
        "  ✓ vérifié · ✗ échec ou budget épuisé · · contrat refusé · ~ rien à juger",
        "  Un jugement isolé ne prouve rien : on suit la tendance (.ephor/docs/09).",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Indicateurs d'Ephor, d'après les jugements.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--task", help="ne regarder qu'une tâche")
    args = parser.parse_args(argv)
    print(report(read_journal(args.repo.resolve(), args.task)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
