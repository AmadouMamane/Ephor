---
# généré par .ephor/adapters.py depuis .claude/skills/feature/SKILL.md — ne pas modifier à la main
description: "Boucle complète d'une fonctionnalité, du contrat au juge et à la revue. Utilise-la pour tout travail qui dépasse une correction triviale."
---

# Fonctionnalité : $ARGUMENTS

État du dépôt :

!`git status --short 2>/dev/null | head -20 || true`

!`git branch --show-current 2>/dev/null || true`

Contrats disponibles :

!`ls tasks 2>/dev/null | grep -v README || echo "aucun contrat"`

## Déroulé imposé

**1. Un contrat approuvé, avant tout code.**
S'il n'existe pas de `tasks/<ID>/contract.json` sur la branche principale, délègue au
sous-agent `planner`, avec la sortie de `make map` dans la demande : il n'a pas de
terminal pour la faire lui-même. Enregistre sa réponse entière, telle quelle, dans
`.verify/plans/<ID>.md`, et donne à l'opérateur la commande qui crée le contrat et les
tests : `make task-new ID=<ID> FROM=.verify/plans/<ID>.md`. Puis **arrête-toi** : c'est
lui qui les crée, les relit, les voit échouer (`make task-check`) et les commite sur
`main`. Tu ne peux ni écrire dans `tasks/`, ni commencer sans ce commit.

**2. La branche de travail.** `git switch -c task/<ID>` depuis la branche principale. Dans un
dossier ouvert par `make task-worktree`, tu y es déjà. Si Ephor signale une autre session dans
ce dossier, ne change pas de branche : demande à l'opérateur `make task-worktree ID=<ID>`, et
une session dans le dossier qu'il ouvre.

**3. Cadrer sans tout lire.** Code inconnu : délègue au sous-agent `scout`. Sa sortie
volumineuse reste dans son contexte, seule la synthèse remonte.

**4. Le plus petit incrément.** Reste dans `allowed_paths`. Un incrément, un commit.
`make verify` après chaque incrément : c'est la boucle rapide.

**5. Le juge.** `make judge TASK=<ID>`. Lis `.verify/judge/<ID>/latest/summary.txt`,
puis **uniquement** le journal de l'étape en échec. Corrige et recommence.
Au troisième échec sur le même critère : arrête-toi, écris le blocage dans
`PROGRESS.md` sous `## Bloqué`, rends la main. Ne boucle pas.

**6. La revue, proportionnée au risque du contrat.**
- `faible` : pas de relecture par un modèle. Le juge et la CI suffisent.
- `modere` : sous-agent `evaluator`. S'il rend `NEEDS_WORK`, retour à l'étape 4.
- `eleve` : `evaluator`, puis `make mutants`, puis sous-agent `reviewer`.
Chaque relecture par un modèle coûte des tokens : ne paie que ce que le risque justifie.

**7. Clore.** Mets à jour `PROGRESS.md`. Décision structurante : une ADR dans `docs/adr/`.
Le verdict qui fait foi est celui du job `juge` de la CI, sur la merge request.

## Interdits pendant cette boucle

Modifier un test d'acceptation, un seuil, la CI, un script ou le Makefile pour passer.
Si la vérification gêne, c'est un sujet à remonter à l'opérateur, pas à contourner.
