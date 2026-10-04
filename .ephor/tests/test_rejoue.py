"""Sur quelles versions de Claude Code, d'opencode et de Podman Ephor a-t-il été rejoué ?

Le moteur le dit (`.ephor/rejoue.txt`), le journal des versions le répète dans l'entrée de la
version en place, et `make doctor` compare les versions du poste à celles-là. Avant : la 2.1.287
de Claude Code a cassé trois choses en une journée (skill, sentinelle, question réseau du juge),
et rien ne disait sur quelle version Ephor avait été mesuré, ni que celle du poste différait.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from conftest import ROOT

REJOUE = ROOT / ".ephor" / "rejoue.txt"
CHANGELOG = ROOT / ".ephor" / "CHANGELOG.md"
VERSION = ROOT / ".ephor" / "VERSION"
LIGNE = re.compile(r"^(\S+) (\d+(?:\.\d+)+) (.+)$")
ENTREE = re.compile(r"^## (\d+\.\d+\.\d+) — .+$", re.M)


def outils_rejoues() -> dict[str, tuple[str, str]]:
    """{binaire: (version, nom lisible)} lu dans `.ephor/rejoue.txt`."""
    outils: dict[str, tuple[str, str]] = {}
    for brute in REJOUE.read_text(encoding="utf-8").splitlines():
        if not brute.strip() or brute.startswith("#"):
            continue
        trouve = LIGNE.match(brute)
        assert trouve, f"ligne illisible dans .ephor/rejoue.txt : {brute!r}"
        outils[trouve.group(1)] = (trouve.group(2), trouve.group(3))
    return outils


def version_d_ephor() -> str:
    trouve = re.match(r"Ephor (\S+)", VERSION.read_text(encoding="utf-8"))
    assert trouve
    return trouve.group(1)


# ------------------------------------------------------------------ le moteur et le journal


def test_le_moteur_dit_sur_quelles_versions_il_a_ete_rejoue() -> None:
    """Les trois outils, chacun avec une version complète et un nom lisible."""
    outils = outils_rejoues()
    assert {"claude", "opencode", "podman"} <= set(outils), outils
    assert outils["claude"][1] == "Claude Code"
    assert outils["podman"][1] == "Podman"


def test_le_journal_des_versions_nomme_les_versions_rejouees() -> None:
    """La dernière entrée versionnée du journal est celle de la version en place, et elle nomme
    chaque outil de `.ephor/rejoue.txt` avec sa version : la règle, depuis la 1.5.4. Une entrée
    « En préparation » peut la précéder, et seulement la précéder."""
    texte = CHANGELOG.read_text(encoding="utf-8")
    titres = re.findall(r"^## (.+)$", texte, re.M)
    assert titres, "aucune entrée dans le journal des versions"
    for rang, titre in enumerate(titres):
        if titre == "En préparation":
            assert rang == 0, "« En préparation » n'a sa place qu'en tête du journal"
    entrees = ENTREE.findall(texte)
    assert entrees[0] == version_d_ephor(), (
        f"le journal commence à {entrees[0]}, .ephor/VERSION dit {version_d_ephor()}"
    )
    debut = ENTREE.search(texte)
    assert debut
    fin = ENTREE.search(texte, debut.end())
    derniere = texte[debut.start() : fin.start() if fin else len(texte)]
    for binaire, (version, nom) in outils_rejoues().items():
        assert f"{nom} {version}" in derniere, (
            f"l'entrée {entrees[0]} du journal ne dit pas « {nom} {version} » ({binaire}) :"
            " chaque version nomme les versions des trois outils sur lesquelles elle a été rejouée"
        )


def test_le_journal_annonce_la_regle_en_tete() -> None:
    """La règle est dite là où on écrit une entrée, pas seulement dans un test."""
    entete = CHANGELOG.read_text(encoding="utf-8").split("## ", 1)[0]
    assert "rejoue.txt" in entete and "make doctor" in entete


# ------------------------------------------------------------------------------ make doctor


def projet_avec_doctor(tmp_path: Path) -> Path:
    projet = tmp_path / "projet"
    (projet / ".ephor" / "scripts").mkdir(parents=True)
    for fichier in (".ephor/scripts/doctor.sh", ".ephor/rejoue.txt", ".ephor/VERSION"):
        shutil.copy(ROOT / fichier, projet / fichier)
    return projet


def outils_factices(
    tmp_path: Path, claude: str | None, opencode: str | None = None, podman: str | None = None
) -> Path:
    """Un dossier d'outils à mettre devant le PATH : chaque `--version` répond comme le vrai
    (`2.1.287 (Claude Code)`, `1.18.33`, `podman version 6.1.2`)."""
    shim = tmp_path / "bin"
    shim.mkdir(parents=True, exist_ok=True)
    for nom, version, forme in (
        ("claude", claude, "%s (Claude Code)"),
        ("opencode", opencode, "%s"),
        ("podman", podman, "podman version %s"),
    ):
        if version is None:
            continue
        (shim / nom).write_text(
            "#!/bin/sh\n"
            f'if [ "$1" = "--version" ]; then echo "{forme % version}"; exit 0; fi\n'
            "exit 0\n",
            encoding="utf-8",
        )
        (shim / nom).chmod(0o755)
    return shim


def doctor(
    projet: Path, shim: Path, *, path_minimal: bool = False
) -> subprocess.CompletedProcess[str]:
    base = "/usr/bin:/bin" if path_minimal else os.environ["PATH"]
    env = dict(os.environ, PATH=f"{shim}:{base}")
    return subprocess.run(
        ["bash", ".ephor/scripts/doctor.sh"], cwd=projet, capture_output=True, text=True, env=env
    )


def blocages(sortie: str) -> list[str]:
    return sorted(ligne for ligne in sortie.splitlines() if "[FAIL]" in ligne)


def test_le_doctor_dit_ok_sur_la_version_rejouee(tmp_path: Path) -> None:
    outils = outils_rejoues()
    projet = projet_avec_doctor(tmp_path)
    shim = outils_factices(
        tmp_path, outils["claude"][0], outils["opencode"][0], outils["podman"][0]
    )
    sortie = doctor(projet, shim).stdout
    ephor = version_d_ephor()
    for binaire, (version, nom) in outils.items():
        attendu = f"[ OK ] {nom} {version} : la version sur laquelle Ephor {ephor} a été rejoué"
        assert attendu in sortie, (binaire, sortie)


def test_le_doctor_previent_sur_une_version_plus_recente_sans_bloquer(tmp_path: Path) -> None:
    """Le cas du 02/10/2026 : Claude Code sorti depuis la mesure. Un avertissement qui nomme ce
    qui peut casser et le geste qui tranche, jamais un [FAIL] : les blocages sont les mêmes
    qu'avec la version rejouée."""
    outils = outils_rejoues()
    projet = projet_avec_doctor(tmp_path)
    ephor = version_d_ephor()
    temoin = doctor(projet, outils_factices(tmp_path / "a", outils["claude"][0])).stdout
    sortie = doctor(projet, outils_factices(tmp_path / "b", "2.1.999")).stdout
    assert (
        f"[WARN] Claude Code 2.1.999 sur ce poste, plus récent que la version rejouée avec"
        f" Ephor {ephor} ({outils['claude'][0]})" in sortie
    ), sortie
    assert "refuser une forme de skill" in sortie
    assert "rejouez un /feature de bout en bout" in sortie
    assert f"echo '{ephor} claude 2.1.999' >> .ephor/projet/rejoue.txt" in sortie
    assert blocages(sortie) == blocages(temoin), (
        "une version différente avertit, elle ne bloque pas"
    )


