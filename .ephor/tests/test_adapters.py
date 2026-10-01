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
from conftest import ROOT, bootstrap, depot_node, git
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
# Les quatre points d'entrée des garde-fous (ADR 0011), un par événement.
POINTS_D_ENTREE = {
    "PreToolUse": "avant-outil.sh",
    "PostToolUse": "apres-outil.sh",
    "UserPromptSubmit": "debut-tour.sh",
    "Stop": "fin-tour.sh",
}
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
await essaie("outil_mcp", () =>
  hooks["tool.execute.before"]({ tool: "fichiers_write_file" },
    { args: { path: join(racine, "tasks", "T-9", "contract.json"), content: "{}" } }))
await essaie("lecture", () =>
  hooks["tool.execute.before"]({ tool: "read" }, { args: { filePath: join(racine, ".env") } }))
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
    regles = adapters.permissions_agent({"tools": "Read, Bash", "permissionMode": "plan"})
    assert regles["edit"] == "deny"
    assert regles["bash"]["*"] == "deny"
    assert regles["bash"]["git diff *"] == "allow"
    meta, _ = adapters.entete(ROOT / ".claude" / "agents" / "planner.md")
    assert adapters.permissions_agent(meta)["bash"] == "deny", "sans Bash, rien à lancer"


def test_l_evaluateur_relit_chaque_tache() -> None:
    """Décision de l'opérateur (30/09/2026, #30) : l'évaluation n'est jamais sautée. Le juge ne
    voit que les critères ; l'évaluateur voit la conception, le code en double, les cas
    oubliés. Et son effort ne dépend plus de la session qui l'appelle : lancé depuis le dossier
    d'une tâche en effort bas, il relisait en effort bas."""
    skill = (ROOT / ".claude" / "skills" / "feature" / "SKILL.md").read_text(encoding="utf-8")
    revue = skill.split("**6.")[1].split("**7.")[0]
    faible = next(ligne for ligne in revue.splitlines() if ligne.startswith("- `faible`"))
    assert "`evaluator`" in faible, faible
    meta, _ = adapters.entete(ROOT / ".claude" / "agents" / "evaluator.md")
    assert "chaque tâche" in meta["description"]
    assert meta.get("effort") == "high" and meta.get("model") == "opus"


def test_la_procedure_enchaine_tout_et_peut_aller_pas_a_pas() -> None:
    """Demandé par l'opérateur (#25, #33) : enchaîner de la demande au verdict, avec une
    confirmation à chaque étape en option. Deux gestes humains restent, et la procédure le dit."""
    skill = (ROOT / ".claude" / "skills" / "feature" / "SKILL.md").read_text(encoding="utf-8")
    meta, _ = adapters.entete(ROOT / ".claude" / "skills" / "feature" / "SKILL.md")
    assert "pas-a-pas" in meta["argument-hint"]
    assert "deux gestes, pas plus" in skill and "« ok »" in skill
    assert "make -s taches" in skill, "le tableau des tâches, pas une liste de dossiers"
    genere = (ROOT / ".opencode" / "commands" / "feature.md").read_text(encoding="utf-8")
    assert "pas-a-pas" in genere


def test_la_procedure_montre_les_taches_meme_sans_le_tableau(tmp_path: Path) -> None:
    """`make taches | head || ls tasks` : le code d'un tube est celui de `head`, et le repli ne
    servait jamais. Sans la cible (un Makefile d'avant la 1.5), la procédure ne montrait rien."""
    skill = (ROOT / ".claude" / "skills" / "feature" / "SKILL.md").read_text(encoding="utf-8")
    ligne = next(ligne for ligne in skill.splitlines() if "make -s taches" in ligne)
    commande = ligne.strip()[2:-1]  # !`…`
    (tmp_path / "tasks" / "FEAT-009").mkdir(parents=True)
    (tmp_path / "Makefile").write_text("verify:\n\t@true\n")
    sortie = subprocess.run(
        ["bash", "-c", commande], cwd=tmp_path, capture_output=True, text=True, check=False
    )
    assert "FEAT-009" in sortie.stdout, sortie.stdout + sortie.stderr


def test_l_effort_d_un_role_passe_a_opencode() -> None:
    """Sous opencode, l'effort d'un rôle est la variante du modèle. Mesuré (30/09/2026, faux
    serveur au format d'Anthropic) : `variant: high` dans le fichier de l'agent, modèle réglé
    sur le poste, et la réflexion envoyée passe de 48 000 à 16 000 tokens."""
    genere = (ROOT / ".opencode" / "agents" / "evaluator.md").read_text(encoding="utf-8")
    assert 'variant: "high"' in genere
    scout = (ROOT / ".opencode" / "agents" / "scout.md").read_text(encoding="utf-8")
    assert "variant:" not in scout, "sans effort réglé, celui de la session"


def test_les_reglages_d_un_role_se_lisent_et_se_changent_sur_place() -> None:
    texte = (ROOT / ".claude" / "agents" / "evaluator.md").read_text(encoding="utf-8")
    assert adapters.reglages_de(texte) == {"model": "opus", "effort": "high"}
    nu = adapters.sans_reglages(texte.encode()).decode()
    entete = nu.split("---")[1]
    assert "model:" not in entete and "effort:" not in entete and "tools:" in entete
    change = adapters.avec_reglages(texte, {"model": "sonnet", "effort": "max"})
    assert change == texte.replace("model: opus\neffort: high", "model: sonnet\neffort: max")
    sans_effort = adapters.avec_reglages(texte, {"model": "opus"})
    assert "effort:" not in sans_effort.split("---")[1], "une valeur absente retire la ligne"
    assert adapters.sans_reglages(change.encode()) == adapters.sans_reglages(texte.encode())


def test_l_evaluateur_peut_lancer_les_tests() -> None:
    """Session réelle (30/09/2026) : l'évaluateur, en mode plan, ne pouvait lancer aucun test
    (Claude Code n'y laisse passer que la lecture ; opencode : git, rg, fd, jq), et sa
    procédure lui disait de « raisonner ». Il relance maintenant les tests, comme le
    relecteur ; il n'écrit toujours rien."""
    meta, corps = adapters.entete(ROOT / ".claude" / "agents" / "evaluator.md")
    assert meta.get("permissionMode") != "plan", "le mode plan refuse make test et pytest"
    regles = adapters.permissions_agent(meta)
    assert regles == {"edit": "deny"}, "le terminal suit les règles du projet"
    projet = adapters.permissions(json.loads((ROOT / ".claude" / "settings.json").read_text()))
    bash = projet["bash"]
    assert isinstance(bash, dict)
    assert bash["make *"] == "allow" and bash["uv run pytest *"] == "allow"
    assert "make verify" in corps and "rouge.json" in corps
    genere = (ROOT / ".opencode" / "agents" / "evaluator.md").read_text(encoding="utf-8")
    assert "bash:" not in genere and "edit: deny" in genere


