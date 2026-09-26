"""Carte des symboles : la vraie imbrication, et rien d'inventé."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import repo_map
from conftest import ROOT

MODULE = '''
"""Module d'essai."""


class Compte:
    def solde(self, devise):
        return 0

    async def recharge(self, montant):
        return montant


def hors_classe(x, *args, **kwargs):
    def interne():
        return 1

    return interne


async def lit_le_fichier(chemin):
    return chemin
'''


def test_une_fonction_de_module_n_est_pas_une_methode(tmp_path: Path) -> None:
    """Défaut classique d'analyse syntaxique : tout ce qui suit une classe devenait sa méthode."""
    (tmp_path / "compte.py").write_text(MODULE, encoding="utf-8")
    lignes = repo_map.carte(tmp_path, 100).splitlines()
    classe = [ligne for ligne in lignes if "class Compte" in ligne]
    methodes = [ligne for ligne in lignes if ligne.startswith("    ")]
    module = [ligne for ligne in lignes if "hors_classe" in ligne]
    assert len(classe) == 1
    assert [m.strip() for m in methodes] == [
        "def solde(devise)",
        "async def recharge(montant)",
    ], "les méthodes de la classe, et elles seules"
    assert module and not module[0].startswith("    "), "une fonction de module reste au bord"
    assert any("async def lit_le_fichier(chemin)" in ligne for ligne in lignes)
    assert not any("interne" in ligne for ligne in lignes), (
        "une fonction imbriquée n'est pas un repère"
    )


def test_les_arguments_et_les_lignes_sont_donnes(tmp_path: Path) -> None:
    (tmp_path / "compte.py").write_text(MODULE, encoding="utf-8")
    lignes = repo_map.carte(tmp_path, 100).splitlines()
    depart = next(ligne for ligne in lignes if "hors_classe" in ligne)
    assert depart.startswith("compte.py:")
    assert "hors_classe(x, *args, **kwargs)" in depart


def test_la_carte_est_mise_en_cache_puis_refaite_quand_le_code_bouge(tmp_path: Path) -> None:
    fichier = tmp_path / "compte.py"
    fichier.write_text(MODULE, encoding="utf-8")
    premiere = repo_map.carte(tmp_path, 100)
    caches = list((tmp_path / ".verify" / "map").glob("*.txt"))
    assert len(caches) == 1 and caches[0].read_text(encoding="utf-8") == premiere
    fichier.write_text(MODULE + "\n\ndef ajoutee():\n    return 2\n", encoding="utf-8")
    seconde = repo_map.carte(tmp_path, 100)
    assert "ajoutee" in seconde
    assert len(list((tmp_path / ".verify" / "map").glob("*.txt"))) == 1, "pas de rebut de cache"


def test_un_fichier_illisible_ne_fait_pas_tomber_la_carte(tmp_path: Path) -> None:
    (tmp_path / "casse.py").write_text("def (:\n", encoding="utf-8")
    (tmp_path / "bon.py").write_text("def ok():\n    return 1\n", encoding="utf-8")
    texte = repo_map.carte(tmp_path, 100)
    assert "illisible" in texte and "def ok()" in texte


def test_la_carte_est_coupee_et_le_dit(tmp_path: Path) -> None:
    (tmp_path / "gros.py").write_text(
        "".join(f"def f{n}():\n    return {n}\n\n" for n in range(50)), encoding="utf-8"
    )
    lignes = repo_map.carte(tmp_path, 10).splitlines()
    assert len(lignes) == 11 and "de plus" in lignes[-1]


def test_l_arbre_ne_montre_que_le_code_du_projet(tmp_path: Path) -> None:
    """Sur un petit projet équipé, les dossiers cachés d'Ephor et des agents faisaient
    vingt-six lignes de carte sur quarante-deux, payées en tokens (mesuré le 25/09/2026)."""
    for dossier in ("mylib", ".ephor/hooks", ".claude/agents", ".opencode/plugins", ".github"):
        (tmp_path / dossier).mkdir(parents=True)
    (tmp_path / "mylib" / "__init__.py").write_text("def add(a, b):\n    return a + b\n")
    script = tmp_path / ".ephor" / "scripts" / "repo_map.sh"
    script.parent.mkdir(parents=True)
    shutil.copy(ROOT / ".ephor" / "scripts" / "repo_map.sh", script)
    sortie = subprocess.run(["bash", str(script)], capture_output=True, text=True, check=True)
    arbre = sortie.stdout.split("== code détecté")[0]
    assert "./mylib" in arbre
    assert "/." not in arbre, arbre


def test_un_lien_casse_ou_un_octet_nul_ne_fait_pas_tomber_la_carte(tmp_path: Path) -> None:
    (tmp_path / "ok.py").write_text("def a():\n    pass\n")
    (tmp_path / "casse.py").symlink_to(tmp_path / "disparu.py")
    (tmp_path / "nul.py").write_bytes(b"x = 1\x00\n")
    carte = repo_map.carte(tmp_path, 50)
    assert "ok.py:1  def a()" in carte
    assert "casse.py" not in carte
    assert "nul.py — illisible" in carte
