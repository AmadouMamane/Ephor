#!/usr/bin/env python3
"""Juge final d'une tâche d'agent : contrat, périmètre, copie propre, preuves.

Il rejoue la commande unique du projet (`.ephor/projet/verify.sh`) puis les critères du
contrat. Bibliothèque standard uniquement et Python 3.9 ou plus récent : il doit tourner
sur un Mac d'entreprise comme en CI.

Ce qu'il garantit : le verdict ne vient pas de l'agent. Le patch est rejoué dans une
copie propre du commit de base, clonée à part, sans lien d'écriture vers votre dépôt ; les
fichiers de contrôle et les tests d'acceptation ne peuvent pas être modifiés ; un critère
doit prouver qu'il est allé au bout, pas seulement sortir en 0 ; les clés d'API ne sont
pas transmises aux tests ; toute modification pendant la vérification invalide le résultat.

Ce qu'il ne garantit pas : en local, il ne confine pas un agent malveillant sur l'hôte.
L'exécution en conteneur (`--runtime podman`) réduit ce risque ; la porte qui fait foi
est celle de la CI, qui part d'un clone frais.
"""

from __future__ import annotations

import argparse
import ast
import atexit
import bisect
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any
from xml.sax.saxutils import escape as xml_escape

SCHEMA_VERSION = 1
ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]{2,50}$")
# Un lot de tâches, pour les ranger par fonctionnalité : `tasks/<lot>/<ID>/` (#35). En
# minuscules, là où un identifiant de tâche est en majuscules : les deux ne se confondent pas.
LOT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
CRITERION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,20}$")
RISKS = ("faible", "modere", "eleve")
DEFAULT_TIMEOUT = 900
DEFAULT_IMAGE = "ghcr.io/astral-sh/uv:python3.12-bookworm-slim"
# Une étape qui écrit plus que ça ne teste plus rien : elle remplit le disque. Une suite
# pytest bavarde tient largement dans 64 Mo.
MAX_LOG_BYTES = 64 * 1024 * 1024
MAX_REPORT_BYTES = 8_000_000
# Les rapports de tests s'écrivent HORS de la copie jugée : un dossier à part, monté à part
# dans le conteneur.
PREUVES_CONTENEUR = "/ephor-preuves"
# Un chemin SOUS un fichier ne peut pas exister : Python n'y lit ni n'y écrit aucun cache.
SANS_CACHE = "/dev/null/ephor-sans-cache"

# Zones de contrôle : jamais modifiables par le patch d'une tâche, quel que soit le contrat.
# La liste vit dans `.ephor/zones.txt`, à côté du juge, et c'est la même pour les gardes, la
# sentinelle et la garde de la CI : cinq listes écrites à la main avaient fini par diverger
# (25/09/2026). Seules les entrées du dépôt concernent le juge. Sans ce fichier, le juge ne
# juge pas : une liste vide ne protégerait rien.
ZONES = Path(__file__).resolve().with_name("zones.txt")


def lire_zones(fichier: Path = ZONES) -> tuple[str, ...]:
    """Les zones du dépôt : ni commentaire, ni exception, ni chemin du poste."""
    try:
        lignes = fichier.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ()
    return tuple(
        ligne.strip()
        for ligne in lignes
        if ligne.strip() and not ligne.strip().startswith(("#", "!", "~"))
    )


PROTECTED = lire_zones()


def zones() -> tuple[str, ...]:
    if not PROTECTED:
        raise JudgeError(f"liste des zones de contrôle introuvable ou vide : {ZONES}")
    return PROTECTED


# État tenu par l'agent, jamais une preuve : toujours dans le périmètre, jamais protégé.
ALWAYS_ALLOWED = ("PROGRESS.md",)

# Amorce des preuves écrites en Python. Sans elle, un critère est « satisfait » dès que le
# processus sort en 0 — et le code jugé, importé par la preuve, peut provoquer cette sortie
# avant la moindre assertion. Un `sys.exit(0)` en tête de module suffisait à faire déclarer
# vainqueur un code faux : reproduit le 23 septembre 2026.
#
# Ici la preuve n'est plus crue sur son code de sortie : elle doit avoir été jouée jusqu'au
# bout, et le dire en imprimant un jeton tiré au hasard à chaque exécution. Un `sys.exit(0)`
# écrit dans la preuve elle-même reste permis — c'est sa fin à elle ; venu d'ailleurs, il
# coupe le jeton, donc le critère échoue.
#
# Portée honnête : la preuve et le code jugé partagent un processus. Un code qui IMITERAIT
# volontairement le jeton passerait encore — mais il faudrait l'écrire noir sur blanc dans
# le patch, là où un lecteur humain le voit. C'est la différence entre une erreur qui passe
# et une fraude qui se signale.
#
# Le jeton ne couvrait que les preuves lancées comme SCRIPT. Or la forme par défaut d'un
# critère, celle de `make task-new` et d'EXEMPLE-001, est `uv run pytest ...` : jugée sur
# son seul code de sortie, une ligne `os._exit(0)` ajoutée au code faisait dire « VÉRIFIÉ »
# à un code jamais corrigé — reproduit le 24 septembre 2026. Une preuve pytest doit
# maintenant rendre un rapport de tests complet :
# voir `verifier_rapport_pytest`.
AMORCE_PREUVE = """
import runpy, sys

jeton, chemin = sys.argv[1], sys.argv[2]
sys.argv = sys.argv[2:]
try:
    runpy.run_path(chemin, run_name="__main__")
except SystemExit as sortie:
    trace = sys.exc_info()[2]
    while trace is not None and trace.tb_next is not None:
        trace = trace.tb_next
    depuis = trace.tb_frame.f_code.co_filename if trace is not None else ""
    if depuis != chemin or sortie.code not in (None, 0):
        raise
print(jeton)
"""
# L'ordre de lecture d'un contrat : repris tel quel quand on en écrit un.
CONTRACT_ORDER = (
    "id",
    "objective",
    "risk",
    "niveau",
    "effort",
    "allowed_paths",
    "criteria",
    "setup",
    "verify",
    "timeout_seconds",
    "budget_tokens",
)
CONTRACT_KEYS = set(CONTRACT_ORDER)
# Le niveau du modèle qui implémente (Claude Code : haiku, sonnet, opus), et son effort.
NIVEAUX = ("rapide", "standard", "fort")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
REQUIRED_KEYS = {"id", "objective", "risk", "allowed_paths", "criteria"}
CRITERION_KEYS = {
    "id",
    "description",
    "check",
    "max_duration_seconds",
    "stdin",
    "expected_output",
    "returncode",
    "rapport_junit",
    "preuves",
}
CRITERION_REQUIRED = {"id", "description", "check"}

# Variables transmises aux commandes vérifiées. Tout le reste (clés LiteLLM, Anthropic,
# OpenAI, jetons GitLab/GitHub, identifiants cloud...) est retiré.
BASE_ENV = {
    "PATH",
    "HOME",
    "USER",
    "LOGNAME",
    "SHELL",
    "TMPDIR",
    "TERM",
    "LANG",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
}
SETUP_ONLY_ENV = {
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "PIP_INDEX_URL",
    "PIP_EXTRA_INDEX_URL",
    "PIP_TRUSTED_HOST",
}
SECRET_HINT = re.compile(
    r"TOKEN|SECRET|PASSW|API_?KEY|ACCESS_KEY|PRIVATE|CREDENTIAL|AUTH|SESSION|COOKIE", re.I
)
INDEX_CREDENTIAL = re.compile(r"^UV_INDEX_[A-Z0-9_]+_(USERNAME|PASSWORD)$")
# Un identifiant écrit DANS l'adresse d'un index (`https://moi:jeton@jfrog/...`), la forme la
# plus courante : `UV_DEFAULT_INDEX` et ses voisines passaient telles quelles à toutes les
# étapes, tests compris (audit du 29/09/2026). Hors installation, l'adresse reste, sans lui.
IDENTIFIANTS_URL = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^/@\s]+@")


def sans_identifiants(valeur: str) -> str:
    """L'adresse sans son `utilisateur:mot-de-passe@`, pour chaque adresse de la valeur."""
    return IDENTIFIANTS_URL.sub(r"\1", valeur)


UV_HOST_ONLY = {"UV_PROJECT_ENVIRONMENT", "UV_PYTHON_INSTALL_DIR", "UV_TOOL_DIR", "UV_CACHE_DIR"}

GIT_HARDENING = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
)


class JudgeError(Exception):
    """Erreur qui empêche de rendre un verdict (contrat, périmètre, dépôt)."""

    def __init__(self, messages: list[str] | str) -> None:
        self.messages = [messages] if isinstance(messages, str) else list(messages)
        super().__init__("; ".join(self.messages))


@dataclass(frozen=True)
class Criterion:
    id: str
    description: str
    check: tuple[str, ...]
    max_duration_seconds: float | None = None
    # Sortie exacte attendue, comparée de l'EXTÉRIEUR du processus : le code
    # jugé ne peut pas la fabriquer sans produire le bon comportement, ou l'écrire en dur.
    stdin: str | None = None
    expected_output: str | None = None
    returncode: int = 0
    # Rapport JUnit d'un autre outil de test (Maven, Gradle, Jest, Go…) : la commande l'écrit
    # dans le dossier `{rapport}` que le juge lui donne, HORS de la copie jugée, et chaque test
    # désigné doit y avoir tourné et réussi (audit du 29/09/2026, F5).
    rapport_junit: tuple[str, ...] | None = None
    # Les fichiers de ces tests : Maven désigne un test par son nom, pas par son chemin, et le
    # juge ne saurait pas les protéger de l'agent sans cette liste.
    preuves: tuple[str, ...] = ()


@dataclass(frozen=True)
class Contract:
    id: str
    objective: str
    risk: str
    allowed_paths: tuple[str, ...]
    criteria: tuple[Criterion, ...]
    setup: tuple[str, ...] | None
    verify: tuple[str, ...]
    timeout_seconds: int
    budget_tokens: int | None
    # Le modèle et l'effort qui implémentent la tâche : sa difficulté, pas son risque (une
    # tâche sans enjeu peut être difficile). Proposés par le planner, approuvés avec le contrat.
    niveau: str | None = None
    effort: str | None = None
    # Où vit la tâche : `tasks/<ID>`, ou `tasks/<lot>/<ID>` quand elle est rangée par
    # fonctionnalité (#35). Ses critères et sa trace rouge parlent de `tasks/<ID>/…` : ce nom
    # canonique est traduit vers ce dossier, et une tâche rangée ne change donc pas de contrat.
    dossier: str = ""

    @property
    def ou(self) -> str:
        return self.dossier or f"tasks/{self.id}"


# --------------------------------------------------------------------------- utilitaires


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def engine_hash() -> str:
    """Empreinte cryptographique du moteur de vérification (.ephor/*.py et ses zones)."""
    ephor_dir = Path(__file__).resolve().parent
    parts: list[bytes] = []
    for path in sorted([*ephor_dir.glob("*.py"), *ephor_dir.glob("zones.txt")]):
        parts.append(path.name.encode("utf-8") + b":" + path.read_bytes())
    return hashlib.sha256(b"".join(parts)).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def git_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
            "LC_ALL": "C",
        }
    )
    if extra:
        env.update(extra)
    return env


