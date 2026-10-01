#!/usr/bin/env python3
"""Dépôts de référence : ceux qu'on LIT pour travailler, et qu'on n'écrit jamais.

Un projet a souvent besoin de lire des dépôts voisins — le schéma des messages qu'il
consomme, un référentiel partagé — sans avoir le droit d'y toucher. Ces dépôts sont
déclarés dans `.ephor/projet/reference-repos.json`, que l'agent ne peut pas modifier : il
ne peut donc pas s'ajouter une source de lecture, ni transformer une lecture en écriture.

Trois usages :
  - `make refs`                  dire ce qui est déclaré, et où ça en est
  - `make map REPO=<nom>`        la forme d'un dépôt de référence, pour ~300 tokens
  - le garde-périmètre s'en sert pour refuser toute écriture qui y atterrirait

Le juge, lui, ne les regarde pas : il vérifie le dépôt de travail, hors ligne, tout seul.
Il note seulement le commit de chaque référence, pour qu'un verdict reste reproductible.

Bibliothèque standard uniquement, et syntaxe Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

CONFIG = ".ephor/projet/reference-repos.json"
NAME = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
# Ce que `os.path.expandvars` laisse quand la variable n'existe pas : `$NOM` ou `${NOM}`.
VARIABLE = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)")
# Un dépôt de référence est un dossier qu'on n'a pas écrit : git y tourne bridé, et borné.
GIT = ("git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-C")
DELAI = 10
# macOS et Windows ignorent la casse : comparer à la lettre près y laisserait passer
# `/Users/x/Depot` pour `/Users/x/depot`. On refuse dans le doute, jamais l'inverse.
CASSE_IGNOREE = sys.platform in ("darwin", "win32")


@dataclass(frozen=True)
class Ref:
    nom: str
    chemin: Path
    pourquoi: str


class RefsError(Exception):
    """Déclaration inutilisable : on préfère refuser que lire n'importe quoi."""


def resolve(raw: str) -> Path:
    """`~/dev/x` et `${DEPOTS}/x` sont acceptés : la config est partagée, pas les postes."""
    return Path(os.path.expandvars(raw)).expanduser()


