"""Scripts d'Ephor : diagnostic, installation sur dépôt existant, vérification stricte."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import operateur
from conftest import ROOT, bootstrap, depot_node, git, judge


def test_doctor_no_longer_cries_wolf_about_the_network(tmp_path: Path) -> None:
    """La version d'origine écrivait dans /dev/null, interdit sur macOS : fausse alerte."""
    shim = tmp_path / "bin"
    shim.mkdir()
    fake_uv = shim / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "uv 0.0.0"; exit 0; fi\n'
        'out=""; while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
        'case "$out" in /dev/*) echo "error: Operation not permitted" >&2; exit 2;; esac\n'
        'cat >/dev/null; echo "idna==3.10" > "$out"; exit 0\n'
    )
    fake_uv.chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
    result = subprocess.run(
        ["bash", str(ROOT / ".ephor" / "scripts" / "doctor.sh")],
        capture_output=True,
        text=True,
        env=env,
    )
    assert "uv atteint un dépôt de paquets" in result.stdout


def test_le_doctor_reconnait_un_certificat_refuse(tmp_path: Path) -> None:
    """Derrière un proxy qui déchiffre, uv joint le dépôt mais refuse son certificat
    (« invalid peer certificate: UnknownIssuer », mesuré le 26/09/2026). Le diagnostic disait
    « dépôt injoignable » avec, entre parenthèses, la dernière ligne du journal : un conseil
    d'uv ou une ligne vide. Il nomme maintenant la cause et le réglage."""
    shim = tmp_path / "bin"
    shim.mkdir()
    fake_uv = shim / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "uv 0.0.0"; exit 0; fi\n'
        "cat >/dev/null\n"
        "echo '  Caused by: invalid peer certificate: UnknownIssuer' >&2\n"
        "echo '' >&2\n"
        "echo 'hint: Consider enabling use of system TLS certificates' >&2\n"
        "exit 2\n"
    )
    fake_uv.chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
    doctor = ["bash", str(ROOT / ".ephor" / "scripts" / "doctor.sh")]
    sortie = subprocess.run(doctor, capture_output=True, text=True, env=env).stdout
    assert "refuse son certificat (invalid peer certificate: UnknownIssuer" in sortie
    assert "UV_SYSTEM_CERTS=true" in sortie
    assert "ne parvient pas à résoudre" not in sortie
    # Une autre panne garde son message, avec la ligne qui dit quelque chose.
    fake_uv.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "--version" ]; then echo "uv 0.0.0"; exit 0; fi\n'
        "cat >/dev/null\n"
        "echo '  Caused by: dns error: failed to lookup address' >&2\n"
        "echo '' >&2\n"
        "exit 2\n"
    )
    sortie = subprocess.run(doctor, capture_output=True, text=True, env=env).stdout
    assert "ne parvient pas à résoudre un paquet depuis le réseau (  Caused by: dns error" in sortie


def test_bootstrap_is_idempotent_and_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "existant"
    (target / "pkg").mkdir(parents=True)
    (target / "pkg" / "__init__.py").write_text("def f():\n    return 1\n")
    (target / ".gitignore").write_text("*.log\n")
    git(target, "init", "-q", "-b", "main")
    bootstrap(target)
    assert list(target.rglob("*.ephor-new")) == [], "aucun doublon au premier passage"
    assert (target / ".ephor" / "judge.py").exists() and (target / "Makefile").exists()
    assert (target / ".ephor" / "deploy" / "manifest.py").exists()
    assert (target / ".ephor" / "scripts" / "review.sh").exists()
    assert (target / ".ephor" / "projet" / "verify.sh").exists()
    assert (target / ".ephor" / "ruff.toml").exists(), "sans lui, le lint du projet juge Ephor"
    assert not (target / ".ephor" / "tests").exists(), "les tests d'Ephor restent chez Ephor"
    bootstrap(target)
    assert list(target.rglob("*.ephor-new")) == [], "fichiers identiques laissés tels quels"
    settings = target / ".claude" / "settings.json"
    settings.write_text("{}\n")
    bootstrap(target)
    assert [p.name for p in target.rglob("*.ephor-new")] == ["settings.json.ephor-new"]
    assert settings.read_text() == "{}\n", "un fichier modifié n'est jamais écrasé"
    ignore = (target / ".gitignore").read_text().splitlines()
    assert ignore.count(".verify/") == 1 and "*.log" in ignore
    # Le greffon Copilot doit arriver COMPLET : le relais, qui est du code, et les deux
    # fichiers que `.ephor/adapters.py` produit à la fin du bootstrap. Il a déjà manqué
    # des morceaux (quatre modules d'Ephor), et les commandes échouaient à l'usage.
    greffon = target / ".ephor" / "greffon-copilot"
    assert (greffon / "hooks" / "relais.sh").exists()
    assert (greffon / "hooks" / "hooks.json").exists(), "make adapters a échoué au bootstrap"
    assert (greffon / ".claude-plugin" / "plugin.json").exists()
    pose = subprocess.run(
        ["bash", str(target / ".ephor" / "scripts" / "install_copilot_plugin.sh")],
        capture_output=True,
        text=True,
        env=dict(os.environ, COPILOT_PLUGINS=str(tmp_path / "greffons")),
        timeout=120,
        check=False,
    )
    assert pose.returncode == 0, pose.stdout + pose.stderr


