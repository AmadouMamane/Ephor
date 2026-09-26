"""Ephor dans un projet : un seul dossier caché, et rien qui déborde.

Avant la 0.9, installer Ephor sur un dépôt existant posait 86 fichiers sur dix-sept
entrées de sa racine, déversait dix-sept squelettes dans son `docs/`, et le lint du projet
remontait le code d'Ephor dès le premier `make verify`. Ces tests tiennent la promesse
inverse, et empêchent qu'un chemin de l'ancienne disposition survive quelque part : un
chemin mort dans une liste de refus ne protège plus rien, et ne le dit pas.
"""

from __future__ import annotations

import importlib.util
import json
import os
import py_compile
import re
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT, bootstrap, depot_node, git

# Ce qu'un dépôt gagne à sa racine, et rien d'autre : les réglages des agents, là où
# chacun les cherche (adresses imposées), le moteur, et l'interface de travail — visible
# exprès, c'est elle qu'on touche tous les jours. Le Makefile et le .gitignore ne sont
# nouveaux que si le dépôt n'en avait pas ; sinon Ephor se range à côté ou ajoute des lignes.
RACINE_ATTENDUE = {
    ".claude",
    ".opencode",
    ".github",
    ".vscode",
    ".ephor",
    "tasks",
    "PROGRESS.md",
    "STEER.md",
    "Makefile",
    ".gitignore",
}


def test_la_racine_du_projet_ne_gagne_que_l_indispensable(tmp_path: Path) -> None:
    cible = tmp_path / "depot"
    (cible / "src").mkdir(parents=True)
    (cible / "src" / "index.js").write_text("console.log('salut')\n", encoding="utf-8")
    git(cible, "init", "-q", "-b", "main")
    avant = {chemin.name for chemin in cible.iterdir()}
    sortie = bootstrap(cible)
    gagne = {chemin.name for chemin in cible.iterdir()} - avant - {".verify"}
    assert gagne == RACINE_ATTENDUE
    assert "nouveau à la racine :" in sortie, "l'encombrement se montre, il ne se découvre pas"
    assert (cible / ".verify" / "detection.md").exists(), "la détection ne traîne pas à la racine"
    caches = [c for c in (cible / ".ephor").rglob("*") if c.name.endswith("_cache")]
    assert not caches, "les caches des outils d'Ephor ne voyagent pas avec lui"


def test_les_squelettes_de_doc_sont_une_option(tmp_path: Path) -> None:
    cible = tmp_path / "noderepo"
    depot_node(cible)
    (cible / "docs").mkdir()
    (cible / "docs" / "README.md").write_text("# la doc du projet\n", encoding="utf-8")
    bootstrap(cible)
    assert [chemin.name for chemin in (cible / "docs").iterdir()] == ["README.md"], (
        "le docs/ du projet est à lui"
    )

    bootstrap(cible, "--docs")
    assert (cible / "docs" / "00-beliefs.md").exists()
    assert (cible / "docs" / "adr" / "0001-record-architecture-decisions.md").exists()
    # La doc d'Ephor voyage avec le moteur, jamais dans le docs/ du projet : ses décisions
    # (« nommer le produit Ephor ») ne sont pas celles du projet.
    assert not list((cible / "docs").glob("1[01]-*.md"))
    assert not (cible / "docs" / "adr" / "0004-le-nom-ephor.md").exists()
    assert (cible / ".ephor" / "docs" / "11-trois-agents.md").exists()