def load(repo: Path) -> list[Ref]:
    path = repo / CONFIG
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise RefsError(f"{CONFIG} illisible : {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("depots"), list):
        raise RefsError(f"{CONFIG} doit contenir une liste « depots »")
    refs: list[Ref] = []
    seen: set[str] = set()
    root = repo.resolve()
    for index, item in enumerate(data["depots"], 1):
        if not isinstance(item, dict):
            raise RefsError(f"dépôt n°{index} : objet attendu")
        nom, chemin = item.get("nom"), item.get("chemin")
        if not isinstance(nom, str) or not NAME.match(nom):
            raise RefsError(f"dépôt n°{index} : nom invalide (minuscules, chiffres, tirets)")
        if nom in seen:
            raise RefsError(f"dépôt {nom} déclaré deux fois")
        seen.add(nom)
        if not isinstance(chemin, str) or not chemin.strip():
            raise RefsError(f"dépôt {nom} : chemin manquant")
        if "\x00" in chemin or "\n" in chemin:
            raise RefsError(f"dépôt {nom} : caractère interdit dans le chemin")
        cible = resolve(chemin)
        # Une variable inconnue reste telle quelle dans le chemin : la nommer, sinon le
        # message parle d'un chemin « pas absolu » et personne ne comprend (vu le 24/09/2026).
        manquantes = sorted(set(VARIABLE.findall(str(cible))))
        if manquantes:
            raise RefsError(
                f"dépôt {nom} : variable d'environnement non définie sur ce poste"
                f" ({', '.join(manquantes)}) dans « {chemin} » — exportez-la"
                " (~/.zshrc), puis relancez l'éditeur"
            )
        if not cible.is_absolute():
            raise RefsError(f"dépôt {nom} : le chemin doit être absolu ({chemin})")
        # Un « dépôt de référence » qui est le dépôt de travail, ou dedans, ne protège rien.
        try:
            resolved = cible.resolve()
        except (OSError, ValueError) as exc:
            raise RefsError(f"dépôt {nom} : chemin impossible à résoudre ({exc})") from exc
        if resolved == root or root in resolved.parents or resolved in root.parents:
            raise RefsError(f"dépôt {nom} : ne peut pas contenir ni être le dépôt de travail")
        refs.append(Ref(nom, resolved, str(item.get("pourquoi") or "")))
    return refs


def state(ref: Ref) -> str:
    """Où en est ce dépôt : commit court, et s'il a des modifications non commitées."""
    if not ref.chemin.is_dir():
        return "ABSENT de ce poste"
    if not (ref.chemin / ".git").exists():
        return "dossier sans dépôt git"
    try:
        commit = subprocess.run(
            [*GIT, str(ref.chemin), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DELAI,
        ).stdout.strip()
        sale = subprocess.run(
            [*GIT, str(ref.chemin), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=DELAI,
        ).stdout.strip()
    except (OSError, ValueError, subprocess.SubprocessError):
        return "git muet"
    return f"{commit}{' (modifié)' if sale else ''}"


def normalise(path: Path) -> str:
    texte = str(path)
    return texte.casefold() if CASSE_IGNOREE else texte


def dans_le_depot(target: Path, repo: Path) -> bool:
    """Le chemin visé, une fois résolu, est-il dans le dépôt de travail ?

    Si oui, il ne peut tomber dans aucun dépôt de référence : `load` refuse une référence
    qui est le dépôt de travail, qui est dedans, ou qui le contient. Le garde-fou n'a donc
    pas besoin de la déclaration pour lui répondre — et une déclaration cassée ne bloque
    plus tout le travail (vu le 24/09/2026 : l'agent, privé de ses outils de fichiers,
    est passé par le terminal). La résolution suit les liens : un lien posé dans le dépôt
    qui mène ailleurs n'est pas « dans le dépôt ».
    """
    try:
        candidate = target.expanduser()
        if not candidate.is_absolute():
            candidate = repo / candidate
        vise = normalise(candidate.resolve())
        racine = normalise(repo.resolve())
    except (OSError, ValueError):
        return False
    return vise == racine or vise.startswith(racine.rstrip("/") + "/")


def inside(refs: list[Ref], target: Path, repo: Path | None = None) -> Ref | None:
    """Le chemin visé tombe-t-il dans un dépôt de référence ?

    On résout avant de comparer : un lien symbolique, un `..`, `~`, une casse différente
    ou `/tmp` au lieu de `/private/tmp` désignent le même fichier, et doivent donc donner
    la même réponse. Comparer des chaînes brutes, c'est laisser quatre portes ouvertes.
    """
    try:
        candidate = target.expanduser()
        if not candidate.is_absolute() and repo is not None:
            candidate = repo / candidate
        resolved = candidate.resolve()
    except (OSError, ValueError):
        return None
    vise = normalise(resolved)
    for ref in refs:
        racine = normalise(ref.chemin)
        if vise == racine or vise.startswith(racine.rstrip("/") + "/"):
            return ref
    return None


def commits(repo: Path) -> dict[str, str]:
    """Ce que le juge note dans son rapport : de quoi rejouer la tâche plus tard.

    Ne lève jamais : un rapport qui manque vaut mieux qu'un juge qui tombe, et une
    déclaration illisible est dite plutôt que tue.
    """
    try:
        return {ref.nom: state(ref) for ref in load(repo)}
    except RefsError as exc:
        return {"__erreur__": str(exc)}
    except (OSError, ValueError) as exc:
        return {"__erreur__": f"déclaration inutilisable : {exc}"}


def listing(repo: Path) -> str:
    refs = load(repo)
    if not refs:
        return (
            f"aucun dépôt de référence déclaré ({CONFIG}).\n"
            "Ce fichier dit quels dépôts on a le droit de LIRE pour travailler.\n"
            "Il est hors de portée de l'agent : c'est l'opérateur qui l'écrit."
        )
    lignes = [f"{len(refs)} dépôt(s) de référence · lecture seule, jamais d'écriture", ""]
    for ref in refs:
        lignes.append(f"  {ref.nom:<16}{state(ref):<22}{ref.chemin}")
        if ref.pourquoi:
            lignes.append(f"  {'':<16}{ref.pourquoi}")
    lignes += ["", "  carte d'un dépôt, pas chère en tokens : make map REPO=<nom>"]
    return "\n".join(lignes)


def cmd_map(repo: Path, nom: str, max_lines: int) -> int:
    refs = {ref.nom: ref for ref in load(repo)}
    if nom not in refs:
        connus = ", ".join(sorted(refs)) or "aucun"
        print(f"dépôt de référence inconnu : {nom} (déclarés : {connus})")
        return 2
    ref = refs[nom]
    if not ref.chemin.is_dir():
        print(f"{nom} : absent de ce poste ({ref.chemin})")
        return 2
    # Aucun cache compilé lu ni écrit dans `.ephor/` : voir `reference_state` dans judge.py.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = "/dev/null/ephor-sans-cache"
    import repo_map  # import local : refs.py sert aussi sans lui

    print(f"== {nom} · {state(ref)} · {ref.chemin} ==")
    # Le cache chez le dépôt de travail : rangé dans le `.verify/` de la référence, il y
    # laissait un fichier, que `make refs` disait ensuite « (modifié) » (audit du 01/10/2026).
    cache = repo / ".verify" / "map" / "refs" / nom
    return repo_map.main(
        ["--repo", str(ref.chemin), "--max-lines", str(max_lines), "--cache", str(cache)]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dépôts de référence, en lecture seule.")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest="commande", required=True)
    sub.add_parser("liste", help="ce qui est déclaré, et où ça en est")
    sub.add_parser("chemins", help="un chemin absolu par ligne (pour les garde-fous)")
    vise = sub.add_parser("vise", help="ce chemin tombe-t-il dans un dépôt de référence ?")
    vise.add_argument("--chemin", required=True)
    carte = sub.add_parser("carte", help="la forme d'un dépôt de référence")
    carte.add_argument("--nom", required=True)
    carte.add_argument("--max-lines", type=int, default=120)
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    try:
        if args.commande == "liste":
            print(listing(repo))
            return 0
        if args.commande == "chemins":
            for ref in load(repo):
                print(ref.chemin)
            return 0
        if args.commande == "vise":
            # 0 = dehors, 3 = dedans, 2 = on ne sait pas — et on ne sait pas vaut refus.
            if dans_le_depot(Path(args.chemin), repo):
                return 0
            depot = inside(load(repo), Path(args.chemin), repo)
            if depot is None:
                return 0
            print(depot.chemin)
            return 3
        return cmd_map(repo, args.nom, args.max_lines)
    except RefsError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