def git(
    repo: Path, *args: str, env: dict[str, str] | None = None, entree: bytes | None = None
) -> bytes:
    """Git durci : aucun hook du dépôt, aucun moniteur de fichiers."""
    try:
        result = subprocess.run(
            ["git", *GIT_HARDENING, "-C", str(repo), *args],
            input=entree,
            capture_output=True,
            check=True,
            timeout=300,
            env=git_env(env),
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.decode(errors="replace").strip().splitlines()
        raise JudgeError(f"git {args[0]} a échoué : {detail[-1] if detail else exc}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"git {args[0]} impossible : {exc}") from exc
    return result.stdout


# Un objet git trouvé : « <hash> <sorte> <taille> ». Tout le reste (« <nom> missing »,
# « ambiguous ») dit qu'il n'y a rien là, même quand le nom demandé contient des espaces.
OBJET_TROUVE = re.compile(rb"^([0-9a-f]{40}|[0-9a-f]{64}) (blob|tree|commit|tag) ([0-9]+)$")


class LecteurGit:
    """Un `git cat-file` qui reste ouvert : une question, une ligne, et pas un processus.

    Cent tâches coûtaient à `make taches` plus de trois mille processus git, un par jeton de
    commande et par tâche : 38 s, et autant à chaque `make task-check` (mesuré le 30/09/2026).
    Les réponses sont celles de `git cat-file -t` et de `git show <arbre>:<chemin>`."""

    def __init__(self, repo: Path, contenus: bool) -> None:
        self.contenus = contenus
        self.identite = identite_du_dossier(repo)
        self.processus = subprocess.Popen(
            [
                "git",
                *GIT_HARDENING,
                "-C",
                str(repo),
                "cat-file",
                "--batch" if contenus else "--batch-check",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=git_env(),
        )

    def demande(self, nom: str) -> tuple[str, str, bytes] | None:
        """(hash, sorte, contenu) de l'objet `nom`, ou None s'il n'existe pas. OSError si le
        lecteur ne répond plus : l'appelant repasse alors par un processus à la fois."""
        entree, sortie = self.processus.stdin, self.processus.stdout
        if entree is None or sortie is None or "\n" in nom:
            raise OSError("question impossible à poser à git cat-file")
        entree.write(nom.encode("utf-8", "surrogateescape") + b"\n")
        entree.flush()
        ligne = sortie.readline()
        if not ligne.endswith(b"\n"):
            raise OSError("git cat-file s'est arrêté")
        trouve = OBJET_TROUVE.match(ligne[:-1])
        if trouve is None:
            return None
        objet, sorte, taille = trouve.group(1).decode(), trouve.group(2).decode(), trouve.group(3)
        if not self.contenus:
            return objet, sorte, b""
        contenu = sortie.read(int(taille))
        sortie.read(1)  # le saut de ligne qui suit chaque contenu
        return objet, sorte, contenu

    def ferme(self) -> None:
        with suppress(OSError, subprocess.TimeoutExpired):
            if self.processus.stdin is not None:
                self.processus.stdin.close()
            self.processus.wait(timeout=5)
        if self.processus.poll() is None:
            self.processus.kill()


# Les lecteurs ouverts, du plus ancien au plus récent. Au-delà de quelques-uns, le plus ancien
# se ferme : un processus qui juge beaucoup de dépôts (la suite de tests d'Ephor) garderait
# sinon des centaines de processus git, et leurs descripteurs de fichiers.
_LECTEURS: dict[tuple[str, bool], LecteurGit | None] = {}
LECTEURS_OUVERTS = 8


def identite_du_dossier(repo: Path) -> tuple[int, int] | None:
    """Ce qui distingue un dossier d'un autre créé au même chemin : un dossier effacé puis
    recréé (la copie propre du juge, d'un jugement à l'autre) ne garde pas son lecteur."""
    try:
        etat = repo.stat()
    except OSError:
        return None
    return etat.st_dev, etat.st_ino


def _fermer_les_lecteurs() -> None:
    for lecteur in _LECTEURS.values():
        if lecteur is not None:
            lecteur.ferme()
    _LECTEURS.clear()


atexit.register(_fermer_les_lecteurs)


def interroge(repo: Path, nom: str, contenus: bool = False) -> tuple[str, str, bytes] | None:
    """Une question à un `git cat-file` qui reste ouvert pour ce dépôt. OSError quand il n'y
    en a pas : l'appelant fait alors comme avant, un processus par question."""
    cle = (str(repo), contenus)
    connu = cle in _LECTEURS
    lecteur = _LECTEURS.pop(cle, None)
    if lecteur is not None and lecteur.identite != identite_du_dossier(repo):
        lecteur.ferme()
        lecteur, connu = None, False
    if not connu:
        with suppress(OSError):
            lecteur = LecteurGit(repo, contenus)
    _LECTEURS[cle] = lecteur  # remis au bout : le plus récent
    while len(_LECTEURS) > LECTEURS_OUVERTS:
        ancien = _LECTEURS.pop(next(iter(_LECTEURS)))
        if ancien is not None:
            ancien.ferme()
    if lecteur is None:
        raise OSError("pas de lecteur git")
    try:
        return lecteur.demande(nom)
    except (OSError, ValueError):
        # Un lecteur qui a cessé de répondre ne resservira pas : les questions suivantes
        # repassent par un processus chacune, comme avant.
        lecteur.ferme()
        _LECTEURS[cle] = None
        raise OSError("git cat-file ne répond plus") from None


def contenu_a(repo: Path, arbre: str, chemin: str) -> bytes:
    """Le contenu d'un fichier à cet arbre git, comme `git show <arbre>:<chemin>` ; JudgeError
    s'il n'y est pas."""
    nom = f"{arbre}:{chemin}"
    try:
        trouve = interroge(repo, nom, contenus=True)
    except OSError:
        return git(repo, "show", nom)
    if trouve is None or trouve[1] != "blob":
        raise JudgeError(f"git show a échoué : {chemin} absent de {arbre}")
    return trouve[2]


def safe_path(value: str) -> bool:
    """Chemin relatif simple : ni absolu, ni `..`, ni joker, ni caractère de contrôle."""
    posix = PurePosixPath(value)
    return (
        bool(value)
        and not posix.is_absolute()
        and bool(posix.parts)
        and not any(part in {".", ".."} for part in value.split("/") if part)
        and not any(char in value for char in "*?[]\\\x00\n\r")
    )


def matches(path: str, rule: str) -> bool:
    return path == rule.rstrip("/") or (rule.endswith("/") and path.startswith(rule))


def is_protected(path: str) -> bool:
    return any(matches(path, rule) for rule in zones())


def relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _sans_doublon(paires: list[tuple[str, Any]]) -> dict[str, Any]:
    objet: dict[str, Any] = {}
    for cle, valeur in paires:
        if cle in objet:
            raise JudgeError(f"clé en double dans le JSON : {cle}")
        objet[cle] = valeur
    return objet


def lire_json(texte: str) -> Any:
    """JSON sans clé en double. `json` garde la DERNIÈRE en silence : un contrat relu avec
    `"allowed_paths"` deux fois ne dirait pas au lecteur celle qui s'applique."""
    return json.loads(texte, object_pairs_hook=_sans_doublon)


# --------------------------------------------------------------------------- preuves

# Ce qui peut précéder la commande qui fait le travail : `uv run`, `poetry run`, une option.
LANCEURS = {"uv", "uvx", "run", "poetry", "pipenv", "pdm", "rye", "exec"}
PYTHON = re.compile(r"^python[0-9.]*$")
PYTEST = ("pytest", "py.test")
FORMES = {
    "sortie": "sortie exacte",
    "jeton": "jeton de fin",
    "rapport": "rapport de tests",
    "junit": "rapport JUnit",
    "code": "code de retour seul",
}


def est_python(mot: str) -> bool:
    return mot == "{python}" or PYTHON.match(PurePosixPath(mot).name) is not None


def _lanceur(argv: Sequence[str], fin: int) -> bool:
    """Tout ce qui précède `fin` n'est qu'un lanceur : `uv run`, une option, un interpréteur."""
    return all(t in LANCEURS or t.startswith("-") or est_python(t) for t in argv[:fin])


def position_script(argv: Sequence[str]) -> int | None:
    """Index de l'interpréteur qui lance un script (`{python} x.py`, `uv run python x.py`)."""
    for index in range(len(argv) - 1):
        if est_python(argv[index]) and argv[index + 1].endswith(".py") and _lanceur(argv, index):
            return index
    return None


def position_pytest(argv: Sequence[str]) -> int | None:
    """Index du jeton qui lance pytest (`pytest`, `uv run pytest`, `{python} -m pytest`)."""
    for index, mot in enumerate(argv):
        if PurePosixPath(mot).name in PYTEST:
            return index if _lanceur(argv, index) else None
        if mot == "-m" and index + 1 < len(argv) and argv[index + 1] == "pytest":
            return index + 1 if _lanceur(argv, index) else None
    return None


def forme_de_preuve(criterion: Criterion) -> str:
    """Comment le juge saura que ce critère est allé au bout — voir `FORMES`."""
    if criterion.rapport_junit is not None:
        return "junit"
    if criterion.expected_output is not None:
        return "sortie"
    if position_script(criterion.check) is not None:
        return "jeton"
    if position_pytest(criterion.check) is not None:
        return "rapport"
    return "code"


def tests_attendus(argv: Sequence[str]) -> list[tuple[str, tuple[str, ...]]]:
    """Fichiers et tests que la commande désigne : `x.py`, `x.py::test_a`, `x.py::C::test_b`."""
    attendus: list[tuple[str, tuple[str, ...]]] = []
    for token in argv:
        chemin, separateur, reste = token.partition("::")
        if token.startswith("-") or not chemin.endswith(".py") or not safe_path(chemin):
            continue
        parties = tuple(p for p in reste.split("::") if p) if separateur else ()
        attendus.append((chemin, parties))
    return attendus


def _meme_nom(a: str, b: str) -> bool:
    """Deux noms pointés désignent la même chose si l'un finit l'autre : pytest les écrit
    depuis son dossier racine, qui n'est pas forcément celui de la commande."""
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def _couvre(classname: str, nom: str, chemin: str, parties: tuple[str, ...]) -> bool:
    module = chemin[:-3].replace("/", ".")
    if not parties:
        morceaux = classname.split(".") if classname else [nom]
        return any(_meme_nom(module, ".".join(morceaux[:n])) for n in range(1, len(morceaux) + 1))
    *classes, fonction = parties
    if not _meme_nom(".".join([module, *classes]), classname):
        return False
    return nom == fonction or ("[" not in fonction and nom.startswith(fonction + "["))


def verifier_rapport_pytest(
    chemin: Path, attendus: Sequence[tuple[str, tuple[str, ...]]]
) -> str | None:
    """Le rapport d'une preuve pytest dit-il qu'elle est allée au bout ? None si oui.

    Un rapport NEUF, complet, cohérent, où chaque test désigné par la commande a tourné et
    réussi. Un processus coupé avant la fin n'en écrit pas ; un test sauté ou marqué
    « échec attendu » n'est pas une réussite.
    """
    if chemin.is_symlink() or not chemin.is_file():
        return "aucun rapport de tests : le processus s'est arrêté avant la fin des tests"
    if chemin.stat().st_size > MAX_REPORT_BYTES:
        return "rapport de tests trop volumineux"
    brut = chemin.read_bytes()
    if b"<!DOCTYPE" in brut.upper() or b"<!ENTITY" in brut.upper():
        return "rapport de tests refusé : il déclare un DOCTYPE ou une entité XML"
    try:
        # Sans DOCTYPE ni entité, expat n'a rien à développer : pas de bombe XML possible.
        racine = ET.fromstring(brut)
    except ET.ParseError:
        return "rapport de tests incomplet (XML tronqué)"
    if racine.tag not in ("testsuite", "testsuites"):
        return "rapport de tests illisible"
    for suite in racine.iter():
        if suite.tag not in ("testsuite", "testsuites"):
            continue
        dedans = list(suite.iter("testcase"))
        comptes = {
            "tests": len(dedans),
            "failures": sum(cas.find("failure") is not None for cas in dedans),
            "errors": sum(cas.find("error") is not None for cas in dedans),
            "skipped": sum(cas.find("skipped") is not None for cas in dedans),
        }
        for cle, compte in comptes.items():
            declare = suite.get(cle)
            if declare is not None and (not declare.isdecimal() or int(declare) != compte):
                return "rapport de tests incohérent : ses compteurs ne disent pas son contenu"
    cas = list(racine.iter("testcase"))
    if not cas:
        return "aucun test exécuté"
    for item in cas:
        if any(item.find(etat) is not None for etat in ("failure", "error", "skipped")):
            return f"test en échec ou sauté : {item.get('classname')}::{item.get('name')}"
    for fichier, parties in attendus:
        if not any(
            _couvre(c.get("classname", ""), c.get("name", ""), fichier, parties) for c in cas
        ):
            return "test attendu absent du rapport : " + "::".join([fichier, *parties])
    return None


# Ce qu'un agent ajoute pour faire taire un contrôle au lieu de le satisfaire. Tout
# silencieux ajouté par le patch est nommé au verdict, et refusé sur une tâche à risque
# élevé.
SILENCIEUX = (
    ("noqa", re.compile(r"#\s*noqa\b", re.I)),
    ("type: ignore", re.compile(r"#\s*type:\s*ignore\b")),
    ("mypy:", re.compile(r"#\s*mypy:")),
    ("pragma: no cover", re.compile(r"#\s*pragma:\s*no\s*cover", re.I)),
    ("nosec", re.compile(r"#\s*nosec\b")),
    ("pylint: disable", re.compile(r"#\s*pylint:\s*disable")),
    ("skip", re.compile(r"pytest\.mark\.skip|pytest\.skip\(|unittest\.skip")),
    ("xfail", re.compile(r"pytest\.mark\.xfail|pytest\.xfail\(")),
    ("eslint-disable", re.compile(r"eslint-disable")),
    ("ts-ignore", re.compile(r"@ts-(?:ignore|nocheck|expect-error)")),
    ("nolint", re.compile(r"//\s*nolint\b")),
)
TEXTE_SEUL = (".md", ".rst", ".txt")


ECHAPPEMENTS_C = {"n": 10, "t": 9, "a": 7, "b": 8, "f": 12, "v": 11, "r": 13, '"': 34, "\\": 92}


def nom_git(brut: str) -> str:
    """Un nom lu dans un en-tête de diff, rendu tel qu'il est sur le disque.

    Git met entre guillemets, échappé à la façon du C, un nom qui porte un accent, un
    guillemet ou une barre oblique inverse (`"b/src/mod\\303\\250le.py"`), et fait suivre
    d'une tabulation un nom qui porte une espace. Lu tel quel, un `# noqa` ajouté dans
    `src/modèle.py` n'était attribué à aucun fichier, donc jamais nommé (mesuré le 25/09/2026).
    """
    brut = brut.rstrip("\t")
    if len(brut) < 2 or not (brut.startswith('"') and brut.endswith('"')):
        return brut
    corps, octets, i = brut[1:-1], bytearray(), 0
    while i < len(corps):
        if corps[i] == "\\" and re.match(r"[0-7]{3}", corps[i + 1 : i + 4]):
            octets.append(int(corps[i + 1 : i + 4], 8) & 0xFF)
            i += 4
        elif corps[i] == "\\" and corps[i + 1 : i + 2] in ECHAPPEMENTS_C:
            octets.append(ECHAPPEMENTS_C[corps[i + 1]])
            i += 2
        else:
            octets.extend(corps[i].encode("utf-8"))
            i += 1
    return octets.decode("utf-8", errors="replace")


def silencieux_ajoutes(patch: bytes) -> list[str]:
    """`fichier:ligne motif` pour chaque silencieux que le patch AJOUTE."""
    trouves: list[str] = []
    fichier: str | None = None
    ligne = 0
    precedente = ""
    for brute in patch.decode("utf-8", errors="replace").splitlines():
        if brute.startswith("+++ ") and precedente.startswith("--- "):
            nom = nom_git(brute[4:])
            fichier = nom[2:] if nom.startswith("b/") else None
            if fichier is not None and fichier.endswith(TEXTE_SEUL):
                fichier = None
        elif brute.startswith("@@"):
            debut = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)", brute)
            ligne = int(debut.group(1)) if debut else 0
        elif fichier is not None and brute.startswith("+"):
            for nom, motif in SILENCIEUX:
                if motif.search(brute[1:]):
                    trouves.append(f"{fichier}:{ligne} {nom}")
                    break
            ligne += 1
        elif fichier is not None and brute.startswith(" "):
            ligne += 1
        precedente = brute
    return trouves


# Ce qui s'exécute sans que personne l'appelle. `src/` est sur le chemin de Python dans un
# projet installé en mode éditable : un `src/sitecustomize.py` tourne dans CHAQUE processus
# Python du projet, avant pytest (mesuré le 25/09/2026) — la variante discrète du trou connu
# de `src/__init__.py` (voir « Ce que le juge ne peut pas voir »). Le juge ne peut pas dire
# ce qu'un tel fichier fait ; il le NOMME, pour que la revue le lise en premier. Les trois
# premiers n'ont rien à faire dans le correctif d'une tâche : refusés sur un risque élevé.
DEMARRAGE = ("sitecustomize.py", "usercustomize.py")
D_OFFICE = ("conftest.py", "__init__.py")


def executes_d_office(chemins: Sequence[str]) -> tuple[list[str], list[str]]:
    """(au démarrage de Python, à l'import ou à la collecte des tests) parmi ces chemins."""
    demarrage = [c for c in chemins if PurePosixPath(c).name in DEMARRAGE or c.endswith(".pth")]
    d_office = [c for c in chemins if PurePosixPath(c).name in D_OFFICE]
    return demarrage, d_office


# Ce qui règle les contrôles eux-mêmes. ruff prend, pour chaque fichier, la configuration la
# plus proche : un `src/app/ruff.toml` qui dit `select = []` éteint le lint de ce dossier
# sans une seule ligne de `noqa` (mesuré le 25/09/2026). C'est un silencieux à l'échelle d'un
# fichier : nommé au verdict, refusé sur un risque élevé. `pyproject.toml` et `package.json`
# portent aussi les dépendances, et changent pour mille raisons honnêtes : le juge lit quelles
# sections changent (voir `controles_d_un_porteur`).
REGLAGES_CONTROLES = (
    "ruff.toml",
    ".ruff.toml",
    "setup.cfg",
    "tox.ini",
    "pytest.ini",
    "mypy.ini",
    ".mypy.ini",
    ".coveragerc",
    ".flake8",
    ".pylintrc",
    "pylintrc",
    ".importlinter",
    ".golangci.yml",
    ".golangci.yaml",
    "tsconfig.json",
)
PREFIXES_REGLAGES = (".eslintrc", "eslint.config.", "jest.config.", "vitest.config.")
PORTEURS_DE_DEPENDANCES = ("pyproject.toml", "package.json")

# Ce qu'un porteur peut changer sans toucher aux contrôles : les dépendances et la fiche du
# paquet. Une liste de ce qui est PERMIS, pas de ce qui est interdit : `[tool.ruff…]`,
# `[tool.pytest…]`, mais aussi un outil que personne n'a prévu, `[project.entry-points.pytest11]`
# (un greffon que pytest charge tout seul) ou `"scripts"` (ce que lance `npm test`) comptent.
# Audit du 29/09/2026 : `select` de ruff vidé dans `pyproject.toml`, VÉRIFIÉ sur un risque élevé.
TABLES_DE_DEPENDANCES = frozenset(
    {
        "project",
        "project.optional-dependencies",
        "project.urls",
        "dependency-groups",
        "tool.uv",
        "tool.uv.sources",
        "tool.uv.index",
        "tool.uv.workspace",
        "tool.poetry",
        "tool.poetry.dependencies",
        "tool.poetry.dev-dependencies",
    }
)
GROUPE_POETRY = re.compile(r"tool\.poetry\.group\.[^.]+\.dependencies")
CLES_DE_DEPENDANCES = frozenset(
    {
        "name",
        "version",
        "description",
        "keywords",
        "author",
        "contributors",
        "license",
        "homepage",
        "repository",
        "bugs",
        "funding",
        "private",
        "engines",
        "packageManager",
        "dependencies",
        "devDependencies",
        "peerDependencies",
        "peerDependenciesMeta",
        "optionalDependencies",
        "bundleDependencies",
        "bundledDependencies",
        "overrides",
        "resolutions",
    }
)
# Un en-tête de table TOML, `[a.b]` ou `[[a.b]]`, et les morceaux de son nom : nus, ou cités.
EN_TETE_TOML = re.compile(r"[ \t]*\[\[?(?P<nom>[^\[\]]*)\]\]?[ \t]*(?:#.*)?")
CLE_TOML = re.compile(r"""[ \t]*(?:"((?:[^"\\]|\\.)*)"|'([^']*)'|([A-Za-z0-9_-]+))[ \t]*""")


def nom_de_table(brut: str) -> str:
    """`tool . "ruff".lint` → `tool.ruff.lint`. Un nom qui ne se lit pas ainsi garde sa forme :
    il ne vaudra jamais une table de dépendances."""
    parties: list[str] = []
    reste = brut
    while True:
        morceau = CLE_TOML.match(reste)
        if morceau is None:
            return brut
        parties.append(next(g for g in morceau.groups() if g is not None))
        reste = reste[morceau.end() :]
        if not reste:
            return ".".join(parties)
        if not reste.startswith("."):
            return brut
        reste = reste[1:]


def tables_toml(texte: str) -> dict[str, list[str]]:
    """Chaque table du fichier et ses lignes, en-têtes compris, dans l'ordre.

    Ni tomllib (Python 3.11) ni analyse complète : la même lecture partout, pour que le juge
    local et celui de la CI disent la même chose. Elle voit une table de plus quand une ligne
    de chaîne ressemble à un en-tête — jamais une de moins : une ligne changée sous
    `[tool.ruff]` ne peut pas passer pour une ligne de `[project]`. Ce qui précède le premier
    en-tête (des clés pointées, `tool.ruff.lint.select = []`) est la table « (hors table) »."""
    tables: dict[str, list[str]] = {}
    courante = "(hors table)"
    for brute in texte.split("\n"):
        ligne = brute.rstrip()
        en_tete = EN_TETE_TOML.fullmatch(ligne)
        if en_tete:
            courante = nom_de_table(en_tete.group("nom"))
        if ligne.strip():
            tables.setdefault(courante, []).append(ligne)
    return tables


def controles_d_un_porteur(chemin: str, avant: bytes | None, apres: bytes | None) -> list[str]:
    """Les sections d'un `pyproject.toml` ou d'un `package.json` que le changement touche, hors
    dépendances et fiche du paquet : `[tool.ruff.lint]`, `"scripts"`… Vide : rien d'autre."""
    texte_avant = (avant or b"").decode("utf-8", "replace")
    texte_apres = (apres or b"").decode("utf-8", "replace")
    if PurePosixPath(chemin).name == "package.json":
        try:
            ancien = json.loads(texte_avant or "{}")
            nouveau = json.loads(texte_apres or "{}")
        except ValueError:
            return ["(illisible)"]
        if not isinstance(ancien, dict) or not isinstance(nouveau, dict):
            return ["(illisible)"]
        absente = object()
        return [
            f'"{cle}"'
            for cle in sorted(set(ancien) | set(nouveau))
            if ancien.get(cle, absente) != nouveau.get(cle, absente)
            and cle not in CLES_DE_DEPENDANCES
        ]
    ancien_t, nouveau_t = tables_toml(texte_avant), tables_toml(texte_apres)
    changees = [
        nom
        for nom in sorted(set(ancien_t) | set(nouveau_t))
        if ancien_t.get(nom) != nouveau_t.get(nom)
    ]
    return [
        nom if nom == "(hors table)" else f"[{nom}]"
        for nom in changees
        if nom not in TABLES_DE_DEPENDANCES and not GROUPE_POETRY.fullmatch(nom)
    ]


def reglages_des_controles(
    chemins: Sequence[str],
    lire: Callable[[str], tuple[bytes | None, bytes | None]] | None = None,
) -> tuple[list[str], list[str]]:
    """(réglages des contrôles, porteurs de dépendances qui ne changent que les dépendances).

    `lire` rend le contenu d'un chemin avant et après le changement. Un porteur qui touche à
    autre chose que ses dépendances devient un réglage, nommé avec ses sections :
    `pyproject.toml ([tool.ruff.lint])`. Sans `lire`, il n'est que nommé."""
    reglages: list[str] = []
    porteurs: list[str] = []
    for chemin in chemins:
        nom = PurePosixPath(chemin).name
        if nom in REGLAGES_CONTROLES or nom.startswith(PREFIXES_REGLAGES):
            reglages.append(chemin)
        elif nom in PORTEURS_DE_DEPENDANCES:
            sections = controles_d_un_porteur(chemin, *lire(chemin)) if lire else []
            if sections:
                reglages.append(f"{chemin} ({', '.join(sections)})")
            else:
                porteurs.append(chemin)
    return reglages, porteurs


def blob_a(repo: Path, commit: str, chemin: str) -> bytes | None:
    """Le contenu d'un fichier à ce commit, tel quel (ni filtre, ni conversion), ou None."""
    try:
        return git(repo, "cat-file", "blob", f"{commit}:{chemin}")
    except JudgeError:
        return None


# Un test retiré n'ajoute aucune ligne : aucun silencieux ne le trahit. Rejoué le 29/09/2026 :
# `tests/test_architecture.py` supprimé (taille des fichiers, `print`, sens des dépendances),
# VÉRIFIÉ, et le verdict n'en disait rien. Le juge compare les tests que chaque fichier de
# test déclare avant et après : Python par son arbre syntaxique (un `def test_…` écrit dans
# une chaîne n'en est pas un), les autres langages par leur forme usuelle.
FICHIER_DE_TEST = re.compile(
    r"(?:^|/)(?:test[^/]*\.py|[^/]*_test\.py|[^/]*_test\.go"
    r"|[^/]*\.(?:test|spec)\.[cm]?[jt]sx?|[^/]*(?:Tests?|IT|TestCase)\.(?:java|kt))$"
    r"|(?:^|/)__tests__/[^/]*\.[cm]?[jt]sx?$|(?:^|/)src/test/.*\.(?:java|kt)$"
)
TEST_GO = re.compile(r"^func\s+(Test\w*)\s*\(", re.M)
TEST_JS = re.compile(r"""^\s*(?:it|test)(?:\.\w+)*\s*\(\s*(['"`])(.*?)\1""", re.M)
ANNOTATION_TEST = re.compile(r"@Test\b")
METHODE_JVM = re.compile(r"\b(?:void|fun)\s+(`[^`]+`|\w+)\s*\(")


def _tests_python(corps: list[ast.stmt], prefixe: str) -> Iterator[str]:
    for noeud in corps:
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if noeud.name.startswith("test"):
                yield prefixe + noeud.name
        elif isinstance(noeud, ast.ClassDef):
            yield from _tests_python(noeud.body, f"{prefixe}{noeud.name}::")


def tests_du_fichier(chemin: str, contenu: bytes | None) -> list[str] | None:
    """Les tests qu'un fichier de test déclare, par leur nom. None : il ne se lit pas."""
    if contenu is None:
        return []
    texte = contenu.decode("utf-8", "replace")
    if chemin.endswith(".py"):
        try:
            return list(_tests_python(ast.parse(texte).body, ""))
        except (SyntaxError, ValueError):
            return None
    if chemin.endswith(".go"):
        return TEST_GO.findall(texte)
    if chemin.endswith((".java", ".kt")):
        noms: list[str] = []
        lignes = texte.split("\n")
        for i, ligne in enumerate(lignes):
            if ANNOTATION_TEST.search(ligne):
                methodes = (METHODE_JVM.search(suite) for suite in lignes[i : i + 6])
                trouvee = next((m for m in methodes if m), None)
                noms.append(trouvee.group(1) if trouvee else "@Test")
        return noms
    return [trouve.group(2) for trouve in TEST_JS.finditer(texte)]


def tests_retires(
    chemins: Sequence[str], lire: Callable[[str], tuple[bytes | None, bytes | None]]
) -> list[str]:
    """Les tests que le changement retire : `fichier::nom`, ou `fichier (supprimé, N test(s))`.

    `lire` rend le contenu d'un chemin avant et après. Un test qui réapparaît sous le même nom
    dans un autre fichier a déménagé : il n'est pas retiré. Un fichier qui ne se lit plus
    (erreur de syntaxe) a perdu tous ses tests."""
    perdus: dict[str, list[str]] = {}
    gagnes: Counter[str] = Counter()
    supprimes: set[str] = set()
    for chemin in chemins:
        if not FICHIER_DE_TEST.search(chemin):
            continue
        avant, apres = lire(chemin)
        if apres is None:
            supprimes.add(chemin)
        restent = Counter(tests_du_fichier(chemin, apres) or [])
        for nom in tests_du_fichier(chemin, avant) or []:
            if restent[nom]:
                restent[nom] -= 1
            else:
                perdus.setdefault(chemin, []).append(nom)
        gagnes.update(+restent)
    retires: list[str] = []
    for chemin, noms in perdus.items():
        vrais: list[str] = []
        for nom in noms:
            if gagnes[nom]:
                gagnes[nom] -= 1
            else:
                vrais.append(nom)
        if vrais and chemin in supprimes:
            retires.append(f"{chemin} (supprimé, {len(vrais)} test(s))")
        else:
            retires.extend(f"{chemin}::{nom}" for nom in vrais)
    return sorted(retires)


# --------------------------------------------------------------------------- contrat


def _argv(value: Any, name: str, errors: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        errors.append(f"{name} doit être une liste d'arguments non vide")
        return ()
    if not all(isinstance(item, str) and item for item in value):
        errors.append(f"{name} ne doit contenir que des chaînes non vides")
        return ()
    return tuple(value)


def junit_du_critere(
    item: dict[str, Any], check: tuple[str, ...], sortie: Any, label: str, errors: list[str]
) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """`rapport_junit` et `preuves` d'un critère, vérifiés : les tests désignés, et les
    fichiers qui les portent."""
    brut, fichiers = item.get("rapport_junit"), item.get("preuves", [])
    preuves: tuple[str, ...] = ()
    if not isinstance(fichiers, list) or not all(
        isinstance(f, str) and safe_path(f) for f in fichiers
    ):
        errors.append(f"{label} : preuves doit être une liste de chemins du dépôt")
    else:
        preuves = tuple(fichiers)
    marque = any("{rapport}" in arg for arg in check)
    if brut is None:
        if marque:
            errors.append(f"{label} : {{rapport}} ne sert qu'avec rapport_junit")
        return None, preuves
    if (
        not isinstance(brut, list)
        or not brut
        or not all(isinstance(nom, str) and nom.strip() for nom in brut)
    ):
        errors.append(
            f"{label} : rapport_junit doit être une liste non vide de tests désignés (Classe.test)"
        )
        return None, preuves
    if not marque:
        errors.append(
            f"{label} : rapport_junit veut {{rapport}} dans check : le dossier où la commande"
            " écrit son rapport"
        )
    if sortie is not None:
        errors.append(f"{label} : rapport_junit ou expected_output, pas les deux")
    if not preuves:
        errors.append(
            f"{label} : rapport_junit veut les fichiers de ses tests dans preuves : sans eux,"
            " l'agent pourrait les réécrire"
        )
    return tuple(nom.strip() for nom in brut), preuves


def relocalise(valeur: str, task_id: str, dossier: str) -> str:
    """`tasks/<ID>/…`, le nom canonique d'un fichier de la tâche, dans son vrai dossier."""
    canon = f"tasks/{task_id}"
    if dossier == canon:
        return valeur
    # Le dossier lui-même, où qu'il soit dans le jeton : `tasks/<ID>/test.py`, mais aussi
    # `--rootdir=tasks/<ID>` ou `cd tasks/<ID> && …`. Jamais un autre nom qui le contient :
    # `tasks/<ID>2` est une autre tâche, `soustasks/<ID>` un autre dossier.
    motif = rf"(?<![A-Za-z0-9_-]){re.escape(canon)}(?![A-Za-z0-9_.-])"
    return re.sub(motif, lambda _: dossier, valeur)


def canonique(chemin: str, task_id: str, dossier: str) -> str:
    """L'inverse : un fichier du dossier réel de la tâche, sous son nom canonique."""
    if dossier != f"tasks/{task_id}" and chemin.startswith(dossier + "/"):
        return f"tasks/{task_id}/" + chemin[len(dossier) + 1 :]
    return chemin


def parse_contract(data: Any, expected_id: str | None = None, dossier: str = "") -> Contract:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise JudgeError("le contrat doit être un objet JSON")
    unknown = sorted(set(data) - CONTRACT_KEYS)
    if unknown:
        errors.append(f"clés inconnues : {', '.join(unknown)}")
    missing = sorted(REQUIRED_KEYS - set(data))
    if missing:
        errors.append(f"clés manquantes : {', '.join(missing)}")
    if errors:
        raise JudgeError(errors)

    task_id = data["id"]
    if not isinstance(task_id, str) or not ID_PATTERN.match(task_id):
        errors.append("id invalide : majuscules, chiffres et tirets, 3 à 51 caractères")
    elif expected_id is not None and task_id != expected_id:
        errors.append(f"id {task_id!r} différent du dossier {expected_id!r}")

    objective = data["objective"]
    if not isinstance(objective, str) or not 10 <= len(objective) <= 12000:
        errors.append("objective doit faire entre 10 et 12 000 caractères")

    risk = data["risk"]
    if risk not in RISKS:
        errors.append(f"risk doit valoir {', '.join(RISKS)}")

    allowed = data["allowed_paths"]
    rules: list[str] = []
    if not isinstance(allowed, list) or not allowed:
        errors.append("allowed_paths doit être une liste non vide")
    else:
        for rule in allowed:
            if not isinstance(rule, str) or not safe_path(rule):
                errors.append(f"chemin autorisé invalide : {rule!r}")
            elif any(
                matches(rule.rstrip("/"), protected) or matches(protected.rstrip("/"), rule)
                for protected in zones()
            ):
                errors.append(f"chemin autorisé qui touche une zone de contrôle : {rule}")
            elif rule in rules:
                errors.append(f"chemin autorisé en double : {rule}")
            else:
                rules.append(rule)

    criteria: list[Criterion] = []
    raw_criteria = data["criteria"]
    if not isinstance(raw_criteria, list) or not raw_criteria:
        errors.append("criteria doit être une liste non vide")
    else:
        seen: set[str] = set()
        for index, item in enumerate(raw_criteria, 1):
            label = f"critère n°{index}"
            if not isinstance(item, dict) or not CRITERION_REQUIRED <= set(item) <= CRITERION_KEYS:
                errors.append(
                    f"{label} : clés attendues id, description, check, et au choix"
                    " max_duration_seconds, stdin, expected_output, returncode, rapport_junit,"
                    " preuves"
                )
                continue
            cid, description = item["id"], item["description"]
            if not isinstance(cid, str) or not CRITERION_ID.match(cid):
                errors.append(f"{label} : id invalide")
                continue
            if cid in seen:
                errors.append(f"{label} : id {cid} en double")
            seen.add(cid)
            if not isinstance(description, str) or len(description) < 3:
                errors.append(f"{label} : description trop courte")
            check = _argv(item["check"], f"{cid}.check", errors)
            limit = item.get("max_duration_seconds")
            if limit is not None and (
                isinstance(limit, bool)
                or not isinstance(limit, (int, float))
                or not 0 < float(limit) <= 7200
            ):
                errors.append(f"{label} : max_duration_seconds doit valoir entre 0 exclu et 7200")
                limit = None
            entree, sortie = item.get("stdin"), item.get("expected_output")
            for nom, valeur in (("stdin", entree), ("expected_output", sortie)):
                if valeur is not None and not isinstance(valeur, str):
                    errors.append(f"{label} : {nom} doit être un texte")
            code_attendu = item.get("returncode", 0)
            if "returncode" in item and sortie is None:
                errors.append(f"{label} : returncode ne sert qu'avec expected_output")
            if (
                isinstance(code_attendu, bool)
                or not isinstance(code_attendu, int)
                or not 0 <= code_attendu <= 255
            ):
                errors.append(f"{label} : returncode doit être un entier entre 0 et 255")
                code_attendu = 0
            if any(arg.startswith(("--junitxml", "--junit-xml")) for arg in check):
                errors.append(f"{label} : le rapport de tests est demandé par le juge, retirez-le")
            designes, preuves = junit_du_critere(item, check, sortie, label, errors)
            criterion = Criterion(
                cid,
                str(description),
                check,
                float(limit) if limit is not None else None,
                entree if isinstance(entree, str) else None,
                sortie if isinstance(sortie, str) else None,
                code_attendu,
                rapport_junit=designes,
                preuves=preuves,
            )
            # Un risque élevé exige une preuve qui va au bout : aucun critère d'une tâche à
            # risque élevé ne se juge sur son seul code de retour.
            if risk == "eleve" and check and forme_de_preuve(criterion) == "code":
                errors.append(
                    f"{label} : risque élevé, et ce critère ne serait jugé que sur son code de"
                    " retour. Écrivez-le comme un test pytest, un script Python lancé par"
                    " {python}, ou donnez sa sortie attendue (expected_output)"
                )
            criteria.append(criterion)

    setup: tuple[str, ...] | None = None
    if data.get("setup") is not None:
        setup = _argv(data["setup"], "setup", errors)
    verify: tuple[str, ...] = ("bash", ".ephor/projet/verify.sh")
    if data.get("verify") is not None:
        verify = _argv(data["verify"], "verify", errors)

    timeout = data.get("timeout_seconds", DEFAULT_TIMEOUT)
    if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 7200:
        errors.append("timeout_seconds doit être un entier entre 1 et 7200")
        timeout = DEFAULT_TIMEOUT

    budget = data.get("budget_tokens")
    if budget is not None and (
        isinstance(budget, bool) or not isinstance(budget, int) or not 1 <= budget <= 100_000_000
    ):
        errors.append("budget_tokens doit être un entier entre 1 et 100000000")
        budget = None

    niveau, effort = data.get("niveau"), data.get("effort")
    if niveau is not None and niveau not in NIVEAUX:
        errors.append(f"niveau doit valoir {', '.join(NIVEAUX)}")
    if effort is not None and effort not in EFFORTS:
        errors.append(f"effort doit valoir {', '.join(EFFORTS)}")

    if errors:
        raise JudgeError(errors)
    contrat = Contract(
        id=str(task_id),
        objective=str(objective),
        risk=str(risk),
        allowed_paths=tuple(rules),
        criteria=tuple(criteria),
        setup=setup,
        verify=verify,
        timeout_seconds=int(timeout),
        budget_tokens=int(budget) if budget is not None else None,
        niveau=niveau if isinstance(niveau, str) else None,
        effort=effort if isinstance(effort, str) else None,
    )
    return _range(contrat, dossier) if dossier and dossier != contrat.ou else contrat


def _range(contract: Contract, dossier: str) -> Contract:
    """Le contrat d'une tâche rangée : ses chemins `tasks/<ID>/…` menés à son vrai dossier."""
    criteres = tuple(
        replace(
            critere,
            check=tuple(relocalise(jeton, contract.id, dossier) for jeton in critere.check),
            preuves=tuple(relocalise(chemin, contract.id, dossier) for chemin in critere.preuves),
        )
        for critere in contract.criteria
    )
    return replace(contract, criteria=criteres, dossier=dossier)


def dossier_du_contrat(path: Path) -> str:
    """`tasks/<ID>` ou `tasks/<lot>/<ID>`, d'après le chemin du contrat."""
    parties = path.parent.parts
    if "tasks" not in parties:
        return ""
    debut = len(parties) - 1 - parties[::-1].index("tasks")
    return "/".join(parties[debut:])


def load_contract(path: Path, expected_id: str | None = None) -> Contract:
    try:
        data = lire_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise JudgeError(f"contrat introuvable : {path}") from exc
    except json.JSONDecodeError as exc:
        raise JudgeError(f"contrat illisible (JSON) : {exc}") from exc
    return parse_contract(data, expected_id, dossier_du_contrat(path))


# --------------------------------------------------------------------------- dépôt


def repo_root(start: str | None) -> Path:
    here = Path(start or ".").resolve()
    try:
        top = subprocess.run(
            ["git", "-C", str(here), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"pas un dépôt git : {here}") from exc
    return Path(top).resolve()


def taches_sur_disque(repo: Path) -> dict[str, list[str]]:
    """Les tâches du dossier de travail, rangées ou non : identifiant -> dossiers."""
    trouvees: dict[str, list[str]] = {}
    racine = repo / "tasks"
    for contrat in [*racine.glob("*/contract.json"), *racine.glob("*/*/contract.json")]:
        dossier = contrat.parent
        rangee = dossier.parent != racine
        if not ID_PATTERN.match(dossier.name) or (
            rangee and not LOT_PATTERN.match(dossier.parent.name)
        ):
            continue
        trouvees.setdefault(dossier.name, []).append(dossier.relative_to(repo).as_posix())
    return trouvees


def taches_a(repo: Path, arbre: str) -> dict[str, str]:
    """Les tâches d'un arbre git (la branche principale, en général) : identifiant -> dossier.
    Un identifiant présent deux fois garde son dossier non rangé : `make taches` le signale."""
    fige = arbre_fige(repo, arbre)
    try:
        if fige is not None:
            noms = liste_de_tasks(repo, fige).noms
        else:
            sortie = git(repo, "ls-tree", "-r", "-z", "--name-only", arbre, "--", "tasks")
            noms = [brut.decode("utf-8", "replace") for brut in sortie.split(b"\0")]
    except JudgeError:
        return {}
    trouvees: dict[str, str] = {}
    for nom in noms:
        morceaux = nom.split("/")
        if morceaux[-1:] != ["contract.json"]:
            continue
        if len(morceaux) == 3 and ID_PATTERN.match(morceaux[1]):
            trouvees[morceaux[1]] = f"tasks/{morceaux[1]}"
        elif (
            len(morceaux) == 4 and LOT_PATTERN.match(morceaux[1]) and ID_PATTERN.match(morceaux[2])
        ):
            trouvees.setdefault(morceaux[2], f"tasks/{morceaux[1]}/{morceaux[2]}")
    return trouvees


def resolve_task(repo: Path, task_id: str | None, task_path: str | None) -> tuple[Path, str]:
    if task_id is not None:
        if not ID_PATTERN.match(task_id):
            raise JudgeError(f"identifiant de tâche invalide : {task_id!r}")
        dossiers = taches_sur_disque(repo).get(task_id, [])
        if len(dossiers) > 1:
            raise JudgeError(f"tâche {task_id} en double : {', '.join(sorted(dossiers))}")
        dossier = dossiers[0] if dossiers else f"tasks/{task_id}"
        return repo / dossier / "contract.json", task_id
    if task_path is None:
        raise JudgeError("indiquez --task-id ou --task")
    path = Path(task_path)
    path = (path if path.is_absolute() else Path.cwd() / path).resolve()
    # Le message le disait, le code ne le vérifiait pas : un contrat ailleurs dans le dépôt
    # était jugé, sous le nom de son dossier, sans être dans la zone de contrôle `tasks/`.
    lieu = path.parent
    rangee = lieu.parent.parent == repo / "tasks" and LOT_PATTERN.match(lieu.parent.name)
    if not ID_PATTERN.match(lieu.name) or not (lieu.parent == repo / "tasks" or rangee):
        raise JudgeError("le contrat doit se trouver dans le dépôt, sous tasks/<ID>/")
    return path, lieu.name


def principales(repo: Path) -> set[str]:
    """Les branches où l'approbation se lit : main, master, et celle que le serveur désigne."""
    noms = {"main", "master"}
    with suppress(JudgeError):
        distante = git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        noms.add(distante.decode().strip().split("/", 1)[-1])
    return noms


def base_d_approbation(repo: Path, base: str) -> bool:
    """Une base où un contrat commité vaut approbation : la branche principale, ou une
    branche du serveur (la CI juge contre la branche cible, `origin/<cible>`)."""
    nom = base[len("refs/heads/") :] if base.startswith("refs/heads/") else base
    return nom in principales(repo) or base.startswith(("origin/", "refs/remotes/"))


def pas_encore_sur_le_serveur(
    repo: Path, base_ref: str, chemin: str, approuve: bytes
) -> str | None:
    """La branche du serveur (`origin/<principale>`, telle qu'au dernier fetch) quand le contrat
    approuvé n'y est pas encore, ou pas sous cette forme ; None sinon, et sans serveur connu.

    Localement, rien n'est hors de portée de l'agent : références, `.verify/`, base du juge. Le
    juge de la CI lit l'approbation sur la branche protégée du serveur (audit du 29/09/2026, F2) :
    une approbation qui n'y est pas encore n'est qu'une approbation locale."""
    if base_ref.startswith(("origin/", "refs/remotes/")):
        return None
    nom = base_ref[len("refs/heads/") :] if base_ref.startswith("refs/heads/") else base_ref
    distante = f"origin/{nom}"
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"{distante}^{{commit}}")
    except JudgeError:
        return None
    try:
        sur_le_serveur = git(repo, "show", f"{distante}:{chemin}")
    except JudgeError:
        return distante
    return None if sur_le_serveur == approuve else distante


def resolve_base(repo: Path, base: str | None) -> tuple[str, str]:
    # La base se choisit à l'appel, et `make` passe sans question : l'agent commitait sur
    # SA branche un contrat qu'il s'était écrit, puis `make judge BASE=<sa branche>`, et le
    # juge local le lisait « approuvé » (audit du 29/09/2026).
    if base and not base_d_approbation(repo, base):
        raise JudgeError(
            f"base choisie à la main ({base}) : un contrat n'est approuvé que commité sur la "
            f"branche principale ({', '.join(sorted(principales(repo)))}) ; pour juger contre "
            "une autre branche du serveur, passez origin/<branche>"
        )
    candidates = [base] if base else ["main", "master", "origin/main", "origin/master"]
    for ref in candidates:
        try:
            commit = git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
        except JudgeError:
            continue
        found = commit.decode().strip()
        if found:
            return ref, git(repo, "merge-base", "HEAD", found).decode().strip()
    raise JudgeError(f"base introuvable ({', '.join(candidates)}) : précisez --base")


def snapshot(
    repo: Path,
    base: str,
    seulement: Sequence[str] = (),
    hors_commit: list[str] | None = None,
) -> tuple[bytes, list[str]]:
    """Patch entre la base et l'état courant du dossier, sans toucher à l'index de l'utilisateur.

    `seulement` le restreint à ces chemins, pris à la lettre (ni joker, ni motif). `hors_commit`,
    quand on le passe, reçoit les fichiers où le dossier diffère du dernier commit : ce qui est
    jugé et que la branche ne contient pas.

    Un index vide, remonté depuis HEAD, et jamais une copie de celui de l'utilisateur : un
    fichier marqué `assume-unchanged` y passerait pour intact, modifié ou non."""
    with tempfile.TemporaryDirectory(prefix="ephor-index-") as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git(repo, "read-tree", "HEAD", env=env)
        git(repo, "add", "-A", "--", ".", env=env)
        # Les rapports du juge ne font jamais partie du patch, ignorés ou non par le dépôt.
        git(repo, "rm", "-r", "--cached", "--ignore-unmatch", "--quiet", "--", ".verify", env=env)
        if hors_commit is not None:
            ecarts = git(
                repo, "diff", "--cached", "--name-only", "-z", "--no-renames", "HEAD", env=env
            )
            hors_commit.extend(chemins_z(ecarts))
        if seulement:
            env["GIT_LITERAL_PATHSPECS"] = "1"
        cibles = ["--", *seulement] if seulement else []
        names = git(
            repo, "diff", "--cached", "--name-only", "-z", "--no-renames", base, *cibles, env=env
        )
        patch = git(
            repo,
            "diff",
            "--cached",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            "--no-renames",
            base,
            *cibles,
            env=env,
        )
    return patch, chemins_z(names)


def chemins_z(sortie: bytes) -> list[str]:
    """Les chemins d'une sortie `git … -z`, triés et sans doublon."""
    return sorted({item.decode("utf-8", errors="strict") for item in sortie.split(b"\0") if item})


def exists_at(repo: Path, commit: str, path: str) -> bool:
    try:
        git(repo, "cat-file", "-e", f"{commit}:{path}")
    except JudgeError:
        return False
    return True


def acceptance_files(repo: Path, base: str, contract: Contract) -> set[str]:
    """Fichiers de la base cités par les critères : ce sont les preuves, donc intouchables."""
    return {path for path in cited_paths(repo, base, contract) if exists_at(repo, base, path)}


def kind_at(repo: Path, commit: str, path: str) -> str | None:
    """`blob` (un fichier), `tree` (un dossier), ou None si rien n'existe là à ce commit."""
    try:
        trouve = interroge(repo, f"{commit}:{path}")
    except OSError:
        pass
    else:
        return None if trouve is None else trouve[1]
    try:
        return git(repo, "cat-file", "-t", f"{commit}:{path}").decode().strip()
    except JudgeError:
        return None


def is_file_at(repo: Path, commit: str, path: str) -> bool:
    """Un FICHIER à ce commit — pas un dossier : `tests` n'est pas une preuve, `tests/x.py` oui."""
    return kind_at(repo, commit, path) == "blob"


def cited_dirs(repo: Path, base: str, contract: Contract) -> list[tuple[str, str]]:
    """Dossiers cités par les critères, `(critère, dossier)` ; `.` est le dépôt entier.

    `uv run pytest tests/` fait de TOUT ce que contient `tests/` la preuve : les tests qui y
    sont, et ceux qu'on y ajoute (un `conftest.py` qui écarte le test gênant, par exemple).
    Seul l'argument de tête, le programme lui-même, n'est pas lu.
    """
    cites: list[tuple[str, str]] = []
    for criterion in contract.criteria:
        for token in criterion.check[1:]:
            brut = token.split("::", 1)[0]
            if brut.startswith(("-", "{")) or brut in LANCEURS:
                continue
            if brut in (".", "./"):
                dossier = "."
            else:
                dossier = str(PurePosixPath(brut))
                if not safe_path(dossier):
                    continue
                if kind_at(repo, base, dossier) != "tree" and not (repo / dossier).is_dir():
                    continue
            if (criterion.id, dossier) not in cites:
                cites.append((criterion.id, dossier))
    return cites


def open_dirs(repo: Path, base: str, contract: Contract) -> list[str]:
    """Dossiers de preuve où le contrat laisse l'agent écrire : une règle du périmètre y
    mène, ou les contient. L'agent y réécrirait ou y ajouterait sa propre preuve."""
    ouverts: list[str] = []
    for cid, dossier in cited_dirs(repo, base, contract):
        dedans = any(
            dossier == "."
            or matches(dossier, rule)
            or rule.rstrip("/") == dossier
            or rule.startswith(dossier + "/")
            for rule in contract.allowed_paths
        )
        if dedans:
            nom = "le dépôt entier" if dossier == "." else f"le dossier {dossier}/"
            ouverts.append(
                f"preuve dans le périmètre de l'agent : {nom}, cité par {cid}, est ouvert à"
                " l'écriture par allowed_paths — citez les fichiers de preuve un par un, ou"
                " retirez ce dossier du périmètre"
            )
    return ouverts


def cited_paths(repo: Path, base: str, contract: Contract) -> list[str]:
    """Fichiers cités par les critères : ce sont eux qui font office de preuve.

    Un fichier est reconnu parce qu'il EXISTE, pas parce que son nom contient une barre
    oblique. L'ancienne règle exigeait un « / » : une preuve posée à la racine du dépôt
    (`check_c1.py`) n'était donc protégée par personne, et l'agent pouvait la vider avant
    de se déclarer vainqueur. Reproduit, et c'est le pire défaut qu'ait eu ce juge.
    """
    cited: list[str] = []
    for criterion in contract.criteria:
        for token in criterion.check:
            candidate = str(PurePosixPath(token.split("::", 1)[0]))
            if candidate.startswith(("-", "{")) or not safe_path(candidate):
                continue
            if candidate in cited:
                continue
            if is_file_at(repo, base, candidate) or (repo / candidate).is_file():
                cited.append(candidate)
        for chemin in criterion.preuves:
            if chemin not in cited and (
                is_file_at(repo, base, chemin) or (repo / chemin).is_file()
            ):
                cited.append(chemin)
    return cited


def writable_proofs(repo: Path, base: str, contract: Contract) -> list[str]:
    """Preuves que l'agent pourrait écrire lui-même : dans son périmètre, absentes de la base.

    Une preuve déjà présente sur la base est intouchable (`acceptance_files`). Une preuve
    qui n'existe pas encore et que le contrat autorise à écrire, c'est l'agent qui note sa
    propre copie : le juge refuse le contrat avant d'exécuter quoi que ce soit.
    """
    return [
        path
        for path in cited_paths(repo, base, contract)
        if any(matches(path, rule) for rule in contract.allowed_paths)
        and not exists_at(repo, base, path)
    ]


# --------------------------------------------------------------------------- rouge d'abord

# Un critère déjà vert avant le travail fait déclarer l'agent VÉRIFIÉ sans qu'il ait rien
# fait. `make task-check` lance donc chaque critère une fois sur le code commité, AVANT
# l'approbation : tous doivent échouer. Il en garde la trace ici, commitée avec le contrat ;
# le juge la lit sur la base, où l'agent ne peut pas écrire, et ne relance rien.
ROUGE = "rouge.json"
COMMANDE_ROUGE = "python3 .ephor/judge.py rouge --task-id"
# Ce que `make task-new` écrit à la place du contrat et des tests : laissé tel quel, un
# « rouge » qui ne prouve rien (le test d'un squelette échoue toujours).
A_ECRIRE = "A ECRIRE"
SQUELETTE = "critère à écrire"


def racines_de_preuve(repo: Path, arbre: str, contract: Contract, task_id: str) -> set[str]:
    """Le dossier de la tâche, et les fichiers et dossiers que ses critères citent."""
    racines = (
        {contract.ou}
        | {dossier for _, dossier in cited_dirs(repo, arbre, contract)}
        | set(cited_paths(repo, arbre, contract))
    )
    return {"."} if "." in racines else racines


def entrees_ls_tree(sortie: bytes) -> list[tuple[str, str, str]]:
    """La sortie de `git ls-tree -r -z`, en `(chemin, sorte, objet)`."""
    entrees: list[tuple[str, str, str]] = []
    for ligne in sortie.split(b"\0"):
        if not ligne:
            continue
        meta, chemin = ligne.split(b"\t", 1)
        _, sorte, objet = meta.split()
        entrees.append((chemin.decode("utf-8", "replace"), sorte.decode(), objet.decode()))
    return entrees


def arbre_fige(repo: Path, arbre: str) -> str | None:
    """Le hash de l'arbre git que désigne `arbre` (une branche, un commit, un arbre) : ce qu'il
    contient ne bouge plus, on peut donc le garder en mémoire. None si on ne le sait pas."""
    try:
        trouve = interroge(repo, f"{arbre}^{{tree}}")
    except OSError:
        return None
    return trouve[0] if trouve is not None and trouve[1] == "tree" else None


@dataclass(frozen=True)
class ListeDeTasks:
    """Tout ce qui vit sous `tasks/` à un arbre figé : les entrées triées, leurs chemins, et la
    place de chaque chemin."""

    entrees: list[tuple[str, str, str]]
    noms: list[str]
    places: dict[str, int]


_LISTES_DE_TASKS: dict[tuple[str, str], ListeDeTasks] = {}


def liste_de_tasks(repo: Path, fige: str) -> ListeDeTasks:
    """Une lecture de `tasks/` par arbre, pour toutes les tâches, au lieu d'une par tâche."""
    cle = (str(repo), fige)
    if cle not in _LISTES_DE_TASKS:
        if len(_LISTES_DE_TASKS) > 32:
            _LISTES_DE_TASKS.clear()
        entrees = sorted(
            entrees_ls_tree(git(repo, "ls-tree", "-r", "-z", "--full-tree", fige, "--", "tasks"))
        )
        noms = [chemin for chemin, _, _ in entrees]
        _LISTES_DE_TASKS[cle] = ListeDeTasks(entrees, noms, {nom: i for i, nom in enumerate(noms)})
    return _LISTES_DE_TASKS[cle]


def entrees_sous(repo: Path, arbre: str, racines: set[str]) -> list[tuple[str, str, str]]:
    """Ce que `git ls-tree -r <arbre> -- <racines>` rendrait : les fichiers de ces racines,
    `(chemin, sorte, objet)`. Ce qui vit sous `tasks/` se lit dans la liste de l'arbre, gardée
    en mémoire ; le reste, par git, comme avant."""
    dans_tasks = sorted(r for r in racines if r == "tasks" or r.startswith("tasks/"))
    ailleurs = sorted(set(racines) - set(dans_tasks))
    fige = arbre_fige(repo, arbre) if dans_tasks else None
    if fige is None:
        ailleurs = sorted(racines)
    trouvees: dict[str, tuple[str, str, str]] = {}
    if fige is not None:
        liste = liste_de_tasks(repo, fige)
        for racine in dans_tasks:
            if racine in liste.places:
                trouvees[racine] = liste.entrees[liste.places[racine]]
            # Le contenu d'un dossier : les chemins qui commencent par « <racine>/ » se suivent.
            for i in range(bisect.bisect_left(liste.noms, racine + "/"), len(liste.noms)):
                if not liste.noms[i].startswith(racine + "/"):
                    break
                trouvees[liste.noms[i]] = liste.entrees[i]
    if ailleurs:
        for entree in entrees_ls_tree(
            git(repo, "ls-tree", "-r", "-z", "--full-tree", arbre, "--", *ailleurs)
        ):
            trouvees[entree[0]] = entree
    return list(trouvees.values())


def preuves_rouges(repo: Path, arbre: str, contract: Contract, task_id: str) -> dict[str, str]:
    """Ce dont dépend le verdict rouge, à cet arbre git : le dossier de la tâche (contrat
    compris), les fichiers et les dossiers que les critères citent. Un seul changé après le
    contrôle, et ce qui a été vu échouer n'est plus ce qui sera jugé."""
    racines = racines_de_preuve(repo, arbre, contract, task_id)
    objets: dict[str, str] = {}
    for nom, sorte, objet in entrees_sous(repo, arbre, racines):
        if sorte == "blob" and nom != f"{contract.ou}/{ROUGE}":
            # Sous son nom canonique : une tâche rangée ensuite garde une trace valable.
            objets[canonique(nom, task_id, contract.ou)] = objet
    # Un seul processus pour tous les contenus : un par fichier coûtait 7 s pour un dossier
    # de 400 fichiers (mesuré le 26/09/2026), payées à chaque jugement.
    contenus = lire_objets(repo, sorted(set(objets.values())))
    return {nom: sha256(contenus[objet]) for nom, objet in sorted(objets.items())}


def chemins_de_preuve(repo: Path, arbre: str, contract: Contract, task_id: str) -> set[str]:
    """Les fichiers dont dépend le verdict d'une tâche, à cet arbre git (ceux que relève son
    contrôle rouge), sans lire leur contenu."""
    racines = racines_de_preuve(repo, arbre, contract, task_id)
    noms = {chemin for chemin, _, _ in entrees_sous(repo, arbre, racines)}
    return noms - {f"{contract.ou}/{ROUGE}"}


@functools.lru_cache(maxsize=4096)
def _preuves_d_une_tache(repo: str, arbre: str, task_id: str, dossier: str) -> frozenset[str]:
    """Les preuves d'une AUTRE tâche, à un arbre git figé (un hash : le résultat ne bouge
    plus). Vide si son contrat est illisible : ce n'est pas à cette tâche de le juger."""
    depot = Path(repo)
    try:
        brut = contenu_a(depot, arbre, f"{dossier}/contract.json").decode("utf-8")
        contrat = parse_contract(lire_json(brut), task_id, dossier)
    except (JudgeError, UnicodeDecodeError):
        return frozenset()
    return frozenset(chemins_de_preuve(depot, arbre, contrat, task_id))


def preuves_partagees(
    repo: Path, arbre: str, contract: Contract, task_id: str, miennes: set[str] | None = None
) -> dict[str, list[str]]:
    """Les preuves de cette tâche qui sont aussi celles d'une autre, à cet arbre : chemin ->
    tâches. Deux contrats qui partagent un test se gênent : le juge de l'un peut dépendre du
    travail de l'autre, et tout changement du fichier périme les deux contrôles rouges. Vu en
    session réelle : un test partagé par deux contrats empêchait un verdict propre (30/09/2026).
    """
    if miennes is None:
        miennes = chemins_de_preuve(repo, arbre, contract, task_id)
    fige = arbre_fige(repo, arbre)
    if fige is None:
        try:
            fige = git(repo, "rev-parse", f"{arbre}^{{tree}}").decode().strip()
        except JudgeError:
            return {}
    partages: dict[str, list[str]] = {}
    for autre, dossier in sorted(taches_a(repo, fige).items()):
        if autre == task_id:
            continue
        for chemin in sorted(miennes & _preuves_d_une_tache(str(repo), fige, autre, dossier)):
            partages.setdefault(chemin, []).append(autre)
    return dict(sorted(partages.items()))


def dire_partages(partages: dict[str, list[str]], task_id: str, dossier: str = "") -> list[str]:
    """L'avertissement que lit l'opérateur au moment d'approuver."""
    if not partages:
        return []
    lignes = ["attention : preuve(s) partagée(s) avec un autre contrat"]
    lignes += [
        f"  {chemin} : aussi une preuve de {', '.join(autres)}"
        for chemin, autres in list(partages.items())[:5]
    ]
    if len(partages) > 5:
        lignes.append(f"  … et {len(partages) - 5} autre(s)")
    lignes.append(
        f"  Le juge de {task_id} peut alors dépendre du travail de l'autre tâche, et changer ces"
        " fichiers périme les deux contrôles rouges. Donnez à chaque contrat ses propres tests,"
        f" dans {dossier or f'tasks/{task_id}'}/ (tasks/README.md)."
    )
    return lignes


def lire_objets(repo: Path, objets: list[str]) -> dict[str, bytes]:
    """Le contenu de plusieurs objets git, en une seule lecture (`git cat-file --batch`)."""
    if not objets:
        return {}
    # D'abord le lecteur qui reste ouvert : cent tâches, cent lectures, et pas cent processus.
    with suppress(OSError):
        lus: dict[str, bytes] = {}
        for objet in objets:
            trouve = interroge(repo, objet, contenus=True)
            if trouve is None:
                raise JudgeError(f"git cat-file : réponse inattendue pour {objet}")
            lus[objet] = trouve[2]
        return lus
    try:
        sortie = subprocess.run(
            ["git", *GIT_HARDENING, "-C", str(repo), "cat-file", "--batch"],
            input=("\n".join(objets) + "\n").encode(),
            capture_output=True,
            check=True,
            timeout=300,
            env=git_env(),
        ).stdout
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        raise JudgeError(f"git cat-file impossible : {exc}") from exc
    contenus: dict[str, bytes] = {}
    position = 0
    for objet in objets:
        fin = sortie.index(b"\n", position)
        entete = sortie[position:fin].split()
        if len(entete) != 3 or entete[0].decode() != objet:
            raise JudgeError(f"git cat-file : réponse inattendue pour {objet}")
        taille = int(entete[2])
        contenus[objet] = sortie[fin + 1 : fin + 1 + taille]
        position = fin + 1 + taille + 1
    return contenus


def trace_rouge(
    task_id: str, commit: str, preuves: dict[str, str], criteres: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "version": 1,
        "tache": task_id,
        "date": now_iso(),
        "commit": commit,
        "moteur_sha256": engine_hash(),
        "preuves": preuves,
        "criteres": criteres,
    }


def lire_trace_rouge(repo: Path, base: str, contract: Contract, task_id: str) -> dict[str, Any]:
    """La trace du contrôle rouge, lue sur la BASE, et qui doit encore dire la vérité."""
    chemin = f"{contract.ou}/{ROUGE}"
    refaire = (
        f"lancez `make task-check ID={task_id}` sur la branche principale, puis commitez "
        f"{chemin} avec le contrat (Makefile d'avant la 1.1 : `{COMMANDE_ROUGE} {task_id}`)"
    )
    try:
        brut = contenu_a(repo, base, chemin)
    except JudgeError:
        raise JudgeError(
            f"contrôle rouge absent de la base ({chemin}) : rien ne montre que les critères "
            f"échouaient avant le travail — {refaire}"
        ) from None
    try:
        trace = lire_json(brut.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, JudgeError):
        trace = None
    if (
        not isinstance(trace, dict)
        or not isinstance(trace.get("preuves"), dict)
        or not isinstance(trace.get("criteres"), list)
    ):
        raise JudgeError(f"contrôle rouge illisible ({chemin}) : {refaire}")
    rouges = {
        critere.get("id")
        for critere in trace["criteres"]
        if isinstance(critere, dict) and critere.get("resultat") == "rouge"
    }
    manquants = [c.id for c in contract.criteria if c.id not in rouges]
    if trace.get("tache") != task_id or manquants:
        quoi = ", ".join(manquants) if manquants else "écrit pour une autre tâche"
        raise JudgeError(f"contrôle rouge qui ne couvre pas ce contrat ({quoi}) : {refaire}")
    attendu: dict[str, Any] = trace["preuves"]
    present = preuves_rouges(repo, base, contract, task_id)
    changes = [
        nom for nom in sorted(attendu.keys() & present.keys()) if attendu[nom] != present[nom]
    ]
    # Qui l'a changé, et à qui d'autre appartient la preuve : sans quoi l'opérateur refait le
    # contrôle sans savoir pourquoi il a été périmé, et recommence à la prochaine fois.
    dehors = {nom for nom in changes if not nom.startswith(f"tasks/{task_id}/")}
    proprietaires = preuves_partagees(repo, base, contract, task_id, dehors) if dehors else {}
    commit_rouge = trace.get("commit")

    def reel(nom: str) -> str:  # le nom canonique, là où le fichier vit vraiment
        return relocalise(nom, task_id, contract.ou)

    ecarts = [
        f"{reel(nom)} a changé depuis"
        + pourquoi_perime(repo, commit_rouge, base, reel(nom), proprietaires.get(nom, []))
        for nom in changes
    ]
    ecarts += [f"{reel(nom)} ajouté depuis" for nom in sorted(present.keys() - attendu.keys())]
    ecarts += [f"{reel(nom)} retiré depuis" for nom in sorted(attendu.keys() - present.keys())]
    if ecarts:
        raise JudgeError(
            [f"contrôle rouge périmé : {ecart}" for ecart in ecarts]
            + [f"les critères n'ont pas été vus échouer sous leur forme actuelle : {refaire}"]
        )
    return trace


def pourquoi_perime(repo: Path, commit: Any, base: str, nom: str, autres: list[str]) -> str:
    """« (modifié par 1a2b3c4 « sujet » ; preuve aussi de T-002) », ou rien."""
    raisons: list[str] = []
    depuis = [base]
    if isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40,64}", commit):
        depuis = [f"{commit}..{base}"]
    try:
        dernier = git(repo, "log", "-1", "--format=%h%x09%s", *depuis, "--", nom).decode().strip()
    except JudgeError:
        dernier = ""
    if "\t" in dernier:
        court, sujet = dernier.split("\t", 1)
        raisons.append(f"modifié par {court} « {sujet} »")
    if autres:
        raisons.append(f"preuve aussi de {', '.join(autres)}")
    return f" ({' ; '.join(raisons)})" if raisons else ""


def changes_depuis(repo: Path, commit: Any, base: str) -> int | None:
    """Fichiers changés hors de tasks/ entre le contrôle rouge et la base. Le juge ne rejoue
    pas les critères sur la base : il le dit au relecteur quand le code a bougé entre-temps."""
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        return None
    try:
        git(repo, "merge-base", "--is-ancestor", commit, base)
    except JudgeError:
        return None
    noms = git(repo, "diff", "--name-only", "-z", "--no-renames", commit, base)
    return len([nom for nom in noms.split(b"\0") if nom and not nom.startswith(b"tasks/")])


def extrait(log: Path, debut: int = 0, code: int | None = None) -> str:
    """La ligne qui dit pourquoi une preuve a échoué, pour que l'opérateur voie que c'est la
    bonne raison : la dernière erreur que pytest détaille (son résumé est coupé à la largeur
    du terminal), sinon ce résumé, sinon la dernière ligne.

    Lue après l'en-tête que le juge écrit : pour une preuve en script, il recopie l'amorce,
    sur plusieurs lignes, et un script muet affichait un morceau de la commande en guise de
    raison (reproduit le 26/09/2026)."""
    try:
        texte = log.read_bytes()[debut:].decode("utf-8", "replace")
    except OSError:
        return "journal illisible"
    utiles = [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]
    muet = "aucune sortie" + (f", code de retour {code}" if code is not None else "")
    choix = (
        [ligne[1:].strip() for ligne in utiles if ligne.startswith("E ")]
        or [ligne for ligne in utiles if ligne.startswith("FAILED ")]
        or utiles
        or [muet]
    )
    return choix[-1][:200]


def rouge_de_travers(rapport: Path, attendus: Sequence[tuple[str, tuple[str, ...]]]) -> str | None:
    """Une preuve pytest rouge pour une mauvaise raison ? None si chaque test désigné a
    tourné et échoué.

    Mesuré le 26/09/2026 avec pytest : un nom de test mal écrit ou un chemin faux laisse un
    rapport SANS aucun test ; un test qui importe une fonction pas encore écrite laisse une
    erreur au niveau du fichier, et c'est un vrai rouge. Un test désigné dont tous les cas
    passent déjà ne prouve rien du travail à venir."""
    if rapport.is_symlink() or not rapport.is_file():
        return "pytest n'a rendu aucun rapport : la commande échoue avant les tests"
    brut = rapport.read_bytes()
    if b"<!DOCTYPE" in brut.upper() or b"<!ENTITY" in brut.upper():
        return "rapport de tests illisible"
    try:
        cas = list(ET.fromstring(brut).iter("testcase"))
    except ET.ParseError:
        return "rapport de tests illisible"
    for fichier, parties in attendus:
        nom = "::".join([fichier, *parties])
        vus = [
            c for c in cas if _couvre(c.get("classname", ""), c.get("name", ""), fichier, parties)
        ]
        du_fichier = [
            c
            for c in cas
            if c.find("error") is not None
            and _couvre(c.get("classname", ""), c.get("name", ""), fichier, ())
        ]
        if not vus and not du_fichier:
            return f"test désigné jamais lancé : {nom} (nom ou chemin mal écrit ?)"
        if (
            vus
            and not du_fichier
            and all(c.find("failure") is None and c.find("error") is None for c in vus)
        ):
            return f"test désigné qui ne fait pas échouer le critère : {nom}"
    if not cas:
        # Un dossier désigné sans aucun test : pytest ne lance rien et sort en erreur.
        return "pytest n'a lancé aucun test (chemin mal écrit, ou dossier sans test ?)"
    return None


def rapports_xml(dossier: Path) -> list[Path]:
    """Les rapports XML d'un dossier écrit par l'outil de test, sans suivre aucun lien."""
    trouves: list[Path] = []
    for racine, _, noms in os.walk(dossier):
        for nom in sorted(noms):
            chemin = Path(racine) / nom
            if nom.endswith(".xml") and not chemin.is_symlink() and chemin.is_file():
                trouves.append(chemin)
    return trouves


def cas_junit(dossier: Path) -> list[ET.Element] | str:
    """Les cas de test des rapports JUnit d'un dossier, ou pourquoi on ne peut pas les lire."""
    fichiers = rapports_xml(dossier)
    if not fichiers:
        return "aucun rapport JUnit"
    cas: list[ET.Element] = []
    for chemin in fichiers:
        if chemin.stat().st_size > MAX_REPORT_BYTES:
            return f"rapport JUnit trop volumineux : {chemin.name}"
        brut = chemin.read_bytes()
        if b"<!DOCTYPE" in brut.upper() or b"<!ENTITY" in brut.upper():
            return f"rapport JUnit refusé : {chemin.name} déclare un DOCTYPE ou une entité XML"
        try:
            # Sans DOCTYPE ni entité, expat n'a rien à développer : pas de bombe XML possible.
            cas += list(ET.fromstring(brut).iter("testcase"))
        except ET.ParseError:
            return f"rapport JUnit illisible : {chemin.name}"
    return cas


def junit_designe(cas: ET.Element, designe: str) -> bool:
    """Un test désigné par `Classe.test`, `Classe#test`, son seul nom, ou toute sa classe."""
    classe, nom = cas.get("classname", ""), cas.get("name", "")
    voulu = designe.replace("#", ".")
    complet = f"{classe}.{nom}" if classe else nom
    return (
        voulu in (nom, classe, complet)
        or complet.endswith("." + voulu)
        or classe.endswith("." + voulu)
    )


def verifier_rapports_junit(dossier: Path, designes: Sequence[str]) -> str | None:
    """Les rapports JUnit d'une preuve disent-ils qu'elle est allée au bout ? None si oui :
    chaque test désigné a tourné, et aucun de ses cas n'a échoué ni n'a été sauté. Le dossier
    est neuf, créé par le juge juste avant : un rapport qui s'y trouve vient de ce passage."""
    cas = cas_junit(dossier)
    if isinstance(cas, str):
        return f"{cas} : l'outil de test s'est arrêté avant, ou n'écrit pas dans {{rapport}}"
    for designe in designes:
        vus = [c for c in cas if junit_designe(c, designe)]
        if not vus:
            return f"test désigné absent du rapport : {designe}"
        if any(c.find(etat) is not None for c in vus for etat in ("failure", "error", "skipped")):
            return f"test désigné en échec ou sauté : {designe}"
    return None


def rouge_junit_de_travers(dossier: Path, designes: Sequence[str]) -> str | None:
    """Une preuve JUnit rouge pour une mauvaise raison ? Un rapport sans le test désigné (nom
    mal écrit), ou où il réussit déjà. Sans aucun rapport, on ne sait pas : un test Java qui
    appelle une classe pas encore écrite ne compile pas, et c'est un vrai rouge. La raison
    affichée, que l'opérateur lit, tranche."""
    cas = cas_junit(dossier)
    if isinstance(cas, str):
        return None
    for designe in designes:
        vus = [c for c in cas if junit_designe(c, designe)]
        if not vus:
            return f"test désigné jamais lancé : {designe} (nom mal écrit ?)"
        if all(c.find(etat) is None for c in vus for etat in ("failure", "error")):
            return f"test désigné qui ne fait pas échouer le critère : {designe}"
    return None


def empreintes_preuves(copy: Path, guarded: set[str]) -> dict[str, str]:
    """Empreinte de chaque preuve, ici et maintenant.

    Le contenu ne suffit pas : une preuve abîmée puis remise à l'identique DANS la même
    étape redevient identique avant qu'on regarde. La date de modification, elle, garde
    la trace du passage. « absente » est une valeur comme une autre : une preuve supprimée
    puis recréée doit se voir aussi.
    """
    releve: dict[str, str] = {}
    for nom in sorted(guarded):
        fichier = copy / nom
        try:
            etat = fichier.stat()
            empreinte = hashlib.sha256(fichier.read_bytes()).hexdigest()
            releve[nom] = f"{etat.st_mtime_ns}:{empreinte}"
        except OSError:
            releve[nom] = "absente"
    return releve


# Ce que le relevé ne parcourt pas : l'environnement installé, les caches, git. Voir « Pas
# fermé » dans .ephor/README.md pour ce que cela laisse ouvert.
SAUTES_AU_RELEVE = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".ephor-cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".hypothesis",
    ".tox",
    ".verify",
}


def a_surveiller(copy: Path, dossiers: Sequence[str]) -> set[str]:
    """Ce dont les preuves dépendent, tel qu'il est maintenant dans la copie.

    Le code jugé tourne dès l'étape de vérification, avec le droit d'écrire dans la copie.
    Mesuré le 25/09/2026 : un test de l'agent réécrivait, pendant la vérification, le test
    d'acceptation rangé dans le dossier que le critère citait (`pytest tasks/T-1/`), et le
    code faux ressortait VÉRIFIÉ. Sont donc relevés à chaque étape : tout ce qui vit dans
    les dossiers de preuve, et partout ailleurs ce qui s'exécute sans être appelé ou règle
    les contrôles — y compris un fichier qui n'existait pas au départ.
    """
    prefixes = tuple(dossier.rstrip("/") + "/" for dossier in dossiers if dossier != ".")
    trouves: set[str] = set()
    for dossier, sous, noms in os.walk(copy):
        sous[:] = [nom for nom in sous if nom not in SAUTES_AU_RELEVE]
        ici = Path(dossier).relative_to(copy).as_posix()
        for nom in noms:
            chemin = nom if ici == "." else f"{ici}/{nom}"
            if (
                chemin.startswith(prefixes)
                or nom in DEMARRAGE
                or nom in D_OFFICE
                or nom.endswith(".pth")
                or nom in REGLAGES_CONTROLES
                or nom in PORTEURS_DE_DEPENDANCES
                or nom.startswith(PREFIXES_REGLAGES)
            ):
                trouves.add(chemin)
    return trouves


def suivis_modifies(copy: Path) -> list[str]:
    """Fichiers suivis de la copie qui ne sont plus ceux qu'on a jugés.

    L'index de la copie porte la base, le correctif et ce que l'installation a changé. Un
    fichier du correctif réécrit ensuite par le code jugé, et ce qui a été vérifié n'est plus
    ce qui sera livré : le manifeste de livraison attesterait un code que personne n'a vu
    passer les critères.
    """
    noms = git(copy, "diff", "--name-only", "-z", "--no-renames")
    return sorted({nom.decode("utf-8", "replace") for nom in noms.split(b"\0") if nom})


def scope_errors(repo: Path, base: str, contract: Contract, paths: list[str]) -> list[str]:
    errors: list[str] = []
    guarded = acceptance_files(repo, base, contract)
    root = repo.resolve()
    for name in paths:
        if not safe_path(name):
            errors.append(f"chemin invalide : {name}")
        elif is_protected(name):
            errors.append(f"fichier de contrôle modifié : {name}")
        elif name in guarded:
            errors.append(f"test d'acceptation modifié : {name}")
        elif name not in ALWAYS_ALLOWED and not any(
            matches(name, rule) for rule in contract.allowed_paths
        ):
            errors.append(f"hors du périmètre du contrat : {name}")
        else:
            target = repo / name
            if target.is_symlink():
                errors.append(f"lien symbolique refusé : {name}")
            elif target.exists() and not target.resolve().is_relative_to(root):
                errors.append(f"chemin qui sort du dépôt : {name}")
    return errors


def create_copy(repo: Path, copy: Path, base: str) -> None:
    """La copie propre : un clone à part, jamais un `git worktree` de votre dépôt.

    Un worktree partage la configuration, les références et les hooks du dépôt d'origine :
    un test qui lançait `git config` ou `git commit` dans la copie écrivait DANS TON dépôt.
    Le clone emprunte les objets en lecture (`--shared`) et n'a rien d'autre en commun.
    """
    copy.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "clone", "--quiet", "--shared", "--no-checkout", "--template=", str(repo), str(copy))
    git(copy, "checkout", "--quiet", "--detach", base)
    git(copy, "remote", "remove", "origin")