def test_un_sous_agent_sans_terminal_ne_recoit_pas_l_ordre_de_s_en_servir() -> None:
    """Le planner devait lancer la carte du dépôt sans en avoir le droit : il tournait en rond."""
    for source in sorted((ROOT / ".claude" / "agents").glob("*.md")):
        meta, corps = adapters.entete(source)
        outils = adapters.liste(meta.get("tools", ""))
        if outils and "Bash" not in outils:
            assert "`bash " not in corps, f"{source.name} : un script à lancer, sans terminal"


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
    """La vue « agents » de VS Code lance les points d'entrée du dépôt, en disant que c'est
    Copilot : la garde de lecture tourne pour lui, le filtre de sortie non (ADR 0011)."""
    fichier = json.loads((ROOT / ".github" / "hooks" / "ephor.json").read_text(encoding="utf-8"))
    appels = {
        evenement: [hook["command"] for hook in groupe]
        for evenement, groupe in fichier["hooks"].items()
    }
    assert appels == {
        evenement: [f"env EPHOR_OUTIL=copilot bash .ephor/hooks/{script}"]
        for evenement, script in POINTS_D_ENTREE.items()
        if evenement != "PostToolUse"
    }
    for (commande,) in appels.values():
        assert os.access(ROOT / commande.rsplit(" ", 1)[1], os.X_OK), commande


def test_claude_code_n_appelle_que_les_points_d_entree() -> None:
    """Ajouter une garde touchait les réglages du projet, que la mise à jour ne remplace pas
    s'ils ont été retouchés : quatre points d'entrée, et plus rien à changer ici (ADR 0011)."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    appels = {
        evenement: [
            hook["command"].strip('"').rsplit("/", 1)[-1]
            for groupe in groupes
            for hook in groupe["hooks"]
        ]
        for evenement, groupes in reglages["hooks"].items()
    }
    assert appels == {evenement: [script] for evenement, script in POINTS_D_ENTREE.items()}
    for groupes in reglages["hooks"].values():
        assert all(groupe.get("matcher", "*") == "*" for groupe in groupes), (
            "le tri par outil se fait dans le point d'entrée, pas ici"
        )


def test_le_lecteur_d_entetes_refuse_ce_qu_il_ne_sait_pas_lire(tmp_path: Path) -> None:
    fichier = tmp_path / "SKILL.md"
    fichier.write_text("---\nname: x\nmetadata:\n  a: 1\n---\n\ncorps\n", encoding="utf-8")
    with pytest.raises(ValueError, match="non supportée"):
        adapters.entete(fichier)


def test_une_liste_yaml_dans_l_entete_d_un_sous_agent_se_lit(tmp_path: Path) -> None:
    """`tools:` suivi de `  - Read`, que Claude Code accepte : refusée, elle faisait échouer
    `make adapters` en entier, et l'installation d'Ephor sur ce dépôt (audit du 01/10/2026)."""
    fichier = tmp_path / "agent.md"
    fichier.write_text("---\nname: x\ntools:\n  - Read\n  - 'Grep'\nmodel: opus\n---\ncorps\n")
    meta, corps = adapters.entete(fichier)
    assert adapters.liste(meta["tools"]) == ["Read", "Grep"] and meta["model"] == "opus"
    assert corps == "\ncorps\n"
    assert adapters.liste("[Read, Grep]") == ["Read", "Grep"]


def test_les_hooks_du_projet_ne_deviennent_pas_des_commandes_cassees_sous_copilot() -> None:
    """Geste 0 fait comme la doc le dit, « gardez vos règles, ajoutez les siennes » : le hook du
    projet `npm run lint:staged` devenait `bash .ephor/hooks/npm run lint:staged`, une commande
    cassée que le relais sautait en silence (audit du 01/10/2026)."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    hooks = reglages["hooks"]
    hooks["PreToolUse"].append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "npm run lint:staged"}]}
    )
    hooks["Stop"].append(
        {"hooks": [{"type": "command", "command": '"$CLAUDE_PROJECT_DIR"/outils/notifier.sh'}]}
    )
    attendus = adapters.gardes_copilot(json.loads((ROOT / ".claude" / "settings.json").read_text()))
    assert adapters.gardes_copilot(reglages) == attendus, "seules les gardes d'Ephor sont relayées"
    assert "npm run" not in adapters.hooks_copilot(reglages)
    assert "notifier.sh" not in adapters.greffon_copilot(reglages)
    assert adapters.hooks_du_projet(reglages) == [
        "PreToolUse : npm run lint:staged",
        'Stop : "$CLAUDE_PROJECT_DIR"/outils/notifier.sh',
    ]


def test_un_texte_en_bloc_dans_l_entete_se_lit(tmp_path: Path) -> None:
    """`description: >-` sur plusieurs lignes, du YAML que Claude Code accepte : la ligne était
    « non supportée », `make adapters` tombait, et l'installation avec (audit du 01/10/2026)."""
    fichier = tmp_path / "SKILL.md"
    fichier.write_text(
        "---\nname: deploy\ndescription: >-\n  Déploie le service\n  en recette.\n"
        "notes: |\n  un\n  deux\nsuite: une valeur\n  sur deux lignes\n---\ncorps\n",
        encoding="utf-8",
    )
    meta, corps = adapters.entete(fichier)
    assert meta["description"] == "Déploie le service en recette."
    assert meta["notes"] == "un\ndeux"
    assert meta["suite"] == "une valeur sur deux lignes" and meta["name"] == "deploy"
    assert corps == "\ncorps\n"


def test_une_virgule_dans_une_valeur_vs_code_reste(tmp_path: Path) -> None:
    """Les virgules finales du JSONC se retiraient aussi DANS les valeurs du projet."""
    existant = '{\n  "files.exclude": {"**/x,}": true},\n  "a": "b, ]",\n}'
    fusion = json.loads(adapters.sans_commentaires(adapters.fichier_vscode(REGLAGES, existant)))
    assert fusion["files.exclude"] == {"**/x,}": True} and fusion["a"] == "b, ]"


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
    # Un outil qu'opencode ne connaît pas (un serveur MCP) passait sans garde : il écrit, il
    # est jugé comme une écriture. Une lecture, elle, ne lance rien (ses réglages la jugent).
    assert "zone de contrôle" in resultat["outil_mcp"], resultat["outil_mcp"]
    assert resultat["lecture"] == "ok"
    assert "service.py" in resultat["consigne"]
    assert "zone de contrôle" in resultat["terminal_zone"], "le terminal ne contourne pas l'édition"
    assert resultat["terminal_propre"] == "ok"


