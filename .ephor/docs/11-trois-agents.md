# 11 — Trois agents, un seul dépôt

Claude Code, opencode et GitHub Copilot travaillent sur le même code. **Aucune garantie
ne dépend d'eux** : elles vivent dans `make verify`, le juge et la CI, qui rendent le même
verdict quel que soit l'outil. Ce chapitre décrit le confort de session que chaque outil
apporte, et ses limites.

## Une source, trois outils

`.claude/` fait foi. Le reste en est **dérivé** :

```bash
make adapters                     # régénère les fichiers des deux autres outils (opérateur)
python3 .ephor/adapters.py check  # échoue si un fichier dérivé a dérivé
```

Dans le dépôt d'Ephor, `make ephor-test` fait ce contrôle avec tous les autres. Dans un
projet équipé, les tests d'Ephor ne sont pas copiés : `make ephor-test` y dirait
« `.ephor/tests` absent », et c'est normal.

| Vous écrivez | Ce qui en est dérivé |
|---|---|
| `.claude/settings.json`, permissions | `.opencode/opencode.json`, `.vscode/settings.json` |
| `.claude/settings.json`, hooks | `.ephor/greffon-copilot/`, et `.github/hooks/ephor.json` |
| `.claude/agents/*.md` | `.opencode/agents/*.md` |
| `.claude/skills/*/SKILL.md` | `.opencode/commands/*.md` |

Trois fichiers ne sont pas générés, parce que ce sont du code : `.ephor/hooks/*.sh`, les
garde-fous ; `.ephor/opencode/ephor.js` (que `.opencode/plugins/ephor.js` importe) et
`.ephor/greffon-copilot/hooks/relais.sh`, qui se contentent de les appeler. Ces deux relais
n'analysent rien : ils traduisent la forme de leur outil vers celle de Claude Code, et
transmettent. Une seule logique, donc, et une seule série de tests.

Les trois outils n'appellent que **quatre points d'entrée**, un par moment du tour :
`avant-outil.sh`, `apres-outil.sh` (Claude Code seul), `debut-tour.sh`, `fin-tour.sh`.
Quelles gardes tournent, et pour quel outil (la variable `EPHOR_OUTIL`, posée par les
greffons), se décide dans ces scripts. Ajouter une garde ne touche ni les réglages du projet
ni le greffon installé sur les postes : la mise à jour du moteur suffit (ADR 0011).

## Ce que chaque outil lit tout seul

| | Claude Code | opencode | Copilot (VS Code) |
|---|---|---|---|
| Contrat de travail | `CLAUDE.md` → `AGENTS.md` | `AGENTS.md` | `AGENTS.md` et `.github/copilot-instructions.md` |
| Skills | `.claude/skills/` | `.claude/skills/`, nativement | `.claude/skills/`, nativement |
| Sous-agents | `.claude/agents/` | `.opencode/agents/`, générés | `.claude/agents/`, nativement |
| Commandes `/nom` | les skills | `.opencode/commands/`, générées | les skills |
| Garde-fous | `.claude/settings.json` | greffon `.ephor/opencode/ephor.js`, importé par `.opencode/plugins/ephor.js` | greffon installé (`make copilot-install`), et hooks du dépôt dans la vue « agents » |
| Lecture des secrets | règles `Read(...)` | permissions dérivées | `read-guard.sh`, qui relit ces mêmes règles |
| Écriture dans une zone de contrôle | règles `Edit(...)`, et `write-guard.sh` | permissions dérivées, et `write-guard.sh` via le greffon | `write-guard.sh` |
| Écriture dans un dépôt voisin | `refs-guard.sh` | le même, via le greffon | le même |
| Écriture d'une zone de contrôle **par le terminal**, forme simple (redirection, `sed -i`, `cp`, `mv`, `rm`, `git restore`, `cd` puis écriture, script en ligne ou en document en ligne, `find -delete`, `git apply`, formateur en place) | `shell-guard.sh` | le même, via le greffon | le même |
| Écriture d'une zone de contrôle par le terminal, **détour compris** (script écrit ailleurs, variable) | le bac à sable : refus du système (ADR 0008) | rien, sauf lancé dans `srt` (`opencode_bac_a_sable.sh`, à essayer) ; la sentinelle le signale en fin de tour | rien ; la sentinelle le signale en fin de tour |