def test_le_doctor_compare_nombre_par_nombre(tmp_path: Path) -> None:
    """2.1.9 est plus ancien que 2.1.287, pas plus récent : une comparaison de texte se
    trompait. Et une version plus ancienne nomme ce qui se dégrade, sans bloquer."""
    outils = outils_rejoues()
    projet = projet_avec_doctor(tmp_path)
    sortie = doctor(projet, outils_factices(tmp_path, "2.1.9")).stdout
    assert "[WARN] Claude Code 2.1.9 sur ce poste, plus ancien que la version rejouée" in sortie, (
        sortie
    )
    assert outils["claude"][0] in sortie
    assert "se dégrade sans bloquer" in sortie
    assert "mettez Claude Code à jour si le poste le permet" in sortie


def test_le_projet_peut_noter_qu_il_a_rejoue_cette_version(tmp_path: Path) -> None:
    """L'avertissement se tait quand le projet a noté la version dans .ephor/projet/rejoue.txt —
    pour CETTE version d'Ephor seulement : une note prise sur la précédente ne vaut plus."""
    projet = projet_avec_doctor(tmp_path)
    shim = outils_factices(tmp_path, "2.1.999")
    ephor = version_d_ephor()
    (projet / ".ephor" / "projet").mkdir()
    note = projet / ".ephor" / "projet" / "rejoue.txt"
    note.write_text("# rejoué par l'équipe\n0.0.1 claude 2.1.999\n", encoding="utf-8")
    assert "[WARN] Claude Code 2.1.999" in doctor(projet, shim).stdout
    note.write_text(f"# rejoué par l'équipe\n{ephor} claude 2.1.999\n", encoding="utf-8")
    sortie = doctor(projet, shim).stdout
    assert (
        f"[ OK ] Claude Code 2.1.999 : rejoué par le projet avec Ephor {ephor}"
        " (.ephor/projet/rejoue.txt)" in sortie
    ), sortie
    assert "[WARN] Claude Code" not in sortie


def test_le_doctor_ne_dit_rien_de_faux_sur_un_outil_absent(tmp_path: Path) -> None:
    """Un poste sans Claude Code (opencode seul, ou la CI) : rien à comparer, et la version
    rejouée reste dite."""
    outils = outils_rejoues()
    projet = projet_avec_doctor(tmp_path)
    shim = outils_factices(tmp_path, None, podman=outils["podman"][0])
    sortie = doctor(projet, shim, path_minimal=True).stdout
    assert (
        f"[ -- ] Claude Code absent de ce poste : rien à comparer (rejoué en {outils['claude'][0]})"
        in sortie
    ), sortie
    assert "[WARN] Claude Code" not in sortie


def test_le_doctor_dit_quand_le_moteur_ne_sait_pas_sur_quoi_il_a_ete_rejoue(tmp_path: Path) -> None:
    """Un moteur d'avant cette règle, ou amputé : un avertissement, et la mise à jour comme
    geste."""
    projet = projet_avec_doctor(tmp_path)
    (projet / ".ephor" / "rejoue.txt").unlink()
    sortie = doctor(projet, outils_factices(tmp_path, "2.1.287")).stdout
    assert "[WARN] rien ne dit sur quelles versions" in sortie
    assert "bootstrap_existing_repo.sh --mettre-a-jour" in sortie
