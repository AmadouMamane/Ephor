#!/usr/bin/env python3
"""Adaptateurs d'agents — une source, trois outils.

`.claude/` fait foi : réglages, sous-agents, skills. Ce script en dérive les fichiers
d'opencode, et vérifie ensuite qu'ils n'ont pas dérivé. Bibliothèque standard seulement,
syntaxe Python 3.9, comme le juge : il doit tourner partout, CI comprise.

    python3 .ephor/adapters.py write   # régénère les fichiers dérivés
    python3 .ephor/adapters.py check   # sort en erreur si un fichier dérivé a dérivé
                                        # (c'est ce que `make ephor-test` vérifie)

Ce script ne garantit rien : les garanties vivent dans le juge et la CI, identiques pour
les trois outils. Ici, on ne règle que le confort de session — permissions, sous-agents,
commandes — pour éviter d'entretenir trois copies à la main.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARQUE = "généré par .ephor/adapters.py depuis"

# Correspondance des outils. Ce qui n'est pas listé n'a pas d'équivalent : ignoré.
OUTILS = {
    "Bash": "bash",
    "Edit": "edit",
    "Write": "edit",
    "MultiEdit": "edit",
    "NotebookEdit": "edit",
    "Read": "read",
    "Grep": "grep",
    "Glob": "glob",
    "WebFetch": "webfetch",
    "WebSearch": "websearch",
}
# opencode n'accepte pas de motif pour ces permissions : une action, point.
SANS_MOTIF = ("webfetch", "websearch")
# Ce qu'un sous-agent en lecture seule garde le droit de lancer.
BASH_LECTURE = ("git status *", "git diff *", "git log *", "git show *", "rg *", "fd *", "jq *")
# Couleurs Claude Code -> couleurs sémantiques d'opencode.
COULEURS = {
    "red": "error",
    "yellow": "warning",
    "green": "success",
    "blue": "info",
    "cyan": "accent",
    "magenta": "secondary",
}
REGLE = re.compile(r"^([A-Za-z]+)(?:\((.*)\))?$")
VERSION = re.compile(r"\b\d+\.\d+\.\d+\b")
BLOC_SHELL = re.compile(r"```!\n(.+?)\n```", re.S)
# Hooks que Copilot ne sait pas exploiter : il ignore `matcher` et ne réécrit pas l'appel ;
# il n'écrit ses tokens qu'à la fermeture de la session, donc pas de remplissage à mesurer.
HOOKS_HORS_COPILOT = ("filter-output.sh", "contexte.sh")
# Garde-fous que seul Copilot a besoin qu'on lui branche : Claude Code et opencode
# appliquent déjà ces refus par leurs permissions. (write-guard.sh n'en est plus : depuis que
# les zones vivent dans le moteur, les trois outils le lancent, et il est dans les réglages.)
HOOKS_COPILOT_EN_PLUS = (("PreToolUse", "read-guard.sh", 10),)
# Le greffon Copilot s'installe par machine : son chemin n'existe pas au moment où on
# écrit le dépôt. `.ephor/scripts/install_copilot_plugin.sh` remplace ce jeton à
# l'installation.
GREFFON = ".ephor/greffon-copilot"
JETON_GREFFON = "__RACINE_DU_GREFFON__"
# --------------------------------------------------------------------- lecture


def version_d_ephor() -> str:
    fichier = ROOT / ".ephor" / "VERSION"
    if not fichier.exists():
        return "0.0.0"
    trouve = VERSION.search(fichier.read_text(encoding="utf-8"))
    return trouve.group(0) if trouve else "0.0.0"


def entete(chemin: Path) -> tuple[dict[str, str], str]:
    """Entête YAML plate (`clé: valeur`) puis corps. Toute autre forme est refusée."""
    texte = chemin.read_text(encoding="utf-8")
    if not texte.startswith("---\n"):
        raise ValueError(f"{chemin} : entête YAML manquante")
    fin = texte.find("\n---\n", 3)
    if fin == -1:
        raise ValueError(f"{chemin} : entête YAML non fermée")
    meta: dict[str, str] = {}
    for ligne in texte[4:fin].splitlines():
        if not ligne.strip() or ligne.lstrip().startswith("#"):
            continue
        cle, separateur, valeur = ligne.partition(":")
        if not separateur or cle != cle.strip() or not cle.strip():
            raise ValueError(f"{chemin} : ligne d'entête non supportée : {ligne!r}")
        meta[cle.strip()] = valeur.strip()
    return meta, texte[fin + 4 :]


def liste(valeur: str) -> list[str]:
    """`tools: Read, Grep, Glob` -> ['Read', 'Grep', 'Glob']."""
    return [element.strip() for element in valeur.replace(",", " ").split() if element.strip()]


# ----------------------------------------------------------------- permissions


def motif_bash(motif: str) -> str:
    """Claude accepte `x:*` comme `x *`. opencode ne connaît que la seconde forme."""
    return motif[:-2] + " *" if motif.endswith(":*") else motif


def motif_chemin(motif: str) -> str:
    """`./x` -> `x` ; `**/x` -> `*x`. Chez opencode, `*` traverse déjà les dossiers."""
    if motif.startswith("./"):
        motif = motif[2:]
    if motif.startswith("**/"):
        reste = motif[3:]
        motif = reste if reste.startswith("*") else "*" + reste
    return motif


def permissions(reglages: dict[str, object]) -> dict[str, object]:
    """Traduit les permissions de Claude Code en permissions opencode.

    Claude tranche par sévérité : deny l'emporte toujours. opencode applique la DERNIÈRE
    règle qui correspond. On écrit donc, dans l'ordre : le défaut, les allow, les ask,
    les deny. Le résultat se lit dans le même sens que le fichier d'origine.
    """
    brut = reglages.get("permissions", {})
    assert isinstance(brut, dict)
    paniers: dict[str, dict[str, str]] = {"bash": {"*": "ask"}, "edit": {"*": "allow"}}
    plats: dict[str, str] = {}
    for action in ("allow", "ask", "deny"):
        entrees = brut.get(action, [])
        assert isinstance(entrees, list)
        for entree in entrees:
            trouve = REGLE.match(str(entree).strip())
            if trouve is None:
                raise ValueError(f"règle de permission illisible : {entree!r}")
            outil, motif = trouve.group(1), trouve.group(2)
            cle = OUTILS.get(outil)
            if cle is None:
                continue
            if cle in SANS_MOTIF:
                # opencode ne sait pas restreindre par domaine : un `allow` restreint
                # deviendrait une autorisation générale. On le rétrograde en question.
                plats[cle] = "ask" if (motif and action == "allow") else action
                continue
            if motif is None:
                motif = "*"
            elif cle == "bash":
                motif = motif_bash(motif)
            elif motif.startswith(("~", "/")):
                cle, motif = "external_directory", motif
            else:
                motif = motif_chemin(motif)
            panier = paniers.setdefault(cle, {})
            panier.pop(motif, None)  # une règle réécrite repart en fin de liste
            panier[motif] = action
    sortie: dict[str, object] = {}
    for cle in ("bash", "edit", "read", "external_directory", "grep", "glob"):
        if cle in paniers:
            sortie[cle] = paniers[cle]
    for cle in SANS_MOTIF:
        if cle in plats:
            sortie[cle] = plats[cle]
    return sortie


def config_opencode(reglages: dict[str, object]) -> str:
    config = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "tool_output": {"max_lines": 600, "max_bytes": 51200},
        "permission": permissions(reglages),
    }
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


# ------------------------------------------------------------------ sous-agents


def valeur_yaml(valeur: str) -> str:
    """Du JSON est du YAML valide : guillemets et échappements gratuits."""
    return json.dumps(valeur, ensure_ascii=False)


def permissions_agent(meta: dict[str, str]) -> dict[str, object]:
    """Ce qu'un sous-agent a le droit de faire, déduit de son entête Claude Code."""
    outils = liste(meta.get("tools", ""))
    interdits = liste(meta.get("disallowedTools", ""))
    plan = meta.get("permissionMode") == "plan"
    # Sans `tools:`, Claude Code donne tous les outils : ne pas l'oublier pour l'écriture,
    # sinon le sous-agent généré est muet là où l'original écrit.
    ecrit = (not outils or {"Edit", "Write"} & set(outils)) and not (
        {"Edit", "Write"} & set(interdits)
    )
    lance = (not outils or "Bash" in outils) and "Bash" not in interdits
    regles: dict[str, object] = {}
    if plan or not ecrit:
        regles["edit"] = "deny"
    if not lance:
        regles["bash"] = "deny"
    elif plan:
        lecture: dict[str, str] = {"*": "deny"}
        for motif in BASH_LECTURE:
            lecture[motif] = "allow"
        regles["bash"] = lecture
    return regles


