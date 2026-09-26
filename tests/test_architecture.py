"""Tests STRUCTURELS : ils vérifient la forme du code, pas son comportement.

C'est la traduction en test des « invariants de goût ». Un agent peut argumenter
contre une consigne en prose ; il ne peut pas argumenter contre un test rouge.
Ajoute ici tout invariant que tu te surprends à rappeler plus d'une fois.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
MAX_FILE_LINES = 400


def _py_files() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_source_file_is_too_long() -> None:
    """Un fichier trop long est un fichier qu'un agent lit mal et modifie mal."""
    offenders = [
        (p.relative_to(SRC), len(p.read_text().splitlines()))
        for p in _py_files()
        if len(p.read_text().splitlines()) > MAX_FILE_LINES
    ]
    assert not offenders, f"fichiers > {MAX_FILE_LINES} lignes : {offenders}"


def test_no_print_in_source() -> None:
    """Tout log applicatif passe par providers.logger (observable, structuré, filtrable)."""
    offenders = [
        f"{p.relative_to(SRC)}:{i}"
        for p in _py_files()
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if line.lstrip().startswith("print(")
    ]
    assert not offenders, f"print() interdit hors UI : {offenders}"


def test_every_module_has_a_docstring() -> None:
    """Le docstring de module est ce que l'agent lit en premier. Il est obligatoire."""
    offenders = [
        str(p.relative_to(SRC))
        for p in _py_files()
        if not p.read_text().lstrip().startswith(('"""', "'''"))
    ]
    assert not offenders, f"modules sans docstring : {offenders}"


def test_dependency_direction_contract_holds() -> None:
    """Redondant avec `make arch`, volontairement : le contrat doit aussi casser en CI pytest."""
    result = subprocess.run(
        [sys.executable, "-m", "importlinter.cli", "lint"],
        capture_output=True,
        text=True,
        cwd=SRC.parent,
    )
    assert result.returncode == 0, result.stdout + result.stderr
