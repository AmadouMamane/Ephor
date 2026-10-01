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
from pathlib import Path
from typing import Any

import usage  # même dossier : les deux modules mesurent, autant partager leurs aides


def entier(valeur: Any) -> int:
    """Un compte qui n'en est pas un vaut zéro : un journal abîmé ne fausse pas le total."""
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else 0


def duree(valeur: Any) -> float:
    """Une durée lue dans le journal, zéro si ce n'en est pas une : `"12,5"`, écrit à la main
    ou par un autre outil, faisait planter `make score` (audit du 01/10/2026)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return 0.0
    return float(valeur)


VERIFIED = "verified_needs_review"
# Tout n'est pas un échec de l'agent : un contrat refusé et un dépôt inchangé disent
# autre chose, et les confondre fausserait la tendance qu'on regarde.
SYMBOLS = {VERIFIED: "✓", "rejected": "·", "no_change": "~", "stale": "~"}
ASIDE = {"rejected": "refusé(s)", "no_change": "sans changement", "stale": "périmé(s)"}


def read_journal(repo: Path, task: str | None = None) -> list[dict[str, Any]]:
    """Les jugements du dépôt, tous ses dossiers de travail compris : une tâche se juge dans
    le sien (#32), et le dossier principal ne voyait aucun de ses verdicts. Une ligne abîmée
    ne masque pas les autres."""
    return usage.journal_des_jugements(repo, task)


def tache(entry: dict[str, Any]) -> str:
    """L'identifiant de la tâche, toujours en texte : un journal abîmé ne la dédouble pas."""
    return str(entry.get("task_id", "?"))


def percent(part: int, whole_count: int) -> str:
    return f"{100 * part // whole_count} %" if whole_count else "—"


def cost_per_criterion(entries: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Rend (tokens, critères satisfaits, tâches mesurées).

    La mesure de tokens est cumulée par tâche, pas par exécution : on ne retient donc
    que le dernier jugement de chaque tâche, sinon la même dépense serait comptée deux fois.
    """
    last: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if entier(entry.get("billable_tokens")):
            last[tache(entry)] = entry
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
    # Une tâche compte une fois, pour son DERNIER jugement : c'est le débit, pas l'activité
    # (.ephor/docs/09). Compter les jugements faisait peser comme trois tâches une tâche
    # rejugée trois fois, et baisser le taux à chaque essai d'un agent qui finit par réussir.
    dernier: dict[str, dict[str, Any]] = {}
    for entry in entries:
        dernier[tache(entry)] = entry
    tasks = sorted(dernier)
    finals = list(dernier.values())
    verified = sum(1 for entry in finals if entry.get("status") == VERIFIED)
    criteria = sum(entier(entry.get("criteria_total")) for entry in finals)
    passed = sum(entier(entry.get("criteria_passed")) for entry in finals)
    tokens, billed_passed, measured = cost_per_criterion(entries)
    trend = "".join(SYMBOLS.get(str(entry.get("status")), "✗") for entry in entries[-12:])
    since = str(entries[0].get("finished_at") or "?")[:10]

    lines = [
        f"score d'Ephor · {len(entries)} jugement(s) sur {len(tasks)} tâche(s) · depuis {since}",
        "",
        f"  tâches vérifiées      {verified:>3} / {len(tasks):<3}"
        f"  {percent(verified, len(tasks)):<6}  (dernier jugement de chacune)",
        f"  critères satisfaits   {passed:>3} / {criteria:<3}  {percent(passed, criteria)}",
        f"  jugements             {len(entries):>3}        {trend}",
    ]
    aside = [
        f"{sum(1 for entry in finals if entry.get('status') == status)} {label}"
        for status, label in ASIDE.items()
        if any(entry.get("status") == status for entry in finals)
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
    # Le niveau d'implémentation du contrat (#24) : ce que chacun coûte, sur de vraies tâches,
    # avant d'en faire une règle. Une tâche compte au niveau de son dernier jugement.
    par_niveau: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        niveau = dernier[tache(entry)].get("niveau")
        if niveau:
            par_niveau.setdefault(str(niveau), []).append(entry)
    if par_niveau:
        lines.append("  par niveau d'implémentation")
        for niveau, groupe in sorted(par_niveau.items()):
            jetons, reussis, _ = cost_per_criterion(groupe)
            taches = {tache(entry) for entry in groupe}
            ok = sum(1 for nom in taches if dernier[nom].get("status") == VERIFIED)
            cout = (
                f"{usage.milliers(jetons // reussis)} tokens par critère"
                if reussis
                else "coût pas encore mesuré"
            )
            lines.append(f"    {niveau:<9}{ok:>3} / {len(taches):<3} vérifiée(s) · {cout}")
    lines += [
        "  score de mutation     hors journal : `make mutants` sur les zones à risque",
        "  régressions           hors journal : étage 3 de la CI, après fusion",
        "",
        "  par tâche",
    ]
    for task in tasks:
        runs = [entry for entry in entries if tache(entry) == task]
        ok = sum(1 for entry in runs if entry.get("status") == VERIFIED)
        seconds = sum(duree(entry.get("seconds")) for entry in runs)
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
