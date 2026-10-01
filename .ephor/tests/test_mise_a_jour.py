"""Mettre Ephor à jour dans un projet déjà équipé : `--mettre-a-jour` (ADR 0007 et 0010).

Avant, relancer l'installation depuis la nouvelle version déposait un `.ephor-new` à côté
de chaque fichier du moteur qui avait changé — des dizaines à reprendre à la main, alors
que la règle dit « `.ephor/` ne se modifie pas dans un projet, on le remplace ». Ces tests
tiennent la promesse et ses limites : le moteur est remplacé là où git garde l'ancien
contenu exact ; hors du moteur, un fichier d'Ephor l'est aussi, s'il est en plus une version
livrée par Ephor, jamais retouchée ; le reste du projet n'est pas touché.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

import adapters
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


def makefile_fusionne(cible: Path) -> None:
    """Le geste 0 d'un vrai dépôt : le Makefile d'Ephor, plus une cible à lui. Ce n'est plus
    une version livrée par Ephor : une mise à jour ne le remplace pas."""
    ajoute(cible / "Makefile", "\ndeploy: ## une cible du projet\n\t@echo deploy\n")
    git(cible, "commit", "-qam", "nos cibles")


def version_qui_change_hors_du_moteur(source: Path) -> None:
    """La version suivante change aussi ce qu'Ephor pose hors de .ephor/ : son greffon
    d'opencode, un sous-agent, et ses réglages, qui branchent un garde-fou de plus."""
    ajoute(source / ".opencode" / "plugins" / "ephor.js", "// changé dans la version suivante\n")
    ajoute(source / ".claude" / "agents" / "planner.md", "\nUne consigne de la version suivante.\n")
    chemin = source / ".claude" / "settings.json"
    reglages = json.loads(chemin.read_text(encoding="utf-8"))
    reglages["hooks"]["Stop"][0]["hooks"].append(
        {"type": "command", "command": '"${CLAUDE_PROJECT_DIR}/.ephor/hooks/nouveau.sh"'}
    )
    chemin.write_text(json.dumps(reglages, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (source / ".ephor" / "hooks" / "nouveau.sh").write_text("#!/usr/bin/env bash\nexit 0\n")
    git(source, "add", "-A")
    git(source, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "-qm", "hors du moteur")  # fmt: skip


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


def test_un_projet_ne_de_l_archive_suit_aussi_les_tests_d_ephor(
    tmp_path: Path, copie_d_ephor: Path, version_suivante: Path
) -> None:
    """La recette « projet neuf » du README copie Ephor entier : ses tests, sa démonstration et
    ses scripts d'atelier viennent avec, et la CI du projet lance ces tests. La mise à jour ne
    les touchait pas, et disait de les retirer : vieux tests contre moteur neuf, CI rouge
    (mesuré le 30/09/2026 sur un projet 1.4.0 mis à jour). Un projet installé par le script,
    lui, ne les reçoit toujours pas."""
    cible = tmp_path / "projet"
    shutil.copytree(copie_d_ephor, cible)
    git(cible, "init", "-q", "-b", "main")
    git(cible, "add", "-A")
    git(cible, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "neuf")
    tests, scripts = version_suivante / ".ephor" / "tests", version_suivante / ".ephor" / "scripts"
    ajoute(tests / "test_score.py", "# changé dans la version suivante\n")
    ajoute(scripts / "demo_judge.sh", "# changé dans la version suivante\n")
    (tests / "test_nouveau.py").write_text("def test_nouveau() -> None:\n    pass\n")
    (tests / "test_refs.py").unlink()
    git(version_suivante, "add", "-A")
    git(version_suivante, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "-qm", "tests")  # fmt: skip

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    for rel in (".ephor/tests/test_score.py", ".ephor/scripts/demo_judge.sh"):
        assert (cible / rel).read_bytes() == (version_suivante / rel).read_bytes(), rel
    assert (cible / ".ephor" / "tests" / "test_nouveau.py").is_file()
    retirer = next(ligne for ligne in sortie.splitlines() if "git rm" in ligne)
    assert ".ephor/tests/test_refs.py" in retirer, "un test que la version n'a plus"
    for script in ("bootstrap_existing_repo.sh", "demo_judge.sh", "empreintes.py"):
        assert script not in retirer, f"{script} est toujours livré"

    installe = projet_equipe(tmp_path / "noderepo")
    mettre_a_jour(installe, version_suivante, "--mettre-a-jour")
    assert not (installe / ".ephor" / "tests").exists()
    assert not (installe / ".ephor" / "scripts" / "demo_judge.sh").exists()


def test_ce_qu_ephor_ne_livre_plus_hors_du_moteur_est_nomme(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Un sous-agent qu'Ephor ne livre plus restait dans le projet, chargé par les agents et
    dérivé vers opencode, sans un mot du bilan ; et une skill n'arrivait que par son SKILL.md,
    sans les fichiers d'à côté (#37)."""
    cible = projet_equipe(tmp_path / "noderepo")
    (version_suivante / ".claude" / "agents" / "scout.md").unlink()
    annexe = version_suivante / ".claude" / "skills" / "feature" / "exemple.md"
    annexe.write_text("un fichier à côté de la skill\n", encoding="utf-8")
    empreintes = version_suivante / ".ephor" / "empreintes.txt"
    lignes = [
        ligne
        for ligne in empreintes.read_text(encoding="utf-8").splitlines()
        if ".claude/agents/scout.md" not in ligne
    ]
    empreinte = hashlib.sha256(annexe.read_bytes()).hexdigest()
    lignes.append(f"{empreinte} .claude/skills/feature/exemple.md actuelle")
    empreintes.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    git(version_suivante, "add", "-A")
    git(version_suivante, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "-qm", "sans scout")  # fmt: skip

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    copiee = cible / ".claude" / "skills" / "feature" / "exemple.md"
    assert copiee.read_bytes() == annexe.read_bytes(), "la skill entière"
    assert (cible / ".claude" / "agents" / "scout.md").exists(), (
        "rien n'est retiré sans qu'on le demande"
    )
    assert "git rm .claude/agents/scout.md" in sortie
    assert "make adapters" in sortie.split("git rm .claude/agents/scout.md", 1)[1].splitlines()[0]


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
    makefile_fusionne(cible)
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


def test_une_cible_d_ephor_dont_la_recette_a_change_est_nommee(
    tmp_path: Path, version_suivante: Path
) -> None:
    """En 1.1, `task-check` a changé de recette sans changer de nom. Le bilan ne nommait que
    les cibles ABSENTES : un projet qui gardait son Makefile lançait encore l'ancienne
    recette, qui n'écrit jamais la trace que le juge réclame. Les cibles propres au projet
    (`test`, dont la recette n'appelle pas le moteur) ne sont pas nommées."""
    cible = projet_equipe(tmp_path / "noderepo")
    makefile_fusionne(cible)
    makefile = version_suivante / "Makefile"
    texte = makefile.read_text(encoding="utf-8")
    assert ".ephor/judge.py rouge" in texte and "\t@$(PY) pytest -q" in texte
    texte = texte.replace(".ephor/judge.py rouge", ".ephor/judge.py rouge --verbeux")
    texte = texte.replace("\t@$(PY) pytest -q", "\t@$(PY) pytest -q -x", 1)
    makefile.write_text(texte, encoding="utf-8")
    git(version_suivante, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "-qam", "recettes changées")  # fmt: skip
    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")
    assert "cibles d'Ephor dont la recette a changé : task-check — reprenez-les" in sortie


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
    assert "Plus livré" not in sortie and "plus rien ne les lit" not in sortie
    assert git(cible, "status", "--porcelain", "--", ".ephor") == ""


def test_ce_qu_ephor_pose_hors_du_moteur_est_remplace_s_il_n_a_jamais_ete_retouche(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Le greffon d'opencode, les réglages et les sous-agents de Claude Code vivent hors de
    `.ephor/`. Ils restaient en `.ephor-new` à chaque mise à jour, et l'ancienne version
    tournait : un projet « en 1.1.5 » chargeait le greffon d'opencode de la 1.1.3, sans
    plafond relevé ni sentinelle (mesuré le 29/09/2026). Une version livrée par Ephor,
    jamais retouchée, est maintenant remplacée, et le `.ephor-new` d'un passage d'avant part."""
    cible = projet_equipe(tmp_path / "noderepo")
    reste = cible / ".opencode" / "plugins" / "ephor.js.ephor-new"
    reste.write_text("// laissé par une mise à jour d'avant\n", encoding="utf-8")
    version_qui_change_hors_du_moteur(version_suivante)

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    for rel in (".opencode/plugins/ephor.js", ".claude/agents/planner.md", ".claude/settings.json"):
        assert (cible / rel).read_bytes() == (version_suivante / rel).read_bytes(), rel
        assert not (cible / f"{rel}.ephor-new").exists(), rel
    changes = git(cible, "diff", "--name-only")
    assert ".opencode/plugins/ephor.js" in changes, "l'ancienne version reste dans git"
    derive = (cible / ".opencode" / "agents" / "planner.md").read_text(encoding="utf-8")
    assert "Une consigne de la version suivante." in derive, "les adaptateurs repartent du neuf"
    bilan = sortie[sortie.index("MISE À JOUR :") :]
    assert "autre(s) fichier(s) d'Ephor remplacé(s), hors du moteur" in bilan
    assert ".opencode/plugins/ephor.js" in bilan and "À REPRENDRE" not in bilan
    assert "relancez opencode" in bilan
    assert ".ephor-new d'un passage précédent retiré(s)" in bilan


def test_des_reglages_d_avant_les_points_d_entree_disent_quoi_remplacer(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Des réglages retouchés avant la 1.4 nomment chaque garde. La 1.4 les remplace par
    quatre points d'entrée : les ajouter à côté ferait tourner chaque garde deux fois. Le
    bilan dit de remplacer, pas d'ajouter (ADR 0011)."""
    cible = projet_equipe(tmp_path / "noderepo")
    reglages = cible / ".claude" / "settings.json"
    propres = json.loads(reglages.read_text(encoding="utf-8"))
    garde = '"${CLAUDE_PROJECT_DIR}/.ephor/hooks/%s"'
    propres["hooks"] = {
        "PreToolUse": [
            {"matcher": "*", "hooks": [{"type": "command", "command": garde % "kill-switch.sh"}]}
        ],
        "Stop": [{"hooks": [{"type": "command", "command": garde % "checkpoint.sh"}]}],
    }
    reglages.write_text(json.dumps(propres, indent=2) + "\n", encoding="utf-8")
    git(cible, "commit", "-qam", "nos réglages d'avant")

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    bilan = sortie[sortie.index("MISE À JOUR :") :]
    assert "PreToolUse : avant-outil.sh" in bilan and "Stop : fin-tour.sh" in bilan
    assert "REMPLACENT la liste des gardes" in bilan


def test_les_reglages_d_un_role_survivent_a_la_mise_a_jour(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Le modèle et l'effort d'un rôle se règlent dans l'en-tête de son sous-agent (#30). Un
    réglage du projet faisait du fichier une retouche : la mise à jour le laissait en
    `.ephor-new`, et le rôle gardait sa consigne d'avant. Elle garde maintenant ces deux
    lignes, remplace le reste, et opencode les reçoit."""
    cible = projet_equipe(tmp_path / "noderepo")
    evaluateur = cible / ".claude" / "agents" / "evaluator.md"
    texte = evaluateur.read_text(encoding="utf-8")
    evaluateur.write_text(
        texte.replace("model: opus", "model: sonnet").replace("effort: high", "effort: max"),
        encoding="utf-8",
    )
    git(cible, "commit", "-qam", "notre évaluateur")
    ajoute(version_suivante / ".claude" / "agents" / "evaluator.md", "\nConsigne suivante.\n")
    git(version_suivante, "-c", "user.email=t@example.invalid", "-c", "user.name=t",
        "commit", "-qam", "évaluateur suivant")  # fmt: skip

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    nouveau = evaluateur.read_text(encoding="utf-8")
    assert "Consigne suivante." in nouveau, "la consigne suit la version"
    assert "model: sonnet\neffort: max" in nouveau, "les réglages du projet restent"
    assert not evaluateur.with_name("evaluator.md.ephor-new").exists()
    assert "réglages du rôle gardés" in sortie
    derive = (cible / ".opencode" / "agents" / "evaluator.md").read_text(encoding="utf-8")
    assert 'variant: "max"' in derive, "et opencode les reçoit"


def test_un_role_regle_par_le_projet_n_est_pas_annonce_remplace_sans_changement(
    tmp_path: Path,
) -> None:
    """Un sous-agent dont le projet a réglé `model:` était annoncé « ↻ remplacé… jamais
    retouché » à chaque mise à jour, même d'une version à elle-même, alors que rien ne
    changeait (audit du 01/10/2026)."""
    cible = projet_equipe(tmp_path / "noderepo")
    planner = cible / ".claude" / "agents" / "planner.md"
    planner.write_text(planner.read_text().replace("model: opus", "model: sonnet"))
    git(cible, "commit", "-qam", "notre planner")
    sortie = mettre_a_jour(cible, ROOT, "--mettre-a-jour")
    assert ".claude/agents/planner.md" not in sortie, sortie
    assert "model: sonnet" in planner.read_text()
    assert git(cible, "status", "--porcelain") == ""


def test_l_installation_ne_double_pas_le_journal_du_projet(tmp_path: Path) -> None:
    """À l'installation, un PROGRESS.md déjà là recevait un .ephor-new, que doctor proposait
    ensuite de mettre à sa place : le journal du projet écrasé (audit du 01/10/2026)."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    (cible / "PROGRESS.md").write_text("# notre journal\n")
    bootstrap(cible)
    assert (cible / "PROGRESS.md").read_text() == "# notre journal\n"
    assert not (cible / "PROGRESS.md.ephor-new").exists()


def test_un_reglage_que_l_ancienne_version_n_avait_pas_arrive_quand_meme(tmp_path: Path) -> None:
    """De la 1.0 à la 1.4, l'évaluateur n'avait que `model:`. Un projet qui l'avait passé de
    `opus` à `sonnet` perdait, en passant en 1.5, le nouvel `effort: high` : une ligne absente
    de l'ancien fichier était lue comme retirée par le projet (audit du 01/10/2026)."""
    import adapters

    ancien = tmp_path / "ancien.md"
    ancien.write_text("---\nname: evaluator\nmodel: sonnet\n---\nConsigne d'avant.\n")
    nouveau = tmp_path / "evaluator.md"
    nouveau.write_text("---\nname: evaluator\nmodel: opus\neffort: high\n---\nConsigne.\n")
    assert adapters.main(["garder-reglages", str(ancien), str(nouveau)]) == 0
    assert (
        nouveau.read_text() == "---\nname: evaluator\nmodel: sonnet\neffort: high\n---\nConsigne.\n"
    )


def test_une_retouche_du_projet_hors_du_moteur_reste_et_se_donne_a_reprendre(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Un dépôt qui avait déjà ses réglages Claude les a fusionnés avec ceux d'Ephor au
    geste 0 : ce n'est plus aucune version livrée, rien ne l'écrase. Mais le bilan doit dire
    que la mise à jour n'est pas finie, et quel garde-fou manque, au lieu de se taire."""
    cible = projet_equipe(tmp_path / "noderepo")
    reglages = cible / ".claude" / "settings.json"
    propres = json.loads(reglages.read_text(encoding="utf-8"))
    propres["permissions"]["allow"].append("Bash(npm test *)")
    reglages.write_text(json.dumps(propres, indent=2) + "\n", encoding="utf-8")
    ajoute(cible / ".opencode" / "plugins" / "ephor.js", "// une retouche du projet\n")
    git(cible, "commit", "-qam", "nos retouches")
    version_qui_change_hors_du_moteur(version_suivante)

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    assert "Bash(npm test *)" in reglages.read_text(encoding="utf-8"), "la retouche reste"
    assert (cible / ".claude" / "settings.json.ephor-new").exists()
    greffon = (cible / ".opencode" / "plugins" / "ephor.js").read_text(encoding="utf-8")
    assert greffon.endswith("// une retouche du projet\n")
    assert "pas une version connue d'Ephor (retouché dans le projet" in sortie
    bilan = sortie[sortie.index("MISE À JOUR :") :]
    assert "À REPRENDRE" in bilan
    assert "hooks d'Ephor que vos réglages ne déclarent pas : Stop : nouveau.sh" in bilan
    assert "opencode charge l'ancien greffon" in bilan
    planner = ".claude/agents/planner.md"
    assert (cible / planner).read_bytes() == (version_suivante / planner).read_bytes(), (
        "ce que le projet n'a pas retouché est remplacé quand même"
    )


def test_une_retouche_d_un_fichier_qu_ephor_ne_change_pas_n_est_pas_a_reprendre(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Les réglages d'un projet existant sont fusionnés au geste 0 : ce n'est plus aucune
    version livrée. Chaque mise à jour les disait « À REPRENDRE », avec un `.ephor-new` que
    `make doctor` signalait ensuite, même quand Ephor ne les avait pas changés (audit du
    01/10/2026). La version qu'Ephor livre est maintenant comparée à celle en place."""
    cible = projet_equipe(tmp_path / "noderepo")
    reglages = cible / ".claude" / "settings.json"
    propres = json.loads(reglages.read_text(encoding="utf-8"))
    propres["permissions"]["allow"].append("Bash(npm test *)")
    reglages.write_text(json.dumps(propres, indent=2) + "\n", encoding="utf-8")
    git(cible, "commit", "-qam", "nos réglages")

    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")

    bilan = sortie[sortie.index("MISE À JOUR :") :]
    assert "À REPRENDRE" not in bilan, bilan
    assert not (cible / ".claude" / "settings.json.ephor-new").exists()
    assert ".claude/settings.json" in bilan and "inchangé" in bilan, "et il le dit"
    # Mais si un .ephor-new attend encore d'être repris, il reste à reprendre.
    (cible / ".claude" / "settings.json.ephor-new").write_text("{}\n", encoding="utf-8")
    sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")
    assert "À REPRENDRE" in sortie[sortie.index("MISE À JOUR :") :]


# L'intégrité d'une LIVRAISON d'Ephor : vérifiée dans son dépôt, pas dans un projet né de son
# archive, qui retouche son Makefile (sa CI devenait rouge, audit du 01/10/2026). Le marqueur
# ne part pas dans l'archive (.gitattributes).
depot_d_ephor = pytest.mark.skipif(
    not (ROOT / ".depot-ephor").exists(), reason="intégrité d'une livraison : dépôt d'Ephor seul"
)


@depot_d_ephor
def test_les_versions_actuelles_ont_leur_empreinte() -> None:
    """Sans elle, une mise à jour ne reconnaîtrait pas la version qu'elle doit remplacer, et
    le fichier resterait en `.ephor-new` pour toujours. À refaire à chaque livraison qui
    change l'un de ces fichiers : python3 .ephor/scripts/empreintes.py."""
    resultat = subprocess.run(
        [sys.executable, "-B", ".ephor/scripts/empreintes.py", "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultat.returncode == 0, resultat.stdout


@depot_d_ephor
def test_tout_ce_que_l_installation_pose_hors_du_moteur_a_son_empreinte(tmp_path: Path) -> None:
    """Un fichier d'Ephor posé hors du moteur sans empreinte ne serait jamais reconnu, donc
    jamais remplacé. Les réglages du projet, l'état tenu par l'agent et les fichiers que les
    adaptateurs régénèrent n'en ont pas besoin."""
    cible = tmp_path / "vide"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    bootstrap(cible)
    au_projet = {"PROGRESS.md", "STEER.md", ".gitignore", *adapters.fichiers()}
    poses = [
        rel
        for rel in git(cible, "ls-files", "-o", "--exclude-standard").splitlines()
        if not rel.startswith(".ephor/") and rel not in au_projet
    ]
    liste = (ROOT / ".ephor" / "empreintes.txt").read_text(encoding="utf-8")
    actuelles = {
        morceaux[1]: morceaux[0]
        for morceaux in (ligne.split() for ligne in liste.splitlines())
        if morceaux[-1:] == ["actuelle"]
    }
    assert ".opencode/plugins/ephor.js" in poses
    for rel in poses:
        assert rel in actuelles, f"{rel} : posé hors du moteur, sans empreinte"
        assert hashlib.sha256((cible / rel).read_bytes()).hexdigest() == actuelles[rel], rel


def test_le_doctor_dit_qu_une_mise_a_jour_n_est_pas_finie(tmp_path: Path) -> None:
    """Un `.ephor-new` qui traîne, c'est l'ancienne version qui tourne ; un greffon d'opencode
    qui n'est pas celui du moteur, c'est opencode qui tourne avec un autre."""
    cible = projet_equipe(tmp_path / "noderepo")

    def doctor() -> str:
        return subprocess.run(
            ["bash", ".ephor/scripts/doctor.sh"],
            cwd=cible,
            capture_output=True,
            text=True,
            check=False,
        ).stdout

    sortie = doctor()
    assert "aucun .ephor-new en attente" in sortie
    assert "greffon opencode à la version du moteur" in sortie
    (cible / ".claude" / "settings.json.ephor-new").write_text("{}\n", encoding="utf-8")
    ajoute(cible / ".opencode" / "plugins" / "ephor.js", "// une autre version\n")
    sortie = doctor()
    assert "1 fichier(s) .ephor-new en attente" in sortie and "(.claude/settings.json)" in sortie
    assert "greffon opencode (.opencode/plugins/ephor.js) différent" in sortie


# ------------------------------------------------- relecture adverse de la 1.5.2 (01/10/2026)


def test_l_installation_pose_les_pointeurs_vers_agents_md(tmp_path: Path) -> None:
    """Claude Code lit CLAUDE.md, Copilot .github/copilot-instructions.md : sans eux, un projet
    équipé par le script travaillait sans contrat de travail chargé. Un fichier du projet reste
    le sien, avec une note s'il ne cite pas AGENTS.md."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    sortie = bootstrap(cible)
    assert (cible / "CLAUDE.md").read_text(encoding="utf-8").strip() == "@AGENTS.md"
    assert "AGENTS.md" in (cible / ".github" / "copilot-instructions.md").read_text(
        encoding="utf-8"
    )
    assert "AGENTS.md absent" in sortie
    autre = tmp_path / "avec-le-sien"
    depot_node(autre)
    (autre / "CLAUDE.md").write_text("# Nos consignes\n")
    sortie = bootstrap(autre)
    assert (autre / "CLAUDE.md").read_text(encoding="utf-8") == "# Nos consignes\n"
    assert not (autre / "CLAUDE.md.ephor-new").exists()
    assert "CLAUDE.md existe et ne cite pas AGENTS.md" in sortie


def test_le_gitignore_ne_recoit_que_ce_que_le_projet_n_ignore_pas_deja(tmp_path: Path) -> None:
    """Né de l'archive d'Ephor, un projet ignore déjà `__pycache__/` partout : la mise à jour
    lui ajoutait quand même `.ephor/**/__pycache__/`, et le bilan disait « fichiers suivis
    modifiés par l'installation : .gitignore »."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    (cible / ".gitignore").write_text("node_modules/\n__pycache__/\n.verify/\n")
    git(cible, "commit", "-qam", "ignore")
    bootstrap(cible)
    ignore = (cible / ".gitignore").read_text(encoding="utf-8")
    assert ".ephor/**/__pycache__/" not in ignore
    assert ignore.count(".verify/") == 1
    assert "mutants/" in ignore and "AGENT_STOP" in ignore


def test_un_echec_des_adaptateurs_est_dit_et_fait_echouer_l_installation(tmp_path: Path) -> None:
    """Avalé par un « à relancer à la main », l'échec laissait opencode et Copilot sans réglages,
    et le bilan disait « 0 à fusionner », code de sortie 0."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    bootstrap(cible)
    (cible / ".claude" / "agents" / "casse.md").write_text("pas d'en-tête YAML\n")
    resultat = subprocess.run(
        ["bash", str(ROOT / ".ephor" / "scripts" / "bootstrap_existing_repo.sh"), str(cible)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert resultat.returncode == 1
    assert "ÉCHEC des adaptateurs" in resultat.stdout
    assert "ADAPTATEURS EN ÉCHEC" in resultat.stdout


def test_un_sous_agent_retouche_que_la_version_ne_change_pas_n_est_pas_a_reprendre(
    tmp_path: Path, version_suivante: Path
) -> None:
    """Un sous-agent ou une skill retouchés par le projet étaient « À REPRENDRE, c'est encore leur
    ANCIENNE version qui tourne », avec un .ephor-new à chaque passage et un WARN du diagnostic
    pour toujours — alors qu'Ephor ne les avait pas changés entre les deux versions."""
    cible = projet_equipe(tmp_path / "noderepo")
    for rel in (".claude/agents/scout.md", ".claude/skills/feature/SKILL.md"):
        ajoute(cible / rel, "\nUne consigne propre au projet.\n")
    git(cible, "commit", "-qam", "nos retouches")
    for _ in range(2):
        sortie = mettre_a_jour(cible, version_suivante, "--mettre-a-jour")
        assert "rien à reprendre" in sortie
        assert ".claude/agents/scout.md" in sortie.split("rien à reprendre")[1]
        assert "À REPRENDRE" not in sortie
        assert not (cible / ".claude" / "agents" / "scout.md.ephor-new").exists()
        assert not (cible / ".claude" / "skills" / "feature" / "SKILL.md.ephor-new").exists()
    assert (
        "Une consigne propre au projet." in (cible / ".claude" / "agents" / "scout.md").read_text()
    )


def _clone_complet(tmp_path: Path, nom: str, *options: str) -> Path | None:
    if git(ROOT, "rev-parse", "--is-shallow-repository").strip() == "true":
        return None
    clone = tmp_path / nom
    subprocess.run(
        ["git", "clone", "-q", *options, f"file://{ROOT}", str(clone)],
        check=True,
        capture_output=True,
    )
    # Le script tel qu'il est sur le disque, pas celui du dernier commit : un correctif pas
    # encore commité doit pouvoir être testé.
    shutil.copy(ROOT / ".ephor" / "scripts" / "empreintes.py", clone / ".ephor" / "scripts")
    shutil.copy(ROOT / ".ephor" / "adapters.py", clone / ".ephor")
    return clone


def test_les_empreintes_refusent_une_histoire_tronquee(tmp_path: Path) -> None:
    """Réécrite depuis un clone superficiel, la liste perdait toutes les versions passées
    (98 lignes -> 23), et `check` la disait « à jour » : une livraison faite ainsi aurait rendu
    les mises à jour incapables de remplacer quoi que ce soit hors du moteur."""
    clone = _clone_complet(tmp_path, "superficiel", "--depth", "1")
    if clone is None:
        pytest.skip("le dépôt d'Ephor est lui-même superficiel ici")
    script = clone / ".ephor" / "scripts" / "empreintes.py"
    avant = (clone / ".ephor" / "empreintes.txt").read_text(encoding="utf-8")
    resultat = subprocess.run(
        [sys.executable, "-B", str(script)], capture_output=True, text=True, check=False
    )
    assert resultat.returncode == 2
    assert "superficiel" in resultat.stdout
    assert (clone / ".ephor" / "empreintes.txt").read_text(encoding="utf-8") == avant


def test_les_empreintes_gardent_les_versions_deja_ecrites_et_voient_celles_qui_manquent(
    tmp_path: Path,
) -> None:
    clone = _clone_complet(tmp_path, "complet")
    if clone is None:
        pytest.skip("le dépôt d'Ephor est lui-même superficiel ici")
    script = clone / ".ephor" / "scripts" / "empreintes.py"
    liste = clone / ".ephor" / "empreintes.txt"
    fausse = "ab" * 32 + " Makefile\n"
    liste.write_text(liste.read_text(encoding="utf-8") + fausse, encoding="utf-8")
    subprocess.run([sys.executable, "-B", str(script)], check=True, capture_output=True)
    assert fausse in liste.read_text(encoding="utf-8"), "une version écrite n'est jamais oubliée"
    toutes = liste.read_text(encoding="utf-8").splitlines()
    retiree = next(x for x in toutes if " Makefile" in x)
    liste.write_text("\n".join(x for x in toutes if x != retiree) + "\n", encoding="utf-8")
    controle = subprocess.run(
        [sys.executable, "-B", str(script), "check"], capture_output=True, text=True, check=False
    )
    assert controle.returncode == 1
    assert "a disparu de la liste" in controle.stdout
