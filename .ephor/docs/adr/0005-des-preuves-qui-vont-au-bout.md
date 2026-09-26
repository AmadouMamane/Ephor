# ADR 0005 — Des preuves qui vont au bout, un juge que l'agent ne touche pas

- **Date** : 2026-09-24
- **Statut** : accepté

## Contexte

Deux façons de faire dire « VÉRIFIÉ » au juge sur un code faux ont été reproduites :

1. **Le juge croyait pytest sur parole.** La forme de critère par défaut — celle
   d'EXEMPLE-001 et de `make task-new` — était jugée sur son seul code de sortie. Une ligne
   `__import__("os")._exit(0)` ajoutée au code, sans rien corriger, suffisait, en local
   comme en conteneur. Seules les preuves écrites comme script étaient protégées.
2. **La porte de CI se jugeait elle-même.** Le job `juge` lançait le juge depuis la
   branche jugée : une branche qui le réécrivait pour dire « oui » passait. Reproduit en
   simulant la CI sur un dépôt jetable.

Un vérificateur plus lourd — qui lance lui-même l'agent dans des clones hors du dépôt —
fermerait ces trous, mais au prix d'un parcours que personne ne suivrait au quotidien. La
question était donc de prendre les bonnes exigences sans le poids.

## Décision

1. **Une preuve pytest rend un rapport de tests.** Le juge demande lui-même le rapport, dans
   un dossier hors de la copie jugée, et l'exige neuf, en XML complet sans DOCTYPE, aux
   compteurs cohérents, avec au moins un test, aucun échec, aucun test sauté, et chaque
   test désigné par la commande présent.
2. **La sortie attendue**, comparée à l'octet près de l'extérieur du processus (`stdin`,
   `expected_output`, `returncode` dans un critère).
3. **Un risque élevé exige une preuve qui va au bout.** Aucun critère d'une tâche `eleve`
   ne se juge sur son seul code de retour, et tout silencieux ajouté (`noqa`,
   `type: ignore`, `skip`…) y est refusé. Sur les autres risques, ils sont nommés au verdict.
4. **La copie jugée est un clone à part**, plus un `git worktree` : un test qui lançait
   `git config` dans la copie écrivait dans le dépôt de l'utilisateur.
5. **Rien ne survit à son étape, rien ne déborde** : le groupe de processus est arrêté à la
   fin de chaque étape, la sortie est bornée à 64 Mo.
6. **Le juge vient d'un endroit que l'agent ne touche pas** : en CI, il est pris sur la
   branche cible par `git archive`.
7. **Le conteneur est durci** : `--cap-drop=ALL`, image en lecture seule, `/tmp` en
   mémoire, `--pull=never`.
8. **Une livraison exige une preuve courante, liée au contenu.** Le rapport garde l'empreinte
   de chaque fichier jugé ; le manifeste refuse un verdict défavorable, un juge changé
   depuis, ou un fichier livré différent de celui jugé.
9. **Contrats sans clé en double** : un contrat qui déclare deux fois la même clé ne dirait
   pas au lecteur laquelle s'applique.
10. **L'aspirateur de docs n'accepte que le web public** : HTTPS, pas de redirection vers un
    autre site, empreinte de ce qui a été reçu.

`make verify` reçoit la même exigence que le juge : une suite de tests coupée avant la fin
n'écrit pas de rapport, et ce n'est pas un vert.

**Écarté, et pourquoi :**

- **Un contrôleur qui lance lui-même l'agent** (clones hors du dépôt, patch exporté à
  réappliquer) : plus isolant, mais c'est le parcours lourd qu'Ephor évite. La garantie
  d'Ephor reste la CI, sur une machine que l'agent ne contrôle pas.
- **Un registre de budget par tentative** : Ephor mesure les tokens réels dans le journal
  de session, ce qui ne demande pas de lancer l'agent soi-même.
- **Le planificateur automatique, les rapports SARIF, les tests générés contre un oracle,
  les mutations bloquantes, la livraison Kubernetes/Cloud Run** : utiles, mais aucun n'a
  encore rencontré un vrai projet. À reprendre quand un besoin mesuré le demandera (voir le
  rituel de `.ephor/docs/09-evaluer-ephor.md`).
- **L'import de documentation interne** : Ephor commite les pages aspirées, et sa règle est
  « le web public, rien d'autre ».

## Conséquences

- Un critère pytest qui ne rend pas de rapport complet est rouge : c'est précisément le
  défaut corrigé.
- Un contrat à risque `eleve` dont un critère n'est qu'une commande quelconque est refusé :
  le réécrire en test pytest, en script Python ou en sortie attendue.
- Ce qui reste ouvert est écrit dans `.ephor/README.md`, « Ce que le juge ne peut pas
  voir » : un code qui ment délibérément à la preuve qui l'importe passe encore, mais en
  clair dans le patch. Et la porte de CI ne fait foi que si la configuration de CI est
  protégée côté GitLab (`.ephor/docs/12-gitlab.md`) : cela ne se règle pas depuis le dépôt.
