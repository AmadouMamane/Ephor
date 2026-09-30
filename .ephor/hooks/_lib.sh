# shellcheck shell=bash
# Helpers partagés. Les hooks reçoivent un JSON sur stdin — mais pas le même JSON selon
# l'outil, et c'est le premier objet de ce fichier.
#
#   Claude Code   {"tool_name": "...", "tool_input": {"file_path": "...", ...}}
#   opencode      la même forme : le greffon .opencode/plugins/ephor.js la traduit
#   Copilot       {"toolName": "...", "toolArgs": <chaîne|objet>, "workingDirectory": "..."}
#                 (c'est ce que reçoit le greffon ; les hooks du dépôt, eux, reçoivent de
#                 la vue « agents » la forme Claude, où le correctif d'`apply_patch` arrive
#                 BRUT dans `tool_input` ; et si un LOT `toolCalls` arrive, le relais du
#                 greffon l'éclate en appels à la forme Copilot)
#
# Chez Copilot, trois pièges, mesurés en session réelle le 23 septembre 2026 :
# le nom de l'outil est en `toolName` (pas `tool_name`) ; il n'y a PAS de `tool_input` ;
# `toolArgs` est une CHAÎNE brute pour `apply_patch` (le correctif entier) et un OBJET
# pour les autres outils. Les chemins qu'il porte sont ABSOLUS.
#
# Le second objet de ce fichier, c'est de rendre un chemin COMPARABLE. Une relecture
# adversariale a montré qu'un détour d'un seul caractère suffisait à traverser tous les
# garde-fous : `dépôt/./.env` passait là où `dépôt/.env` était refusé, et sur un disque
# Mac (insensible à la casse) `.EPHOR/judge.py` écrit bien dans `.ephor/`. Comparer des
# chaînes brutes ne protège de rien. Tout chemin sort d'ici résolu : absolu, sans « . »,
# sans « .. », sans double barre, liens des dossiers suivis.
#
# Un garde-fou ne doit connaître aucune de ces formes : il appelle `outil`, `racine`,
# `cibles` et `corps`, et c'est tout.
#
# Le TROISIÈME objet de ce fichier : lire ce JSON sans dépendre de jq. Sur un poste
# d'entreprise, `brew install jq` n'est pas une option et l'ajout d'un binaire se demande.
# Or les gardes sortaient en « autorisé » dès que jq manquait : écrire dans les réglages
# d'Ephor et lire un `.env` passaient sans un mot, et le relais Copilot ne lançait plus
# AUCUNE garde. jq reste le moteur préféré (rapide, déjà là) ; python3 est le repli, et
# Ephor en dépend de toute façon partout ailleurs. Sans ni l'un ni l'autre, une garde de
# sécurité ne peut pas lire l'appel qu'elle doit juger : elle refuse.
INPUT="$(cat)"
# Git durci, comme dans le juge : aucun hook du dépôt, aucun moniteur de fichiers. Sans cela,
# un `core.fsmonitor` glissé dans .git/config était lancé seize fois à chaque fin de tour, par
# les hooks d'Ephor eux-mêmes, sans aucune demande d'autorisation (mesuré le 25/09/2026).
git() { command git -c core.fsmonitor=false -c core.hooksPath=/dev/null "$@"; }
if command -v jq >/dev/null 2>&1; then MOTEUR=jq
elif command -v python3 >/dev/null 2>&1; then MOTEUR=python3
else MOTEUR=aucun
fi
moteur_ok() { [ "$MOTEUR" != "aucun" ]; }
jq_ok() { [ "$MOTEUR" = "jq" ]; }   # jq SEUL : réservé à ce qui doit ÉCRIRE du JSON
deny() { # deny "raison" -> bloque l'appel d'outil
  # La seconde ligne n'est pas décorative. Le 24/09/2026, un agent refusé a écrit « comme
  # le terminal restait utilisable, j'ai basculé sur des commandes shell ». La garde du
  # terminal n'est pas une frontière : c'est la consigne qui doit arriver avec le refus.
  printf '%s\n%s\n' "$1" "Ne contourne pas ce refus par le terminal, un script ou un autre outil. S'il te semble injustifié, arrête-toi et dis-le à l'opérateur." >&2
  exit 2
}

