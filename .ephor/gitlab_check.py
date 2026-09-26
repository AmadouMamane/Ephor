#!/usr/bin/env python3
"""Les réglages GitLab qui font foi : sont-ils en place ? Lecture seule.

Le juge de la CI ne fait foi que si une branche ne peut pas réécrire la CI, ni fusionner
sans elle. Cela ne se règle pas dans le dépôt : c'est une affaire de réglages du projet
GitLab. Cette commande les lit par l'API et dit, pour chacun, s'il est en place — et
sinon, où cliquer. Elle ne modifie rien.

    make gitlab-check                       # projet et instance déduits de `origin`
    GITLAB_PROJECT=groupe/projet GITLAB_URL=https://gitlab.interne make gitlab-check

Jeton : `GITLAB_TOKEN` (ou `GL_TOKEN`), portée `read_api` suffit. Il n'est jamais affiché.
Certificat d'entreprise refusé : `export SSL_CERT_FILE=<autorité de l'entreprise>`.

Écrit d'après la documentation de l'API GitLab et essayé contre un faux serveur ; pas
encore contre une vraie instance. Bibliothèque standard seule, Python 3.9.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

OK, A_FAIRE, INCONNU = "OK", "À FAIRE", "INCONNU"
DEVELOPPEUR = 30  # niveaux d'accès GitLab : 30 développeur, 40 mainteneur, 0 personne
CODEOWNERS = ("CODEOWNERS", ".gitlab/CODEOWNERS", "docs/CODEOWNERS")


class IllisibleError(Exception):
    """L'API n'a pas répondu ce qu'on attendait (droits, offre, adresse)."""


@dataclass
class Constat:
    etat: str
    titre: str
    detail: str


def depuis_origin() -> tuple[str | None, str | None]:
    """(adresse de l'instance, chemin du projet) d'après le remote `origin`."""
    try:
        url = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return lire_remote(url)


def lire_remote(url: str) -> tuple[str | None, str | None]:
    """`git@hote:groupe/projet.git` ou `https://hote/groupe/projet.git` -> (instance, chemin)."""
    ssh = re.match(r"^(?:ssh://)?[^@/]+@([^:/]+)(?::\d+)?[:/](.+?)(?:\.git)?/?$", url)
    if ssh:
        return f"https://{ssh.group(1)}", ssh.group(2)
    web = re.match(r"^(https?://[^/]+)/(.+?)(?:\.git)?/?$", sans_identifiant(url))
    if web:
        return web.group(1), web.group(2)
    return None, None


def sans_identifiant(url: str) -> str:
    """`https://oauth2:<jeton>@gitlab…` -> `https://gitlab…`. Un jeton glissé dans l'adresse
    du remote était affiché en tête du rapport, et dans chaque message d'erreur."""
    return re.sub(r"^(https?://)[^@/]*@", r"\1", url)


