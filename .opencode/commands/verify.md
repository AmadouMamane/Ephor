---
# généré par .ephor/adapters.py depuis .claude/skills/verify/SKILL.md — ne pas modifier à la main
description: "Lance la vérification rapide et, si une tâche est en cours, le juge. À lancer avant tout commit significatif."
---

# Vérification

!`make verify || true`

## Ce que tu fais maintenant

1. Si `VERDICT: RED` : ouvre **uniquement** `.verify/<étape>.txt` de l'étape rouge.
   Ne relance pas en affichant tout le log : le détail est déjà sur disque.
2. Si `VERDICT: GREEN` et que la branche est `task/<ID>` : `make judge TASK=<ID>`, puis
   lis `.verify/judge/<ID>/latest/summary.txt`.
3. `GREEN PARTIEL` n'est pas une validation : des étapes n'ont pas tourné. `make doctor`.
4. Test intermittent : ne le supprime pas et ne le relance pas en boucle. Marque-le
   `@pytest.mark.flaky`, note-le dans `docs/04-tech-debt.md` avec la date, signale-le.

## Rappel de coût

Un log de tests complet dans la conversation coûte des dizaines de milliers de tokens et
ne sert qu'une fois. Le fichier sur disque coûte zéro et sert toute la session.
