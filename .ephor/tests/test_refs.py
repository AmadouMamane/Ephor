"""Dépôts de référence : ce qu'on déclare, ce qu'on lit, ce qu'on refuse d'écrire."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import EPHOR, ROOT, git

sys.path.insert(0, str(EPHOR))

import refs

HOOK = ROOT / ".ephor" / "hooks" / "refs-guard.sh"


def declare(repo: Path, depots: list[dict[str, str]]) -> None:
    (repo / refs.CONFIG).parent.mkdir(parents=True, exist_ok=True)
    (repo / refs.CONFIG).write_text(json.dumps({"depots": depots}), encoding="utf-8")


def make_repo(path: Path, fichier: str = "schema.py") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / fichier).write_text("class Message:\n    def valider(self): ...\n", encoding="utf-8")
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.email", "test@example.invalid")
    git(path, "config", "user.name", "Ephor test")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "schéma")
    return path


def test_nothing_declared_is_not_an_error(tmp_path: Path) -> None:
    assert refs.load(tmp_path) == []
    assert "aucun dépôt de référence" in refs.listing(tmp_path)


def test_a_declared_repository_carries_its_commit(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "producteurs")
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "producteurs", "chemin": str(source), "pourquoi": "les messages"}])
    (ref,) = refs.load(travail)
    assert ref.nom == "producteurs"
    assert len(refs.state(ref)) == 7  # commit court, dépôt propre
    (source / "schema.py").write_text("# modifié\n", encoding="utf-8")
    assert "modifié" in refs.state(ref)


def test_the_home_and_the_environment_are_expanded(tmp_path: Path, monkeypatch: Any) -> None:
    source = make_repo(tmp_path / "transporteurs")
    monkeypatch.setenv("DEPOTS_EQUIPE", str(tmp_path))
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "transporteurs", "chemin": "${DEPOTS_EQUIPE}/transporteurs"}])
    assert refs.load(travail)[0].chemin == source.resolve()


@pytest.mark.parametrize(
    ("depot", "message"),
    [
        ({"nom": "Producteurs", "chemin": "/tmp/x"}, "nom invalide"),
        ({"nom": "producteurs"}, "chemin manquant"),
        ({"nom": "producteurs", "chemin": "../voisin"}, "absolu"),
    ],
)
def test_a_bad_declaration_is_refused(tmp_path: Path, depot: dict[str, str], message: str) -> None:
    declare(tmp_path, [depot])
    with pytest.raises(refs.RefsError, match=message):
        refs.load(tmp_path)


def test_a_reference_cannot_be_the_work_repository(tmp_path: Path) -> None:
    """Se déclarer soi-même comme référence rendrait tout le dépôt intouchable... ou pas."""
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "soi", "chemin": str(travail)}])
    with pytest.raises(refs.RefsError, match="dépôt de travail"):
        refs.load(travail)
    declare(travail, [{"nom": "dedans", "chemin": str(travail / "src")}])
    with pytest.raises(refs.RefsError, match="dépôt de travail"):
        refs.load(travail)


def test_a_duplicate_name_is_refused(tmp_path: Path) -> None:
    declare(tmp_path, [{"nom": "a", "chemin": "/tmp/un"}, {"nom": "a", "chemin": "/tmp/deux"}])
    with pytest.raises(refs.RefsError, match="deux fois"):
        refs.load(tmp_path)


def test_it_knows_what_falls_inside_a_reference(tmp_path: Path) -> None:
    source = make_repo(tmp_path / "producteurs")
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "producteurs", "chemin": str(source)}])
    chargés = refs.load(travail)
    assert refs.inside(chargés, source / "schemas" / "message.py") is not None
    assert refs.inside(chargés, source) is not None
    assert refs.inside(chargés, tmp_path / "ailleurs" / "x.py") is None


def test_the_map_of_an_unknown_repository_says_which_ones_exist(tmp_path: Path) -> None:
    declare(tmp_path, [])
    assert refs.main(["--repo", str(tmp_path), "carte", "--nom", "inconnu"]) == 2


def test_la_carte_d_un_depot_de_reference_n_ecrit_rien_chez_lui(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`make map REPO=<nom>` rangeait son cache dans le `.verify/` du dépôt de référence :
    un fichier y apparaissait, et `make refs` le disait ensuite « (modifié) » (audit du
    01/10/2026). Il se range chez le dépôt de travail."""
    source = make_repo(tmp_path / "producteurs")
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "producteurs", "chemin": str(source), "pourquoi": "les messages"}])
    assert refs.main(["--repo", str(travail), "carte", "--nom", "producteurs"]) == 0
    assert "class Message" in capsys.readouterr().out
    assert git(source, "status", "--porcelain", "--ignored") == "", "rien d'écrit chez la référence"
    assert list((travail / ".verify" / "map").rglob("*.txt")), "le cache vit chez le travail"