PILOTE_PLAFOND = """
const module = await import("__GREFFON__")
const annonces = []
const coupe = {
  role: "assistant", finish: "length",
  tokens: { input: 5000, output: 0, reasoning: 32000, cache: { read: 0, write: 0 } },
}
const client = {
  tui: { showToast: async ({ body }) => { annonces.push(body) } },
  session: {
    get: async ({ path }) =>
      ({ data: { id: path.id, parentID: path.id === "enfant" ? "p" : undefined } }),
    messages: async ({ path }) => ({ data: path.id === "enfant" ? [{ info: coupe }] : [] }),
  },
}
const hooks = await module.ephor({ worktree: "__RACINE__", client })
const resultat = {}
async function plafond(nom, modele, actuel) {
  const sortie = { maxOutputTokens: actuel }
  await hooks["chat.params"]({ sessionID: nom, model: modele }, sortie)
  return sortie.maxOutputTokens
}
const grand = { limit: { context: 1000000, output: 128000 } }
async function avecReflexion(nom, modele, actuel, budget) {
  const pensee = { type: "enabled", budgetTokens: budget }
  const sortie = { maxOutputTokens: actuel, options: { thinking: pensee } }
  await hooks["chat.params"]({ sessionID: nom, model: modele }, sortie)
  return [sortie.maxOutputTokens, sortie.options.thinking.budgetTokens]
}
const copilot = { limit: { context: 200000, output: 64000 } }
resultat.max = await avecReflexion("max", copilot, 32000, 63999)
resultat.high = await avecReflexion("high", copilot, 32000, 32000)
resultat.large = await avecReflexion("large", grand, 32000, 63999)
resultat.releve = await plafond("principale", grand, 32000)
resultat.court = await plafond("titre", grand, 1000)
resultat.partage = await plafond("autre", { limit: { context: 262144, output: 262144 } }, 32000)
resultat.inconnu = await plafond("passerelle", { limit: { context: 0, output: 0 } }, 32000)
const tache = {
  title: "contrat", output: "<task_result></task_result>", metadata: { sessionId: "enfant" },
}
await hooks["tool.execute.after"]({ tool: "task" }, tache)
resultat.tache = tache.output
const sain = { title: "t", output: "ok", metadata: { sessionId: "principale" } }
await hooks["tool.execute.after"]({ tool: "task" }, sain)
resultat.sain = sain.output
const fin = (id, session, tokens, finish) => hooks.event({ event: { type: "message.updated",
  properties: { info: {
    id, sessionID: session, role: "assistant", time: { completed: 1 }, finish, tokens,
  } } } })
const plein = { input: 600000, output: 14000, reasoning: 0, cache: { read: 0, write: 0 } }
await fin("m1", "principale", plein, "stop")
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "principale" } } })
await fin("m2", "enfant", coupe.tokens, "length")
await fin("m2", "enfant", coupe.tokens, "length")
await hooks.event({ event: { type: "session.idle", properties: { sessionID: "enfant" } } })
resultat.annonces = annonces
console.log(JSON.stringify(resultat))
"""


