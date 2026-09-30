"""Un geste de l'opérateur se protège lui-même : il refuse de tourner sous un agent.

Les refus des réglages comparent des débuts de commande : un autre ordre des mots, un script
ou `bash -c` les contournaient (audit du 29/09/2026), et la garde du terminal ne voit que ce
qu'on lui montre. Or chaque outil d'agent pose une variable dans l'environnement de son
terminal, que tout ce qu'il lance hérite, détours compris :
  - Claude Code : CLAUDECODE=1, et AI_AGENT=claude-code_<version>_agent (vus en session) ;
  - opencode : OPENCODE=1 (son code, 1.18) ;
  - le terminal d'agent de VS Code, pour Copilot : AI_AGENT et COPILOT_AGENT=1 (son code).
Un geste de l'opérateur qui en trouve une refuse. Ce n'est pas un mur : un agent décidé peut
retirer la variable, ce que la garde du terminal refuse quand il le fait franchement. Le juge,
la CI et la branche protégée du serveur restent la barrière.

    python3 .ephor/operateur.py "<geste>"   # code 2, avec la raison, sous un agent
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping

VARIABLES = ("CLAUDECODE", "OPENCODE", "AI_AGENT", "COPILOT_AGENT")


def agent_en_cours(env: Mapping[str, str] = os.environ) -> str | None:
    """La variable qui dit qu'un agent a lancé ce processus, ou None."""
    return next((nom for nom in VARIABLES if env.get(nom)), None)


def refus(geste: str, env: Mapping[str, str] = os.environ) -> str | None:
    """Le message de refus quand un agent lance ce geste, ou None."""
    nom = agent_en_cours(env)
    if nom is None:
        return None
    return (
        f"REFUS : {geste} est un geste de l'opérateur, et c'est un agent qui le lance ici "
        f"({nom} est posé dans l'environnement). Lancez-le vous-même, dans votre terminal."
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    message = refus(args[0] if args else "ce geste")
    if message is None:
        return 0
    print(message)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