def rendre_autonome(copy: Path) -> None:
    """La copie, sans plus rien emprunter à votre dépôt : pour le conteneur.

    `--shared` fait lire à la copie les objets de votre dépôt par un chemin du poste, que le
    conteneur ne voit pas : git y trouvait un dépôt cassé, et un projet versionné par git
    (hatch-vcs, setuptools-scm) ne s'installait pas — VÉRIFIÉ en local, ÉCHEC en conteneur
    (audit du 29/09/2026). Seul ce que la base atteint est recopié : son histoire et ses
    étiquettes, pas vos autres branches."""
    a_retirer = git(copy, "for-each-ref", "--format=delete %(refname)", "refs/heads")
    a_retirer += git(
        copy, "for-each-ref", "--no-merged", "HEAD", "--format=delete %(refname)", "refs/tags"
    )
    if a_retirer:
        git(copy, "update-ref", "--stdin", entree=a_retirer)
    git(copy, "reflog", "expire", "--expire=now", "--all")
    git(copy, "repack", "-a", "-d", "-q")
    (copy / ".git" / "objects" / "info" / "alternates").unlink(missing_ok=True)


# Ce qui tire la version d'un projet de son histoire git, à l'installation.
VERSION_PAR_GIT = re.compile(
    r"\b(setuptools[-_]scm|hatch-vcs|versioningit|dunamai|poetry-dynamic-versioning"
    r"|uv-dynamic-versioning|setuptools-git-versioning|use_scm_version)\b"
)


