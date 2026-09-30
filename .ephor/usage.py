#!/usr/bin/env python3
"""Ce qu'une tâche a coûté en tokens — mesuré, jamais deviné.

Trois sources, toutes locales et sûres :

- **Claude Code** : le journal de session JSONL, où chaque réponse porte son `usage`.
  Une même requête y apparaît plusieurs fois (texte, appels d'outils) : on dédoublonne
  par identifiant de requête, sinon on compte double.
- **Copilot** : le journal `events.jsonl` de la session. Il ne donne les tokens qu'à la
  fermeture de la session ; en cours de route, seulement ce que Copilot facture (AIU,
  requêtes premium). Tant que le détail manque, la mesure le dit (`detail_complet`).
- **opencode** : sa base SQLite, où chaque session porte déjà ses totaux et son coût.

Bibliothèque standard, Python 3.9, lecture seule, aucun réseau : comme le juge.

    python3 .ephor/usage.py claude <journal.jsonl> [--etat .verify/usage/T-1.json]
    python3 .ephor/usage.py copilot <events.jsonl> [--etat .verify/usage/T-1.json]
    python3 .ephor/usage.py opencode [--repo .] [--db <chemin>]
    python3 .ephor/usage.py rafraichir --etat .verify/usage/T-1.json
    python3 .ephor/usage.py remplissage <journal.jsonl>   # contexte occupé, Claude Code
    python3 .ephor/usage.py contexte < charge.json        # alerte au-delà de 60 %
    python3 .ephor/usage.py rapport            # ou `make usage`

`facturables` = entrée + écriture de cache + sortie. Les lectures de cache sont comptées
à part : elles coûtent environ dix fois moins, et elles écraseraient le total.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

CHAMPS = ("input", "output", "cache_write", "cache_read")


def vide(source: str) -> dict[str, Any]:
    compte: dict[str, Any] = dict.fromkeys(CHAMPS, 0)
    compte.update(requetes=0, facturables=0, source=source)
    return compte


def totalise(compte: dict[str, Any]) -> dict[str, Any]:
    compte["facturables"] = compte["input"] + compte["cache_write"] + compte["output"]
    return compte


def nombre(valeur: Any) -> int:
    """Un compteur lu dans un journal : un nombre, sinon zéro (jamais une exception)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return 0
    return int(valeur)