def agent_opencode(source: Path, meta: dict[str, str], corps: str) -> str:
    lignes = ["---", f"# {MARQUE} {relatif(source)} — ne pas modifier à la main"]
    lignes.append("description: " + valeur_yaml(meta["description"]))
    lignes.append("mode: subagent")
    if "maxTurns" in meta:
        lignes.append("steps: " + str(int(meta["maxTurns"])))
    couleur = COULEURS.get(meta.get("color", ""))
    if couleur is not None:
        lignes.append("color: " + couleur)
    regles = permissions_agent(meta)
    if regles:
        lignes.append("permission:")
    for cle, valeur in regles.items():
        if isinstance(valeur, str):
            lignes.append(f"  {cle}: {valeur}")
        else:
            assert isinstance(valeur, dict)
            lignes.append(f"  {cle}:")
            for motif, action in valeur.items():
                lignes.append(f"    {valeur_yaml(motif)}: {action}")
    lignes.append("---")
    return "\n".join(lignes) + corps


# -------------------------------------------------------------------- commandes


def commande_opencode(source: Path, meta: dict[str, str], corps: str) -> str:
    def convertit(trouve: re.Match[str]) -> str:
        commande = trouve.group(1).strip()
        if "\n" in commande:
            raise ValueError(f"{source} : bloc `!` sur plusieurs lignes, non traduisible")
        return "!`" + commande + "`"

    lignes = ["---", f"# {MARQUE} {relatif(source)} — ne pas modifier à la main"]
    lignes.append("description: " + valeur_yaml(meta["description"]))
    lignes.append("---")
    return "\n".join(lignes) + BLOC_SHELL.sub(convertit, corps)