def version_par_git(copy: Path) -> str | None:
    """L'outil qui tire la version du projet de git à l'installation, s'il y en a un."""
    for nom in ("pyproject.toml", "setup.cfg", "setup.py"):
        with suppress(OSError):
            texte = (copy / nom).read_text(encoding="utf-8", errors="replace")
            trouve = VERSION_PAR_GIT.search(texte)
            if trouve:
                return trouve.group(1)
    return None


def image_a_git(runtime: Runtime) -> bool:
    """L'image a-t-elle git ? Lancée comme le juge la lance, sans réseau."""
    assert runtime.image is not None
    extra = shlex.split(os.environ.get("EPHOR_CONTAINER_ARGS", ""))
    commande = [runtime.kind, "run", "--rm", "--pull=never", "--network", "none"]
    commande += identite_du_poste(runtime.kind, extra) + extra
    try:
        sonde = subprocess.run(
            [*commande, runtime.image, "git", "--version"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return sonde.returncode == 0


def preparer_pour_le_conteneur(runtime: Runtime, copy: Path, installe: bool) -> None:
    """La copie rendue autonome ; et, pour un projet qui a besoin de git pour s'installer,
    une image qui a git — sinon le dire, plutôt que l'erreur de setuptools-scm, qui ne dit
    rien de la vraie cause."""
    rendre_autonome(copy)
    outil = version_par_git(copy) if installe else None
    if outil and not image_a_git(runtime):
        raise JudgeError(
            f"le projet tire sa version de git ({outil}) et l'image {runtime.image} n'a pas"
            " git : l'installation échouerait. Prenez une image avec git (make ci-image, puis"
            " EPHOR_IMAGE=<image>), ou jugez en local (RUNTIME=local)"
        )


def remove_copy(copy: Path) -> None:
    """Efface la copie, même si un outil y a laissé des dossiers en lecture seule."""
    for dossier, sous_dossiers, _ in os.walk(copy):
        for nom in sous_dossiers:
            chemin = Path(dossier) / nom
            if not chemin.is_symlink():  # un lien mènerait hors de la copie : on n'y touche pas
                with suppress(OSError):
                    chemin.chmod(0o700)
    shutil.rmtree(copy, ignore_errors=True)


# --------------------------------------------------------------------------- exécution


def filtered_env(phase: str, has_setup: bool) -> dict[str, str]:
    """Environnement des commandes locales : le strict nécessaire, jamais de clé d'API."""
    env: dict[str, str] = {}
    for name, value in os.environ.items():
        basic = name in BASE_ENV or name.startswith("LC_")
        # UV_CACHE_DIR est la seule variable « hôte » utile en local : elle évite de
        # retélécharger tout l'index à chaque jugement. Les autres resteraient collées à
        # l'environnement du poste et fausseraient la copie propre.
        uv_config = (
            name.startswith("UV_")
            and name not in UV_HOST_ONLY - {"UV_CACHE_DIR"}
            and not name.startswith("UV_PUBLISH")
            and not SECRET_HINT.search(name)
        )
        setup_only = phase == "setup" and (
            name in SETUP_ONLY_ENV or INDEX_CREDENTIAL.match(name) is not None
        )
        if basic or setup_only:
            env[name] = value
        elif uv_config:
            env[name] = value if phase == "setup" else sans_identifiants(value)
    env.update(
        {
            "VERIFY_STRICT": "1",
            "EPHOR_JUDGE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    if phase != "setup" and has_setup:
        env["UV_FROZEN"] = "1"
    return env


def container_env_names(phase: str, has_setup: bool) -> dict[str, str]:
    """Variables fixées dans le conteneur. Les autres sont relayées par nom, sans valeur visible."""
    env = {
        "VERIFY_STRICT": "1",
        "EPHOR_JUDGE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": "/tmp",  # noqa: S108 - /tmp du conteneur, pas de l'hôte
        "UV_CACHE_DIR": "/work/.ephor-cache/uv",
        "UV_LINK_MODE": "copy",
        "UV_PYTHON_DOWNLOADS": "never",
    }
    if phase != "setup":
        env["UV_OFFLINE"] = "1"
        if has_setup:
            env["UV_FROZEN"] = "1"
    return env


def relayed_names(phase: str) -> list[str]:
    """Variables passées AU CONTENEUR par leur nom seul : leur valeur ne s'affiche jamais.

    Une valeur qui commence par `/` est un chemin du poste : elle ne désigne rien dans le
    conteneur, et la relayer ferait échouer l'installation pour une raison incompréhensible.
    """
    names: list[str] = []
    for name, value in os.environ.items():
        if name.startswith("UV_"):
            if name in UV_HOST_ONLY or name.startswith("UV_PUBLISH") or value.startswith("/"):
                continue
            if SECRET_HINT.search(name) and not (phase == "setup" and INDEX_CREDENTIAL.match(name)):
                continue
            names.append(name)
        elif phase == "setup" and name in SETUP_ONLY_ENV:
            names.append(name)
    return sorted(names)


def kill_group(pid: int) -> None:
    with suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def run_process(
    argv: list[str],
    cwd: Path,
    log: Path,
    timeout: float,
    env: dict[str, str],
    on_timeout: Callable[[], None] | None = None,
    entree: str | None = None,
) -> dict[str, Any]:
    """Borne la durée ET la sortie, et ne laisse aucun processus derrière l'étape.

    Deux règles. Un processus lancé en arrière-plan par une étape survivait à celle-ci :
    il pouvait encore écrire dans la copie pendant les étapes suivantes, après le relevé
    des preuves. Et une sortie sans fin remplissait le disque.
    """
    start = time.monotonic()
    entete = f"$ {shlex.join(argv)}\n".encode()
    base = {"output_limit": False, "output_offset": len(entete)}
    with log.open("wb") as stream, tempfile.TemporaryFile() as source:
        stream.write(entete)
        stream.flush()
        if timeout <= 0:
            stream.write(b"budget de temps epuise avant le lancement\n")
            return {**base, "returncode": 124, "timed_out": True, "seconds": 0.0}
        if entree is not None:
            source.write(entree.encode("utf-8"))
            source.seek(0)
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=stream,
                stderr=subprocess.STDOUT,
                stdin=source if entree is not None else subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
        except OSError as exc:
            stream.write(f"{exc}\n".encode())
            return {**base, "returncode": 127, "timed_out": False, "seconds": 0.0}
        timed_out = trop_bavard = False
        try:
            while process.poll() is None:
                if time.monotonic() - start >= timeout:
                    timed_out = True
                    break
                if log.stat().st_size > MAX_LOG_BYTES:
                    trop_bavard = True
                    break
                time.sleep(0.05)
            if timed_out or trop_bavard:
                kill_group(process.pid)
                if on_timeout is not None:
                    on_timeout()
            code = process.wait()
        except BaseException:
            kill_group(process.pid)
            process.wait()
            raise
        finally:
            # Le chef du groupe a fini ; ses enfants lancés en arrière-plan, pas forcément.
            kill_group(process.pid)
    # Une sortie énorme mais rapide passe entre deux coups d'œil : on regarde aussi à la fin.
    trop_bavard = trop_bavard or log.stat().st_size > MAX_LOG_BYTES
    if trop_bavard:
        with log.open("r+b") as coupe:
            coupe.truncate(MAX_LOG_BYTES)
    return {
        **base,
        "returncode": code,
        "timed_out": timed_out,
        "output_limit": trop_bavard,
        "seconds": round(time.monotonic() - start, 2),
    }


@dataclass
class Runtime:
    kind: str
    image: str | None = None
    note: str = ""

    def python(self) -> str:
        return sys.executable if self.kind == "local" else "python3"


def engine_ready(engine: str) -> bool:
    if shutil.which(engine) is None:
        return False
    try:
        result = subprocess.run([engine, "info"], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def choose_runtime(requested: str, image: str | None) -> Runtime:
    chosen_image = image or os.environ.get("EPHOR_IMAGE") or DEFAULT_IMAGE
    if requested == "local":
        return Runtime("local")
    if requested in ("podman", "docker"):
        if not engine_ready(requested):
            hint = " (podman machine start ?)" if requested == "podman" else ""
            raise JudgeError(f"{requested} indisponible ou arrêté{hint}")
        return Runtime(requested, chosen_image)
    for engine in ("podman", "docker"):
        if engine_ready(engine):
            return Runtime(engine, chosen_image)
    return Runtime("local", note="aucun moteur de conteneur prêt : exécution locale")


def ensure_image(runtime: Runtime, log: Path) -> None:
    assert runtime.image is not None
    inspect = subprocess.run(
        [runtime.kind, "image", "inspect", runtime.image],
        capture_output=True,
        timeout=60,
        check=False,
    )
    if inspect.returncode == 0:
        return
    result = run_process(
        [runtime.kind, "pull", runtime.image], log.parent, log, 900, dict(os.environ)
    )
    if result["returncode"] != 0:
        # « introuvable » était le seul mot rendu quand le registre refusait l'accès :
        # en entreprise, l'image est privée et la cause la plus fréquente est l'absence de
        # connexion, pas une faute de frappe.
        registre = runtime.image.split("/")[0] if "/" in runtime.image else ""
        piste = ""
        if "." in registre or ":" in registre:
            piste = f" Registre privé ? Connectez-vous d'abord : {runtime.kind} login {registre}."
        raise JudgeError(
            f"image {runtime.image} absente du poste et impossible à télécharger "
            f"(journal : {log.name}).{piste}"
            " Pour en désigner une autre : EPHOR_IMAGE=<image>, ou make judge IMAGE=<image>."
        )


def remove_container(engine: str, name: str) -> None:
    subprocess.run([engine, "rm", "-f", name], capture_output=True, check=False, timeout=60)


# Options par lesquelles l'opérateur règle déjà l'espace des utilisateurs, ou entre dans un
# pod : Podman les refuse à côté de `--userns=keep-id` (« mutually exclusive », « cannot set
# user namespace mode when joining pod », mesuré en 6.1.2).
REGLE_DEJA_L_IDENTITE = (
    "--user",
    "-u",
    "--uidmap",
    "--gidmap",
    "--subuidname",
    "--subgidname",
    "--pod",
)


def identite_du_poste(kind: str, extra: list[str]) -> list[str]:
    """Sous Podman Linux sans droits root, le conteneur tourne sous l'identité du poste.

    Mesuré le 24/09/2026 dans une Fedora 44 (SELinux actif, Podman 6.1.2 sans droits
    root) : une image qui déclare son propre utilisateur ne pouvait pas écrire dans la
    copie jugée — « Permission denied » dès l'installation, et les quatre agents de la
    démonstration tombaient en ÉCHEC, l'honnête compris. `--userns=keep-id` seul ne suffit
    pas : l'image garde son utilisateur. Avec l'identité du poste en plus, l'image de la
    maison comme celle par défaut rendent les verdicts du local, et tout ce que le conteneur
    écrit appartient au poste. Sur Mac, la machine virtuelle de Podman fait ce travail
    elle-même : rien n'est ajouté. Un opérateur qui règle déjà l'identité garde la main —
    et une étape qui doit écrire dans le système de l'image la reprend avec `--user=0`.
    """
    # Dans une variable : mypy, qui connaît la plateforme où il tourne, tiendrait sinon la
    # suite pour du code mort sur un Mac.
    sous_linux = sys.platform.startswith("linux")
    if kind != "podman" or not sous_linux or os.getuid() == 0:
        return []
    if any(option.startswith(REGLE_DEJA_L_IDENTITE) for option in extra):
        return []
    return ["--userns=keep-id", f"--user={os.getuid()}:{os.getgid()}"]


def container_command(
    runtime: Runtime,
    argv: list[str],
    copy: Path,
    phase: str,
    has_setup: bool,
    name: str,
    preuves: Path | None = None,
    entree: bool = False,
) -> tuple[list[str], dict[str, str]]:
    assert runtime.image is not None
    fixed = container_env_names(phase, has_setup)
    suffix = os.environ.get("EPHOR_VOLUME_SUFFIX")
    if suffix is None:
        suffix = ":Z" if runtime.kind == "podman" and sys.platform.startswith("linux") else ""
    # PAS de montage en lecture seule sur /work, et c'est mesuré, pas frileux : le cache
    # d'uv, l'environnement virtuel et les caches de pytest vivent DEDANS. En lecture seule,
    # une vérification ordinaire meurt sur « Read-only file system » — donc le bac à sable,
    # qui est l'un des arguments d'Ephor, devient inutilisable. L'idée reste bonne :
    # elle demande de sortir d'abord les caches vers un montage temporaire inscriptible.
    command = [
        runtime.kind,
        "run",
        "--rm",
        # L'image a été vérifiée avant la première étape : aucune autre ne doit arriver en
        # cours de route sous le même nom.
        "--pull=never",
        "--name",
        name,
        "--memory",
        os.environ.get("EPHOR_CONTAINER_MEMORY", "4g"),
        "--cpus",
        os.environ.get("EPHOR_CONTAINER_CPUS", "2"),
        "--pids-limit",
        "1024",
        "--security-opt",
        "no-new-privileges",
    ]
    # Durcissement : aucun privilège noyau, système de fichiers de l'image en lecture seule,
    # /tmp en mémoire. /work reste inscriptible (voir plus haut). Mesuré avec docker sur Mac
    # Apple Silicon, juge complet : même verdict.
    # Une image de la maison qui aurait besoin de plus : EPHOR_CONTAINER_HARDENING=0.
    if os.environ.get("EPHOR_CONTAINER_HARDENING", "1") != "0":
        command += [
            "--cap-drop=ALL",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,nodev,size=1g",  # noqa: S108 - /tmp du conteneur
        ]
    command += ["-v", f"{copy}:/work{suffix}", "-w", "/work"]
    if preuves is not None:
        command += ["-v", f"{preuves}:{PREUVES_CONTENEUR}{suffix}"]
    if entree:
        command.append("-i")
    if phase != "setup":
        command += ["--network", "none"]
    client_env = dict(os.environ)
    for key, value in fixed.items():
        command += ["-e", f"{key}={value}"]
    for key in relayed_names(phase):
        # Relayée par son nom, la valeur ne s'affiche pas. Mais un identifiant dans une
        # adresse d'index n'a rien à faire hors installation : l'adresse passe alors sans
        # lui, en clair, puisqu'elle ne contient plus rien de secret.
        valeur = os.environ.get(key, "")
        propre = valeur if phase == "setup" else sans_identifiants(valeur)
        command += ["-e", key if propre == valeur else f"{key}={propre}"]
    extra = shlex.split(os.environ.get("EPHOR_CONTAINER_ARGS", ""))
    command += identite_du_poste(runtime.kind, extra) + extra
    command += [runtime.image, *argv]
    return command, client_env


# --------------------------------------------------------------------------- rapport


def write_junit(path: Path, task_id: str, results: list[dict[str, Any]]) -> None:
    failures = sum(1 for item in results if not item["ok"])
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        f'<testsuite name="juge {xml_escape(task_id)}" tests="{len(results)}" '
        f'failures="{failures}">',
    ]
    for item in results:
        name = xml_escape(item["name"])
        lines.append(
            f'  <testcase classname="juge.{xml_escape(task_id)}" name="{name}" '
            f'time="{item.get("seconds", 0)}">'
        )
        if not item["ok"]:
            message = xml_escape(item.get("detail", "échec"), {'"': "&quot;"})
            lines.append(f'    <failure message="{message}"/>')
        lines.append("  </testcase>")
    lines.append("</testsuite>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


VERDICTS = {
    "verified_needs_review": "VÉRIFIÉ, reste la revue humaine (rien n'est fusionné ni livré)",
    "verification_failed": "ÉCHEC, lisez seulement le journal indiqué",
    "budget_exhausted": "ÉCHEC, budget de temps épuisé",
    "stale": "PÉRIMÉ, le code a changé pendant la vérification : relancez",
    "no_change": "RIEN À JUGER, aucun changement par rapport à la base",
    "rejected": "REFUSÉ, rien n'a été exécuté",
}


def milliers(nombre: int) -> str:
    return f"{nombre:,}".replace(",", " ")


def read_usage(repo: Path, task_id: str) -> dict[str, Any] | None:
    """Ce que la session a coûté, mesuré par le hook de budget. Absent = non mesuré."""
    fichier = repo / ".verify" / "usage" / f"{task_id}.json"
    try:
        mesure = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(mesure, dict) or "facturables" not in mesure:
        return None
    return {
        cle: mesure[cle]
        for cle in (
            "source",
            "requetes",
            "input",
            "output",
            "cache_write",
            "cache_read",
            "facturables",
            "cout_dollars",
            "nano_aiu",
            "requetes_premium",
            "detail_complet",
            "sous_agents",
        )
        if cle in mesure
    }


# `milliers` et `entier` existent aussi dans usage.py. Copie volontaire : le juge ne
# dépend d'aucun autre module d'Ephor, pour qu'on puisse le lire seul et s'y fier.
def entier(valeur: Any, defaut: int = 0) -> int:
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) else defaut


def usage_line(usage: dict[str, Any], budget: Any) -> str:
    # Sous Copilot, une session encore ouverte n'a pas donné ses tokens : ce qui est compté
    # est un minimum. Le dire, plutôt que de laisser croire que la tâche a coûté si peu.
    minimum = "au moins " if usage.get("detail_complet") is False else ""
    detail = (
        f"  tokens  {minimum}{milliers(entier(usage.get('facturables')))} facturables"
        f" · {milliers(entier(usage.get('cache_read')))} lus en cache"
        f" · {usage.get('requetes', '?')} requêtes ({usage.get('source', '?')})"
    )
    if entier(usage.get("sous_agents")):
        detail += f", dont {entier(usage.get('sous_agents'))} sous-agent(s)"
    if "nano_aiu" in usage:
        aiu = f"{entier(usage.get('nano_aiu')) / 1e9:.2f}".replace(".", ",")
        detail += f" · Copilot : {aiu} AIU, {entier(usage.get('requetes_premium'))} premium"
    if isinstance(budget, int):
        depasse = " DÉPASSÉ" if entier(usage.get("facturables")) > budget else ""
        detail += f" · budget {milliers(budget)}{depasse}"
    return detail


def reference_state(repo: Path) -> dict[str, str]:
    """Où en étaient les dépôts de référence : un verdict doit pouvoir se rejouer.

    Le juge ne les lit pas et ne les vérifie pas — il vérifie le dépôt de travail, hors
    ligne. Il note seulement leur commit, parce qu'un critère peut dépendre d'un schéma
    qui vit ailleurs.
    """
    # Importer un module voisin, c'est lire ET écrire un cache compilé dans `.ephor/`.
    # Écrit : dans un dépôt qui n'ignore pas `__pycache__/` — un dépôt Node, typiquement —,
    # le juge prenait ce cache pour une retouche de ses propres fichiers, et toute tâche
    # était REFUSÉE, ou PÉRIMÉE. Lu : un cache fabriqué à la main, ignoré par git donc
    # absent du patch, s'exécutait à la place du vrai module (reproduit le 24/09/2026).
    # Ni l'un ni l'autre : le cache est dirigé vers un chemin qui ne peut pas exister.
    sys.dont_write_bytecode = True
    sys.pycache_prefix = SANS_CACHE
    try:
        import refs  # import local : le juge marche sans
    except ImportError:
        return {}
    return refs.commits(repo)


def append_journal(repo: Path, report: dict[str, Any]) -> None:
    """Une ligne par jugement, gardée entre les nettoyages.

    Le rapport détaillé d'une tâche est écrasé à chaque exécution : sans ce journal, on
    ne peut suivre aucune tendance, et le rituel de désinstallation reste une intention.
    """
    criteria = report.get("criteria") or []
    usage = report.get("usage") or {}
    steps = report.get("steps") or []
    entry = {
        "run_id": report.get("run_id"),
        "task_id": report.get("task_id"),
        "finished_at": report.get("finished_at"),
        "status": report.get("status"),
        "niveau": report.get("niveau"),
        "effort": report.get("effort"),
        "criteria_passed": sum(1 for item in criteria if item.get("passed")),
        "criteria_total": len(criteria),
        "seconds": round(sum(float(step.get("seconds") or 0) for step in steps), 2),
        # Un minimum n'est pas un coût : compté, il ferait paraître la tâche bon marché.
        "billable_tokens": None
        if usage.get("detail_complet") is False
        else usage.get("facturables"),
        "runtime": (report.get("runtime") or {}).get("kind"),
        "base_commit": report.get("base_commit"),
        "head_commit": report.get("head_commit"),
        "engine_sha256": report.get("engine_sha256"),
    }
    path = repo / ".verify" / "journal.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # un journal qui échoue n'empêche jamais de rendre un verdict


def format_summary(report: dict[str, Any], lines: list[str]) -> str:
    head = f"juge {report['task_id']}"
    if report.get("engine_sha256"):
        head += f" · moteur {report['engine_sha256'][:8]}"
    if report.get("base_commit"):
        head += f" · base {report.get('base_ref')}@{report['base_commit'][:7]}"
    head += f" · exécution {report.get('runtime', {}).get('kind', '?')}"
    verdict = VERDICTS.get(report["status"], report["status"])
    if report["status"] == "verified_needs_review" and report.get("uncommitted_paths"):
        verdict += " — sur du travail pas encore commité : commitez-le tel quel"
    return "\n".join([head, *lines, f"VERDICT : {verdict}"]) + "\n"


# --------------------------------------------------------------------------- commandes


@dataclass
class Banc:
    """Une copie prête, et ce qu'on y lance : l'installation, la vérification, les critères.

    Le juge s'en sert pour rendre son verdict, et `make task-check` pour s'assurer que chaque
    critère échoue AVANT le travail : une preuve y est lancée et lue exactement de la même
    façon. Les preuves sont relevées avant la première étape, et recomparées après chacune.
    """

    repo: Path
    run_id: str
    runtime: Runtime
    copy: Path
    logs: Path
    preuves: Path
    report_dir: Path
    has_setup: bool
    deadline: float
    steps: list[dict[str, Any]]
    lines: list[str]
    junit: list[dict[str, Any]]
    gardees: set[str] = field(default_factory=set)
    dossiers: list[str] = field(default_factory=list)
    reference_preuves: dict[str, str] = field(default_factory=dict)
    preuves_abimees: list[str] = field(default_factory=list)
    any_timeout: bool = False

    def execute(
        self,
        label: str,
        key: str,
        argv: tuple[str, ...],
        phase: str,
        max_seconds: float | None = None,
        critere: Criterion | None = None,
    ) -> dict[str, Any]:
        resolved = [self.runtime.python() if part == "{python}" else part for part in argv]
        annonce = list(resolved)  # ce que le rapport montre : la commande du contrat
        forme = forme_de_preuve(critere) if critere is not None else "étape"
        jeton = ""
        rapport: Path | None = None
        rapports: Path | None = None
        # Chaque forme de preuve dit à sa façon qu'elle est allée au bout. Seule la
        # dernière, « code de retour seul », croit le processus sur parole : elle est
        # nommée au verdict, et refusée sur une tâche à risque élevé.
        index = position_script(argv)
        if forme == "jeton" and index is not None:
            jeton = "preuve-" + os.urandom(8).hex()
            resolved = [
                *resolved[: index + 1],
                "-c",
                AMORCE_PREUVE,
                jeton,
                *resolved[index + 1 :],
            ]
        elif forme == "rapport":
            nom = f"{key}-{os.urandom(6).hex()}.xml"
            rapport = self.preuves / nom
            cible = str(rapport) if self.runtime.kind == "local" else f"{PREUVES_CONTENEUR}/{nom}"
            resolved = [*resolved, f"--junitxml={cible}"]
        elif forme == "junit":
            rapports = self.preuves / f"{key}-{os.urandom(6).hex()}"
            rapports.mkdir()
            dossier = (
                str(rapports)
                if self.runtime.kind == "local"
                else f"{PREUVES_CONTENEUR}/{rapports.name}"
            )
            resolved = [part.replace("{rapport}", dossier) for part in resolved]
        entree = critere.stdin if critere is not None else None
        log = self.logs / f"{key}.log"
        cleanup: Callable[[], None] | None = None
        if self.runtime.kind == "local":
            command, env = resolved, filtered_env(phase, self.has_setup)
        else:
            name = f"ephor-judge-{self.run_id}-{key.lower()}"
            command, env = container_command(
                self.runtime,
                resolved,
                self.copy,
                phase,
                self.has_setup,
                name,
                preuves=self.preuves,
                entree=entree is not None,
            )
            cleanup = functools.partial(remove_container, self.runtime.kind, name)
        # Le maximum du critère coupe avant le budget global : un critère lent échoue
        # tout seul, il ne consomme pas le temps des suivants.
        limit = self.deadline - time.monotonic()
        bounded = False
        if max_seconds is not None and max_seconds < limit:
            limit, bounded = max_seconds, True
        result = run_process(command, self.copy, log, limit, env, on_timeout=cleanup, entree=entree)
        termine = not result["timed_out"] and not result["output_limit"]
        reason = ""
        if forme == "sortie" and critere is not None:
            # Comparée de l'extérieur, à l'octet près, sans l'en-tête que le juge écrit.
            sortie = log.read_bytes()[result["output_offset"] :].decode("utf-8", "replace")
            ok = termine and result["returncode"] == critere.returncode
            ok = ok and sortie == critere.expected_output
            if termine and result["returncode"] != critere.returncode:
                reason = f"code de retour {result['returncode']}, attendu {critere.returncode}"
            elif termine and not ok:
                reason = "sortie différente de la sortie attendue (voir le journal)"
        else:
            ok = termine and result["returncode"] == 0
        if ok and jeton:
            # Une LIGNE égale au jeton, pas le jeton n'importe où : le journal recopie
            # la commande lancée, donc le jeton s'y trouve déjà. Cherché à la légère,
            # ce contrôle se validait lui-même.
            sortie = log.read_text(encoding="utf-8", errors="replace")
            if not any(ligne.strip() == jeton for ligne in sortie.splitlines()):
                ok = False
                reason = "preuve interrompue avant la fin : le code jugé a coupé le processus"
        if rapport is not None:
            if ok and critere is not None:
                manque = verifier_rapport_pytest(rapport, tests_attendus(critere.check))
                if manque is not None:
                    ok, reason = False, manque
            if rapport.is_file() and not rapport.is_symlink():
                shutil.copy2(rapport, self.logs / f"{key}.junit.xml")
        if rapports is not None:
            if ok and critere is not None and critere.rapport_junit:
                manque = verifier_rapports_junit(rapports, critere.rapport_junit)
                if manque is not None:
                    ok, reason = False, manque
            garde = self.logs / f"{key}.junit"
            garde.mkdir(exist_ok=True)
            for xml in rapports_xml(rapports)[:50]:
                shutil.copy2(xml, garde / xml.name)
        if result["output_limit"]:
            ok = False
            reason = f"sortie de plus de {MAX_LOG_BYTES // 2**20} Mo : processus arrêté"
        too_slow = bounded and result["timed_out"]
        if too_slow:
            ok = False
        else:
            self.any_timeout = self.any_timeout or result["timed_out"]
        shown = relative(self.report_dir / "logs" / log.name, self.repo)
        if too_slow:
            reason = f"trop lent : maximum {max_seconds:.1f} s".replace(".", ",")
        mesure = {cle: valeur for cle, valeur in result.items() if cle != "output_offset"}
        self.steps.append({"name": key, "argv": annonce, **mesure, "log": f"logs/{log.name}"})
        seconds = f"{result['seconds']:.1f} s".replace(".", ",")
        status = "ok   " if ok else "FAIL "
        detail = "" if ok else f"  -> {reason or shown}"
        self.lines.append(f"  {status} {seconds:>8}  {label}{detail}")
        self.junit.append(
            {
                "name": label,
                "ok": ok,
                "seconds": result["seconds"],
                "detail": reason or shown,
            }
        )
        releves = (
            self.gardees | set(self.reference_preuves) | a_surveiller(self.copy, self.dossiers)
        )
        for nom, empreinte in empreintes_preuves(self.copy, releves).items():
            if (
                empreinte != self.reference_preuves.get(nom, "absente")
                and nom not in self.preuves_abimees
            ):
                self.preuves_abimees.append(nom)
        if phase == "setup":
            # L'installation est celle du contrat, écrite par l'opérateur : ce qu'elle
            # change à un fichier suivi (un verrou de dépendances) fait partie du jugé.
            git(self.copy, "add", "-u")
        else:
            for nom in suivis_modifies(self.copy):
                if nom not in self.preuves_abimees:
                    self.preuves_abimees.append(nom)
        return {
            "ok": ok,
            "too_slow": too_slow,
            "seconds": result["seconds"],
            "reason": reason,
            "forme": forme,
            "debut": result["output_offset"],
            "code": result["returncode"],
        }


def cmd_check(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    task_path, task_id = resolve_task(repo, args.task_id, args.task)
    run_id = uuid.uuid4().hex[:12]
    report_dir = (
        Path(args.report_dir).resolve()
        if args.report_dir
        else repo / ".verify" / "judge" / task_id / "latest"
    )
    workdir_root = Path(
        args.workdir
        or os.environ.get("EPHOR_WORKDIR")
        or Path.home() / ".cache" / "ephor" / "judge"
    ).expanduser()
    workdir = workdir_root / f"{repo.name}-{sha256(str(repo).encode())[:8]}" / run_id
    logs = workdir / "logs"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "engine_sha256": engine_hash(),
        "run_id": run_id,
        "task_id": task_id,
        "started_at": now_iso(),
        "repo": str(repo),
        "status": "running",
        "reasons": [],
        "steps": [],
        "criteria": [],
        "usage": None,
        "reference_repos": {},
        "review_required": True,
        "merged": False,
        "deployed": False,
        "runtime": {"kind": args.runtime, "image": None},
    }
    lines: list[str] = []
    junit: list[dict[str, Any]] = []

    def rendre_compte() -> None:
        """Tout ce qui est écrit à la fin, quel que soit le verdict, même après une erreur.

        Une fermeture plutôt qu'une fonction : elle lit dix variables de `cmd_check`, et
        les passer en paramètres déplacerait la densité sans l'enlever.
        """
        report["finished_at"] = now_iso()
        usage = read_usage(repo, task_id)
        if usage is not None:
            report["usage"] = usage
            lines.append(usage_line(usage, report.get("budget_tokens")))
        summary = format_summary(report, lines)
        if report_dir.exists() and not args.report_dir:
            shutil.rmtree(report_dir, ignore_errors=True)
        (report_dir / "logs").mkdir(parents=True, exist_ok=True)
        if logs.exists():
            for item in logs.iterdir():
                if item.is_dir() and not item.is_symlink():  # les rapports JUnit d'un critère
                    shutil.copytree(item, report_dir / "logs" / item.name, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, report_dir / "logs" / item.name)
        workdir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        for folder in (workdir, report_dir):
            (folder / "report.json").write_text(payload, encoding="utf-8")
            (folder / "summary.txt").write_text(summary, encoding="utf-8")
        write_junit(report_dir / "junit.xml", task_id, junit)
        append_journal(repo, report)
        print(summary, end="")

    copy = workdir / "copy"
    copy_created = False
    try:
        contract = load_contract(task_path, task_id)
        report["risk"] = contract.risk
        report["niveau"] = contract.niveau
        report["effort"] = contract.effort
        report["budget_tokens"] = contract.budget_tokens
        base_ref, base = resolve_base(repo, args.base)
        report.update(
            base_ref=base_ref,
            base_commit=base,
            head_commit=git(repo, "rev-parse", "HEAD").decode().strip(),
        )
        contract_bytes = task_path.read_bytes()
        report["contract_sha256"] = sha256(contract_bytes)
        rel_contract = PurePosixPath(task_path.relative_to(repo)).as_posix()
        try:
            approved = git(repo, "show", f"{base}:{rel_contract}")
        except JudgeError:
            approved = None
        if approved is None:
            raise JudgeError(
                "contrat absent de la base : faites-le valider et commiter sur la branche "
                "principale avant le travail de l'agent"
            )
        if approved != contract_bytes:
            raise JudgeError("contrat différent de la version approuvée sur la base")
        # Lu sur la base, comme le contrat : l'agent ne peut ni l'écrire ni le rafraîchir.
        rouge = lire_trace_rouge(repo, base, contract, task_id)
        report["rouge"] = {"date": rouge.get("date"), "commit": rouge.get("commit")}
        writable = writable_proofs(repo, base, contract)
        # Un dossier cité l'est tout entier : `pytest tests/` avec `tests/` dans le périmètre,
        # et l'agent réécrivait le test d'acceptation qui s'y trouvait — VÉRIFIÉ sur un code
        # jamais corrigé (reproduit le 25/09/2026).
        ouverts = open_dirs(repo, base, contract)
        if writable or ouverts:
            raise JudgeError(
                [
                    f"preuve dans le périmètre de l'agent et absente de la base : {path}"
                    for path in writable
                ]
                + ouverts
            )
        lines.append("  ok    contrat approuvé sur la base")
        junit.append({"name": "contrat approuvé", "ok": True})
        distante = pas_encore_sur_le_serveur(repo, base_ref, rel_contract, approved)
        if distante:
            lines.append(
                f"  info  approbation locale, pas encore sur le serveur ({distante}, dernier"
                " fetch) : la CI ne la verra qu'après le git push de la branche principale"
            )
        jour = str(rouge.get("date") or "?")[:10]
        lines.append(
            f"  ok    contrôle rouge du {jour} : les {len(contract.criteria)} critère(s)"
            " échouaient avant le travail"
        )
        junit.append({"name": "contrôle rouge", "ok": True})
        # Une preuve hors du dossier de la tâche, qui sert aussi une autre tâche : un critère
        # peut attendre le travail de l'autre (session réelle, 30/09/2026).
        dehors = {
            nom
            for nom in chemins_de_preuve(repo, base, contract, task_id)
            if not nom.startswith(f"{contract.ou}/")
        }
        partages = preuves_partagees(repo, base, contract, task_id, dehors) if dehors else {}
        for chemin, autres in list(partages.items())[:5]:
            lines.append(
                f"  info  {chemin} est aussi une preuve de {', '.join(autres)} : un critère peut"
                " dépendre de son travail, et changer ce fichier périme les deux contrôles rouges"
            )
        bouge = changes_depuis(repo, rouge.get("commit"), base)
        if bouge:
            lines.append(
                f"  info  {bouge} fichier(s) hors tasks/ ont changé sur la base depuis le contrôle"
                " rouge : les critères n'y ont pas été rejoués (à regarder en revue)"
            )
        elif bouge is None:
            # Contrôle lancé sur une autre branche, ou trace recopiée d'un autre dépôt (celle
            # d'EXEMPLE-001 dans un projet neuf) : rien ne dit sur quel code ils ont échoué.
            lines.append(
                f"  info  contrôle rouge fait sur un commit absent de l'histoire de la base"
                f" ({str(rouge.get('commit'))[:7]}) : rien ne dit sur quel code les critères"
                f" ont échoué ; make task-check ID={task_id} sur la branche principale le refait"
            )

        hors_commit: list[str] = []
        patch, paths = snapshot(repo, base, hors_commit=hors_commit)
        report.update(
            changed_paths=paths, patch_sha256=sha256(patch), uncommitted_paths=hors_commit
        )
        if not paths:
            report["status"] = "no_change"
            return 1
        errors = scope_errors(repo, base, contract, paths)
        if errors:
            raise JudgeError(errors)
        silences = silencieux_ajoutes(patch)
        report["silencers_added"] = silences
        demarrage, d_office = executes_d_office(paths)
        report["autorun_files"] = demarrage + d_office

        def base_et_disque(chemin: str) -> tuple[bytes | None, bytes | None]:
            fichier = repo / chemin
            return blob_a(repo, base, chemin), fichier.read_bytes() if fichier.is_file() else None

        reglages, porteurs = reglages_des_controles(paths, base_et_disque)
        report["control_config_files"] = reglages + porteurs
        retires = tests_retires(paths, base_et_disque)
        report["tests_removed"] = retires
        if retires and contract.risk == "eleve":
            raise JudgeError(
                [f"test retiré sur une tâche à risque élevé : {t}" for t in retires]
                + [
                    "un test ne se retire pas pour faire passer ; s'il doit vraiment partir,"
                    " c'est l'opérateur qui le retire sur la branche principale"
                ]
            )
        if reglages and contract.risk == "eleve":
            raise JudgeError(
                [f"réglage des contrôles modifié, sur un risque élevé : {c}" for c in reglages]
                + [
                    "il peut éteindre le lint, les types ou les tests sans un seul silencieux ;"
                    " s'il est vraiment nécessaire, c'est l'opérateur qui le change"
                ]
            )
        if demarrage and contract.risk == "eleve":
            raise JudgeError(
                [
                    f"fichier exécuté au démarrage de Python, sur un risque élevé : {c}"
                    for c in demarrage
                ]
                + [
                    "il tournerait avant chaque preuve ; retirez-le, ou faites-le ajouter"
                    " par l'opérateur"
                ]
            )
        if silences and contract.risk == "eleve":
            raise JudgeError(
                [f"silencieux ajouté sur une tâche à risque élevé : {item}" for item in silences]
                + [
                    "retirez-les ; si l'un est vraiment justifié, c'est l'opérateur qui l'ajoute"
                    " sur la branche principale"
                ]
            )
        lines.append(f"  ok    périmètre : {len(paths)} fichier(s)")
        junit.append({"name": "périmètre", "ok": True})
        if hors_commit:
            # Le juge juge le disque ; la merge request, elle, part de la branche. Un correctif
            # jamais commité ressortait VÉRIFIÉ sans un mot (audit du 29/09/2026).
            montres = ", ".join(hors_commit[:5]) + (" …" if len(hors_commit) > 5 else "")
            lines.append(
                f"  info  {len(hors_commit)} fichier(s) jugé(s) sur le disque, pas encore"
                f" commité(s) : {montres} — la branche ne les contient pas"
            )
        if silences:
            montres = ", ".join(silences[:5]) + (" …" if len(silences) > 5 else "")
            lines.append(
                f"  info  {len(silences)} silencieux ajouté(s), à justifier en revue : {montres}"
            )
        if retires:
            montres = ", ".join(retires[:5]) + (" …" if len(retires) > 5 else "")
            lines.append(f"  info  test(s) retiré(s), à justifier en revue : {montres}")
        if demarrage or d_office:
            lines.append(
                "  info  exécuté(s) sans être appelé(s), à lire d'abord en revue : "
                + ", ".join((demarrage + d_office)[:5])
                + (" …" if len(demarrage + d_office) > 5 else "")
            )
        if reglages:
            lines.append(
                "  info  réglage(s) des contrôles modifié(s), à lire en revue : "
                + ", ".join(reglages[:5])
                + (" …" if len(reglages) > 5 else "")
            )
        if porteurs:
            lines.append(
                f"  info  dépendance(s) modifiée(s), à lire en revue : {', '.join(porteurs)}"
            )
        faibles = [c.id for c in contract.criteria if forme_de_preuve(c) == "code"]
        if faibles:
            lines.append(
                f"  info  jugé(s) sur le seul code de retour : {', '.join(faibles)}"
                " (voir .ephor/README.md, « Ce que le juge ne peut pas voir »)"
            )
        avec_dossier = {cid for cid, _ in cited_dirs(repo, base, contract)}
        implicites = [
            c.id
            for c in contract.criteria
            if forme_de_preuve(c) == "rapport"
            and not tests_attendus(c.check)
            and c.id not in avec_dossier
        ]
        if implicites:
            lines.append(
                f"  info  pytest choisit lui-même ce qu'il lance : {', '.join(implicites)}"
                " (aucun fichier désigné : la preuve est ce que sa configuration collecte)"
            )

        # Seulement ici : tant que le patch n'est pas accepté, on ne lance rien d'après un
        # fichier de l'arbre de travail. La liste des dépôts de référence nomme des dossiers ;
        # lancer git dans un dossier que l'agent a nommé avant de l'avoir refusé, c'est lui obéir.
        report["reference_repos"] = reference_state(repo)
        if report["reference_repos"]:
            etats = " · ".join(f"{nom} {etat}" for nom, etat in report["reference_repos"].items())
            lines.append(f"  info  dépôts de référence lus : {etats}")

        runtime = choose_runtime(args.runtime, args.image)
        report["runtime"] = {"kind": runtime.kind, "image": runtime.image}
        if runtime.note:
            lines.append(f"  info  {runtime.note}")
        if runtime.image and "@sha256:" not in runtime.image:
            lines.append(
                f"  info  image {runtime.image} non épinglée par empreinte :"
                " à épingler avec EPHOR_IMAGE=<image>@sha256:<...>"
            )
        logs.mkdir(parents=True, exist_ok=True)
        preuves = workdir / "preuves"
        preuves.mkdir(parents=True, exist_ok=True)
        patch_file = workdir / "candidate.patch"
        patch_file.write_bytes(patch)
        if runtime.kind != "local":
            ensure_image(runtime, logs / "pull.log")
        create_copy(repo, copy, base)
        copy_created = True
        # `--index` : l'index de la copie porte ce qui est jugé. Tout fichier suivi qui s'en
        # écarte ensuite a été réécrit pendant l'exécution (voir `suivis_modifies`).
        git(copy, "apply", "--check", "--index", "--binary", "--whitespace=nowarn", str(patch_file))
        git(copy, "apply", "--index", "--binary", "--whitespace=nowarn", str(patch_file))
        if runtime.kind != "local":
            preparer_pour_le_conteneur(runtime, copy, contract.setup is not None)
        # Ce qui a été jugé, fichier par fichier : le manifeste de livraison s'en sert pour
        # refuser de livrer autre chose : la preuve est liée au contenu.
        report["files_sha256"] = {
            nom: sha256((copy / nom).read_bytes()) if (copy / nom).is_file() else None
            for nom in paths
        }

        # Les preuves sont relevées AVANT la première étape, et recomparées après CHACUNE.
        # Comparer une seule fois à la fin ne voyait pas le sabotage remis en état : abîmer
        # une preuve, la faire passer, puis la remettre d'aplomb ressortait « VÉRIFIÉ » avec
        # du code faux. Reproduit le 23 septembre 2026.
        gardees = acceptance_files(repo, base, contract)
        # Les dossiers de preuve : ceux que les critères citent, et celui de la tâche, où
        # vivent ses tests, leurs données et leurs aides.
        dossiers = sorted({d for _, d in cited_dirs(repo, base, contract)} | {contract.ou})
        reference_preuves = empreintes_preuves(copy, gardees | a_surveiller(copy, dossiers))
        banc = Banc(
            repo=repo,
            run_id=run_id,
            runtime=runtime,
            copy=copy,
            logs=logs,
            preuves=preuves,
            report_dir=report_dir,
            has_setup=contract.setup is not None,
            deadline=time.monotonic() + contract.timeout_seconds,
            steps=report["steps"],
            lines=lines,
            junit=junit,
            gardees=gardees,
            dossiers=dossiers,
            reference_preuves=reference_preuves,
        )

        setup_ok = True
        if contract.setup is not None:
            setup_ok = banc.execute("installation", "setup", contract.setup, "setup")["ok"]
        all_ok = setup_ok
        if setup_ok:
            all_ok = (
                banc.execute("vérification", "verify", contract.verify, "verify")["ok"] and all_ok
            )
            for criterion in contract.criteria:
                outcome = banc.execute(
                    f"{criterion.id} {criterion.description[:60]}",
                    criterion.id,
                    criterion.check,
                    "verify",
                    criterion.max_duration_seconds,
                    critere=criterion,
                )
                entry: dict[str, Any] = {
                    "id": criterion.id,
                    "description": criterion.description,
                    "passed": outcome["ok"],
                    "seconds": outcome["seconds"],
                    "proof": FORMES[outcome["forme"]],
                }
                if outcome["reason"]:
                    entry["reason"] = outcome["reason"]
                if criterion.max_duration_seconds is not None:
                    entry["max_duration_seconds"] = criterion.max_duration_seconds
                    entry["too_slow"] = outcome["too_slow"]
                report["criteria"].append(entry)
                all_ok = outcome["ok"] and all_ok

        if banc.preuves_abimees:
            # Constaté, donc consigné, que les critères aient réussi ou non : une preuve
            # touchée pendant l'exécution retire son sens à tout ce qui a été mesuré.
            all_ok = False
            detail = ", ".join(banc.preuves_abimees)
            lines.append(
                "  FAIL        preuve, réglage ou fichier jugé modifié pendant l'exécution : "
                + detail
            )
            junit.append({"name": "intégrité des preuves", "ok": False, "detail": detail})
            report["tampered_proofs"] = banc.preuves_abimees

        after, _ = snapshot(repo, base)
        if sha256(after) != report["patch_sha256"] or task_path.read_bytes() != contract_bytes:
            report["status"] = "stale"
        elif all_ok:
            report["status"] = "verified_needs_review"
        elif banc.any_timeout:
            report["status"] = "budget_exhausted"
        else:
            report["status"] = "verification_failed"
        return 0 if report["status"] == "verified_needs_review" else 1
    except JudgeError as exc:
        report["status"] = "rejected"
        report["reasons"] = exc.messages
        for message in exc.messages:
            lines.append(f"  REFUS {message}")
        junit.append({"name": "recevabilité", "ok": False, "detail": "; ".join(exc.messages)})
        return 2
    finally:
        if copy_created and not args.keep:
            remove_copy(copy)
        rendre_compte()


def cmd_validate(args: argparse.Namespace) -> int:
    repo = repo_root(args.repo)
    task_path, task_id = resolve_task(repo, args.task_id, args.task)
    try:
        contract = load_contract(task_path, task_id)
    except JudgeError as exc:
        for message in exc.messages:
            print(f"  REFUS {message}")
        return 2
    count = len(contract.criteria)
    print(f"contrat {contract.id} valide : {count} critère(s), risque {contract.risk}")
    if args.command == "validate":
        # Un projet mis à jour sans reprendre le nouveau Makefile lance encore cette forme
        # seule sous le nom de `make task-check` : le juge lui réclamerait une trace que
        # cette recette n'écrit jamais.
        print(
            f"forme seulement. Avant de commiter, le contrôle rouge : make task-check ID={task_id}"
            f" (Makefile d'avant la 1.1 : {COMMANDE_ROUGE} {task_id})"
        )
    return 0


def recevabilite(copy: Path, arbre: str, contract: Contract) -> list[str]:
    """Ce que le contrat promet d'échouer pour une mauvaise raison, dit avant de rien lancer :
    un dossier de preuve ouvert à l'agent (le juge le refuserait), une preuve introuvable (le
    critère échouerait sur « fichier absent », pas sur le comportement qui manque)."""
    problemes = open_dirs(copy, arbre, contract)
    a_ecrire = [
        nom
        for nom, texte in [("objective", contract.objective)]
        + [(c.id, c.description) for c in contract.criteria]
        if texte.lstrip().upper().startswith(A_ECRIRE)
    ]
    if a_ecrire:
        problemes.append(
            f"le contrat est encore le squelette de make task-new : « {A_ECRIRE} » dans"
            f" {', '.join(a_ecrire)}"
        )
    for critere in contract.criteria:
        forme = forme_de_preuve(critere)
        index = position_script(critere.check)
        fichiers = [critere.check[index + 1]] if forme == "jeton" and index is not None else []
        if forme == "rapport":
            fichiers += [fichier for fichier, _ in tests_attendus(critere.check)]
        fichiers += list(critere.preuves)
        for fichier in dict.fromkeys(fichiers):
            if safe_path(fichier) and not is_file_at(copy, arbre, fichier):
                problemes.append(f"{critere.id} : preuve introuvable, {fichier}")
    return problemes


def preuves_du_dossier(
    repo: Path, arbre: str, contract: Contract, task_id: str, chemins: Sequence[str]
) -> tuple[list[str], list[str]]:
    """(preuves de la tâche, le reste) parmi ces chemins modifiés du dossier.

    Les preuves : le dossier de la tâche, les fichiers et les dossiers que ses critères
    citent. Pas encore commitées quand l'opérateur lance le contrôle, elles y entrent. Le
    reste — un travail en cours, un brouillon — n'est pas ce que l'agent recevra : il reste
    dehors."""
    fichiers = set(cited_paths(repo, arbre, contract))
    dossiers = {dossier for _, dossier in cited_dirs(repo, arbre, contract)}
    if "." in dossiers:
        return list(chemins), []
    prefixes = (f"{contract.ou}/", *(dossier.rstrip("/") + "/" for dossier in dossiers))
    retenus = [c for c in chemins if c in fichiers or c.startswith(prefixes)]
    return retenus, [c for c in chemins if c not in retenus]


def pre_commit_du_depot(repo: Path) -> list[str] | None:
    """La commande pre-commit que `git commit` lancera sur ce dépôt ; None quand le commit ne
    lance pas pre-commit (pas de configuration, pas de hook installé, ou un autre hook) ;
    liste vide quand le hook est là mais pre-commit introuvable.

    Le hook se cherche avec le git de l'opérateur, pas celui du juge : `core.hooksPath` décide
    du hook que le commit lancera, et le git durci du juge le remplace par /dev/null."""
    if not (repo / ".pre-commit-config.yaml").is_file():
        return None
    try:
        chemin = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--git-path", "hooks/pre-commit"],
            capture_output=True,
            check=True,
            text=True,
            timeout=30,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired):
        return None
    hook = Path(chemin) if Path(chemin).is_absolute() else repo / chemin
    if not hook.is_file() or not os.access(hook, os.X_OK):
        return None
    texte = hook.read_text(encoding="utf-8", errors="replace")
    if "pre-commit" not in texte and "pre_commit" not in texte:
        return None
    # Le hook d'installation de pre-commit nomme l'interpréteur qui l'a installé.
    interprete = re.search(r"^INSTALL_PYTHON=['\"]?([^'\"\n]+)", texte, re.M)
    if interprete and os.access(interprete.group(1), os.X_OK):
        return [interprete.group(1), "-mpre_commit"]
    executable = shutil.which("pre-commit")
    return [executable] if executable else []


def passer_les_hooks_du_commit(repo: Path, fichiers: list[str]) -> None:
    """Les hooks de pre-commit (ruff, fin de fichier, espaces…) s'appliquent aussi à
    `tasks/` : pris avant eux, l'empreinte du contrôle rouge était périmée dès le commit de
    l'approbation (sept fois dans une même session réelle, rejoué le 30/09/2026). On les
    passe donc ici, sur les preuves pas encore commitées, comme le commit les passera ; un
    second passage vérifie qu'ils n'ont plus rien à changer."""
    commande = pre_commit_du_depot(repo) if fichiers else None
    if commande is None:
        return
    if not commande:
        print(
            "ATTENTION : le hook de pre-commit est installé, mais pre-commit est introuvable :"
            " le commit échouera, ou réécrira ces fichiers après le contrôle."
        )
        return
    avant = {nom: (repo / nom).read_bytes() for nom in fichiers}
    for _ in range(2):
        try:
            passage = subprocess.run(
                [*commande, "run", "--files", *fichiers],
                cwd=repo,
                capture_output=True,
                text=True,
                timeout=600,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(
                f"ATTENTION : les hooks du commit n'ont pas pu tourner ({exc}) : s'ils"
                " réécrivent ces fichiers au commit, le contrôle sera à refaire."
            )
            return
        if passage.returncode == 0:
            break
    reecrits = [nom for nom in fichiers if (repo / nom).read_bytes() != avant[nom]]
    if reecrits:
        print("réécrit par les hooks du commit : " + ", ".join(reecrits))
    if passage.returncode != 0:
        print(
            "ATTENTION : les hooks du commit échouent encore sur ces fichiers : le commit sera"
            " refusé. Corrigez, puis relancez ce contrôle, dont la trace serait sinon périmée."
        )
        dit = [ligne for ligne in (passage.stdout + passage.stderr).splitlines() if ligne.strip()]
        for ligne in dit[-8:]:
            print(f"  {ligne}")


def cmd_rouge(args: argparse.Namespace) -> int:
    """`make task-check` : la forme du contrat, puis chaque critère lancé une fois sur le code
    commité, comme le juge le lancera. Tous doivent échouer, et pour la bonne raison ; alors
    seulement la trace est écrite, à commiter avec le contrat.

    Le code commité, c'est HEAD, plus les preuves de la tâche telles qu'elles sont dans le
    dossier : le contrat et ses tests ne sont pas encore commités quand on le lance. Le
    reste du dossier n'y entre pas. Mesuré le 27/09/2026 : un refactor pas commité qui
    cassait la fonction faisait échouer un critère que la branche principale satisfaisait
    déjà ; la tâche commitée seule, l'agent sortait VÉRIFIÉ sans rien faire.

    Chaque raison est affichée, pour que l'opérateur la lise : ce que le juge sait
    reconnaître d'un échec de travers (preuve introuvable, test pytest jamais lancé), il le
    refuse ; le reste, c'est cette lecture.
    """
    # Geste de l'opérateur : refusé quand un agent le lance, détours compris (operateur.py).
    sys.dont_write_bytecode = True  # rien de compilé dans le moteur, une zone
    try:
        from operateur import refus as refus_operateur
    except ImportError:
        print("REFUS : .ephor/operateur.py manque : moteur incomplet, refaites la mise à jour.")
        return 2
    message = refus_operateur("make task-check")
    if message:
        print(message)
        return 2
    if cmd_validate(args) != 0:
        return 2
    repo = repo_root(args.repo)
    task_path, task_id = resolve_task(repo, args.task_id, args.task)
    contract = load_contract(task_path, task_id)
    branche = git(repo, "branch", "--show-current").decode().strip()
    if branche.startswith("task/"):
        # Sur la branche de la tâche, le travail de l'agent fait passer les critères : la
        # trace commitée était retirée du dossier, et le juge refusait ensuite la tâche.
        print(
            f"REFUS : vous êtes sur {branche}. Le contrôle rouge se fait sur la branche"
            " principale, AVANT le travail de l'agent : ici, son travail ferait passer les"
            " critères. git switch main, puis relancez."
        )
        return 2
    trace_path = task_path.parent / ROUGE
    run_id = uuid.uuid4().hex[:12]
    report_dir = repo / ".verify" / "rouge" / task_id
    workdir = (
        Path(args.workdir or os.environ.get("EPHOR_WORKDIR") or Path.home() / ".cache/ephor/judge")
        .expanduser()
        .joinpath(f"{repo.name}-{sha256(str(repo).encode())[:8]}", f"rouge-{run_id}")
    )
    logs, preuves, copy = workdir / "logs", workdir / "preuves", workdir / "copy"
    head = git(repo, "rev-parse", "HEAD").decode().strip()
    criteres: list[dict[str, str]] = []
    verts: list[str] = []
    refus: list[str] = []
    try:
        _, modifies = snapshot(repo, head)
        retenus, ecartes = preuves_du_dossier(repo, head, contract, task_id, modifies)
        trace_actuelle = f"{contract.ou}/{ROUGE}"
        passer_les_hooks_du_commit(
            repo, [nom for nom in retenus if nom != trace_actuelle and (repo / nom).is_file()]
        )
        patch = snapshot(repo, head, retenus)[0] if retenus else b""
        if ecartes:
            montres = ", ".join(ecartes[:5]) + (
                f" et {len(ecartes) - 5} autre(s)" if len(ecartes) > 5 else ""
            )
            print(
                f"laissé(s) de côté, pas commité(s) et hors des preuves de la tâche : {montres}."
                " Le contrôle tourne sur le code commité, celui que l'agent recevra."
            )
        runtime = choose_runtime(args.runtime, args.image)
        logs.mkdir(parents=True)
        preuves.mkdir()
        create_copy(repo, copy, head)
        if patch:
            courant = workdir / "courant.patch"
            courant.write_bytes(patch)
            git(copy, "apply", "--index", "--binary", "--whitespace=nowarn", str(courant))
        arbre = git(copy, "write-tree").decode().strip()
        refus = recevabilite(copy, arbre, contract)
        empreintes = preuves_rouges(copy, arbre, contract, task_id)
        partages = preuves_partagees(copy, arbre, contract, task_id, set(empreintes))
        if not refus:
            if runtime.kind != "local":
                ensure_image(runtime, logs / "pull.log")
                preparer_pour_le_conteneur(runtime, copy, contract.setup is not None)
            print("contrôle rouge : chaque critère doit ÉCHOUER sur le code commité")
            criteres, verts, refus = lancer_en_rouge(
                Banc(
                    repo=repo,
                    run_id=run_id,
                    runtime=runtime,
                    copy=copy,
                    logs=logs,
                    preuves=preuves,
                    report_dir=report_dir,
                    has_setup=contract.setup is not None,
                    deadline=time.monotonic() + contract.timeout_seconds,
                    steps=[],
                    lines=[],
                    junit=[],
                ),
                contract,
            )
    finally:
        if logs.is_dir():
            shutil.rmtree(report_dir, ignore_errors=True)
            shutil.copytree(logs, report_dir / "logs")
        remove_copy(copy)
        shutil.rmtree(workdir, ignore_errors=True)
    if refus or verts:
        # Une trace qui ne dit plus la vérité ne doit pas rester là, prête à être commitée.
        retiree = trace_path.is_file()
        trace_path.unlink(missing_ok=True)
        if verts:
            print(
                f"REFUS : {', '.join(verts)} passe(nt) déjà sur le code commité : l'agent serait"
                " VÉRIFIÉ sans rien faire. Resserrez la preuve, ou retirez le critère : ce qui"
                " doit seulement rester vrai va dans la suite normale, que le juge lance aussi."
            )
        for message in refus:
            print(f"REFUS : {message}")
        if retiree:
            print(f"{relative(trace_path, repo)} retiré : il ne dit plus la vérité")
        print(f"journaux : {relative(report_dir / 'logs', repo)}")
        return 1
    try:
        ancienne = lire_json(trace_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, JudgeError):
        ancienne = None
    if (
        isinstance(ancienne, dict)
        and ancienne.get("preuves") == empreintes
        and [c.get("id") for c in ancienne.get("criteres") or [] if isinstance(c, dict)]
        == [c["id"] for c in criteres]
        and changes_depuis(repo, ancienne.get("commit"), head) == 0
    ):
        # Mêmes preuves, et rien n'a bougé hors de tasks/ depuis le commit de la trace : la
        # réécrire ne changerait que sa date et son commit, à recommiter pour rien, à chaque
        # nouveau passage (relancer juste après avoir commité en créait un à chaque fois).
        print(
            f"{relative(trace_path, repo)} déjà à jour : rien n'a changé depuis le dernier contrôle"
        )
        for ligne in dire_partages(partages, task_id, contract.ou):
            print(ligne)
        return 0
    trace = trace_rouge(task_id, head, empreintes, criteres)
    trace_path.write_text(json.dumps(trace, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        f"écrit : {relative(trace_path, repo)}. Lisez chaque raison : un critère doit échouer"
        " parce que le comportement manque, pas pour une faute de frappe.\n"
        f"Puis commitez {contract.ou}/ entier (contrat, tests, {ROUGE}) sur la branche"
        " principale : ce commit est l'approbation."
    )
    for ligne in dire_partages(partages, task_id, contract.ou):
        print(ligne)
    return 0


# ------------------------------------------------------------ tâches : tableau et rangement

ETATS_DU_JUGE = {
    "verified_needs_review": "vérifiée",
    "verification_failed": "en échec",
    "rejected": "refusée",
    "stale": "périmée",
    "budget_exhausted": "budget épuisé",
    "no_change": "en cours",
}


def derniers_verdicts(repo: Path) -> dict[str, str]:
    """Le dernier verdict de chaque tâche, dans les journaux des jugements de TOUS les dossiers
    du dépôt. Une tâche se juge dans son dossier (#32), et son verdict y reste : lu dans le seul
    dossier principal, une tâche vérifiée y restait « en cours » (mesuré le 30/09/2026)."""
    sys.dont_write_bytecode = True  # rien de compilé dans le moteur, une zone
    from usage import journal_des_jugements

    verdicts: dict[str, str] = {}
    for entree in journal_des_jugements(repo):
        if isinstance(entree.get("task_id"), str):
            verdicts[entree["task_id"]] = str(entree.get("status") or "")
    return verdicts


def taches_nommees(repo: Path, ref: str) -> set[str]:
    """Les tâches qu'un commit de cette branche nomme `task/<ID>`, comme le fait le commit de
    fusion d'une merge request. Une lecture de l'histoire pour toutes les tâches, au lieu d'une
    par tâche : cent tâches, et autant de parcours de toute l'histoire."""
    try:
        messages = git(repo, "log", "--format=%B%x00", "--fixed-strings", "--grep=task/", ref)
    except JudgeError:
        return set()
    return set(
        re.findall(r"task/([A-Z][A-Z0-9-]{2,50})(?![A-Z0-9-])", messages.decode("utf-8", "replace"))
    )


def branches_entrees(repo: Path, ref: str) -> set[str]:
    """Les références `task/…` déjà entrées dans cette branche : leur dernier commit y est."""
    try:
        sortie = git(
            repo,
            "for-each-ref",
            f"--merged={ref}",
            "--format=%(refname)",
            "refs/heads/task/",
            "refs/remotes",
        )
    except JudgeError:
        return set()
    return set(sortie.decode("utf-8", "replace").split())


def branches_de_tache(repo: Path) -> dict[str, str]:
    """Les branches `task/<ID>`, locales puis du serveur : identifiant -> référence."""
    trouvees: dict[str, str] = {}
    try:
        sortie = git(repo, "for-each-ref", "--format=%(refname)", "refs/remotes", "refs/heads")
    except JudgeError:
        return trouvees
    for ref in sortie.decode("utf-8", "replace").split():
        nom = ref.split("/", 2)[2] if ref.startswith("refs/heads/") else ref.split("/", 3)[-1]
        if nom.startswith("task/") and ID_PATTERN.match(nom[5:]):
            trouvees.setdefault(nom[5:], ref)
    return trouvees


@dataclass
class VueDesTaches:
    """Ce que git et le juge disent de toutes les tâches, lu une fois pour toutes : la branche
    principale figée à un commit, les tâches nommées par ses commits, les branches de tâche et
    celles qui y sont entrées, et le dernier verdict de chacune."""

    ref: str
    commit: str
    nommees: set[str]
    entrees: set[str]
    branches: dict[str, str]
    verdicts: dict[str, str]

    @classmethod
    def lire(cls, repo: Path) -> VueDesTaches:
        ref, _ = resolve_base(repo, None)
        commit = git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}").decode().strip()
        return cls(
            ref=ref,
            commit=commit,
            nommees=taches_nommees(repo, commit),
            entrees=branches_entrees(repo, commit),
            branches=branches_de_tache(repo),
            verdicts=derniers_verdicts(repo),
        )

    def fusionnee(self, task_id: str) -> bool:
        """Un commit de la branche principale nomme `task/<ID>` (le commit de fusion d'une merge
        request le fait), ou la branche, jugée VÉRIFIÉE, y est entrée. Une branche sans travail
        est aussi « dans » la branche principale : sans verdict, elle est en cours."""
        if task_id in self.nommees:
            return True
        branche = self.branches.get(task_id)
        return (
            branche is not None
            and branche in self.entrees
            and self.verdicts.get(task_id) == "verified_needs_review"
        )


def etat_de_tache(repo: Path, vue: VueDesTaches, task_id: str, contrat: Contract) -> str:
    """Ce que dit git, puis le juge, d'une tâche approuvée sur la branche principale."""
    if vue.fusionnee(task_id):
        return "fusionnée"
    try:
        lire_trace_rouge(repo, vue.commit, contrat, task_id)
    except JudgeError:
        return "rouge à refaire"
    if task_id not in vue.branches:
        return "approuvée"
    return ETATS_DU_JUGE.get(vue.verdicts.get(task_id, ""), "en cours")


def cmd_taches(args: argparse.Namespace) -> int:
    """`make taches` : une ligne par tâche, et son état, calculé à partir de git et du journal
    du juge, jamais tenu à la main. Avec une centaine de tâches dans `tasks/`, on ne s'y
    retrouvait plus (30/09/2026, #35) ; les tâches fusionnées sont cachées par défaut."""
    repo = repo_root(args.repo)
    vue = VueDesTaches.lire(repo)
    doubles = {
        tache: dossiers for tache, dossiers in taches_sur_disque(repo).items() if len(dossiers) > 1
    }
    lignes: list[tuple[str, str, str, str, str, str]] = []
    cachees = 0
    for tache, dossier in taches_a(repo, vue.commit).items():
        try:
            brut = contenu_a(repo, vue.commit, f"{dossier}/contract.json").decode("utf-8")
            contrat = parse_contract(lire_json(brut), tache, dossier)
        except (JudgeError, UnicodeDecodeError):
            lignes.append(
                (
                    dossier.split("/")[1] if dossier.count("/") == 2 else "",
                    tache,
                    "contrat illisible",
                    "",
                    "",
                    "",
                )
            )
            continue
        etat = etat_de_tache(repo, vue, tache, contrat)
        if etat == "fusionnée" and not args.tout:
            cachees += 1
            continue
        lot = dossier.split("/")[1] if dossier.count("/") == 2 else ""
        objectif = " ".join(contrat.objective.split())
        objectif = objectif if len(objectif) <= 60 else objectif[:59] + "…"
        lignes.append((lot, tache, etat, contrat.risk, contrat.niveau or "", objectif))
    print(f"tâches approuvées sur {vue.ref} :")
    if lignes:
        entete = ("Lot", "ID", "État", "Risque", "Niveau")
        largeurs = [max([len(entete[i])] + [len(ligne[i]) for ligne in lignes]) for i in range(5)]

        def rangee(valeurs: Sequence[str]) -> str:
            return "  ".join(valeurs[i].ljust(largeurs[i]) for i in range(5))

        print("  " + rangee(entete) + "  Objectif")
        for ligne in sorted(lignes):
            print("  " + rangee(ligne) + f"  {ligne[5]}")
    else:
        print("  aucune")
    if cachees:
        print(f"  … et {cachees} fusionnée(s) : make taches TOUT=1 pour les voir")
    for tache, dossiers in sorted(doubles.items()):
        print(
            f"  ATTENTION : {tache} existe deux fois ({', '.join(sorted(dossiers))}) : gardez-en un"
        )
    return 0


def cmd_etat(args: argparse.Namespace) -> int:
    """L'état d'une tâche, tel que `make taches` le calcule : pour `make task-clore`."""
    repo = repo_root(args.repo)
    vue = VueDesTaches.lire(repo)
    dossier = taches_a(repo, vue.commit).get(args.task_id)
    if dossier is None:
        print("inconnue")
        return 1
    brut = contenu_a(repo, vue.commit, f"{dossier}/contract.json").decode("utf-8")
    contrat = parse_contract(lire_json(brut), args.task_id, dossier)
    print(etat_de_tache(repo, vue, args.task_id, contrat))
    return 0


def cmd_ranger(args: argparse.Namespace) -> int:
    """`make task-ranger LOT=<lot> ID=<ID> | PREFIXE=<P>` : range des tâches dans un dossier par
    fonctionnalité, `tasks/<lot>/<ID>/`, dans un commit sur la branche principale. Dans
    l'arborescence de l'éditeur, cent dossiers de tâches en deviennent une dizaine (#35).
    Leurs contrats et leurs contrôles rouges restent valables : ils parlent de `tasks/<ID>/…`,
    et le juge le traduit vers le vrai dossier."""
    sys.dont_write_bytecode = True  # rien de compilé dans le moteur, une zone
    from operateur import refus as refus_operateur

    message = refus_operateur("make task-ranger")
    if message:
        print(message)
        return 2
    repo = repo_root(args.repo)
    if not LOT_PATTERN.match(args.lot):
        print(f"lot invalide : {args.lot!r} (minuscules, chiffres, tirets : paiement, auth-sso…)")
        return 2
    branche = git(repo, "branch", "--show-current").decode().strip()
    if branche not in principales(repo):
        print(
            f"REFUS : vous êtes sur {branche or 'une tête détachée'} ; le rangement se commite sur"
            " la branche principale. git switch main, puis relancez."
        )
        return 2
    taches = {
        tache: dossiers[0]
        for tache, dossiers in taches_sur_disque(repo).items()
        if len(dossiers) == 1
    }
    choisies = sorted(
        tache
        for tache in taches
        if tache in (args.task_id or []) or (args.prefixe and tache.startswith(args.prefixe))
    )
    inconnues = sorted(set(args.task_id or []) - set(taches))
    if inconnues:
        print(f"REFUS : tâche(s) introuvable(s) : {', '.join(inconnues)}")
        return 2
    a_ranger = [
        (tache, taches[tache]) for tache in choisies if taches[tache] != f"tasks/{args.lot}/{tache}"
    ]
    if not a_ranger:
        print("rien à ranger : ces tâches sont déjà dans ce lot, ou aucune ne correspond")
        return 0
    sales = git(repo, "status", "--porcelain", "--", *(dossier for _, dossier in a_ranger)).decode()
    if sales.strip():
        print(
            "REFUS : ces dossiers ont des changements pas commités : commitez-les, ou retirez-les,"
            " d'abord"
        )
        return 2
    for tache, dossier in a_ranger:
        (repo / "tasks" / args.lot).mkdir(parents=True, exist_ok=True)
        fait = subprocess.run(
            ["git", "-C", str(repo), "mv", dossier, f"tasks/{args.lot}/{tache}"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if fait.returncode != 0:
            print(f"git mv {dossier} a échoué : {(fait.stdout + fait.stderr).strip()}")
            return 1
    noms = ", ".join(tache for tache, _ in a_ranger)
    fait = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-q",
            "-m",
            f"Tâches rangées dans tasks/{args.lot}/ : {noms}",
            "--",
            "tasks",
        ],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    if fait.returncode != 0:
        print(f"le commit a échoué : {(fait.stdout + fait.stderr).strip()[-400:]}")
        return 1
    print(f"rangé dans tasks/{args.lot}/ : {noms}. Contrats et contrôles rouges restent valables.")
    return 0


def cmd_dossier(args: argparse.Namespace) -> int:
    """Le dossier d'une tâche, rangée ou non : pour les scripts (review.sh, worktree.sh)."""
    repo = repo_root(args.repo)
    if args.ref:
        dossier = taches_a(repo, args.ref).get(args.task_id)
        if dossier is None:
            return 1
        print(dossier)
        return 0
    chemin, _ = resolve_task(repo, args.task_id, None)
    print(chemin.parent.relative_to(repo).as_posix())
    return 0


def cmd_perimes(args: argparse.Namespace) -> int:
    """`make task-check` sans ID : le contrôle rouge de chaque tâche, lu sur la branche
    principale comme le juge le lira. Un reformatage du dépôt périme d'un coup toutes les
    traces qu'il touche ; on les découvrait une par une, au juge (session réelle, 30/09/2026).
    Rien n'est lancé : la liste se lit dans git."""
    repo = repo_root(args.repo)
    vue = VueDesTaches.lire(repo)
    ref = vue.ref
    print(f"contrôles rouges, lus sur {ref} :")
    a_jour = 0
    fusionnees = 0
    a_refaire: list[str] = []
    for tache, dossier in sorted(taches_a(repo, vue.commit).items()):
        # Une tâche fusionnée ne se juge plus : sa trace n'a plus rien à dire, et cent tâches
        # finies noyaient les quelques-unes qui restent à refaire.
        if vue.fusionnee(tache):
            fusionnees += 1
            continue
        try:
            brut = contenu_a(repo, vue.commit, f"{dossier}/contract.json")
        except JudgeError:
            continue
        try:
            lire_trace_rouge(
                repo,
                vue.commit,
                parse_contract(lire_json(brut.decode("utf-8")), tache, dossier),
                tache,
            )
        except JudgeError as exc:
            a_refaire.append(tache)
            ecarts = [
                message.split(" : ", 1)[1]
                for message in exc.messages
                if message.startswith("contrôle rouge périmé : ")
            ]
            if ecarts:
                print(f"  PÉRIMÉ  {tache} : {', '.join(ecarts)}")
            else:
                print(f"  À REFAIRE  {tache} : {exc.messages[0].split(' : ')[0]}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            a_refaire.append(tache)
            print(f"  À REFAIRE  {tache} : contrat illisible ({exc})")
        else:
            a_jour += 1
    print(f"à jour : {a_jour} tâche(s)")
    if fusionnees:
        print(f"fusionnée(s), plus à juger : {fusionnees}")
    if not a_refaire:
        return 0
    print(
        f"à refaire sur {ref} : " + " ; ".join(f"make task-check ID={tache}" for tache in a_refaire)
    )
    return 1


def lancer_en_rouge(
    banc: Banc, contract: Contract
) -> tuple[list[dict[str, str]], list[str], list[str]]:
    """L'installation, puis chaque critère une fois : ceux qui échouent pour la bonne raison,
    ceux qui passent déjà, et ce qui empêche de conclure."""
    criteres: list[dict[str, str]] = []
    verts: list[str] = []
    if contract.setup is not None:
        installation = banc.execute("installation", "setup", contract.setup, "setup")
        if not installation["ok"]:
            raison = extrait(banc.logs / "setup.log", installation["debut"], installation["code"])
            return (
                [],
                [],
                [
                    "l'installation du contrat échoue : impossible de dire si les critères "
                    f"échouent pour la bonne raison ({raison})"
                ],
            )
    refus: list[str] = []
    for critere in contract.criteria:
        issue = banc.execute(
            critere.id, critere.id, critere.check, "verify", critere.max_duration_seconds, critere
        )
        titre = f"{critere.id} {critere.description[:60]}"
        if banc.any_timeout:
            return criteres, verts, [f"budget de temps du contrat épuisé pendant {critere.id}"]
        if issue["ok"]:
            verts.append(critere.id)
            print(f"  VERT   {titre} : passe déjà")
            continue
        travers = None
        journal = banc.logs / f"{critere.id}.log"
        if issue["code"] in (126, 127) and issue["forme"] != "rapport":
            # Le code des coquilles pour « introuvable » et « pas exécutable ».
            travers = (
                f"commande introuvable ou non exécutable (code {issue['code']}) : le critère"
                " échoue avant de rien tester"
            )
        elif issue["forme"] == "rapport":
            travers = rouge_de_travers(
                banc.logs / f"{critere.id}.junit.xml", tests_attendus(critere.check)
            )
            if travers is None and SQUELETTE in journal.read_text("utf-8", "replace"):
                travers = "test encore à écrire : c'est celui du squelette de make task-new"
        elif issue["forme"] == "junit" and critere.rapport_junit:
            travers = rouge_junit_de_travers(
                banc.logs / f"{critere.id}.junit", critere.rapport_junit
            )
        if travers is not None:
            refus.append(f"{critere.id} échoue pour une mauvaise raison : {travers}")
            print(f"  FAUX   {titre} : rouge pour une mauvaise raison\n         -> {travers}")
            continue
        raison = issue["reason"] or extrait(journal, issue["debut"], issue["code"])
        criteres.append({"id": critere.id, "resultat": "rouge", "raison": raison})
        print(f"  rouge  {titre}\n         -> {raison}")
    return criteres, verts, refus


TEST_NAME = re.compile(r"^test_[A-Za-z0-9_]*$")


def skeleton_test(task_id: str, tests: list[str]) -> str:
    body = "".join(
        f'\n\ndef {name}() -> None:\n    pytest.fail("{SQUELETTE} : {name}")\n' for name in tests
    )
    return (
        f'"""Tests d\'acceptation de {task_id}.\n\n'
        "Écrits et validés AVANT le travail de l'agent, commités avec le contrat.\n"
        '"""\n'
        "\n"
        "import pytest\n" + body
    )


def wanted_tests(task_id: str, contract: Contract) -> dict[str, list[str]]:
    """Fichiers de test cités par les critères, et le nom de test attendu dans chacun."""
    prefix = f"tasks/{task_id}/"
    wanted: dict[str, list[str]] = {}
    for criterion in contract.criteria:
        for token in criterion.check:
            path, _, selector = token.partition("::")
            if not (path.startswith(prefix) and path.endswith(".py") and safe_path(path)):
                continue
            names = wanted.setdefault(path, [])
            name = selector.split("::")[-1]
            if TEST_NAME.match(name) and name not in names:
                names.append(name)
    return wanted


# Un bloc de code Markdown : sa clôture (``` ou ~~~), sa ligne d'annonce, son contenu.
BLOC_DE_CODE = re.compile(r"^(`{3,}|~{3,})([^\n]*)\n(.*?)^\1[ \t]*$", re.M | re.S)
CHEMIN_DE_TACHE = re.compile(r"tasks/[^\s`'\"*,;()<>]+")


def lire_plan(texte: str, task_id: str) -> tuple[Any, dict[str, str]]:
    """Le plan de `make task-new FROM=` : un contrat JSON seul, ou la réponse entière du
    planner. Dans la réponse, le premier bloc JSON est le contrat, et chaque bloc de code
    annoncé par un chemin sous `tasks/` (sur sa ligne d'ouverture, ou sur la dernière ligne
    avant lui) est un fichier de la tâche. Un chemin hors de `tasks/<ID>/`, ou un fichier
    Python qui ne se lit pas, et rien n'est créé.

    Vu en session réelle, vrai en 1.3.0 : `FROM` ne prenait que le contrat, et l'opérateur
    recopiait à la main, à chaque tâche, les tests que le planner avait déjà écrits."""
    if texte.lstrip().startswith("{"):
        return lire_json(texte), {}
    contrat: Any = None
    fichiers: dict[str, str] = {}
    refus: list[str] = []
    for bloc in BLOC_DE_CODE.finditer(texte):
        annonce, contenu = bloc.group(2).strip(), bloc.group(3)
        langue = annonce.split()[0].lower() if annonce else ""
        if contrat is None and (langue == "json" or (not langue and contenu.lstrip()[:1] == "{")):
            contrat = lire_json(contenu)
            continue
        avant = texte[: bloc.start()].rstrip("\n").rsplit("\n", 1)[-1]
        trouve = CHEMIN_DE_TACHE.search(annonce) or CHEMIN_DE_TACHE.search(avant)
        if trouve is None:
            continue
        nom = trouve.group(0).rstrip(".:")
        if not (safe_path(nom) and nom.startswith(f"tasks/{task_id}/")):
            refus.append(f"le plan écrit hors de tasks/{task_id}/ : {nom}")
        elif nom in (f"tasks/{task_id}/contract.json", f"tasks/{task_id}/{ROUGE}"):
            refus.append(f"{nom} : vient du bloc JSON ou de make task-check, pas d'un bloc à part")
        elif nom in fichiers:
            refus.append(f"{nom} : deux blocs pour le même fichier")
        else:
            if nom.endswith(".py"):
                try:
                    ast.parse(contenu)
                except SyntaxError as exc:
                    refus.append(f"{nom} : Python invalide (ligne {exc.lineno} : {exc.msg})")
                    continue
            fichiers[nom] = contenu
    if refus:
        raise JudgeError(refus)
    if contrat is None:
        raise JudgeError("pas de contrat dans le plan : ni un JSON seul, ni un bloc ```json")
    return contrat, fichiers


def cmd_new(args: argparse.Namespace) -> int:
    # Geste de l'opérateur : refusé quand un agent le lance, détours compris (operateur.py).
    sys.dont_write_bytecode = True  # rien de compilé dans le moteur, une zone
    try:
        from operateur import refus as refus_operateur
    except ImportError:
        print("REFUS : .ephor/operateur.py manque : moteur incomplet, refaites la mise à jour.")
        return 2
    message = refus_operateur("make task-new")
    if message:
        print(message)
        return 2
    repo = repo_root(args.repo)
    task_id = args.task_id
    if not ID_PATTERN.match(task_id):
        print(f"identifiant invalide : {task_id!r} (ex. FEAT-001)")
        return 2
    lot = getattr(args, "lot", None)
    if lot is not None and not LOT_PATTERN.match(lot):
        print(f"lot invalide : {lot!r} (minuscules, chiffres, tirets : paiement, auth-sso…)")
        return 2
    dossier = f"tasks/{lot}/{task_id}" if lot else f"tasks/{task_id}"
    folder = repo / dossier
    deja = taches_sur_disque(repo).get(task_id)
    if deja or folder.exists():
        print(f"{', '.join(deja or [dossier])} existe déjà : rien n'est écrasé")
        return 2
    test_name = f"test_{task_id.lower().replace('-', '_')}.py"
    data: dict[str, Any] = {
        "id": task_id,
        "objective": f"{A_ECRIRE} : le comportement observable attendu, en deux ou trois phrases.",
        "risk": "faible",
        "allowed_paths": ["src/", "tests/"],
        "criteria": [
            {
                "id": "C1",
                "description": f"{A_ECRIRE} : un critère qu'un tiers peut trancher sans débat.",
                "check": [
                    "uv",
                    "run",
                    "pytest",
                    "-q",
                    "-p",
                    "no:randomly",
                    f"tasks/{task_id}/{test_name}::test_c1",
                ],
            }
        ],
        "setup": ["uv", "sync", "--all-extras"],
        "verify": ["bash", ".ephor/projet/verify.sh"],
        "timeout_seconds": DEFAULT_TIMEOUT,
    }
    # Contrat déjà rédigé et relu : on le reprend tel quel, après l'avoir validé, avec les
    # fichiers de tests que le plan donne. Rien n'est créé si un morceau est mauvais, pour ne
    # pas laisser un dossier à moitié fait.
    fournis: dict[str, str] = {}
    if args.from_file:
        source = Path(args.from_file).expanduser()
        try:
            data, fournis = lire_plan(source.read_text(encoding="utf-8"), task_id)
        except OSError as exc:
            print(f"plan introuvable ou illisible : {exc}")
            return 2
        except UnicodeDecodeError:
            print("plan illisible : le fichier n'est pas en UTF-8")
            return 2
        except json.JSONDecodeError as exc:
            print(f"plan illisible (JSON) : {exc}")
            return 2
        except JudgeError as exc:
            for message in exc.messages:
                print(f"  REFUS {message}")
            return 2
        if isinstance(data, dict):
            data.setdefault("id", task_id)
        try:
            contract = parse_contract(data, task_id)
        except JudgeError as exc:
            for message in exc.messages:
                print(f"  REFUS {message}")
            return 2
        tests = wanted_tests(task_id, contract)
    else:
        tests = {f"tasks/{task_id}/{test_name}": ["test_c1"]}

    folder.mkdir(parents=True)
    ordered = {key: data[key] for key in CONTRACT_ORDER if key in data}
    (folder / "contract.json").write_text(
        json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    written = [f"{dossier}/contract.json"]
    # Le plan et les critères parlent de `tasks/<ID>/…` : dans un lot, ces fichiers vivent
    # dans `tasks/<lot>/<ID>/`, et le juge les y retrouve (#35).
    for path, contenu in sorted(fournis.items()):
        reel = relocalise(path, task_id, dossier)
        (repo / reel).parent.mkdir(parents=True, exist_ok=True)
        (repo / reel).write_text(contenu, encoding="utf-8")
        written.append(f"{reel} (du plan)")
    squelettes: list[str] = []
    for path, names in sorted(tests.items()):
        reel = relocalise(path, task_id, dossier)
        target = repo / reel
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skeleton_test(task_id, names or ["test_a_ecrire"]), encoding="utf-8")
        written.append(reel)
        squelettes.append(reel)
    print("créé : " + ", ".join(written))
    for path in sorted(set(fournis) - set(tests)):
        reel = relocalise(path, task_id, dossier)
        print(f"ATTENTION : aucun critère ne cite {reel} ; il fera partie des preuves, relisez-le.")
    if not tests:
        print(
            f"ATTENTION : aucun critère ne s'appuie sur un fichier de {dossier}/.\n"
            "C'est là que vivent les preuves : ailleurs, elles ne sont protégées que si\n"
            "elles existent déjà sur la branche principale."
        )
    if getattr(args, "sans_conseil", False):
        return 0  # make task-approve enchaîne lui-même le contrôle rouge et le commit
    if args.from_file:
        if squelettes:
            print(
                f"Reste à écrire : {', '.join(squelettes)} (squelette : le plan ne le donnait pas)."
            )
        print(
            "le contrat vient de votre plan et il est valide"
            + (", les tests aussi" if fournis else "")
            + ". Relisez tout, voyez chaque critère\n"
            f"échouer (make task-check ID={task_id}), puis commitez le dossier sur la branche\n"
            "principale : ce commit est l'approbation.\n"
            f"Ensuite : git switch -c task/{task_id}"
        )
    else:
        print(
            "ensuite : complétez-les, faites-les relire, voyez-les échouer\n"
            f"(make task-check ID={task_id}), commitez le dossier sur la branche principale,\n"
            f"puis créez la branche de travail : git switch -c task/{task_id}"
        )
    return 0


def confirme(question: str) -> bool:
    """Un « oui » tapé dans le terminal de l'opérateur. Sans terminal (la CI, un agent dont le
    terminal n'en est pas un), c'est non."""
    try:
        # Deux ouvertures : en lecture ET écriture à la fois, Python exige un fichier qu'on
        # peut parcourir, ce qu'un terminal n'est pas, et tout finissait en « pas de terminal ».
        tty = Path("/dev/tty")
        with tty.open("w", encoding="utf-8") as sortie, tty.open(encoding="utf-8") as entree:
            sortie.write(question)
            sortie.flush()
            return entree.readline().strip().lower() == "oui"
    except OSError:
        print(
            "REFUS : pas de terminal pour confirmer. Lancez make task-approve vous-même, dans"
            " votre terminal."
        )
        return False


def resume_du_plan(
    task_id: str, contract: Contract, fournis: dict[str, str], plan: str, dossier: str = ""
) -> str:
    """Ce que l'opérateur approuve, en quelques lignes : le reste est dans le plan. Les tests
    sont nommés là où ils seront écrits (`tasks/<lot>/<ID>/` pour une tâche rangée)."""
    lignes = [
        f"Approbation de {task_id}, d'après {plan}",
        f"  objectif  : {contract.objective}",
        f"  risque    : {contract.risk}",
        f"  périmètre : {', '.join(contract.allowed_paths)}",
        "  critères  :",
    ]
    lignes += [f"    {c.id} {c.description}" for c in contract.criteria]
    lignes.append("  tests d'examen :")
    lignes += [
        f"    {relocalise(nom, task_id, dossier or f'tasks/{task_id}')}"
        f" ({len(contenu.splitlines())} lignes)"
        for nom, contenu in sorted(fournis.items())
    ]
    lignes.append(
        "Approuver, c'est dire : ces critères et ces tests sont l'examen de l'agent. Relisez le"
        " plan au besoin avant de répondre."
    )
    return "\n".join(lignes)


def cmd_approuve(args: argparse.Namespace) -> int:
    """`make task-approve ID=<ID> FROM=<plan>` : l'approbation d'un seul geste. Le plan (la
    réponse du planner) devient le contrat et les tests, le contrôle rouge les voit échouer,
    et le commit sur la branche principale approuve, après que l'opérateur a lu le résumé et
    tapé « oui » dans son terminal.

    Vu en session réelle : contrats et tests sont écrits par un agent, et les gestes de
    l'opérateur (task-new, recopier les tests, task-check, commit) étaient mécaniques ; seule
    la lecture compte. Une fenêtre de confirmation ne s'ouvre pas depuis le terminal de Claude
    Code, dont le bac à sable refuse aussi d'écrire tasks/ (mesuré le 30/09/2026), et un
    agent qui aurait les droits d'accessibilité du système pourrait la cliquer. Un « oui » tapé
    dans le terminal de l'opérateur, par un geste qu'aucun agent ne lance (operateur.py), non."""
    from operateur import refus as refus_operateur

    message = refus_operateur("make task-approve")
    if message:
        print(message)
        return 2
    repo = repo_root(args.repo)
    task_id = args.task_id
    if not ID_PATTERN.match(task_id):
        print(f"identifiant invalide : {task_id!r} (ex. FEAT-001)")
        return 2
    branche = git(repo, "branch", "--show-current").decode().strip()
    if branche not in principales(repo):
        print(
            f"REFUS : vous êtes sur {branche or 'une tête détachée'} ; l'approbation se commite"
            " sur la branche principale. git switch main, puis relancez."
        )
        return 2
    lot = getattr(args, "lot", None)
    if lot is not None and not LOT_PATTERN.match(lot):
        print(f"lot invalide : {lot!r} (minuscules, chiffres, tirets : paiement, auth-sso…)")
        return 2
    chemin = f"tasks/{lot}/{task_id}" if lot else f"tasks/{task_id}"
    deja = taches_sur_disque(repo).get(task_id)
    if deja or (repo / chemin).exists():
        print(
            f"REFUS : {', '.join(deja or [chemin])} existe déjà. Pour le reprendre : make"
            f" task-check ID={task_id}, puis commitez-le ; ou supprimez-le et relancez."
        )
        return 2
    # Le plan, lu et vérifié AVANT toute question : on ne demande rien pour un plan invalide.
    try:
        data, fournis = lire_plan(
            Path(args.from_file).expanduser().read_text(encoding="utf-8"), task_id
        )
        if isinstance(data, dict):
            data.setdefault("id", task_id)
        contract = parse_contract(data, task_id)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"plan introuvable ou illisible : {exc}")
        return 2
    except JudgeError as exc:
        for erreur in exc.messages:
            print(f"  REFUS {erreur}")
        return 2
    manquants = [chemin for chemin in wanted_tests(task_id, contract) if chemin not in fournis]
    if manquants:
        print(
            f"REFUS : le plan ne donne pas {', '.join(manquants)} : un squelette échoue toujours,"
            " et le contrôle rouge le refuserait. Complétez le plan, ou passez par make task-new."
        )
        return 2
    print(resume_du_plan(task_id, contract, fournis, args.from_file, chemin))
    for ligne in dire_partages(preuves_partagees(repo, "HEAD", contract, task_id), task_id, chemin):
        print(ligne)
    if not confirme(f"Approuver {task_id} ? Tapez « oui » pour approuver : "):
        print("rien n'est fait : l'approbation n'a pas été confirmée")
        return 1
    creation = argparse.Namespace(
        repo=args.repo, task_id=task_id, from_file=args.from_file, sans_conseil=True, lot=lot
    )
    if cmd_new(creation) != 0:
        return 2
    rouge = argparse.Namespace(
        command="rouge",
        repo=args.repo,
        task_id=task_id,
        task=None,
        runtime=args.runtime,
        image=args.image,
        workdir=args.workdir,
    )
    if cmd_rouge(rouge) != 0:
        print(
            f"Approbation arrêtée : {chemin}/ est créé, pas commité. Corrigez-le, puis"
            f" make task-check ID={task_id} et commitez-le ; ou supprimez-le et relancez avec un"
            " plan corrigé."
        )
        return 1
    # Le commit d'approbation : le dossier de la tâche seul, avec les hooks de l'opérateur.
    for commande in (
        ["git", "-C", str(repo), "add", "--", chemin],
        [
            "git",
            "-C",
            str(repo),
            "commit",
            "-q",
            "-m",
            f"{task_id} : contrat approuvé",
            "--",
            chemin,
        ],
    ):
        fait = subprocess.run(commande, capture_output=True, text=True, timeout=600, check=False)
        if fait.returncode != 0:
            sortie = (fait.stdout + fait.stderr).splitlines()
            dit = [ligne for ligne in sortie if ligne.strip()][-6:]
            print(f"le commit d'approbation a échoué ({' '.join(commande[3:5])}) :")
            for ligne in dit:
                print(f"  {ligne}")
            return 1
    court = git(repo, "rev-parse", "--short", "HEAD").decode().strip()
    print(
        f"{task_id} approuvé ({court}, sur {branche}). Le serveur ne le saura qu'après votre"
        " git push."
    )
    # Une tâche, un dossier : plusieurs tâches dans un même dossier se marchaient dessus
    # (session réelle, #32). Sauf DOSSIER=non, la tâche s'ouvre dans le sien, avec la ligne
    # qui y lance une session au niveau du contrat.
    ouvrir = repo / ".ephor" / "scripts" / "worktree.sh"
    if getattr(args, "sans_dossier", False):
        print(
            f"Dossier à part : make task-worktree ID={task_id} ;"
            f" ou ici : git switch -c task/{task_id}"
        )
    elif not ouvrir.is_file():
        print(f"Ouvrez son dossier : make task-worktree ID={task_id}")
    elif subprocess.run(
        ["bash", str(ouvrir), task_id], cwd=repo, timeout=1800, check=False
    ).returncode:
        print(f"Le dossier de la tâche ne s'est pas ouvert : make task-worktree ID={task_id}")
    return 0


def cmd_garde(args: argparse.Namespace) -> int:
    """La garde des contrôles de la CI : ce que la branche change dans les zones de contrôle,
    et les silencieux qu'elle ajoute. Code 3 : revue humaine obligatoire.

    Les deux CI recopiaient chacune la liste des zones dans une expression écrite à la main ;
    elles avaient pris du retard sur le juge (sans `.pre-commit-config.yaml`). Elles
    appellent maintenant le moteur de la branche CIBLE, qui porte la seule liste.
    """
    repo = repo_root(args.repo)
    base = git(repo, "merge-base", "HEAD", args.base).decode().strip()
    noms = git(repo, "diff", "--name-only", "-z", "--no-renames", base, "HEAD")
    chemins = sorted({nom.decode("utf-8", "replace") for nom in noms.split(b"\0") if nom})
    touchees = [chemin for chemin in chemins if is_protected(chemin)]
    patch = git(repo, "diff", "--no-ext-diff", "--no-textconv", "--no-renames", base, "HEAD")
    silences = silencieux_ajoutes(patch)

    def base_et_tete(chemin: str) -> tuple[bytes | None, bytes | None]:
        return blob_a(repo, base, chemin), blob_a(repo, "HEAD", chemin)

    reglages, _ = reglages_des_controles(chemins, base_et_tete)
    retires = tests_retires(chemins, base_et_tete)
    for chemin in touchees:
        print(f"zone de contrôle modifiée : {chemin}")
    for silence in silences:
        print(f"silencieux ajouté : {silence}")
    for chemin in reglages:
        print(f"réglage des contrôles modifié : {chemin}")
    for test in retires:
        print(f"test retiré : {test}")
    if touchees:
        print("revue humaine obligatoire : la branche touche l'infrastructure de vérification")
    if silences or reglages or retires:
        print(
            "justification requise dans la merge request pour chaque silencieux, réglage"
            " ou test retiré"
        )
    if not (touchees or silences or reglages or retires):
        print(f"garde : {len(chemins)} fichier(s), aucune zone de contrôle, aucun silencieux")
    return 3 if touchees or silences or reglages or retires else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Juge final d'une tâche d'agent.")
    sub = parser.add_subparsers(dest="command", required=True)

    def task_options(p: argparse.ArgumentParser) -> None:
        group = p.add_mutually_exclusive_group(required=True)
        group.add_argument("--task-id", help="identifiant, ex. FEAT-001 (tasks/<ID>/contract.json)")
        group.add_argument("--task", help="chemin du contrat, sous tasks/")
        p.add_argument("--repo", help="racine du dépôt (défaut : dossier courant)")

    check = sub.add_parser("check", help="juger l'état courant d'une tâche")
    task_options(check)
    check.add_argument("--base", help="référence de base (défaut : main, master, origin/main)")
    check.add_argument("--runtime", choices=["local", "podman", "docker", "auto"], default="local")
    check.add_argument(
        "--image", help=f"image de conteneur (défaut : EPHOR_IMAGE ou {DEFAULT_IMAGE})"
    )
    check.add_argument("--workdir", help="dossier de travail (défaut : ~/.cache/ephor/judge)")
    check.add_argument(
        "--report-dir", help="où écrire le rapport (défaut : .verify/judge/<ID>/latest)"
    )
    check.add_argument(
        "--keep", action="store_true", help="garder la copie vérifiée pour inspection"
    )
    check.set_defaults(func=cmd_check)

    validate = sub.add_parser("validate", help="vérifier la forme d'un contrat")
    task_options(validate)
    validate.set_defaults(func=cmd_validate)

    rouge = sub.add_parser(
        "rouge",
        help="make task-check : la forme du contrat, puis chaque critère doit échouer (opérateur)",
    )
    task_options(rouge)
    rouge.add_argument("--runtime", choices=["local", "podman", "docker", "auto"], default="local")
    rouge.add_argument(
        "--image", help=f"image de conteneur (défaut : EPHOR_IMAGE ou {DEFAULT_IMAGE})"
    )
    rouge.add_argument("--workdir", help="dossier de travail (défaut : ~/.cache/ephor/judge)")
    rouge.set_defaults(func=cmd_rouge)

    perimes = sub.add_parser(
        "perimes", help="make task-check sans ID : les contrôles rouges à refaire, toutes tâches"
    )
    perimes.add_argument("--repo", help="racine du dépôt (défaut : dossier courant)")
    perimes.set_defaults(func=cmd_perimes)

    approuve = sub.add_parser(
        "approuve",
        help="make task-approve : le plan devient contrat et tests, vus échouer, puis commités"
        " sur la branche principale après un « oui » de l'opérateur (opérateur)",
    )
    approuve.add_argument("--task-id", required=True)
    approuve.add_argument("--from", dest="from_file", required=True, help="le plan du planner")
    approuve.add_argument("--repo")
    approuve.add_argument(
        "--runtime", choices=["local", "podman", "docker", "auto"], default="local"
    )
    approuve.add_argument(
        "--image", help=f"image de conteneur (défaut : EPHOR_IMAGE ou {DEFAULT_IMAGE})"
    )
    approuve.add_argument("--workdir", help="dossier de travail (défaut : ~/.cache/ephor/judge)")
    approuve.add_argument("--lot", help="ranger la tâche dans tasks/<lot>/ (une fonctionnalité)")
    approuve.add_argument(
        "--sans-dossier", action="store_true", help="ne pas ouvrir le dossier de la tâche"
    )
    approuve.set_defaults(func=cmd_approuve)

    taches = sub.add_parser("taches", help="une ligne par tâche et son état (make taches)")
    taches.add_argument("--repo")
    taches.add_argument("--tout", action="store_true", help="montrer aussi les tâches fusionnées")
    taches.set_defaults(func=cmd_taches)

    ranger = sub.add_parser("ranger", help="ranger des tâches dans tasks/<lot>/ (opérateur)")
    ranger.add_argument("--repo")
    ranger.add_argument("--lot", required=True)
    ranger.add_argument("--task-id", action="append", help="une tâche (répétable)")
    ranger.add_argument("--prefixe", help="toutes les tâches dont l'identifiant commence ainsi")
    ranger.set_defaults(func=cmd_ranger)

    etat = sub.add_parser("etat", help="l'état d'une tâche, comme make taches (scripts)")
    etat.add_argument("--repo")
    etat.add_argument("--task-id", required=True)
    etat.set_defaults(func=cmd_etat)

    dossier = sub.add_parser("dossier", help="le dossier d'une tâche, rangée ou non (scripts)")
    dossier.add_argument("--repo")
    dossier.add_argument("--task-id", required=True)
    dossier.add_argument("--ref", help="lu sur cette référence git plutôt que sur le disque")
    dossier.set_defaults(func=cmd_dossier)

    new = sub.add_parser("new", help="créer le squelette d'un contrat (opérateur)")
    new.add_argument("--task-id", required=True)
    new.add_argument("--repo")
    new.add_argument("--lot", help="ranger la tâche dans tasks/<lot>/ (une fonctionnalité)")
    new.add_argument(
        "--from",
        dest="from_file",
        help="contrat déjà rédigé et relu (JSON) : repris tel quel s'il est valide",
    )
    new.set_defaults(func=cmd_new)

    garde = sub.add_parser(
        "garde", help="la branche touche-t-elle une zone de contrôle ? (CI ; code 3 : oui)"
    )
    garde.add_argument("--base", required=True, help="branche cible, ex. origin/main")
    garde.add_argument("--repo")
    garde.set_defaults(func=cmd_garde)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except JudgeError as exc:
        for message in exc.messages:
            print(f"erreur : {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
