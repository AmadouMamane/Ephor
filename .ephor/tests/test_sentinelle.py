"""La sentinelle : une zone de contrôle qui change pendant un tour, par n'importe quel moyen."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, git
from test_hooks import hook, sans_jq


def depot_surveille(tmp_path: Path, ignore: str = "__pycache__/\n") -> Path:
    """Un dépôt équipé : réglages d'Ephor, Makefile, consigne, un peu de code, tout commité."""
    repo = tmp_path / "depot"
    (repo / ".claude").mkdir(parents=True)
    (repo / ".claude" / "settings.json").write_text(
        (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (repo / ".ephor" / "projet").mkdir(parents=True)
    (repo / ".ephor" / "projet" / "verify.sh").write_text("echo vert\n", encoding="utf-8")
    (repo / "Makefile").write_text("verify:\n\t@bash .ephor/projet/verify.sh\n")
    (repo / "AGENTS.md").write_text("# consignes\n")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("A = 1\n")
    (repo / ".gitignore").write_text(ignore)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "équipé")
    return repo


def debut(repo: Path, **charge: str) -> subprocess.CompletedProcess[str]:
    return hook("steer.sh", {"session_id": "s1", "prompt": "corrige", **charge}, project=repo)


def fin(repo: Path, **charge: str) -> subprocess.CompletedProcess[str]:
    return hook("checkpoint.sh", {"session_id": "s1", **charge}, project=repo)


def test_une_zone_changee_par_le_terminal_arrete_l_agent(tmp_path: Path) -> None:
    """Le 24/09/2026, un agent refusé est passé par le terminal. La sentinelle ne regarde
    pas le moyen : elle compare le résultat au début du tour."""
    repo = depot_surveille(tmp_path)
    assert debut(repo).returncode == 0
    (repo / "Makefile").write_text("verify:\n\t@true\n")  # comme un `python3 -c` l'aurait fait
    (repo / ".ephor" / "projet" / "verify.sh").unlink()
    (repo / ".ephor" / "porte.sh").write_text("echo\n")
    (repo / "src" / "a.py").write_text("A = 2\n")  # le travail normal, lui, ne compte pas
    resultat = fin(repo)
    assert resultat.returncode == 1, "l'outil doit MONTRER l'alerte"
    arret = (repo / "AGENT_STOP").read_text(encoding="utf-8")
    assert "Makefile (modifié)" in arret
    assert ".ephor/projet/verify.sh (supprimé)" in arret
    assert ".ephor/porte.sh (ajouté)" in arret
    assert "src/a.py" not in arret
    assert "ATTENTION" in resultat.stderr
    refus = hook("kill-switch.sh", {"tool_name": "Bash"}, project=repo)
    assert refus.returncode == 2 and "sentinelle" in refus.stderr
    points = git(repo, "for-each-ref", "refs/ephor/checkpoints")
    assert points.strip(), "le point de reprise est pris quand même"


def test_le_travail_normal_et_les_caches_ne_la_declenchent_pas(tmp_path: Path) -> None:
    """Dépôt Node : `__pycache__/` n'y est pas ignoré. Un cache posé dans `.ephor/` par un
    simple lancement de Python ferait crier la sentinelle pour rien."""
    repo = depot_surveille(tmp_path, ignore="node_modules/\n")
    debut(repo)
    (repo / "src" / "a.py").write_text("A = 3\n")
    (repo / "src" / "b.py").write_text("B = 1\n")
    (repo / ".ephor" / "__pycache__").mkdir()
    (repo / ".ephor" / "__pycache__" / "refs.cpython-311.pyc").write_bytes(b"\x00")
    resultat = fin(repo)
    assert resultat.returncode == 0, resultat.stderr
    assert not (repo / "AGENT_STOP").exists()


def test_une_retouche_de_l_operateur_entre_deux_tours_passe(tmp_path: Path) -> None:
    repo = depot_surveille(tmp_path)
    debut(repo)
    assert fin(repo).returncode == 0
    (repo / ".ephor" / "projet" / "verify.sh").write_text("echo adapté\n")  # l'opérateur
    debut(repo)  # le tour suivant commence : nouvelle photo
    assert fin(repo).returncode == 0
    assert not (repo / "AGENT_STOP").exists()


def test_l_alerte_n_est_donnee_qu_une_fois(tmp_path: Path) -> None:
    """Dans la vue « agents », hooks du dépôt et greffon tournent tous les deux."""
    repo = depot_surveille(tmp_path)
    debut(repo)
    (repo / "AGENTS.md").write_text("# autres consignes\n")
    assert fin(repo).returncode == 1
    assert fin(repo).returncode == 0
    assert (repo / "AGENT_STOP").read_text(encoding="utf-8").count("sentinelle") == 1


def test_un_commit_de_l_agent_ne_cache_pas_le_changement(tmp_path: Path) -> None:
    repo = depot_surveille(tmp_path)
    debut(repo)
    (repo / "Makefile").write_text("verify:\n\t@true\n")
    git(repo, "commit", "-qam", "ni vu ni connu")
    assert fin(repo).returncode == 1
    assert "Makefile (modifié)" in (repo / "AGENT_STOP").read_text(encoding="utf-8")


@pytest.mark.parametrize("sans", [False, True], ids=["jq", "python3"])
def test_la_forme_de_copilot_et_le_poste_sans_jq(tmp_path: Path, sans: bool) -> None:
    """Le greffon relaie la charge telle que Copilot l'écrit : `sessionId`, `cwd`. Il pose
    aussi le dépôt dans l'environnement, comme ici."""
    repo = depot_surveille(tmp_path)
    chemin = sans_jq(tmp_path) if sans else None
    for outil in ("mkdir", "mktemp", "awk", "wc", "rm"):
        vrai = subprocess.run(["which", outil], capture_output=True, text=True).stdout.strip()
        if chemin and vrai and not (Path(chemin) / outil).exists():
            (Path(chemin) / outil).symlink_to(vrai)
    charge = {"sessionId": "c1", "cwd": str(repo)}
    assert hook("steer.sh", {**charge, "prompt": "x"}, project=repo, path=chemin).returncode == 0
    (repo / "Makefile").write_text("verify:\n\t@true\n")
    fin_de_tour = {**charge, "stopReason": "end_turn"}
    resultat = hook("checkpoint.sh", fin_de_tour, project=repo, path=chemin)
    assert resultat.returncode == 1, resultat.stderr
    assert "Makefile (modifié)" in (repo / "AGENT_STOP").read_text(encoding="utf-8")


def test_la_photo_ne_parle_pas_a_l_agent(tmp_path: Path) -> None:
    """Ce que le hook de début de tour écrit sur sa sortie, l'agent le lit comme consigne."""
    repo = depot_surveille(tmp_path)
    assert debut(repo).stdout == ""


def depot_equipe(tmp_path: Path) -> Path:
    """Un dépôt où Ephor lui-même est commité : ses hooks, sa liste de zones, ses réglages."""
    repo = depot_surveille(tmp_path)
    shutil.copytree(ROOT / ".ephor" / "hooks", repo / ".ephor" / "hooks")
    shutil.copy(ROOT / ".ephor" / "zones.txt", repo / ".ephor" / "zones.txt")
    (repo / ".gitignore").write_text("__pycache__/\n.claude/settings.local.json\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "Ephor commité")
    return repo


def son_hook(repo: Path, nom: str, **charge: str) -> subprocess.CompletedProcess[str]:
    """Le hook DU dépôt, comme l'outil le lance."""
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(repo))
    return subprocess.run(
        [str(repo / ".ephor" / "hooks" / nom)],
        input=json.dumps({"session_id": "s1", **charge}),
        capture_output=True,
        text=True,
        env=env,
        cwd=repo,
        timeout=60,
        check=False,
    )


def sans_regles_edit(repo: Path) -> None:
    reglages = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    reglages["permissions"]["deny"] = [
        r for r in reglages["permissions"]["deny"] if not r.startswith("Edit(")
    ]
    (repo / ".claude" / "settings.json").write_text(json.dumps(reglages), encoding="utf-8")


def test_retirer_les_regles_n_aveugle_plus_la_sentinelle(tmp_path: Path) -> None:
    """Mesuré le 25/09/2026 : la sentinelle lisait ses zones dans .claude/settings.json. Un
    agent qui en retirait les règles, puis touchait au juge, ne déclenchait rien."""
    repo = depot_equipe(tmp_path)
    assert son_hook(repo, "steer.sh", prompt="x").returncode == 0
    sans_regles_edit(repo)
    (repo / ".ephor" / "hooks" / "kill-switch.sh").write_text("#!/bin/sh\nexit 0\n")
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    arret = (repo / "AGENT_STOP").read_text(encoding="utf-8")
    assert ".claude/settings.json (modifié)" in arret
    assert ".ephor/hooks/kill-switch.sh (modifié)" in arret


def test_une_zone_retiree_de_la_liste_reste_regardee(tmp_path: Path) -> None:
    repo = depot_equipe(tmp_path)
    son_hook(repo, "steer.sh", prompt="x")
    zones = repo / ".ephor" / "zones.txt"
    zones.write_text(zones.read_text().replace("Makefile\n", ""))
    (repo / "Makefile").write_text("verify:\n\t@true\n")
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    arret = (repo / "AGENT_STOP").read_text(encoding="utf-8")
    assert "Makefile (modifié)" in arret and ".ephor/zones.txt (modifié)" in arret


def test_une_sentinelle_reecrite_pendant_le_tour_est_jugee_par_celle_du_commit(
    tmp_path: Path,
) -> None:
    repo = depot_equipe(tmp_path)
    son_hook(repo, "steer.sh", prompt="x")
    (repo / ".ephor" / "hooks" / "sentinelle.sh").write_text("#!/bin/sh\nexit 0\n")
    (repo / "Makefile").write_text("verify:\n\t@true\n")
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    assert ".ephor/hooks/sentinelle.sh (modifié)" in (repo / "AGENT_STOP").read_text()


def test_les_fichiers_ignores_d_une_zone_sont_vus(tmp_path: Path) -> None:
    """`.git/info/exclude` suffisait à cacher un fichier nouveau dans une zone."""
    repo = depot_equipe(tmp_path)
    son_hook(repo, "steer.sh", prompt="x")
    (repo / ".git" / "info" / "exclude").write_text(".ephor/projet/sast.sh\n")
    (repo / ".ephor" / "projet" / "sast.sh").write_text("exit 0\n")
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    arret = (repo / "AGENT_STOP").read_text(encoding="utf-8")
    assert ".ephor/projet/sast.sh (ajouté)" in arret
    assert "dossier git" in arret


def test_les_reglages_locaux_qui_coupent_les_hooks_sont_vus(tmp_path: Path) -> None:
    """Ignoré par git, ce fichier échappe à toute revue. Claude Code l'écrit lui-même quand
    l'opérateur clique « ne plus demander » : ce clic-là ne doit pas arrêter l'agent."""
    repo = depot_equipe(tmp_path)
    local = repo / ".claude" / "settings.local.json"
    son_hook(repo, "steer.sh", prompt="x")
    local.write_text('{"permissions": {"allow": ["Bash(npm test)"]}}')
    assert son_hook(repo, "checkpoint.sh").returncode == 0
    son_hook(repo, "steer.sh", prompt="x")
    local.write_text('{"disableAllHooks": true}')
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    assert "settings.local.json : disableAllHooks" in (repo / "AGENT_STOP").read_text()


def test_la_configuration_git_changee_pendant_le_tour_est_vue(tmp_path: Path) -> None:
    repo = depot_equipe(tmp_path)
    son_hook(repo, "steer.sh", prompt="x")
    git(repo, "config", "core.fsmonitor", "/tmp/piege")
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    assert "dossier git" in (repo / "AGENT_STOP").read_text(encoding="utf-8")
    (repo / "AGENT_STOP").unlink()
    son_hook(repo, "steer.sh", prompt="x")
    git(repo, "switch", "-qc", "task/T-1")  # une branche n'est pas une configuration
    git(repo, "config", "branch.task/T-1.remote", "origin")
    assert son_hook(repo, "checkpoint.sh").returncode == 0


def test_un_tour_interrompu_est_controle_au_debut_du_suivant(tmp_path: Path) -> None:
    """Mesuré le 25/09/2026 (Claude Code 2.1.282) : un tour interrompu par Échap ne lance
    pas le hook de fin de tour. La photo suivante absorbait tout ce qu'il avait changé."""
    repo = depot_equipe(tmp_path)
    assert son_hook(repo, "steer.sh", prompt="x").returncode == 0
    (repo / "Makefile").write_text("verify:\n\t@true\n")
    # pas de fin de tour : l'opérateur a interrompu l'agent
    suivant = son_hook(repo, "steer.sh", prompt="on continue")
    assert suivant.returncode == 2, "la demande est refusée : l'opérateur doit le voir"
    assert "interrompu" in suivant.stderr and "Makefile (modifié)" in suivant.stderr
    assert (repo / "AGENT_STOP").exists()


def test_deux_debuts_de_tour_de_suite_ne_crient_pas(tmp_path: Path) -> None:
    """Dans la vue « agents », hooks du dépôt et greffon lancent tous deux le début de tour."""
    repo = depot_equipe(tmp_path)
    assert son_hook(repo, "steer.sh", prompt="x").returncode == 0
    assert son_hook(repo, "steer.sh", prompt="x").returncode == 0
    assert not (repo / "AGENT_STOP").exists()


def test_sans_session_connue_pas_de_controle_au_debut(tmp_path: Path) -> None:
    """opencode appelle le début de tour sans charge : rien ne dit que c'est le même agent."""
    repo = depot_equipe(tmp_path)
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(repo))
    lancer = [str(repo / ".ephor" / "hooks" / "steer.sh")]
    assert subprocess.run(lancer, input="", env=env, cwd=repo, check=False).returncode == 0
    (repo / "Makefile").write_text("verify:\n\t@true\n")
    assert subprocess.run(lancer, input="", env=env, cwd=repo, check=False).returncode == 0


