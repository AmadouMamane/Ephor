# Journal des versions d'Ephor

Une entrée par version : ce qui change pour qui s'en sert. Les raisons de fond sont dans
`.ephor/docs/adr/`. La version en place est dans `.ephor/VERSION`.

## 1.5.2 — 1er octobre 2026

Chaque fonctionnalité de la 1.5.1 évaluée de nouveau, par trois relectures adverses menées
dans des dépôts jetables (juge et boucle de tâche, garde-fous, installation et outils), et,
pour la première fois dans ce conteneur, le juge sous Docker : la démonstration y rend ses
quatre verdicts justes. Chaque défaut a été rejoué, puis fermé par un test qui échoue sans
son correctif (991 tests d'Ephor avant, 1086 après). Les tickets #36, #37 et #38 sont fermés.

- **Le juge lit l'approbation d'aujourd'hui.** Il lisait contrat, preuves et contrôle rouge
  au point de départ de la branche : une preuve resserrée ensuite sur la branche principale
  ne comptait pas, et un correctif à moitié juste ressortait VÉRIFIÉ, en local comme contre
  `origin/main` ; une tâche retirée de main aussi. Il refuse maintenant, et demande
  d'intégrer la branche principale (`git merge main`).
- **Une preuve nommée par son module est protégée.** `{python} -m unittest tests.test_x`,
  ou `pytest --pyargs tests.test_x`, avec `tests/` dans le périmètre : l'agent vidait le
  test, et VÉRIFIÉ. Le module est lu comme son fichier ; `python -m app.cli` reste le
  programme essayé.
- **Les autres trous du juge.** `--base origin/task/<ID>` (la branche de l'agent, poussée)
  faisait lire « approuvé » le contrat qu'il s'y était écrit. En risque élevé, `# ruff: noqa`,
  `# flake8: noqa`, `# pyright: ignore`, un `@skip` de unittest, `self.skipTest`, `it.skip`,
  `t.Skip`, `@Disabled` passaient ; une ligne `-- a` puis `++ b/README.md`, dans un bloc, se
  lisait comme l'en-tête d'un fichier texte et cachait le `# noqa` suivant ; un greffon pytest
  déclaré en clé pointée sous `[project]` passait pour la fiche du paquet. Un critère nommé
  `verify`, `setup` ou `pull` écrasait le journal de l'étape du même nom.
- **`make review` ne lance plus le code de l'agent avec vos droits.** Dans le dossier de la
  tâche, il lançait le verify.sh, le Makefile et le juge que l'agent avait pu retoucher :
  « 2 étape(s) sur 2 vertes » avec un juge réécrit. Une zone retouchée arrête la revue avant
  de rien lancer, et le juge est celui de votre copie. Une vérification « GREEN PARTIEL »
  n'est plus comptée verte.
- **`make taches` et la fermeture d'une tâche.** Une fusion annulée par `git revert` laissait
  la tâche « fusionnée » pour toujours (#36) ; une fusion par squash, le bouton par défaut de
  GitHub, ne la faisait jamais fusionnée. `make task-ranger` ne voyait pas un contrat qui
  cite le dossier entier d'une tâche (`tasks/PAY-001`, sans barre). `make task-clore`, lancé
  depuis le dossier de la tâche, se retirait lui-même ; il refuse, et donne la ligne à lancer.
  `make mutants` suit la branche que le serveur désigne (`develop`).
- **La garde du terminal.** Faux refus : `2>&1` ou `2>/dev/null` après un `cd` dans une zone
  (le descripteur lu comme un fichier), un mot qui ressemble à une zone (`tasks = []`,
  `.gitignore`, `"$TMPDIR/tasks.log"`), `( cd tasks && ls ) && echo x > notes.txt`,
  `ls # > Makefile`, `git reset HEAD src/a.py` sur main, et, sans python3, tout nom qui
  contenait celui d'une zone. Écritures qui passaient : une commande de plus de 128 Kio
  (python3 ne démarrait pas, et tout passait), `exec 3<> Makefile`, `( cd src && make ) &&
  rm -f Makefile`, `open(os.path.join('tasks', …), 'w')`, et ce que développent un joker ou
  un chemin large : `sed -i … *.md`, `find . -name Makefile -delete`, `git restore .`,
  `git checkout -- '*.md'`, `git stash` et `git reset --hard` (qui effaçaient la consigne
  en cours d'écriture), `cat fix.patch | git apply` ; à la lecture, `cat .env*` et
  `cat < .env`.
- **Les autres gardes.** `make stop`, posé dans votre copie, n'arrêtait pas l'agent du
  dossier d'une tâche. Les outils MCP de fichiers (`edit_file`, `move_file`) n'étaient pas
  lus comme des écritures ; sous jq, un élément de liste qui n'était pas un objet faisait
  tout taire. Des arguments d'outil en chaîne JSON (Copilot CLI) sont lus. Sous Copilot, le
  dossier d'une tâche était bloqué en entier, `read_file` compris : il est jugé par les
  gardes de la copie déclarée. La sentinelle nomme une dépendance ou un script glissé dans
  `.opencode/package.json`, qu'opencode installerait au démarrage suivant (#38).
- **Installation et mise à jour.** La configuration opencode du projet était écrasée
  (modèle, serveurs MCP, ses propres sous-agents et commandes) : ses clés restent, et un
  fichier qui n'est pas d'Ephor reçoit un `.ephor-new`. Un hook propre au projet devenait
  une commande cassée sous Copilot. Un en-tête YAML en bloc (`description: >-`) faisait
  tomber `make adapters`, donc l'installation. Ce qu'Ephor ne livre plus hors du moteur
  (un sous-agent, une skill) est nommé avec la commande pour le retirer (#37), et une skill
  arrive entière. Un rôle dont le projet a réglé `model:` n'est plus annoncé « remplacé » à
  chaque mise à jour ; un `PROGRESS.md` déjà là ne reçoit plus de `.ephor-new`.
- **Le diagnostic** taisait le mot de passe du proxy (il l'affichait en clair), et concluait
  « Rien ne bloque » sur un dépôt dont les réglages ne déclarent aucune garde : c'est un
  échec, avec le geste à faire.
- **Les outils.** `make map REPO=<nom>` écrivait son cache chez le dépôt de référence, censé
  rester en lecture seule. `make score` et `make usage` tombaient sur `NaN`, `1e999` ou un
  appel d'outil abîmé ; le verdict perdait le « au moins » des sous-agents. Une référence
  dans un sous-dossier de monorepo a son commit ; `make gitlab-check` lit un remote SSH sans
  utilisateur et dit une coupure réseau sans trace ; `make verify` ne renvoie plus un projet
  non Python vers `make setup`.
- **Un projet né de l'archive garde sa CI verte** quand il retouche son Makefile : les deux
  tests d'intégrité d'une livraison ne tournent que dans le dépôt d'Ephor (`.depot-ephor`,
  qui ne part pas dans l'archive).

**Ce qui peut changer pour vous.** Une tâche retouchée sur la branche principale après le
départ de sa branche demande `git merge main` avant le juge. Un critère nommé `verify`,
`setup` ou `pull`, ou deux critères qui ne diffèrent que par la casse, sont à renommer. En
risque élevé, les nouveaux silencieux reconnus sont refusés comme les autres. `make
task-clore` se lance depuis votre copie.

**Après la mise à jour d'un projet** : `make adapters` (la configuration opencode est
maintenant fusionnée), et `make copilot-install` sur chaque poste qui travaille sous Copilot
(le relais a changé), puis une fenêtre VS Code neuve.

**Limites connues, gardées.** Une branche principale qui ne s'appelle ni `main` ni `master`
doit être désignée par le dépôt distant (`origin/HEAD`). `rg -l x | xargs sed -i …` n'est pas
lu par la garde du terminal (le juge reste la barrière). `.env.example` reste refusé à la
lecture par `Read(**/.env.*)`. Le juge sous Podman, opencode et Copilot en session réelle
restent à voir sur le poste (#39).

## 1.5.1 — 1er octobre 2026

Chaque fonctionnalité de la 1.5.0 évaluée de nouveau : la boucle d'une tâche rejouée de bout
en bout dans un projet neuf (approbation, dossier de la tâche, juge, revue, fusion,
fermeture, score), les outils annexes lancés un à un, et trois relectures adverses (juge,
garde-fous, mesure et installation), menées dans des dépôts jetables. Chaque défaut a été
rejoué sur la 1.5.0, puis fermé par un test qui échoue sans son correctif. Une version de
correctifs : rien ne change dans ce que le projet écrit ou règle.

- **La garde du terminal attrape les formes simples qui passaient encore.** Un saut de
  ligne ne séparait pas deux commandes : `echo x` puis `rm -rf .ephor` sur la ligne
  suivante, ou `git switch main` puis `git commit` (l'approbation, que le bac à sable de
  Claude Code ne couvre pas), passaient. Aussi : le reste de la ligne d'un document en ligne
  (`cat <<EOF > tasks/…/contract.json`, `| tee Makefile`, `| git commit -F -`), les blocs du
  shell (`if … then rm …`, `for … do sed -i …`, `{ … }`, `!`), les préfixes à options
  (`sudo -n`, `env -u X`, `timeout 30`, `nice -n 5`), les substitutions entre guillemets ou
  par accents graves, `python3 <<< "…"`, `sed -ri`, `find -L`, une ligne continuée, et les
  variables posées dans la commande même (`for f in tasks/*; do …"$f"`, `X=.ephor; rm $X`).
  Le dossier courant de la session compte (`cd .ephor` à un appel, `sed -i` au suivant),
  comme `git -C <dossier>` et `git --no-pager` ; les liens symboliques sont suivis ; et du
  dossier d'une tâche, la copie principale (ses zones, son `main`) est gardée elle aussi. À
  l'inverse, un autre dépôt n'est plus pris pour celui-ci : `cd /tmp/x && git config …` ou
  `git -C /tmp/x commit` sur son `main` étaient refusés (vu en session), comme `bash -n`,
  qui ne lance rien.
- **Les autres gardes.** Sous `AGENT_STOP`, seul un outil d'édition du dossier écrit
  `PROGRESS.md` : un appel MCP (GitHub) ou un terminal qui portait `"path": "PROGRESS.md"`
  passait. Sous Copilot, une recherche qui vise un secret par son motif de fichiers
  (`grep_search` et `includePattern: "**/.env"`) est refusée. Un lien vers un fichier de
  zone (`src/lien -> ../Makefile`) ne passe plus la garde d'écriture. Un nom d'outil vide
  se lit pareil sous jq et sous python3.
- **`make judge` et `make review` lancés du dossier principal jugent la tâche dans son
  dossier.** Depuis « une tâche, un dossier », ils y rendaient « rien à juger », la revue
  vérifiait la branche principale, et ce verdict plus récent refaisait la tâche « en cours »
  dans `make taches`. Sans dossier à part, le verdict nomme la branche `task/<ID>`.
- **`make task-ranger` ne commite que ce qu'il range.** Il commitait tout `tasks/` : une
  retouche pas commitée d'un autre contrat partait sur la branche principale, approuvée sans
  avoir été relue. Et il refuse de ranger une tâche dont un autre contrat cite les fichiers,
  au lieu de casser sa preuve en disant « restent valables ».
- **`make taches` ne dit plus « fusionnée » une branche sans commit** vérifiée sur du
  travail pas commité ; il reconnaît toujours une fusion en avance rapide.
- **Le juge ne plante plus**, sur un nom de fichier qui n'est pas de l'UTF-8 ou un contrat
  qui ne l'est pas : c'est un refus qui le dit, et toute erreur imprévue aussi (le journal
  gardait « running »). Le test partagé d'une tâche rangée est signalé ; `setup` et `verify`
  d'une tâche rangée trouvent ses fichiers ; la branche que désigne le serveur (`develop`)
  sert de base partout ; un lot ne s'appelle pas `tasks` ; la ligne de lancement cite un
  chemin que le shell interpréterait au collage.
- **La sortie des sous-agents de Claude Code est dite pour ce qu'elle est : un minimum.**
  Mesuré avec Claude Code 2.1.286, sur les sous-agents de cette évaluation : 0 à 3 réponses
  sur 48 à 77 ont leur compte de sortie final ; les autres gardent un `stop_reason` nul et
  quelques tokens, et la sortie déléguée était sous-estimée d'environ 90 % sans que rien le
  dise. Le verdict écrit « au moins », `make usage` « ≥ » et « sortie non écrite (N
  réponses sur M) » ; le plafond s'applique à ce minimum (`.ephor/docs/07`).
- **La démonstration du juge tourne sans rsync**, absent d'un Linux minimal : la dernière
  ligne de la recette du projet neuf s'arrêtait à sa première commande.
- **Installation et mise à jour.** Un `.gitignore` sans saut de ligne final voyait sa
  dernière ligne collée à la première ajoutée (`dist.verify/`). Un `Makefile` ou des
  réglages fusionnés au geste 0 étaient « À REPRENDRE » à chaque mise à jour, même
  inchangés dans Ephor : ils ne le sont plus quand Ephor les livre à l'identique et qu'ils
  gardent ses cibles et ses hooks. Un rôle dont le projet avait changé le seul `model:`
  avant la 1.5 recevait bien le nouvel `effort: high`. Une liste YAML dans l'en-tête d'un
  sous-agent (`tools:` puis `- Read`) ne fait plus échouer `make adapters`, et les virgules
  finales du JSONC de VS Code ne se retirent plus dans les valeurs.
- **Et aussi.** `make score` ne plante plus sur une durée mal écrite dans le journal ;
  l'aide de `usage.py` garde ses exemples lisibles.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version,
puis relancez Claude Code et opencode. Le greffon de Copilot ne change pas : rien à
réinstaller, son relais appelle les gardes du moteur, que la mise à jour remplace.

## 1.5.0 — 1er octobre 2026

Les points de terrain que la 1.4.0 n'avait pas pris (tickets #27 à #35) : un test partagé par
deux contrats, l'évaluateur qui ne pouvait lancer aucun test, la réponse vide du planner en
effort max sous opencode, le modèle et l'effort de chaque rôle, cent tâches illisibles,
`PROGRESS.md` pas tenu, des tâches qui se marchaient dessus dans un même dossier, le mode
automatique, et les sous-agents lents. Puis chaque fonctionnalité de cette version a été
rejouée dans un projet neuf, avec les vrais outils : quatre défauts trouvés et corrigés,
dont deux qui touchaient justement le travail à cent tâches et une tâche par dossier, et
quelques points plus petits (les cinq dernières entrées).

- **Un test partagé par deux contrats, signalé au moment d'approuver.** Un critère qui lance
  le test d'un autre contrat lie les deux tâches : le juge de l'une peut attendre le travail
  de l'autre, et changer ce test périme les deux contrôles rouges. Vu en session réelle :
  aucun verdict propre, et on ne l'apprenait qu'au juge. `make task-check` et
  `make task-approve` (avant le « oui ») nomment le test et l'autre contrat ; le juge le
  rappelle en revue ; le planner ne l'écrit plus. Et un contrôle rouge périmé dit maintenant
  quel commit l'a périmé, et à quel autre contrat la preuve appartient.
- **L'évaluateur relance les tests.** En mode « plan », il ne pouvait lancer aucun test
  (Claude Code n'y laisse passer que les commandes de lecture ; opencode : git, rg, fd, jq),
  et sa procédure lui disait de « raisonner ». Il relance maintenant `make verify` et les
  tests de la tâche, comme le relecteur ; il n'écrit toujours rien (édition refusée, ni
  commit ni `make fmt` dans sa consigne). Pour « un test échouerait sans le changement »,
  il lit le contrôle rouge, qui le prouve pour chaque critère.
- **Sous opencode, en effort max, la réponse garde sa place.** Pour un modèle Claude servi
  par Copilot, opencode donne à la réflexion le budget maximal annoncé moins un token, et sa
  bibliothèque ramène « réflexion + réponse » à la limite du modèle : quand la limite vaut ce
  budget, la réponse n'avait plus qu'un token, et le planner rendait du vide sur les grosses
  tâches (lu dans le code d'opencode 1.18.33 ; plateaux de 32 000 et 63 999 tokens vus en
  session réelle). Le greffon laisse toujours 16 000 tokens à la réponse ; la réflexion
  prend le reste. L'effort demandé ne change pas. Mesuré de bout en bout, opencode 1.18.33
  et un faux serveur au format d'Anthropic : avant, 64 000 tokens envoyés dont 63 999 de
  réflexion ; après, 64 000 dont 48 000.
- **L'évaluateur relit chaque tâche, et le modèle et l'effort de chaque rôle se règlent.**
  L'évaluation sautait encore une tâche à risque faible écrite par un modèle fort : elle a
  maintenant lieu à chaque fois (décision de l'opérateur). Aucun sous-agent ne fixait son
  effort : il héritait de celui de la session, et dans le dossier d'une tâche lancée en effort
  bas, l'évaluateur relisait en effort bas. Par défaut, évaluateur `opus` en `high`,
  relecteur `sonnet` en `high`. Le réglage de chaque rôle tient dans deux lignes de l'en-tête
  de son sous-agent (`model:`, `effort:`) : la mise à jour les garde et remplace le reste,
  `make adapters` passe l'effort à opencode (sa `variant`, mesuré : 48 000 → 16 000 tokens
  de réflexion en `high`), et `make doctor` dit quand l'évaluateur n'est plus au niveau fort.
- **Cent tâches, lisibles : `make taches` et des dossiers par fonctionnalité.** Avec une
  centaine de tâches dans `tasks/`, on ne s'y retrouvait plus, ni dans l'arborescence de
  l'éditeur. `make taches` donne une ligne par tâche et son état (approuvée, en cours,
  vérifiée, en échec, rouge à refaire, fusionnée), calculé à partir de git et du journal du
  juge ; les fusionnées sont cachées. Une tâche se range dans `tasks/<lot>/<ID>/` : à la
  création (`LOT=` sur `task-new` et `task-approve`, lot proposé par le planner), ou après
  coup (`make task-ranger LOT=… ID=…|PREFIXE=…`, un commit sur la branche principale). Son
  contrat et son contrôle rouge restent valables : ils parlent de `tasks/<ID>/…`, que le juge
  traduit vers le vrai dossier ; la garde du périmètre, le budget, la revue et
  `make task-worktree` la retrouvent aussi.
- **`PROGRESS.md` tenu à jour.** Seules des consignes le demandaient, et il ne l'était pas
  (session réelle). Après trois tours de travail sans qu'il bouge (le code, les commits, les
  fichiers nouveaux), la fin de tour le demande : sous Claude Code, l'agent reprend la main
  le temps de l'écrire, une fois, sans boucle ; sous opencode, l'opérateur le lit. Mesuré en
  vraie session Claude Code : l'agent a noté ses trois changements, puis s'est arrêté.
  `EPHOR_RAPPEL_PROGRES_TOURS` change le seuil, `EPHOR_RAPPEL_PROGRES=0` l'éteint. Et les
  réponses de fin de tour sortent en un seul objet JSON : deux à la suite, l'outil n'en lisait
  aucun.
- **Une tâche, un dossier, par défaut.** Plusieurs tâches dans un même dossier se marchaient
  dessus (session réelle). `make task-approve` finit maintenant en ouvrant le dossier de la
  tâche (`DOSSIER=non` pour s'en passer), et affiche la ligne à coller :
  `cd … && claude --model … --effort … "/feature <ID>"`, au niveau du contrat. `/feature`
  n'implémente plus dans le dossier principal, sauf demande de l'opérateur.
  `make task-clore ID=<ID>` retire le dossier et la branche locale d'une tâche fusionnée
  (`FORCE=1` pour une tâche abandonnée ; jamais un dossier qui a du travail pas commité).
- **Le mode automatique, de la demande au verdict.** `/feature <demande>` enchaîne tout ;
  l'opérateur fait deux gestes, pas plus : taper « oui » à l'approbation, puis coller la
  ligne qui lance l'agent dans le dossier de la tâche. Ces deux-là restent : l'approbation
  ne se délègue pas à un agent, et le dossier de la tâche veut sa session, au modèle du
  contrat. `/feature <ID> pas-a-pas` demande un « ok » avant chaque étape. Pas de fenêtre
  de confirmation du système : elle ne s'ouvre pas depuis le terminal de Claude Code
  (mesuré). La procédure montre aussi le tableau des tâches (`make taches`).
- **Les sous-agents mesurés : où part le temps.** Les tâches confiées à un sous-agent, le
  planner surtout, prenaient énormément de temps (session réelle) ; sans chiffres, on ne
  tranche pas. `make usage` montre maintenant, pour les sous-agents récents du dépôt (Claude
  Code et opencode) : durée, modèle et effort, sortie (et réflexion sous opencode), appels
  d'outils, fichiers lus. Et le compte des sous-agents de Claude Code est corrigé : chaque bloc
  d'une réponse porte le compte de sortie du moment où il a été écrit, seul le dernier est
  juste, et Ephor gardait le premier ; les agents d'un « Workflow » n'étaient pas lus
  (anthropics/claude-code#93620). Avec Claude Code 2.1.278 à 2.1.281, même le dernier bloc
  reste à quelques tokens : `make usage` le dit (« sortie non écrite ») au lieu d'afficher
  un coût faux. L'effort du planner se décidera sur ces chiffres, sur de vraies tâches.
- **Cent tâches, lues en moins d'une seconde.** `make taches`, `make task-check` sans `ID` et
  la recherche des tests partagés posaient une question à git par jeton de commande et par
  tâche. Mesuré avec 100 tâches : `make taches` 38 s, `make task-check` sans `ID` 35 s, et
  30 s de plus à chaque `make task-check ID=…` (la même recherche tourne deux fois dans
  `make task-approve`) ; `/feature` affiche le tableau à chaque appel. Un seul `git cat-file`
  reste maintenant ouvert et répond à toutes les questions, et le contenu de `tasks/` se lit
  une fois par commande. Mêmes 100 tâches : 0,7 s, 0,5 s, et le contrôle rouge d'une tâche
  revient à la durée de ses critères. `make task-check` sans `ID` ne liste plus les tâches
  fusionnées.
- **Le verdict d'une tâche se voit du dossier principal, et reste après elle.** Une tâche se
  juge dans son dossier, et son verdict y restait. Du dossier principal, `make taches` la
  disait « en cours » une fois vérifiée, `make score` ne voyait aucun jugement, `make usage`
  aucune mesure ; fusionnée en avance rapide, elle n'était pas « fusionnée », et
  `make task-clore` refusait de la fermer ; et quand il la fermait, verdicts et mesure de
  tokens partaient avec le dossier. Ces commandes lisent maintenant tous les dossiers du
  dépôt, et `make task-clore` garde dans le dossier principal les jugements et la mesure
  d'une tâche avant de retirer son dossier.
- **La ligne qui lance opencode dans le dossier d'une tâche démarre.** Elle donnait
  `opencode --model … --variant …` : l'écran d'opencode n'a pas `--variant` (seul
  `opencode run` l'a) ; il affichait son aide et ne démarrait pas (opencode 1.18.33,
  vérifié). L'effort se choisit dans l'écran : la ligne dit `/variants`. Un contrat qui ne
  donne que l'effort a aussi sa ligne de lancement.
- **Un projet né de l'archive d'Ephor garde des tests d'Ephor à jour.** La recette « projet
  neuf » copie Ephor entier, ses tests compris, et la CI du projet les lance. La mise à jour
  ne les touchait pas, et disait de retirer la démonstration et le script d'installation,
  toujours livrés : un projet 1.4.0 mis à jour gardait ses anciens tests contre le nouveau
  moteur, et sa CI passait au rouge (mesuré). Ils suivent maintenant la version, dans un
  projet qui les a déjà ; un projet équipé par le script d'installation ne les reçoit
  toujours pas.
- **Et aussi.** Le résumé d'approbation et l'alerte de test partagé nomment le vrai dossier
  d'une tâche rangée ; ce dossier se traduit aussi dans `--rootdir=tasks/<ID>` ou
  `cd tasks/<ID> && …`, et jamais dans un autre nom qui le contient. `/feature` montre les
  tâches même sans la cible `make taches` (son repli ne servait jamais). Le rappel de
  `PROGRESS.md` ne relit plus en entier les gros fichiers nouveaux à chaque fin de tour, et
  efface l'état des sessions finies depuis un mois. L'évaluateur et `/feature` savent qu'une
  tâche peut être rangée dans `tasks/<lot>/<ID>/`.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version.
Les lignes `model:` et `effort:` des sous-agents que le projet a changées sont gardées, et
passées à opencode ; si vous les changez ensuite, `make adapters`. Relancez Claude Code et
opencode. Le greffon de Copilot ne change pas : rien à réinstaller. Une tâche ouverte dans
son dossier se ferme par `make task-clore ID=<ID>` plutôt que par `git worktree remove`,
pour garder son verdict. Le `AGENTS.md` du projet n'est pas touché : sa règle de revue
(« l'évaluateur relit chaque tâche ») peut être reprise de celui d'Ephor.

## 1.4.0 — 30 septembre 2026

Un retour de terrain et la fin de l'audit du 29 septembre. Les gênes vues en session réelle,
sur un vrai projet (une mutation qui ne mesurait rien, un contrôle rouge périmé par le
commit, des tests du planner à recopier, des tâches qui se marchaient dessus) ont chacune été
rejouées, puis fermées par un test qui échoue sur la 1.3.0. S'y ajoutent les derniers
chantiers de l'audit : la consigne de l'opérateur hors de portée de l'agent, ses gestes
protégés, « Python d'abord », un bac à sable pour opencode, et quatre points d'entrée pour
les garde-fous.

- **`make mutants` mesure enfin sur un vrai module.** mutmut recopie le code en empilant les
  variantes de chaque fonction : la copie est dix fois plus longue. Les tests de structure
  livrés par Ephor (`tests/test_architecture.py`) lisaient cette copie, échouaient (« plus
  de 400 lignes »), et mutmut s'arrêtait avant le premier mutant ; le script répondait
  « aucun mutant exécuté » et sortait en vert. Ces tests sont écartés du passage de mutmut :
  ils jugent la forme du code, pas son comportement. Rejoué : un module de 302 lignes donne
  son score au lieu de rien.
- **Une mutation arrêtée en route est un échec.** Quand mutmut ne va pas au bout,
  `make mutants` dit ÉCHEC, montre sa raison et sort en erreur ; `make review` le compte
  comme une étape en échec. Un fichier sans rien de mutable reste un cas normal. Le script
  dit aussi combien de mutants aucun test n'exécute : ce code-là n'a pas de test du tout.
- **La passe de nuit de la CI passe par le même script** (`mutants.sh --tout`) : elle
  échouait au démarrage sur tout vrai projet, pour la même raison. Son journal devient
  `.verify/mutants-run.log`.
- **Le commit ne périme plus le contrôle rouge.** `make task-check` prenait l'empreinte des
  tests tels quels ; au commit, les hooks de pre-commit (ruff, fin de fichier, espaces) les
  réécrivaient, et le juge refusait ensuite la tâche : « contrôle rouge périmé ». Vu sept
  fois dans une même session. `task-check` passe maintenant ces hooks lui-même, sur les
  preuves pas encore commitées, avant de prendre l'empreinte : le commit n'a plus rien à
  changer. S'ils échouent sans pouvoir corriger, il le dit tout de suite.
- **`make task-check` sans `ID` liste les contrôles rouges à refaire**, pour toutes les
  tâches, lus sur la branche principale comme le juge les lira. Un reformatage du dépôt en
  périme plusieurs d'un coup : on les voit tous, et plus un par un, au juge.
- **`make task-new FROM=` reprend aussi les tests du planner.** Le planner rend le contrat
  et les tests ; seul le contrat entrait, et les tests étaient à recopier par-dessus un
  squelette, à chaque tâche. `FROM` accepte sa réponse entière : le premier bloc JSON devient
  le contrat, chaque bloc de code annoncé par son chemin sous `tasks/<ID>/` devient un
  fichier de la tâche. Un chemin hors de la tâche ou un Python illisible, et rien n'est créé.
  La skill `feature` enregistre la réponse du planner dans `.verify/plans/<ID>.md` et donne
  la commande. Un contrat JSON seul marche comme avant.
- **Une tâche par dossier : `make task-worktree ID=<ID>`.** Plusieurs tâches lancées dans un
  même dossier se marchaient dessus (session réelle) : elles partagent la branche et les
  fichiers. La commande ouvre une tâche approuvée dans son propre dossier (un worktree git),
  sur sa branche, avec son environnement : une session d'agent par dossier. Mesuré avec
  Claude Code 2.1.285 : depuis un worktree, le bac à sable laisse commiter et protège la
  configuration et les hooks du dépôt principal, mais pas son `.git/info/` ; la commande
  l'ajoute aux réglages locaux du dossier. Juge, gardes et sentinelle y tournent pareil.
- **Deux sessions dans un même dossier : Ephor prévient.** Au début du tour, une fois, quand
  une autre session d'agent encore vivante travaille dans le même dossier ; sous opencode,
  par un message à l'écran. Rien après un `/clear`. Limite : sous Copilot, deux
  conversations d'une même fenêtre VS Code ne se voient pas.
- **`STEER.md` n'est plus à la portée de l'agent, et arrive en cours de tour.** L'agent
  pouvait l'écrire, et son contenu lui revenait comme « consigne opérateur » à chaque tour :
  un texte glissé par une page web devenait une consigne permanente. C'est maintenant une
  zone de contrôle, refusée à l'agent (gardes, bac à sable de Claude Code, juge, CI) ; la
  sentinelle, seule, laisse l'opérateur l'écrire pendant le tour. La consigne arrive une
  fois par changement, et plus à chaque tour. Sous Claude Code, dès l'appel d'outil suivant,
  et plus seulement au
  prochain message de l'opérateur : mesuré dans une vraie session, une consigne écrite
  pendant une commande de l'agent a été suivie dès la fin de la commande.
- **Un geste de l'opérateur refuse de tourner sous un agent.** Les refus des réglages
  comparent des débuts de commande : un autre ordre des mots, un script ou `bash -c` les
  contournaient. Chaque outil d'agent pose une variable que tout ce qu'il lance hérite
  (`CLAUDECODE` et `AI_AGENT` pour Claude Code, `OPENCODE`, `AI_AGENT` et `COPILOT_AGENT` dans le
  terminal d'agent de VS Code) : `task-new`, `task-check`, `task-worktree`, `adapters`, `fetch-docs`,
  `copilot-install`, `checkpoints-purge` et l'installation la lisent, et refusent. La garde du
  terminal refuse qu'on retire ces variables (`unset`, `env -u`, `env -i`, `X= commande`). Ce
  n'est pas un mur : le juge et la CI restent la barrière.
- **L'approbation d'un seul geste : `make task-approve ID=<ID> FROM=<plan>`.** Contrats et
  tests viennent d'un agent ; les gestes de l'opérateur (créer la tâche, recopier les tests,
  contrôle rouge, commit) étaient mécaniques. La commande montre le résumé du plan, attend un
  « oui » tapé dans le terminal de l'opérateur, puis crée la tâche, la voit échouer et commite
  l'approbation sur la branche principale. L'agent enchaîne ensuite seul jusqu'au verdict.
  Pas de fenêtre de confirmation : mesuré, elle ne s'ouvre pas depuis le terminal de Claude
  Code, dont le bac à sable refuse aussi d'écrire `tasks/` ; et un agent qui aurait les droits
  d'accessibilité du système pourrait la cliquer.
- **Le juge dit quand l'approbation qu'il lit est encore locale.** Quand le serveur est connu
  (`origin/main`, au dernier fetch) et n'a pas encore le contrat : « approbation locale, pas
  encore sur le serveur », car c'est là que la CI la lira.
- **Sous opencode, les rôles reçoivent leurs modèles d'une commande :
  `make opencode-modeles FORT=… [STANDARD=…] [RAPIDE=…]`.** Elle écrit la configuration
  d'opencode du poste, jamais le dépôt ; `make doctor` la propose tant qu'un rôle n'a pas de
  modèle. Écarté après lecture du code d'opencode : `{env:…}` dans la configuration du dépôt
  (une variable absente y devient un modèle vide, qui remplace le réglage du poste).
- **Hors Python, une preuve qui va au bout : le rapport JUnit.** Un critère Maven, Gradle,
  Jest ou Go n'était jugé que sur son code de retour, donc refusé en risque élevé. Il peut
  maintenant désigner ses tests (`rapport_junit`) : la commande écrit son rapport JUnit dans
  le dossier `{rapport}` que le juge lui donne, hors de la copie jugée, et chaque test désigné
  doit y avoir tourné et réussi. Les fichiers de ces tests se nomment dans `preuves`, et
  l'agent ne peut plus les toucher. Au contrôle rouge, un test désigné absent du rapport est
  un faux rouge (nom mal écrit) ; l'absence de rapport, elle, est laissée à la lecture de
  l'opérateur (un test Java qui appelle une classe pas encore écrite ne compile pas).
  Exemples dans `tasks/README.md`.
- **« Python d'abord », dit.** En tête du README ; et la carte du code, la mutation et
  l'échantillon de tests disent SKIP hors Python, au lieu de « rien à muter » ou « aucun
  test » (la passe de nuit de la CI aussi, sur un dépôt sans Python).
- **Le niveau d'implémentation dans le contrat.** `niveau` (rapide, standard, fort) et
  `effort` disent quel modèle doit écrire la tâche : sa difficulté, pas son risque. Le
  planner les propose ; `make task-worktree` donne la ligne de lancement qui va avec
  (Claude Code : `claude --model opus --effort max` ; opencode : le modèle du poste pour ce
  niveau) ; sous le niveau fort, l'`evaluator` relit même une tâche à risque faible ; le juge
  note le niveau, et `make score` donne, par niveau, les tâches vérifiées et le coût par
  critère. Facultatif : à mesurer sur de vraies tâches avant d'en faire le défaut.
- **opencode dans le bac à sable du système (à essayer sur le poste).**
  `bash .ephor/scripts/opencode_bac_a_sable.sh` lance opencode dans `srt`, le moteur du bac à
  sable de Claude Code, avec des réglages tirés de la liste des zones : tout ce qu'opencode
  lance ne peut plus écrire une zone ni lire un `.env`. Mesuré en local, de bout en bout : un
  script écrit puis lancé par opencode, qui visait `tasks/`, reçoit « Operation not
  permitted » ; le code, les commits et les dépendances du greffon s'écrivent. Réseau fermé
  sauf `EPHOR_SRT_DOMAINES`. L'essai derrière le proxy de l'entreprise est le ticket #26.
- **Quatre points d'entrée pour les garde-fous, qui ne changeront plus.** Ajouter une garde
  touchait trois listes : les réglages de Claude Code, le greffon d'opencode, et celui de
  Copilot, installé sur chaque poste. La mise à jour ne remplaçait les deux premiers que
  s'ils n'avaient jamais été retouchés, et un poste oublié gardait l'ancien greffon. Les
  trois outils n'appellent plus que quatre scripts du moteur, un par moment du tour (avant
  un outil, après, au message de l'opérateur, en fin de tour) : c'est là que se décide
  quelles gardes tournent. Le greffon d'opencode vit maintenant dans le moteur ; le fichier
  que lit opencode ne fait que l'importer. Au passage, sous opencode, un outil qu'il ne
  connaît pas (celui d'un serveur MCP) passe les gardes au lieu de passer sans rien ; sous
  Copilot, une lecture lance deux gardes au lieu de sept. Décision : ADR 0011.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version.
Une dernière fois, les réglages de Claude Code et les deux greffons changent (ADR 0011) :
`make copilot-install` sur chaque poste qui travaille sous Copilot, puis une fenêtre VS Code
neuve ; relancez Claude Code et opencode. Des réglages de Claude Code retouchés dans le
projet reçoivent leur `.ephor-new` : remplacez-y les lignes `.ephor/hooks/…` par les quatre
du `.ephor-new`, sans les ajouter à côté (chaque garde tournerait deux fois), et gardez vos
hooks à vous. Un poste réinstallé sert aussi les projets pas encore mis à jour.
La CI n'est pas mise à jour par Ephor : pour la passe de nuit, remplacez la ligne
`uv run mutmut run …` par `bash .ephor/scripts/mutants.sh --tout`, et joignez
`.verify/mutants-run.log` à la place de `mutants-full.log` (GitHub : ajoutez
`include-hidden-files: true` à l'envoi). Le `AGENTS.md` du projet n'est pas touché :
sa ligne sur `STEER.md` (« injecté à chaque tour ») peut être reprise de celui d'Ephor.

## 1.3.0 — 30 septembre 2026

Des garde-fous deux fois plus rapides sous Copilot et opencode, et un trou fermé dans le
panneau de discussion de Copilot.

- **Les gardes d'un appel tournent en même temps.** Sous Copilot, les sept gardes de chaque
  appel d'outil tournaient l'une après l'autre, lectures comprises ; sous opencode, les
  quatre d'une écriture. Mesuré sur un poste rapide : un appel sous Copilot passe de 0,48 à
  0,69 s à 0,25 à 0,33 s ; une écriture sous opencode, de 0,45 à 0,22 s. Même verdict, mêmes
  messages : un test compare les deux modes appel par appel. Claude Code lançait déjà ses
  hooks ensemble : rien ne change pour lui.
- **Une garde muette refuse.** Une garde qui n'a pas rendu son verdict en 20 s est arrêtée,
  et l'appel est refusé. Avant, elle tenait l'appel jusqu'au délai de l'outil, qui laisse
  passer.
- **Revenir à la série** : `EPHOR_GARDES_EN_SERIE=1`, même verdict, pour un poste où lancer
  plusieurs processus à la fois pose problème (un antivirus qui les inspecte un par un).
  `.ephor/docs/11-trois-agents.md` dit où le poser pour chaque outil.
- **Le panneau de discussion de Copilot n'écrit plus une zone de contrôle en direct.** Il
  écrit avec `create_file`, `replace_string_in_file`, `multi_replace_string_in_file` et
  `insert_edit_into_file` : aucun de ces noms n'était connu des gardes d'écriture et de
  périmètre, qui laissaient passer. La sentinelle et le juge le rattrapaient après coup ; la
  garde, non. Un outil au nom inconnu qui vise un fichier avec un contenu écrit est
  maintenant jugé comme une écriture, et un secret écrit par ces outils est vu. Trouvé en
  lisant la doc de VS Code : pas encore observé en session.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version.
Pour Copilot : `make copilot-install` sur chaque poste, puis une fenêtre VS Code neuve (le
relais du greffon a changé ; `make doctor` le signale tant que ce n'est pas fait). Pour
opencode : relancez-le.

## 1.2.0 — 30 septembre 2026

Les défauts de l'audit du 29 septembre : des contrôles qu'on croyait tenir, et qui ne
tenaient pas. Chacun a été reproduit, puis fermé par un test qui échoue sur la 1.1.7.

**Le juge**

- **Un test retiré se voit.** Un fichier de test supprimé, un `test_…` disparu d'un
  fichier : nommé au verdict, refusé sur un risque élevé, signalé par la garde de la CI. Un
  test qui change de fichier n'est pas retiré. Avant, supprimer `tests/test_architecture.py`
  donnait VÉRIFIÉ sans un mot.
- **Éteindre le lint dans `pyproject.toml` compte.** Le juge lit quelles sections changent.
  Les dépendances et la fiche du paquet restent un simple signal ; `[tool.ruff…]`,
  `[tool.pytest…]`, un greffon pytest, `"scripts"` ou `"jest"` d'un `package.json` sont des
  réglages des contrôles : refusés sur un risque élevé, signalés par la garde de la CI.
- **Le verdict dit quand le travail n'est pas commité.** Le juge juge le disque ; la merge
  request part de la branche. Les fichiers pas encore commités sont nommés, et le verdict
  finit par « sur du travail pas encore commité ».
- **Une base choisie à la main n'approuve plus rien.** `make judge BASE=<branche de
  l'agent>` faisait passer un contrat que personne n'avait approuvé. Seule la branche
  principale, ou une branche du serveur (`origin/…`), sert de base.
- **En conteneur, git marche dans la copie jugée.** Elle empruntait les objets du dépôt par
  un chemin que le conteneur ne voit pas : un projet versionné par git (hatch-vcs,
  setuptools-scm) s'installait en silence avec une fausse version, ou échouait sans dire
  pourquoi. Avec une image sans git, le juge refuse maintenant avant de rien lancer, et dit
  quelle image prendre.
- **Un identifiant écrit dans l'adresse de l'index de paquets** (`https://moi:jeton@…`)
  n'est plus transmis hors de l'installation. `make doctor` le signale.

**Les garde-fous**

- **L'agent ne déplace plus la branche principale** par le terminal (`git switch -C main`,
  `git branch -f main`, `git update-ref`, `git reset` sur main, `git push . x:main`…). La
  sentinelle voit en plus les commits du tour qui touchent une zone de contrôle et une
  branche principale déplacée, et donne la commande pour la remettre.
- **La sentinelle ne crie plus pour rien** quand l'agent change de branche ou rebase sans
  toucher aux zones.
- **Les gestes de l'opérateur sont refusés dans n'importe quel ordre de mots** :
  `make ID=X task-check` passait par la règle générale `make *`.
- **Un secret ne se lit plus d'un geste.** Un `.env` de sous-dossier est refusé à la
  lecture ; le terminal refuse d'afficher un fichier que les réglages refusent à la lecture,
  et `env` ou `printenv` sans nom de variable.
- **Plus de plafond de réflexion caché.** `MAX_THINKING_TOKENS=16000` ne touchait plus que
  d'anciens modèles ; c'est le niveau d'effort qui règle la réflexion (`.ephor/docs/07`).

**Le reste**

- **Points de reprise** : repartis de l'index réel, deux fois plus rapides sur 20 000
  fichiers, et leur coût ne grandit plus avec le volume du dépôt ; sans les fichiers de
  plus de 5 Mo (le message du point les nomme) ; gardés 30 jours. `make checkpoints-purge`
  les supprime tous (geste de l'opérateur).
- **Données personnelles** : le contrôle dit combien de fichiers Python il a lus. Du code
  d'un autre langage n'est pas lu : il le dit (`SKIP fuites` dans `make verify`, PARTIEL dans
  `make sec` sans scanner de la maison), au lieu de « rien trouvé ». Il voit aussi le prénom,
  le nom complet et l'objet client passé tout entier (`%r`, `str(client)`).
- **`make doctor`** se lance avant `make setup` sans échouer ; il dit si les rôles ont leur
  modèle sous opencode, si `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX` bride les réponses, et si
  le greffon Copilot enchaîne la liste de garde-fous du dépôt.
- **`make review`** va au bout, juge compris, et finit par son bilan.
- **Les skills** n'ouvrent plus que les commandes des réglages : `/feature` ouvrait tout git,
  `/brownfield-onboard` tout le terminal.
- **Le scan des agents** dit qu'il n'a pas tourné quand `SNYK_TOKEN` manque, au lieu de
  sortir vert.
- **Doc** : la vraie recette des modèles par rôle sous opencode (`.ephor/docs/07`, 2.6),
  le délai des garde-fous sous Claude Code aussi, la ligne `info` attendue de l'exemple dans
  un projet neuf, `pre-commit-hooks` épinglé par empreinte.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version.
Trois choses à reprendre à la main, car elles appartiennent au projet :

- `.ephor/projet/verify.sh` n'est jamais remplacé : reprenez le bloc `fuites` du modèle
  livré avec cette version. Sans lui, un dépôt Java ou TypeScript reste « ok fuites » sans
  rien lire.
- La CI (`.gitlab-ci.yml`, `.github/workflows/`) arrive en `.ephor-new` si vous l'avez
  retouchée : `diff`, puis reprenez le scan des agents.
- Sous opencode : réglez une fois le modèle de chaque rôle sur le poste
  (`.ephor/docs/07-token-budget.md`, 2.6) ; `make doctor` dit ce qui manque.

Puis relancez Claude Code (ses réglages ont changé). Rien à réinstaller pour Copilot.

## 1.1.7 — 29 septembre 2026

Le contrôle anti-secret refusait des textes ordinaires, et laissait passer les secrets des
gros fichiers.

- **Un secret en tête d'un fichier de plus de 16 Ko passait.** La recherche s'arrête à sa
  première correspondance ; le texte qu'on lui envoyait était alors coupé en pleine
  écriture, et le script comptait cette coupure pour « pas de secret ». Mesuré : une clé AWS
  en tête d'un fichier de 21 Ko passait, sous les trois outils. Seul le verdict de la
  recherche compte maintenant.
- **`risk-…` n'est plus pris pour une clé d'API.** Le motif des clés en `sk-` n'avait ni
  début de mot ni casse : tout identifiant en tirets passant par « …sk- » (`risk-`, `task-`,
  `desk-`, `disk-`) était refusé. Il vise maintenant une clé en minuscules, en début de mot,
  avec au moins un chiffre.
- **Un texte d'interface n'est plus pris pour un mot de passe.** `"password": "Le mot de
  passe doit contenir au moins 12 caractères"` était refusé : une valeur entre guillemets
  n'est un secret que si elle n'a pas d'espace.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version.
Rien à reprendre, rien à réinstaller pour Copilot.

## 1.1.6 — 29 septembre 2026

Une mise à jour qui s'annonçait faite, et laissait tourner l'ancienne version.

- **La mise à jour remplace aussi ce qu'Ephor pose hors de `.ephor/`.** Le greffon
  d'opencode, les réglages et les sous-agents de Claude Code, les skills et le `Makefile`
  arrivaient en `.ephor-new` à chaque mise à jour, et l'ancienne version restait active. Un
  projet mis à jour en 1.1.5 comme ce journal le disait chargeait encore le greffon d'opencode
  de la 1.1.3 : ni plafond de réponse relevé, ni sentinelle, ni alerte de contexte, et rien
  ne le signalait. Un tel fichier est maintenant remplacé s'il est, à l'octet près, une
  version livrée par Ephor (`.ephor/empreintes.txt`), donc jamais retouché dans le projet.
  Sinon il garde son `.ephor-new`, et le bilan le donne « à reprendre », avec les
  garde-fous qui manquent aux réglages (ADR 0010).
- **`make doctor` dit quand une mise à jour n'est pas finie** : un `.ephor-new` qui traîne,
  un greffon d'opencode qui n'est pas celui du moteur.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, lancé depuis cette
version. Elle rattrape les mises à jour 1.1.4 et 1.1.5 : les fichiers jamais retouchés sont
remplacés, et leurs `.ephor-new` retirés. Ce que le bilan donne « à reprendre » se fusionne
à la main (`diff <fichier> <fichier>.ephor-new`), puis `make adapters`. Relancer ensuite
opencode ou Claude Code. Un projet équipé avant la 1.0 n'est pas reconnu par la liste : il
reprend ces fichiers à la main, une fois.

Dans le dépôt d'Ephor, à chaque livraison qui change l'un de ces fichiers :
`python3 .ephor/scripts/empreintes.py`, puis commit de `.ephor/empreintes.txt` (sinon
`make ephor-test` échoue).

## 1.1.5 — 29 septembre 2026

Un contrat que l'agent s'écrivait et s'approuvait lui-même, sous opencode, sans que rien ne
le voie.

- **La sentinelle tourne sous opencode.** On la croyait impossible faute de fin de tour : le
  greffon d'opencode en a une. Le message de l'opérateur ouvre le tour (photo des zones de
  contrôle), la session redevenue inactive le ferme (contrôle, puis point de reprise). Avant,
  un script ou un test qui écrivait dans `tasks/` n'était vu par personne ; le greffon
  reprenait même la photo à chaque requête, ce qui aurait effacé la trace.
- **L'agent n'écrit plus l'histoire de la branche principale.** `git add` et `git commit`
  passaient sans question : un contrat glissé dans `tasks/` par un détour, commité sur la
  branche principale, valait approbation pour le juge local. La garde du terminal refuse
  désormais `git commit`, `merge`, `cherry-pick`, `revert`, `am`, `rebase` et `pull` sur
  cette branche, y compris après un `git switch main` dans la même commande. L'agent commite
  sur ses branches de tâche ; l'approbation reste le geste de l'opérateur. `AGENTS.md` le dit.
- **Les points de reprise d'Ephor existent aussi sous opencode**, en plus de son `/undo`.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, puis relancer opencode.
La mise à jour ne touche pas l'`AGENTS.md` du projet, écrit à la main : y ajouter la règle
(« un commit par incrément, sur ta branche de tâche, jamais sur la branche principale »). La
garde l'applique de toute façon ; la règle écrite évite à l'agent de buter dessus.

## 1.1.4 — 29 septembre 2026

Le sous-agent `planner`, vu échouer sur un vrai projet, et la règle des 60 % de contexte,
que personne n'appliquait.

- **Le plafond de réponse d'opencode est relevé.** opencode coupe chaque réponse à 32 000
  tokens, réflexion comprise, quel que soit le modèle (défaut connu, ouvert chez lui). Au
  plus haut niveau d'effort, et même en `high`, la réflexion du planner le remplissait :
  quatre échecs à 32 000 tokens de réflexion et 0 de réponse, de 5 à 17 minutes chacun, et
  environ 4,60 $ pour rien. Le greffon relève maintenant le plafond de chaque requête jusqu'à
  la limite du modèle, sans dépasser le quart de sa mémoire. Au plus haut niveau, le même
  planner a répondu en 132 secondes, pour le prix d'une réussite en `high`, avec un meilleur
  contrat. Un poste qui a fixé `OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX` garde son choix.
- **Une réponse coupée ne passe plus en silence.** opencode finissait le tour comme si de
  rien n'était ; l'agent qui avait délégué recevait du vide et concluait, une demi-heure
  plus tard, que le planner « n'était fiable que sur les demandes triviales ». Le greffon
  l'affiche à l'écran, et l'agent reçoit la raison avec la réponse.
- **L'alerte de contexte.** Au-delà de 60 % de la mémoire du modèle, un message en fin de
  tour : `/handoff`, puis une nouvelle session. Ephor ne vide rien lui-même. Sous opencode,
  le pourcentage est exact. Sous Claude Code, dont les hooks ignorent la taille de la
  mémoire, elle est supposée et le message le dit ; `EPHOR_CONTEXTE_TOKENS` la déclare.
  Rien sous Copilot, qui ne donne ses tokens qu'à la fermeture de la session.
- **On demandait au planner de lancer la carte du dépôt, sans terminal.** Ses droits le lui
  interdisent, à raison : il ne fait que lire. C'est désormais l'agent qui l'appelle qui lui
  passe la carte (`make map`) ; à défaut, il se repère avec ses outils de lecture. Un test
  refuse qu'un sous-agent sans terminal reçoive l'ordre de lancer un script.

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, puis relancer opencode
pour qu'il recharge son greffon. Rien à installer sur le poste, et rien à reprendre pour
Copilot.

## 1.1.3 — 27 septembre 2026

Relecture des textes : des formulations neutres, partageables telles quelles (exemples du
code, documentation d'installation, commentaires de la CI). Aucun changement de
comportement. Dans un projet équipé, `bootstrap_existing_repo.sh --mettre-a-jour` suffit :
rien d'autre à reprendre.

## 1.1.2 — 27 septembre 2026

Audit de la 1.1.1 : chaque fonctionnalité essayée sur un projet neuf, sur un dépôt existant
et en conteneur, chaque défaut reproduit puis couvert par un test.

- **Faux « VÉRIFIÉ » par un travail en cours.** `make task-check` lançait les critères sur
  le dossier tel quel. Un refactor pas commité qui cassait la fonction faisait échouer un
  critère que la branche principale satisfaisait déjà : la tâche commitée seule, l'agent
  sortait VÉRIFIÉ sans rien faire. Le contrôle tourne maintenant sur le code commité, plus
  les seules preuves de la tâche ; le reste est nommé et laissé de côté. Il refuse aussi de
  tourner sur une branche `task/…`, où il retirait la trace commitée.
- `make task-check` refuse deux autres rouges pour une mauvaise raison : le squelette de
  `make task-new` pas rempli (« A ECRIRE », test « critère à écrire ») et une commande
  introuvable (code 126 ou 127). Il écrivait la trace.
- Le juge dit quand le contrôle rouge a été fait sur un commit que la base ne contient pas
  (autre branche, trace recopiée d'un autre dépôt) : il se taisait.
- `make task-check` est refusé aux agents, comme `make task-new` : `Bash(make *)` le leur
  laissait sans question.
- La garde du terminal voit `git config` et `git remote` qui écrivent `.git/config`, où un
  `core.hooksPath` fait lancer par git le code de l'agent. Sous Copilot, ça passait.
- `make score` compte des tâches, d'après le dernier jugement de chacune : une tâche
  rejugée trois fois pesait comme trois tâches.
- `make review` trouve l'approbation sur `master`, comme le juge ; `make delivery-check`
  ne redonne plus sa place au modèle de politique qu'on vient d'y copier.
- La CI GitHub : le scan complet de la nuit se lance aussi à la main (`workflow_dispatch`).

### Mettre à jour un projet équipé de la 1.1

`bootstrap_existing_repo.sh --mettre-a-jour`, puis :

- **`.claude/settings.json`** : deux refus en plus, `Bash(make task-check *)` et
  `Bash(python3 .ephor/judge.py rouge *)`. Reprenez-les du `.ephor-new`, puis
  `make adapters` pour opencode et VS Code. Le greffon Copilot n'a pas changé.
- **un contrôle rouge fait avec un travail en cours pas commité** : refaites-le
  (`make task-check ID=<ID>` sur la branche principale), et commitez la trace.

## 1.1.1 — 26 septembre 2026

Audit de la 1.1.0, chaque défaut reproduit puis couvert par un test.

- `make task-check` refuse un rouge pour une mauvaise raison quand il se reconnaît : preuve
  introuvable, dossier de preuve ouvert à l'agent, test pytest désigné qui n'a jamais
  tourné (nom mal écrit) ou qui passe déjà, dossier sans test. Il écrivait la trace.
- Relancé sans que rien ait changé, même juste après avoir commité, il ne réécrit plus la
  trace : sa date seule changeait, à recommiter à chaque passage.
- La raison d'un script muet est la sienne (« aucune sortie, code de retour 3 »), plus un
  morceau de la commande.
- Les empreintes des preuves se lisent en une fois : 7 s pour un dossier de 400 fichiers,
  à chaque jugement, contre 0,15 s maintenant.
- La mise à jour d'un projet nomme les cibles d'Ephor dont la recette a changé, pas
  seulement les absentes : un `make task-check` resté en 1.0 n'écrivait jamais la trace
  que le juge réclame. Le juge et `judge.py validate` donnent la commande directe.
- L'image de CI refuse un certificat d'entreprise qui ne finit pas par `.crt` (un `ca.pem`
  était ignoré sans un mot) ; `make doctor` cite l'erreur exacte d'uv.

## 1.1.0 — 26 septembre 2026

**Rouge d'abord.** Un critère déjà vert avant le travail faisait déclarer l'agent VÉRIFIÉ
sans qu'il ait rien fait. `make task-check ID=<ID>` lance maintenant chaque critère une
fois sur le code actuel : tous doivent échouer, et la raison de chacun est affichée. Il
écrit alors `tasks/<ID>/rouge.json`, à commiter avec le contrat. Le juge refuse une tâche
sans cette trace, ou dont une preuve a changé depuis (ADR 0009). EXEMPLE-001 perd son
critère C3, vert avant tout travail : ce qui doit seulement rester vrai va dans la suite
normale.

**En entreprise.**
- `make doctor` lance bubblewrap pour de vrai sous Linux : sans lui ou sans socat, ou
  quand il ne peut pas démarrer, Claude Code saute son bac à sable avec un simple
  avertissement, et le diagnostic le dit maintenant (ADR 0008).
- L'autorité de certification de l'entreprise : un certificat PEM déposé dans
  `.ephor/deploy/autorites/` entre dans l'image de CI et du juge ; `make doctor` reconnaît
  un certificat refusé par uv et donne `UV_SYSTEM_CERTS=true`. Détail : `.ephor/docs/10`,
  « Certificat de l'entreprise ».
- Les images se prennent sur un miroir : `make ci-image EPHOR_CI_BASE=… EPHOR_CI_GITLEAKS=…`,
  et `EPHOR_TEST_IMAGE` pour les tests d'Ephor en conteneur.
- La CI GitHub passe à gitleaks-action 3.0.0 (Node 24).

### Mettre à jour un projet équipé de la 1.0

`bootstrap_existing_repo.sh --mettre-a-jour`, puis :

- **le `Makefile`** : la recette de `task-check` a changé (elle appelle `judge.py rouge`)
  et `ci-image` prend deux variables. Le bilan dépose un `Makefile.ephor-new` et, depuis
  la 1.1.1, nomme ces deux cibles : reprenez-les ;
- **chaque contrat déjà commité** : `make task-check ID=<ID>` sur la branche principale,
  puis commitez le `rouge.json` qu'il écrit. Sans lui, le juge refuse la tâche. Un critère
  qu'il trouve déjà vert est à resserrer ou à retirer.

## 1.0.0 — 26 septembre 2026

Première version partagée avec l'équipe.

**Le juge.** Il rend le verdict d'une tâche hors de l'agent, contre un contrat approuvé
sur `main`, dans une copie clonée à part, avec un environnement filtré. Une preuve doit
montrer qu'elle est allée au bout : rapport de tests complet, sortie exacte comparée de
l'extérieur, ou jeton imprimé en fin de script. Les preuves sont relevées avant chaque
étape et recomparées après. Il tourne à l'identique en local, sous Podman ou Docker sans
réseau, et en CI, où il vient toujours de la branche cible.

**Les garde-fous.** Une seule liste de zones de contrôle (`.ephor/zones.txt`), lue par les
gardes d'écriture, de lecture et du terminal, par la sentinelle de fin de tour, par le juge
et par la CI. Sous Claude Code, le terminal tourne dans un bac à sable qui refuse d'écrire
une zone, détour compris. Arrêt d'urgence (`make stop`) et consigne en cours de route
(`STEER.md`).

**Trois agents.** Claude Code, opencode et GitHub Copilot, à partir d'une seule source
(`.claude/`). Copilot passe par un greffon posé une fois par poste (`make copilot-install`).

**Les tokens.** Mesure par tâche, plafond qui arrête l'agent, sous-agents compris ; carte
du code (`make map`) pour lire moins.

**La conformité.** Secrets (gitleaks), données personnelles dans les journaux et les
messages d'erreur, branchement du scanner de sécurité de l'entreprise (`make sec`).

**Le reste de la chaîne.** Revue proportionnée au risque du contrat (`make review`),
manifeste de livraison lié au code jugé, dépôts voisins en lecture seule (`make refs`),
pages du web entrées comme données (`make fetch-docs`), tableau de bord des jugements
(`make score`), installation et mise à jour dans un dépôt existant.

Ce qui n'a pas encore tourné en conditions réelles est listé à la fin du `README.md`.

### Un projet déjà équipé d'une version d'avant la 1.0

`bootstrap_existing_repo.sh --mettre-a-jour` remplace le moteur (`.ephor/`). Trois choses
restent à reprendre à la main dans `.claude/settings.json` (le bilan les signale par le
`.ephor-new`) : la garde `write-guard.sh` déclarée pour Claude Code, le bloc `sandbox`
(ADR 0008), et les refus `.git/config`, `.git/hooks/**`, `.git/info/**`, `GNUmakefile`,
`makefile`, `.mcp.json`, `CLAUDE.local.md` ; et, pour opencode, `.opencode/plugins/ephor.js`.
Tant qu'ils manquent, Claude Code et opencode n'appliquent pas la liste des zones à
l'édition ; la sentinelle et le juge, eux, la lisent déjà. Puis `make adapters`, et
`make copilot-install` sur chaque poste : l'ordre des gardes du greffon a changé.

Deux règles du juge peuvent faire refuser ou échouer une tâche qui passait :

- un critère qui cite un **dossier** ouvert à l'écriture par `allowed_paths` (`pytest tests/`
  avec `tests/` dans le périmètre) est refusé — citez les fichiers de preuve un par un, ou
  resserrez le périmètre ;
- une vérification qui réécrit un fichier suivi de la copie fait échouer le jugement,
  puisque ce qui a été vérifié ne serait plus ce qui sera livré.

Un projet équipé avant que tout Ephor tienne dans `.ephor/` : le script reprend lui-même
ses réglages à leur nouvelle adresse, puis donne la commande qui retire les anciens
fichiers.
