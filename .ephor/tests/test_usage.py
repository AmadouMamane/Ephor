"""Mesure des tokens et plafond par tâche : compter juste, et s'arrêter quand c'est dit."""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

import usage
from conftest import ROOT, judge
from test_hooks import depot_de_tache, hook, sans_jq

REPONSE = {
    "type": "assistant",
    "requestId": "req_1",
    "message": {
        "id": "msg_1",
        "usage": {
            "input_tokens": 10,
            "output_tokens": 100,
            "cache_creation_input_tokens": 1000,
            "cache_read_input_tokens": 50000,
        },
    },
}


def journal(chemin: Path, entrees: list[dict[str, object]]) -> Path:
    chemin.write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entrees), encoding="utf-8"
    )
    return chemin


def test_une_requete_comptee_une_fois(tmp_path: Path) -> None:
    """Claude Code écrit plusieurs lignes par réponse : les compter toutes doublerait la note."""
    double = dict(REPONSE)
    fichier = journal(tmp_path / "session.jsonl", [REPONSE, double, {"type": "user"}])
    mesure = usage.claude_code(fichier)
    assert mesure["requetes"] == 1
    assert mesure["facturables"] == 10 + 1000 + 100, "les lectures de cache restent à part"
    assert mesure["cache_read"] == 50000


def bloc(requete: str, sortie: int, sorte: str = "text", **extra: object) -> dict[str, object]:
    """Une ligne d'une réponse de Claude Code : un bloc, et l'usage tel qu'il l'écrit."""
    usage_ = {"input_tokens": 10, "output_tokens": sortie, "cache_creation_input_tokens": 0}
    message = {"id": requete, "usage": usage_, "content": [{"type": sorte, **extra}]}
    return {"type": "assistant", "requestId": requete, "message": message}


def test_la_sortie_d_un_sous_agent_se_lit_a_son_dernier_bloc(tmp_path: Path) -> None:
    """Dans le journal d'un sous-agent, chaque bloc d'une réponse porte le compte de sortie du
    moment où il a été écrit ; seul le dernier est juste (anthropics/claude-code#93620). Le
    premier gardé, une session très déléguée était sous-estimée d'un facteur dix chez d'autres.
    Et les agents d'un « Workflow » écrivent sous subagents/workflows/<run>/."""
    principal = journal(tmp_path / "session.jsonl", [REPONSE])
    sous = tmp_path / "session" / "subagents"
    (sous / "workflows" / "run1").mkdir(parents=True)
    journal(sous / "agent-a.jsonl", [bloc("r1", 8, "thinking"), bloc("r1", 8), bloc("r1", 1200)])
    journal(sous / "workflows" / "run1" / "agent-b.jsonl", [bloc("r2", 300)])
    mesure = usage.claude_code(principal)
    assert mesure["output"] == 100 + 1200 + 300, mesure
    assert mesure["requetes"] == 3


