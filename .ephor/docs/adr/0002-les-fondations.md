# ADR 0002 — Les fondations : un juge hors de l'agent, des garanties hors des outils

- **Date** : 2026-09-22
- **Statut** : accepté

## Contexte

Un agent de codage se déclare volontiers vainqueur. Une règle écrite en prose dans ses
consignes est une suggestion ; un hook qui refuse une écriture se contourne par le
terminal ; un test d'acceptation que l'agent peut réécrire ne prouve rien. Il faut donc
que le verdict vienne d'ailleurs.

Dans l'environnement cible, trois agents coexistent (Claude Code, opencode, Copilot), la
forge est GitLab, les paquets et images viennent de JFrog, et le budget de tokens est
limité.

## Décision

1. **Une boucle quotidienne courte et sûre** : une commande unique (`make verify`) aux
   sorties courtes et au code de retour toujours exact, des permissions resserrées, un
   diagnostic du poste (`make doctor`), une installation qui n'écrase rien, des points de
   reprise hors de l'historique plutôt qu'un commit à chaque réponse.
2. **Un juge au centre**, en bibliothèque standard : contrat approuvé sur la branche
   principale, périmètre, copie propre, Git durci, environnement filtré, empreintes. C'est
   lui, et non l'agent, qui dit si une tâche est faite.
3. **Garanties hors des agents** : juge et CI. Les réglages propres à chaque agent restent
   des conforts, remplaçables.
4. **GitLab en référence**, GitHub en variante ; versions figées par empreinte.
5. **Podman pour le juge**, installation avec réseau, vérification sans réseau.
6. **Revue proportionnée au risque** du contrat, pour ne payer en tokens que ce qui compte.

## Conséquences

- Une fonctionnalité demande un contrat commité avant le code : un peu de rigueur en plus
  au départ, beaucoup moins de débats à la fin.
- Ephor se teste lui-même (`make ephor-test`) ; toute évolution passe par ces tests.
- Ce qui n'a pas encore tourné en conditions réelles est tenu à jour à la fin du
  `README.md`.