def anciens_journaux(etat: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Les sous-totaux déjà mesurés, un par journal, quel que soit l'agent qui l'a écrit.

    Une tâche survit aux sessions — après un `/clear`, le journal change de nom — et peut
    passer d'un agent à l'autre. Le total de la tâche est la somme de ses journaux.
    """
    anciens = (etat or {}).get("journaux")
    if not isinstance(anciens, dict):
        return {}
    return {str(k): dict(v) for k, v in anciens.items() if isinstance(v, dict)}


def somme(journaux: dict[str, dict[str, Any]]) -> dict[str, Any]:
    # Un sous-total sans agent date d'avant Copilot : c'était Claude Code.
    agents = sorted({str(s.get("agent") or "claude-code") for s in journaux.values()})
    compte = vide(" + ".join(agents) or "claude-code")
    for sous_total in journaux.values():
        for champ in CHAMPS:
            compte[champ] += nombre(sous_total.get(champ))
        compte["requetes"] += nombre(sous_total.get("requetes"))
    copilot = [s for s in journaux.values() if s.get("agent") == "copilot"]
    if copilot:
        compte["nano_aiu"] = sum(nombre(s.get("nano_aiu")) for s in copilot)
        compte["requetes_premium"] = sum(nombre(s.get("requetes_premium")) for s in copilot)
        compte["detail_complet"] = all(
            nombre(s.get("aiu_detaille")) >= nombre(s.get("nano_aiu")) for s in copilot
        )
    delegues = sum(1 for s in journaux.values() if s.get("sous_agent"))
    if delegues:
        compte["sous_agents"] = delegues
    compte["journaux"] = journaux
    return totalise(compte)


def sous_agents(journal: Path) -> list[Path]:
    """Les journaux des sous-agents d'une session de Claude Code.

    Chaque sous-agent écrit le sien, dans `<session>/subagents/`, à côté du journal
    principal, et ses requêtes n'apparaissent nulle part ailleurs. Mesuré le 25/09/2026 sur
    une vraie session : 1,14 M tokens facturables dans le journal principal, 0,54 M dans cinq
    journaux de sous-agents — un tiers de la dépense invisible au plafond, alors qu'AGENTS.md
    demande justement de déléguer l'exploration à des sous-agents.
    """
    # Les agents d'un « Workflow » écrivent sous subagents/workflows/<run>/ : un lecteur qui ne
    # connaît que subagents/agent-*.jsonl ne les voyait pas (anthropics/claude-code#93620).
    dossier = journal.with_suffix("") / "subagents"
    return sorted(dossier.rglob("*.jsonl")) if dossier.is_dir() else []


def _claude_un_journal(journal: Path, sous: dict[str, Any]) -> dict[str, Any]:
    """Ajoute au sous-total d'UN journal ce qui y a été écrit depuis la dernière lecture."""
    depart = nombre(sous.get("octets"))
    if journal.stat().st_size < depart:  # journal réécrit : ce sous-total repart de zéro
        sous, depart = {}, 0
    sous["agent"] = "claude-code"
    for champ in (*CHAMPS, "requetes"):
        sous[champ] = nombre(sous.get(champ))
    deja_vues = sous.get("vues")
    ordre: list[str] = [str(v) for v in deja_vues] if isinstance(deja_vues, list) else []
    vues = set(ordre)
    # Dans le journal d'un sous-agent, chaque bloc d'une réponse porte le compte de sortie du
    # moment où il a été écrit : seul le dernier est juste (anthropics/claude-code#93620). On
    # garde donc, par réponse, la plus grande valeur vue, et on ajoute l'écart.
    deja_sorties = sous.get("sorties")
    sorties: dict[str, int] = (
        {str(k): nombre(v) for k, v in deja_sorties.items()}
        if isinstance(deja_sorties, dict)
        else {}
    )

    with journal.open(encoding="utf-8", errors="ignore") as fichier:
        fichier.seek(depart)
        position = fichier.tell()
        while True:
            ligne = fichier.readline()
            if not ligne:
                break
            if not ligne.endswith("\n"):
                break  # ligne en cours d'écriture : on garde la position d'AVANT pour la relire
            position = fichier.tell()  # une ligne complète, comptée ou écartée, ne se relit pas
            try:
                entree = json.loads(ligne)
            except ValueError:
                continue
            # Un compteur à `null` (une passerelle de modèles qui ne le remplit pas) ou une
            # ligne qui n'est pas un objet faisaient tomber la lecture ENTIÈRE, à chaque tour :
            # plus de mesure, donc plus de plafond, et rien ne le disait (vu le 25/09/2026).
            if not isinstance(entree, dict) or not isinstance(entree.get("message"), dict):
                continue
            message = entree["message"]
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            identifiant = str(entree.get("requestId") or message.get("id") or "")
            if not identifiant:
                continue
            sortie = nombre(usage.get("output_tokens"))
            if identifiant in vues:
                if sortie > sorties.get(identifiant, 0):
                    sous["output"] += sortie - sorties.get(identifiant, 0)
                    sorties[identifiant] = sortie
                continue
            vues.add(identifiant)
            ordre.append(identifiant)
            sorties[identifiant] = sortie
            sous["requetes"] += 1
            sous["input"] += nombre(usage.get("input_tokens"))
            sous["output"] += sortie
            sous["cache_write"] += nombre(usage.get("cache_creation_input_tokens"))
            sous["cache_read"] += nombre(usage.get("cache_read_input_tokens"))
        sous["octets"] = position
    sous["vues"] = ordre[-200:]  # de quoi dédoublonner à la frontière, pas un historique
    sous["sorties"] = {cle: sorties[cle] for cle in sous["vues"] if cle in sorties}
    return sous


def claude_code(journal: Path, etat: dict[str, Any] | None = None) -> dict[str, Any]:
    """Somme le journal de session de Claude Code ET ceux de ses sous-agents, en reprenant
    chacun où on s'était arrêté."""
    journaux = anciens_journaux(etat)
    lus = [(journal, False), *((fichier, True) for fichier in sous_agents(journal))]
    for fichier, delegue in lus:
        sous = _claude_un_journal(fichier, journaux.get(str(fichier), {}))
        if delegue:
            sous["sous_agent"] = True
        journaux[str(fichier)] = sous
    return somme(journaux)


# Assez pour contenir la dernière réponse, même suivie de grosses sorties d'outils.
QUEUE_OCTETS = 4 * 1024 * 1024
CHAMPS_CONTEXTE = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)