Les zones de contrôle viennent d'une seule liste, dans le moteur : `.ephor/zones.txt`. Les
gardes, la sentinelle, le juge et la garde de la CI la lisent tous ; les refus `Edit(...)` des
réglages du projet peuvent l'allonger, jamais la raccourcir. Elle couvre aussi `.git/`, un
`GNUmakefile` (lu par make avant le `Makefile`), `.mcp.json`, et sur le poste les réglages des
agents, y compris la copie du greffon que VS Code exécute.

Copilot et opencode lisent déjà les skills du dépôt : il n'y a ni prompt files ni copie
des skills à entretenir.

## Ce qui diffère d'un outil à l'autre

- **opencode écrit sans demander** à l'intérieur du dépôt, sauf dans les zones de contrôle,
  qui sont refusées. Claude Code, lui, demande. Le filet reste le juge, qui refuse tout
  fichier hors du contrat.
- **Chez VS Code, `false` veut dire « demande »**, pas « refuse ». Ce qui bloque vraiment,
  ce sont les hooks — d'où `chat.useClaudeHooks` à `true` dans `.vscode/settings.json`, et
  d'où `write-guard.sh`, qui refuse une zone de contrôle comme sous les deux autres outils.
  VS Code ignore aussi le champ `matcher` : chaque garde-fou tourne à chaque appel d'outil,
  et sort sans rien faire quand l'appel ne le concerne pas.
- **La lecture hors du dépôt** est une demande, pas un refus, chez opencode : `~/.aws`,
  `~/.ssh`, `~/.kube` et gcloud sont refusés dossier par dossier, mais un fichier isolé
  comme `~/.netrc` ne peut pas être exprimé — opencode demandera.
- **La compaction des sorties** n'existe que sous Claude Code, et ne touche que des
  commandes **déjà autorisées** dans `.claude/settings.json` : le hook rend une décision
  « allow », il ne doit donc jamais approuver ce que l'opérateur n'a pas approuvé. Chez
  opencode, une sortie d'outil est coupée à 600 lignes ou 50 ko et le reste part sur
  disque ; c'est un plafond, pas un résumé.
