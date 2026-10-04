"""Ephor (privé) : Ephor sur un dépôt partagé que l'équipe n'a pas adopté (.ephor/scripts/prive.sh).

Le moteur est commité sur la branche principale LOCALE seulement, derrière un marqueur ; ce
que l'équipe reçoit par merge request, c'est le contrat, ses tests et les commits de la
tâche, rien d'Ephor. Ces tests jouent le clone de l'opérateur face à un dépôt distant local,
comme en entreprise : poser, la garde pre-push, publier, la fusion côté serveur, adopter,
retirer.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT, git

SCRIPT = ROOT / ".ephor" / "scripts" / "prive.sh"
MAKEFILE_EQUIPE = (
    "test:\n\tnpm test\n\nlint:\n\tnpm run lint\n\nsetup:\n\tnpm ci\n\nPY := python3\n"
)
PLAN = {
    "id": "FEAT-001",
    "objective": "Le taux double la valeur qu'on lui donne.",
    "risk": "faible",
    "niveau": "standard",
    "effort": "high",
    "allowed_paths": ["src/taux.py"],
    "criteria": [
        {
            "id": "C1",
            "description": "le taux double",
            "check": ["{python}", "tasks/FEAT-001/check_c1.py"],
        }
    ],
    "verify": ["{python}", "-c", "print('vérification du projet')"],
    "timeout_seconds": 120,
}


def environnement(tmp_path: Path) -> dict[str, str]:
    """Sans la configuration git du poste (hooks globaux, signature), et les sauvegardes
    dans un cache jetable."""
    return dict(
        os.environ,
        GIT_CONFIG_GLOBAL=os.devnull,
        XDG_CONFIG_HOME=str(tmp_path / "xdg"),
        XDG_CACHE_HOME=str(tmp_path / "cache"),
    )


def depot_partage(tmp_path: Path, makefile: bool = True, reglages: bool = True) -> Path:
    """Le clone d'un dépôt d'équipe : son Makefile, ses réglages Claude, son CLAUDE.md, un
    hook pre-push à lui, et un dépôt distant « origin » local, nu, avec sa branche désignée."""
    origin = tmp_path / "origin.git"
    # `-b main` : sans lui, le dépôt nu naît sur `master` là où git n'a pas de branche par
    # défaut réglée (le runner de la CI), et `set-head` ne trouvait pas `main` (14 tests rouges).
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
    clone = tmp_path / "equipe"
    (clone / "src").mkdir(parents=True)
    (clone / "src" / "index.js").write_text("console.log('salut')\n", encoding="utf-8")
    (clone / "src" / "taux.py").write_text("def taux(x):\n    return x\n", encoding="utf-8")
    (clone / "package.json").write_text(
        '{"name":"app","scripts":{"test":"jest"}}\n', encoding="utf-8"
    )
    (clone / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (clone / "CLAUDE.md").write_text("# Projet\n", encoding="utf-8")
    if makefile:
        (clone / "Makefile").write_text(MAKEFILE_EQUIPE, encoding="utf-8")
    if reglages:
        (clone / ".claude").mkdir()
        (clone / ".claude" / "settings.json").write_text(
            '{"permissions":{"allow":["Bash(npm test)"]}}\n'
        )
    git(clone, "init", "-q", "-b", "main")
    git(clone, "config", "user.email", "test@example.invalid")
    git(clone, "config", "user.name", "Ephor test")
    hook = clone / ".git" / "hooks" / "pre-push"
    hook.write_text(
        "#!/bin/sh\n"
        'echo precedent >> "$(git rev-parse --path-format=absolute --git-common-dir)/hook-temoin"\n'
    )
    hook.chmod(0o755)
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "base du projet")
    git(clone, "remote", "add", "origin", str(origin))
    git(clone, "push", "-q", "-u", "origin", "main")
    git(clone, "remote", "set-head", "origin", "-a")
    (clone / ".git" / "hook-temoin").unlink(missing_ok=True)
    return clone


def prive(
    clone: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    """Le script du clone, sauf pour poser : celui du dépôt d'Ephor, comme l'installation."""
    script = str(SCRIPT) if args[:1] == ("poser",) else ".ephor/scripts/prive.sh"
    return subprocess.run(
        ["bash", script, *args],
        cwd=clone,
        env=env or environnement(clone.parent),
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )


def poser(clone: Path) -> subprocess.CompletedProcess[str]:
    sortie = prive(clone, "poser", str(clone))
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    return sortie


def commits_locaux(clone: Path) -> list[str]:
    return git(clone, "log", "--format=%s", "origin/main..main").splitlines()


def marques(clone: Path, plage: str = "origin/main..main") -> list[str]:
    return git(clone, "rev-list", "--grep=^Ephor-prive: ", plage).split()


def approuver(clone: Path, env: dict[str, str]) -> None:
    """Le contrat FEAT-001, approuvé sur la branche principale locale comme l'opérateur le
    ferait : make task-new, le test d'acceptation, le contrôle rouge, le commit."""
    plan = clone.parent / "plan.json"
    plan.write_text(json.dumps(PLAN), encoding="utf-8")
    juge = [sys.executable, "-B", ".ephor/judge.py"]
    subprocess.run(
        [*juge, "new", "--task-id", "FEAT-001", "--from", str(plan)],
        cwd=clone,
        env=env,
        check=True,
        capture_output=True,
    )
    (clone / "tasks" / "FEAT-001" / "check_c1.py").write_text(
        "import sys\nsys.path.insert(0, 'src')\nfrom taux import taux\nassert taux(3) == 6\n",
        encoding="utf-8",
    )
    rouge = subprocess.run(
        [*juge, "rouge", "--task-id", "FEAT-001", "--workdir", str(clone.parent / "travail")],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rouge.returncode == 0, rouge.stdout + rouge.stderr
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "FEAT-001 : contrat approuvé")