def remplissage(journal: Path) -> int | None:
    """Ce qu'occupe la conversation principale de Claude Code, d'après sa dernière réponse.

    Chaque requête renvoie toute la conversation : l'entrée de la dernière réponse, caches
    compris, plus ce qu'elle a produit, c'est ce que le modèle aura en mémoire au tour
    suivant. On ne lit que la fin du journal : il peut peser des centaines de mégaoctets.
    """
    try:
        taille = journal.stat().st_size
        with journal.open("rb") as fichier:
            fichier.seek(max(0, taille - QUEUE_OCTETS))
            lignes = fichier.read().decode("utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    for ligne in reversed(lignes):
        try:
            entree = json.loads(ligne)
        except ValueError:
            continue
        if not isinstance(entree, dict) or entree.get("isSidechain"):
            continue
        message = entree.get("message")
        usage = message.get("usage") if isinstance(message, dict) else None
        if isinstance(usage, dict):
            return sum(nombre(usage.get(champ)) for champ in CHAMPS_CONTEXTE)
    return None


# Les seuls évènements de Copilot qui comptent. Un journal peut peser des centaines de
# mégaoctets (sorties d'outils) : une ligne qui ne cite aucun d'eux n'est pas décodée.
GENRES_COPILOT = ('"assistant.message"', '"session.usage_checkpoint"', '"session.shutdown"')

# Les tokens d'une fermeture de session Copilot (`tokenDetails`), rangés dans nos champs.
DETAIL_COPILOT = (
    ("input", "input"),
    ("output", "output"),
    ("cache_write", "cache_write"),
    ("cache_read", "cache_read"),
)


def copilot(journal: Path, etat: dict[str, Any] | None = None) -> dict[str, Any]:
    """Somme le journal d'une session Copilot (`events.jsonl`), en reprenant où on s'était arrêté.

    Copilot n'écrit pas les tokens de chaque requête (mesuré le 24/09/2026, vue « agents »
    de VS Code). Il écrit :

    - une réponse du modèle par requête (`assistant.message`), qu'on compte ;
    - après chaque demande, ce qu'il a facturé depuis le début de la session
      (`session.usage_checkpoint` : AIU, requêtes premium) — écrit APRÈS la fin de tour,
      donc toujours en retard d'une demande sur le hook de budget ;
    - à la fermeture (`session.shutdown`), le détail des tokens, mais seulement depuis la
      dernière reprise de la session. VS Code ferme une session quand il en ouvre une
      autre, parfois le lendemain. Une fermeture sans détail (reprise après un arrêt
      brutal) laisse une portion perdue.

    Chaque portion détaillée couvre les AIU facturés depuis la fermeture précédente. Le
    détail est complet quand il couvre tout ce qui a été facturé ; sinon, les tokens
    comptés sont un minimum, et la mesure le dit.
    """
    journaux = anciens_journaux(etat)
    cle = str(journal)
    sous: dict[str, Any] = journaux.get(cle, {})
    depart = nombre(sous.get("octets"))
    if journal.stat().st_size < depart:  # journal réécrit : ce sous-total repart de zéro
        sous, depart = {}, 0
    sous["agent"] = "copilot"
    for champ in (*CHAMPS, "requetes", "nano_aiu", "requetes_premium", "aiu_ferme", "aiu_detaille"):
        sous[champ] = nombre(sous.get(champ))

    with journal.open(encoding="utf-8", errors="ignore") as fichier:
        fichier.seek(depart)
        position = fichier.tell()
        while True:
            ligne = fichier.readline()
            if not ligne.endswith("\n"):
                break  # fin, ou ligne en cours d'écriture : on la relira
            position = fichier.tell()  # pas d'identifiant à dédoublonner : on ne relit rien
            if not any(genre in ligne for genre in GENRES_COPILOT):
                continue
            try:
                entree = json.loads(ligne)
            except ValueError:
                continue
            if not isinstance(entree, dict):
                continue
            genre = entree.get("type")
            donnees = entree.get("data")
            if not isinstance(donnees, dict):
                donnees = {}
            if genre == "assistant.message":
                sous["requetes"] += 1
            if genre not in ("session.usage_checkpoint", "session.shutdown"):
                continue
            aiu = nombre(donnees.get("totalNanoAiu"))
            sous["nano_aiu"] = max(sous["nano_aiu"], aiu)
            premium = nombre(donnees.get("totalPremiumRequests"))
            sous["requetes_premium"] = max(sous["requetes_premium"], premium)
            if genre != "session.shutdown":
                continue
            detail = donnees.get("tokenDetails")
            detail = detail if isinstance(detail, dict) else {}
            jetons = {}
            for champ, cle_copilot in DETAIL_COPILOT:
                case = detail.get(cle_copilot)
                jetons[champ] = nombre(case.get("tokenCount")) if isinstance(case, dict) else 0
            if any(jetons.values()):
                for champ, valeur in jetons.items():
                    sous[champ] += valeur
                sous["aiu_detaille"] += max(0, aiu - sous["aiu_ferme"])
            sous["aiu_ferme"] = max(sous["aiu_ferme"], aiu)
        sous["octets"] = position
    journaux[cle] = sous
    return somme(journaux)


def rafraichir(etat: dict[str, Any]) -> dict[str, Any]:
    """Relit les journaux d'une mesure : ce qu'ils ont écrit après le dernier tour mesuré.

    Sous Claude Code, un sous-agent encore au travail à la fin du tour écrit ensuite.

    La fermeture d'une session Copilot — la seule à donner les tokens — tombe quand VS Code
    ouvre une autre session : le hook de budget de la première ne tourne plus. Relire ses
    journaux au moment du rapport ou du jugement rattrape ce détail. Un journal disparu
    garde ce qu'on en savait.
    """
    mesure = etat
    for chemin, sous in anciens_journaux(etat).items():
        journal = Path(chemin)
        if not journal.is_file():
            continue
        if sous.get("agent") == "copilot":
            mesure = copilot(journal, mesure)
        elif sous.get("agent") == "claude-code" and not sous.get("sous_agent"):
            mesure = claude_code(journal, mesure)
    return mesure


def opencode(base: Path, repertoire: Path) -> dict[str, Any]:
    """Somme les sessions d'opencode ouvertes dans ce dépôt (base SQLite, lecture seule)."""
    compte = vide("opencode")
    if not base.exists():
        return totalise(compte)
    # `as_uri` échappe ce qu'un chemin peut porter (`?`, `#`, `%`, espace) : collé tel quel
    # derrière `file:`, un `?` dans le chemin devenait le début des options.
    lien = base.resolve().as_uri() + "?mode=ro"
    try:
        connexion = sqlite3.connect(lien, uri=True)
    except sqlite3.Error:  # base illisible (droits, verrou, pas une base) : on le dit
        compte["source"] = "opencode (base illisible)"
        return totalise(compte)
    try:
        lignes = connexion.execute(
            "select tokens_input, tokens_output, tokens_cache_write, tokens_cache_read, cost"
            " from session where directory = ? or path = ?",
            (str(repertoire), str(repertoire)),
        ).fetchall()
    except sqlite3.DatabaseError:  # schéma changé : on le dit plutôt que de mentir
        compte["source"] = "opencode (schéma inconnu)"
        return totalise(compte)
    finally:
        connexion.close()
    cout = 0.0
    for entree, sortie, ecriture, lecture, prix in lignes:
        compte["requetes"] += 1
        compte["input"] += nombre(entree)
        compte["output"] += nombre(sortie)
        compte["cache_write"] += nombre(ecriture)
        compte["cache_read"] += nombre(lecture)
        cout += prix if isinstance(prix, (int, float)) and not isinstance(prix, bool) else 0.0
    if cout:
        compte["cout_dollars"] = round(cout, 4)
    return totalise(compte)


# ------------------------------------------------------- sous-agents : où part le temps
#
# Vu en session réelle (30/09/2026, #34) : les tâches confiées à un sous-agent, le planner
# surtout, prenaient énormément de temps. L'effort ? Le modèle ? Le nombre de fichiers lus ?
# Sans chiffres, on ne tranche pas. Pour chaque sous-agent récent du dépôt : sa durée, son
# modèle et son effort, ce qu'il a produit, ses appels d'outils et les fichiers qu'il a lus.


SORTIE_PARTIELLE = 10


def _secondes(horodatage: Any) -> float | None:
    if not isinstance(horodatage, str):
        return None
    try:
        return datetime.fromisoformat(horodatage.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def profil_claude(journal: Path) -> dict[str, Any]:
    """Un sous-agent de Claude Code, d'après son journal et la fiche posée à côté."""
    fiche: dict[str, Any] = {}
    with suppress(OSError, ValueError):
        lue = json.loads(journal.with_suffix(".meta.json").read_text(encoding="utf-8"))
        fiche = lue if isinstance(lue, dict) else {}
    debut = fin = None
    sorties: dict[str, int] = {}
    modeles: set[str] = set()
    efforts: set[str] = set()
    outils: Counter[str] = Counter()
    lus: set[str] = set()
    with journal.open(encoding="utf-8", errors="ignore") as fichier:
        for ligne in fichier:
            try:
                entree = json.loads(ligne)
            except ValueError:
                continue
            if not isinstance(entree, dict):
                continue
            moment = _secondes(entree.get("timestamp"))
            if moment is not None:
                debut = moment if debut is None else min(debut, moment)
                fin = moment if fin is None else max(fin, moment)
            if isinstance(entree.get("effort"), str):
                efforts.add(entree["effort"])
            message = entree.get("message")
            if not isinstance(message, dict) or entree.get("type") != "assistant":
                continue
            if isinstance(message.get("model"), str):
                modeles.add(message["model"])
            identifiant = str(entree.get("requestId") or message.get("id") or "")
            usage = message.get("usage")
            if identifiant and isinstance(usage, dict):
                sortie_lue = nombre(usage.get("output_tokens"))
                sorties[identifiant] = max(sorties.get(identifiant, 0), sortie_lue)
            contenu = message.get("content")
            for bloc in contenu if isinstance(contenu, list) else []:
                if isinstance(bloc, dict) and bloc.get("type") == "tool_use":
                    outils[str(bloc.get("name"))] += 1
                    cible = (bloc.get("input") or {}).get("file_path")
                    if bloc.get("name") == "Read" and isinstance(cible, str):
                        lus.add(cible)
    return {
        "outil": "claude-code",
        "role": str(fiche.get("agentType") or journal.stem),
        "demande": str(fiche.get("description") or ""),
        "modele": ", ".join(sorted(modeles)),
        "effort": ", ".join(sorted(efforts)),
        "debut": debut,
        "secondes": round((fin or 0) - (debut or 0), 1) if debut is not None else None,
        "requetes": len(sorties),
        "sortie": sum(sorties.values()),
        # Quelques tokens par réponse, sur une réponse qui a réfléchi et appelé des outils :
        # c'est le compte du début de la réponse, jamais complété (anthropics/claude-code#93620).
        "sortie_ecrite": not sorties
        or sorted(sorties.values())[len(sorties) // 2] > SORTIE_PARTIELLE,
        "reflexion": None,  # comptée dans la sortie par Claude Code
        "outils": dict(outils.most_common()),
        "fichiers_lus": len(lus),
    }


def dossier_claude(repo: Path) -> Path:
    """Où Claude Code range les journaux des sessions ouvertes dans ce dépôt."""
    nom = re.sub(r"[^A-Za-z0-9]", "-", str(repo.resolve()))
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects" / nom


def sous_agents_claude(repo: Path, combien: int = 10) -> list[dict[str, Any]]:
    dossier = dossier_claude(repo)
    if not dossier.is_dir():
        return []
    sessions = sorted(dossier.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
    profils: list[dict[str, Any]] = []
    for session in sessions[:20]:
        for journal in sous_agents(session):
            with suppress(OSError):
                profils.append(profil_claude(journal))
    profils.sort(key=lambda p: p.get("debut") or 0, reverse=True)
    return profils[:combien]


def sous_agents_opencode(base: Path, repertoire: Path, combien: int = 10) -> list[dict[str, Any]]:
    """Les sous-agents d'opencode dans ce dépôt : ses sessions filles (base SQLite, lecture
    seule). opencode compte la réflexion à part : on la montre."""
    if not base.exists():
        return []
    try:
        connexion = sqlite3.connect(base.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return []
    profils: list[dict[str, Any]] = []
    try:
        sessions = connexion.execute(
            "select id, title, tokens_output, tokens_reasoning from session"
            " where parent_id is not null and (directory = ? or path = ?)"
            " order by time_created desc limit ?",
            (str(repertoire), str(repertoire), combien),
        ).fetchall()
        for ident, titre, sortie, reflexion in sessions:
            messages = connexion.execute(
                "select time_created, time_updated, data from message where session_id = ?",
                (ident,),
            ).fetchall()
            parts = connexion.execute(
                "select data from part where session_id = ?", (ident,)
            ).fetchall()
            debut = min((m[0] for m in messages), default=None)
            fin = max((m[1] for m in messages), default=None)
            role, modele = "", ""
            for _, _, donnees in messages:
                with suppress(ValueError, TypeError):
                    lu = json.loads(donnees)
                    role = role or str(lu.get("agent") or "")
                    modele = modele or str(
                        (lu.get("model") or {}).get("modelID") or lu.get("modelID") or ""
                    )
            outils: Counter[str] = Counter()
            lus: set[str] = set()
            for (donnees,) in parts:
                with suppress(ValueError, TypeError):
                    lu = json.loads(donnees)
                    if lu.get("type") == "tool":
                        outils[str(lu.get("tool"))] += 1
                        cible = ((lu.get("state") or {}).get("input") or {}).get("filePath")
                        if lu.get("tool") == "read" and isinstance(cible, str):
                            lus.add(cible)
            profils.append(
                {
                    "outil": "opencode",
                    "role": role or str(titre),
                    "demande": str(titre),
                    "modele": modele,
                    "effort": "",
                    "debut": debut / 1000 if debut else None,
                    "secondes": round((fin - debut) / 1000, 1) if debut and fin else None,
                    "requetes": len(messages),
                    "sortie": nombre(sortie),
                    "reflexion": nombre(reflexion),
                    "outils": dict(outils.most_common()),
                    "fichiers_lus": len(lus),
                }
            )
    except sqlite3.DatabaseError:
        return []
    finally:
        connexion.close()
    return profils


def duree(secondes: float | None) -> str:
    if secondes is None:
        return "?"
    minutes, reste = divmod(round(secondes), 60)
    return f"{minutes} min {reste:02d} s" if minutes else f"{reste} s"


def ligne_sous_agent(profil: dict[str, Any]) -> str:
    appels = sum(profil["outils"].values())
    detail = ", ".join(f"{nom} {nombre}" for nom, nombre in list(profil["outils"].items())[:4])
    reglage = " · ".join(v for v in (profil["modele"], profil["effort"]) if v)
    reflexion = f" (réflexion {milliers(profil['reflexion'])})" if profil.get("reflexion") else ""
    sortie = f"sortie {milliers(profil['sortie'])}{reflexion}"
    if not profil.get("sortie_ecrite", True):
        sortie = "sortie non écrite par Claude Code"
    return (
        f"  {profil['role'][:16]:<16} {duree(profil['secondes']):>12}  {reglage[:28]:<28}"
        f" {sortie} · {appels} appels ({detail}) · {profil['fichiers_lus']} fichiers lus"
    )


def milliers(nombre: int) -> str:
    return f"{nombre:,}".replace(",", " ")


# La règle du README : au-delà, `/handoff` puis une nouvelle session. Plus loin, le modèle
# relit à chaque message une conversation qui ne sert plus, et la paie.
SEUIL_CONTEXTE = 60
FENETRE_COURTE = 200_000
FENETRE_LONGUE = 1_000_000


def alerte_contexte(charge: dict[str, Any], declaree: str | None = None) -> str | None:
    """Le message pour l'opérateur quand la conversation dépasse le seuil, sinon rien.

    opencode : le greffon connaît la taille exacte de la mémoire du modèle et la passe.
    Claude Code : ses hooks ne la reçoivent pas et son journal ne la note pas (une session
    à un million de tokens y porte le même nom de modèle qu'une autre, vu le 29/09/2026).
    On la suppose, sauf si l'opérateur la déclare dans EPHOR_CONTEXTE_TOKENS.
    """
    contexte = charge.get("contexte")
    if isinstance(contexte, dict):
        occupe, taille = nombre(contexte.get("occupe")), nombre(contexte.get("taille"))
        supposee = False
    else:
        journal = charge.get("transcript_path")
        occupe = (remplissage(Path(journal)) if isinstance(journal, str) else None) or 0
        fixee = int(declaree) if declaree and declaree.isdigit() else 0
        taille = fixee or (FENETRE_LONGUE if occupe > FENETRE_COURTE else FENETRE_COURTE)
        supposee = not fixee
    if occupe <= 0 or taille <= 0 or occupe * 100 < SEUIL_CONTEXTE * taille:
        return None
    message = (
        f"Contexte à {occupe * 100 // taille} % ({milliers(occupe)} tokens sur "
        f"{milliers(taille)}) : /handoff, puis une nouvelle session."
    )
    if supposee:
        message += " Taille de la mémoire supposée : EPHOR_CONTEXTE_TOKENS pour la déclarer."
    return message


def facture_copilot(mesure: dict[str, Any]) -> str:
    """Ce que Copilot a facturé, dans ses unités : « 3,56 AIU · 13 requêtes premium »."""
    aiu = f"{nombre(mesure.get('nano_aiu')) / 1e9:.2f}".replace(".", ",")
    premium = nombre(mesure.get("requetes_premium"))
    return f"{aiu} AIU · {premium} requête{'s' if premium > 1 else ''} premium"


# ------------------------------------------------ le dépôt entier : tous ses dossiers
#
# Une tâche s'ouvre dans son propre dossier (un worktree git, #32) : l'agent y travaille, le
# juge y rend son verdict, le hook de budget y dépose la mesure. Lus dans le seul dossier
# principal, verdicts et mesures n'existaient pas : `make score` disait « aucun jugement »,
# `make taches` laissait « en cours » une tâche vérifiée, et `make task-clore` effaçait le tout
# avec le dossier (mesuré le 30/09/2026). On lit donc tous les dossiers du dépôt, et
# `make task-clore` rapatrie ce qu'un dossier contient avant de le retirer.


def dossiers_du_depot(repo: Path) -> list[Path]:
    """Ce dossier, puis les autres dossiers de travail du même dépôt (`git worktree list`)."""
    dossiers = [repo]
    try:
        sortie = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "-C",
                str(repo),
                "worktree",
                "list",
                "--porcelain",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return dossiers
    vus = {repo.resolve()}
    for ligne in sortie.splitlines():
        if not ligne.startswith("worktree "):
            continue
        dossier = Path(ligne[len("worktree ") :])
        with suppress(OSError):
            if dossier.is_dir() and dossier.resolve() not in vus:
                vus.add(dossier.resolve())
                dossiers.append(dossier)
    return dossiers


def lire_journal(chemin: Path) -> list[dict[str, Any]]:
    """Les jugements d'un journal, une ligne chacun ; une ligne abîmée ne masque pas les autres."""
    try:
        lignes = chemin.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    entrees: list[dict[str, Any]] = []
    for ligne in lignes:
        if not ligne.strip():
            continue
        try:
            entree = json.loads(ligne)
        except ValueError:
            continue
        if isinstance(entree, dict):
            entrees.append(entree)
    return entrees


def journal_des_jugements(repo: Path, tache: str | None = None) -> list[dict[str, Any]]:
    """Les jugements du dépôt, tous dossiers confondus, dans l'ordre où ils ont été rendus. Un
    même jugement (son `run_id`), rapatrié d'un dossier de tâche, ne compte qu'une fois."""
    journaux = [
        entrees
        for dossier in dossiers_du_depot(repo)
        if (entrees := lire_journal(dossier / ".verify" / "journal.jsonl"))
    ]
    if len(journaux) == 1:
        tout = journaux[0]
    else:
        vus: set[str] = set()
        tout = []
        for entrees in journaux:
            for entree in entrees:
                course = entree.get("run_id")
                if isinstance(course, str) and course:
                    if course in vus:
                        continue
                    vus.add(course)
                tout.append(entree)
        tout.sort(key=lambda entree: str(entree.get("finished_at") or ""))
    return [entree for entree in tout if tache is None or entree.get("task_id") == tache]


def fusionne_mesures(mesures: list[dict[str, Any]]) -> dict[str, Any]:
    """Une tâche mesurée dans deux dossiers : ses journaux de session, réunis. Un même journal
    garde la mesure qui l'a lu le plus loin."""
    if len(mesures) == 1:
        return mesures[0]
    journaux: dict[str, dict[str, Any]] = {}
    for mesure in mesures:
        for chemin, sous in anciens_journaux(mesure).items():
            if chemin not in journaux or nombre(sous.get("octets")) > nombre(
                journaux[chemin].get("octets")
            ):
                journaux[chemin] = sous
    return somme(journaux)


def rapatrier(source: Path, cible: Path) -> list[str]:
    """Garde dans `cible` (le dossier principal) les jugements et les mesures d'un dossier de
    tâche qu'on va retirer (`make task-clore`). Rend ce qui a été gardé."""
    faits: list[str] = []
    journal = cible / ".verify" / "journal.jsonl"
    deja = {entree.get("run_id") for entree in lire_journal(journal) if entree.get("run_id")}
    nouvelles: list[str] = []
    try:
        lignes = (source / ".verify" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        lignes = []
    for ligne in lignes:
        try:
            entree = json.loads(ligne)
        except ValueError:
            continue
        if isinstance(entree, dict) and (not entree.get("run_id") or entree["run_id"] not in deja):
            nouvelles.append(ligne.strip())
    if nouvelles:
        journal.parent.mkdir(parents=True, exist_ok=True)
        with journal.open("a", encoding="utf-8") as flux:
            flux.write("".join(ligne + "\n" for ligne in nouvelles))
        faits.append(f"{len(nouvelles)} jugement(s)")
    dossier = source / ".verify" / "usage"
    for fichier in sorted(dossier.glob("*.json")) if dossier.is_dir() else []:
        try:
            mesure = json.loads(fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(mesure, dict):
            continue
        garde = cible / ".verify" / "usage" / fichier.name
        mesures = [mesure]
        with suppress(OSError, ValueError):
            existante = json.loads(garde.read_text(encoding="utf-8"))
            if isinstance(existante, dict):
                mesures.insert(0, existante)
        garde.parent.mkdir(parents=True, exist_ok=True)
        garde.write_text(
            json.dumps(fusionne_mesures(mesures), ensure_ascii=False) + "\n", encoding="utf-8"
        )
        faits.append(f"la mesure de tokens de {fichier.stem}")
    return faits


def lire_et_rafraichir(fichier: Path) -> dict[str, Any] | None:
    """Une mesure déposée par le hook, remise à jour si des journaux Copilot ont avancé."""
    try:
        mesure = json.loads(fichier.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(mesure, dict):
        return None
    try:
        neuve = rafraichir(mesure)
    except OSError:
        return mesure
    if neuve != mesure:
        # Écriture impossible : le rapport reste juste ; seul le juge verra l'ancienne mesure.
        with contextlib.suppress(OSError):
            fichier.write_text(json.dumps(neuve, ensure_ascii=False) + "\n", encoding="utf-8")
    return neuve


def rapport(repo: Path) -> str:
    """Ce que chaque tâche a coûté, d'après les mesures déposées par le hook de budget."""
    lignes = [
        "tokens facturables = entrée + écriture de cache + sortie",
        "(les lectures de cache, environ dix fois moins chères, sont comptées à part)",
    ]
    # Chaque dossier du dépôt : une tâche se mesure dans le sien (#32).
    dossiers = dossiers_du_depot(repo)
    fichiers: dict[str, list[Path]] = {}
    for dossier in dossiers:
        mesures_ici = dossier / ".verify" / "usage"
        for fichier in sorted(mesures_ici.glob("*.json")) if mesures_ici.is_dir() else []:
            fichiers.setdefault(fichier.stem, []).append(fichier)
    mesures = sorted(fichiers)
    partiel = False
    for tache in mesures:
        lues = [m for m in (lire_et_rafraichir(f) for f in fichiers[tache]) if m is not None]
        if not lues:
            continue
        mesure = fusionne_mesures(lues)
        budget = ""
        contrat = repo / "tasks" / tache / "contract.json"
        if not contrat.exists():  # rangée par fonctionnalité : tasks/<lot>/<ID>/ (#35)
            contrat = next(iter(sorted((repo / "tasks").glob(f"*/{tache}/contract.json"))), contrat)
        if contrat.exists():
            try:
                lu = json.loads(contrat.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                lu = None
            plafond = lu.get("budget_tokens") if isinstance(lu, dict) else None
            if isinstance(plafond, int) and not isinstance(plafond, bool):
                depasse = " DÉPASSÉ" if nombre(mesure.get("facturables")) > plafond else ""
                budget = f"  (budget {milliers(plafond)}{depasse})"
        # Sous Copilot, tant qu'une session n'est pas fermée, ses tokens manquent : le
        # total est un minimum (« ≥ »). Ce que Copilot facture, lui, est toujours là.
        minimum = "≥ " if mesure.get("detail_complet") is False else ""
        partiel = partiel or bool(minimum)
        copilot = f" · {facture_copilot(mesure)}" if "nano_aiu" in mesure else ""
        lignes.append(
            "  {:<14}{:<14}{:>12}{}{}".format(
                tache,
                str(mesure.get("source", "?")),
                minimum + milliers(nombre(mesure.get("facturables"))),
                budget,
                copilot,
            )
        )
    if partiel:
        lignes.append("  ≥ : un minimum, Copilot ne donne les tokens qu'en fermant la session")
    oc = vide("opencode")
    for dossier in dossiers:
        ici = opencode(base_opencode(), dossier.resolve())
        for champ in (*CHAMPS, "requetes"):
            oc[champ] += nombre(ici.get(champ))
    totalise(oc)
    if oc["requetes"]:
        lignes.append(
            "  {:<14}{:<14}{:>12}  ({} sessions, tout le dépôt)".format(
                "dépôt", "opencode", milliers(int(oc["facturables"])), oc["requetes"]
            )
        )
    if not mesures and not oc["requetes"]:
        lignes.append("  (rien de mesuré : pas de tâche en cours, ou pas de session enregistrée)")
    profils = [
        profil
        for dossier in dossiers
        for profil in sous_agents_claude(dossier)
        + sous_agents_opencode(base_opencode(), dossier.resolve())
    ]
    if profils:
        lignes.append("")
        lignes.append("sous-agents récents : durée, modèle et effort, sortie, appels d'outils")
        profils.sort(key=lambda p: p.get("debut") or 0, reverse=True)
        lignes += [ligne_sous_agent(profil) for profil in profils[:10]]
        if not all(profil.get("sortie_ecrite", True) for profil in profils[:10]):
            lignes.append(
                "  sortie non écrite : Claude Code n'écrit pas toujours la sortie de ses"
                " sous-agents dans leur journal (anthropics/claude-code#93620) ; leur coût est"
                " alors sous-estimé, leur durée et leurs appels restent justes"
            )
    return "\n".join(lignes)


def base_opencode() -> Path:
    racine = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(racine) / "opencode" / "opencode.db"


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(description=__doc__)
    sous = analyseur.add_subparsers(dest="commande", required=True)
    claude = sous.add_parser("claude", help="journal de session de Claude Code")
    claude.add_argument("journal", type=Path)
    claude.add_argument("--etat", type=Path, help="mesure précédente, pour ne relire que la suite")
    cop = sous.add_parser("copilot", help="journal events.jsonl d'une session Copilot")
    cop.add_argument("journal", type=Path)
    cop.add_argument("--etat", type=Path, help="mesure précédente, pour ne relire que la suite")
    raf = sous.add_parser("rafraichir", help="relit les journaux Copilot d'une mesure")
    raf.add_argument("--etat", type=Path, required=True)
    rem = sous.add_parser("remplissage", help="contexte occupé, journal de Claude Code")
    rem.add_argument("journal", type=Path)
    sous.add_parser("contexte", help="alerte de contexte : charge du hook sur l'entrée standard")
    oc = sous.add_parser("opencode", help="base SQLite d'opencode")
    oc.add_argument("--repo", type=Path, default=Path.cwd())
    oc.add_argument("--db", type=Path, default=None)
    rap = sous.add_parser("rapport", help="ce que les tâches ont coûté, toutes sources")
    rap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    rapa = sous.add_parser(
        "rapatrier", help="garde ici les jugements et mesures d'un dossier de tâche (task-clore)"
    )
    rapa.add_argument("source", type=Path)
    rapa.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    options = analyseur.parse_args(argv)
    if options.commande == "rapport":
        print(rapport(options.repo))
        return 0
    if options.commande == "rapatrier":
        faits = rapatrier(options.source, options.repo)
        if faits:
            print(f"gardé(s) dans {options.repo} : {', '.join(faits)}")
        return 0
    if options.commande == "rafraichir":
        lire_et_rafraichir(options.etat)
        return 0
    if options.commande == "remplissage":
        occupe = remplissage(options.journal)
        print(json.dumps({} if occupe is None else {"occupe": occupe}))
        return 0
    if options.commande == "contexte":
        try:
            charge = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            return 0
        if not isinstance(charge, dict):
            return 0
        message = alerte_contexte(charge, os.environ.get("EPHOR_CONTEXTE_TOKENS"))
        if message:
            print(json.dumps({"systemMessage": message}, ensure_ascii=False))
        return 0
    if options.commande in ("claude", "copilot"):
        etat = None
        if options.etat and options.etat.exists():
            try:
                lu = json.loads(options.etat.read_text(encoding="utf-8"))
            except ValueError:
                lu = None
            etat = lu if isinstance(lu, dict) else None
        lire = claude_code if options.commande == "claude" else copilot
        compte = lire(options.journal, etat)
    else:
        compte = opencode(options.db or base_opencode(), options.repo.resolve())
    print(json.dumps(compte, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