- **Le périmètre du contrat** est refusé en direct sous les trois outils (même script ;
  sous Copilot, par le greffon ou par les hooks du dépôt).
  **Le plafond de tokens** s'applique pleinement sous Claude Code, qui écrit les tokens
  de chaque requête. Copilot ne les donne qu'à la fermeture de la session : d'ici là,
  Ephor rapporte ce qu'il facture (AIU, requêtes premium) et un minimum de tokens, qui
  n'arrête l'agent que s'il dépasse déjà le plafond. opencode n'a pas de plafond par tâche.
  Sous Claude Code, les sous-agents comptent : chacun écrit son propre journal, lu avec
  celui de la session (un tiers d'une vraie session y était invisible, le 25/09/2026).
- **Le plafond d'une réponse.** opencode coupe chaque réponse à 32 000 tokens, réflexion
  comprise, quel que soit le modèle (défaut connu, ouvert :
  <https://github.com/anomalyco/opencode/issues/29363>). Quand la réflexion le remplit, le
  tour finit sans réponse et sans erreur. Mesuré le 28/09/2026 sur le `planner` (Sonnet 5 via
  Copilot) : quatre échecs, au plus haut niveau d'effort et même en `high`, chacun terminé à
  32 000 tokens de réflexion et 0 de réponse, après 5 à 17 minutes ; l'agent qui l'appelait
  recevait du vide et concluait que le planner « n'était fiable que sur les demandes
  triviales ». L'agent principal y échappe presque toujours : il avance par petites
  réponses, le planner doit tout rendre en une seule. Le greffon relève
  donc le plafond de chaque requête jusqu'à la limite du modèle, sans dépasser le quart de
  sa mémoire (pour Sonnet 5 via Copilot : 128 000) : le même planner, au plus haut niveau, a
  répondu en 132 secondes, 12 955 tokens de réflexion, sans erreur. Un poste qui fixe
  `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX` garde son choix. Si une réponse est encore coupée,
  opencode ne dit rien, le greffon si : un message à l'écran, et l'agent qui a délégué reçoit
  la raison avec la réponse vide. Relever le plafond ne touche pas au moment où opencode
  compacte la conversation (lu dans son code, 1.18.32). Sa branche v2 abandonne ce plafond.
  **En effort max, la réflexion garde sa place, la réponse aussi.** Pour un modèle Claude
  servi par Copilot, opencode donne à la réflexion le budget maximal annoncé moins un token,
  et sa bibliothèque ramène « réflexion + réponse » à la limite du modèle : quand la limite
  vaut ce budget, la réponse n'a plus qu'un token (lu dans son code, 1.18.33 ; #27). Le
  greffon laisse donc toujours 16 000 tokens à la réponse, la réflexion prend le reste, et les
  deux ne dépassent jamais la limite du modèle. L'effort demandé ne change pas.
  Claude Code demande la limite du modèle quand il le reconnaît, et 32 000 sinon, par exemple
  derrière une passerelle qui renomme les modèles : `CLAUDE_CODE_MAX_OUTPUT_TOKENS` le relève.
- **L'alerte de contexte.** La règle « contexte au-delà de 60 % : `/handoff`, puis une
  nouvelle session » n'était appliquée par personne : l'agent ne voit pas son remplissage.
  À chaque fin de tour, `contexte.sh` le dit à l'opérateur, sans rien vider lui-même (vider
  avant que l'état soit écrit perdrait du travail). opencode : un message à l'écran, calculé
  comme le pourcentage qu'il affiche. Claude Code : un message sous la réponse ; ses hooks ne
  connaissent pas la taille de la mémoire du modèle, Ephor la suppose (200 000, ou un million
  dès que la conversation dépasse 200 000) et le dit ; `EPHOR_CONTEXTE_TOKENS` la déclare.
  Copilot ne donne ses tokens qu'à la fermeture de la session : pas d'alerte.
- **La sentinelle** regarde le résultat, pas le moyen : au début du tour, elle photographie
  les zones de contrôle et les branches ; à la fin, une zone changée pendant le tour, par
  quelque chemin que ce soit — sur le disque, dans un commit du tour, ou en déplaçant la
  branche principale —, pose `AGENT_STOP` avec la liste des fichiers. Changer de branche ou
  rebaser, sans rien toucher aux zones, ne la fait pas crier. Sous les trois outils : Claude
  Code, Copilot (vue « agents » et greffon), et opencode depuis 1.1.5, par son greffon (le
  message de l'opérateur ouvre le tour, la session redevenue inactive le ferme). Avant, un
  script qui écrivait dans `tasks/` n'y était vu par personne (constaté le 29/09/2026).
  Elle lit ses zones dans le moteur, voit les fichiers ignorés par git, le dossier git, un
  réglage local qui coupe les hooks ou le bac à sable, et une dépendance ou un script ajouté
  à `.opencode/package.json`, qu'opencode installerait au démarrage suivant (le fichier, qu'il
  écrit lui-même, n'est pas comparé ; ses fichiers de verrou non plus). Claude Code ne lance pas la fin de
  tour quand on interrompt l'agent (mesuré le 25/09/2026) : un tour jamais contrôlé l'est
  au début du suivant, et la demande est refusée si une zone a changé. Elle constate après
  coup, et un agent qui veut la faire taire y arrive encore ; le juge reste la barrière.
- **Les points de reprise** : les trois outils les font dans une référence Git privée
  (`make checkpoints`), à la fin de chaque tour — sous Copilot, vu tirer dans la vue
  « agents ». opencode garde en plus les siens (`/undo`).
- **La branche principale n'est écrite que par l'opérateur.** Le commit d'un contrat sur
  cette branche est l'approbation, et le juge la lit là. La garde du terminal refuse donc à
  l'agent d'y écrire l'histoire (`git commit`, `merge`, `cherry-pick`…), y compris après un
  `git switch main` dans la même commande. C'est un filet, sous les trois outils ; en CI, la
  protection de la branche fait foi.
- **opencode installe ses dépendances de greffon dans le dépôt** (`.opencode/node_modules`,
  une soixantaine de mégaoctets). Il les ignore lui-même dans Git ; `.gitignore` le refait,
  par sécurité, pour que le juge ne les voie jamais.

## opencode dans le bac à sable du système (à essayer sur le poste)

Sous Claude Code, le bac à sable du système refuse d'écrire une zone de contrôle, détour
compris. opencode n'en a pas, mais le moteur de ce bac à sable existe seul : `srt`
(`npm install -g @anthropic-ai/sandbox-runtime`). Lancez opencode par
`bash .ephor/scripts/opencode_bac_a_sable.sh` (arguments d'opencode en plus) : ses réglages
viennent de la liste des zones du moteur, et tout ce qu'opencode lance ne peut plus écrire
une zone ni lire un `.env`.

Mesuré le 30/09/2026 (opencode 1.18.33, srt 0.0.78, faux modèle local, greffon d'Ephor
chargé) : le modèle fait écrire `src/porte.py` à opencode (accepté), puis le lui fait lancer ;
le script tente d'écrire dans `tasks/` et reçoit `PermissionError: Operation not permitted`.
La garde du terminal laissait passer ce détour ; le système, non. Les commits, le code du
projet, les dépendances du greffon s'écrivent.

Réseau : fermé, sauf les domaines de `EPHOR_SRT_DOMAINES` (par défaut ceux de Copilot, du
catalogue d'opencode, de npm et de PyPI) ; derrière un proxy, srt passe par celui de
`HTTPS_PROXY`. Une passerelle sur le poste (127.0.0.1) : `EPHOR_SRT_LOCAL=1`, qui ouvre tous
les ports locaux. Limite vue : opencode ne suit plus les changements de fichiers (« Error
starting FSEvents stream »), sans effet sur le travail. Pas encore essayé derrière le proxy de
l'entreprise, ni avec le vrai fournisseur : c'est le ticket #26.

## Copilot : comment les garde-fous tournent

**Un geste de plus, sur le poste et non dans le dépôt :**

```bash
make copilot-install   # une fois par machine, puis quand le journal des versions le dit
```

puis une **fenêtre VS Code neuve** — pas un rechargement : une session ouverte avant
l'installation ne voit pas le greffon, et rien ne le dit.

Copilot a deux chemins vers les garde-fous d'Ephor :

| Chemin | Où | Quand il tourne |
|---|---|---|
| **Le greffon** | `~/.copilot/installed-plugins/ephor/`, posé par `make copilot-install` | dans les deux vues, dès qu'il est installé et la fenêtre neuve |
| **Les hooks du dépôt** | `.github/hooks/ephor.json`, généré par `make adapters` | dans la vue « agents » seulement ; le panneau de discussion ne les lançait pas au dernier essai |

Dans la vue « agents », les hooks du dépôt passent **avant** le greffon, avec une charge
au format Claude (`session_id`, `tool_input`) ; s'ils refusent, le greffon n'est pas
appelé. Le greffon, lui, reçoit chaque appel seul, à la forme propre à Copilot
(`toolName`, `toolArgs`, `workingDirectory`). Les garde-fous lisent les deux formes, au
même endroit : `.ephor/hooks/_lib.sh`.

Le greffon ne contient aucune règle : il retrouve le dépôt ouvert et rejoue l'appel vers
les garde-fous de CE dépôt. Il ne le fait que pour les dépôts inscrits dans `depots.txt`,
à côté de lui — `make copilot-install` y ajoute celui d'où il tourne. Un greffon est actif
pour **tous** les dossiers que Copilot ouvre : lancer les scripts d'un dépôt inconnu, ce
serait exécuter le code d'un inconnu à chaque appel d'outil. C'est la seule entorse à
« tout vit dans le dépôt », et elle est subie, pas choisie.

**VS Code garde sa propre copie du greffon**, dans
`~/Library/Application Support/Code/agentPlugins/`, et ne la refait que si la date du
dossier `hooks/` du greffon change. `make copilot-install` date donc ce dossier à chaque
installation ; VS Code resynchronise dans la seconde. Les copies d'anciens greffons qui
traînent dans ce dossier ne tournent plus.

### Ce qui est refusé, et comment

- **Un refus bloque, sans bouton d'autorisation**, dans le panneau de discussion comme
  dans la vue « agents », zones de contrôle comprises. L'agent annonce le refus et
  s'arrête. Ce n'est vrai que parce que `chat.tools.edits.autoApprove` approuve tout
  d'avance (`"**/*": true`) **sans exception** : une zone de contrôle marquée `false`
  ferait poser une question, et dans la vue « agents » cette question devient un bouton
  qui lève le refus. Le générateur n'écrit donc aucune exception.
- **Le bouton d'arrêt** (`AGENT_STOP`) refuse dès la demande, avant tout appel d'outil.
- **Un correctif est lu où qu'il soit** — brut dans `toolArgs` ou `tool_input`, ou dans un
  champ `patch`, `diff`, `input` — et tous les fichiers qu'il nomme sont examinés. Un appel
  qui porte un correctif est jugé comme une écriture, **quel que soit le nom de l'outil**.
- **Les outils propres à la vue « agents »** sont connus : `create` (`path`, `file_text`),
  `edit` (`path`, `old_str`, `new_str`), `str_replace_editor`, `view`, `rg` et `glob`
  (`paths`, liste ou simple chaîne), `grep`, `bash`, `apply_patch`.
- **Ceux du panneau de discussion aussi** : il lit avec `read_file`, et écrit avec
  `create_file`, `replace_string_in_file`, `multi_replace_string_in_file` (une liste
  `replacements`), `insert_edit_into_file` (le code dans `code`). Ces noms d'écriture
  n'étaient pas connus avant la 1.3 : une zone de contrôle s'y écrivait sans refus en direct
  (trouvé le 30/09/2026, d'après la doc de VS Code, pas encore vu en session). Et un outil au
  nom inconnu qui vise un fichier **avec** un contenu écrit est jugé comme une écriture.
- **Le terminal** : `shell-guard.sh` lit la commande, nomme ce qu'elle écrit et refuse si
  c'est une zone de contrôle, formes simples comprises (`cd` puis écriture, même d'un appel
  à l'autre, script en document en ligne, `find -delete`, `git apply`, commandes sur
  plusieurs lignes, blocs `if`/`for`, `git -C`, substitutions, variables posées dans la
  commande elle-même). **Ce n'est pas une frontière** : un détour par une variable calculée,
  du base64 ou un script écrit ailleurs passe, et Copilot n'a pas de bac à sable mesuré. La
  sentinelle le signale en fin de tour ; le juge reste la barrière. Sans python3, il refuse
  toute commande qui nomme une zone de contrôle.
- **Une forme d'appel inconnue est refusée** par le relais du greffon. Un lot d'appels
  (`toolCalls` ou `tool_calls`) est éclaté, et chaque appel passe toutes les gardes. Quand
  l'éditeur changera de protocole, le filet se verra tomber au lieu de disparaître.
- **Les gardes d'un appel tournent en même temps** (`.ephor/hooks/ensemble.sh`), avec
  le verdict et les messages qu'elles rendraient l'une après l'autre ; depuis la 1.4, le
  point d'entrée ne lance que celles qui regardent l'appel (une lecture : le bouton d'arrêt
  et la garde de lecture ; une écriture : les cinq de l'écriture). Mesuré le 30/09/2026,
  poste rapide : un appel passe de 0,48 à 0,69 s à 0,25 à 0,33 s ; sous opencode, une
  écriture de 0,45 à 0,22 s. Une garde qui n'a pas rendu son verdict en 20 s
  (`EPHOR_GARDES_DELAI`) est arrêtée, et compte pour un refus. `EPHOR_GARDES_EN_SERIE=1`
  remet la série, même verdict, pour un poste où lancer plusieurs processus à la fois pose
  problème : dans l'environnement de VS Code pour Copilot (`launchctl setenv
  EPHOR_GARDES_EN_SERIE 1`, puis relancer VS Code), dans le terminal qui lance opencode.
  Claude Code lance déjà ses hooks ensemble : rien ne change pour lui. Les hooks du dépôt
  (vue « agents ») passent par le même point d'entrée.

### Limites connues

- **Poste sans greffon.** Faute d'exception dans `.vscode/settings.json`, VS Code ne
  demande plus rien avant d'écrire une zone de contrôle. La vue « agents » reste gardée par
  les hooks du dépôt ; ailleurs, seuls le juge et la CI refusent encore. `make doctor` dit
  si le greffon manque, s'il est périmé, ou si ce dépôt n'y est pas déclaré. D'où la règle :
  **installer le greffon avant d'ouvrir Copilot sur un dépôt, puis une fenêtre neuve.**
- **Un hook qui dépasse son délai laisse passer l'appel**, sous Copilot comme sous Claude
  Code (« A timed-out command hook doesn't block the tool call », doc des hooks de Claude
  Code). C'est le comportement des moteurs, pas un choix d'Ephor. Le greffon de Copilot et
  celui d'opencode arrêtent une garde muette au bout de 20 s et refusent, bien avant ce
  délai ; restent les hooks du dépôt (vue « agents ») et Claude Code. Mesuré : une commande
  de 2 Mo est jugée en 0,5 s, loin des délais (10 à 30 s) ; le risque est faible, et le juge
  reste la barrière.
- **Copilot refuse souvent de lui-même**, avant d'appeler le moindre outil. C'est
  appréciable et ce n'est pas une garantie ; surtout, ça brouille les essais : pour éprouver
  un garde-fou, il faut une demande que l'agent n'a aucune raison de refuser seul.
- **Compte Copilot d'entreprise : non essayé.** Les garde-fous sont marqués « aperçu » dans
  l'éditeur, et une stratégie d'entreprise peut les activer ou les couper.

## Vérifier après une mise à jour de l'éditeur

Le protocole en cinq minutes est dans `.ephor/docs/10-installation.md`, « Vérifier
Copilot, une fois, en cinq minutes ». Si un bouton d'autorisation réapparaît sur une zone de
contrôle, c'est une régression : le refus se fait battre, et la question se rouvre.

Pour savoir ce qui se passe vraiment, sans rien deviner :

| Question | Où regarder |
|---|---|
| Qu'a chargé l'éditeur ? Quels outils a-t-il lancés ? | `~/Library/Application Support/Code/logs/<session>/window*/customizationsDebug.log`, et `agenthost.log` (`Tool started: <nom>`) |
| Le greffon est-il appelé, et avec quoi ? | `touch .verify/trace-copilot` dans le dépôt : chaque charge reçue va dans `.verify/copilot-charges.jsonl` |
| Qui a prononcé un refus ? | `touch ~/.copilot/installed-plugins/ephor/trace` : chaque appel au greffon est noté dans `trace.jsonl`, à côté, avec son code et son message. Un refus absent de ce fichier vient des hooks du dépôt |
| Qu'a fait l'agent dans la vue « agents » ? | `~/.copilot/session-state/<session>/events.jsonl` — attention, la charge qu'il montre est celle d'avant traduction |

À éteindre après usage : ces traces contiennent les demandes de l'opérateur.

## Ce qui a été mesuré

Tout ce chapitre repose sur des sessions réelles, du 22 au 25 septembre 2026 : VS Code
1.139.0, opencode 1.18.32, Claude Code 2.1.282. Chaque constat est tenu depuis par
`make ephor-test`.

| Constat | Ce qui le tient |
|---|---|
| opencode charge la config, les cinq sous-agents, leurs permissions, et lit les skills du dépôt sans réglage ; un refus coupe l'appel d'outil | adaptateurs dérivés de `.claude/` (ADR 0003) |
| Copilot charge `AGENTS.md`, les sous-agents et les skills ; il trouve les hooks du dépôt, mais ne les lance que dans la vue « agents » | greffon au format Claude, posé sur le poste par `make copilot-install` |
| Une fenêtre ouverte avant l'installation du greffon ne le voit pas, et rien ne le dit | règle de la fenêtre neuve ; `make doctor` contrôle le greffon |
| Copilot envoie ses appels sous deux formes (la sienne, et celle de Claude dans la vue « agents »), parfois par lots, avec un correctif parfois brut | les deux formes lues dans `_lib.sh`, lots éclatés, tout appel qui porte un correctif jugé comme une écriture, forme inconnue refusée |
| Dans la vue « agents », une zone de contrôle marquée « demander » devenait un bouton qui levait le refus | aucune exception dans `chat.tools.edits.autoApprove` ; remesuré : plus de bouton, fichier intact |
| Un agent refusé propose de passer par le terminal (`python3 -c`) | `shell-guard.sh`, la sentinelle, et sous Claude Code le bac à sable (ADR 0008) |
| VS Code garde sa copie du greffon tant que la date du dossier ne change pas | `make copilot-install` date le dossier |
| Copilot ne donne ses tokens qu'à la fermeture de la session | minimum signalé comme tel, facturé rapporté, journaux relus par `make usage` et `make judge` |
| Une déclaration de dépôts de référence qui citait une variable non exportée bloquait toute écriture ; `read_file` n'était pas examiné pour les secrets | écriture dans le dépôt de travail toujours permise, variable nommée par `make doctor`, `read_file` et `list_dir` examinés |
| Claude Code ne lance pas la fin de tour d'un tour interrompu | la sentinelle contrôle au début du tour suivant |
| Les sous-agents de Claude Code échappaient au plafond de tokens | leurs journaux sont comptés |
| Un nom de fichier entre guillemets, échappé en octal (`"b/\056ephor/zones.txt"`), passait devant les gardes | les gardes lisent le nom réel |

## Pour changer quelque chose

1. Modifiez **`.claude/`**, jamais un fichier dérivé (le juge et la CI les refusent).
2. `make adapters`, puis `python3 .ephor/adapters.py check` (dans le dépôt d'Ephor :
   `make ephor-test`, qui le fait aussi).
3. Si un outil change de format, c'est `.ephor/adapters.py` qu'on corrige — un seul endroit.