# Les deux formes ramenées à des objets d'arguments. Les parenthèses comptent : chez jq,
# la virgule est plus lâche que le tube, donc `(a), (b) | f` ne veut pas dire `(a, b) | f`.
# Le test de type n'est pas décoratif : `.tool_input // {}` fait sortir jq en erreur quand
# `tool_input` est une chaîne, et TOUT le programme rend alors le vide, en silence.
_ARGS='((if (.tool_input | type) == "object" then .tool_input else {} end), (if (.toolArgs | type) == "object" then .toolArgs else {} end))'
# Champs qui portent un contenu écrit, tous outils confondus. `file_text` et `new_str` sont
# ceux du moteur de la fenêtre d'agents de VS Code (`create`, `edit`, `str_replace_editor`) :
# sans eux, un secret écrit par ces outils passait devant le contrôle sans être lu.
_ECRIT='.content, .new_string, .newString, .new_str, .file_text, .new_source, .patch, .patchText, .diff, .text, .code, (.edits[]?.new_string), (.edits[]?.newString), (.replacements[]? | objects | .newString, .new_string)'
# Champs qui portent un CORRECTIF, et eux seuls : un correctif nomme des fichiers, un
# contenu ordinaire n'en nomme pas. Confondre les deux faisait refuser l'écriture d'un
# PROGRESS.md qui citait un diff — l'agent ne pouvait plus résumer son état avant l'arrêt.
_CORRECTIF='.patch, .patchText, .diff, .input'
# Un correctif peut aussi arriver BRUT, comme valeur entière des arguments : `toolArgs`
# chez Copilot, et `tool_input` quand la fenêtre d'agents de VS Code passe l'appel aux hooks
# du dépôt, au format Claude (mesuré le 24/09/2026 : `apply_patch` vers .vscode/ passait, parce que seul
# `toolArgs` était lu brut — la même écriture faite avec `edit`, en objet, était refusée).
_BRUT='(.toolArgs, .tool_input) | strings'
# Champs qui portent un chemin. `paths` est une liste, ou une simple chaîne chez `rg` et
# `glob` de la fenêtre d'agents — lue comme une liste, la chaîne ne donnait rien.
# `replacements` et `files` : le panneau de discussion de VS Code (`multi_replace_string_in_file`,
# `editFiles`), dont les fichiers visés n'étaient pas lus (trouvé le 30/09/2026).
_VISE='.file_path, .notebook_path, .path, .filePath, .target_file, .targetFile, (.paths[]?), (.paths | strings), (.edits[]?.file_path), (.edits[]?.filePath), (.replacements[]? | objects | .filePath, .file_path), (.files[]? | strings)'
_CHAINES='map(select(type == "string" and . != ""))'

# Le même travail, en python3, quand jq n'est pas là. Une opération NOMMÉE de chaque côté :
# personne n'écrit un programme jq ailleurs que dans `_prog`, donc rien ne peut diverger
# en silence entre les deux moteurs.
_PY='
import json
import sys

ECRIT = ("content", "new_string", "newString", "new_str", "file_text", "new_source", "patch", "patchText", "diff", "text", "code")
CORRECTIF = ("patch", "patchText", "diff", "input")
VISE = ("file_path", "notebook_path", "path", "filePath", "target_file", "targetFile")


def charger(source):
    try:
        if source == "-":
            return json.load(sys.stdin)
        with open(source, encoding="utf-8") as fichier:
            return json.load(fichier)
    except Exception:
        return None


op = sys.argv[1]

if op in ("refus", "liste", "scalaire"):
    data = charger(sys.argv[2])
    if not isinstance(data, dict):
        sys.exit(1)
    if op == "refus":
        prefixe = sys.argv[3] + "("
        for regle in (data.get("permissions") or {}).get("deny") or []:
            if isinstance(regle, str) and regle.startswith(prefixe) and regle.endswith(")"):
                print(regle[len(prefixe):-1])
    elif op == "liste":
        for valeur in data.get(sys.argv[3]) or []:
            if isinstance(valeur, str):
                print(valeur)
    else:
        valeur = data.get(sys.argv[3])
        if isinstance(valeur, (str, int, float)) and not isinstance(valeur, bool):
            print(valeur)
    sys.exit(0)

charge = charger("-")
if not isinstance(charge, dict):
    sys.exit(1)
if op == "objet":
    sys.exit(0)


def texte(*cles):
    for cle in cles:
        valeur = charge.get(cle)
        if isinstance(valeur, str) and valeur:
            return valeur
    return ""


