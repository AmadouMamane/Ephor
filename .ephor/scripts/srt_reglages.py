"""Les réglages du bac à sable d'opencode (srt), tirés de la liste des zones du moteur.

Sous Claude Code, le bac à sable du système refuse d'écrire une zone de contrôle, détour
compris : c'est la seule vraie frontière pour le terminal. opencode n'en a pas. Le moteur de
ce bac à sable existe seul : `@anthropic-ai/sandbox-runtime` (commande `srt` : Seatbelt sur
Mac, bubblewrap sous Linux). `.ephor/scripts/opencode_bac_a_sable.sh` lance opencode dedans,
avec ces réglages : tout ce qu'opencode lance ne peut plus écrire une zone, ni lire un `.env`.

Mesuré le 30/09/2026 (opencode 1.18.33, srt 0.0.78, faux modèle local) : opencode tourne
dedans ; un script qui écrit `tasks/` est refusé (« Operation not permitted »), comme
`.ephor/`, `.git/config` et `.git/info/` ; `.env` est illisible ; les commits et le code du
projet s'écrivent. Pas encore essayé derrière le proxy de l'entreprise, avec le vrai
fournisseur : les domaines à ouvrir se donnent dans `EPHOR_SRT_DOMAINES`.

    python3 .ephor/scripts/srt_reglages.py > reglages.json
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
# Ce qu'il faut à opencode avec Copilot, puis à ses dépendances (greffon, Python du projet).
DOMAINES = (
    "github.com",
    "api.github.com",
    "api.githubcopilot.com",
    "*.githubcopilot.com",
    "copilot-proxy.githubusercontent.com",
    "models.opencode.ai",
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
)


def zones(racine: Path) -> list[str]:
    """Les zones du dépôt, telles que la liste du moteur les écrit (hors celles du poste)."""
    lignes = (racine / ".ephor" / "zones.txt").read_text(encoding="utf-8").splitlines()
    return [ligne for ligne in lignes if ligne and not ligne.startswith(("#", "~", "!"))]


def interdits(racine: Path) -> list[str]:
    """Ce que rien, dans le bac à sable, ne peut écrire."""
    refus: list[str] = []
    for zone in zones(racine):
        if zone == ".git/":
            # Le reste de .git s'écrit à chaque commit ; srt refuse déjà config et hooks.
            refus.append("./.git/info")
        elif zone == ".opencode/":
            # opencode y installe lui-même les dépendances de son greffon (node_modules).
            refus += [f"./.opencode/{nom}" for nom in ("plugins", "agents", "commands")]
            refus.append("./.opencode/opencode.json")
        else:
            refus.append("./" + zone.rstrip("/"))
    return refus


def reglages(racine: Path, env: Mapping[str, str]) -> dict[str, object]:
    """Les réglages de srt : le projet et les dossiers d'opencode en écriture, les zones et les
    `.env` refusés, le réseau limité aux domaines donnés."""
    maison = Path(env.get("HOME") or Path.home())
    donnees = Path(env.get("XDG_DATA_HOME") or maison / ".local" / "share")
    cache = Path(env.get("XDG_CACHE_HOME") or maison / ".cache")
    etat = Path(env.get("XDG_STATE_HOME") or maison / ".local" / "state")
    temporaire = os.path.realpath(env.get("TMPDIR") or "/tmp")
    ecriture = [".", "/tmp", "/private/tmp", temporaire]
    ecriture += [str(dossier / "opencode") for dossier in (donnees, cache, etat)]
    ecriture += [str(cache / nom) for nom in ("uv", "ephor", "pre-commit")]
    # opencode installe les dépendances de son greffon par npm, qui passe par son cache (mesuré).
    ecriture.append(str(maison / ".npm"))
    domaines = env.get("EPHOR_SRT_DOMAINES", "").split() or list(DOMAINES)
    return {
        "filesystem": {
            "allowWrite": list(dict.fromkeys(ecriture)),
            "denyWrite": interdits(racine),
            "denyRead": ["**/.env"],
        },
        # Une passerelle sur ce poste (127.0.0.1) se joint en direct, pas par le relais de srt :
        # EPHOR_SRT_LOCAL=1 ouvre les ports locaux, tous, d'où le choix explicite (mesuré).
        "network": {
            "allowedDomains": domaines,
            "deniedDomains": [],
            "allowLocalBinding": env.get("EPHOR_SRT_LOCAL") == "1",
        },
    }


def main() -> int:
    json.dump(reglages(RACINE, os.environ), sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
