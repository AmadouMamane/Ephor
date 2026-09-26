"""Adaptateurs : ce que les autres outils reçoivent, et ce qui doit rester en phase."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import adapters
from conftest import ROOT, git
from test_hooks import depot_de_tache, sans_jq

REGLAGES: dict[str, Any] = {
    "permissions": {
        "allow": ["Bash(make *)", "Bash(git status *)"],
        "ask": ["Bash(git push *)"],
        "deny": [
            "Bash(make task-new *)",
            "Edit(.ephor/**)",
            "Read(**/*.pem)",
            "Read(~/.ssh/**)",
            "WebFetch",
        ],
    }
}
MOTEUR = shutil.which("bun") or shutil.which("node")
PILOTE = """
import { writeFileSync, appendFileSync } from "node:fs"
import { join } from "node:path"
const racine = "__RACINE__"
const module = await import("__GREFFON__")
const hooks = await module.ephor({ worktree: racine })
const resultat = {}
async function essaie(nom, appel) {
  try { await appel(); resultat[nom] = "ok" }
  catch (erreur) { resultat[nom] = String(erreur.message) }
}
await essaie("propre", () =>
  hooks["tool.execute.before"]({ tool: "write" },
    { args: { filePath: "src/a.py", content: "x = 1" } }))
await essaie("secret", () =>
  hooks["tool.execute.before"]({ tool: "write" },
    { args: { filePath: "src/a.py", content: 'API_KEY="sk-abcdefghijklmnopqrstuvwx"' } }))
await essaie("perimetre", () =>
  hooks["tool.execute.before"]({ tool: "write" },
    { args: { filePath: "docs/ailleurs.md", content: "hors sujet" } }))
appendFileSync(join(racine, "STEER.md"), "\\nNe touche plus à service.py\\n")
const sortie = { system: [] }
await hooks["experimental.chat.system.transform"]({}, sortie)
resultat.consigne = sortie.system.join("\\n")
await essaie("terminal_zone", () =>
  hooks["tool.execute.before"]({ tool: "bash" },
    { args: { command: "echo x > .ephor/judge.py" } }))
await essaie("terminal_propre", () =>
  hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "ls src" } }))
await essaie("zone_moteur", () =>
  hooks["tool.execute.before"]({ tool: "write" },
    { args: { filePath: join(racine, "GNUmakefile"), content: "judge:" } }))
writeFileSync(join(racine, "AGENT_STOP"), "")
await essaie("arret", () => hooks["tool.execute.before"]({ tool: "read" }, { args: {} }))
await essaie("arret_progres", () =>
  hooks["tool.execute.before"]({ tool: "write" },
    { args: { filePath: join(racine, "PROGRESS.md"), content: "## Bloqué" } }))