def test_a_detour_designates_the_same_place(tmp_path: Path) -> None:
    """Lien symbolique, `..`, `~`, casse : quatre façons d'écrire le même chemin."""
    source = make_repo(tmp_path / "amont")
    travail = tmp_path / "travail"
    travail.mkdir()
    declare(travail, [{"nom": "amont", "chemin": str(source)}])
    chargés = refs.load(travail)
    raccourci = travail / "raccourci"
    raccourci.symlink_to(source)
    assert refs.inside(chargés, raccourci / "schema.py") is not None
    assert refs.inside(chargés, source / ".." / "amont" / "schema.py") is not None
    assert refs.inside(chargés, Path("../amont/schema.py"), travail) is not None
    if refs.CASSE_IGNOREE:
        assert refs.inside(chargés, Path(str(source).upper()) / "schema.py") is not None


def test_an_impossible_path_is_refused_not_a_crash(tmp_path: Path) -> None:
    declare(tmp_path, [{"nom": "amont", "chemin": "/tmp/pas\x00possible"}])
    with pytest.raises(refs.RefsError, match="interdit"):
        refs.load(tmp_path)
    # Le juge appelle commits() : il doit apprendre le problème, pas tomber avec.
    assert "__erreur__" in refs.commits(tmp_path)


@pytest.mark.skipif(os.name == "nt", reason="hooks bash")
def test_the_guard_refuses_to_write_into_a_reference(tmp_path: Path) -> None:
    """Le juge ne verra jamais ce fichier : s'il n'est pas refusé ici, il ne l'est nulle part."""
    source = make_repo(tmp_path / "producteurs")
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "producteurs", "chemin": str(source)}])
    for module in ("refs.py", "repo_map.py"):
        (travail / ".ephor" / module).write_text(
            (EPHOR / module).read_text(encoding="utf-8"), encoding="utf-8"
        )

    def ecrire(cible: Path) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, CLAUDE_PROJECT_DIR=str(travail))
        return subprocess.run(
            [str(HOOK)],
            input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(cible)}}),
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
            check=False,
        )

    refuse = ecrire(source / "schema.py")
    assert refuse.returncode == 2
    assert "lecture seule" in refuse.stderr
    assert ecrire(travail / "src" / "moteur.py").returncode == 0

    # Les détours désignent le même fichier : ils doivent recevoir la même réponse.
    raccourci = travail / "raccourci"
    raccourci.symlink_to(source)
    assert ecrire(raccourci / "schema.py").returncode == 2
    assert ecrire(source / ".." / source.name / "schema.py").returncode == 2

    # Un autre outil, un autre champ : le nom de l'outil ne décide de rien.
    autre = subprocess.run(
        [str(HOOK)],
        input=json.dumps({"tool_name": "mcp__fs__write", "tool_input": {"path": str(source)}}),
        capture_output=True,
        text=True,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(travail)),
        timeout=30,
        check=False,
    )
    assert autre.returncode == 2

    # Déclaration illisible : ce qui sort du dépôt est refusé, jamais en silence, avec la
    # raison et la consigne de ne pas contourner.
    (travail / refs.CONFIG).write_text("{pas du json", encoding="utf-8")
    dehors = ecrire(tmp_path / "ailleurs" / "x.py")
    assert dehors.returncode == 2
    assert "inutilisable" in dehors.stderr and "illisible" in dehors.stderr
    assert "Ne contourne pas" in dehors.stderr
    assert ecrire(raccourci / "schema.py").returncode == 2, "un lien qui sort du dépôt en sort"
    # Mais le travail DANS le dépôt continue : aucune référence ne peut s'y trouver. Le
    # 24/09/2026, une déclaration cassée bloquait tout, et l'agent est passé par le terminal.
    assert ecrire(travail / "src" / "moteur.py").returncode == 0


