"""Mettre Ephor à jour dans un projet déjà équipé : `--mettre-a-jour` (ADR 0007).

Avant, relancer l'installation depuis la nouvelle version déposait un `.ephor-new` à côté
de chaque fichier du moteur qui avait changé — des dizaines à reprendre à la main, alors
que la règle dit « `.ephor/` ne se modifie pas dans un projet, on le remplace ». Ces tests
tiennent la nouvelle promesse et ses limites : le moteur est remplacé là où git garde
l'ancien contenu exact, et nulle part ailleurs ; le reste du projet n'est pas touché.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT, bootstrap, depot_node, git

VERSION = re.compile(r"^Ephor \S+")


def version_actuelle() -> str:
    trouve = VERSION.match((ROOT / ".ephor" / "VERSION").read_text(encoding="utf-8"))
    assert trouve is not None
    return trouve.group(0)


@pytest.fixture(scope="module")
def copie_d_ephor(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Les fichiers suivis de la copie de travail d'Ephor — ce que contient le zip."""
    base = tmp_path_factory.mktemp("ephor")
    for rel in filter(None, git(ROOT, "ls-files", "-z").split("\0")):
        source = ROOT / rel
        if source.is_file():
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, base / rel)
    return base


@pytest.fixture
def version_suivante(copie_d_ephor: Path, tmp_path: Path) -> Path:
    """Une version suivante d'Ephor : la copie, avec ce qu'une vraie version change."""
    source = tmp_path / "ephor-suivante"
    shutil.copytree(copie_d_ephor, source)
    moteur = source / ".ephor"
    for rel in ("judge.py", "score.py", "usage.py", "greffon-copilot/hooks/relais.sh"):
        ajoute(moteur / rel, "# changé dans la version suivante\n")
    (moteur / "nouveau.py").write_text("# un module qui n'existait pas\n", encoding="utf-8")
    (moteur / "repo_map.py").unlink()
    texte = (moteur / "VERSION").read_text(encoding="utf-8")
    (moteur / "VERSION").write_text(VERSION.sub("Ephor 9.9.9", texte), encoding="utf-8")
    ajoute(
        source / "Makefile", "\nnouvelle-cible: ## une commande de la version suivante\n\t@true\n"
    )
    git(source, "init", "-q", "-b", "main")
    git(source, "add", "-A")
    git(source, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "v")
    return source


def ajoute(chemin: Path, ligne: str) -> None:
    with chemin.open("a", encoding="utf-8") as fichier:
        fichier.write(ligne)