console.log(JSON.stringify(resultat))
"""


def test_les_fichiers_generes_sont_a_jour() -> None:
    """LE test : si `.claude/` bouge sans `make adapters`, la CI le dit avant la fusion."""
    assert adapters.verifier() == 0


def test_la_severite_de_claude_devient_un_ordre_chez_opencode() -> None:
    """Claude tranche par sévérité, opencode par ordre : le deny doit finir dernier."""
    regles = adapters.permissions(REGLAGES)
    bash = list(regles["bash"])
    assert bash[0] == "*" and regles["bash"]["*"] == "ask"
    assert bash.index("make task-new *") > bash.index("make *")
    assert regles["bash"]["make task-new *"] == "deny"
    assert regles["bash"]["git push *"] == "ask"
    assert regles["edit"]["*"] == "allow" and regles["edit"][".ephor/**"] == "deny"
    assert regles["read"] == {"*.pem": "deny"}
    assert regles["external_directory"] == {"~/.ssh/**": "deny"}
    assert regles["webfetch"] == "deny"


def test_les_zones_de_controle_sont_refusees_aux_trois_outils() -> None:
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    opencode = json.loads((ROOT / ".opencode" / "opencode.json").read_text(encoding="utf-8"))
    vscode = json.loads(
        adapters.sans_commentaires((ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    )
    for zone in (".ephor/**", "tasks/**", "Makefile", "AGENTS.md", ".claude/**"):
        assert f"Edit({zone})" in reglages["permissions"]["deny"]
        assert opencode["permission"]["edit"][zone] == "deny"
        # Rien pour VS Code, et c'est le refus qui tient : une entrée ici ferait
        # DEMANDER, et dans la vue « agents » la question devient un bouton qui lève
        # le refus du garde-fou. Voir test_la_liste_des_editions_ne_pose_aucune_question.
        assert "**/" + zone not in vscode["chat.tools.edits.autoApprove"]


def test_un_sous_agent_en_mode_plan_ne_peut_que_lire() -> None:
    meta, _ = adapters.entete(ROOT / ".claude" / "agents" / "evaluator.md")
    regles = adapters.permissions_agent(meta)
    assert regles["edit"] == "deny"
    assert regles["bash"]["*"] == "deny"
    assert regles["bash"]["git diff *"] == "allow"
    meta, _ = adapters.entete(ROOT / ".claude" / "agents" / "planner.md")
    assert adapters.permissions_agent(meta)["bash"] == "deny", "sans Bash, rien à lancer"


def test_les_blocs_shell_des_skills_deviennent_des_injections_opencode() -> None:
    texte = (ROOT / ".opencode" / "commands" / "verify.md").read_text(encoding="utf-8")
    assert "!`make verify || true`" in texte
    assert "```!" not in texte, "opencode ne connaît pas les blocs exécutables de Claude"


def test_les_expressions_de_vs_code_sont_valides_et_echappees() -> None:
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    motifs = adapters.reglages_vscode(reglages)["chat.tools.terminal.autoApprove"]
    assert isinstance(motifs, dict)
    for motif in motifs:
        assert motif.startswith("/") and motif.endswith("/"), motif
        corps = motif[1:-1]
        assert "/" not in corps.replace("\\/", ""), f"barre oblique non échappée : {motif}"
        re.compile(corps.replace("\\/", "/"))
    assert motifs["/^rm\\b/"] is False and motifs["/^make\\b/"] is True
    for commande, attendu in (("fd .", True), ("fdisk -l", False), ("rm -rf build", False)):
        approuve = [
            valeur
            for cle, valeur in motifs.items()
            if re.match(cle[1:-1].replace("\\/", "/"), commande)
        ]
        assert (approuve == [True]) is attendu, f"{commande} : {approuve}"


def test_les_gardes_de_copilot_pointent_sur_des_scripts_qui_existent() -> None:
    fichier = json.loads((ROOT / ".github" / "hooks" / "ephor.json").read_text(encoding="utf-8"))
    commandes = [hook["command"] for groupe in fichier["hooks"].values() for hook in groupe]
    assert commandes, "aucun garde-fou branché"
    for commande in commandes:
        assert os.access(ROOT / commande.split(" ", 1)[1], os.X_OK), commande
    assert not any("filter-output" in commande for commande in commandes), (
        "Copilot ne réécrit pas l'appel d'outil : ce hook n'a rien à y faire"
    )


def test_le_lecteur_d_entetes_refuse_ce_qu_il_ne_sait_pas_lire(tmp_path: Path) -> None:
    fichier = tmp_path / "SKILL.md"
    fichier.write_text("---\nname: x\nmetadata:\n  a: 1\n---\n\ncorps\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non supportée"):
        adapters.entete(fichier)


def test_les_reglages_vs_code_du_projet_sont_conserves() -> None:
    existant = (
        '{\n  // une préférence d\'équipe\n  "python.analysis.typeCheckingMode": "strict",\n}'
    )
    fusion = json.loads(adapters.sans_commentaires(adapters.fichier_vscode(REGLAGES, existant)))
    assert fusion["python.analysis.typeCheckingMode"] == "strict", "virgule finale tolérée"
    assert fusion["chat.useClaudeHooks"] is True


def test_un_acces_reseau_restreint_ne_devient_pas_un_acces_general() -> None:
    """Claude sait limiter WebFetch à un domaine ; opencode non. On rétrograde en question."""
    regles = adapters.permissions({"permissions": {"allow": ["WebFetch(domain:docs.python.org)"]}})
    assert regles["webfetch"] == "ask"
    general = adapters.permissions({"permissions": {"allow": ["WebFetch"]}})
    assert general["webfetch"] == "allow"


def test_un_sous_agent_sans_liste_d_outils_garde_le_droit_d_ecrire() -> None:
    """Sans `tools:`, Claude Code donne tout : le sous-agent généré doit en faire autant."""
    assert adapters.permissions_agent({"description": "x"}) == {}
    assert adapters.permissions_agent({"description": "x", "tools": "Read Grep"})["edit"] == "deny"


def test_seul_un_fichier_dont_la_source_a_disparu_est_supprime(tmp_path: Path) -> None:
    """Une copie faite à la main porte la même marque : elle ne doit pas être effacée."""
    attendus = adapters.fichiers()
    copie = ROOT / ".opencode" / "agents" / "essai-copie.md"
    orphelin = ROOT / ".opencode" / "agents" / "essai-orphelin.md"
    copie.write_text(f"---\n# {adapters.MARQUE} .claude/agents/scout.md\n---\n\ncorps\n")
    orphelin.write_text(f"---\n# {adapters.MARQUE} .claude/agents/disparu.md\n---\n\ncorps\n")
    try:
        assert list(adapters.perimes(attendus)) == [".opencode/agents/essai-orphelin.md"]
    finally:
        copie.unlink()
        orphelin.unlink()


@pytest.mark.skipif(
    MOTEUR is None or shutil.which("jq") is None,
    reason="greffon opencode : bun ou node, et jq, sont nécessaires",
)
def test_le_greffon_opencode_applique_les_memes_gardes(tmp_path: Path) -> None:
    """Le greffon n'a pas de logique propre : il doit rendre le verdict des hooks bash."""
    projet = depot_de_tache(tmp_path, ["src/"])
    shutil.copytree(ROOT / ".ephor" / "hooks", projet / ".ephor" / "hooks")
    shutil.copy(ROOT / ".ephor" / "zones.txt", projet / ".ephor" / "zones.txt")
    (projet / "STEER.md").write_text((ROOT / "STEER.md").read_text(encoding="utf-8"))
    (projet / ".claude").mkdir()
    shutil.copy(ROOT / ".claude" / "settings.json", projet / ".claude" / "settings.json")
    pilote = tmp_path / "pilote.mjs"
    pilote.write_text(
        PILOTE.replace("__RACINE__", str(projet)).replace(
            "__GREFFON__", str(ROOT / ".opencode" / "plugins" / "ephor.js")
        ),
        encoding="utf-8",
    )
    assert MOTEUR is not None
    sortie = subprocess.run(
        [MOTEUR, str(pilote)], capture_output=True, text=True, timeout=120, check=True
    )
    resultat = json.loads(sortie.stdout)
    assert resultat["propre"] == "ok", "du code ordinaire doit passer"
    assert "REFUSÉ" in resultat["secret"]
    assert "hors du périmètre" in resultat["perimetre"]
    assert "AGENT_STOP" in resultat["arret"]
    # Sous arrêt, l'agent doit pouvoir noter son état : le bouton recevait l'appel sans charge,
    # et refusait donc aussi PROGRESS.md, contrairement à ce que dit AGENTS.md.
    assert resultat["arret_progres"] == "ok"
    assert "zone de contrôle" in resultat["zone_moteur"], "la liste du moteur vaut sous opencode"
    assert "service.py" in resultat["consigne"]
    assert "zone de contrôle" in resultat["terminal_zone"], "le terminal ne contourne pas l'édition"
    assert resultat["terminal_propre"] == "ok"