@pytest.mark.skipif(os.name == "nt", reason="hooks bash")
def test_a_reference_is_read_with_the_copilot_panel_tools(tmp_path: Path) -> None:
    """Le panneau de Copilot lit avec `read_file` : ce nom manquait, et une lecture était
    jugée comme une écriture — refusée dans un dépôt de référence, fait pour être lu."""
    source = make_repo(tmp_path / "producteurs")
    travail = tmp_path / "travail"
    declare(travail, [{"nom": "producteurs", "chemin": str(source)}])
    (travail / ".ephor" / "refs.py").write_text(
        (EPHOR / "refs.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    for outil, args in (
        ("read_file", {"filePath": str(source / "schema.py")}),
        ("list_dir", {"path": str(source)}),
    ):
        lecture = subprocess.run(
            [str(HOOK)],
            input=json.dumps(
                {"toolName": outil, "toolArgs": args, "workingDirectory": str(travail)}
            ),
            capture_output=True,
            text=True,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(travail)),
            timeout=30,
            check=False,
        )
        assert lecture.returncode == 0, f"{outil} : {lecture.stderr}"


def test_an_unset_variable_is_named(tmp_path: Path, monkeypatch: Any) -> None:
    """Sans variable, le chemin restait « ${X}/y » et le message parlait d'un chemin pas
    absolu : personne ne comprenait qu'il fallait exporter une variable."""
    monkeypatch.delenv("DEPOTS_ABSENTS", raising=False)
    declare(tmp_path, [{"nom": "metrics", "chemin": "${DEPOTS_ABSENTS}/metrics"}])
    with pytest.raises(refs.RefsError, match="DEPOTS_ABSENTS") as erreur:
        refs.load(tmp_path)
    assert "non définie" in str(erreur.value)


def test_une_reference_dans_un_sous_dossier_de_depot_a_son_commit(tmp_path: Path) -> None:
    """Une référence déclarée dans un sous-dossier d'un monorepo se disait « dossier sans dépôt
    git », et le juge n'en notait aucun commit (audit du 01/10/2026)."""
    mono = make_repo(tmp_path / "mono", "racine.py")
    (mono / "producteurs").mkdir()
    (mono / "producteurs" / "schema.py").write_text("class Message: ...\n", encoding="utf-8")
    git(mono, "add", "-A")
    git(mono, "commit", "-qm", "sous-dossier")
    ref = refs.Ref("producteurs", mono / "producteurs", "")
    assert len(refs.state(ref)) == 7, refs.state(ref)
    (mono / "racine.py").write_text("# ailleurs\n", encoding="utf-8")
    assert refs.state(ref).endswith(")") is False, "modifié hors du sous-dossier : pas lui"
    (mono / "producteurs" / "schema.py").write_text("# modifié\n", encoding="utf-8")
    assert "modifié" in refs.state(ref)


def test_la_liste_separe_l_etat_du_chemin(tmp_path: Path) -> None:
    """Un état de vingt-deux caractères (« dossier sans dépôt git ») se collait au chemin."""
    repo = make_repo(tmp_path / "travail")
    sans_git = tmp_path / "sans-git"
    sans_git.mkdir()
    declare(repo, [{"nom": "sans-git", "chemin": str(sans_git), "pourquoi": "voir"}])
    lignes = refs.listing(repo).splitlines()
    ligne = next(x for x in lignes if "sans-git" in x and "voir" not in x)
    assert "dossier sans dépôt git  " + str(sans_git) in ligne


def test_une_sortie_fermee_par_head_n_est_pas_une_erreur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make map REPO=x | head` : « erreur : [Errno 32] Broken pipe », code 2."""
    repo = make_repo(tmp_path / "travail")

    class Fermee:
        def write(self, texte: str) -> int:
            raise BrokenPipeError(32, "Broken pipe")

        def flush(self) -> None:
            return None

    monkeypatch.setattr(sys, "stdout", Fermee())
    assert refs.main(["--repo", str(repo), "liste"]) == 0