if op == "outil":
    print(texte("tool_name", "toolName"))
    sys.exit(0)
if op == "depot":
    print(texte("workingDirectory", "cwd"))
    sys.exit(0)
if op == "journal":
    print(texte("transcript_path", "transcriptPath"))
    sys.exit(0)
if op == "session":
    print(texte("session_id", "sessionId"))
    sys.exit(0)
if op == "evenement":
    print(texte("hook_event_name", "hookEventName"))
    sys.exit(0)

args = [a for a in (charge.get("tool_input"), charge.get("toolArgs")) if isinstance(a, dict)]
brut = [charge[c] for c in ("toolArgs", "tool_input") if isinstance(charge.get(c), str)]


def cueillir(cles, cles_edits, cles_remplacements=()):
    trouve = []
    for a in args:
        for cle in cles:
            valeur = a.get(cle)
            if isinstance(valeur, str) and valeur:
                trouve.append(valeur)
        for liste, sous_cles in (("edits", cles_edits), ("replacements", cles_remplacements)):
            elements = a.get(liste)
            for element in elements if isinstance(elements, list) else []:
                if isinstance(element, dict):
                    for cle in sous_cles:
                        valeur = element.get(cle)
                        if isinstance(valeur, str) and valeur:
                            trouve.append(valeur)
    return trouve


if op == "corps":
    edits = ("new_string", "newString")
    print("\n".join(cueillir(ECRIT + ("input",), edits, edits) + brut))
elif op == "correctif":
    print("\n".join(cueillir(CORRECTIF, ()) + brut))
elif op == "commande":
    print(next((a["command"] for a in args if isinstance(a.get("command"), str)), ""))
elif op == "cibles":
    vus = cueillir(VISE, ("file_path", "filePath"), ("filePath", "file_path"))
    for a in args:
        for cle in ("paths", "files"):
            chemins = a.get(cle)
            if isinstance(chemins, str) and cle == "paths":
                chemins = [chemins]
            for chemin in chemins if isinstance(chemins, list) else []:
                if isinstance(chemin, str) and chemin:
                    vus.append(chemin)
    print("\n".join(vus))
else:
    sys.exit(1)
'

_prog() { # le programme jq d'une opération nommée
  case "$1" in
    outil)     printf '%s' '.tool_name // .toolName // empty' ;;
    depot)     printf '%s' '.workingDirectory // .cwd // empty' ;;
    journal)   printf '%s' '.transcript_path // .transcriptPath // empty' ;;
    session)   printf '%s' '.session_id // .sessionId // empty' ;;
    evenement) printf '%s' '.hook_event_name // .hookEventName // empty' ;;
    corps)     printf '%s' "[$_ARGS | ($_ECRIT, .input)] + [$_BRUT] | $_CHAINES | join(\"\n\")" ;;
    correctif) printf '%s' "[$_ARGS | ($_CORRECTIF)] + [$_BRUT] | $_CHAINES | join(\"\n\")" ;;
    cibles)    printf '%s' "[$_ARGS | ($_VISE)] | $_CHAINES | .[]" ;;
    commande)  printf '%s' "[$_ARGS | .command] | $_CHAINES | .[0] // empty" ;;
  esac
}

_charge() { # _charge <opération> — la seule porte vers la charge reçue
  case "$MOTEUR" in
    jq)      printf '%s' "$INPUT" | jq -r "$(_prog "$1")" 2>/dev/null ;;
    python3) printf '%s' "$INPUT" | python3 -c "$_PY" "$1" 2>/dev/null ;;
    *)       return 1 ;;
  esac
}

charge_ok() { # la charge est-elle un objet JSON lisible ? (échec fermé des gardes strictes)
  case "$MOTEUR" in
    jq)      printf '%s' "$INPUT" | jq -e 'type == "object"' >/dev/null 2>&1 ;;
    python3) printf '%s' "$INPUT" | python3 -c "$_PY" objet >/dev/null 2>&1 ;;
    *)       return 1 ;;
  esac
}

field() { # un champ brut, en jq seulement : réservé au hook qui doit RENDRE du JSON
  printf '%s' "$INPUT" | jq -r "$1 // empty" 2>/dev/null
}

