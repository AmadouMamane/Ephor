#!/usr/bin/env python3
"""Carte des symboles Python — la forme du code pour quelques centaines de tokens.

Lire la syntaxe plutôt que deviner : une recherche par motif range les fonctions d'un
module sous la classe qui les précède et oublie les `async def`. Ici, l'arbre syntaxique
donne la vraie imbrication, et le résultat est mis en cache tant que rien ne bouge.

    python3 .ephor/repo_map.py [--repo .] [--max-lines 120]

Bibliothèque standard, Python 3.9, lecture seule : comme le juge.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import sys
from pathlib import Path

IGNORES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".verify",
    "mutants",
    "build",
    "dist",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "htmlcov",
}


def fichiers(repo: Path) -> list[Path]:
    trouves: list[Path] = []
    for dossier, sous, noms in os.walk(repo):
        sous[:] = sorted(nom for nom in sous if nom not in IGNORES and not nom.startswith("."))
        for nom in sorted(noms):
            # Un lien cassé (`x.py` vers un fichier disparu) faisait tomber toute la carte
            # sur une trace Python : il n'y a rien à lire, on passe.
            if nom.endswith(".py") and (Path(dossier) / nom).is_file():
                trouves.append(Path(dossier) / nom)
    return trouves


def empreinte(chemins: list[Path]) -> str:
    """Change dès qu'un fichier change : la carte n'est jamais servie périmée."""
    signature = hashlib.sha256()
    for chemin in chemins:
        etat = chemin.stat()
        signature.update(f"{chemin}:{etat.st_mtime_ns}:{etat.st_size}".encode())
    return signature.hexdigest()[:16]


def signature(noeud: ast.AST) -> str:
    if not isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return ""
    args = [a.arg for a in noeud.args.args if a.arg not in ("self", "cls")]
    if noeud.args.vararg:
        args.append("*" + noeud.args.vararg.arg)
    if noeud.args.kwarg:
        args.append("**" + noeud.args.kwarg.arg)
    prefixe = "async def" if isinstance(noeud, ast.AsyncFunctionDef) else "def"
    return f"{prefixe} {noeud.name}({', '.join(args)})"


def carte_fichier(chemin: Path, racine: Path) -> list[str]:
    try:
        arbre = ast.parse(chemin.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError, ValueError) as erreur:  # ValueError : un octet nul
        return [f"{chemin.relative_to(racine)} — illisible : {type(erreur).__name__}"]
    lignes: list[str] = []
    for noeud in arbre.body:  # le corps du module, donc le vrai niveau supérieur
        relatif = chemin.relative_to(racine)
        if isinstance(noeud, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in noeud.bases) if noeud.bases else ""
            entete = f"class {noeud.name}({bases})" if bases else f"class {noeud.name}"
            lignes.append(f"{relatif}:{noeud.lineno}  {entete}")
            methodes = [signature(m) for m in noeud.body if signature(m)]
            for methode in methodes:
                lignes.append(f"    {methode}")
        elif isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            lignes.append(f"{relatif}:{noeud.lineno}  {signature(noeud)}")
    return lignes


def carte(repo: Path, max_lignes: int, dossier_cache: Path | None = None) -> str:
    """La carte, depuis le cache quand le code n'a pas bougé. Le cache vit dans le dépôt de
    TRAVAIL : celui d'un dépôt de référence, lu seulement, y aurait laissé un fichier."""
    chemins = fichiers(repo)
    if not chemins:
        return "SKIP : aucun fichier Python, et la carte ne lit que le Python (rg --files, sinon)\n"
    dossier = dossier_cache or repo / ".verify" / "map"
    cache = dossier / f"{empreinte(chemins)}-{max_lignes}.txt"
    if cache.exists():
        return cache.read_text(encoding="utf-8")
    lignes: list[str] = []
    for chemin in chemins:
        lignes.extend(carte_fichier(chemin, repo))
    total = len(lignes)
    if total > max_lignes:
        lignes = lignes[:max_lignes]
        lignes.append(f"… {total - max_lignes} symboles de plus : affine avec rg")
    texte = "\n".join(lignes) + "\n"
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        for ancien in cache.parent.glob("*.txt"):  # une carte à la fois, jamais de rebut
            ancien.unlink()
        cache.write_text(texte, encoding="utf-8")
    except OSError:
        pass
    return texte


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description="Carte des symboles Python")
    analyseur.add_argument("--repo", type=Path, default=Path.cwd())
    analyseur.add_argument("--max-lines", type=int, default=120)
    analyseur.add_argument("--cache", type=Path, help="dossier du cache (<repo>/.verify/map)")
    options = analyseur.parse_args(argv)
    sys.stdout.write(carte(options.repo.resolve(), options.max_lines, options.cache))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
