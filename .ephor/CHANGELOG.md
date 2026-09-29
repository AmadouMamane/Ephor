# Journal des versions d'Ephor

Une entrée par version : ce qui change pour qui s'en sert. Les raisons de fond sont dans
`.ephor/docs/adr/`. La version en place est dans `.ephor/VERSION`.

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