refus() { # refus <réglages> <Read|Edit> : les motifs refusés, un par ligne
  # « Read( » et « Edit( » font cinq caractères, d'où le découpage littéral, inchangé.
  case "$MOTEUR" in
    jq)      jq -r --arg p "$2(" '.permissions.deny[]? | select(startswith($p)) | .[5:-1]' "$1" 2>/dev/null ;;
    python3) python3 -c "$_PY" refus "$1" "$2" </dev/null 2>/dev/null ;;
    *)       return 1 ;;
  esac
}

liste() { # liste <fichier json> <clé> : les chaînes d'un tableau, une par ligne
  case "$MOTEUR" in
    jq)      jq -r --arg k "$2" '.[$k][]? | strings' "$1" 2>/dev/null ;;
    python3) python3 -c "$_PY" liste "$1" "$2" </dev/null 2>/dev/null ;;
    *)       return 1 ;;
  esac
}

scalaire() { # scalaire <fichier json | -> <clé> : une valeur simple, vide si absente
  case "$MOTEUR" in
    jq)      jq -r --arg k "$2" '.[$k] // empty' "$1" 2>/dev/null ;;
    python3) python3 -c "$_PY" scalaire "$1" "$2" 2>/dev/null ;;
    *)       return 1 ;;
  esac
}

# Les zones de contrôle. La liste qui fait foi vit dans le MOTEUR, `.ephor/zones.txt` : une
# défense ne lit pas sa politique dans un fichier que l'agent peut écrire (mesuré le
# 25/09/2026 : la sentinelle lisait ses zones dans .claude/settings.json, et un agent qui en
# retirait une règle l'aveuglait). Les réglages du projet peuvent en AJOUTER, par leurs refus
# `Edit(...)`, jamais en retirer.
ZONES_MOTEUR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)/zones.txt"

_entrees_zones() { # _entrees_zones <zones|exceptions> : les lignes utiles, « ~/ » développé
  [ -f "$ZONES_MOTEUR" ] || return 1
  local ligne
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    ligne="${ligne%$'\r'}"
    case "$ligne" in ""|"#"*) continue ;; esac
    case "$1:$ligne" in zones:"!"*|exceptions:[!!]*) continue ;; esac
    ligne="${ligne#!}"
    case "$ligne" in "~/"*) ligne="$HOME/${ligne#\~/}" ;; esac
    printf '%s\n' "$ligne"
  done < "$ZONES_MOTEUR"
}

zones_moteur() { # les zones du moteur, telles qu'écrites ; échoue sans la liste
  _entrees_zones zones
}

motifs_zones() { # motifs_zones <racine> [réglages] : un motif de `case` par ligne
  # Relatif : à comparer au chemin dans le dépôt. Absolu : à comparer au chemin entier.
  local entree dossier
  [ -f "$ZONES_MOTEUR" ] || return 1
  while IFS= read -r entree; do
    case "$entree" in */) printf '%s\n%s\n' "${entree%/}" "${entree}*" ;; *) printf '%s\n' "$entree" ;; esac
  done <<ZONES_LISTE
$(zones_moteur)
ZONES_LISTE
  # Le dossier git réel, s'il n'est pas `.git/` à la racine (arbre de travail secondaire).
  while IFS= read -r dossier; do
    [ -n "$dossier" ] || continue
    case "$dossier" in /*) ;; *) dossier="$1/$dossier" ;; esac
    dossier="$(resoudre "$dossier")"
    printf '%s\n%s\n' "$dossier" "$dossier/*"
  done <<GIT_LISTE
$(git -C "$1" rev-parse --absolute-git-dir --git-common-dir 2>/dev/null | sort -u)
GIT_LISTE
  if [ -n "${2:-}" ] && [ -f "$2" ]; then
    refus "$2" Edit | while IFS= read -r entree; do
      case "$entree" in ./*) entree="${entree#./}" ;; "~/"*) entree="$HOME/${entree#\~/}" ;; esac
      printf '%s\n' "$entree"
    done
  fi
  return 0
}

hors_zone() { # hors_zone <chemin absolu> : une exception de la liste (la mémoire de l'agent) ?
  local motif
  while IFS= read -r motif; do
    [ -n "$motif" ] || continue
    case "$motif" in */) motif="${motif}*" ;; esac
    # shellcheck disable=SC2254  # le motif est un glob, c'est voulu
    case "$1" in $motif) return 0 ;; esac
  done <<EXCEPTIONS_LISTE
$(_entrees_zones exceptions)
EXCEPTIONS_LISTE
  return 1
}

outil() { # le nom de l'outil appelé, vide si l'outil ne le donne pas
  _charge outil
}

