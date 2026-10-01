---
# généré par .ephor/adapters.py depuis .claude/skills/feature/SKILL.md — ne pas modifier à la main
description: "Boucle complète d'une fonctionnalité, du contrat au juge et à la revue. Utilise-la pour tout travail qui dépasse une correction triviale."
---

# Fonctionnalité : $ARGUMENTS

État du dépôt :

!`git status --short 2>/dev/null | head -20 || true`

!`git branch --show-current 2>/dev/null || true`

Tâches :

!`( make -s taches 2>/dev/null || ls tasks 2>/dev/null | grep -v README ) | head -40`

## En automatique, par défaut

De la demande au verdict, l'opérateur fait deux gestes, pas plus : taper « oui » à
l'approbation (`make task-approve`), puis coller la ligne qui lance l'agent dans le dossier de
la tâche. Tout le reste s'enchaîne. Ces deux gestes restent : l'approbation ne se délègue
pas à un agent (c'est elle que le juge lit), et le dossier de la tâche veut une session à
lui, au modèle que le contrat demande.

**Pas à pas, en option** : si les arguments contiennent `pas-a-pas`, annonce chaque étape en
une ligne avant de la faire (« Étape 5 : le juge. Je lance ? »), et attends que l'opérateur
réponde « ok ». Sans cette option, n'attends aucun feu vert entre deux étapes.

## Déroulé imposé

**1. Un contrat approuvé, avant tout code.**
S'il n'existe pas de contrat approuvé pour `<ID>` sur la branche principale (dans
`tasks/<ID>/`, ou `tasks/<lot>/<ID>/` pour une tâche rangée : le tableau ci-dessus), délègue
au sous-agent `planner`, avec la sortie de `make map` dans la demande : il n'a pas de
terminal pour la faire lui-même. Enregistre sa réponse entière, telle quelle, dans
`.verify/plans/<ID>.md`, et donne à l'opérateur la commande qui l'approuve d'un seul geste :
`make task-approve ID=<ID> FROM=.verify/plans/<ID>.md LOT=<lot>` (résumé, « oui » tapé,
contrat et tests créés, vus échouer, commités sur `main`). Le lot est la fonctionnalité que
le planner propose : la tâche se range dans `tasks/<lot>/`. Pour les retoucher d'abord :
`make task-new ID=<ID> FROM=.verify/plans/<ID>.md`, puis `make task-check`. Puis
**arrête-toi** : tu ne peux ni écrire dans `tasks/`, ni commencer sans ce commit.

Une fois l'approbation faite, enchaîne les étapes 2 à 7 sans attendre d'autre feu vert :
l'approbation était le seul (en `pas-a-pas`, un « ok » à chaque étape). Arrête-toi
seulement sur un blocage (étape 5) ou au verdict.

**2. Le dossier de la tâche.** Une tâche s'implémente dans son propre dossier, sur sa
branche : `make task-approve` l'ouvre, et affiche la ligne qui y lance une session au niveau
du contrat (`cd … && claude --model … "/feature <ID>"`). Dans ce dossier, tu es déjà sur
`task/<ID>` : continue. Dans le dossier principal, sur la branche principale : ne crée pas la
branche ici ; donne cette ligne à l'opérateur (ou `make task-worktree ID=<ID>` s'il ne l'a
pas), et arrête-toi. Seulement s'il te demande de travailler ici : `git switch -c task/<ID>`.

**3. Cadrer sans tout lire.** Code inconnu : délègue au sous-agent `scout`. Sa sortie
volumineuse reste dans son contexte, seule la synthèse remonte.

**4. Le plus petit incrément.** Reste dans `allowed_paths`. Un incrément, un commit.
`make verify` après chaque incrément : c'est la boucle rapide. Lance-le tel quel, sans `| tee`
ni redirection : sa sortie est déjà courte, et un tuyau vers un fichier ferait demander une
permission à l'opérateur.

**5. Le juge.** `make judge TASK=<ID>`. Lis `.verify/judge/<ID>/latest/summary.txt`,
puis **uniquement** le journal de l'étape en échec. Corrige et recommence.
Au troisième échec sur le même critère : arrête-toi, écris le blocage dans
`PROGRESS.md` sous `## Bloqué`, rends la main. Ne boucle pas.

**6. La revue, proportionnée au risque du contrat.**
- `faible` : sous-agent `evaluator`, toujours. Le juge et la CI ne voient que les critères ;
  lui voit la conception, le code en double, les cas oubliés. Relire coûte bien moins cher
  qu'écrire. S'il rend `NEEDS_WORK`, retour à l'étape 4.
- `modere` : sous-agent `evaluator`. S'il rend `NEEDS_WORK`, retour à l'étape 4.
- `eleve` : `evaluator`, puis `make mutants`, puis sous-agent `reviewer`.
Chaque relecture par un modèle coûte des tokens : ne paie que ce que le risque justifie.

**7. Clore.** Mets à jour `PROGRESS.md`. Décision structurante : une ADR dans `docs/adr/`.
Le verdict qui fait foi est celui du job `juge` de la CI, sur la merge request.

## Interdits pendant cette boucle

Modifier un test d'acceptation, un seuil, la CI, un script ou le Makefile pour passer.
Si la vérification gêne, c'est un sujet à remonter à l'opérateur, pas à contourner.
