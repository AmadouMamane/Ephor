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
import sqlite3
import sys
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
    dossier = journal.with_suffix("") / "subagents"
    return sorted(dossier.glob("*.jsonl")) if dossier.is_dir() else []


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
            if not identifiant or identifiant in vues:
                continue
            vues.add(identifiant)
            ordre.append(identifiant)
            sous["requetes"] += 1
            sous["input"] += nombre(usage.get("input_tokens"))
            sous["output"] += nombre(usage.get("output_tokens"))
            sous["cache_write"] += nombre(usage.get("cache_creation_input_tokens"))
            sous["cache_read"] += nombre(usage.get("cache_read_input_tokens"))
        sous["octets"] = position
    sous["vues"] = ordre[-200:]  # de quoi dédoublonner à la frontière, pas un historique
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
    dossier = repo / ".verify" / "usage"
    mesures = sorted(dossier.glob("*.json")) if dossier.is_dir() else []
    partiel = False
    for fichier in mesures:
        mesure = lire_et_rafraichir(fichier)
        if mesure is None:
            continue
        budget = ""
        contrat = repo / "tasks" / fichier.stem / "contract.json"
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
                fichier.stem,
                str(mesure.get("source", "?")),
                minimum + milliers(nombre(mesure.get("facturables"))),
                budget,
                copilot,
            )
        )
    if partiel:
        lignes.append("  ≥ : un minimum, Copilot ne donne les tokens qu'en fermant la session")
    oc = opencode(base_opencode(), repo.resolve())
    if oc["requetes"]:
        lignes.append(
            "  {:<14}{:<14}{:>12}  ({} sessions, tout le dépôt)".format(
                "dépôt", "opencode", milliers(int(oc["facturables"])), oc["requetes"]
            )
        )
    if not mesures and not oc["requetes"]:
        lignes.append("  (rien de mesuré : pas de tâche en cours, ou pas de session enregistrée)")
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
    options = analyseur.parse_args(argv)
    if options.commande == "rapport":
        print(rapport(options.repo))
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