def test_les_caches_des_outils_de_verification_ne_la_declenchent_pas(tmp_path: Path) -> None:
    """Depuis qu'elle voit les fichiers ignorés, un cache posé dans une zone par `make verify`
    (ruff rangeait le sien dans .ephor/.ruff_cache) la faisait crier à chaque vérification."""
    repo = depot_equipe(tmp_path)
    son_hook(repo, "steer.sh", prompt="x")
    for cache in (".ruff_cache", ".mypy_cache", ".pytest_cache", "__pycache__"):
        (repo / ".ephor" / cache).mkdir()
        (repo / ".ephor" / cache / "donnees").write_text("x")
    assert son_hook(repo, "checkpoint.sh").returncode == 0
    assert not (repo / "AGENT_STOP").exists()


def test_ruff_ne_range_plus_son_cache_dans_le_moteur() -> None:
    reglage = (ROOT / ".ephor" / "ruff.toml").read_text(encoding="utf-8")
    assert 'cache-dir = "../.ruff_cache"' in reglage


def test_couper_le_bac_a_sable_pendant_le_tour_est_vu(tmp_path: Path) -> None:
    repo = depot_equipe(tmp_path)
    son_hook(repo, "steer.sh", prompt="x")
    (repo / ".claude" / "settings.local.json").write_text('{"sandbox": {"enabled": false}}')
    assert son_hook(repo, "checkpoint.sh").returncode == 1
    assert "sandbox.enabled=false" in (repo / "AGENT_STOP").read_text(encoding="utf-8")