def mettre_a_jour(cible: Path, source: Path, *options: str) -> str:
    script = source / ".ephor" / "scripts" / "bootstrap_existing_repo.sh"
    return subprocess.run(
        ["bash", str(script), *options, str(cible)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def projet_equipe(cible: Path, *, geste_0: bool = True) -> Path:
    """Un dépôt Node équipé avec la version en cours, geste 0 fait, le tout commité."""
    depot_node(cible)
    bootstrap(cible)
    if geste_0:
        for nom in ("Makefile", ".claude/settings.json"):
            (cible / f"{nom}.ephor-new").replace(cible / nom)
        subprocess.run(
            [sys.executable, ".ephor/adapters.py", "write"],
            cwd=cible,
            capture_output=True,
            check=True,
        )
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "Ephor posé")
    return cible


def test_le_moteur_intact_est_remplace_et_le_projet_n_est_pas_touche(
    tmp_path: Path, version_suivante: Path
) -> None:
    cible = projet_equipe(tmp_path / "noderepo")
    (cible / "PROGRESS.md").write_text("# PROGRESS\n\n- le journal du projet\n", encoding="utf-8")
    verify = cible / ".ephor" / "projet" / "verify.sh"
    verify.write_text("#!/usr/bin/env bash\necho les étapes du projet\n", encoding="utf-8")
    git(cible, "commit", "-qam", "le projet travaille")
    # Une retouche du moteur, pas commitée : elle ne doit pas se perdre.
    ajoute(cible / ".ephor" / "score.py", "# retouche locale\n")

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    moteur, neuf = cible / ".ephor", version_suivante / ".ephor"
    assert (moteur / "judge.py").read_bytes() == (neuf / "judge.py").read_bytes()
    assert not (moteur / "judge.py.ephor-new").exists(), "remplacé, pas déposé à côté"
    assert ".ephor/judge.py" in git(cible, "diff", "--name-only"), "l'ancien reste dans git"
    assert (moteur / "nouveau.py").exists()

    assert (moteur / "score.py").read_text(encoding="utf-8").endswith("# retouche locale\n")
    assert (moteur / "score.py.ephor-new").read_bytes() == (neuf / "score.py").read_bytes()
    assert ".ephor/score.py modifié depuis le dernier commit" in sortie

    assert (moteur / "repo_map.py").exists(), "rien n'est retiré sans qu'on le demande"
    assert "git rm .ephor/repo_map.py" in sortie

    assert verify.read_text(encoding="utf-8").endswith("les étapes du projet\n")
    assert (cible / "PROGRESS.md").read_text(encoding="utf-8").endswith("journal du projet\n")
    assert not (cible / "PROGRESS.md.ephor-new").exists(), "le journal n'a rien à fusionner"
    assert not (cible / "STEER.md.ephor-new").exists()

    assert f"{version_actuelle()} -> Ephor 9.9.9" in sortie
    assert "4 fichier(s) du moteur remplacé(s)" in sortie
    assert "make copilot-install" in sortie, "le greffon de chaque poste est une copie"
    touches = [ligne for ligne in sortie.splitlines() if "fichiers suivis modifiés" in ligne]
    assert not any(".ephor/" in ligne for ligne in touches), "le moteur a sa propre ligne"


def test_une_retouche_cachee_a_git_n_est_pas_ecrasee(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Trouvé en relecture : un fichier marqué skip-worktree paraît intact à `git diff`.
    Il était remplacé, et sa retouche n'était plus nulle part — ni sur le disque, ni dans
    git. Idem pour un lien symbolique suivi : c'est sa cible, hors de git, qui était
    écrasée. Et un fichier en lecture seule arrêtait la mise à jour à mi-chemin."""
    cible = projet_equipe(tmp_path / "noderepo")
    partage = tmp_path / "partage.py"
    partage.write_text("# le module partagé\n", encoding="utf-8")
    (cible / ".ephor" / "score.py").unlink()
    (cible / ".ephor" / "score.py").symlink_to(partage)
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "un lien vers un module partagé")
    ajoute(cible / ".ephor" / "judge.py", "# retouche cachée\n")
    git(cible, "update-index", "--skip-worktree", ".ephor/judge.py")
    (cible / ".ephor" / "usage.py").chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    juge = (cible / ".ephor" / "judge.py").read_text(encoding="utf-8")
    assert juge.endswith("# retouche cachée\n")
    assert (cible / ".ephor" / "judge.py.ephor-new").exists()
    assert partage.read_text(encoding="utf-8") == "# le module partagé\n"
    assert ".ephor/score.py lien symbolique" in sortie
    assert "MISE À JOUR" in sortie, "la mise à jour est allée jusqu'au bilan"
    if os.geteuid() != 0:  # root écrit partout : la lecture seule ne l'arrête pas
        assert ".ephor/usage.py en lecture seule" in sortie


def test_un_makefile_qui_lance_deja_le_juge_n_est_pas_inerte(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Le geste 0 crie « INERTE » quand le Makefile du projet ne connaît pas Ephor. Après
    ce geste, un Makefile qui diffère dit seulement que la version d'Ephor a changé : le
    crier à chaque mise à jour apprenait à ignorer le seul message qui compte."""
    cible = projet_equipe(tmp_path / "noderepo")
    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")
    assert "GESTE 0" not in sortie
    assert "cibles d'Ephor absentes du vôtre : nouvelle-cible\n" in sortie, "ni .PHONY ni rien"
    assert "nouvelle-cible" not in (cible / "Makefile").read_text(encoding="utf-8")

    inerte = projet_equipe(tmp_path / "sans-geste-0", geste_0=False)
    assert "GESTE 0" in mettre_a_jour(inerte, version_suivante, "--mettre-a-jour")

    # Un Makefile du projet qui a repris la seule cible du juge, à sa façon : armé, et les
    # cibles qui lui manquent sont nommées — sans les cibles spéciales de make.
    a_sa_facon = projet_equipe(tmp_path / "a-sa-facon")
    (a_sa_facon / "Makefile").write_text(
        "juge:\n\tpython3 .ephor/judge.py check --task-id $(TASK)\n", encoding="utf-8"
    )
    git(a_sa_facon, "commit", "-qam", "le Makefile du projet")
    sortie = mettre_a_jour(a_sa_facon, version_suivante, "--mettre-a-jour")
    manquantes = [ligne for ligne in sortie.splitlines() if "cibles d'Ephor absentes" in ligne]
    assert manquantes and "verify" in manquantes[0].split(), sortie
    assert ".PHONY" not in manquantes[0]


def test_une_simple_mention_d_ephor_ne_fait_pas_taire_le_geste_0(tmp_path: Path) -> None:
    """Trouvé en relecture : le script conseille d'exclure `.ephor/` de flake8, et un refus
    d'écrire dans `.ephor/hooks/` est une règle de permission ordinaire. L'un ou l'autre
    faisait taire le geste 0 sur un dépôt où Ephor était inerte."""
    cible = tmp_path / "exclusions"
    depot_node(cible)
    (cible / "Makefile").write_text("lint:\n\tflake8 --exclude .ephor/ .\n", encoding="utf-8")
    (cible / ".claude" / "settings.json").write_text(
        '{"permissions":{"deny":["Edit(.ephor/hooks/**)"]}}\n', encoding="utf-8"
    )
    sortie = bootstrap(cible)
    assert "GESTE 0" in sortie
    assert "AUCUN n'est déclaré" in sortie and "No rule to make target" in sortie


def test_sans_l_option_rien_n_est_remplace_et_ses_restes_sont_retires_ensuite(
    tmp_path: Path, version_suivante: Path
) -> None:
    cible = projet_equipe(tmp_path / "noderepo")
    juge = cible / ".ephor" / "judge.py"
    avant = juge.read_bytes()
    mettre_a_jour(cible, version_suivante)
    assert juge.read_bytes() == avant, "sans --mettre-a-jour, rien n'est jamais écrasé"
    assert (cible / ".ephor" / "judge.py.ephor-new").exists()

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")
    assert juge.read_bytes() == (version_suivante / ".ephor" / "judge.py").read_bytes()
    assert not (cible / ".ephor" / "judge.py.ephor-new").exists()
    assert ".ephor-new d'un passage précédent retiré(s)" in sortie


def test_sans_commit_rien_n_est_remplace(tmp_path: Path, version_suivante: Path) -> None:
    """L'ancienne version doit rester quelque part. Sans commit, git ne la garde pas."""
    cible = tmp_path / "sans-commit"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    bootstrap(cible)
    juge = cible / ".ephor" / "judge.py"
    avant = juge.read_bytes()
    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")
    assert juge.read_bytes() == avant
    assert (cible / ".ephor" / "judge.py.ephor-new").exists()
    assert "aucun commit git" in sortie and "pas suivi par git" in sortie
    assert "Le greffon Copilot a changé" not in sortie, "rien n'a été remplacé"


def test_un_projet_dans_un_sous_dossier_de_son_depot(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Un dépôt qui porte plusieurs projets : git lit les chemins depuis sa racine, le
    script depuis le projet. Mal accordés, rien n'était jamais « suivi », donc rien remplacé."""
    depot = tmp_path / "mono"
    projet = depot / "services" / "app"
    projet.mkdir(parents=True)
    (projet / "index.js").write_text("console.log('app')\n", encoding="utf-8")
    git(depot, "init", "-q", "-b", "main")
    git(depot, "config", "user.email", "test@example.invalid")
    git(depot, "config", "user.name", "Ephor test")
    bootstrap(projet)
    git(depot, "add", "-A")
    git(depot, "commit", "-qm", "Ephor posé dans un sous-dossier")
    sortie = mettre_a_jour(projet, version_suivante, "--mettre-a-jour")
    assert (projet / ".ephor" / "judge.py").read_bytes() == (
        version_suivante / ".ephor" / "judge.py"
    ).read_bytes()
    assert not (projet / ".ephor" / "judge.py.ephor-new").exists()
    assert "services/app/" not in sortie, "les chemins se lisent depuis le projet"


def test_depuis_la_meme_version_rien_ne_bouge(tmp_path: Path) -> None:
    cible = projet_equipe(tmp_path / "noderepo")
    sortie = mettre_a_jour(cible, ROOT, "--mettre-a-jour")
    assert "aucun fichier du moteur remplacé" in sortie
    assert git(cible, "status", "--porcelain", "--", ".ephor") == ""