def test_une_sortie_de_sous_agent_sans_compte_final_est_un_minimum_et_le_dit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mesuré le 01/10/2026 avec Claude Code 2.1.286 : dans le journal d'un sous-agent, 0 à 3
    réponses sur 48 à 67 avaient leur compte final ; les autres gardaient `stop_reason` nul et
    quelques dizaines de tokens de sortie. Le total, le plafond et le juge sous-estimaient la
    sortie d'environ 90 %, sans le dire, et `make usage` affichait « sortie 48 »."""

    def arret(ligne: dict[str, object], raison: str | None) -> dict[str, object]:
        message = ligne["message"]
        assert isinstance(message, dict)
        return {**ligne, "message": {**message, "stop_reason": raison}}

    principal = journal(tmp_path / "session.jsonl", [arret(REPONSE, "end_turn")])
    sous = tmp_path / "session" / "subagents"
    sous.mkdir(parents=True)
    blocs = [arret(bloc("r1", 40, "tool_use", name="Read", input={}), None)]
    blocs += [arret(bloc("r2", 60), None), arret(bloc("r2", 900), "tool_use")]
    blocs += [arret(bloc(f"r{i}", 48), None) for i in range(3, 6)]
    journal(sous / "agent-a.jsonl", blocs)
    mesure = usage.claude_code(principal)
    assert mesure["sans_final"] == 4, "r2 a fini par avoir son compte ; r1, r3, r4, r5 non"
    assert "au moins" in judge.usage_line(mesure, None)
    assert "4 réponse(s) de sous-agent" in judge.usage_line(mesure, None)
    # Reprise de la lecture : la réponse r1 reçoit son compte final plus tard.
    with (sous / "agent-a.jsonl").open("a", encoding="utf-8") as suite:
        suite.write(json.dumps(arret(bloc("r1", 700), "tool_use")) + "\n")
    assert usage.claude_code(principal, mesure)["sans_final"] == 3
    # Le profil du sous-agent ne montre plus un faux total.
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    depot = tmp_path / "depot"
    depot.mkdir()
    dossier = usage.dossier_claude(depot)
    (dossier / "s" / "subagents").mkdir(parents=True)
    journal(dossier / "s.jsonl", [REPONSE])
    journal(dossier / "s" / "subagents" / "agent-b.jsonl", blocs)
    (profil,) = usage.sous_agents_claude(depot)
    assert not profil["sortie_ecrite"] and profil["sans_final"] == 4
    assert "sortie non écrite par Claude Code (4 réponse(s) sur 5)" in usage.ligne_sous_agent(
        profil
    )
    # Un journal principal ordinaire (chaque réponse a son compte final) reste exact.
    assert "sans_final" not in usage.claude_code(
        journal(tmp_path / "seul.jsonl", [arret(REPONSE, "end_turn")])
    )


def test_la_mesure_reprend_ou_elle_s_etait_arretee(tmp_path: Path) -> None:
    fichier = journal(tmp_path / "session.jsonl", [REPONSE])
    premier = usage.claude_code(fichier)
    suite = dict(REPONSE, requestId="req_2")
    journal(fichier, [REPONSE, suite])
    second = usage.claude_code(fichier, premier)
    assert second["requetes"] == 2 and second["facturables"] == 2 * premier["facturables"]
    lus = second["journaux"][str(fichier)]["octets"]
    assert lus > premier["journaux"][str(fichier)]["octets"], "on ne relit pas ce qui est compté"


def test_une_tache_qui_traverse_deux_sessions_est_additionnee(tmp_path: Path) -> None:
    """Après un /clear, le journal change de nom : le total de la tâche doit survivre."""
    avant = journal(tmp_path / "session1.jsonl", [REPONSE])
    etat = usage.claude_code(avant)
    apres = journal(tmp_path / "session2.jsonl", [dict(REPONSE, requestId="req_2")])
    total = usage.claude_code(apres, etat)
    assert total["requetes"] == 2
    assert total["facturables"] == 2 * etat["facturables"]
    assert len(total["journaux"]) == 2


def test_un_journal_recommence_est_relu_du_debut(tmp_path: Path) -> None:
    fichier = journal(tmp_path / "session.jsonl", [REPONSE, dict(REPONSE, requestId="req_2")])
    avance = usage.claude_code(fichier)
    journal(fichier, [dict(REPONSE, requestId="req_3")])
    apres = usage.claude_code(fichier, avance)
    assert apres["requetes"] == 1, "journal plus court : on repart de zéro plutôt que de mentir"
    assert apres["facturables"] == 1110


def test_les_sessions_opencode_du_depot_sont_additionnees(tmp_path: Path) -> None:
    base = tmp_path / "opencode.db"
    connexion = sqlite3.connect(base)
    connexion.execute(
        "create table session (id text, directory text, path text, cost real,"
        " tokens_input int, tokens_output int, tokens_cache_write int, tokens_cache_read int)"
    )
    connexion.executemany(
        "insert into session values (?,?,?,?,?,?,?,?)",
        [
            ("s1", str(tmp_path), str(tmp_path), 0.25, 100, 20, 5, 900),
            ("s2", str(tmp_path), str(tmp_path), 0.25, 1, 2, 3, 4),
            ("s3", "/ailleurs", "/ailleurs", 9.0, 10**6, 10**6, 0, 0),
        ],
    )
    connexion.commit()
    connexion.close()
    mesure = usage.opencode(base, tmp_path)
    assert mesure["requetes"] == 2, "les sessions d'un autre dépôt ne comptent pas"
    assert mesure["facturables"] == (100 + 5 + 20) + (1 + 3 + 2)
    assert mesure["cout_dollars"] == 0.5


