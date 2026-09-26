#!/usr/bin/env python3
"""Refuse qu'une donnée personnelle parte dans un journal ou dans un message d'erreur.

La règle existe depuis toujours dans `AGENTS.md` — aucune donnée client dans un log —
mais rien ne l'appliquait. Le garde-secrets attrape un mot de passe écrit en dur ; il ne
voit pas `logger.info(f"virement vers {client.iban}")`. Dans une banque, le bug coûte
cher, la fuite coûte le métier.

On lit l'arbre syntaxique, pas du texte : `client.iban` dans un f-string passé à
`logger.info` est vu, une variable nommée `iban_pattern` dans un commentaire ne l'est pas.

La liste des champs sensibles vit dans ce fichier, celle des exceptions dans
`.ephor/projet/fuites-autorisees.txt`. L'agent ne peut modifier ni l'un ni l'autre : il ne
peut donc pas s'autoriser lui-même.

Bibliothèque standard uniquement, et syntaxe Python 3.9.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
from pathlib import Path

# Appels qui finissent dans un journal, donc sur un disque, donc dans une sauvegarde.
LOG_CALLS = {
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "log",
    "print",
}
# Un seul mot suffit : ces termes ne désignent rien d'autre qu'une donnée personnelle.
SINGLE = {
    "iban",
    "bic",
    "rib",
    "pan",
    "cvv",
    "cvc",
    "nir",
    "ssn",
    "email",
    "courriel",
    "telephone",
    "phone",
    "mobile",
    "passeport",
    "passport",
}
# Deux mots voisins : « nom » ou « carte » seuls sont trop courants pour alerter.
PAIRS = {
    ("numero", "compte"),
    ("account", "number"),
    ("numero", "carte"),
    ("card", "number"),
    ("carte", "bancaire"),
    ("date", "naissance"),
    ("birth", "date"),
    ("nom", "client"),
    ("client", "name"),
    ("nom", "famille"),
    ("last", "name"),
    ("first", "name"),
    ("full", "name"),
    ("adresse", "postale"),
    ("postal", "address"),
    ("numero", "secu"),
    ("security", "number"),
}
# Mots qui disent « ce n'est pas la donnée » : un compte, un motif, une valeur masquée,
# un nom de colonne. Sans eux, `stats.email_count` et `IBAN_MOTIF` sonnent pour rien, et
# un contrôle qui sonne pour rien finit désactivé.
NEUTRES = {
    "count",
    "counts",
    "total",
    "totaux",
    "nombre",
    "nb",
    "len",
    "size",
    "taille",
    "longueur",
    "motif",
    "pattern",
    "regex",
    "regexp",
    "masque",
    "masked",
    "anonymise",
    "anonymized",
    "hash",
    "colonne",
    "column",
    "champ",
    "field",
    "type",
    "erreur",
    "error",
    "invalide",
}
# Les caches ne sont pas du code de projet. `.ephor-cache` compte double : en conteneur,
# le juge y range le cache d'uv, DANS le dossier monté — le même commit était donc VÉRIFIÉ
# en local et en ÉCHEC en conteneur, sur des « fuites » trouvées dans pip et packaging.
SKIPPED = {
    ".git",
    ".venv",
    "venv",
    ".verify",
    "mutants",
    "__pycache__",
    "node_modules",
    ".ephor-cache",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    "site-packages",
}
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def parts(name: str) -> list[str]:
    """Découpe un identifiant en mots : numeroCarteBancaire -> numero, carte, bancaire."""
    return [word for word in CAMEL.sub("_", name).lower().split("_") if word]


def sensitive(name: str) -> str | None:
    """Rend le champ sensible reconnu dans l'identifiant, ou None."""
    if name.isupper():
        return None  # une constante est un motif ou un libellé, jamais une donnée client
    words = parts(name)
    if any(word in NEUTRES for word in words):
        return None
    for word in words:
        if word in SINGLE:
            return word
    # Boucle indexée plutôt que `pairwise` ou `zip(strict=)` : tous deux sont en 3.10.
    for index in range(len(words) - 1):
        pair = (words[index], words[index + 1])
        if pair in PAIRS:
            return f"{pair[0]} {pair[1]}"
    return None


def is_log_call(node: ast.AST) -> str | None:
    """Rend le nom de l'appel s'il s'agit d'un journal ou d'une erreur levée."""
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in LOG_CALLS:
            return node.func.attr
        if isinstance(node.func, ast.Name) and node.func.id in LOG_CALLS:
            return node.func.id
    return None


def named(node: ast.AST) -> tuple[str, str]:
    """Ce qu'un nœud désigne : (ce qu'on lit dans le code, l'identifiant à juger)."""
    if isinstance(node, ast.Attribute):
        return f".{node.attr}", node.attr
    if isinstance(node, ast.Name):
        return node.id, node.id
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
        value = node.slice.value
        if isinstance(value, str):
            return f"[{value!r}]", value
    return "", ""


def first_sensitive(node: ast.AST) -> str | None:
    """Le premier champ sensible cité dans cette expression, s'il y en a un."""
    for inner in ast.walk(node):
        _, name = named(inner)
        if name:
            champ = sensitive(name)
            if champ is not None:
                return champ
    return None


