# ADR 0007 — Mettre Ephor à jour dans un projet : remplacer le moteur là où git garde l'ancien

- **Date** : 2026-09-24
- **Statut** : accepté

## Contexte

L'ADR 0006 a posé la règle : dans un projet, `.ephor/` ne se modifie pas, il se remplace ;
seul `.ephor/projet/` est au projet. Mais le seul moyen de mettre Ephor à jour restait de
relancer l'installation, qui n'écrase jamais rien : chaque fichier du moteur qui avait
changé arrivait en `.ephor-new` à côté de l'ancien. Une version qui touche vingt fichiers
en laissait vingt à reprendre à la main — alors que la règle dit qu'il n'y a rien à
fusionner. Deux défauts s'y ajoutaient, vus en préparant cette décision :

1. `PROGRESS.md` et `STEER.md` recevaient eux aussi un `.ephor-new` à chaque relance, et
   l'on était invité à « fusionner » le journal du projet avec un modèle vide.
2. Le geste 0 criait « Ephor est POSÉ mais INERTE » dès qu'un `Makefile.ephor-new`
   existait — donc à chaque mise à jour d'un projet où il marchait très bien.

## Décision

**`bootstrap_existing_repo.sh --mettre-a-jour /chemin/du/depot`**, lancé depuis la
nouvelle version d'Ephor. Pour chaque fichier du moteur (`.ephor/` hors `projet/`) qui a
changé :

- **son contenu est exactement celui du dernier commit** : il est **remplacé**. L'ancienne
  version reste dans l'historique ; `git diff -- .ephor/` montre tout ce qui a changé, et
  `git checkout -- .ephor/` remet les anciens ;
- **sinon** — retouché depuis le dernier commit, pas suivi, lien symbolique, en lecture
  seule — il reçoit son `.ephor-new`, comme avant, et le bilan dit pourquoi, fichier par
  fichier. Sans commit, rien ne garde l'ancien : rien n'est remplacé.

On compare les **contenus** (`git hash-object` contre le fichier de `HEAD`), pas l'avis de
`git diff` : un fichier marqué `skip-worktree` ou `assume-unchanged` paraît intact à git,
et sa retouche disparaissait sans laisser de trace. Un lien symbolique n'est jamais
remplacé : c'est sa cible, hors de git, qu'on aurait écrasée. Ces trois cas, et le fichier
en lecture seule qui arrêtait la mise à jour à mi-chemin, ont chacun leur test.

Ce qui ne change pas, exprès :

- **Hors de `.ephor/`, rien n'est remplacé.** Le `Makefile`, `.claude/settings.json`, les
  sous-agents peuvent avoir été retouchés et commités par le projet, et rien ne permet de le
  savoir. Ils gardent le `.ephor-new`.
- **Rien n'est retiré.** Un fichier que la nouvelle version n'a plus est nommé, avec la
  commande `git rm` ; l'opérateur décide.
- **Sans l'option, le comportement est celui d'avant** : rien n'est jamais écrasé.

Et, dans le même geste :

- en mise à jour, `PROGRESS.md` et `STEER.md` existants sont laissés tels quels ;
- un `.ephor-new` d'un passage précédent est retiré quand son fichier est maintenant à jour ;
- le geste 0 ne s'affiche plus que si Ephor est réellement inerte. Le test est étroit : un
  `Makefile` qui lance le juge (`.ephor/judge.py`), des réglages Claude dont le bloc
  `hooks` déclare `.ephor/hooks/`. Une simple mention de `.ephor/` — une exclusion de
  flake8, que le script conseille lui-même, ou un refus dans les permissions — ne prouve
  rien, et faisait taire le geste 0 sur un dépôt inerte. Quand le geste 0 a été fait, le
  bilan nomme les cibles d'Ephor absentes du `Makefile` du projet ;
- le bilan dit d'où la mise à jour part et où elle arrive (`Ephor 1.0.0 -> Ephor 1.1.0`),
  et rappelle `make copilot-install` quand le greffon a changé : celui de chaque poste est
  une copie, il ne suit pas le dépôt tout seul.

## Conséquences

- Mettre un projet à jour tient en une commande, puis `make verify`, puis un commit à part.
- Une retouche du moteur commitée dans un projet **est** remplacée. C'est la règle de
  l'ADR 0006, et c'est réversible : elle reste dans l'historique, et le diff la montre.
- Un projet placé dans un sous-dossier de son dépôt est servi : les chemins sont lus
  depuis le projet, pas depuis la racine du dépôt. Un test le tient — mal accordés, rien
  n'était jamais « suivi », donc rien n'était remplacé, et rien ne le disait.
- Ce n'est pas encore un paquet versionné. Le signal pour y passer ne change pas : la
  première correction qui oblige à toucher plus de deux dépôts à la main.
