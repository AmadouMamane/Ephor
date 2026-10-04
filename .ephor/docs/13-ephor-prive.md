# 13 — Ephor privé : sur un dépôt partagé, sans l'imposer

> Un dépôt qu'on est seul à toucher reçoit Ephor en entier, commité, avec sa CI (doc 10,
> doc 12). Un dépôt partagé que l'équipe n'a pas encore validé, c'est autre chose : on veut
> Ephor pour soi — contrat, garde-fous, juge — sans rien imposer, et sans rien pousser du
> moteur. C'est ce que fait `.ephor/scripts/prive.sh`. Ephor ne sait pas qu'il est privé :
> juge, diagnostic, installation sont les mêmes ; seule la disposition git diffère.

## Le principe

| Où | Quoi |
|---|---|
| Votre branche principale **locale** | tout Ephor, dans un commit marqué `Ephor-prive:` posé au-dessus de `origin/<principale>`, et les commits d'approbation des contrats (`tasks/<ID>/`). Jamais poussé : une garde `pre-push` le refuse. |
| `.git/info/exclude` | ce qu'Ephor aurait écrit dans `.gitignore` (`.verify/`, `AGENT_STOP`…), plus `PROGRESS.md`, `STEER.md`, `.claude/settings.local.json`, `CLAUDE.local.md`. Le `.gitignore` de l'équipe ne change pas. |
| `.claude/settings.local.json` | les réglages de Claude Code (garde-fous, permissions, bac à sable), si l'équipe a déjà `.claude/settings.json`. opencode et Copilot sont dérivés de ceux-là. |
| `Makefile` | celui de l'équipe, plus une ligne : `-include .ephor/projet/prive.mk`. Les cibles d'Ephor y sont, sauf celles que l'équipe définit déjà (`test`, `lint`, `setup`…) : les siennes gagnent, sans avertissement. |
| La merge request | les commits de la tâche, rejoués sur `origin/<principale>`, précédés du contrat et de ses tests d'acceptation, avec le verdict local dans `tasks/<ID>/rapport.md`. Jamais `rouge.json` (trace interne), jamais `.ephor/`. |

Les commits d'Ephor sont reconnus à leur **marqueur** (une dernière ligne `Ephor-prive: …`
dans le message) ou à leurs fichiers, jamais à « au-dessus de main » : `git pull --rebase`
les déplace à chaque fois, et c'est prévu.

## Poser

```bash
bash /chemin/vers/ephor/.ephor/scripts/prive.sh poser /chemin/du/clone
```

Depuis la branche principale, copie de travail propre. Le script d'installation fait son
travail (ses bannières « geste 0 » ne s'appliquent pas ici : les réglages vont dans
`settings.local.json`, les cibles dans `prive.mk`), puis la disposition privée est posée et
commitée. Ensuite, comme d'habitude : adapter `.ephor/projet/verify.sh`, écrire `AGENTS.md`
s'il manque, `make doctor`, `make verify`. Ces retouches se commitent sur la branche
principale locale **avec le marqueur** :

```bash
git commit -a --trailer 'Ephor-prive: réglages'
```

Un commit sans marqueur qui ne touche que des fichiers d'Ephor est reconnu quand même ; un
commit mixte (Ephor et du code) ne l'est pas, et `publier` le refusera. Mettre Ephor à jour :
la même commande `poser`, depuis la nouvelle version.

Avant de poser, trois choses à regarder dans le dépôt : l'outil d'agent qu'on y utilise
(opencode, Claude Code, Copilot : doc 11), ce que l'équipe a déjà à la racine (`Makefile`,
`CLAUDE.md` ou `AGENTS.md`, `.claude/`, `.opencode/`), et comment elle fusionne ses merge
requests (squash ou non : voir « Après la fusion »).

## La boucle

Rien ne change pour l'agent : contrat (`make task-approve`), dossier de tâche, `/feature`,
`make judge`. Deux habitudes changent pour vous :

- **`git pull --rebase`**, jamais `git pull` qui fusionne : le commit d'Ephor se replace
  au-dessus. Si la rebase s'arrête sur un fichier d'Ephor (l'équipe a touché la dernière ligne
  du `Makefile`, ajouté un `.claude/settings.json`) : gardez la version de l'équipe, finissez
  la rebase, relancez `poser`.
- **Rien ne se pousse à la main** depuis une branche partie de la principale locale : elle
  emporte Ephor, et la garde la refuse. C'est `publier` qui envoie, en rejouant. Une branche
  partie de `origin/<principale>` passe, elle, sans Ephor.

## Publier

```bash
bash .ephor/scripts/prive.sh publier FEAT-001                      # tâche jugée VÉRIFIÉE
bash .ephor/scripts/prive.sh publier FEAT-001 --sans-verdict        # brouillon, sans verdict
bash .ephor/scripts/prive.sh publier --sans-contrat fix/typo        # une correction sans contrat
```

