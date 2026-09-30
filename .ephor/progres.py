"""Fin de tour : PROGRESS.md, tenu à jour (#31). Appelé par `.ephor/hooks/progres.sh`.

C'est lui qui permet de reprendre après `/clear`, et le bouton d'arrêt compte dessus : l'agent
y note son état avant de s'arrêter. Vu en session réelle (30/09/2026) : il n'était pas tenu à
jour régulièrement ; seules des consignes le demandaient, rien ne le vérifiait.

À chaque fin de tour : le tour a-t-il travaillé (code, commits), et PROGRESS.md a-t-il bougé ?
Après `EPHOR_RAPPEL_PROGRES_TOURS` tours de travail (3 par défaut) sans qu'il bouge, il est
demandé à l'agent, une fois par série. Bibliothèque standard, Python 3.9.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

SEUIL = 3


def git(racine: Path, *args: str) -> bytes:
    """Git durci, comme dans les hooks : aucun hook du dépôt, aucun moniteur de fichiers."""
    durci = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    commande = ["git", "-C", str(racine), *durci, *args]
    return subprocess.run(commande, capture_output=True, timeout=60, check=False).stdout


# Un fichier nouveau, pas encore suivi par git, compte par son contenu : sa liste ne change pas
# quand il est réécrit (mesuré en session réelle : A = 1 devenu A = 2, et rien de vu). Mais à
# chaque fin de tour : au-delà de quelques mégaoctets, sa taille et sa date suffisent, sans
# quoi un dossier de données pas encore ignoré se relisait en entier, à chaque tour.
NOUVEAUX_LUS = 2000
TAILLE_LUE = 256 * 1024
TOTAL_LU = 8 * 1024 * 1024
# L'état d'une série vit dans .git/, un fichier par session : au-delà d'un mois, il part.
GARDE_SECONDES = 30 * 86400


def travail_du_depot(racine: Path) -> str:
    """Ce que le tour a pu changer : le dernier commit, les changements suivis, les nouveaux."""
    empreinte = hashlib.sha256()
    empreinte.update(git(racine, "rev-parse", "HEAD"))
    empreinte.update(git(racine, "diff", "HEAD", "--no-ext-diff", "--no-textconv"))
    nouveaux = git(racine, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0")
    reste = TOTAL_LU
    for nom in [n for n in nouveaux if n][:NOUVEAUX_LUS]:
        empreinte.update(nom + b"\0")
        chemin = racine / nom.decode("utf-8", "surrogateescape")
        with suppress(OSError):
            etat = chemin.stat()
            if etat.st_size <= min(TAILLE_LUE, reste):
                reste -= etat.st_size
                empreinte.update(chemin.read_bytes())
            else:
                empreinte.update(f"{etat.st_size}:{etat.st_mtime_ns}".encode())
    return empreinte.hexdigest()


def purge(gitdir: Path, maintenant: float) -> None:
    """Les états de séries des sessions finies depuis plus d'un mois."""
    for vieux in gitdir.glob("ephor-progres-*"):
        with suppress(OSError):
            if maintenant - vieux.stat().st_mtime > GARDE_SECONDES:
                vieux.unlink()


def seuil() -> int:
    try:
        return max(1, int(os.environ.get("EPHOR_RAPPEL_PROGRES_TOURS", str(SEUIL))))
    except ValueError:
        return SEUIL


def rappel(racine: Path, gitdir: Path, charge: dict[str, Any]) -> str:
    """Le rappel à donner à l'agent, ou rien ; l'état de la série est gardé dans .git/."""
    session = str(charge.get("session_id") or charge.get("sessionId") or "sans-session")
    session = re.sub(r"[^A-Za-z0-9._-]", "", session)[:64] or "sans-session"
    progres = hashlib.sha256((racine / "PROGRESS.md").read_bytes()).hexdigest()
    travail = travail_du_depot(racine)
    fichier = gitdir / f"ephor-progres-{session}"
    try:
        etat = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        etat = {}
    if not isinstance(etat, dict) or etat.get("progres") != progres:
        # Premier tour de la session, ou PROGRESS.md vient d'être écrit : une série repart.
        etat = {"progres": progres, "travail": travail, "tours": 0, "rappele": False}
    elif etat.get("travail") != travail:
        etat.update(travail=travail, tours=int(etat.get("tours", 0)) + 1)
    message = ""
    # `stop_hook_active` : Claude Code continue déjà sur un rappel ; jamais deux de suite.
    if etat["tours"] >= seuil() and not etat.get("rappele") and not charge.get("stop_hook_active"):
        etat["rappele"] = True
        message = (
            f"PROGRESS.md n'a pas bougé depuis {etat['tours']} tours de travail. Avant de rendre"
            " la main, mets-le à jour : fait, en cours, bloqué, prochain pas, une ligne chacun."
            " C'est lui qui permet de reprendre après /clear."
        )
    with suppress(OSError):
        fichier.write_text(json.dumps(etat), encoding="utf-8")
    purge(gitdir, time.time())
    return message


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 0
    try:
        charge = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    # Les sous-agents ne tiennent pas PROGRESS.md : rien pour eux.
    if not isinstance(charge, dict) or charge.get("sous_agent"):
        return 0
    message = rappel(Path(argv[1]), Path(argv[2]), charge)
    if not message:
        return 0
    if os.environ.get("EPHOR_OUTIL", "claude") == "claude":
        # Claude Code continue le tour avec cette raison : l'agent l'écrit lui-même.
        sortie = {
            "decision": "block",
            "reason": message,
            "systemMessage": "Ephor : PROGRESS.md pas mis à jour depuis plusieurs tours de"
            " travail ; demandé à l'agent.",
        }
    else:
        # opencode et Copilot ne font pas continuer l'agent : c'est l'opérateur qui le lit.
        sortie = {
            "systemMessage": "Ephor : " + message.split(". Avant", 1)[0] + " : demandez à"
            " l'agent de le mettre à jour (fait, en cours, bloqué, prochain pas)."
        }
    print(json.dumps(sortie, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