class Api:
    def __init__(self, base: str, jeton: str) -> None:
        if not base.startswith("https://") and not base.startswith("http://127.0.0.1"):
            raise SystemExit(f"erreur : {base} n'est pas en https — le jeton n'y partira pas")
        self.base = base.rstrip("/") + "/api/v4"
        self.jeton = jeton

    def get(self, chemin: str) -> Any:
        requete = urllib.request.Request(
            self.base + chemin, headers={"PRIVATE-TOKEN": self.jeton, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(requete, timeout=20) as reponse:
                return json.loads(reponse.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise IllisibleError(f"HTTP {exc.code}") from exc
        except ValueError as exc:  # une page de connexion, un proxy : pas du JSON
            raise IllisibleError("réponse qui n'est pas du JSON") from exc
        except urllib.error.URLError as exc:
            raise SystemExit(
                f"erreur : {self.base} injoignable ({exc.reason}). Derrière un proxy : "
                "HTTPS_PROXY. Certificat refusé : SSL_CERT_FILE vers l'autorité de l'entreprise."
            ) from exc


def ci_hors_des_branches(projet: dict[str, Any]) -> Constat:
    titre = "la CI ne se réécrit pas depuis une branche"
    chemin = projet.get("ci_config_path") or ""
    if "@" in chemin:
        return Constat(OK, titre, f"fichier de CI pris dans un autre projet : {chemin}")
    return Constat(
        A_FAIRE,
        titre,
        "Réglages > CI/CD > Pipelines généraux > « Fichier de configuration CI/CD » : "
        "`.gitlab-ci.yml@<groupe>/controles`, un projet où seule la plateforme écrit. "
        "(Une politique d'exécution de pipeline, offre Ultimate, remplit le même rôle : "
        "cette commande ne sait pas la voir.)",
    )


def fusion_sous_ci(projet: dict[str, Any]) -> Constat:
    titre = "une fusion exige une CI verte"
    if projet.get("only_allow_merge_if_pipeline_succeeds") is not True:
        return Constat(
            A_FAIRE,
            titre,
            "Réglages > Demandes de fusion > Vérifications : « Le pipeline doit réussir ».",
        )
    if projet.get("allow_merge_on_skipped_pipeline") is True:
        return Constat(
            A_FAIRE,
            titre,
            "« Les pipelines ignorés comptent comme réussis » est coché : un pipeline sauté "
            "laisse fusionner sans juge. Décochez-le (Réglages > Demandes de fusion).",
        )
    return Constat(OK, titre, "pipeline obligatoire, et un pipeline sauté ne compte pas")


def branche_protegee(api: Api, ident: str, branche: str) -> tuple[Constat, dict[str, Any] | None]:
    titre = f"`{branche}` ne reçoit rien qui ne soit passé par une fusion"
    try:
        protection = api.get(f"/projects/{ident}/protected_branches/{urllib.parse.quote(branche)}")
    except IllisibleError:
        return (
            Constat(
                A_FAIRE,
                titre,
                f"Réglages > Dépôt > Branches protégées : protéger `{branche}`, "
                "« Autorisés à pousser » : personne, « Autorisés à fusionner » : mainteneurs.",
            ),
            None,
        )
    pousseurs = [n.get("access_level") for n in protection.get("push_access_levels", [])]
    if any(isinstance(n, int) and 0 < n <= DEVELOPPEUR for n in pousseurs):
        detail = "des développeurs peuvent y pousser directement, sans juge ni revue"
        return Constat(
            A_FAIRE, titre, detail + " : « Autorisés à pousser » → personne."
        ), protection
    if protection.get("allow_force_push") is True:
        return Constat(A_FAIRE, titre, "la poussée forcée y est permise : retirez-la."), protection
    return Constat(OK, titre, "protégée, sans poussée directe de développeur"), protection


def proprietaires(api: Api, ident: str, branche: str, protection: dict[str, Any] | None) -> Constat:
    titre = "les zones de contrôle passent par leurs propriétaires"
    fichier = None
    for chemin in CODEOWNERS:
        try:
            api.get(
                f"/projects/{ident}/repository/files/{urllib.parse.quote(chemin, safe='')}"
                f"?ref={urllib.parse.quote(branche)}"
            )
        except IllisibleError:
            continue
        fichier = chemin
        break
    if fichier is None:
        return Constat(
            A_FAIRE,
            titre,
            "aucun fichier CODEOWNERS sur la branche principale : partez de "
            "`.ephor/docs/CODEOWNERS.example`, mettez-y de vrais groupes, commitez-le en "
            "`.gitlab/CODEOWNERS`.",
        )
    if protection is None or protection.get("code_owner_approval_required") is not True:
        return Constat(
            A_FAIRE,
            titre,
            f"`{fichier}` existe, mais son approbation n'est pas obligatoire : Réglages > Dépôt "
            "> Branches protégées > « Approbation des propriétaires du code » (offre Premium).",
        )
    return Constat(OK, titre, f"`{fichier}`, approbation obligatoire sur la branche principale")


def approbations(api: Api, ident: str) -> Constat:
    titre = "personne n'approuve son propre travail"
    try:
        regles = api.get(f"/projects/{ident}/approvals")
    except IllisibleError as exc:
        return Constat(
            INCONNU, titre, f"réglages d'approbation illisibles ({exc}) : droits, ou offre Free."
        )
    manques = []
    if regles.get("merge_requests_author_approval") is not False:
        manques.append("l'auteur peut approuver sa propre demande")
    if regles.get("merge_requests_disable_committers_approval") is not True:
        manques.append("qui a commité peut approuver")
    if regles.get("disable_overriding_approvers_per_merge_request") is not True:
        manques.append("une demande peut changer ses propres règles d'approbation")
    if regles.get("reset_approvals_on_push") is not True:
        manques.append("une approbation survit à un commit ajouté après elle")
    if manques:
        return Constat(
            A_FAIRE, titre, "Réglages > Demandes de fusion > Approbations : " + " ; ".join(manques)
        )
    return Constat(OK, titre, "auteur et commiteurs exclus, règles figées, remise à zéro au push")


def constats(api: Api, projet_chemin: str) -> list[Constat]:
    ident = urllib.parse.quote(projet_chemin, safe="")
    try:
        projet = api.get(f"/projects/{ident}")
    except IllisibleError as exc:
        raise SystemExit(
            f"erreur : projet {projet_chemin} illisible ({exc}). Vérifie GITLAB_PROJECT, et que "
            "le jeton a la portée read_api sur ce projet."
        ) from exc
    branche = projet.get("default_branch") or "main"
    protection_constat, protection = branche_protegee(api, ident, branche)
    return [
        ci_hors_des_branches(projet),
        fusion_sous_ci(projet),
        protection_constat,
        proprietaires(api, ident, branche, protection),
        approbations(api, ident),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Réglages GitLab qui font foi (lecture seule).")
    parser.add_argument("--url", default=os.environ.get("GITLAB_URL"))
    parser.add_argument("--projet", default=os.environ.get("GITLAB_PROJECT"))
    args = parser.parse_args(argv)

    url, projet = args.url, args.projet
    if not url or not projet:
        url_origin, projet_origin = depuis_origin()
        url, projet = url or url_origin, projet or projet_origin
    if not url or not projet:
        print(
            "erreur : instance ou projet inconnus — "
            "GITLAB_URL=https://… GITLAB_PROJECT=groupe/projet"
        )
        return 2
    url = sans_identifiant(url)
    jeton = os.environ.get("GITLAB_TOKEN") or os.environ.get("GL_TOKEN")
    if not jeton:
        print("erreur : exportez GITLAB_TOKEN (portée read_api) — il n'est jamais affiché")
        return 2

    liste = constats(Api(url, jeton), projet)
    print(f"GitLab · {projet} · {url}")
    for constat in liste:
        print(f"  [{constat.etat:^7}] {constat.titre}")
        if constat.etat != OK:
            print(f"            → {constat.detail}")
    manquants = [c for c in liste if c.etat != OK]
    if not manquants:
        print("La porte de la CI fait foi : aucune branche ne peut la contourner.")
        return 0
    print(
        f"{len(manquants)} réglage(s) à faire : tant qu'ils manquent, le juge de la CI arrête un "
        "agent pressé, pas une branche qui réécrirait la CI. Détail : .ephor/docs/12-gitlab.md"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