Dans une copie à part (jamais sur la branche de l'agent) : `origin/<principale>`, puis le
commit « contrat et tests d'acceptation » (`tasks/<ID>/contract.json`, les tests,
`rapport.md` ; sans `rouge.json`), puis chaque commit de la tâche, dans l'ordre. Un commit
qui touche un fichier hors du périmètre du contrat arrête tout, nommé. Avant de pousser
(`--force-with-lease`, vers `task/<ID>`), les jugements et la mesure de tokens sont
sauvegardés sous `~/.cache/ephor/prive/<dépôt>/`. Le texte de la merge request est prêt
dans `.verify/prive/<ID>/mr.md`, et la commande `glab` est affichée.

**Le verdict est une déclaration.** `rapport.md` dit ce que le juge a vu sur votre poste :
verdict, base, contrôle rouge, critères, niveau, tokens. L'équipe ne peut pas le recalculer
sans Ephor dans sa CI ; les tests d'acceptation, eux, sont dans la demande et se rejouent.
Vérifiez que la CI de l'équipe les lance (`testpaths` de pytest : `publier` prévient).
Un verdict vérifiable, c'est Ephor public, avec le juge en CI (doc 12).

**Après la fusion.** `git pull --rebase` ; `make taches` voit la tâche fusionnée, par le nom
de la branche (une fusion par merge request nomme `task/<ID>`) ou par son contenu (fusion
par squash, branche supprimée) ; puis `make task-clore ID=<ID>`. L'identifiant d'une tâche
est la clé du ticket de l'équipe (`PAY-123`) : rien d'Ephor ne transparaît dans les noms.

## Ce qui reste vrai, et ce qui ne l'est plus

| | Ephor public | Ephor privé |
|---|---|---|
| Contrat avant le code, vu échouer d'abord | oui, commit sur la principale | oui — approbation **locale** : vous êtes le seul approbateur |
| Garde-fous en session (zones, périmètre, secrets, sentinelle) | oui | oui, sur votre poste ; les sessions des collègues ne voient rien |
| Juge local | oui | oui |
| Juge en CI, branche protégée, CODEOWNERS | oui (doc 12) | **non** : le verdict arrive en déclaration |
| Traces (`.verify/journal.jsonl`, `make score`, `make usage`) | dans le dépôt | hors de git, sauvegardées à chaque `publier` et par `retirer` |
| Ce que l'équipe voit | tout | le contrat, ses tests, le code, `rapport.md` |

Un essai privé n'est pas un essai secret : dites à l'équipe qu'Ephor tourne sur votre
clone, fixez une date de décision, et montrez `make score` ce jour-là. Ce qu'il y a à
valider, c'est le **processus** — un contrat approuvé par merge request avant le code — pas
des fichiers.

## Adopter, ou retirer

```bash
bash .ephor/scripts/prive.sh adopter     # la branche adoption-ephor, à relire, à pousser avec --no-verify
bash .ephor/scripts/prive.sh retirer     # la principale locale = celle du serveur ; plus rien d'Ephor
bash .ephor/scripts/prive.sh etat        # où en est ce clone
```

`adopter` rejoue sur `origin/<principale>` les commits d'Ephor et les contrats, remet les
exclusions dans `.gitignore`, ajoute `PROGRESS.md` et `STEER.md`, le pointeur `@AGENTS.md`
dans `CLAUDE.md` ; si l'équipe a son `.claude/settings.json`, les garde-fous arrivent à côté,
en `.ephor-new`, à fusionner dans la merge request (le geste 0 de la doc 10). Les contrats
déjà sur le serveur comptent comme approuvés : rien à rejouer. Après la fusion : `git pull
--rebase`, puis `retirer`, qui enlève la garde et les exclusions locales ; Ephor reste, il
est à l'équipe.

`retirer` refuse d'abandonner un contrat pas encore publié (`--force` pour passer outre) ;
il sauvegarde `.verify/` d'abord, remet la branche principale à celle du serveur, retire le
bloc d'exclusions, la garde (le hook `pre-push` précédent est remis), et les fichiers propres
au poste. Restent, à vous : les branches `task/*` et les dossiers de tâche ouverts.

## Limites connues

- **`make adapters`** dérive opencode et Copilot de `.claude/settings.json`, donc des
  réglages de l'équipe quand elle en a : relancez `poser` plutôt, il dérive des vôtres.
- **`.vscode/settings.json`** de l'équipe n'est pas touché : sous Copilot, reprenez les clés
  `chat.*` dans vos réglages utilisateur (doc 11).
- **Un `.opencode/opencode.json` de l'équipe** reçoit le bloc `permission` d'Ephor, en local
  seulement ; une rebase peut s'y arrêter si l'équipe le change.
- **Le dossier d'une tâche** (`make task-worktree`) n'a pas de `PROGRESS.md` au départ : il est
  hors de git. L'agent l'écrit à son premier tour, et il reste hors de git.
- **`git push --no-verify`** contourne la garde : réservé à la branche d'adoption.
- **`make doctor`** signale que les garde-fous sont déclarés dans `settings.local.json`
  seulement : c'est attendu.