# ------------------------------------------------------- greffon Copilot (par machine)
#
# Copilot n'exécute PAS les hooks d'un dossier de travail : mesuré le 23 septembre 2026,
# le fichier est trouvé, listé, jamais appelé. Seul un greffon installé dans
# `~/.copilot/installed-plugins/` s'exécute. Le greffon ne contient aucune règle : il
# rejoue la charge vers les garde-fous du dépôt.

GREFFON = ROOT / ".ephor" / "greffon-copilot"


def test_le_greffon_copilot_appelle_les_gardes_du_depot() -> None:
    manifeste = json.loads((GREFFON / ".claude-plugin" / "plugin.json").read_text())
    assert manifeste["name"] == "ephor"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifeste["version"]), manifeste["version"]

    fichier = json.loads((GREFFON / "hooks" / "hooks.json").read_text())
    assert set(fichier["hooks"]) == {"PreToolUse", "UserPromptSubmit", "Stop"}
    for evenement, entrees in fichier["hooks"].items():
        assert len(entrees) == 1, f"{evenement} : un seul appel de relais, qui enchaîne"
        commande = entrees[0]["command"]
        assert adapters.JETON_GREFFON in commande, "le chemin n'existe qu'à l'installation"
        gardes = commande.split('relais.sh" ', 1)[1].split()
        assert gardes, evenement
        for garde in gardes:
            assert os.access(ROOT / ".ephor" / "hooks" / garde, os.X_OK), garde
    assert "filter-output.sh" not in json.dumps(fichier), (
        "Copilot ne réécrit pas l'appel d'outil : ce hook n'a rien à y faire"
    )