def travailler(clone: Path, *messages: str) -> None:
    """La tâche, sur sa branche : un commit par message, chacun dans le périmètre."""
    git(clone, "switch", "-qc", "task/FEAT-001")
    for i, message in enumerate(messages):
        (clone / "src" / "taux.py").write_text(
            f"def taux(x):\n    {'# ' * i}\n    return x * 2\n", encoding="utf-8"
        )
        git(clone, "commit", "-qam", message)


def juger(clone: Path, env: dict[str, str]) -> None:
    verdict = subprocess.run(
        [
            sys.executable,
            "-B",
            ".ephor/judge.py",
            "check",
            "--task-id",
            "FEAT-001",
            "--workdir",
            str(clone.parent / "travail"),
        ],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr


def test_poser_commite_ephor_sur_le_main_local_sans_toucher_aux_fichiers_de_l_equipe(
    tmp_path: Path,
) -> None:
    clone = depot_partage(tmp_path)
    sortie = poser(clone)
    assert "commit « Ephor (privé) » sur main" in sortie.stdout
    # Un seul commit, marqué, et rien ne traîne : la copie de travail est propre.
    assert commits_locaux(clone) == [
        "Ephor (privé) : moteur, garde-fous et interface de travail, sur ce clone seulement"
    ]
    assert len(marques(clone)) == 1
    assert git(clone, "status", "--porcelain") == ""
    assert not list(clone.rglob("*.ephor-new"))
    # Les fichiers de l'équipe restent les siens : .gitignore à l'octet près, réglages Claude.
    assert (clone / ".gitignore").read_text() == "node_modules/\n"
    assert (
        clone / ".claude" / "settings.json"
    ).read_text() == '{"permissions":{"allow":["Bash(npm test)"]}}\n'
    exclusions = (clone / ".git" / "info" / "exclude").read_text().splitlines()
    assert (
        ".verify/" in exclusions
        and "/PROGRESS.md" in exclusions
        and ".claude/settings.local.json" in exclusions
    )
    assert (clone / "PROGRESS.md").exists()
    assert "!! PROGRESS.md" in git(clone, "status", "--porcelain", "--ignored")
    # Les réglages d'Ephor, propres au poste ; le pointeur vers AGENTS.md aussi.
    assert (clone / ".claude" / "settings.local.json").read_text() == (
        ROOT / ".claude" / "settings.json"
    ).read_text()
    assert (clone / "CLAUDE.local.md").read_text() == "@AGENTS.md\n"
    assert (clone / "CLAUDE.md").read_text() == "# Projet\n"
    # Le Makefile de l'équipe gagne une ligne ; ses cibles gagnent sur celles d'Ephor.
    assert (clone / "Makefile").read_text().startswith(MAKEFILE_EQUIPE)
    assert "-include .ephor/projet/prive.mk" in (clone / "Makefile").read_text()
    inclus = (clone / ".ephor" / "projet" / "prive.mk").read_text()
    assert (
        "\njudge:" in inclus
        and "\ntest:" not in inclus
        and "\nsetup:" not in inclus
        and "\nPY :=" not in inclus
    )
    assert (
        "écartées, parce que l'équipe les définit déjà" in inclus.lower()
        or "lint setup test" in inclus
    )
    env = environnement(tmp_path)
    assert (
        "npm test"
        in subprocess.run(
            ["make", "-n", "test"], cwd=clone, env=env, capture_output=True, text=True
        ).stdout
    )
    judge = subprocess.run(
        ["make", "-n", "judge", "TASK=X"], cwd=clone, env=env, capture_output=True, text=True
    )
    assert ".ephor/judge.py" in judge.stdout and "warning" not in judge.stderr, judge.stderr
    # opencode et Copilot sont dérivés des réglages d'Ephor, pas de ceux de l'équipe.
    opencode = json.loads((clone / ".opencode" / "opencode.json").read_text())
    assert opencode.get("permission"), "les permissions d'opencode viennent des réglages d'Ephor"
    # La garde pre-push est là, le hook de l'équipe chaîné derrière.
    assert "ephor-prive" in (clone / ".git" / "hooks" / "pre-push").read_text()
    assert (clone / ".git" / "hooks" / "pre-push.avant-ephor-prive").exists()
    # Relancé : rien ne change, rien ne se duplique.
    deux = poser(clone)
    assert "rien de nouveau à commiter" in deux.stdout
    assert len(marques(clone)) == 1 and git(clone, "status", "--porcelain") == ""


def test_poser_sans_makefile_ni_reglages_d_equipe_pose_ceux_d_ephor(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path, makefile=False, reglages=False)
    poser(clone)
    assert (clone / "Makefile").read_text() == (ROOT / "Makefile").read_text()
    assert not (clone / ".ephor" / "projet" / "prive.mk").exists()
    assert (clone / ".claude" / "settings.json").read_text() == (
        ROOT / ".claude" / "settings.json"
    ).read_text()
    assert not (clone / ".claude" / "settings.local.json").exists()
    assert git(clone, "status", "--porcelain") == "" and len(marques(clone)) == 1


def test_poser_refuse_tout_ce_qui_rendrait_le_commit_d_ephor_impur(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path)
    (clone / "brouillon.txt").write_text("x\n")
    sortie = prive(clone, "poser", str(clone))
    assert sortie.returncode == 2 and "changements en cours" in sortie.stderr
    (clone / "brouillon.txt").unlink()
    git(clone, "switch", "-qc", "autre")
    sortie = prive(clone, "poser", str(clone))
    assert sortie.returncode == 2 and "branche principale" in sortie.stderr
    git(clone, "switch", "-q", "main")
    (clone / "GNUmakefile").write_text("all:\n\ttrue\n")
    git(clone, "add", "GNUmakefile")
    git(clone, "commit", "-qm", "un GNUmakefile")
    sortie = prive(clone, "poser", str(clone))
    assert sortie.returncode == 2 and "GNUmakefile" in sortie.stderr
    git(clone, "reset", "-q", "--hard", "HEAD~1")
    # Un geste de l'opérateur : refusé sous un agent, avant tout.
    sortie = prive(clone, "poser", str(clone), env=dict(environnement(tmp_path), CLAUDECODE="1"))
    assert sortie.returncode == 2 and "geste de l'opérateur" in sortie.stdout + sortie.stderr
    assert not (clone / ".ephor").exists()


def test_la_garde_pre_push_ne_laisse_rien_d_ephor_partir(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path)
    poser(clone)
    env = environnement(tmp_path)

    def pousser(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "push", "-q", "origin", *args],
            cwd=clone,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    refus = pousser("main")
    assert refus.returncode != 0 and "marqueur Ephor-prive" in refus.stderr
    # Une branche partie du main local emporte Ephor : refusée aussi (publier la rejouera).
    git(clone, "switch", "-qc", "depuis-main")
    (clone / "src" / "a.js").write_text("1\n")
    git(clone, "add", "src/a.js")
    git(clone, "commit", "-qm", "a")
    refus = pousser("depuis-main")
    assert refus.returncode != 0 and "marqueur Ephor-prive" in refus.stderr
    # Une branche partie du serveur, sans rien d'Ephor : elle passe, et le hook de l'équipe tourne.
    git(clone, "switch", "-qc", "propre", "origin/main")
    (clone / "src" / "b.js").write_text("2\n")
    git(clone, "add", "src/b.js")
    git(clone, "commit", "-qm", "b")
    ok = pousser("propre")
    assert ok.returncode == 0, ok.stderr
    assert (clone / ".git" / "hook-temoin").read_text() == "precedent\n", (
        "le hook pre-push de l'équipe est chaîné"
    )
    # Une ligne qui nomme .ephor/ (une exclusion, une inclusion), un fichier d'Ephor, une trace
    # rouge : refusés un par un, avec le commit et le fichier.
    for fichier, contenu, attendu in (
        (
            ".gitignore",
            "node_modules/\n.ephor/**/__pycache__/\n",
            "ajoute une ligne qui nomme .ephor/ dans .gitignore",
        ),
        (
            ".ephor/projet/note.txt",
            "x\n",
            "fichier(s) d'Ephor ou trace locale : .ephor/projet/note.txt",
        ),
        ("tasks/X-001/rouge.json", "{}\n", "tasks/X-001/rouge.json"),
    ):
        (clone / fichier).parent.mkdir(parents=True, exist_ok=True)
        (clone / fichier).write_text(contenu)
        git(clone, "add", "-f", fichier)
        git(clone, "commit", "-qm", f"touche {fichier}")
        refus = pousser("propre")
        assert refus.returncode != 0 and attendu in refus.stderr, refus.stderr
        git(clone, "reset", "-q", "--hard", "HEAD~1")


def test_publier_envoie_le_contrat_puis_la_tache_et_rien_d_ephor(tmp_path: Path) -> None:
    """Le flux entier : contrat approuvé, tâche jugée VÉRIFIÉE, publiée ; fusion côté serveur
    par merge request, puis par squash ; make taches les voit fusionnées ; retirer."""
    clone = depot_partage(tmp_path)
    poser(clone)
    env = environnement(tmp_path)
    approuver(clone, env)
    travailler(clone, "FEAT-001 : le taux double", "FEAT-001 : commentaire")
    juger(clone, env)

    sortie = prive(clone, "publier", "FEAT-001", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert "poussé : origin/task/FEAT-001" in sortie.stdout
    assert git(clone, "log", "--format=%s", "origin/main..origin/task/FEAT-001").splitlines() == [
        "FEAT-001 : commentaire",
        "FEAT-001 : le taux double",
        "FEAT-001 : contrat de tâche et tests d'acceptation",
    ]
    assert not marques(clone, "origin/main..origin/task/FEAT-001")
    arbre = git(clone, "ls-tree", "-r", "--name-only", "origin/task/FEAT-001").split()
    assert "tasks/FEAT-001/contract.json" in arbre and "tasks/FEAT-001/check_c1.py" in arbre
    assert "tasks/FEAT-001/rapport.md" in arbre and "tasks/FEAT-001/rouge.json" not in arbre
    assert not [f for f in arbre if f.startswith(".ephor/") or f.startswith(".claude/agents")]
    assert (
        git(
            clone,
            "diff",
            "--stat",
            "origin/main",
            "origin/task/FEAT-001",
            "--",
            "Makefile",
            ".gitignore",
            ".claude",
        )
        == ""
    )
    rapport = git(clone, "show", "origin/task/FEAT-001:tasks/FEAT-001/rapport.md")
    assert (
        "VÉRIFIÉ" in rapport
        and "python3 tasks/FEAT-001/check_c1.py" in rapport
        and "{python}" not in rapport
    )
    assert "C1 : ok" in rapport and "Contrôle rouge" in rapport
    mr = (clone / ".verify" / "prive" / "FEAT-001" / "mr.md").read_text()
    assert "### Commits" in mr and "FEAT-001 : le taux double" in mr
    sauvegardes = list(
        (tmp_path / "cache" / "ephor" / "prive" / "equipe").glob(
            "*-FEAT-001/principal/judge/FEAT-001/latest/report.json"
        )
    )
    assert sauvegardes, "les jugements sont sauvegardés hors du dépôt avant la poussée"
    assert git(clone, "rev-parse", "--verify", "refs/ephor/publier/FEAT-001")
    # La branche de l'agent n'a pas bougé, le main local non plus.
    assert git(clone, "branch", "--show-current").strip() == "task/FEAT-001"
    assert len(marques(clone)) == 1

    # Un commit de plus, republiée : la branche du serveur est remplacée, pas empilée.
    git(clone, "switch", "-q", "task/FEAT-001")
    (clone / "src" / "taux.py").write_text(
        'def taux(x):\n    """Double."""\n    return x * 2\n', encoding="utf-8"
    )
    git(clone, "commit", "-qam", "FEAT-001 : docstring")
    juger(clone, env)
    sortie = prive(clone, "publier", "FEAT-001", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert (
        len(git(clone, "log", "--format=%s", "origin/main..origin/task/FEAT-001").splitlines()) == 4
    )

    # Côté serveur, un collègue fusionne par merge request. Après git pull --rebase, le commit
    # d'Ephor se replace au-dessus, et le juge voit la tâche fusionnée.
    collegue = tmp_path / "collegue"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "origin.git"), str(collegue)], check=True, env=env
    )
    git(collegue, "config", "user.email", "c@example.invalid")
    git(collegue, "config", "user.name", "Collègue")
    git(
        collegue,
        "merge",
        "-q",
        "--no-ff",
        "origin/task/FEAT-001",
        "-m",
        "Merge branch 'task/FEAT-001' into 'main'",
    )
    git(collegue, "push", "-q", "origin", "main")
    git(clone, "switch", "-q", "main")
    subprocess.run(
        ["git", "pull", "-q", "--rebase", "origin", "main"],
        cwd=clone,
        env=env,
        check=True,
        capture_output=True,
    )
    assert len(marques(clone)) == 1 and git(clone, "status", "--porcelain") == ""
    etat = subprocess.run(
        [sys.executable, "-B", ".ephor/judge.py", "etat", "--task-id", "FEAT-001"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert etat.stdout.strip() == "fusionnée", etat.stdout + etat.stderr

    # Une deuxième tâche, fusionnée par squash cette fois, sa branche supprimée : vue
    # fusionnée aussi, par son contenu.
    plan = json.loads((tmp_path / "plan.json").read_text())
    plan["id"] = "FEAT-002"
    plan["objective"] = "Le taux dit son nom."
    plan["allowed_paths"] = ["src/nom.py"]
    plan["criteria"][0]["check"] = ["{python}", "tasks/FEAT-002/check_c1.py"]
    (tmp_path / "plan2.json").write_text(json.dumps(plan))
    juge = [sys.executable, "-B", ".ephor/judge.py"]
    subprocess.run(
        [*juge, "new", "--task-id", "FEAT-002", "--from", str(tmp_path / "plan2.json")],
        cwd=clone,
        env=env,
        check=True,
        capture_output=True,
    )
    (clone / "tasks" / "FEAT-002" / "check_c1.py").write_text(
        "import sys\nsys.path.insert(0, 'src')\nfrom nom import nom\nassert nom() == 'taux'\n"
    )
    subprocess.run(
        [*juge, "rouge", "--task-id", "FEAT-002", "--workdir", str(tmp_path / "travail")],
        cwd=clone,
        env=env,
        check=True,
        capture_output=True,
    )
    git(clone, "add", "-A")
    git(clone, "commit", "-qm", "FEAT-002 : contrat approuvé")
    git(clone, "switch", "-qc", "task/FEAT-002")
    (clone / "src" / "nom.py").write_text("def nom():\n    return 'taux'\n")
    git(clone, "add", "src/nom.py")
    git(clone, "commit", "-qm", "FEAT-002 : le nom")
    subprocess.run(
        [*juge, "check", "--task-id", "FEAT-002", "--workdir", str(tmp_path / "travail")],
        cwd=clone,
        env=env,
        check=True,
        capture_output=True,
        timeout=300,
    )
    sortie = prive(clone, "publier", "FEAT-002", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    git(collegue, "fetch", "-q", "origin")
    git(collegue, "merge", "-q", "--squash", "origin/task/FEAT-002")
    git(collegue, "commit", "-qm", "FEAT-002 (squash)")
    git(collegue, "push", "-q", "origin", "main", ":task/FEAT-002")
    git(clone, "switch", "-q", "main")
    subprocess.run(
        ["git", "pull", "-q", "--rebase", "--prune", "origin", "main"],
        cwd=clone,
        env=env,
        check=True,
        capture_output=True,
    )
    etat = subprocess.run(
        [*juge, "etat", "--task-id", "FEAT-002"], cwd=clone, env=env, capture_output=True, text=True
    )
    assert etat.stdout.strip() == "fusionnée", etat.stdout + etat.stderr

    # retirer : les contrats sont sur le serveur, rien n'est perdu ; le clone redevient celui
    # de l'équipe, hook compris.
    sortie = prive(clone, "retirer", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert git(clone, "rev-parse", "main") == git(clone, "rev-parse", "origin/main")
    assert (
        not (clone / ".ephor").exists() and not (clone / ".claude" / "settings.local.json").exists()
    )
    assert not (clone / "PROGRESS.md").exists() and not (clone / "CLAUDE.local.md").exists()
    assert "ephor-prive" not in (clone / ".git" / "info" / "exclude").read_text()
    assert (
        (clone / ".git" / "hooks" / "pre-push").read_text().startswith("#!/bin/sh\necho precedent")
    )
    assert not (clone / ".git" / "ephor-prive").exists()
    assert list(
        (tmp_path / "cache" / "ephor" / "prive" / "equipe").glob(
            "*-tout/principal/.verify/journal.jsonl"
        )
    )


def test_publier_refuse_sans_verdict_et_hors_du_perimetre(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path)
    poser(clone)
    env = environnement(tmp_path)
    approuver(clone, env)
    travailler(clone, "FEAT-001 : le taux double")
    sortie = prive(clone, "publier", "FEAT-001", env=env)
    assert sortie.returncode == 2 and "pas de verdict VÉRIFIÉ" in sortie.stderr
    assert not git(clone, "ls-remote", "origin", "task/FEAT-001").strip()
    # En brouillon, sans pousser : le rapport le dit.
    sortie = prive(clone, "publier", "FEAT-001", "--sans-verdict", "--sans-pousser", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert (
        "pas poussé" in sortie.stdout
        and not git(clone, "ls-remote", "origin", "task/FEAT-001").strip()
    )
    assert "sans verdict" in git(
        clone, "show", "refs/ephor/publier/FEAT-001:tasks/FEAT-001/rapport.md"
    )
    # Un commit hors du périmètre du contrat : refusé, nommé.
    (clone / "README.md").write_text("# lisez-moi\n")
    git(clone, "add", "README.md")
    git(clone, "commit", "-qm", "README en passant")
    sortie = prive(clone, "publier", "FEAT-001", "--sans-verdict", "--sans-pousser", env=env)
    assert sortie.returncode == 2 and "hors du périmètre du contrat : README.md" in sortie.stderr
    assert "README en passant" in sortie.stderr
    # Du travail pas commité dans le dossier de la tâche : refusé aussi.
    git(clone, "reset", "-q", "--hard", "HEAD~1")
    (clone / "src" / "taux.py").write_text("def taux(x):\n    return x * 3\n")
    sortie = prive(clone, "publier", "FEAT-001", "--sans-verdict", "--sans-pousser", env=env)
    assert sortie.returncode == 2 and "pas commité" in sortie.stderr
    # Un identifiant qui n'en est pas un.
    sortie = prive(clone, "publier", "feat-1", env=env)
    assert sortie.returncode == 2 and "clé du ticket" in sortie.stderr


def test_publier_sans_contrat_rejoue_une_correction_sans_ephor(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path)
    poser(clone)
    env = environnement(tmp_path)
    git(clone, "switch", "-qc", "fix/typo")
    (clone / "src" / "index.js").write_text("console.log('salut') // ok\n")
    git(clone, "commit", "-qam", "typo")
    git(clone, "switch", "-q", "main")
    sortie = prive(clone, "publier", "--sans-contrat", "fix/typo", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert git(clone, "log", "--format=%s", "origin/main..origin/fix/typo").splitlines() == ["typo"]
    assert not [
        f
        for f in git(clone, "ls-tree", "-r", "--name-only", "origin/fix/typo").split()
        if f.startswith(".ephor/")
    ]
    sortie = prive(clone, "publier", "--sans-contrat", "main", env=env)
    assert sortie.returncode == 2 and "branche principale" in sortie.stderr


def test_adopter_prepare_la_branche_que_l_equipe_relira(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path)
    poser(clone)
    env = environnement(tmp_path)
    approuver(clone, env)
    sortie = prive(clone, "adopter", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert "GESTE 0 pour l'équipe" in sortie.stdout
    arbre = git(clone, "ls-tree", "-r", "--name-only", "adoption-ephor").split()
    for attendu in (
        "PROGRESS.md",
        "STEER.md",
        ".ephor/judge.py",
        ".ephor/projet/prive.mk",
        "tasks/FEAT-001/rouge.json",
        ".claude/settings.json.ephor-new",
    ):
        assert attendu in arbre, attendu
    ignore = git(clone, "show", "adoption-ephor:.gitignore").splitlines()
    assert ".verify/" in ignore and "/PROGRESS.md" not in ignore and "node_modules/" in ignore
    assert git(clone, "show", "adoption-ephor:CLAUDE.md").endswith("@AGENTS.md\n")
    assert (
        git(clone, "show", "adoption-ephor:.claude/settings.json")
        == '{"permissions":{"allow":["Bash(npm test)"]}}\n'
    )
    pousse = subprocess.run(
        ["git", "push", "-q", "--no-verify", "-u", "origin", "adoption-ephor"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert pousse.returncode == 0, pousse.stderr
    # Sans --no-verify, la garde refuse ce qui porte Ephor et n'est pas encore sur le serveur :
    # c'est le seul cas où la contourner est voulu.
    git(clone, "branch", "-f", "adoption-essai", "main")
    refus = subprocess.run(
        ["git", "push", "-q", "origin", "adoption-essai"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
    )
    assert refus.returncode != 0 and "marqueur Ephor-prive" in refus.stderr


def test_retirer_refuse_d_abandonner_un_contrat_pas_publie(tmp_path: Path) -> None:
    clone = depot_partage(tmp_path)
    poser(clone)
    env = environnement(tmp_path)
    approuver(clone, env)
    sortie = prive(clone, "retirer", env=env)
    assert (
        sortie.returncode == 2
        and "contrat pas publié" in sortie.stderr
        and "FEAT-001 : contrat approuvé" in sortie.stderr
    )
    assert (clone / ".ephor").exists()
    sortie = prive(clone, "etat", env=env)
    assert (
        sortie.returncode == 0
        and "FEAT-001 : contrat approuvé" in sortie.stdout
        and "garde pre-push : en place" in sortie.stdout
    )
    sortie = prive(clone, "retirer", "--force", env=env)
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert git(clone, "rev-parse", "main") == git(clone, "rev-parse", "origin/main")
    assert not (clone / ".ephor").exists()


def test_le_doctor_voit_les_garde_fous_dans_les_reglages_locaux(tmp_path: Path) -> None:
    """Le diagnostic criait « INERTE » dès que .claude/settings.json ne déclarait pas les
    garde-fous : en mode privé, ils sont dans settings.local.json, et ils tournent."""
    clone = depot_partage(tmp_path)
    poser(clone)
    shim = tmp_path / "bin"
    shim.mkdir()
    uv = shim / "uv"
    uv.write_text(
        "#!/usr/bin/env bash\n"
        "if [ \"$1\" = --version ]; then echo 'uv 0.0.0'; fi\n"
        "cat >/dev/null 2>&1; exit 0\n"
    )
    uv.chmod(0o755)
    env = dict(environnement(tmp_path), PATH=f"{shim}{os.pathsep}{os.environ['PATH']}")
    doctor = subprocess.run(
        ["bash", ".ephor/scripts/doctor.sh"],
        cwd=clone,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert "déclarés dans .claude/settings.local.json seulement" in doctor.stdout
    assert "INERTE" not in doctor.stdout


@pytest.mark.parametrize("geste", ["publier", "adopter", "retirer", "etat"])
def test_les_gestes_refusent_un_clone_qui_n_est_pas_en_mode_prive(
    tmp_path: Path, geste: str
) -> None:
    """Sans le script dans le clone, c'est celui d'Ephor qui est lancé depuis le clone ; il
    doit dire que rien n'est posé, pas planter."""
    clone = depot_partage(tmp_path)
    args = ("FEAT-001",) if geste == "publier" else ()
    sortie = subprocess.run(
        ["bash", str(SCRIPT), geste, *args],
        cwd=clone,
        env=environnement(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert sortie.returncode in (0, 2), sortie.stdout + sortie.stderr
    texte = sortie.stdout + sortie.stderr
    assert (
        "pas en mode privé" in texte or "rien d'Ephor" in texte or "aucun commit d'Ephor" in texte
    )