# ---------------------------------------------------------------------- Copilot


def expression(motif: str) -> str:
    """Motif de commande Claude -> expression régulière VS Code, ancrée au début.

    VS Code lit `/.../` : la barre oblique doit être échappée, l'espace non.
    """
    prefixe = motif.endswith(" *")
    base = motif[:-2] if prefixe else motif
    morceaux = []
    for caractere in base:
        if caractere == "*":
            morceaux.append(".*")
        elif caractere in "\\^$.|?+()[]{}/":
            morceaux.append("\\" + caractere)
        else:
            morceaux.append(caractere)
    # Limite de mot, sinon `fd *` approuverait `fdisk` et `rg *` approuverait `rgrep`.
    fin = "\\b" if prefixe else ("" if motif.endswith("*") else "$")
    return "/^{}{}/".format("".join(morceaux), fin)


def gardes_copilot(reglages: dict[str, object]) -> dict[str, list[tuple[str, int]]]:
    """Événement -> [(nom du garde-fou, délai)], dans l'ordre de `.claude/settings.json`."""
    hooks = reglages.get("hooks", {})
    assert isinstance(hooks, dict)
    sortie: dict[str, list[tuple[str, int]]] = {}
    for evenement in ("PreToolUse", "UserPromptSubmit", "Stop"):
        groupes = hooks.get(evenement, [])
        assert isinstance(groupes, list)
        for groupe in groupes:
            for hook in groupe.get("hooks", []):
                script = str(hook.get("command", "")).strip('"').rsplit("/", 1)[-1]
                if not script or script in HOOKS_HORS_COPILOT:
                    continue
                sortie.setdefault(evenement, []).append((script, int(hook.get("timeout", 10))))
    for evenement, script, delai in HOOKS_COPILOT_EN_PLUS:
        sortie.setdefault(evenement, []).append((script, delai))
    return sortie


def hooks_copilot(reglages: dict[str, object]) -> str:
    """Forme « hooks du dossier de travail ». Trouvée et listée par VS Code, jamais
    exécutée le 23 septembre 2026 ; le 24, dans la vue « agents », elle TOURNE, avant le
    greffon, avec une charge au format Claude — et un refus y dispense le greffon de
    parler (mesuré au capteur du poste). Le greffon ci-dessous reste nécessaire pour les
    vues où ces hooks ne tournent pas."""
    sortie: dict[str, list[dict[str, object]]] = {}
    for evenement, gardes in gardes_copilot(reglages).items():
        for script, delai in gardes:
            sortie.setdefault(evenement, []).append(
                {"type": "command", "command": "bash .ephor/hooks/" + script, "timeout": delai}
            )
    return json.dumps({"version": 1, "hooks": sortie}, indent=2, ensure_ascii=False) + "\n"