def test_une_mise_a_jour_ne_touche_jamais_aux_reglages_du_projet(tmp_path: Path) -> None:
    """`.ephor/projet/` est au projet. Relancer l'installation — c'est ainsi qu'on met Ephor
    à jour — ne le remplace pas, et n'y dépose pas non plus de .ephor-new à fusionner :
    ces fichiers-là sont FAITS pour différer de ceux d'Ephor."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    bootstrap(cible)
    verify = cible / ".ephor" / "projet" / "verify.sh"
    verify.write_text("#!/usr/bin/env bash\necho les étapes de CE projet\n", encoding="utf-8")
    sortie = bootstrap(cible)
    assert verify.read_text(encoding="utf-8").endswith("les étapes de CE projet\n")
    assert not list((cible / ".ephor" / "projet").glob("*.ephor-new"))
    assert "3 réglage(s) du projet gardé(s)" in sortie


def outil(nom: str) -> str:
    chemin = ROOT / ".venv" / "bin" / nom
    trouve = str(chemin) if chemin.exists() else shutil.which(nom)
    if trouve is None:
        pytest.skip(f"{nom} absent")
    return trouve


def test_le_lint_du_projet_ne_juge_pas_ephor(tmp_path: Path) -> None:
    """Le défaut qui rendait `make verify` rouge au premier essai : le ruff du projet lisait
    les fichiers d'Ephor avec les règles du projet. Un dossier caché ne suffit pas — ruff y
    descend, lui (mesuré). C'est `.ephor/ruff.toml` qui règle ça : pour chaque fichier, ruff
    prend la configuration la plus proche."""
    cible = tmp_path / "pyrepo"
    (cible / "src").mkdir(parents=True)
    # Des règles que le code d'Ephor ne respecte pas : docstrings partout, annotations.
    (cible / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\n\n[tool.ruff.lint]\nselect = ["D", "ANN", "F"]\n',
        encoding="utf-8",
    )
    (cible / "src" / "app.py").write_text("import os\n", encoding="utf-8")
    git(cible, "init", "-q", "-b", "main")
    bootstrap(cible)

    lint = subprocess.run(
        [outil("ruff"), "check", "--output-format=concise", "."],
        cwd=cible,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    assert any(ligne.startswith("src/app.py:") for ligne in lint), "le projet est bien lu"
    assert not [ligne for ligne in lint if ligne.startswith(".ephor/")], "\n".join(lint)

    types = subprocess.run(
        [outil("mypy"), "."], cwd=cible, capture_output=True, text=True, check=False
    ).stdout
    assert ".ephor" not in types, "mypy ne descend pas dans un dossier caché"


# Les chemins de la disposition d'avant la 0.9. Un seul survivant suffit à désarmer un
# contrôle : une liste de refus qui nomme `scripts/` ne protège plus `.ephor/scripts/`.
ANCIENS = re.compile(
    # les trois dossiers de code, à la racine
    r"(?<![\w./~$-])(?:ephor|scripts|deploy)/"
    # les mêmes derrière une variable : `$ROOT/ephor/usage.py`, `"$SRC"/scripts/`
    r"|\$\{?\w+\}?\"?/(?:ephor|scripts|deploy)/"
    # les mêmes, écrits depuis la racine : `/ephor/ @groupe/plateforme` (CODEOWNERS)
    r"|(?:^|\s)/(?:ephor|scripts|deploy)/"
    # les mêmes derrière le dépôt d'Ephor lui-même : `~/dev/ephor/scripts/bootstrap…`
    r"|(?<![.\w-])ephor/(?:scripts|deploy|tests|hooks)/"
    # garde-fous et greffon, sortis de .claude/ et de .copilot/
    r"|\.claude/hooks|\.copilot/greffon"
    # la déclaration des dépôts de référence, sortie de la racine
    r"|(?<![\w./-])(?<!/ \")reference-repos\.json"
    # la doc et les décisions d'Ephor, sorties du docs/ du projet
    r"|(?<![\w./-])docs/(?:0[7-9]|1[01]|adr/000[2-5]|CODEOWNERS\.example)"
)
# Les exceptions, toutes nommées. Les décisions datées racontent ce qui était vrai à leur
# date. `.gitleaks.toml` garde les anciens chemins parce que gitleaks relit tout
# l'historique. `.ephor/ruff.toml` écrit ses chemins depuis son propre dossier, comme ruff
# les lit. Et ce fichier doit pouvoir écrire les motifs qu'il traque.
#
# Un fichier peut aussi nommer l'ancienne disposition entre deux repères,
# `>>> ancienne disposition` et `<<< ancienne disposition` : c'est le cas de l'installateur,
# qui doit reconnaître un dépôt équipé en 0.8 pour en reprendre les réglages.
DISPENSES = (
    ".ephor/docs/adr/",
    ".gitleaks.toml",
    ".ephor/ruff.toml",
    ".ephor/tests/test_rangement.py",
    "uv.lock",
)
# Un seul ancien chemin reste lu, et exprès : le relais Copilot, posé une fois par poste,
# sert aussi les projets pas encore passés à la 0.9, dont les garde-fous sont à l'ancienne
# adresse.
PERMIS = {(".ephor/greffon-copilot/hooks/relais.sh", ".claude/hooks")}


def test_aucun_chemin_de_l_ancienne_disposition() -> None:
    fichiers = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    restes = []
    for nom in fichiers:
        if nom.startswith(DISPENSES) or not (ROOT / nom).is_file():
            continue
        try:
            texte = (ROOT / nom).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        dedans = False
        for numero, ligne in enumerate(texte.splitlines(), 1):
            if ">>> ancienne disposition" in ligne or "<<< ancienne disposition" in ligne:
                dedans = ">>>" in ligne
                continue
            if dedans:
                continue
            if any((nom, m.group()) not in PERMIS for m in ANCIENS.finditer(ligne)):
                restes.append(f"{nom}:{numero}: {ligne.strip()[:90]}")
    assert not restes, "chemin de l'ancienne disposition :\n" + "\n".join(restes)


def test_le_motif_attrape_bien_ce_qu_il_doit() -> None:
    """Un garde qui ne mord jamais passe pour un garde satisfait. Il a déjà manqué
    `"$ROOT/ephor/usage.py"` : le hook de budget ne mesurait plus rien, en silence."""
    for vieux in (
        "python3 ephor/judge.py",
        'python3 "$ROOT/ephor/usage.py"',
        'for f in "$SRC"/scripts/*.sh',
        "bash ~/dev/ephor/scripts/bootstrap_existing_repo.sh",
        "bash .claude/hooks/steer.sh",
        "Edit(reference-repos.json)",
        "voir docs/11-trois-agents.md",
        "/deploy/ @groupe/livraison",
    ):
        assert ANCIENS.search(vieux), vieux
    for neuf in (
        "python3 .ephor/judge.py",
        'python3 "$ROOT/.ephor/usage.py"',
        "~/.cache/ephor/judge",
        "refs/ephor/checkpoints",
        "bash ~/dev/ephor/.ephor/scripts/bootstrap_existing_repo.sh",
        ".ephor/projet/reference-repos.json",
        "voir .ephor/docs/11-trois-agents.md",
        "docs/06-runbook.md",
        '"docs/CODEOWNERS"',  # un emplacement que GitLab lit, pas l'ancien modèle d'Ephor
        "/.ephor/deploy/ @groupe/livraison",
        "cd ~/dev/ephor/ && make",
    ):
        assert not ANCIENS.search(neuf), neuf


def test_le_juge_tourne_dans_un_depot_node_equipe(tmp_path: Path) -> None:
    """Le cas le plus courant, de bout en bout : un dépôt Node, qui n'ignore pas `__pycache__/`.
    Le juge importait un module voisin, Python déposait son cache compilé dans le dossier
    d'Ephor, et le juge prenait ce cache pour une retouche de ses propres fichiers : toute
    tâche était REFUSÉE — ou PÉRIMÉE, quand le cache naissait pendant la vérification.
    Le défaut datait d'avant la 0.9 ; seul un vrai dépôt, pas celui d'Ephor, le montrait."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    bootstrap(cible)
    (cible / "src" / "taux.py").write_text("def taux(x):\n    return x\n", encoding="utf-8")
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "id": "FEAT-001",
                "objective": "Le taux double la valeur qu'on lui donne.",
                "risk": "faible",
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
        ),
        encoding="utf-8",
    )
    juge = [sys.executable, str(cible / ".ephor" / "judge.py")]
    subprocess.run(
        [*juge, "new", "--task-id", "FEAT-001", "--from", str(plan)], cwd=cible, check=True
    )
    (cible / "tasks" / "FEAT-001" / "check_c1.py").write_text(
        "import sys\nsys.path.insert(0, 'src')\nfrom taux import taux\nassert taux(3) == 6\n",
        encoding="utf-8",
    )
    rouge = subprocess.run(
        [*juge, "rouge", "--task-id", "FEAT-001", "--workdir", str(tmp_path / "travail")],
        cwd=cible,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rouge.returncode == 0, rouge.stdout + rouge.stderr
    assert "rouge  C1" in rouge.stdout and "AssertionError" in rouge.stdout
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "Ephor et contrat FEAT-001 approuvé")
    git(cible, "switch", "-qc", "task/FEAT-001")
    (cible / "src" / "taux.py").write_text("def taux(x):\n    return x * 2\n", encoding="utf-8")

    verdict = subprocess.run(
        [*juge, "check", "--task-id", "FEAT-001", "--workdir", str(tmp_path / "travail")],
        cwd=cible,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert verdict.returncode == 0, verdict.stdout + verdict.stderr
    assert not list((cible / ".ephor").rglob("*.pyc")), "le juge écrit dans son propre dossier"


def test_les_options_se_placent_ou_l_on_veut(tmp_path: Path) -> None:
    """`--docs` n'était lu qu'en premier : placé après le chemin, il était ignoré en silence."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    script = ROOT / ".ephor" / "scripts" / "bootstrap_existing_repo.sh"
    subprocess.run(["bash", str(script), str(cible), "--docs"], capture_output=True, check=True)
    assert (cible / "docs" / "00-beliefs.md").exists()
    inconnue = subprocess.run(
        ["bash", str(script), "--doc", str(cible)], capture_output=True, text=True, check=False
    )
    assert inconnue.returncode == 2 and "option inconnue" in inconnue.stderr


def test_une_mise_a_jour_depuis_la_0_8_garde_les_reglages_du_projet(tmp_path: Path) -> None:
    """Relancer l'installation est la façon de mettre Ephor à jour. Sur un dépôt équipé en
    0.8, elle posait les valeurs par défaut dans `.ephor/projet/` sans regarder les anciens
    réglages : les dépôts de référence, les exceptions et le scanner maison disparaissaient
    en silence, et deux garde-fous cessaient de protéger (reproduit par une relecture)."""
    voisin = tmp_path / "voisin"
    voisin.mkdir()
    cible = tmp_path / "equipe-en-0-8"
    depot_node(cible)
    (cible / "ephor").mkdir()
    (cible / "ephor" / "judge.py").write_text("# juge 0.8\n", encoding="utf-8")
    (cible / "ephor" / "fuites-autorisees.txt").write_text("src/a.py:3\n", encoding="utf-8")
    (cible / "ephor" / "sast.sh").write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
    (cible / "reference-repos.json").write_text(
        json.dumps({"depots": [{"nom": "voisin", "chemin": str(voisin)}]}), encoding="utf-8"
    )
    (cible / "scripts").mkdir()
    (cible / "scripts" / "verify.sh").write_text(
        'cd "$(dirname "$0")/.." || exit 1\npython3 ephor/personal_data.py\necho mes étapes\n',
        encoding="utf-8",
    )
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "Ephor 0.8")

    sortie = bootstrap(cible)
    projet = cible / ".ephor" / "projet"
    assert json.loads((projet / "reference-repos.json").read_text())["depots"][0]["nom"] == "voisin"
    assert (projet / "fuites-autorisees.txt").read_text() == "src/a.py:3\n"
    assert (projet / "sast.sh").read_text().endswith("exit 1\n")
    verify = (projet / "verify.sh").read_text()
    assert '"$(dirname "$0")/../.."' in verify and "python3 .ephor/personal_data.py" in verify
    assert "mes étapes" in verify
    assert "ANCIENNE DISPOSITION" in sortie and "git rm -r ephor" in sortie

    # La garde des dépôts de référence protège toujours le voisin déclaré.
    charge = {"tool_name": "Write", "tool_input": {"file_path": str(voisin / "x.py")}}
    garde = subprocess.run(
        [str(cible / ".ephor" / "hooks" / "refs-guard.sh")],
        input=json.dumps(charge),
        capture_output=True,
        text=True,
        env=dict(os.environ, CLAUDE_PROJECT_DIR=str(cible)),
        timeout=30,
        check=False,
    )
    assert garde.returncode == 2, garde.stderr


def test_un_cache_compile_plante_ne_remplace_pas_un_module_du_juge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le projet ignore `.ephor/**/__pycache__/` : sans quoi le juge prenait ses propres
    caches pour une retouche. Mais un cache ignoré n'est pas dans le patch, et Python le
    LIT : un `refs.cpython-*.pyc` fabriqué à la main s'exécutait à la place de `refs.py`,
    dans le juge, sans laisser de trace dans `git status` (reproduit par une relecture)."""
    copie = tmp_path / ".ephor"
    copie.mkdir()
    for module in ("judge.py", "refs.py", "repo_map.py"):
        shutil.copy(ROOT / ".ephor" / module, copie / module)
    source = copie / "refs.py"
    temoin = tmp_path / "PLANTE"
    piege = f"open({str(temoin)!r}, 'w').close()\ndef commits(repo):\n    return {{}}\n"
    faux = tmp_path / "faux.py"
    faux.write_text(piege + "#" * (source.stat().st_size - len(piege.encode()) - 1) + "\n")
    # Le juge, déjà appelé par d'autres tests dans ce processus, y a posé son préfixe.
    monkeypatch.setattr(sys, "pycache_prefix", None)
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir()
    py_compile.compile(str(faux), cfile=str(cache))
    entete = bytearray(cache.read_bytes())  # la date et la taille de la VRAIE source
    entete[8:16] = struct.pack("<II", int(source.stat().st_mtime), source.stat().st_size)
    cache.write_bytes(bytes(entete))

    code = f"import sys; sys.path.insert(0, {str(copie)!r}); import judge; judge.reference_state"
    code += f"(__import__('pathlib').Path({str(tmp_path)!r}))"
    subprocess.run([sys.executable, "-c", code], check=True, timeout=60)
    assert not temoin.exists(), "le cache planté s'est exécuté à la place du vrai module"
