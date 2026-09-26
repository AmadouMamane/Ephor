# ADR 0009 — Rouge d'abord : chaque critère doit échouer avant le travail

- **Date** : 2026-09-26
- **Statut** : accepté

## Contexte

`tasks/README.md` le disait depuis le début : chaque critère a une commande qui échoue tant
que le comportement n'existe pas. Rien ne le vérifiait. Un critère déjà vert sur la branche
principale fait pourtant déclarer l'agent VÉRIFIÉ sans qu'il ait rien fait : il lui suffit
d'un patch quelconque dans son périmètre. L'exemple livré en avait un (EXEMPLE-001, C3 :
« un libellé valide reste nettoyé »), vert avant tout travail.

## Décision

- `make task-check ID=<ID>`, lancé par l'opérateur sur la branche principale avant de
  commiter le contrat, vérifie sa forme, puis lance chaque critère **une fois**, sur le code
  actuel (HEAD plus l'état du dossier, puisque le contrat n'est pas encore commité), avec
  la machinerie du juge : même copie propre, mêmes formes de preuve. **Tous doivent
  échouer.** La raison de chaque échec est affichée : une faute de frappe fait échouer aussi,
  c'est à l'opérateur de voir que l'échec vient du comportement qui manque.
- Alors seulement il écrit `tasks/<ID>/rouge.json` : les critères vus échouer, leur raison,
  et l'empreinte de chaque fichier dont dépend le verdict (le dossier de la tâche, contrat
  compris, et les fichiers ou dossiers que les critères citent). Un critère vert, une
  installation en échec ou un budget épuisé : pas de trace, et l'ancienne est retirée.
- L'opérateur la commite avec le contrat. Le juge la lit **sur la base**, comme le contrat,
  et refuse une tâche sans elle, ou dont une preuve a changé depuis. Il ne relance rien :
  lire un fichier ne coûte rien, et l'agent ne peut ni l'écrire (`tasks/` est une zone) ni
  la rafraîchir (elle est lue sur la base, pas sur sa branche).
- Ce qui doit seulement **rester** vrai n'est pas un critère : sa place est la suite
  normale, que l'étape `verify` du juge fait tourner. C3 a quitté EXEMPLE-001 ; un test de
  `tests/` le couvrait déjà.

## Conséquences

- Un geste de plus pour l'opérateur, entre relire et commiter. `make task-new` le rappelle.
- Aucune période de transition : aucun projet n'avait encore de tâche (décidé le 26/09/2026).
- Un test retouché sur la branche principale après le contrôle fait refuser la tâche
  jusqu'à ce que `make task-check` soit relancé : c'est le but.
- Le juge compte les fichiers changés hors de `tasks/` entre le contrôle et la base, et le
  dit au verdict, pour la revue.

## Limites

- Le contrôle dit que les critères échouaient **le jour du contrôle**. Si la branche
  principale change ensuite le code qu'ils testent, ils peuvent passer au vert sans l'agent :
  le juge ne les rejoue pas, il signale seulement que la base a bougé.
- Un échec pour une mauvaise raison est refusé quand il se reconnaît : preuve introuvable,
  dossier de preuve ouvert à l'agent, test pytest désigné jamais lancé (nom ou chemin mal
  écrit : pytest rend alors un rapport sans aucun test, mesuré le 26/09/2026) ou déjà vert.
  Un test qui importe une fonction pas encore écrite reste un vrai rouge. Le reste (une
  faute dans le corps d'un script) compte comme rouge : la raison est affichée, la lire
  reste un acte humain.
- L'opérateur peut écrire la trace à la main. Il n'est pas l'adversaire que le juge vise :
  l'agent, lui, ne peut pas.
