# Journal des versions d'Ephor

Une entrée par version : ce qui change pour qui s'en sert. Les raisons de fond sont dans
`.ephor/docs/adr/`. La version en place est dans `.ephor/VERSION`.

## 1.4.0 — en préparation

Trois gênes relevées en session réelle, sur un vrai projet : une mutation qui ne mesurait
rien, un contrôle rouge périmé par le commit, et des tests du planner à recopier à la main.
Chacune rejouée, puis fermée par un test qui échoue sur la 1.3.0.

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

Dans un projet équipé : `bootstrap_existing_repo.sh --mettre-a-jour`, depuis cette version.
La CI n'est pas mise à jour par Ephor : pour la passe de nuit, remplacez la ligne
`uv run mutmut run …` par `bash .ephor/scripts/mutants.sh --tout`, et joignez
`.verify/mutants-run.log` à la place de `mutants-full.log` (GitHub : ajoutez
`include-hidden-files: true` à l'envoi). Pour opencode : relancez-le (son greffon, le
planner et la commande `feature` ont changé).

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