# Les outils qui écrivent un fichier, sous les trois outils et les vues de Copilot. Un nom
# qui manquait laissait passer l'écriture : le panneau de discussion de VS Code écrit avec
# `create_file` et `replace_string_in_file` (sa doc), que ni la garde d'écriture ni celle du
# périmètre ne connaissaient (trouvé le 30/09/2026). Et un nom inconnu qui porte un correctif,
# ou un fichier visé AVEC un contenu écrit, écrit quand même : le nom d'un outil change d'un
# moteur à l'autre, la forme d'une écriture, non. Une lecture vise un fichier sans contenu.
ecriture() { # l'appel écrit-il un fichier ? (code de retour)
  case "$(outil)" in
    "" | Write | Edit | MultiEdit | NotebookEdit | write | edit | patch | apply_patch | multiedit) return 0 ;;
    createFile | editFiles | str_replace* | */editFiles | */createFile | create) return 0 ;;
    create_file | replace_string_in_file | multi_replace_string_in_file | insert_edit_into_file) return 0 ;;
    edit_notebook_file | create_new_jupyter_notebook) return 0 ;;
  esac
  [ -n "$(corps)" ] || return 1
  [ -n "$(correctif)" ] && return 0
  [ -n "$(_charge cibles)" ]
}

commande() { # la commande passée au terminal, vide si l'appel n'en porte pas
  _charge commande
}

_plier() { # plie « . », « .. » et les doubles barres d'un chemin absolu
  local morceau pile="" IFS=/
  for morceau in $1; do
    case "$morceau" in
      ""|.) ;;
      ..) pile="${pile%/*}" ;;
      *) pile="$pile/$morceau" ;;
    esac
  done
  printf '%s' "${pile:-/}"
}

resoudre() { # resoudre <chemin> [racine] -> chemin absolu comparable
  local chemin="$1" reste="" tete reel
  case "$chemin" in /*) ;; *) chemin="${2:-$PWD}/$chemin" ;; esac
  while [ "${chemin%/}" != "$chemin" ] && [ "$chemin" != "/" ]; do chemin="${chemin%/}"; done
  tete="$chemin"
  while [ -n "$tete" ]; do
    # `cd` fait le travail où il peut : il suit les liens et avale les détours.
    if reel="$(cd -- "$tete" 2>/dev/null && pwd -P)"; then
      # Ce qui reste n'existe pas encore — donc aucun lien à craindre : on le plie à la
      # main. Sans cela, `src/../ephor/x` restait tel quel quand `src/` n'existait pas,
      # et traversait toutes les listes de refus.
      printf '%s' "$(_plier "${reel%/}$reste")"
      return 0
    fi
    [ "$tete" = "/" ] && break
    reste="/${tete##*/}$reste"
    tete="${tete%/*}"
    [ -n "$tete" ] || tete="/"
  done
  printf '%s' "$(_plier "$chemin")"
}

racine() { # le dépôt de travail : réglage de l'outil, sinon la charge, sinon le dossier courant
  local depot="${CLAUDE_PROJECT_DIR:-}"
  [ -n "$depot" ] || depot="$(_charge depot)"
  [ -n "$depot" ] || depot="$(pwd)"
  resoudre "$depot" "$PWD"
}

# Tous les outils n'envoient pas le chemin dans un champ : `apply_patch` (Copilot) et
# `patch` (opencode) le portent DANS le correctif. Un garde-fou qui cherche `file_path`
# ne voit alors rien, et laisse passer. Ces fonctions sont la seule façon de lire une
# écriture, quel que soit l'outil.
corps() { # tout ce qui est écrit, quel que soit le champ qui le porte
  _charge corps
}

correctif() { # les correctifs seuls — ce dont on a le droit de tirer des noms de fichiers
  _charge correctif
}