def greffon_copilot(reglages: dict[str, object]) -> str:
    """Forme « greffon installé », la seule qui s'exécute dans toutes les vues.

    Un appel de relais par événement, et non un appel par garde-fou : le relais retrouve
    le dépôt ouvert dans la charge, puis enchaîne les garde-fous de CE dépôt. Le chemin du
    greffon n'est connu qu'à l'installation, d'où le jeton, remplacé par
    `.ephor/scripts/install_copilot_plugin.sh`.
    """
    sortie: dict[str, list[dict[str, object]]] = {}
    for evenement, gardes in gardes_copilot(reglages).items():
        noms = " ".join(script for script, _ in gardes)
        entree = {
            "type": "command",
            "command": f'bash "{JETON_GREFFON}/hooks/relais.sh" {noms}',
            # Les garde-fous s'enchaînent dans un seul appel : le délai les couvre tous.
            "timeout": sum(delai for _, delai in gardes) + 5,
        }
        sortie[evenement] = [entree]
    return json.dumps({"hooks": sortie}, indent=2, ensure_ascii=False) + "\n"


def manifeste_copilot() -> str:
    """`plugin.json` au format Claude : c'est le seul que Copilot ait accepté.

    La version est celle d'Ephor quand on la trouve. Un dépôt où Ephor vient
    d'être posé n'a pas forcément de fichier VERSION, et la forme du sien ne nous
    appartient pas : dans le doute on met 0.0.0, plutôt que de faire échouer
    `make adapters` sur un détail cosmétique.
    """
    version = version_d_ephor()
    manifeste = {
        "name": "ephor",
        "version": version,
        "description": (
            "Relais des garde-fous du dépôt ouvert : bouton d'arrêt, anti-secret, "
            "périmètre du contrat, dépôts de référence, zones de contrôle."
        ),
    }
    return json.dumps(manifeste, indent=2, ensure_ascii=False) + "\n"


def reglages_vscode(reglages: dict[str, object]) -> dict[str, object]:
    """Les cinq clés `chat.*` que le générateur possède ; le reste du fichier est à vous.

    Attention : côté VS Code, `false` fait DEMANDER, il ne bloque pas. Et c'est la
    QUESTION qui affaiblissait les garde-fous : cette liste portait les zones de contrôle
    en `false`, et dans la vue « agents » VS Code affichait alors son bouton
    d'autorisation habituel — un clic, et le fichier était écrit malgré le refus du
    garde-fou (mesuré deux fois sous VS Code 1.139.0). Sur un chemin approuvé d'avance,
    au contraire, le refus est définitif. Les exceptions sont donc retirées : plus de
    question sur une zone de contrôle, donc plus de bouton pour la contredire.

    Le prix, assumé : sur un poste où le greffon n'est PAS installé, VS Code ne demande
    plus rien avant d'écrire une zone de contrôle. Hors de la vue « agents », où les hooks
    du dépôt tournent, rien ne tient alors en direct — mais le juge et la CI refusent le
    correctif, et `make doctor` nomme l'état du greffon.

    Ce qui bloque vraiment côté Copilot, ce sont les hooks — d'où `chat.useClaudeHooks`.
    """
    brut = reglages.get("permissions", {})
    assert isinstance(brut, dict)
    terminal: dict[str, bool] = {}
    for action in ("allow", "ask", "deny"):
        entrees = brut.get(action, [])
        assert isinstance(entrees, list)
        for entree in entrees:
            trouve = REGLE.match(str(entree).strip())
            if trouve is None or trouve.group(2) is None:
                continue
            outil, motif = trouve.group(1), trouve.group(2)
            if outil == "Bash":
                terminal[expression(motif_bash(motif))] = action == "allow"
    return {
        "chat.useClaudeHooks": True,
        "chat.useAgentsMdFile": True,
        "chat.tools.global.autoApprove": False,
        "chat.tools.terminal.autoApprove": terminal,
        # Une seule entrée, et c'est voulu : voir la docstring. Une exception ici
        # rouvrirait le bouton qui bat le garde-fou dans la vue « agents ».
        "chat.tools.edits.autoApprove": {"**/*": True},
    }


def sans_commentaires(texte: str) -> str:
    """Retire commentaires et virgules finales hors chaînes : VS Code écrit du JSONC."""
    sortie: list[str] = []
    i, n, chaine = 0, len(texte), False
    while i < n:
        c = texte[i]
        if chaine:
            sortie.append(c)
            if c == "\\" and i + 1 < n:
                sortie.append(texte[i + 1])
                i += 2
                continue
            chaine = c != '"'
            i += 1
            continue
        if c == '"':
            chaine = True
            sortie.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and texte[i + 1] == "/":
            while i < n and texte[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and texte[i + 1] == "*":
            i += 2
            while i + 1 < n and not (texte[i] == "*" and texte[i + 1] == "/"):
                i += 1
            i += 2
            continue
        sortie.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(sortie))