@pytest.mark.skipif(MOTEUR is None, reason="greffon opencode : bun ou node est nécessaire")
@pytest.mark.parametrize("fixe_par_le_poste", [False, True])
def test_le_greffon_releve_le_plafond_et_dit_quand_il_coupe(
    tmp_path: Path, fixe_par_le_poste: bool
) -> None:
    """opencode coupe chaque réponse à 32 000 tokens, réflexion comprise, et rend du vide
    sans erreur : le planner a échoué ainsi quatre fois sur quatre (28/09/2026)."""
    projet = tmp_path / "projet"
    shutil.copytree(ROOT / ".ephor" / "hooks", projet / ".ephor" / "hooks")
    shutil.copy(ROOT / ".ephor" / "usage.py", projet / ".ephor" / "usage.py")
    pilote = tmp_path / "pilote.mjs"
    pilote.write_text(
        PILOTE_PLAFOND.replace("__RACINE__", str(projet)).replace(
            "__GREFFON__", str(ROOT / ".opencode" / "plugins" / "ephor.js")
        ),
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"}
    if fixe_par_le_poste:
        env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"] = "64000"
    assert MOTEUR is not None
    sortie = subprocess.run(
        [MOTEUR, str(pilote)], capture_output=True, text=True, timeout=120, check=True, env=env
    )
    resultat = json.loads(sortie.stdout)
    if fixe_par_le_poste:
        assert resultat["releve"] == 32000, "le poste a choisi : on n'y touche pas"
    else:
        assert resultat["releve"] == 128000, "la limite du modèle, pas celle d'opencode"
        assert resultat["partage"] == 65536, "jamais plus du quart de la mémoire"
    assert resultat["court"] == 1000, "un plafond court est voulu (titre, résumé)"
    # Effort max d'un modèle Copilot : réflexion de 63 999 pour une limite de 64 000, la
    # réponse n'avait plus qu'un token (#27). La réflexion garde tout, sauf 16 000.
    assert resultat["max"][1] == 48000, "la réflexion laisse sa place à la réponse"
    if not fixe_par_le_poste:
        assert resultat["max"][0] == 16000, "réponse et réflexion tiennent dans la limite"
        assert resultat["high"] == [32000, 32000], "en high, rien ne change à la réflexion"
        assert resultat["large"] == [64001, 63999], "de la place partout : total = limite"
    else:
        assert resultat["max"][0] == 32000, "le plafond de réponse fixé par le poste reste"
    assert resultat["inconnu"] == 32000, "limite inconnue : on ne devine pas"
    assert resultat["tache"].startswith("ATTENTION : Réponse coupée au plafond : 32000 tokens")
    assert "<task_result>" in resultat["tache"], "la réponse du sous-agent reste là"
    assert resultat["sain"] == "ok"
    annonces = resultat["annonces"]
    assert [a["variant"] for a in annonces] == ["warning", "error"], annonces
    assert "61 %" in annonces[0]["message"] and "/handoff" in annonces[0]["message"]
    assert "32000 tokens de réflexion" in annonces[1]["message"], "une coupure, annoncée une fois"


PILOTE_SENTINELLE = """
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs"
import { join } from "node:path"
const racine = "__RACINE__"
const module = await import("__GREFFON__")
const annonces = []
const client = {
  tui: { showToast: async ({ body }) => { annonces.push(body) } },
  session: { get: async () => ({ data: {} }), messages: async () => ({ data: [] }) },
}
const hooks = await module.ephor({ worktree: racine, client })
const resultat = {}
const tour = async (session, pendant) => {
  await hooks["chat.message"]({ sessionID: session }, { message: {}, parts: [] })
  pendant()
  // opencode relit la consigne à chaque requête : la photo ne doit pas être reprise ici.
  await hooks["experimental.chat.system.transform"]({ sessionID: session }, { system: [] })
  await hooks.event({ event: { type: "session.idle", properties: { sessionID: session } } })
}
await tour("calme", () => writeFileSync(join(racine, "src", "a.py"), "A = 2\\n"))
resultat.calme = { annonces: annonces.length, arret: existsSync(join(racine, "AGENT_STOP")) }
await tour("detour", () => {
  mkdirSync(join(racine, "tasks", "T-9"), { recursive: true })
  writeFileSync(join(racine, "tasks", "T-9", "contract.json"), "{}")
})
resultat.arret = existsSync(join(racine, "AGENT_STOP"))
  ? readFileSync(join(racine, "AGENT_STOP"), "utf8") : ""
resultat.annonces = annonces
console.log(JSON.stringify(resultat))
"""


def projet_opencode(tmp_path: Path) -> Path:
    """Un projet commité avec les hooks, les zones et les réglages d'Ephor."""
    projet = tmp_path / "projet"
    shutil.copytree(ROOT / ".ephor" / "hooks", projet / ".ephor" / "hooks")
    shutil.copy(ROOT / ".ephor" / "zones.txt", projet / ".ephor" / "zones.txt")
    shutil.copy(ROOT / ".ephor" / "usage.py", projet / ".ephor" / "usage.py")
    (projet / ".claude").mkdir()
    shutil.copy(ROOT / ".claude" / "settings.json", projet / ".claude" / "settings.json")
    (projet / "src").mkdir()
    (projet / "src" / "a.py").write_text("A = 1\n")
    git(projet, "init", "-q", "-b", "main")
    git(projet, "config", "user.email", "t@example.invalid")
    git(projet, "config", "user.name", "t")
    git(projet, "add", "-A")
    git(projet, "commit", "-qm", "base")
    return projet


PILOTE_UN_TOUR = """
const module = await import("__GREFFON__")
const annonces = []
const client = { tui: { showToast: async ({ body }) => { annonces.push(body) } } }
const hooks = await module.ephor({ worktree: "__RACINE__", client })
await hooks["chat.message"]({ sessionID: "s-opencode" }, { message: {}, parts: [] })
console.log(JSON.stringify(annonces))
"""


@pytest.mark.skipif(MOTEUR is None, reason="greffon opencode : bun ou node est nécessaire")
def test_sous_opencode_l_autre_session_du_dossier_est_annoncee(tmp_path: Path) -> None:
    """Une autre session d'agent vivante dans le même dossier : opencode ne donne pas au modèle
    ce que le début de tour écrit, son greffon le montre donc à l'opérateur."""
    projet = projet_opencode(tmp_path)
    autre = subprocess.Popen(["sleep", "60"])
    try:
        sessions = Path(git(projet, "rev-parse", "--absolute-git-dir").strip()) / "ephor-sessions"
        sessions.mkdir()
        (sessions / str(autre.pid)).write_text("session=s-autre\nbranche=task/T-2\noutil=sleep\n")
        pilote = tmp_path / "pilote.mjs"
        pilote.write_text(
            PILOTE_UN_TOUR.replace("__RACINE__", str(projet)).replace(
                "__GREFFON__", str(ROOT / ".opencode" / "plugins" / "ephor.js")
            ),
            encoding="utf-8",
        )
        assert MOTEUR is not None
        sortie = subprocess.run(
            [MOTEUR, str(pilote)], capture_output=True, text=True, timeout=120, check=True
        )
    finally:
        autre.kill()
        autre.wait()
    annonces = json.loads(sortie.stdout)
    assert [a["variant"] for a in annonces] == ["warning"], annonces
    assert "une autre session d'agent" in annonces[0]["message"]
    assert "branche task/T-2" in annonces[0]["message"]


@pytest.mark.skipif(MOTEUR is None, reason="greffon opencode : bun ou node est nécessaire")
def test_la_sentinelle_tourne_sous_opencode(tmp_path: Path) -> None:
    """opencode n'avait pas de sentinelle : un script qui écrivait dans tasks/ n'était vu par
    personne (29/09/2026). Son greffon a pourtant un début et une fin de tour."""
    projet = projet_opencode(tmp_path)
    pilote = tmp_path / "pilote.mjs"
    pilote.write_text(
        PILOTE_SENTINELLE.replace("__RACINE__", str(projet)).replace(
            "__GREFFON__", str(ROOT / ".opencode" / "plugins" / "ephor.js")
        ),
        encoding="utf-8",
    )
    assert MOTEUR is not None
    sortie = subprocess.run(
        [MOTEUR, str(pilote)], capture_output=True, text=True, timeout=120, check=True
    )
    resultat = json.loads(sortie.stdout)
    assert resultat["calme"] == {"annonces": 0, "arret": False}, "du code ordinaire : silence"
    assert "tasks/T-9/contract.json" in resultat["arret"], resultat["arret"]
    assert [a["variant"] for a in resultat["annonces"]] == ["error"]
    assert "sentinelle" in resultat["annonces"][0]["message"]
    reprises = git(projet, "for-each-ref", "--format=%(refname)", "refs/ephor/checkpoints")
    assert "refs/ephor/checkpoints/calme" in reprises, "les points de reprise aussi"


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
        assert gardes == [POINTS_D_ENTREE[evenement]], "le greffon ne change plus : ADR 0011"
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


def test_le_relais_juge_le_dossier_d_une_tache_avec_les_gardes_de_la_copie_declaree(
    tmp_path: Path,
) -> None:
    """`make task-approve` dit d'ouvrir le dossier de la tâche sous Copilot ; il n'est pas
    déclaré, et porte des garde-fous : le relais refusait tout, `read_file` compris (audit du
    01/10/2026). Il est jugé maintenant par les gardes de la copie déclarée, l'opérateur."""
    principal = tmp_path / "depot"
    (principal / ".ephor").mkdir(parents=True)
    shutil.copytree(ROOT / ".ephor" / "hooks", principal / ".ephor" / "hooks")
    shutil.copy(ROOT / ".ephor" / "zones.txt", principal / ".ephor" / "zones.txt")
    (principal / "src").mkdir()
    (principal / "src" / "app.py").write_text("x = 1\n")
    git(principal, "init", "-q", "-b", "main")
    git(principal, "add", "-A")
    git(principal, "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "x")
    tache = tmp_path / "depot-T-1"
    git(principal, "worktree", "add", "-q", "-b", "task/T-1", str(tache))
    relais = relais_pose(tmp_path, [str(principal)])
    passe = par_le_relais(relais, tache, str(tache / "src" / "app.py"))
    assert passe.returncode == 0, passe.stderr
    refus = par_le_relais(relais, tache, str(tache / ".ephor" / "zones.txt"))
    assert refus.returncode == 2 and "zone de contrôle" in refus.stderr, refus.stderr


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


def test_les_gestes_de_l_operateur_sont_refuses_aux_trois_outils() -> None:
    """`make task-check` écrit la trace qui fait l'approbation avec le contrat : un geste de
    l'opérateur, comme `make task-new`. `Bash(make *)` le laissait à l'agent sans question
    (vu le 27/09/2026)."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    opencode = json.loads((ROOT / ".opencode" / "opencode.json").read_text(encoding="utf-8"))
    vscode = adapters.sans_commentaires(
        (ROOT / ".vscode" / "settings.json").read_text(encoding="utf-8")
    )
    for geste in (
        "make task-new *",
        "make task-check *",
        "make task-worktree *",
        "bash .ephor/scripts/worktree.sh *",
        "python3 .ephor/judge.py new *",
        "python3 .ephor/judge.py rouge *",
    ):
        assert f"Bash({geste})" in reglages["permissions"]["deny"], geste
        assert opencode["permission"]["bash"][geste] == "deny", geste
    assert "make task-check" in vscode


def test_une_skill_n_ouvre_pas_plus_que_les_reglages() -> None:
    """`/feature` ouvrait `Bash(git *)`, et `/brownfield-onboard` tout le terminal : pendant
    la skill, `git reset`, `git branch -f` ou `git clean -fdx` passaient sans question
    (audit du 29/09/2026). Une skill reprend des commandes des réglages, jamais plus."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    permis = set(reglages["permissions"]["allow"])
    vues = 0
    for skill in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        entete = skill.read_text(encoding="utf-8").split("---")[1]
        outils = re.search(r"^allowed-tools:(.*)$", entete, re.M)
        texte = outils.group(1) if outils else ""
        assert not re.search(r"(?<![(\w])Bash(?![(\w])", texte), f"{skill.parent.name} : Bash nu"
        for commande in re.findall(r"Bash\([^)]*\)", texte):
            vues += 1
            assert commande in permis, f"{skill.parent.name} ouvre {commande}"
    assert vues, "les skills nomment leurs commandes"


def rendez_vous(ici: Path, moi: str, autre: str) -> str:
    """Une garde qui attend l'autre : elles ne se voient que si elles tournent ensemble."""
    return (
        "#!/usr/bin/env bash\ncat >/dev/null\n"
        f'touch "{ici}/{moi}"\n'
        f'for i in $(seq 1 30); do [ -e "{ici}/{autre}" ] && exit 0; sleep 0.1; done\n'
        f'echo "seule : {moi}" >&2\nexit 2\n'
    )


def depot_aux_gardes_factices(tmp_path: Path, factices: dict[str, str]) -> Path:
    depot = tmp_path / "depot"
    gardes = depot / ".ephor" / "hooks"
    gardes.mkdir(parents=True)
    for vrai in ("_lib.sh", "ensemble.sh", "avant-outil.sh", "fin-tour.sh"):
        shutil.copy(ROOT / ".ephor" / "hooks" / vrai, gardes / vrai)
    for nom, corps in factices.items():
        (gardes / nom).write_text(corps, encoding="utf-8")
        (gardes / nom).chmod(0o755)
    return depot


def test_le_relais_copilot_lance_les_gardes_ensemble(tmp_path: Path) -> None:
    """Sept gardes l'une après l'autre à chaque appel d'outil, lectures comprises : 0,55 à
    0,6 s par appel sous Copilot (audit du 29/09/2026). Elles tournent maintenant ensemble."""
    rdv = tmp_path / "rdv"
    rdv.mkdir()
    depot = depot_aux_gardes_factices(
        tmp_path,
        {"write-guard.sh": rendez_vous(rdv, "w", "r"), "read-guard.sh": rendez_vous(rdv, "r", "w")},
    )
    relais = relais_pose(tmp_path, [str(depot)])
    charge = {
        "toolName": "read_file",
        "toolArgs": {"filePath": str(depot / "a.py")},
        "workingDirectory": str(depot),
    }
    resultat = subprocess.run(
        ["bash", str(relais), "write-guard.sh", "read-guard.sh"],
        input=json.dumps(charge),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert resultat.returncode == 0, resultat.stderr


PILOTE_ENSEMBLE = """
const module = await import("__GREFFON__")
const hooks = await module.ephor({ worktree: "__RACINE__" })
try {
  await hooks["tool.execute.before"]({ tool: "write" },
    { args: { filePath: "src/a.py", content: "x = 1" } })
  console.log("ok")
} catch (erreur) {
  console.log(String(erreur.message))
}
"""


@pytest.mark.skipif(MOTEUR is None, reason="greffon opencode : bun ou node est nécessaire")
def test_le_greffon_opencode_lance_les_gardes_ensemble(tmp_path: Path) -> None:
    """Sous opencode, les quatre gardes d'une écriture tournaient l'une après l'autre : 0,5 s
    par écriture (audit du 29/09/2026). Elles tournent maintenant ensemble."""
    rdv = tmp_path / "rdv"
    rdv.mkdir()
    passe = "#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n"
    depot = depot_aux_gardes_factices(
        tmp_path,
        {
            "block-secrets.sh": rendez_vous(rdv, "b", "w"),
            "write-guard.sh": rendez_vous(rdv, "w", "b"),
            "refs-guard.sh": passe,
            "scope-guard.sh": passe,
        },
    )
    pilote = tmp_path / "pilote.mjs"
    pilote.write_text(
        PILOTE_ENSEMBLE.replace("__RACINE__", str(depot)).replace(
            "__GREFFON__", str(ROOT / ".opencode" / "plugins" / "ephor.js")
        ),
        encoding="utf-8",
    )
    assert MOTEUR is not None
    sortie = subprocess.run(
        [MOTEUR, str(pilote)], capture_output=True, text=True, timeout=120, check=True
    )
    assert sortie.stdout.strip() == "ok", sortie.stdout


# ------------------------------------------------ points d'entrée (ADR 0011)


def journalise(journal: Path, nom: str, suite: str = "exit 0") -> str:
    """Une garde factice qui note son nom, puis fait `suite`."""
    return f'#!/usr/bin/env bash\ncat >/dev/null\necho {nom} >> "{journal}"\n{suite}\n'


GARDES_D_APPEL = (
    "kill-switch.sh",
    "block-secrets.sh",
    "write-guard.sh",
    "scope-guard.sh",
    "refs-guard.sh",
    "shell-guard.sh",
    "filter-output.sh",
    "read-guard.sh",
)
ECRIT = {"kill-switch.sh", "block-secrets.sh", "write-guard.sh", "scope-guard.sh", "refs-guard.sh"}
CORRECTIF = "*** Begin Patch\n*** Add File: a.py\n+x\n*** End Patch"


@pytest.mark.parametrize(
    ("outil", "charge", "attendues"),
    [
        ("", {"tool_name": "Read", "tool_input": {"file_path": "a.py"}}, {"kill-switch.sh"}),
        (
            "",
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            {"kill-switch.sh", "shell-guard.sh", "filter-output.sh"},
        ),
        (
            "claude",
            {"tool_name": "Write", "tool_input": {"file_path": "a.py", "content": "x"}},
            ECRIT,
        ),
        (
            "claude",
            {"tool_name": "mcp__x__y", "tool_input": {"query": "q"}},
            ECRIT | {"shell-guard.sh", "filter-output.sh"},
        ),
        (
            "copilot",
            {"toolName": "read_file", "toolArgs": {"filePath": "/d/a.py"}},
            {"kill-switch.sh", "read-guard.sh"},
        ),
        (
            "copilot",
            {"toolName": "run_in_terminal", "toolArgs": {"command": "ls"}},
            {"kill-switch.sh", "shell-guard.sh"},
        ),
        ("copilot", {"toolName": "apply_patch", "toolArgs": CORRECTIF}, ECRIT | {"read-guard.sh"}),
        (
            "opencode",
            {"tool_name": "bash", "tool_input": {"command": "ls"}},
            {"kill-switch.sh", "shell-guard.sh"},
        ),
    ],
)
def test_le_point_d_entree_choisit_les_gardes_de_l_appel(
    tmp_path: Path, outil: str, charge: dict[str, object], attendues: set[str]
) -> None:
    """Quelles gardes pour quel appel, et pour quel outil : décidé dans le moteur, que la
    mise à jour remplace, et plus dans trois listes à tenir d'accord (ADR 0011)."""
    journal = tmp_path / "journal"
    depot = depot_aux_gardes_factices(
        tmp_path, {nom: journalise(journal, nom) for nom in GARDES_D_APPEL}
    )
    env = {k: v for k, v in os.environ.items() if k != "EPHOR_OUTIL"}
    if outil:
        env["EPHOR_OUTIL"] = outil
    resultat = subprocess.run(
        ["bash", str(depot / ".ephor" / "hooks" / "avant-outil.sh")],
        input=json.dumps(charge),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert set(journal.read_text(encoding="utf-8").split()) == attendues


@pytest.mark.parametrize(
    ("outil", "alerte", "lances"),
    [
        ("", False, {"budget.sh", "checkpoint.sh", "contexte.sh"}),
        ("claude", True, {"budget.sh", "checkpoint.sh", "contexte.sh"}),
        ("opencode", False, {"checkpoint.sh", "contexte.sh"}),
        ("copilot", True, {"budget.sh", "checkpoint.sh"}),
    ],
)
def test_la_fin_de_tour_dit_tout_ce_qu_elle_a_a_dire(
    tmp_path: Path, outil: str, alerte: bool, lances: set[str]
) -> None:
    """Budget, sentinelle et point de reprise, alerte de contexte : lancés ensemble. L'alerte
    de la sentinelle (code 1, pour que l'outil la montre) n'étouffe pas celle du contexte."""
    journal = tmp_path / "journal"
    sentinelle = "echo 'sentinelle : tasks/T-9 a changé pendant le tour' >&2; exit 1"
    depot = depot_aux_gardes_factices(
        tmp_path,
        {
            "budget.sh": journalise(journal, "budget.sh"),
            "checkpoint.sh": journalise(
                journal, "checkpoint.sh", sentinelle if alerte else "exit 0"
            ),
            "contexte.sh": journalise(
                journal, "contexte.sh", """echo '{"systemMessage": "61 % de la mémoire"}'"""
            ),
        },
    )
    env = {k: v for k, v in os.environ.items() if k != "EPHOR_OUTIL"}
    if outil:
        env["EPHOR_OUTIL"] = outil
    resultat = subprocess.run(
        ["bash", str(depot / ".ephor" / "hooks" / "fin-tour.sh")],
        input='{"session_id": "s"}',
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    assert set(journal.read_text(encoding="utf-8").split()) == lances
    avec_contexte = "contexte.sh" in lances
    if alerte:
        assert resultat.returncode == 1
        assert "sentinelle" in resultat.stderr
        assert ("61 % de la mémoire" in resultat.stderr) == avec_contexte, "redite en clair"
        assert resultat.stdout == "", "un code non nul fait ignorer la sortie par l'outil"
    else:
        assert resultat.returncode == 0, resultat.stderr
        sortie = [json.loads(ligne) for ligne in resultat.stdout.splitlines() if ligne]
        assert sortie == ([{"systemMessage": "61 % de la mémoire"}] if avec_contexte else [])


def test_la_fin_de_tour_rend_un_seul_objet_json(tmp_path: Path) -> None:
    """L'alerte de contexte et le rappel de PROGRESS.md répondent chacun en JSON : deux objets
    à la suite, et l'outil n'en lisait aucun. Ils sortent en un seul (#31)."""
    journal = tmp_path / "journal"
    depot = depot_aux_gardes_factices(
        tmp_path,
        {
            "checkpoint.sh": journalise(journal, "checkpoint.sh"),
            "contexte.sh": journalise(
                journal, "contexte.sh", """echo '{"systemMessage": "61 %"}'"""
            ),
            "progres.sh": journalise(
                journal,
                "progres.sh",
                """echo '{"decision": "block", "reason": "PROGRES", "systemMessage": "r"}'""",
            ),
        },
    )
    env = {k: v for k, v in os.environ.items() if k != "EPHOR_OUTIL"}
    resultat = subprocess.run(
        ["bash", str(depot / ".ephor" / "hooks" / "fin-tour.sh")],
        input='{"session_id": "s"}',
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
        env=env,
    )
    assert resultat.returncode == 0, resultat.stderr
    assert json.loads(resultat.stdout) == {
        "decision": "block",
        "reason": "PROGRES",
        "systemMessage": "61 %\nr",
    }, "budget.sh absent du dépôt : sauté, sans erreur"


def test_le_relais_sert_un_depot_d_avant_les_points_d_entree(tmp_path: Path) -> None:
    """Un poste réinstallé en 1.4 sert aussi un dépôt resté en 1.3 : sans les points d'entrée,
    le relais lance les gardes d'alors, et ne laisse pas ce dépôt sans garde-fou."""
    journal = tmp_path / "journal"
    appel = (*GARDES_D_APPEL[:6], "read-guard.sh")
    depot = depot_aux_gardes_factices(
        tmp_path, {nom: journalise(journal, nom) for nom in (*appel, "steer.sh")}
    )
    for entree in ("avant-outil.sh", "fin-tour.sh"):
        (depot / ".ephor" / "hooks" / entree).unlink()
    relais = relais_pose(tmp_path, [str(depot)])
    charge = {
        "toolName": "read_file",
        "toolArgs": {"filePath": str(depot / "a.py")},
        "workingDirectory": str(depot),
    }
    for point, attendues in (("avant-outil.sh", set(appel)), ("debut-tour.sh", {"steer.sh"})):
        journal.write_text("", encoding="utf-8")
        resultat = subprocess.run(
            ["bash", str(relais), point],
            input=json.dumps(charge),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert resultat.returncode == 0, resultat.stderr
        assert set(journal.read_text(encoding="utf-8").split()) == attendues, point


def test_le_greffon_d_opencode_vit_dans_le_moteur() -> None:
    """Le fichier que lit opencode ne fait qu'importer le greffon du moteur : il ne change
    plus d'une version à l'autre, et la mise à jour du moteur suffit (ADR 0011)."""
    projet = (ROOT / ".opencode" / "plugins" / "ephor.js").read_text(encoding="utf-8")
    code = [ligne for ligne in projet.splitlines() if ligne and not ligne.startswith("//")]
    assert code == ['export { ephor } from "../../.ephor/opencode/ephor.js"']
    moteur = (ROOT / ".ephor" / "opencode" / "ephor.js").read_text(encoding="utf-8")
    appeles = set(re.findall(r'garde\(racine, "([^"]+)"', moteur))
    assert appeles == {"avant-outil.sh", "debut-tour.sh", "fin-tour.sh"}, appeles


def test_les_roles_recoivent_leurs_modeles_sous_opencode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Vu en session réelle : sous opencode, planner et evaluator tournaient sur le modèle de
    la session ; la recette de la doc 07 n'avait pas été trouvée. Une commande l'applique, dans
    la configuration du poste, jamais dans le dépôt."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    config = tmp_path / "opencode" / "opencode.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"model": "p/session", "agent": {"scout": {"temperature": 0.1}}}))
    assert adapters.main(["modeles", "FORT=p/fort", "RAPIDE=p/rapide", "STANDARD="]) == 0
    reglee = json.loads(config.read_text())
    assert reglee["model"] == "p/session", "le modèle de la session n'est pas touché"
    assert reglee["agent"]["planner"]["model"] == reglee["agent"]["evaluator"]["model"] == "p/fort"
    assert reglee["agent"]["scout"] == {"temperature": 0.1, "model": "p/rapide"}
    assert "reviewer" not in reglee["agent"], "le niveau standard n'était pas donné"
    monkeypatch.setenv("OPENCODE", "1")
    assert adapters.main(["modeles", "FORT=p/autre"]) == 2, "un geste de l'opérateur"
    monkeypatch.delenv("OPENCODE")
    config.unlink()
    jsonc = config.parent / "opencode.jsonc"
    jsonc.write_text("// mes réglages\n{}\n")
    assert adapters.main(["modeles", "FORT=p/fort"]) == 1, "un JSONC commenté n'est pas réécrit"
    assert '"planner"' in capsys.readouterr().out
    assert jsonc.read_text() == "// mes réglages\n{}\n"
    # Un `.jsonc` sans commentaire (celui qu'opencode écrit lui-même) est réglé comme l'autre :
    # il était refusé, et `make doctor` disait ensuite « aucun modèle réglé » (02/10/2026).
    jsonc.write_text('{"$schema": "https://opencode.ai/config.json"}\n')
    assert adapters.main(["modeles", "FORT=p/fort"]) == 0
    assert json.loads(jsonc.read_text())["agent"]["planner"]["model"] == "p/fort"
    assert not config.exists(), "réglé dans le .jsonc, pas à côté"


def test_l_installation_n_ecrase_pas_la_configuration_opencode_du_projet(tmp_path: Path) -> None:
    """Un projet qui travaille déjà sous opencode : son modèle, son serveur MCP, son sous-agent
    et sa commande étaient remplacés par ceux d'Ephor, sans .ephor-new ni un mot du bilan, et
    perdus s'ils n'étaient pas suivis par git (audit du 01/10/2026)."""
    cible = tmp_path / "projet"
    depot_node(cible)
    dossier = cible / ".opencode"
    (dossier / "agents").mkdir(parents=True)
    (dossier / "commands").mkdir()
    mcp = {"docs": {"type": "remote", "url": "https://mcp.exemple.invalid"}}
    (dossier / "opencode.json").write_text(json.dumps({"model": "maison/fort", "mcp": mcp}))
    (dossier / "agents" / "reviewer.md").write_text("---\ndescription: le nôtre\n---\nRelis.\n")
    (dossier / "commands" / "verify.md").write_text("---\ndescription: la nôtre\n---\nnpm test\n")

    sortie = bootstrap(cible)

    config = json.loads((dossier / "opencode.json").read_text(encoding="utf-8"))
    assert config["model"] == "maison/fort" and config["mcp"] == mcp, "ses clés à lui restent"
    assert config["permission"]["bash"], "les permissions d'Ephor y sont"
    for nom in ("agents/reviewer.md", "commands/verify.md"):
        assert (
            "le nôtre" in (dossier / nom).read_text() or "la nôtre" in (dossier / nom).read_text()
        )
        assert adapters.MARQUE in (dossier / f"{nom}.ephor-new").read_text(encoding="utf-8")
        assert f".opencode/{nom}" in sortie
    assert adapters.MARQUE in (dossier / "agents" / "planner.md").read_text(encoding="utf-8")


# ------------------------------------------------- relecture adverse de la 1.5.2 (01/10/2026)


def _claude_minimal(racine: Path, reglages: dict[str, Any] | None = None) -> None:
    """Un .claude/ de projet : réglages, un sous-agent, une skill."""
    (racine / ".claude" / "agents").mkdir(parents=True)
    (racine / ".claude" / "skills" / "verif").mkdir(parents=True)
    (racine / ".claude" / "settings.json").write_text(
        json.dumps(reglages or {"permissions": {"allow": ["Bash(make *)"]}, "hooks": {}})
    )
    (racine / ".claude" / "agents" / "scout.md").write_text(
        "---\nname: scout\ndescription: éclaireur\nmodel: haiku\n---\nCherche.\n"
    )
    (racine / ".claude" / "skills" / "verif" / "SKILL.md").write_text(
        "---\nname: verif\ndescription: vérifie\n---\n```!\nmake verify || true\n```\n"
    )


def test_une_regle_mcp_ou_inconnue_n_arrete_plus_la_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`"allow": ["mcp__github__list_issues"]` : « règle de permission illisible », vingt lignes de
    trace, aucun fichier dérivé, et l'installation disait « 0 à fusionner », code 0."""
    _claude_minimal(
        tmp_path,
        {"permissions": {"allow": ["mcp__github__list_issues", "Bash(make *)", "Zut:(x)"]}},
    )
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    rendus = adapters.fichiers()
    assert ".opencode/opencode.json" in rendus
    config = json.loads(rendus[".opencode/opencode.json"])
    assert config["permission"]["bash"]["make *"] == "allow"
    assert any("Zut:(x)" in note for note in adapters.NOTES)
    assert not any("mcp__github__list_issues" in note for note in adapters.NOTES), (
        "un outil MCP se lit ; opencode ne sait juste pas le restreindre"
    )


def test_un_en_tete_sans_description_ou_un_maxturns_cite_passent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude_minimal(tmp_path)
    (tmp_path / ".claude" / "agents" / "muet.md").write_text(
        '---\nname: muet\nmodel: sonnet\nmaxTurns: "15"\n---\nRelis.\n'
    )
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    rendus = adapters.fichiers()
    agent = rendus[".opencode/agents/muet.md"]
    assert re.search(r'description: "?muet"?', agent)
    assert "steps: 15" in agent
    assert any("muet.md : pas de `description:`" in note for note in adapters.NOTES)


def test_un_bloc_shell_sur_plusieurs_lignes_est_garde_et_dit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ValueError: bloc ! sur plusieurs lignes, non traduisible` arrêtait tout ; le bloc reste en
    texte, les autres fichiers s'écrivent, et la note le dit."""
    _claude_minimal(tmp_path)
    (tmp_path / ".claude" / "skills" / "verif" / "SKILL.md").write_text(
        "---\nname: verif\ndescription: vérifie\n---\n```!\nmake verify || true\necho fini\n```\n"
    )
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    rendus = adapters.fichiers()
    assert "```!\nmake verify || true\necho fini\n```" in rendus[".opencode/commands/verif.md"]
    assert any("plusieurs lignes" in note for note in adapters.NOTES)


def test_le_controle_des_derives_laisse_un_fichier_qui_est_au_projet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`adapters.py check` disait « modifié à la main » d'un sous-agent opencode du projet, pour
    toujours, et renvoyait vers `make adapters`, qui le laisse exprès."""
    _claude_minimal(tmp_path)
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    assert adapters.ecrire() == 0
    assert adapters.verifier() == 0
    (tmp_path / ".opencode" / "agents" / "scout.md").write_text(
        "---\ndescription: le nôtre\n---\nCherche à notre façon.\n"
    )
    assert adapters.verifier() == 0, "un fichier à vous n'est pas une dérive"
    (tmp_path / ".opencode" / "commands" / "verif.md").write_text(
        (tmp_path / ".opencode" / "commands" / "verif.md").read_text() + "\nretouche\n"
    )
    assert adapters.verifier() == 1, "un fichier dérivé retouché, lui, l'est"


def test_le_bloc_permission_d_un_opencode_json_du_projet_est_annonce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _claude_minimal(tmp_path)
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "opencode.json").write_text(
        json.dumps({"model": "p/m", "permission": {"bash": {"npm run build": "allow"}}})
    )
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    config = json.loads(adapters.fichiers()[".opencode/opencode.json"])
    assert config["model"] == "p/m", "ses clés restent"
    assert any("bloc « permission »" in note for note in adapters.NOTES)
    # Une génération d'Ephor relue (sa marque `share`) n'est pas « votre bloc ».
    (tmp_path / ".opencode" / "opencode.json").write_text(
        json.dumps({"share": "disabled", "permission": {"bash": {"x": "deny"}}})
    )
    adapters.fichiers()
    assert not any("bloc « permission »" in note for note in adapters.NOTES)


def test_les_modeles_d_opencode_suivent_le_nom_du_fichier_et_nomment_les_oublies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`make opencode-modeles` réglait `agent.relecteur` pour un fichier relecteur-maison.md
    (opencode nomme l'agent par son fichier), taisait un rôle dont `model:` n'est pas un
    niveau, et prenait n'importe quelle valeur."""
    _claude_minimal(tmp_path)
    (tmp_path / ".claude" / "agents" / "relecteur-maison.md").write_text(
        "---\nname: relecteur\ndescription: relit\nmodel: sonnet\n---\nRelis.\n"
    )
    (tmp_path / ".claude" / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: relit\nmodel: claude-sonnet-4-5\n---\nRelis.\n"
    )
    monkeypatch.setattr(adapters, "ROOT", tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    assert adapters.modeles_opencode({"FORT": "sans-barre", "STANDARD": "", "RAPIDE": ""}) == 2
    assert "fournisseur/modèle" in capsys.readouterr().out
    assert adapters.modeles_opencode({"FORT": "", "STANDARD": "p/std", "RAPIDE": "p/vite"}) == 0
    sortie = capsys.readouterr().out
    reglee = json.loads((tmp_path / "config" / "opencode" / "opencode.json").read_text())
    assert reglee["agent"]["relecteur-maison"]["model"] == "p/std"
    assert "relecteur" not in reglee["agent"]
    assert reglee["agent"]["scout"]["model"] == "p/vite"
    assert "reviewer (model: claude-sonnet-4-5)" in sortie


def test_les_blocs_en_ligne_des_skills_passent_le_controle_de_claude_code() -> None:
    """Claude Code 2.1.286 refuse un bloc `!`…`` qui mêle une accolade et un guillemet
    (« Contains brace with quote character (expansion obfuscation) ») : la skill /feature ne
    démarrait plus du tout (vu en session réelle, 01/10/2026). Et 2.1.287 refuse un sous-shell
    entre parenthèses (« This command uses shell operators that require approval for safety ») :
    même symptôme, vu le 02/10/2026 ; `||` et `|` entre commandes autorisées passent."""
    for skill in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        for bloc in re.findall(r"!`([^`]*)`", skill.read_text(encoding="utf-8")):
            melange = (set("{}") & set(bloc)) and (set("\"'") & set(bloc))
            assert not melange, f"{skill.relative_to(ROOT)} : accolade et guillemet dans « {bloc} »"
            assert not (set("()") & set(bloc)), (
                f"{skill.relative_to(ROOT)} : sous-shell dans « {bloc} »"
                " (refusé par Claude Code 2.1.287)"
            )