def test_verify_strict_turns_missing_tools_into_failure(tmp_path: Path) -> None:
    (tmp_path / ".ephor" / "projet").mkdir(parents=True)
    shutil.copy(ROOT / ".ephor" / "projet" / "verify.sh", tmp_path / ".ephor" / "projet")
    # La CI pose VERIFY_STRICT=1 pour tout le travail : le mode souple doit être demandé
    # ici, sinon ce test ne passe que sur un poste (vu au premier passage en CI, 25/09/2026).
    souple = {cle: valeur for cle, valeur in os.environ.items() if cle != "VERIFY_STRICT"}
    lax = subprocess.run(
        ["bash", ".ephor/projet/verify.sh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=souple,
    )
    assert lax.returncode == 0 and "GREEN PARTIEL" in lax.stdout
    strict = subprocess.run(
        ["bash", ".ephor/projet/verify.sh"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=dict(os.environ, VERIFY_STRICT="1"),
    )
    assert strict.returncode == 1 and "VERDICT: RED" in strict.stdout


def test_les_faux_secrets_des_tests_restent_exemptes_dans_tout_l_historique() -> None:
    """Le garde anti-secret a besoin de faux secrets pour être testé, et gitleaks les
    trouve. Ils étaient exemptés par EMPREINTE — or une empreinte contient le commit, et
    gitleaks relit tout l'historique : chaque commit touchant le fichier de test en
    fabriquait une nouvelle, et `make sec` est resté rouge sans que personne le voie.
    Ils sont maintenant exemptés par valeur. Ce test est là pour que ça ne recommence pas."""
    if shutil.which("gitleaks") is None:
        pytest.skip("gitleaks absent : optionnel, Ephor ne s'écroule pas sans lui")
    scan = subprocess.run(
        ["gitleaks", "detect", "--no-banner", "--redact", "-v"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert scan.returncode == 0, scan.stdout + scan.stderr


def test_le_doctor_nomme_le_greffon_copilot_manquant(tmp_path: Path) -> None:
    """L'angle mort le plus coûteux d'Ephor : sous Copilot, un greffon non installé ne
    se voit nulle part — l'agent travaille sans garde-fou et rien ne le dit. Une journée
    y est passée. Le diagnostic le nomme maintenant, dans les trois états."""

    def doctor(greffons: Path) -> str:
        run = subprocess.run(
            ["bash", str(ROOT / ".ephor" / "scripts" / "doctor.sh")],
            capture_output=True,
            text=True,
            env=dict(os.environ, COPILOT_PLUGINS=str(greffons)),
            timeout=180,
            check=False,
        )
        return run.stdout

    assert "greffon Copilot non installé" in doctor(tmp_path / "vide")

    greffon = tmp_path / "pose" / "ephor"
    (greffon / "hooks").mkdir(parents=True)
    shutil.copy(ROOT / ".ephor" / "greffon-copilot" / "hooks" / "relais.sh", greffon / "hooks")
    modele = (ROOT / ".ephor" / "greffon-copilot" / "hooks" / "hooks.json").read_text()
    liste = modele.replace("__RACINE_DU_GREFFON__", str(greffon))
    (greffon / "hooks" / "hooks.json").write_text(liste, encoding="utf-8")
    (greffon / "depots.txt").write_text("", encoding="utf-8")
    assert "n'y est pas déclaré" in doctor(tmp_path / "pose")

    (greffon / "depots.txt").write_text(str(ROOT) + "\n", encoding="utf-8")
    assert "greffon Copilot installé et ce dépôt déclaré" in doctor(tmp_path / "pose")

    # Le relais à jour, mais une liste de gardes d'avant : une garde ne tournait pas.
    ancienne = liste.replace("budget.sh checkpoint.sh", "checkpoint.sh")
    assert ancienne != liste
    (greffon / "hooks" / "hooks.json").write_text(ancienne, encoding="utf-8")
    assert "n'enchaîne pas la liste de garde-fous du dépôt" in doctor(tmp_path / "pose")
    (greffon / "hooks" / "hooks.json").write_text(liste, encoding="utf-8")

    (greffon / "hooks" / "relais.sh").write_text("vieille version\n", encoding="utf-8")
    assert "ne correspond plus" in doctor(tmp_path / "pose")

    # Un relais périmé se signale aussi dans un dépôt NON déclaré : celui d'avant la 0.9 n'y
    # reconnaît pas les garde-fous, et laisse passer au lieu de refuser.
    (greffon / "depots.txt").write_text("", encoding="utf-8")
    assert "ne correspond plus" in doctor(tmp_path / "pose")


def test_le_doctor_dit_si_les_roles_ont_leur_modele_sous_opencode(tmp_path: Path) -> None:
    """Sous opencode, un sous-agent sans modèle prend celui de l'agent qui l'appelle : la doc
    disait « rien à faire », et scout comme planner tournaient sur le modèle de la session."""
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "opencode").write_text("#!/bin/sh\nexit 0\n")
    (shim / "opencode").chmod(0o755)
    conf = tmp_path / "conf" / "opencode"
    conf.mkdir(parents=True)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}", XDG_CONFIG_HOME=str(conf.parent))
    env.pop("OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX", None)
    doctor = ["bash", str(ROOT / ".ephor" / "scripts" / "doctor.sh")]
    sortie = subprocess.run(doctor, capture_output=True, text=True, env=env, check=False).stdout
    assert "aucun modèle réglé sur ce poste pour scout, planner, evaluator" in sortie
    (conf / "opencode.json").write_text(
        '{\n  "model": "p/standard",\n  "agent": {\n    "scout": {"model": "p/rapide"},\n'
        '    "planner": { "model": "p/fort" }\n  }\n}\n',
        encoding="utf-8",
    )
    (conf / "agents").mkdir()
    (conf / "agents" / "evaluator.md").write_text("---\nmodel: p/fort\n---\n", encoding="utf-8")
    env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"] = "16000"
    sortie = subprocess.run(doctor, capture_output=True, text=True, env=env, check=False).stdout
    assert "un modèle réglé pour scout, planner et evaluator" in sortie
    assert "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX=16000" in sortie


def test_le_doctor_se_lance_avant_setup(tmp_path: Path) -> None:
    """La recette « nouveau projet » lance `make doctor` AVANT `make setup` : un `.venv`
    absent la faisait finir sur `make: *** [doctor] Error 1` (audit du 29/09/2026)."""
    projet = tmp_path / "neuf"
    (projet / ".ephor" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / ".ephor" / "scripts" / "doctor.sh", projet / ".ephor" / "scripts")
    (projet / "pyproject.toml").write_text('[project]\nname = "neuf"\n', encoding="utf-8")
    sortie = subprocess.run(
        ["bash", ".ephor/scripts/doctor.sh"], cwd=projet, capture_output=True, text=True
    ).stdout
    assert "environnement pas encore installé" in sortie
    assert "[FAIL] environnement" not in sortie


def test_doctor_refuses_a_python_that_is_too_old(tmp_path: Path) -> None:
    """Le motif `3.[2-9]*` attrapait 3.9 : le python3 d'un Mac passait pour un ≥ 3.11,
    exactement le blocage que .ephor/docs/10 annonce en premier sur un poste d'entreprise."""
    shim = tmp_path / "bin"
    shim.mkdir()
    fake = shim / "python3"
    fake.write_text('#!/usr/bin/env bash\necho "3.9"\n')
    fake.chmod(0o755)
    env = dict(os.environ, PATH=f"{shim}:{os.environ['PATH']}")
    out = subprocess.run(
        ["bash", str(ROOT / ".ephor" / "scripts" / "doctor.sh")],
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    assert "[ OK ] python3 système 3.9" not in out, "3.9 annoncé comme ≥ 3.11"
    assert "le projet exige ≥ 3.11" in out


def test_doctor_exits_nonzero_when_it_found_a_blocker(tmp_path: Path) -> None:
    """Le diagnostic sortait toujours en succès, [FAIL] à l'écran compris : tout ce qui
    l'enchaîne croyait la voie libre."""
    faux = tmp_path / "depot"
    (faux / ".ephor" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / ".ephor" / "scripts" / "doctor.sh", faux / ".ephor" / "scripts")
    result = subprocess.run(
        ["bash", str(faux / ".ephor" / "scripts" / "doctor.sh")], capture_output=True, text=True
    )
    assert "[FAIL]" in result.stdout
    assert result.returncode != 0, "un blocage trouvé doit se voir au code de retour"


def test_sec_admits_when_the_secret_scanner_did_not_run() -> None:
    """`make sec` écrivait « gitleaks absent (best effort) » dans un fichier et rendait
    VERT : deux secrets indexés, aucun mot à l'écran."""
    outil = shutil.which("gitleaks")
    chemin = os.environ["PATH"]
    if outil:
        dossier = str(Path(outil).parent)
        chemin = ":".join(p for p in chemin.split(":") if p != dossier)
    env = dict(os.environ, PATH=chemin, VERIFY_STRICT="0")
    souple = subprocess.run(["make", "sec"], cwd=ROOT, capture_output=True, text=True, env=env)
    assert "PARTIEL" in souple.stdout, "un scanner qui n'a pas tourné doit se dire"
    assert souple.returncode == 0
    strict = subprocess.run(
        ["make", "sec"], cwd=ROOT, capture_output=True, text=True, env=dict(env, VERIFY_STRICT="1")
    )
    assert strict.returncode != 0, "en CI stricte, un contrôle non exécuté est un échec"


def test_bootstrap_names_the_two_files_that_make_it_work(tmp_path: Path) -> None:
    """Le script n'écrase rien, et c'est pour ça qu'il laissait Ephor INERTE sans le
    dire : les deux seuls fichiers mis de côté sur un dépôt vivant — le Makefile et les
    réglages — sont exactement les deux qui le font marcher. 68 fichiers posés, aucune
    commande, aucune garde déclarée, et un bilan qui résumait ça par « 1 à fusionner »."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    sortie = bootstrap(cible)
    assert "GESTE 0" in sortie
    assert "Makefile.ephor-new" in sortie or "Makefile" in sortie
    assert "AUCUN n'est déclaré" in sortie, "l'illusion de protection doit être nommée"
    assert "à fusionner : .claude/settings.json Makefile" in sortie
    # La sonde proposée doit discriminer pour de vrai : les gardes déclarées ou non. Lancer
    # la garde à la main ne le dit plus : elle lit ses zones dans le moteur, fusion ou pas.
    reglages = cible / ".claude" / "settings.json"
    assert "grep -c '.ephor/hooks/write-guard.sh' .claude/settings.json" in sortie
    assert ".ephor/hooks/write-guard.sh" not in reglages.read_text(encoding="utf-8")
    shutil.copy(cible / ".claude" / "settings.json.ephor-new", reglages)
    assert ".ephor/hooks/write-guard.sh" in reglages.read_text(encoding="utf-8")
    charge = '{"tool_name":"Edit","tool_input":{"path":".ephor/judge.py"}}'
    garde = subprocess.run(
        [str(cible / ".ephor" / "hooks" / "write-guard.sh")],
        input=charge,
        capture_output=True,
        text=True,
        cwd=cible,
        timeout=30,
        check=False,
    )
    assert garde.returncode == 2, "la zone du moteur est refusée"


def test_bootstrap_does_not_blame_the_repo_for_its_own_changes(tmp_path: Path) -> None:
    """Le bilan annonçait « 0 à fusionner » alors que des fichiers suivis avaient bougé,
    sans les nommer. Il les nomme — mais seulement les SIENS, pas le travail en cours."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    (cible / "src" / "index.js").write_text("console.log('travail en cours')\n")
    sortie = bootstrap(cible)
    ligne = [ligne for ligne in sortie.splitlines() if "fichiers suivis modifiés" in ligne]
    assert ligne, "les fichiers suivis modifiés par l'installation doivent être nommés"
    assert ".gitignore" in ligne[0]
    assert "index.js" not in ligne[0], "le travail en cours n'est pas de notre fait"


def test_no_closed_loop_on_a_repo_that_is_not_python(tmp_path: Path) -> None:
    """`make setup` refuse sans pyproject.toml, et le diagnostic renvoyait vers `make
    setup` : on tournait en rond, sur le cas le plus courant en entreprise."""
    cible = tmp_path / "noderepo"
    depot_node(cible)
    sortie = bootstrap(cible)
    assert "n'est pas un projet Python" in sortie
    diagnostic = subprocess.run(
        ["bash", ".ephor/scripts/doctor.sh"], cwd=cible, capture_output=True, text=True, check=False
    )
    assert "ce dépôt n'est pas un projet Python" in diagnostic.stdout
    assert "l'étape la plus souvent sautée" not in diagnostic.stdout


def test_the_starting_recipes_are_runnable() -> None:
    """La recette de démarrage doit tourner telle quelle : `cp -R` emporte le `.git`, donc
    sans `rm -rf .git` d'abord, `git init` est ignoré, le commit initial n'existe jamais, et
    le nouveau projet hérite de l'historique d'Ephor et d'un environnement périmé."""
    readme = (ROOT / "README.md").read_text()
    guide = (ROOT / ".ephor" / "docs" / "10-installation.md").read_text()
    for texte in (readme, guide):
        recette = next(bloc for bloc in texte.split("```") if "cp -R" in bloc)
        assert recette.index("rm -rf .git") < recette.index("git init"), (
            "l'ordre compte : sans effacer le .git d'Ephor, git init est ignoré"
        )
    assert ".ephor/docs/10-installation.md" in readme, "la porte d'entrée doit mener au poste bridé"


def test_lint_ne_compte_pas_les_lignes_de_politesse() -> None:
    """`make lint` annonçait « 1 finding(s) » quand ruff dit « All checks passed! » : il
    comptait les LIGNES du fichier de sortie. Bénin, et faux dans le sens rassurant — mais
    c'est exactement le genre de chiffre qu'un agent recopie dans son compte rendu sans le
    regarder. Le compte doit valoir le nombre de remontées réelles, propre ou non."""
    resultat = subprocess.run(
        ["make", "lint"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    annonce = re.search(r"lint: (\d+) finding", resultat.stdout)
    assert annonce, resultat.stdout + resultat.stderr
    lignes = (ROOT / ".verify" / "lint.txt").read_text(encoding="utf-8").splitlines()
    reelles = [ligne for ligne in lignes if re.match(r"^[^ ].*:\d+:", ligne)]
    assert int(annonce.group(1)) == len(reelles)
    if not reelles:
        assert "All checks passed" in "\n".join(lignes), "sortie propre attendue"


def test_arch_compte_les_contrats_qui_tiennent() -> None:
    """`make arch` annonçait « 0 contrat(s) » quand deux tenaient : il cherchait « KEPT » en
    début de ligne, là où import-linter l'écrit à la fin."""
    resultat = subprocess.run(
        ["make", "arch"], cwd=ROOT, capture_output=True, text=True, check=False
    )
    annonce = re.search(r"arch: (\d+) contrat\(s\), (\d+) rompu", resultat.stdout)
    assert annonce, resultat.stdout + resultat.stderr
    sortie = (ROOT / ".verify" / "arch.txt").read_text(encoding="utf-8")
    bilan = re.search(r"Contracts: (\d+) kept, (\d+) broken", sortie)
    assert bilan, sortie
    assert int(annonce.group(1)) == int(bilan.group(1)) + int(bilan.group(2)) > 0
    assert int(annonce.group(2)) == int(bilan.group(2))


def test_en_ci_le_juge_vient_de_la_branche_cible() -> None:
    """Le juge était lu dans la branche jugée : une branche qui réécrivait .ephor/judge.py
    pour toujours dire « oui » passait la porte « qui fait foi ». Reproduit le 24/09/2026.
    Le juge de la CI vient maintenant de la branche cible, que la branche ne touche pas."""
    fichiers = {
        "GitLab": ROOT / ".gitlab-ci.yml",
        "GitHub": ROOT / ".github" / "workflows" / "pre-queue.yml",
    }
    for forge, chemin in fichiers.items():
        texte = chemin.read_text(encoding="utf-8")
        assert "git archive" in texte and "juge-de-base/.ephor/judge.py" in texte, forge
        assert "python3 .ephor/judge.py" not in texte, f"{forge} lance le juge de la branche jugée"


def test_un_scan_qui_ne_peut_pas_tourner_le_dit() -> None:
    """Sans SNYK_TOKEN, le scan des agents sortait vert sans rien scanner, toutes les nuits :
    le « ce qui ne peut pas tourner dit que tout va bien » qu'Ephor combat ailleurs."""
    github = (ROOT / ".github" / "workflows" / "post-merge.yml").read_text(encoding="utf-8")
    assert "::warning title=scan des agents non lancé::" in github
    gitlab = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    assert 'non_lance="$non_lance agents(pas de SNYK_TOKEN)"' in gitlab
    assert 'if [ -n "$non_lance" ]; then exit 3; fi' in gitlab


def test_verify_ne_croit_pas_une_suite_coupee_avant_la_fin() -> None:
    """`os._exit(0)` dans le code testé faisait sortir pytest en 0 avant le premier test,
    et `make verify` annonçait GREEN. Une suite allée au bout écrit son rapport."""
    texte = (ROOT / ".ephor" / "projet" / "verify.sh").read_text(encoding="utf-8")
    assert "--junitxml" in texte and "aucun rapport" in texte


def test_le_doctor_nomme_une_declaration_de_references_cassee(tmp_path: Path) -> None:
    """Une variable pas exportée sur le poste, et toute écriture hors du dépôt était
    refusée sans que rien ne l'ait annoncé (vu le 24/09/2026). Le diagnostic le dit."""
    copie = tmp_path / "depot"
    (copie / ".ephor" / "scripts").mkdir(parents=True)
    (copie / ".ephor" / "projet").mkdir()
    shutil.copy(ROOT / ".ephor" / "scripts" / "doctor.sh", copie / ".ephor" / "scripts")
    shutil.copy(ROOT / ".ephor" / "refs.py", copie / ".ephor")
    (copie / ".ephor" / "projet" / "reference-repos.json").write_text(
        '{"depots": [{"nom": "metrics", "chemin": "${VARIABLE_ABSENTE_DU_POSTE}/m"}]}',
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "VARIABLE_ABSENTE_DU_POSTE"}
    run = subprocess.run(
        ["bash", str(copie / ".ephor" / "scripts" / "doctor.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
        check=False,
    )
    assert "[FAIL] déclaration des dépôts de référence inutilisable" in run.stdout
    assert "VARIABLE_ABSENTE_DU_POSTE" in run.stdout
    assert run.returncode != 0


def test_la_ci_apres_fusion_lance_les_tests_d_ephor_et_peut_echouer() -> None:
    """Mesuré le 25/09/2026 sur GitHub : le travail d'après fusion ne lançait que les six
    tests du modèle, jamais ceux d'Ephor, et chaque étape finissait par « || echo » — vert
    quoi qu'il arrive. Une coche verte qui ne peut pas rougir ne dit rien."""
    avale = re.compile(r"^[^#]*\|\|\s*(echo|true)\b", re.M)
    fichiers = {
        "GitLab": ROOT / ".gitlab-ci.yml",
        "GitHub après fusion": ROOT / ".github" / "workflows" / "post-merge.yml",
        "GitHub avant fusion": ROOT / ".github" / "workflows" / "pre-queue.yml",
    }
    for forge, chemin in fichiers.items():
        texte = chemin.read_text(encoding="utf-8")
        assert not avale.findall(texte), f"{forge} avale un échec"
        assert ".ephor/tests" in texte or "make ephor-test" in texte, f"{forge} : tests d'Ephor"
    github = fichiers["GitHub après fusion"].read_text(encoding="utf-8")
    porte = github[github.index("  ephor:") : github.index("  signaux:")]
    assert "make ephor-test" in porte and "continue-on-error" not in porte
    gitlab = fichiers["GitLab"].read_text(encoding="utf-8")
    deep = gitlab[gitlab.index("\ndeep:") : gitlab.index("\nsignaux:")]
    assert ".ephor/tests" in deep and "allow_failure" not in deep


def test_gitleaks_a_la_meme_version_partout() -> None:
    """L'action GitHub prenait sa version par défaut (8.24.3), qui ne lit pas `[[allowlists]]` :
    le scan complet de la nuit remontait les faux secrets des tests (26/09/2026)."""
    image = (ROOT / ".ephor" / "deploy" / "Containerfile.ci").read_text(encoding="utf-8")
    version = re.search(r"gitleaks:v(\d+\.\d+\.\d+)", image)
    assert version
    for nom in ("pre-queue.yml", "post-merge.yml"):
        texte = (ROOT / ".github" / "workflows" / nom).read_text(encoding="utf-8")
        assert f"GITLEAKS_VERSION: {version.group(1)}" in texte, nom


def test_la_ci_gitlab_sert_aussi_les_projets_equipes() -> None:
    """Le `.gitlab-ci.yml` d'Ephor est déposé tel quel dans un projet `controles`, qui sert
    tous les projets équipés (.ephor/docs/12-gitlab.md). Ceux-ci n'ont pas les tests d'Ephor :
    lancés sans condition, pytest y sortait en 4, et `verify` rougissait partout."""
    texte = (ROOT / ".gitlab-ci.yml").read_text(encoding="utf-8")
    lignes = [
        ligne for ligne in texte.splitlines() if ".ephor/tests" in ligne and "pytest" in ligne
    ]
    assert len(lignes) == 2
    assert all("[ -d .ephor/tests ]" in ligne for ligne in lignes), lignes


def test_la_ci_github_envoie_vraiment_le_dossier_cache_des_rapports() -> None:
    """Mesuré le 25/09/2026 : upload-artifact ignore les dossiers cachés par défaut. Le
    rapport `.verify/` joint à chaque passage de CI était vide depuis toujours, et
    « if-no-files-found: ignore » le taisait."""
    for nom in ("pre-queue.yml", "post-merge.yml"):
        texte = (ROOT / ".github" / "workflows" / nom).read_text(encoding="utf-8")
        envois = texte.split("actions/upload-artifact@")[1:]
        for envoi in envois:
            etape = envoi.split("\n      - ")[0]
            if "path: .verify/" in etape:
                assert "include-hidden-files: true" in etape, nom
                assert "if-no-files-found: ignore" not in etape, nom


def test_le_doctor_nomme_ce_qui_detourne_make_et_coupe_les_hooks(tmp_path: Path) -> None:
    """Un GNUmakefile fait mentir `make judge` ; un réglage local peut couper tous les hooks.
    Et la limite du terminal est dite là où l'opérateur regarde."""
    copie = tmp_path / "depot"
    (copie / ".ephor" / "scripts").mkdir(parents=True)
    (copie / ".claude").mkdir()
    shutil.copy(ROOT / ".ephor" / "scripts" / "doctor.sh", copie / ".ephor" / "scripts")
    shutil.copy(ROOT / ".ephor" / "zones.txt", copie / ".ephor")
    (copie / "Makefile").write_text("verify:\n\t@true\n")
    git(copie, "init", "-q", "-b", "main")

    def diagnostic() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(copie / ".ephor" / "scripts" / "doctor.sh")],
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )

    propre = diagnostic().stdout
    assert "liste des zones du moteur" in propre and "pas une frontière" in propre
    assert "GNUmakefile" not in propre and "makefile présent" not in propre
    (copie / "GNUmakefile").write_text("judge:\n\t@echo VÉRIFIÉ\n")
    (copie / ".claude" / "settings.local.json").write_text('{"disableAllHooks": true}')
    sale = diagnostic()
    assert "[FAIL] GNUmakefile présent" in sale.stdout
    assert "disableAllHooks" in sale.stdout
    assert sale.returncode != 0


def test_le_bac_a_sable_de_claude_code_est_la_frontiere_du_terminal(tmp_path: Path) -> None:
    """ADR 0008, mesuré le 25/09/2026 : le bac à sable applique les refus Edit(...) à tout ce
    que le terminal lance, détour compris. Sans `allowUnsandboxedCommands: false`, un clic
    suffit à en sortir ; sans le cache d'uv, `make verify` casse ; un refus de tout .git/
    empêche git d'écrire ses objets."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    bac = reglages["sandbox"]
    assert bac["enabled"] is True and bac["allowUnsandboxedCommands"] is False
    assert bac["autoAllowBashIfSandboxed"] is False, "la liste blanche d'Ephor reste la règle"
    assert "~/.cache/uv" in bac["filesystem"]["allowWrite"]
    refus = reglages["permissions"]["deny"]
    assert "Edit(.git/config)" in refus and "Edit(.git/hooks/**)" in refus
    assert "Edit(.git/**)" not in refus, "git n'écrirait plus ses objets dans le bac à sable"
    # Le poste qui le coupe est nommé par le diagnostic.
    copie = tmp_path / "depot"
    (copie / ".ephor" / "scripts").mkdir(parents=True)
    (copie / ".claude").mkdir()
    shutil.copy(ROOT / ".ephor" / "scripts" / "doctor.sh", copie / ".ephor" / "scripts")
    shutil.copy(ROOT / ".ephor" / "zones.txt", copie / ".ephor")
    shutil.copy(ROOT / ".claude" / "settings.json", copie / ".claude")
    git(copie, "init", "-q", "-b", "main")
    doctor = ["bash", str(copie / ".ephor" / "scripts" / "doctor.sh")]
    # Un poste Mac, où rien n'est à installer. Sous Linux, le diagnostic lance bubblewrap pour
    # de vrai : la machine Ubuntu de GitHub, sans bubblewrap ni socat, répondait « SAUTÉ »
    # (26/09/2026). Ce cas-là a son propre test, juste en dessous.
    mac = tmp_path / "mac"
    mac.mkdir()
    vrai_uname = shutil.which("uname")
    (mac / "uname").write_text(
        f'#!/bin/sh\nif [ "$1" = "-s" ]; then echo Darwin; else exec {vrai_uname} "$@"; fi\n'
    )
    (mac / "uname").chmod(0o755)
    env = dict(os.environ, PATH=f"{mac}{os.pathsep}{os.environ['PATH']}")
    actif = subprocess.run(
        doctor, capture_output=True, text=True, env=env, timeout=180, check=False
    )
    assert "[ OK ] bac à sable de Claude Code" in actif.stdout
    (copie / ".claude" / "settings.local.json").write_text('{"sandbox": {"enabled": false}}')
    coupe = subprocess.run(
        doctor, capture_output=True, text=True, env=env, timeout=180, check=False
    )
    assert "[WARN] bac à sable de Claude Code coupé" in coupe.stdout


def test_le_doctor_voit_le_bac_a_sable_saute_sous_linux(tmp_path: Path) -> None:
    """Sous Linux, sans bubblewrap et socat, ou quand bubblewrap ne peut pas démarrer, Claude
    Code saute son bac à sable avec un simple avertissement. Le diagnostic le dit. Le poste
    Linux est simulé : un PATH qui reprend tous les outils du vrai, sauf bwrap et socat."""
    copie = tmp_path / "depot"
    (copie / ".ephor" / "scripts").mkdir(parents=True)
    (copie / ".claude").mkdir()
    shutil.copy(ROOT / ".ephor" / "scripts" / "doctor.sh", copie / ".ephor" / "scripts")
    shutil.copy(ROOT / ".ephor" / "zones.txt", copie / ".ephor")
    shutil.copy(ROOT / ".claude" / "settings.json", copie / ".claude")
    git(copie, "init", "-q", "-b", "main")
    poste = tmp_path / "bin"
    poste.mkdir()
    for dossier in map(Path, os.environ["PATH"].split(os.pathsep)):
        if not dossier.is_dir():
            continue
        for outil in dossier.iterdir():
            cible = poste / outil.name
            if outil.name not in {"bwrap", "socat", "uname"} and not os.path.lexists(cible):
                cible.symlink_to(outil)
    vrai_uname = shutil.which("uname")
    (poste / "uname").write_text(
        f'#!/bin/sh\nif [ "$1" = "-s" ]; then echo Linux; else exec {vrai_uname} "$@"; fi\n'
    )
    (poste / "uname").chmod(0o755)
    doctor = ["bash", str(copie / ".ephor" / "scripts" / "doctor.sh")]

    def diagnostic() -> str:
        env = dict(os.environ, PATH=str(poste))
        return subprocess.run(
            doctor, capture_output=True, text=True, env=env, timeout=180, check=False
        ).stdout

    sans = diagnostic()
    assert "SAUTÉ sur ce poste (il manque bubblewrap et socat)" in sans
    assert "apt-get install bubblewrap socat" in sans and "failIfUnavailable" in sans
    assert "[ OK ] bac à sable" not in sans

    def outil(nom: str, corps: str) -> None:
        (poste / nom).write_text(f"#!/bin/sh\n{corps}\n")
        (poste / nom).chmod(0o755)

    outil("socat", "exit 0")
    outil("bwrap", 'echo "bwrap: setting up uid map: Permission denied" >&2; exit 1')
    refuse = diagnostic()
    assert "bubblewrap ne démarre pas : bwrap: setting up uid map: Permission denied" in refuse
    assert "AppArmor" in refuse and "[ OK ] bac à sable" not in refuse

    outil("bwrap", "exit 0")
    assert "[ OK ] bac à sable de Claude Code" in diagnostic()
    # Coupé par le poste : rien à essayer, le diagnostic le dit comme avant.
    (copie / ".claude" / "settings.local.json").write_text('{"sandbox": {"enabled": false}}')
    assert "[WARN] bac à sable de Claude Code coupé" in diagnostic()


def test_la_revue_trouve_le_contrat_sur_master_comme_le_juge(tmp_path: Path) -> None:
    """`make review` ne cherchait l'approbation que sur `main` : sur un dépôt dont la branche
    principale s'appelle `master`, il annonçait « c'est l'approbation qui manque » alors que
    le juge, lui, trouvait le contrat (vu le 27/09/2026)."""
    cible = tmp_path / "depot"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "master")
    git(cible, "config", "user.email", "test@example.invalid")
    git(cible, "config", "user.name", "Ephor test")
    bootstrap(cible)
    (cible / "tasks" / "T-001").mkdir(parents=True)
    (cible / "tasks" / "T-001" / "contract.json").write_text('{"risk": "modere"}\n')
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "base")
    sortie = subprocess.run(
        ["bash", ".ephor/scripts/review.sh", "T-001"],
        cwd=cible,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "approbation qui manque" not in sortie.stderr
    assert "Niveau de risque   : modere" in sortie.stdout


def test_la_revue_va_au_bout_et_dit_son_bilan(tmp_path: Path) -> None:
    """En `set -e`, un `verify` rouge arrêtait `make review` : le juge ne tournait pas, et
    rien ne résumait la revue (audit du 29/09/2026)."""
    cible = tmp_path / "depot"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    git(cible, "config", "user.email", "test@example.invalid")
    git(cible, "config", "user.name", "Ephor test")
    bootstrap(cible)
    (cible / "tasks" / "T-001").mkdir(parents=True)
    (cible / "tasks" / "T-001" / "contract.json").write_text('{"risk": "faible"}\n')
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "base")
    (cible / ".ephor" / "projet" / "verify.sh").write_text("echo rouge\nexit 1\n")
    sortie = subprocess.run(
        ["bash", ".ephor/scripts/review.sh", "T-001"],
        cwd=cible,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert sortie.returncode != 0
    assert "-> juge" in sortie.stdout, "le juge tourne même après un verify rouge"
    assert "revue : 0 étape(s) sur 2 vertes — en échec : vérification, juge" in sortie.stdout


def _projet_a_muter(racine: Path, fonctions: int) -> Path:
    """Un projet équipé en miniature : la suite de structure livrée par Ephor, le script de
    mutation du moteur, et un module de `fonctions` fonctions ajouté sur une branche de tâche,
    dont seule `f1` est testée."""
    (racine / "src" / "app").mkdir(parents=True)
    (racine / "src" / "app" / "__init__.py").write_text('"""Paquet."""\n')
    (racine / "tests").mkdir()
    shutil.copy(ROOT / "tests" / "test_architecture.py", racine / "tests")
    (racine / ".ephor" / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / ".ephor" / "scripts" / "mutants.sh", racine / ".ephor" / "scripts")
    (racine / "pyproject.toml").write_text(
        '[project]\nname = "app"\nversion = "0"\n\n[tool.mutmut]\nsource_paths = ["src/"]\n'
    )
    (racine / ".gitignore").write_text("mutants/\n.verify/\n__pycache__/\n")
    git(racine, "init", "-q", "-b", "main")
    git(racine, "config", "user.email", "test@example.invalid")
    git(racine, "config", "user.name", "Ephor test")
    git(racine, "add", "-A")
    git(racine, "commit", "-qm", "base")
    git(racine, "switch", "-qc", "task/CALC-001")
    lignes = ['"""Calculs."""', ""]
    for i in range(fonctions):
        lignes += [
            f"def f{i}(x: int) -> int:",
            f'    """f{i}."""',
            f"    if x > {i}:",
            f"        return x + {i}",
            "    return x - 1",
            "",
        ]
    (racine / "src" / "app" / "calc.py").write_text("\n".join(lignes) + "\n")
    (racine / "tests" / "test_calc.py").write_text(
        '"""Tests du calcul."""\n\nfrom app.calc import f1\n\n\n'
        'def test_f1() -> None:\n    """f1."""\n    assert f1(5) == 6\n    assert f1(0) == -1\n'
    )
    git(racine, "add", "-A")
    git(racine, "commit", "-qm", "calculs")
    return racine


def _uv_factice(dossier: Path, corps: str) -> dict[str, str]:
    """Un `uv` placé en tête du PATH : `uv run <commande>` lance <commande> telle quelle, ou
    joue le rôle que `corps` lui donne."""
    dossier.mkdir()
    uv = dossier / "uv"
    uv.write_text("#!/usr/bin/env bash\n" + corps)
    uv.chmod(0o755)
    return {**os.environ, "PATH": f"{dossier}{os.pathsep}{os.environ['PATH']}"}


def test_la_mutation_ne_bute_plus_sur_les_tests_de_structure(tmp_path: Path) -> None:
    """Vu en session réelle, rejoué le 30/09/2026 : mutmut recopie le code en empilant les
    variantes de chaque fonction (302 lignes, copie de 2 905). Les tests de structure lisaient
    la copie, échouaient (« plus de 400 lignes »), et mutmut s'arrêtait avant le premier
    mutant ; le script disait « aucun mutant exécuté », code 0, sur tout vrai module."""
    bin_venv = Path(sys.executable).parent
    if not (bin_venv / "mutmut").exists():
        pytest.skip("mutmut absent de l'environnement des tests")
    projet = _projet_a_muter(tmp_path / "projet", fonctions=20)
    env = _uv_factice(tmp_path / "bin", '[ "$1" = run ] && shift\nexec "$@"\n')
    env["PATH"] = f"{tmp_path / 'bin'}{os.pathsep}{bin_venv}{os.pathsep}{os.environ['PATH']}"
    sortie = subprocess.run(
        ["bash", ".ephor/scripts/mutants.sh"],
        cwd=projet,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert "score de mutation : 66%  (4 tués, 2 survivants sur 6)" in sortie.stdout, sortie.stdout
    # Les 19 fonctions sans test ne comptent pas dans le score : le script le dit.
    assert "114 mutant(s) qu'aucun test n'exécute" in sortie.stdout, sortie.stdout


def test_une_mutation_arretee_en_route_n_est_pas_un_vert(tmp_path: Path) -> None:
    """Quand mutmut s'arrêtait avant le premier mutant, le script le taisait (code 0) et
    accusait le code. La passe de nuit de la CI exigeait déjà son rapport de fin ; le poste,
    non. Un module sans rien de mutable reste, lui, un cas normal."""
    projet = _projet_a_muter(tmp_path / "projet", fonctions=2)
    appels = tmp_path / "appels.txt"
    env = _uv_factice(
        tmp_path / "bin",
        f'echo "$*" >> "{appels}"\n'
        'case "$MODE" in\n'
        "  mort) echo 'FAILED tests/test_calc.py::test_f1 - AssertionError'\n"
        "        echo 'failed to collect stats. runner returned 1'; exit 1 ;;\n"
        "  vide) echo 'AssertionError: Filtered for specific mutants, but nothing matches'\n"
        "        exit 1 ;;\n"
        "esac\n",
    )

    def lance(mode: str, *options: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", ".ephor/scripts/mutants.sh", *options],
            cwd=projet,
            env={**env, "MODE": mode},
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    morte = lance("mort")
    assert morte.returncode != 0, morte.stdout
    assert "ÉCHEC" in morte.stdout and "aucun score" in morte.stdout, morte.stdout
    assert "failed to collect stats" in morte.stdout, "la raison de mutmut est affichée"
    assert "test_calc.py::test_f1" in morte.stdout, "le test qui l'a arrêté est nommé"

    vide = lance("vide")
    assert vide.returncode == 0, vide.stdout
    assert "rien à muter" in vide.stdout and "ÉCHEC" not in vide.stdout, vide.stdout

    # La passe complète (celle de la CI) ne filtre rien, et passe par les mêmes règles.
    complete = lance("mort", "--tout")
    assert complete.returncode != 0 and "ÉCHEC" in complete.stdout, complete.stdout
    assert appels.read_text().splitlines()[-1] == "run mutmut run"


def test_la_ci_mute_avec_le_script_du_moteur() -> None:
    """La passe de nuit lançait mutmut elle-même : les tests de structure l'arrêtaient au
    démarrage sur tout vrai projet. Elle passe par le script, qui les écarte et dit ÉCHEC
    quand mutmut ne va pas au bout."""
    for chemin in (ROOT / ".gitlab-ci.yml", ROOT / ".github" / "workflows" / "post-merge.yml"):
        texte = chemin.read_text(encoding="utf-8")
        assert "bash .ephor/scripts/mutants.sh --tout" in texte, chemin.name
        assert "mutmut run" not in texte, chemin.name
        assert ".verify/mutants-run.log" in texte, f"{chemin.name} : le journal reste joint"


def test_une_tache_s_ouvre_dans_son_propre_dossier(tmp_path: Path) -> None:
    """Vécu en session réelle (30/09/2026) : plusieurs tâches lancées dans un même dossier se
    marchaient dessus. `make task-worktree` ouvre une tâche approuvée dans son dossier, sur sa
    branche, avec son environnement ; et le bac à sable de Claude Code y protège aussi le
    `.git/info/` du dépôt principal, qu'il laissait écrire depuis un worktree (mesuré)."""
    cible = tmp_path / "depot"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    git(cible, "config", "user.email", "test@example.invalid")
    git(cible, "config", "user.name", "Ephor test")
    bootstrap(cible)
    ignores = (cible / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/settings.local.json" in ignores.splitlines(), "propres au poste"
    # Un projet dont le .gitignore ne les ignore pas (pas encore mis à jour, ou retouché) : le
    # worktree doit rester propre quand même. Vu en CI, où Claude Code n'a pas posé son
    # ignore global comme sur le poste de développement.
    (cible / ".gitignore").write_text(ignores.replace(".claude/settings.local.json\n", ""))
    (cible / "tasks" / "FEAT-001").mkdir(parents=True)
    (cible / "tasks" / "FEAT-001" / "contract.json").write_text('{"risk": "faible"}\n')
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "FEAT-001 approuvé")
    journal = tmp_path / "uv.txt"
    env = _uv_factice(tmp_path / "bin", f'echo "uv $*" >> "{journal}"\nexit 0\n')
    # Sans la configuration git du poste, qui peut ignorer ces réglages à la place du projet.
    env |= {"GIT_CONFIG_GLOBAL": os.devnull, "XDG_CONFIG_HOME": str(tmp_path / "xdg")}

    def ouvre(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", ".ephor/scripts/worktree.sh", *args],
            cwd=cible,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    sortie = ouvre("FEAT-001")
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    dossier = tmp_path / "depot-FEAT-001"
    assert git(dossier, "branch", "--show-current").strip() == "task/FEAT-001"
    assert "environnement prêt" in sortie.stdout and "sync" in journal.read_text()
    assert f'cd "{dossier.resolve()}"' in sortie.stdout
    local = json.loads((dossier / ".claude" / "settings.local.json").read_text())
    commun = git(cible, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    systeme = local["sandbox"]["filesystem"]
    assert local["sandbox"]["enabled"] is True and f"{commun}/info" in systeme["denyWrite"]
    assert "~/.cache/uv" in systeme["allowWrite"], "le reste du bac à sable est repris"
    etat = subprocess.run(
        ["git", "-C", str(dossier), "status", "--porcelain"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert etat == "", "les réglages locaux restent hors de git"

    deux_fois = ouvre("FEAT-001")
    assert deux_fois.returncode == 2 and "existe déjà" in deux_fois.stdout
    ailleurs = ouvre("FEAT-001", str(tmp_path / "autre"))
    assert ailleurs.returncode == 2 and "task/FEAT-001" in ailleurs.stdout + ailleurs.stderr
    sans_contrat = ouvre("FEAT-002")
    assert sans_contrat.returncode == 2 and "pas de contrat approuvé" in sans_contrat.stdout
    assert not (tmp_path / "depot-FEAT-002").exists()


@pytest.mark.parametrize("variable", ["CLAUDECODE", "OPENCODE", "AI_AGENT", "COPILOT_AGENT"])
def test_un_geste_de_l_operateur_refuse_sous_un_agent(
    tmp_path: Path, variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Les refus des réglages comparent des débuts de commande : un autre ordre des mots, un
    script ou `bash -c` passaient (audit du 29/09/2026). Chaque outil d'agent pose une
    variable que tout ce qu'il lance hérite ; le geste de l'opérateur la lit et refuse."""
    cible = tmp_path / "depot"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    git(cible, "config", "user.email", "test@example.invalid")
    git(cible, "config", "user.name", "Ephor test")
    bootstrap(cible)
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "équipé")
    env = {**os.environ, variable: "1"}

    def lance(*commande: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(commande), cwd=cible, env=env, capture_output=True, text=True, timeout=120
        )

    for commande in (
        ("make", "task-new", "ID=FEAT-001"),
        ("make", "task-check", "ID=FEAT-001"),
        ("make", "task-worktree", "ID=FEAT-001"),
        ("make", "adapters"),
        ("make", "fetch-docs", "URL=https://example.com/doc"),
        ("make", "checkpoints-purge"),
        ("bash", "-c", "python3 .ephor/judge.py new --task-id FEAT-001"),
        ("bash", str(ROOT / ".ephor" / "scripts" / "bootstrap_existing_repo.sh"), str(cible)),
    ):
        sortie = lance(*commande)
        assert sortie.returncode != 0, commande
        assert f"({variable} est posé" in sortie.stdout + sortie.stderr, commande
    assert not (cible / "tasks" / "FEAT-001").exists()
    assert not (tmp_path / "depot-FEAT-001").exists()
    monkeypatch.setenv(variable, "1")
    assert judge.main(["rouge", "--task-id", "FEAT-001", "--repo", str(cible)]) == 2
    # Lancé par un humain, le même geste passe.
    ouvert = subprocess.run(
        ["make", "task-new", "ID=FEAT-001"],
        cwd=cible,
        env={k: v for k, v in os.environ.items() if k not in operateur.VARIABLES},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert ouvert.returncode == 0, ouvert.stdout + ouvert.stderr


def test_hors_python_la_mutation_et_l_echantillon_disent_skip(tmp_path: Path) -> None:
    """F5 (audit du 29/09/2026) : sur un dépôt Java, la mutation disait « rien à muter » et
    l'échantillon « aucun test », comme si tout allait bien. Ils ne savent lire que le
    Python : ils le disent (SKIP), jamais un vert muet."""
    projet = tmp_path / "java"
    (projet / "src" / "main" / "java").mkdir(parents=True)
    (projet / ".ephor" / "scripts").mkdir(parents=True)
    for script in ("mutants.sh", "test_sample.sh"):
        shutil.copy(ROOT / ".ephor" / "scripts" / script, projet / ".ephor" / "scripts")
    (projet / "src" / "main" / "java" / "Virement.java").write_text("class Virement {}\n")
    git(projet, "init", "-q", "-b", "main")
    git(projet, "config", "user.email", "test@example.invalid")
    git(projet, "config", "user.name", "Ephor test")
    git(projet, "add", "-A")
    git(projet, "commit", "-qm", "base")
    git(projet, "switch", "-qc", "task/VIR-001")
    (projet / "src" / "main" / "java" / "Virement.java").write_text("class Virement { int x; }\n")
    git(projet, "commit", "-qam", "travail")
    env = _uv_factice(tmp_path / "bin", "exit 0\n")

    def lance(*commande: str) -> str:
        fait = subprocess.run(
            list(commande), cwd=projet, env=env, capture_output=True, text=True, timeout=60
        )
        assert fait.returncode == 0, fait.stdout + fait.stderr
        return fait.stdout

    assert "SKIP : la mutation ne sait muter que le Python" in lance(
        "bash", ".ephor/scripts/mutants.sh"
    )
    assert "SKIP : pas de Python sous src/" in lance("bash", ".ephor/scripts/mutants.sh", "--tout")
    assert "SKIP : l'échantillon ne sait tirer que des tests pytest" in lance(
        "bash", ".ephor/scripts/test_sample.sh"
    )


def test_la_tache_s_ouvre_avec_la_ligne_de_lancement_de_son_niveau(tmp_path: Path) -> None:
    """#24 : le contrat dit quel modèle doit écrire la tâche (sa difficulté, pas son risque) ;
    le dossier de la tâche s'ouvre avec la ligne de lancement qui va avec."""
    cible = tmp_path / "depot"
    cible.mkdir()
    git(cible, "init", "-q", "-b", "main")
    git(cible, "config", "user.email", "test@example.invalid")
    git(cible, "config", "user.name", "Ephor test")
    bootstrap(cible)
    (cible / "tasks" / "FEAT-001").mkdir(parents=True)
    (cible / "tasks" / "FEAT-001" / "contract.json").write_text(
        '{"risk": "faible", "niveau": "fort", "effort": "max"}\n'
    )
    git(cible, "add", "-A")
    git(cible, "commit", "-qm", "FEAT-001 approuvé")
    env = _uv_factice(tmp_path / "bin", "exit 0\n")
    env |= {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}
    (tmp_path / "xdg" / "opencode").mkdir(parents=True)
    (tmp_path / "xdg" / "opencode" / "opencode.json").write_text(
        '{"agent": {"planner": {"model": "passerelle/fort"}}}'
    )
    sortie = subprocess.run(
        ["bash", ".ephor/scripts/worktree.sh", "FEAT-001"],
        cwd=cible,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert sortie.returncode == 0, sortie.stdout + sortie.stderr
    assert "Le contrat demande le niveau fort, effort max" in sortie.stdout
    assert "claude --model opus --effort max" in sortie.stdout
    assert "opencode --model passerelle/fort" in sortie.stdout


def test_le_bac_a_sable_d_opencode_suit_la_liste_des_zones(tmp_path: Path) -> None:
    """F3 : opencode n'avait pas la frontière du bac à sable. Les réglages de `srt` viennent
    de la liste des zones du moteur : une zone ajoutée y entre d'elle-même. Mesuré avec les
    vrais outils le 30/09/2026 (voir srt_reglages.py)."""
    script = ROOT / ".ephor" / "scripts" / "srt_reglages.py"
    env = {**os.environ, "HOME": str(tmp_path), "TMPDIR": str(tmp_path)}
    for cle in ("XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME", "EPHOR_SRT_LOCAL"):
        env.pop(cle, None)

    def reglages(**ajout: str) -> dict[str, object]:
        fait = subprocess.run(
            [sys.executable, str(script)], env={**env, **ajout}, capture_output=True, check=True
        )
        lu: dict[str, object] = json.loads(fait.stdout)
        return lu

    lu = reglages(EPHOR_SRT_DOMAINES="modeles.example depots.example")
    systeme = lu["filesystem"]
    assert isinstance(systeme, dict)
    refus = systeme["denyWrite"]
    for zone in ("./tasks", "./.ephor", "./STEER.md", "./.claude", "./Makefile", "./.git/info"):
        assert zone in refus, zone
    assert "./.opencode/plugins" in refus and "./.opencode" not in refus, "ses dépendances"
    assert "./.git" not in refus, "les commits écrivent .git"
    zones = [
        ligne
        for ligne in (ROOT / ".ephor" / "zones.txt").read_text(encoding="utf-8").splitlines()
        if ligne and not ligne.startswith(("#", "~", "!")) and ligne not in (".git/", ".opencode/")
    ]
    assert all("./" + zone.rstrip("/") in refus for zone in zones), "toute la liste"
    assert "." in systeme["allowWrite"] and systeme["denyRead"] == ["**/.env"]
    assert f"{tmp_path}/.local/share/opencode" in systeme["allowWrite"]
    reseau = lu["network"]
    assert isinstance(reseau, dict)
    assert reseau["allowedDomains"] == ["modeles.example", "depots.example"]
    assert reseau["allowLocalBinding"] is False
    local = reglages(EPHOR_SRT_LOCAL="1")["network"]
    assert isinstance(local, dict) and local["allowLocalBinding"] is True
    sans_srt = subprocess.run(
        ["bash", str(ROOT / ".ephor" / "scripts" / "opencode_bac_a_sable.sh")],
        env={**env, "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (
        sans_srt.returncode == 2
        and "npm install -g @anthropic-ai/sandbox-runtime" in sans_srt.stdout
    )