def aliases(tree: ast.AST) -> dict[str, str]:
    """Variables qui portent une donnée personnelle : `msg = f"{client.iban}"`.

    Sans cela, une variable intermédiaire suffit à sortir n'importe quoi dans un journal,
    et c'est la forme la plus courante dans du vrai code. Ce n'est pas un suivi de flux
    complet : une affectation simple, à l'échelle du fichier, c'est déjà l'essentiel.
    """
    trouvees: dict[str, str] = {}
    for node in ast.walk(tree):
        cibles: list[ast.expr] = []
        valeur: ast.expr | None = None
        if isinstance(node, ast.Assign):
            cibles, valeur = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            cibles, valeur = [node.target], node.value
        if valeur is None:
            continue
        champ = first_sensitive(valeur)
        if champ is None:
            continue
        for cible in cibles:
            if isinstance(cible, ast.Name):
                trouvees[cible.id] = champ
    return trouvees


def exposed(call: ast.Call, portees: dict[str, str]) -> list[tuple[int, str, str]]:
    """Champs sensibles cités dans les ARGUMENTS : (ligne, ce qui est écrit, le champ).

    On ne regarde jamais l'appelé lui-même : `raise EmailInvalide("format attendu")` parle
    d'un format, pas d'une adresse, et faire sonner un nom de classe rendrait l'outil
    insupportable sur du code qui parle de clients.
    """
    found: list[tuple[int, str, str]] = []
    morceaux: list[ast.AST] = [*call.args, *call.keywords]
    for morceau in morceaux:
        for node in ast.walk(morceau):
            # `"{iban}".format(iban=compte)` : le nom du paramètre dit déjà ce qui sort.
            if isinstance(node, ast.keyword) and node.arg:
                champ = sensitive(node.arg)
                if champ is not None:
                    found.append((getattr(node.value, "lineno", 0), f"{node.arg}=", champ))
                continue
            if isinstance(node, ast.Dict):
                for cle in node.keys:
                    if isinstance(cle, ast.Constant) and isinstance(cle.value, str):
                        champ = sensitive(cle.value)
                        if champ is not None:
                            found.append((cle.lineno, f"{cle.value!r}:", champ))
                continue
            label, name = named(node)
            if not name:
                continue
            champ = sensitive(name)
            if champ is None and isinstance(node, ast.Name):
                champ = portees.get(node.id)
                if champ is not None:
                    label = f"{node.id} (porte {champ})"
            if champ is not None:
                found.append((getattr(node, "lineno", 0), label, champ))
    return found


def scan_source(source: str | bytes) -> list[tuple[int, str]]:
    """Fuites trouvées dans un fichier : (ligne, ce qu'on reproche).

    En octets, l'encodage déclaré en tête (`# -*- coding: latin-1 -*-`) est respecté.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        # Un fichier qui ne compile pas est déjà un échec ailleurs. ValueError : un octet
        # nul, que Python refusait avant la 3.12 par une autre exception.
        return []
    portees = aliases(tree)
    findings: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        call: ast.Call | None = None
        kind = ""
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            call, kind = node.exc, "erreur levée"
        elif isinstance(node, ast.Call):
            name = is_log_call(node)
            if name is not None:
                call, kind = node, f"{name}()"
        if call is None:
            continue
        for line, label, champ in exposed(call, portees):
            if (line, label) in seen:
                continue
            seen.add((line, label))
            findings.append((line, f"{kind} expose {label} ({champ})"))
    return sorted(findings)


def allowed(repo: Path) -> set[str]:
    """Exceptions assumées, dans un fichier que l'agent ne peut pas modifier."""
    path = repo / ".ephor" / "projet" / "fuites-autorisees.txt"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {line.strip() for line in lines if line.strip() and not line.startswith("#")}


def fichiers_python(repo: Path) -> list[Path]:
    """Les `.py` du dépôt, sans descendre dans ce qui est sauté.

    On juge le chemin DANS le dépôt : jugé sur le chemin entier, un dépôt rangé sous un
    dossier nommé `venv` ou `mutants` était sauté tout entier, et le contrôle disait « aucune
    donnée personnelle ». Et on ne descend pas dans `.venv/` pour en jeter le contenu après.
    """
    trouves: list[Path] = []
    for dossier, sous, noms in os.walk(repo):
        ici = Path(dossier)
        sous[:] = sorted(
            nom for nom in sous if nom not in SKIPPED and not (ici == repo and nom == ".ephor")
        )
        trouves.extend(ici / nom for nom in sorted(noms) if nom.endswith(".py"))
    return trouves


def scan_repo(repo: Path) -> list[str]:
    """Tout le dépôt, sauf `.ephor/` (l'outil ne se juge pas lui-même) et les exceptions."""
    waived = allowed(repo)
    findings: list[str] = []
    for file in fichiers_python(repo):
        relative = file.relative_to(repo).as_posix()
        if relative in waived:
            continue
        # En octets : lu en UTF-8, un vieux fichier en Latin-1 faisait tomber tout le
        # contrôle, et `make verify` rougissait sur une trace Python (vu le 25/09/2026).
        try:
            source = file.read_bytes()
        except OSError:
            continue
        for line, message in scan_source(source):
            if f"{relative}:{line}" in waived:
                continue
            findings.append(f"{relative}:{line}: {message}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    findings = scan_repo(args.repo.resolve())
    if not findings:
        print("aucune donnée personnelle dans les journaux")
        return 0
    for finding in findings:
        print(finding)
    print(
        f"\n{len(findings)} fuite(s) possible(s). Une donnée client n'entre jamais dans un\n"
        "journal ni dans un message d'erreur : journalise un identifiant technique à la\n"
        "place. Faux positif ? Dis-le à l'opérateur : la liste d'exceptions est dans\n"
        ".ephor/projet/fuites-autorisees.txt, hors de ta portée."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
