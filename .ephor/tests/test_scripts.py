"""Scripts d'Ephor : diagnostic, installation sur dépôt existant, vérification stricte."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import ROOT, bootstrap, depot_node, git


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
    (greffon / "depots.txt").write_text("", encoding="utf-8")
    assert "n'y est pas déclaré" in doctor(tmp_path / "pose")

    (greffon / "depots.txt").write_text(str(ROOT) + "\n", encoding="utf-8")
    assert "greffon Copilot installé et ce dépôt déclaré" in doctor(tmp_path / "pose")

    (greffon / "hooks" / "relais.sh").write_text("vieille version\n", encoding="utf-8")
    assert "ne correspond plus" in doctor(tmp_path / "pose")

    # Un relais périmé se signale aussi dans un dépôt NON déclaré : celui d'avant la 0.9 n'y
    # reconnaît pas les garde-fous, et laisse passer au lieu de refuser.
    (greffon / "depots.txt").write_text("", encoding="utf-8")
    assert "ne correspond plus" in doctor(tmp_path / "pose")


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
    actif = subprocess.run(doctor, capture_output=True, text=True, timeout=180, check=False)
    assert "[ OK ] bac à sable de Claude Code" in actif.stdout
    (copie / ".claude" / "settings.local.json").write_text('{"sandbox": {"enabled": false}}')
    coupe = subprocess.run(doctor, capture_output=True, text=True, timeout=180, check=False)
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