def test_le_greffon_installe_refuse_pour_de_vrai(tmp_path: Path) -> None:
    """Le greffon est installé hors du dépôt : c'est la seule entorse à « tout vit dans
    le dépôt ». On vérifie donc qu'il marche une fois posé, pas seulement qu'il est écrit."""
    env = dict(os.environ)
    env["COPILOT_PLUGINS"] = str(tmp_path)
    pose = subprocess.run(
        ["bash", str(ROOT / ".ephor" / "scripts" / "install_copilot_plugin.sh")],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert pose.returncode == 0, pose.stdout + pose.stderr
    relais = tmp_path / "ephor" / "hooks" / "relais.sh"
    assert relais.exists()
    # Le jeton a été remplacé par le chemin réel, sinon Copilot n'appellerait rien.
    installe = (tmp_path / "ephor" / "hooks" / "hooks.json").read_text()
    assert adapters.JETON_GREFFON not in installe
    assert str(tmp_path / "ephor") in installe

    def appel(racine: Path, chemin: str) -> subprocess.CompletedProcess[str]:
        patch = f"*** Begin Patch\n*** Update File: {racine / chemin}\n+# x\n*** End Patch"
        charge = {"toolName": "apply_patch", "toolArgs": patch, "workingDirectory": str(racine)}
        return subprocess.run(
            ["bash", str(relais), "write-guard.sh"],
            input=json.dumps(charge),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    refus = appel(ROOT, ".ephor/judge.py")
    assert refus.returncode == 2
    assert "zone de contrôle" in refus.stderr
    assert appel(ROOT, "src/app.py").returncode == 0

    # Un dépôt non déclaré : le greffon tourne pour TOUS les dossiers ouverts, et lancer
    # les scripts d'un dépôt inconnu reviendrait à exécuter le code d'un inconnu.
    inconnu = tmp_path / "inconnu"
    (inconnu / ".claude" / "hooks").mkdir(parents=True)
    piege = inconnu / ".claude" / "hooks" / "write-guard.sh"
    piege.write_text("#!/usr/bin/env bash\ntouch '" + str(tmp_path / "piege") + "'\nexit 0\n")
    piege.chmod(0o755)
    assert appel(inconnu, "src/app.py").returncode == 0
    assert not (tmp_path / "piege").exists(), "un dépôt non déclaré ne doit rien exécuter"


def test_le_greffon_se_genere_sans_fichier_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un dépôt où Ephor vient d'être posé n'a pas forcément de VERSION, et la forme
    du sien ne nous appartient pas. `make adapters` ne doit pas tomber pour si peu.

    Avant la 0.9, la version d'Ephor était lue dans le VERSION de la RACINE — celui du
    projet, quand il en avait un : le greffon annonçait alors la version du projet. Elle
    vit maintenant dans `.ephor/VERSION`, et le fichier du projet n'est plus lu."""
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    assert json.loads(adapters.manifeste_copilot())["version"] == "0.0.0"
    (tmp_path / "VERSION").write_text("mon-projet 4.2.9 — hier\n", encoding="utf-8")
    assert json.loads(adapters.manifeste_copilot())["version"] == "0.0.0"
    (tmp_path / ".ephor").mkdir()
    (tmp_path / ".ephor" / "VERSION").write_text("Ephor 0.9.1 — demain\n", encoding="utf-8")
    assert json.loads(adapters.manifeste_copilot())["version"] == "0.9.1"


def relais_pose(tmp_path: Path, depots: list[str]) -> Path:
    """Le relais, installé à la main, avec la liste de dépôts qu'on veut éprouver."""
    greffon = tmp_path / "greffon"
    (greffon / "hooks").mkdir(parents=True)
    shutil.copy(GREFFON / "hooks" / "relais.sh", greffon / "hooks" / "relais.sh")
    (greffon / "depots.txt").write_text("".join(d + "\n" for d in depots), encoding="utf-8")
    return greffon / "hooks" / "relais.sh"


def par_le_relais(
    relais: Path,
    ouvert: Path,
    vise: str,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    patch = f"*** Begin Patch\n*** Update File: {vise}\n+# x\n*** End Patch"
    charge = {"toolName": "apply_patch", "toolArgs": patch, "workingDirectory": str(ouvert)}
    return subprocess.run(
        ["bash", str(relais), "write-guard.sh"],
        input=json.dumps(charge),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
        cwd=cwd,
    )


def test_le_relais_retrouve_le_depot_sans_jq(tmp_path: Path) -> None:
    """Le relais lisait le dossier ouvert avec jq. Sans jq, il retombait sur son dossier
    courant : plus aucun dépôt déclaré reconnu, donc AUCUNE garde lancée, en silence.
    Le dossier courant est ici volontairement ailleurs que le dépôt."""
    relais = relais_pose(tmp_path, [str(ROOT)])
    env = {**os.environ, "PATH": sans_jq(tmp_path)}
    refus = par_le_relais(relais, ROOT, str(ROOT / ".ephor" / "judge.py"), env=env, cwd=tmp_path)
    assert refus.returncode == 2, "sans jq, le relais doit encore trouver le dépôt déclaré"
    assert "zone de contrôle" in refus.stderr
    passe = par_le_relais(relais, ROOT, str(ROOT / "src" / "app.py"), env=env, cwd=tmp_path)
    assert passe.returncode == 0


def test_le_relais_ne_lance_que_les_gardes_du_depot_declare(tmp_path: Path) -> None:
    """Le greffon est actif pour TOUS les dossiers que Copilot ouvre. Une relecture
    adversariale a montré trois façons de lui faire exécuter le code d'un inconnu, ou de
    le faire taire. Les trois sont ici."""
    relais = relais_pose(tmp_path, [str(ROOT)])
    piege = tmp_path / "mechant"
    (piege / ".claude" / "hooks").mkdir(parents=True)
    (piege / ".claude" / "hooks" / "_lib.sh").write_text("", encoding="utf-8")
    temoin = tmp_path / "PIEGE"
    garde = piege / ".claude" / "hooks" / "write-guard.sh"
    garde.write_text(f"#!/usr/bin/env bash\ntouch '{temoin}'\nexit 0\n", encoding="utf-8")
    garde.chmod(0o755)

    # Le dépôt déclaré, et n'importe quel sous-dossier : ce sont SES gardes qui tournent.
    assert par_le_relais(relais, ROOT, str(ROOT / ".ephor" / "judge.py")).returncode == 2
    assert par_le_relais(relais, ROOT / "docs", str(ROOT / ".ephor" / "judge.py")).returncode == 2

    # Un détour par « .. » ne rachète pas un dépôt non déclaré.
    detour = Path(str(ROOT) + "/../" * 8 + str(piege).lstrip("/"))
    refus = par_le_relais(relais, detour, str(piege / "x.py"))
    assert not temoin.exists(), "le code d'un dépôt non déclaré a été exécuté"
    assert refus.returncode == 2, "un dépôt qui attend des gardes doit refuser, pas se taire"

    # Un dossier qui n'attend rien d'Ephor n'est pas notre affaire : on le laisse.
    neutre = tmp_path / "neutre"
    neutre.mkdir()
    assert par_le_relais(relais, neutre, str(neutre / "x.py")).returncode == 0


def test_le_relais_sert_aussi_un_projet_reste_a_l_ancienne_disposition(tmp_path: Path) -> None:
    """Le greffon est posé UNE fois par poste, et les projets passent à la 0.9 un par un.
    Un projet encore équipé à l'ancienne — garde-fous dans le dossier de Claude — doit
    rester gardé : ni refuser tout appel d'outil, ni se taire."""
    ancien = tmp_path / "ancien"
    shutil.copytree(ROOT / ".ephor" / "hooks", ancien / ".claude" / "hooks")
    # Les gardes lisent leurs zones à côté de leur dossier : sans liste, elles refusent tout.
    shutil.copy(ROOT / ".ephor" / "zones.txt", ancien / ".claude" / "zones.txt")
    reglages = (ROOT / ".claude" / "settings.json").read_text(encoding="utf-8")
    (ancien / ".claude" / "settings.json").write_text(reglages, encoding="utf-8")
    relais = relais_pose(tmp_path, [str(ancien)])
    refus = par_le_relais(relais, ancien, str(ancien / ".claude" / "settings.json"))
    assert refus.returncode == 2 and "zone de contrôle" in refus.stderr
    assert par_le_relais(relais, ancien, str(ancien / "src" / "app.py")).returncode == 0


def test_le_relais_refuse_quand_il_ne_peut_plus_faire_son_travail(tmp_path: Path) -> None:
    """Un filet qui disparaît sans prévenir est pire que pas de filet : on continue à
    travailler en se croyant couvert."""
    relais = relais_pose(tmp_path, [])
    refus = par_le_relais(relais, ROOT, str(ROOT / ".ephor" / "judge.py"))
    assert refus.returncode == 2
    assert "make copilot-install" in refus.stderr

    (relais.parent.parent / "depots.txt").unlink()
    sans_liste = par_le_relais(relais, ROOT, str(ROOT / ".ephor" / "judge.py"))
    assert sans_liste.returncode == 2, "plus de liste = plus de gardes : il faut le dire"


def test_la_liste_des_editions_ne_pose_aucune_question() -> None:
    """Mesuré deux fois sous VS Code 1.139.0 : dans la vue « agents », une zone de contrôle
    portée en `false` (= demander) fait apparaître le bouton d'autorisation habituel, et un
    clic écrit le fichier MALGRÉ le refus du garde-fou. Sur un chemin approuvé d'avance, le
    refus tient. C'est donc la question qui affaiblissait la garde : la liste n'en pose
    plus aucune. Prix assumé, écrit dans .ephor/docs/11 : sans greffon installé, VS Code ne
    demande plus rien sur une zone de contrôle."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    derive = adapters.reglages_vscode(reglages)["chat.tools.edits.autoApprove"]
    assert derive == {"**/*": True}, "une exception rouvrirait le bouton qui bat la garde"
    ecrit = json.loads(
        adapters.sans_commentaires((ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8"))
    )
    assert ecrit["chat.tools.edits.autoApprove"] == {"**/*": True}


def gardes_avant_outil() -> tuple[str, ...]:
    """Les gardes que le greffon enchaîne avant chaque outil, lues dans son hooks.json :
    les tests éprouvent la liste que le poste exécutera, pas une copie qui vieillit."""
    greffon = json.loads((GREFFON / "hooks" / "hooks.json").read_text(encoding="utf-8"))
    commande = greffon["hooks"]["PreToolUse"][0]["command"]
    return tuple(commande.split('relais.sh" ', 1)[1].split())


GARDES_AVANT_OUTIL = gardes_avant_outil()
FAUX_SECRET = 'CLE = "AKIAIOSFODNN7EXAMPLE"\n'


def lot(*appels: tuple[str, object]) -> dict[str, object]:
    """Un lot à la forme de la fenêtre d'agents de VS Code, relevée dans son journal."""
    return {
        "sessionId": "essai",
        "cwd": str(ROOT),
        "toolCalls": [
            {"id": f"call_{i}", "name": nom, "args": args} for i, (nom, args) in enumerate(appels)
        ],
    }


def par_le_lot(
    relais: Path, charge: object, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(relais), *GARDES_AVANT_OUTIL],
        input=charge if isinstance(charge, str) else json.dumps(charge),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        env=env,
    )


@pytest.mark.parametrize("moteur", ["jq", "python3"])
def test_la_fenetre_d_agents_envoie_des_lots_et_chaque_appel_est_juge(
    tmp_path: Path, moteur: str
) -> None:
    """Mesuré le 24/09/2026, VS Code 1.139.0, fenêtre d'agents (« vscode-agent-host ») :
    une écriture dans .vscode/settings.json, zone de contrôle, est passée. La garde avait
    tourné, mais le moteur envoie un LOT d'appels, forme qu'aucune garde ne savait lire :
    sans nom d'outil reconnu, les six laissaient passer, en silence."""
    if moteur == "jq" and shutil.which("jq") is None:
        pytest.skip("jq absent")
    relais = relais_pose(tmp_path, [str(ROOT)])
    env = {**os.environ} if moteur == "jq" else {**os.environ, "PATH": sans_jq(tmp_path)}
    vscode = str(ROOT / ".vscode" / "settings.json")
    correctif = f"*** Begin Patch\n*** Update File: {vscode}\n@@\n+x\n*** End Patch\n"

    refus = par_le_lot(relais, lot(("apply_patch", correctif)), env)
    assert refus.returncode == 2 and "zone de contrôle" in refus.stderr, refus.stderr

    lecture = lot(("view", {"path": vscode}), ("rg", {"pattern": "x", "paths": ".vscode"}))
    assert par_le_lot(relais, lecture, env).returncode == 0, "lire n'est pas écrire"

    # Un seul appel interdit dans le lot suffit : sinon il suffirait de le cacher au milieu.
    melange = lot(
        ("view", {"path": vscode}),
        ("create", {"path": str(ROOT / ".ephor" / "x.py"), "file_text": "x"}),
    )
    assert par_le_lot(relais, melange, env).returncode == 2

    # Les outils et les champs propres à ce moteur.
    neuf = str(ROOT / "src" / "neuf.py")
    assert (
        par_le_lot(
            relais, lot(("create", {"path": neuf, "file_text": FAUX_SECRET})), env
        ).returncode
        == 2
    )
    edition = {"path": neuf, "old_str": "a", "new_str": FAUX_SECRET}
    assert par_le_lot(relais, lot(("edit", edition)), env).returncode == 2
    secret = lot(("rg", {"pattern": "KEY", "paths": ".env"}))
    assert par_le_lot(relais, secret, env).returncode == 2, "paths en simple chaîne"
    assert (
        par_le_lot(relais, lot(("create", {"path": neuf, "file_text": "x = 1\n"})), env).returncode
        == 0
    )
    assert par_le_lot(relais, lot(), env).returncode == 0, "un lot vide ne demande rien"


def test_une_forme_d_appel_inconnue_est_refusee(tmp_path: Path) -> None:
    """La forme a déjà changé une fois sans prévenir. La prochaine fois, le filet doit se
    voir tomber : une charge d'appel d'outil qu'on ne sait pas lire est refusée."""
    relais = relais_pose(tmp_path, [str(ROOT)])
    for charge in ({"cwd": str(ROOT), "calls": [{"tool": "apply_patch"}]}, f'{{"cwd": "{ROOT}", '):
        refus = par_le_lot(relais, charge)
        assert refus.returncode == 2 and "ne reconnaît pas la forme" in refus.stderr, charge
    # Ce qui n'est pas un appel d'outil (un message envoyé, la fin d'un tour) n'est pas
    # concerné : la consigne de STEER.md passe toujours.
    message = subprocess.run(
        ["bash", str(relais), "steer.sh"],
        input=json.dumps({"sessionId": "essai", "prompt": "salut", "cwd": str(ROOT)}),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert message.returncode == 0, message.stderr
    # La sentinelle a photographié ce dépôt-ci au passage : un test ne laisse rien derrière lui.
    photo = ROOT / git(ROOT, "rev-parse", "--git-path", "ephor-sentinelle/essai").strip()
    photo.unlink(missing_ok=True)


def formes_d_un_correctif(cible: str, ajout: str) -> dict[str, object]:
    """Les formes sous lesquelles un même correctif peut arriver au relais."""
    patch = f"*** Begin Patch\n*** Update File: {cible}\n@@\n+{ajout}\n*** End Patch\n"
    claude = {"hook_event_name": "PreToolUse", "session_id": "essai", "cwd": str(ROOT)}
    return {
        "copilot": {"toolName": "apply_patch", "toolArgs": patch, "workingDirectory": str(ROOT)},
        "claude, correctif brut": {**claude, "tool_name": "apply_patch", "tool_input": patch},
        "claude, correctif en objet": {
            **claude,
            "tool_name": "apply_patch",
            "tool_input": {"input": patch},
        },
        "nom d'outil inconnu": {**claude, "tool_name": "ApplyPatch", "tool_input": patch},
        "lot toolCalls": lot(("apply_patch", patch)),
        "lot tool_calls": {**claude, "tool_calls": [{"name": "apply_patch", "args": patch}]},
    }


@pytest.mark.parametrize("moteur", ["jq", "python3"])
def test_un_correctif_est_lu_sous_toutes_ses_formes(tmp_path: Path, moteur: str) -> None:
    """Mesuré le 24/09/2026 dans la fenêtre d'agents de VS Code : la même demande — écrire
    dans .vscode/settings.json — était refusée la veille avec l'outil `edit` (arguments en
    objet), et PASSAIT avec `apply_patch`. Le moteur traduit l'appel au format Claude, et le
    correctif arrive brut dans `tool_input`, où seul `toolArgs` était lu. Aucune forme ne
    doit plus pouvoir cacher un correctif."""
    if moteur == "jq" and shutil.which("jq") is None:
        pytest.skip("jq absent")
    relais = relais_pose(tmp_path, [str(ROOT)])
    env = {**os.environ} if moteur == "jq" else {**os.environ, "PATH": sans_jq(tmp_path)}
    zone = formes_d_un_correctif(str(ROOT / ".vscode" / "settings.json"), '"chat.fontSize": 28,')
    ordinaire = formes_d_un_correctif(str(ROOT / "src" / "app" / "neuf.py"), "x = 1")
    secret = formes_d_un_correctif(str(ROOT / "src" / "app" / "neuf.py"), FAUX_SECRET.strip())
    for forme, charge in zone.items():
        refus = par_le_lot(relais, charge, env)
        assert refus.returncode == 2 and "zone de contrôle" in refus.stderr, forme
    for forme, charge in ordinaire.items():
        assert par_le_lot(relais, charge, env).returncode == 0, forme
    for forme, charge in secret.items():
        assert par_le_lot(relais, charge, env).returncode == 2, forme


def test_le_contournement_releve_en_session_est_refuse_par_le_relais(tmp_path: Path) -> None:
    """La charge exacte reçue par le greffon le 24/09/2026 (capteur du dépôt), quand l'agent
    a voulu réécrire .vscode/settings.json par python3 après le refus de son correctif."""
    relais = relais_pose(tmp_path, [str(ROOT)])
    script = (
        'from pathlib import Path\np=Path(".vscode/settings.json")\ns=p.read_text()\n'
        'old="  \\"chat.fontSize\\": 28,"\nnew="  \\"chat.fontSize\\": 40,"\n'
        "p.write_text(s.replace(old,new))"
    )
    charge = {
        "sessionId": "c262ab81-1288-4429-aba5-a5dae515d504",
        "timestamp": 1790252807107,
        "toolName": "bash",
        "toolArgs": {"command": f"python3 -c '{script}'", "description": "Update font size"},
        "workingDirectory": str(ROOT),
    }
    refus = par_le_lot(relais, charge)
    assert refus.returncode == 2 and "script en ligne qui écrit" in refus.stderr, refus.stderr


def test_le_capteur_du_poste_note_chaque_appel_sans_changer_le_verdict(tmp_path: Path) -> None:
    """Le 24/09/2026, un refus est tombé sans laisser de ligne au capteur du dépôt, qui ne
    voit que les appels ayant trouvé leur dépôt. Celui du poste note TOUT, dès la première
    ligne, avec le code de sortie et le message — et ne change aucun verdict."""
    relais = relais_pose(tmp_path, [str(ROOT)])
    trace = relais.parent.parent / "trace.jsonl"
    zone = formes_d_un_correctif(str(ROOT / ".vscode" / "settings.json"), "x")["copilot"]
    ailleurs = {"toolName": "apply_patch", "toolArgs": "x", "workingDirectory": str(tmp_path)}

    assert par_le_lot(relais, zone).returncode == 2
    assert not trace.exists(), "éteint par défaut : les charges portent les demandes"

    (relais.parent.parent / "trace").touch()
    refus = par_le_lot(relais, zone)
    assert refus.returncode == 2 and "zone de contrôle" in refus.stderr, "verdict inchangé"
    assert par_le_lot(relais, ailleurs).returncode == 0
    lignes = [json.loads(ligne) for ligne in trace.read_text(encoding="utf-8").splitlines()]
    assert [ligne["code"] for ligne in lignes] == [2, 0]
    assert "zone de contrôle" in lignes[0]["message"] and lignes[1]["message"] == ""
    assert json.loads(lignes[0]["charge"]) == zone
    assert lignes[0]["gardes"].split() == list(GARDES_AVANT_OUTIL)


def test_l_installation_date_le_dossier_pour_que_vs_code_refasse_sa_copie(tmp_path: Path) -> None:
    """VS Code garde sa propre copie des hooks du greffon et ne la refait que si la DATE DU
    DOSSIER change (mesuré le 24/09/2026 : son repère est cette date, en millisecondes).
    Réécrire les fichiers en place ne la changeait pas : toutes les réinstallations d'une
    journée sont passées inaperçues, et la vue « agents » gardait l'ancienne liste de gardes."""
    greffons = tmp_path / "greffons"
    env = {**os.environ, "COPILOT_PLUGINS": str(greffons)}
    script = ROOT / ".ephor" / "scripts" / "install_copilot_plugin.sh"
    subprocess.run(["bash", str(script)], env=env, capture_output=True, check=True, timeout=120)
    hooks = greffons / "ephor" / "hooks"
    os.utime(hooks, (1_000_000_000, 1_000_000_000))
    subprocess.run(["bash", str(script)], env=env, capture_output=True, check=True, timeout=120)
    assert hooks.stat().st_mtime > 1_000_000_000, "le dossier doit changer de date"