_sans_guillemets() { # « "b/\056ephor/x" » -> « b/.ephor/x » : un nom écrit à la façon de git
  # Git met entre guillemets, échappé à la façon du C, un nom qui porte un caractère
  # inhabituel — et `git apply` accepte cette forme pour N'IMPORTE QUEL nom, octal compris :
  # `"b/\056ephor/zones.txt"` écrit bien .ephor/zones.txt. Lu tel quel, ce nom ne
  # ressemblait à aucune zone, et le correctif passait devant les gardes (mesuré le
  # 25/09/2026). En bash seul, comme le reste de cette lecture.
  local brut="$1" nom="" c o
  case "$brut" in
    \"*\") brut="${brut#\"}"; brut="${brut%\"}" ;;
    *) printf '%s' "$brut"; return 0 ;;
  esac
  while [ -n "$brut" ]; do
    c="${brut:0:1}"; brut="${brut:1}"
    if [ "$c" = "\\" ] && [ -n "$brut" ]; then
      c="${brut:0:1}"; brut="${brut:1}"
      case "$c" in
        [0-7]) o="$c${brut:0:2}"; brut="${brut:2}"; printf -v c "\\$o" ;;
        n) c=$'\n' ;; t) c=$'\t' ;; r) c=$'\r' ;; a) c=$'\a' ;; b) c=$'\b' ;; f) c=$'\f' ;; v) c=$'\v' ;;
      esac
    fi
    nom="$nom$c"
  done
  printf '%s' "$nom"
}

_nom_de_correctif() { # un nom lu dans un en-tête : sans date, sans guillemets, sans « a/ » ni « b/ »
  local nom="$1"
  nom="${nom%%$'\t'*}"
  nom="${nom%"${nom##*[![:space:]]}"}"
  nom="$(_sans_guillemets "$nom")"
  case "$nom" in a/*|b/*) nom="${nom#?/}" ;; esac
  [ -n "$nom" ] && [ "$nom" != /dev/null ] && printf '%s\n' "$nom"
  return 0
}

_noms_du_correctif() { # un correctif sur l'entrée -> un nom de fichier par ligne
  # En bash seul, sans awk ni sed : un outil absent rendait « aucun nom », et la garde
  # laissait passer (vu en test sur un PATH minimal, le 25/09/2026).
  local ligne precedente="" v4a=0 reste
  while IFS= read -r ligne || [ -n "$ligne" ]; do
    ligne="${ligne%$'\r'}"
    case "$ligne" in
      "*** Begin Patch"*) v4a=1 ;;
      "*** Add File: "*|"*** Update File: "*|"*** Delete File: "*) _nom_de_correctif "${ligne#*File: }" ;;
      "*** Move to:"*) reste="${ligne#"*** Move to:"}"; _nom_de_correctif "${reste#"${reste%%[![:space:]]*}"}" ;;
    esac
    if [ "$v4a" = 0 ]; then
      case "$ligne" in
        "+++ "*) case "$precedente" in "--- "*) _nom_de_correctif "${precedente#"--- "}"; _nom_de_correctif "${ligne#"+++ "}" ;; esac ;;
        "diff --git a/"*" b/"*) reste="${ligne#"diff --git a/"}"; _nom_de_correctif "${reste%% b/*}"; _nom_de_correctif "${reste#* b/}" ;;
        # Forme entre guillemets : un correctif binaire, ou un simple changement de droits,
        # ne nomme son fichier QUE là. Le premier nom suffit : un renommage a ses lignes.
        'diff --git "'*) reste="${ligne#diff --git }"; _nom_de_correctif "${reste%%\" *}\"" ;;
        "rename from "*|"rename to "*|"copy from "*|"copy to "*) _nom_de_correctif "${ligne#* * }" ;;
      esac
    fi
    precedente="$ligne"
  done
}

cibles() { # un chemin résolu par ligne, y compris ceux cachés dans un correctif
  local depot
  depot="$(racine)"
  {
    _charge cibles
    # Deux formats. Celui de Copilot et d'opencode (« *** Begin Patch ») nomme ses fichiers
    # en tête de section ; `Move to` est le renommage : sans lui, un correctif déplaçait un
    # fichier anodin vers une zone de contrôle sans que personne ne voie la destination.
    # Le format unifié (git, ou diff -u) les nomme par paires « --- avant » / « +++ après » :
    # seul « +++ b/ » était lu, donc une SUPPRESSION (« +++ /dev/null ») ne nommait rien et
    # passait, comme tout « diff -u » sans préfixe (mesuré le 25/09/2026). Les deux côtés de
    # la paire comptent, et les renommages de git aussi. Le format unifié n'est lu que hors
    # d'un « *** Begin Patch » : là, une ligne ôtée qui commence par « -- » n'est pas un nom.
    correctif | _noms_du_correctif
  } | while IFS= read -r brut; do
    [ -n "$brut" ] || continue
    resoudre "$brut" "$depot"
    printf '\n'
  done
}