def test_le_profil_des_sous_agents_dit_ou_part_le_temps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Session réelle (30/09/2026, #34) : les tâches confiées à un sous-agent, le planner
    surtout, prenaient énormément de temps. Sans chiffres, on ne tranche pas : durée, modèle
    et effort, sortie, appels d'outils, fichiers lus, pour Claude Code et opencode."""
    depot = tmp_path / "depot"
    depot.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    dossier = usage.dossier_claude(depot)
    sous = dossier / "session" / "subagents"
    sous.mkdir(parents=True)
    journal(dossier / "session.jsonl", [REPONSE])

    def horodate(ligne: dict[str, object], moment: str) -> dict[str, object]:
        return {**ligne, "timestamp": f"2026-09-30T10:{moment}.000Z", "effort": "max"}

    lectures = [
        horodate(bloc("r1", 900, "tool_use", name="Read", input={"file_path": "a.py"}), "00:00"),
        horodate(bloc("r2", 700, "tool_use", name="Read", input={"file_path": "b.py"}), "00:40"),
        horodate(bloc("r3", 500, "tool_use", name="Grep", input={"pattern": "x"}), "01:30"),
    ]
    journal(sous / "agent-p.jsonl", lectures)
    (sous / "agent-p.meta.json").write_text('{"agentType": "planner", "description": "plan"}')
    partiel = [horodate(bloc("r4", 8, "thinking"), "02:00"), horodate(bloc("r5", 3), "02:10")]
    journal(sous / "agent-s.jsonl", partiel)
    (sous / "agent-s.meta.json").write_text('{"agentType": "scout"}')
    profils = {p["role"]: p for p in usage.sous_agents_claude(depot)}
    planner = profils["planner"]
    assert planner["secondes"] == 90 and planner["effort"] == "max"
    assert planner["outils"] == {"Read": 2, "Grep": 1} and planner["fichiers_lus"] == 2
    assert planner["sortie"] == 2100 and planner["sortie_ecrite"]
    assert "1 min 30 s" in usage.ligne_sous_agent(planner)
    assert "sortie non écrite" in usage.ligne_sous_agent(profils["scout"]), "3 et 8 tokens"

    base = tmp_path / "opencode.db"
    connexion = sqlite3.connect(base)
    connexion.execute(
        "create table session (id text, title text, parent_id text, directory text, path text,"
        " time_created int, tokens_output int, tokens_reasoning int)"
    )
    connexion.execute(
        "create table message (session_id text, time_created int, time_updated int, data text)"
    )
    connexion.execute("create table part (session_id text, data text)")
    connexion.execute(
        "insert into session values ('f', 'plan', 'p', ?, ?, 1, 4000, 30000)",
        (str(depot), str(depot)),
    )
    donnees = json.dumps({"agent": "planner", "model": {"modelID": "claude-opus"}})
    connexion.execute("insert into message values ('f', 1000, 181000, ?)", (donnees,))
    for outil, entree in (
        ("read", {"filePath": "a.py"}),
        ("read", {"filePath": "a.py"}),
        ("glob", {}),
    ):
        partie = {"type": "tool", "tool": outil, "state": {"input": entree}}
        connexion.execute("insert into part values ('f', ?)", (json.dumps(partie),))
    connexion.commit()
    connexion.close()
    (oc,) = usage.sous_agents_opencode(base, depot)
    assert oc["role"] == "planner" and oc["secondes"] == 180 and oc["reflexion"] == 30000
    assert oc["outils"] == {"read": 2, "glob": 1} and oc["fichiers_lus"] == 1


def test_une_base_inconnue_le_dit_au_lieu_de_zero(tmp_path: Path) -> None:
    base = tmp_path / "opencode.db"
    sqlite3.connect(base).execute("create table autre (x int)").connection.close()
    assert "schéma inconnu" in usage.opencode(base, tmp_path)["source"]


def test_le_plafond_pose_le_bouton_d_arret_avec_sa_raison(tmp_path: Path) -> None:
    repo = depot_de_tache(tmp_path, ["src/"])
    contrat = repo / "tasks" / "T-9" / "contract.json"
    contrat.write_text(json.dumps({"allowed_paths": ["src/"], "budget_tokens": 500}))
    fichier = journal(tmp_path / "session.jsonl", [REPONSE])  # 1110 facturables
    (repo / ".ephor").mkdir()
    (repo / ".ephor" / "usage.py").write_text(
        (ROOT / ".ephor" / "usage.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    resultat = hook("budget.sh", {"transcript_path": str(fichier)}, project=repo)
    assert resultat.returncode == 0, "un hook Stop ne bloque jamais la fin du tour"
    arret = (repo / "AGENT_STOP").read_text(encoding="utf-8")
    assert "budget de la tâche T-9 dépassé" in arret and "1110" in arret
    mesure = json.loads((repo / ".verify" / "usage" / "T-9.json").read_text(encoding="utf-8"))
    assert mesure["facturables"] == 1110

    refus = hook("kill-switch.sh", {}, project=repo)
    assert refus.returncode == 2 and "budget de la tâche T-9 dépassé" in refus.stderr


def test_sans_plafond_le_hook_mesure_sans_arreter(tmp_path: Path) -> None:
    repo = depot_de_tache(tmp_path, ["src/"])
    fichier = journal(tmp_path / "session.jsonl", [REPONSE])
    (repo / ".ephor").mkdir()
    (repo / ".ephor" / "usage.py").write_text(
        (ROOT / ".ephor" / "usage.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    assert hook("budget.sh", {"transcript_path": str(fichier)}, project=repo).returncode == 0
    assert not (repo / "AGENT_STOP").exists(), "pas de budget au contrat : rien à faire respecter"
    assert (repo / ".verify" / "usage" / "T-9.json").exists(), "on mesure quand même"


def test_le_rapport_tient_en_quelques_lignes(tmp_path: Path) -> None:
    repo = depot_de_tache(tmp_path, ["src/"])
    (repo / "tasks" / "T-9" / "contract.json").write_text(
        json.dumps({"allowed_paths": ["src/"], "budget_tokens": 500})
    )
    dossier = repo / ".verify" / "usage"
    dossier.mkdir(parents=True)
    (dossier / "T-9.json").write_text(json.dumps({"facturables": 1110, "source": "claude-code"}))
    texte = usage.rapport(repo)
    assert "T-9" in texte and "1 110" in texte and "DÉPASSÉ" in texte
    assert len(texte.splitlines()) <= 6, "un rapport qu'on lit d'un coup d'œil"


def test_la_ligne_de_commande_rend_du_json(tmp_path: Path) -> None:
    fichier = journal(tmp_path / "session.jsonl", [REPONSE])
    sortie = subprocess.run(
        ["python3", str(ROOT / ".ephor" / "usage.py"), "claude", str(fichier)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(sortie.stdout)["facturables"] == 1110


# Copilot : la forme de `events.jsonl`, relevée le 24/09/2026 dans la vue « agents » de VS Code.
UNE_REPONSE = {"type": "assistant.message", "data": {"model": "m", "content": ""}}


def point(aiu: int, premium: int) -> dict[str, object]:
    """Ce que Copilot écrit après chaque demande : le facturé depuis le début, pas de tokens."""
    return {
        "type": "session.usage_checkpoint",
        "data": {"totalNanoAiu": aiu, "totalPremiumRequests": premium},
    }


def fermeture(aiu: int, premium: int, **jetons: int) -> dict[str, object]:
    """Ce que Copilot écrit en fermant la session : les tokens, depuis la dernière reprise."""
    detail = {cle: {"tokenCount": n} for cle, n in jetons.items()}
    return {
        "type": "session.shutdown",
        "data": {"totalNanoAiu": aiu, "totalPremiumRequests": premium, "tokenDetails": detail},
    }


def session_copilot(dossier: Path, entrees: list[dict[str, object]], nom: str = "abc") -> Path:
    session = dossier / ".copilot" / "session-state" / nom
    session.mkdir(parents=True, exist_ok=True)
    return journal(session / "events.jsonl", entrees)


def depot_avec_compteur(tmp_path: Path, budget: int | None = None) -> Path:
    repo = depot_de_tache(tmp_path, ["src/"])
    if budget is not None:
        (repo / "tasks" / "T-9" / "contract.json").write_text(
            json.dumps({"allowed_paths": ["src/"], "budget_tokens": budget})
        )
    (repo / ".ephor").mkdir()
    (repo / ".ephor" / "usage.py").write_text(
        (ROOT / ".ephor" / "usage.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return repo


def mesure_de(repo: Path) -> dict[str, object]:
    return json.loads((repo / ".verify" / "usage" / "T-9.json").read_text(encoding="utf-8"))


def test_une_session_copilot_ouverte_dit_ce_qu_elle_facture_et_que_les_tokens_manquent(
    tmp_path: Path,
) -> None:
    fichier = session_copilot(tmp_path, [UNE_REPONSE, UNE_REPONSE, point(700_000_000, 1)])
    mesure = usage.copilot(fichier)
    assert mesure["requetes"] == 2, "une réponse du modèle = une requête facturée"
    assert mesure["nano_aiu"] == 700_000_000 and mesure["requetes_premium"] == 1
    assert mesure["facturables"] == 0
    assert mesure["detail_complet"] is False, "0 token alors que Copilot facture : un minimum"


def test_une_session_copilot_fermee_donne_ses_tokens(tmp_path: Path) -> None:
    fichier = session_copilot(
        tmp_path,
        [
            UNE_REPONSE,
            point(700_000_000, 1),
            UNE_REPONSE,
            point(800_000_000, 2),
            fermeture(800_000_000, 2, input=24, cache_write=27241, output=902, cache_read=172368),
        ],
    )
    mesure = usage.copilot(fichier)
    assert mesure["facturables"] == 24 + 27241 + 902, "les lectures de cache restent à part"
    assert mesure["cache_read"] == 172368
    assert mesure["detail_complet"] is True
    assert mesure["requetes"] == 2 and mesure["requetes_premium"] == 2


def test_une_portion_fermee_sans_detail_laisse_un_minimum(tmp_path: Path) -> None:
    """Forme réelle : une reprise après un arrêt brutal écrit une fermeture vide pour la
    portion d'avant. Ses tokens sont perdus ; seuls ceux d'après la reprise sont connus."""
    fichier = session_copilot(
        tmp_path,
        [
            UNE_REPONSE,
            point(2_000_000_000, 12),
            fermeture(2_000_000_000, 12),
            {"type": "session.resume", "data": {}},
            UNE_REPONSE,
            point(3_000_000_000, 13),
            fermeture(3_000_000_000, 13, input=9, cache_write=32163, output=232, cache_read=63963),
        ],
    )
    mesure = usage.copilot(fichier)
    assert mesure["facturables"] == 9 + 32163 + 232
    assert mesure["nano_aiu"] == 3_000_000_000, "le facturé est cumulé à travers les reprises"
    assert mesure["detail_complet"] is False


def test_la_relecture_d_un_journal_copilot_ne_compte_rien_deux_fois(tmp_path: Path) -> None:
    """Pas d'identifiant de requête à dédoublonner : tout tient à la position de reprise."""
    fichier = session_copilot(tmp_path, [UNE_REPONSE, point(1, 1)])
    premier = usage.copilot(fichier)
    with fichier.open("a", encoding="utf-8") as suite:
        suite.write(json.dumps(UNE_REPONSE) + "\n" + json.dumps(point(2, 2)) + "\n")
        suite.write(json.dumps(UNE_REPONSE))  # ligne en cours d'écriture
    second = usage.copilot(fichier, premier)
    assert second["requetes"] == 2 and second["requetes_premium"] == 2
    with fichier.open("a", encoding="utf-8") as suite:
        suite.write("\n")
    troisieme = usage.copilot(fichier, second)
    assert troisieme["requetes"] == 3, "la ligne achevée est lue, une seule fois"


def test_une_tache_passee_de_claude_code_a_copilot_additionne_les_deux(tmp_path: Path) -> None:
    claude = usage.claude_code(journal(tmp_path / "session.jsonl", [REPONSE]))
    fichier = session_copilot(tmp_path, [UNE_REPONSE, fermeture(5, 1, input=1, output=2)])
    total = usage.copilot(fichier, claude)
    assert total["facturables"] == 1110 + 3
    assert total["requetes"] == 2
    assert total["source"] == "claude-code + copilot"
    ancien = {"source": "claude-code", "journaux": {"vieux.jsonl": {"input": 7, "requetes": 1}}}
    assert usage.copilot(fichier, ancien)["facturables"] == 7 + 3, "une mesure d'avant survit"


def test_le_hook_mesure_le_journal_de_copilot_sans_le_dire_complet(tmp_path: Path) -> None:
    """La vue « agents » de VS Code déclenche la fin de tour avec son propre journal. Lu
    comme celui de Claude Code, il donnait 0 token. Il a maintenant son lecteur, et une
    session encore ouverte n'est jamais présentée comme une mesure complète."""
    repo = depot_avec_compteur(tmp_path)
    fichier = session_copilot(tmp_path, [UNE_REPONSE, point(610_020_000, 1)])
    assert hook("budget.sh", {"transcript_path": str(fichier)}, project=repo).returncode == 0
    mesure = mesure_de(repo)
    assert mesure["source"] == "copilot" and mesure["requetes"] == 1
    assert mesure["nano_aiu"] == 610_020_000 and mesure["detail_complet"] is False


@pytest.mark.parametrize("sans", [False, True], ids=["jq", "python3"])
def test_le_hook_lit_aussi_la_forme_du_greffon(tmp_path: Path, sans: bool) -> None:
    """Le greffon relaie la charge telle que Copilot l'écrit : `transcriptPath`."""
    repo = depot_avec_compteur(tmp_path)
    fichier = session_copilot(tmp_path, [UNE_REPONSE, point(1, 1)])
    chemin = None
    if sans:
        chemin = sans_jq(tmp_path)
        (Path(chemin) / "mkdir").symlink_to(str(shutil.which("mkdir")))  # sans jq, pas sans mkdir
    resultat = hook("budget.sh", {"transcriptPath": str(fichier)}, project=repo, path=chemin)
    assert resultat.returncode == 0
    assert mesure_de(repo)["requetes"] == 1


def test_un_minimum_depasse_suffit_a_arreter(tmp_path: Path) -> None:
    repo = depot_avec_compteur(tmp_path, budget=500)
    fichier = session_copilot(
        tmp_path, [UNE_REPONSE, fermeture(9, 1, input=600), UNE_REPONSE, point(20, 2)]
    )
    hook("budget.sh", {"transcript_path": str(fichier)}, project=repo)
    assert "budget de la tâche T-9 dépassé" in (repo / "AGENT_STOP").read_text(encoding="utf-8")


def test_des_tokens_inconnus_n_arretent_pas(tmp_path: Path) -> None:
    repo = depot_avec_compteur(tmp_path, budget=500)
    fichier = session_copilot(tmp_path, [UNE_REPONSE, point(10**12, 50)])
    hook("budget.sh", {"transcript_path": str(fichier)}, project=repo)
    assert not (repo / "AGENT_STOP").exists(), "rien ne prouve le dépassement : on n'arrête pas"


def test_le_rapport_rattrape_une_fermeture_tardive(tmp_path: Path) -> None:
    """VS Code ferme une session quand il en ouvre une autre : le hook de la première ne
    tourne plus. Le rapport (et `make judge`) relit ses journaux et rattrape le détail."""
    repo = depot_avec_compteur(tmp_path)
    fichier = session_copilot(tmp_path, [UNE_REPONSE, point(3_560_000_000, 13)])
    hook("budget.sh", {"transcript_path": str(fichier)}, project=repo)
    avant = usage.rapport(repo)
    assert "≥ 0" in avant and "3,56 AIU" in avant and "13 requêtes premium" in avant
    assert "minimum" in avant, "le « ≥ » s'explique"
    with fichier.open("a", encoding="utf-8") as suite:
        suite.write(json.dumps(fermeture(3_560_000_000, 13, input=10, output=100)) + "\n")
    subprocess.run(
        [
            "python3",
            str(ROOT / ".ephor" / "usage.py"),
            "rafraichir",
            "--etat",
            str(repo / ".verify" / "usage" / "T-9.json"),
        ],
        check=True,
    )
    assert mesure_de(repo)["detail_complet"] is True, "la mesure du juge est à jour"
    apres = usage.rapport(repo)
    assert "≥" not in apres and "110" in apres


def session_avec_sous_agents(dossier: Path, *nombres: int) -> Path:
    """Un journal principal (une requête) et, à côté, un journal par sous-agent, comme
    Claude Code les range : `<session>/subagents/agent-*.jsonl`."""
    principal = journal(dossier / "s1.jsonl", [REPONSE])
    rangement = dossier / "s1" / "subagents"
    rangement.mkdir(parents=True)
    for rang, requetes in enumerate(nombres):
        journal(
            rangement / f"agent-{rang}.jsonl",
            [dict(REPONSE, requestId=f"sous_{rang}_{i}") for i in range(requetes)],
        )
    return principal


def test_les_sous_agents_comptent_dans_la_tache(tmp_path: Path) -> None:
    """Mesuré le 25/09/2026 : un tiers d'une vraie session était dépensé par des sous-agents,
    dans des journaux qu'Ephor ne lisait pas. AGENTS.md demande justement de leur déléguer."""
    principal = session_avec_sous_agents(tmp_path, 2, 1)
    mesure = usage.claude_code(principal)
    assert mesure["requetes"] == 4
    assert mesure["facturables"] == 4 * 1110
    assert mesure["sous_agents"] == 2
    # Relu plus tard, rien n'est compté deux fois ; un nouveau sous-agent s'ajoute.
    journal(
        principal.with_suffix("") / "subagents" / "agent-9.jsonl", [dict(REPONSE, requestId="n")]
    )
    suite = usage.claude_code(principal, mesure)
    assert suite["requetes"] == 5 and suite["sous_agents"] == 3


def test_le_plafond_voit_les_sous_agents(tmp_path: Path) -> None:
    repo = depot_de_tache(tmp_path, ["src/"])
    contrat = repo / "tasks" / "T-9" / "contract.json"
    contrat.write_text(json.dumps({"allowed_paths": ["src/"], "budget_tokens": 2000}))
    (repo / ".ephor").mkdir()
    shutil.copy(ROOT / ".ephor" / "usage.py", repo / ".ephor" / "usage.py")
    principal = session_avec_sous_agents(tmp_path, 1)  # 1110 + 1110 : le principal seul passe
    assert hook("budget.sh", {"transcript_path": str(principal)}, project=repo).returncode == 0
    assert "2220" in (repo / "AGENT_STOP").read_text(encoding="utf-8")


def test_le_juge_rattrape_un_sous_agent_ecrit_apres_le_tour(tmp_path: Path) -> None:
    principal = session_avec_sous_agents(tmp_path)
    etat = tmp_path / "T-9.json"
    etat.write_text(json.dumps(usage.claude_code(principal)), encoding="utf-8")
    journal(
        principal.with_suffix("") / "subagents" / "agent-0.jsonl", [dict(REPONSE, requestId="x")]
    )
    assert usage.main(["rafraichir", "--etat", str(etat)]) == 0
    mesure = json.loads(etat.read_text(encoding="utf-8"))
    assert mesure["requetes"] == 2 and mesure["sous_agents"] == 1


def test_un_compteur_nul_ou_une_ligne_etrange_n_eteint_pas_la_mesure(tmp_path: Path) -> None:
    """Un `null` dans un compteur faisait tomber toute la lecture, à chaque tour : plus de
    mesure, donc plus de plafond, et rien ne le disait (vu le 25/09/2026)."""
    nul = {
        "requestId": "req_nul",
        "message": {"id": "m", "usage": {"input_tokens": 7, "cache_creation_input_tokens": None}},
    }
    fichier = tmp_path / "session.jsonl"
    fichier.write_text(
        "[1, 2]\n"
        + json.dumps({"requestId": "x", "message": "texte"})
        + "\n"
        + json.dumps(nul)
        + "\n"
        + json.dumps(REPONSE)
        + "\n",
        encoding="utf-8",
    )
    mesure = usage.claude_code(fichier)
    assert mesure["requetes"] == 2
    assert mesure["facturables"] == 7 + 1110


def test_la_mesure_de_la_tache_survit_a_un_budget_mal_ecrit(tmp_path: Path) -> None:
    (tmp_path / ".verify" / "usage").mkdir(parents=True)
    (tmp_path / ".verify" / "usage" / "T-1.json").write_text(
        json.dumps(usage.claude_code(journal(tmp_path / "s.jsonl", [REPONSE])))
    )
    (tmp_path / "tasks" / "T-1").mkdir(parents=True)
    (tmp_path / "tasks" / "T-1" / "contract.json").write_text("[1]")
    assert "T-1" in usage.rapport(tmp_path)


@pytest.mark.parametrize("abimee", ["pas une base", None])
def test_une_base_opencode_illisible_le_dit_sans_tomber(tmp_path: Path, abimee: str | None) -> None:
    base = tmp_path / "opencode?x.db"
    if abimee is None:
        base.mkdir()
    else:
        base.write_text(abimee)
    assert "opencode (" in usage.opencode(base, tmp_path)["source"]


# ------------------------------------------------------------------ alerte de contexte


def test_le_remplissage_est_celui_de_la_derniere_reponse_principale(tmp_path: Path) -> None:
    """Toute la conversation repart à chaque requête : la dernière réponse dit ce qui est en
    mémoire. Une réponse de sous-agent n'est pas la mémoire de la conversation principale."""
    ancienne = dict(REPONSE, requestId="req_0")
    delegue = {
        **REPONSE,
        "requestId": "req_x",
        "isSidechain": True,
        "message": {"id": "m", "usage": {"input_tokens": 999_999}},
    }
    fichier = journal(tmp_path / "session.jsonl", [ancienne, REPONSE, delegue, {"type": "user"}])
    assert usage.remplissage(fichier) == 10 + 100 + 1000 + 50000
    assert usage.remplissage(tmp_path / "absent.jsonl") is None


def test_l_alerte_parle_a_60_pour_cent_et_se_tait_avant() -> None:
    assert usage.alerte_contexte({"contexte": {"occupe": 599_999, "taille": 1_000_000}}) is None
    message = usage.alerte_contexte({"contexte": {"occupe": 614_000, "taille": 1_000_000}})
    assert message is not None
    assert "61 %" in message and "/handoff" in message and "supposée" not in message


def test_sous_claude_code_la_taille_est_supposee_ou_declaree(tmp_path: Path) -> None:
    """Les hooks de Claude Code ne reçoivent pas la taille de la mémoire : on le dit."""
    gros = {**REPONSE, "message": {"id": "m", "usage": {"input_tokens": 130_000}}}
    charge = {"transcript_path": str(journal(tmp_path / "s.jsonl", [gros]))}
    supposee = usage.alerte_contexte(charge)
    assert supposee is not None and "65 %" in supposee and "EPHOR_CONTEXTE_TOKENS" in supposee
    assert usage.alerte_contexte(charge, "1000000") is None, "déclarée à un million : 13 %"
    enorme = {**REPONSE, "message": {"id": "m", "usage": {"input_tokens": 300_000}}}
    charge = {"transcript_path": str(journal(tmp_path / "t.jsonl", [enorme]))}
    assert usage.alerte_contexte(charge) is None, "plus de 200 000 : la mémoire fait un million"


def test_le_hook_de_contexte_parle_a_l_operateur_sans_bloquer(tmp_path: Path) -> None:
    gros = {**REPONSE, "message": {"id": "m", "usage": {"input_tokens": 150_000}}}
    fichier = journal(tmp_path / "session.jsonl", [gros])
    resultat = hook("contexte.sh", {"transcript_path": str(fichier)})
    assert resultat.returncode == 0, "un hook Stop ne bloque jamais la fin du tour"
    assert "75 %" in json.loads(resultat.stdout)["systemMessage"]
    calme = hook("contexte.sh", {"contexte": {"occupe": 10, "taille": 1_000_000}})
    assert calme.returncode == 0 and calme.stdout == ""
    abime = hook("contexte.sh", {"transcript_path": 3})
    assert abime.returncode == 0 and abime.stdout == ""