def fichier_vscode(reglages: dict[str, object], existant: str | None) -> str:
    """Nos clés, plus les vôtres : le reste du fichier est conservé (pas les commentaires)."""
    garde: dict[str, object] = {}
    if existant is not None and existant.strip():
        try:
            garde = json.loads(sans_commentaires(existant))
        except ValueError as erreur:
            message = f".vscode/settings.json illisible ({erreur}) : corrigez-le à la main"
            raise ValueError(message) from erreur
    garde.update(reglages_vscode(reglages))
    entete = (
        "// Les clés chat.* sont générées depuis .claude/settings.json par"
        " .ephor/adapters.py (make adapters).\n"
        "// Vos autres clés sont conservées ; vos commentaires, eux, ne le sont pas.\n"
        "// Les zones de contrôle ne sont VOLONTAIREMENT pas listées ici : une entrée\n"
        "// `false` fait demander, et dans la vue « agents » cette question devient un\n"
        "// bouton qui lève le refus du garde-fou. Détail : .ephor/docs/11-trois-agents.md.\n"
    )
    return entete + json.dumps(garde, indent=2, ensure_ascii=False) + "\n"


# ----------------------------------------------------------------------- sortie


def relatif(chemin: Path) -> str:
    return str(chemin.relative_to(ROOT))


def fichiers() -> dict[str, str]:
    """Chemin relatif -> contenu attendu."""
    reglages = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    rendus = {".opencode/opencode.json": config_opencode(reglages)}
    for source in sorted((ROOT / ".claude" / "agents").glob("*.md")):
        meta, corps = entete(source)
        rendus[f".opencode/agents/{source.stem}.md"] = agent_opencode(source, meta, corps)
    for source in sorted((ROOT / ".claude" / "skills").glob("*/SKILL.md")):
        meta, corps = entete(source)
        nom = source.parent.name
        rendus[f".opencode/commands/{nom}.md"] = commande_opencode(source, meta, corps)
    rendus[".github/hooks/ephor.json"] = hooks_copilot(reglages)
    rendus[GREFFON + "/.claude-plugin/plugin.json"] = manifeste_copilot()
    rendus[GREFFON + "/hooks/hooks.json"] = greffon_copilot(reglages)
    ancien = ROOT / ".vscode" / "settings.json"
    rendus[".vscode/settings.json"] = fichier_vscode(
        reglages, ancien.read_text(encoding="utf-8") if ancien.exists() else None
    )
    return rendus


def perimes(attendus: dict[str, str]) -> Iterator[str]:
    """Fichiers générés dont la source a disparu — un sous-agent ou une skill supprimée.

    On ne touche à rien d'autre : une copie faite à la main garde la marque du fichier
    d'origine, dont la source existe toujours, et doit rester en place.
    """
    for chemin in sorted((ROOT / ".opencode").rglob("*.md")):
        nom = relatif(chemin)
        if nom in attendus:
            continue
        texte = chemin.read_text(encoding="utf-8", errors="ignore")
        trouve = re.search(re.escape(MARQUE) + r" (\S+)", texte)
        if trouve is not None and not (ROOT / trouve.group(1)).exists():
            yield nom


def ecrire() -> int:
    attendus = fichiers()
    ecrits = 0
    for nom, contenu in attendus.items():
        cible = ROOT / nom
        if cible.exists() and cible.read_text(encoding="utf-8") == contenu:
            continue
        cible.parent.mkdir(parents=True, exist_ok=True)
        cible.write_text(contenu, encoding="utf-8")
        print("  écrit    " + nom)
        ecrits += 1
    for nom in perimes(attendus):
        (ROOT / nom).unlink()
        print("  supprimé " + nom)
        ecrits += 1
    print(f"adaptateurs : {len(attendus)} fichier(s) à jour, {ecrits} modifié(s)")
    return 0


def verifier() -> int:
    attendus = fichiers()
    ecarts: list[str] = []
    for nom, contenu in attendus.items():
        cible = ROOT / nom
        if not cible.exists():
            ecarts.append(nom + " (manquant)")
        elif cible.read_text(encoding="utf-8") != contenu:
            ecarts.append(nom + " (modifié à la main ou source changée)")
    ecarts.extend(nom + " (périmé)" for nom in perimes(attendus))
    if ecarts:
        print(f"adaptateurs : {len(ecarts)} écart(s) avec .claude/")
        for ecart in ecarts:
            print("  " + ecart)
        print("corrigez avec : make adapters")
        return 1
    print(f"adaptateurs : {len(attendus)} fichier(s) conformes à .claude/")
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    commande = arguments[0] if arguments else ""
    if commande == "write":
        return ecrire()
    if commande == "check":
        return verifier()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
