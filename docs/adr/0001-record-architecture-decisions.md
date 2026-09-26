# ADR 0001 — Consigner les décisions d'architecture

- **Date** : AAAA-MM-JJ
- **Statut** : accepté

## Contexte

Un agent qui reprend le dépôt dans trois semaines n'a aucun souvenir des arbitrages.
Sans trace, il re-débat, ou pire, il ré-implémente selon une logique différente.

## Décision

Toute décision structurante — choix de dépendance, changement de contrat de couches,
choix de persistance, format d'échange — donne lieu à une ADR numérotée dans
`docs/adr/`, courte, en trois sections : contexte, décision, conséquences.

## Conséquences

Le dépôt reste le système de référence : ce que l'agent ne peut pas atteindre en
contexte pendant qu'il travaille n'existe pas. Une décision consignée dans un outil
de discussion ou dans une tête est une décision perdue.
