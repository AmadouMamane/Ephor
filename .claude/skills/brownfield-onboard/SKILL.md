---
name: brownfield-onboard
description: Installe Ephor sur un dépôt existant qui n'en a pas. À utiliser une seule fois par dépôt, avant toute autre tâche sur ce dépôt.
argument-hint: [chemin du dépôt, ou . pour le dépôt courant]
allowed-tools: Read Grep Glob Bash Edit Write Task
---

# Installation d'Ephor sur un dépôt existant

Le piège de cette opération est de vouloir tout réparer d'un coup. On ne répare rien :
on rend le dépôt **observable et vérifiable** par un agent. La réparation vient après.

## Étape 1 — Inventaire, sans rien lire en profondeur

```!
bash .ephor/scripts/repo_map.sh 2>/dev/null | head -60 || true
```

Détecte, dans cet ordre : langage(s) et gestionnaire de paquets, commande de test
existante, commande de lint existante, CI existante, points d'entrée, présence de
tests du tout. Délègue au sous-agent `scout` si la recherche s'annonce large.

## Étape 2 — La commande unique

C'est **la** livraison de cette étape, et elle passe avant tout le reste. Rédige
`.ephor/projet/verify.sh` de sorte que `make verify` :

- enchaîne format, lint, types, tests, dans cet ordre ;
- écrive chaque sortie dans `.verify/<étape>.txt` ;
- n'imprime qu'une ligne par étape plus un bloc `stats` pré-calculé ;
- sorte 0 si tout est vert, 1 sinon.

Si le dépôt n'a aucun test, `make verify` fait quand même format + lint et le dit.
Un harnais sans commande unique n'est pas un harnais.

Ce fichier est une zone de contrôle : il dit ce que « vert » veut dire, donc tu ne peux
pas l'écrire toi-même, et c'est voulu. Donne son contenu complet à l'opérateur, qui
l'enregistre ; puis lance `make verify` pour vérifier qu'il tient.

## Étape 3 — AGENTS.md, court

Écris-le **à la main**, pas en le faisant générer par un agent : la documentation
curatée par un humain surpasse la documentation générée, parce qu'elle dit ce qui
compte plutôt que ce qui existe. 150 lignes maximum, table des matières vers `docs/`.

Contenu minimal : ce que fait le projet en deux phrases ; la commande unique ; la
règle de dépendances réelle (même si elle est actuellement violée — dis-le) ; les
trois pièges spécifiques à ce dépôt que tu viens de découvrir ; où sont les secrets
et pourquoi il ne faut pas les lire.

## Étape 4 — Un premier contrat, minuscule

Pas de contrat sur du code que personne n'a relu. Prends la plus petite demande réelle et
fais préparer par le sous-agent `planner` un contrat d'un seul critère. L'opérateur le
crée (`make task-new`), le relit et le commite sur la branche principale. `make judge`
doit alors fonctionner de bout en bout : c'est le test d'Ephor, pas celui du code.

## Étape 5 — Contraintes mécaniques, progressivement

Ajoute `import-linter` (ou l'équivalent) avec le contrat de couches **tel qu'il est
réellement respecté aujourd'hui**, pas tel qu'il devrait être. Un contrat rouge dès
le premier jour est un contrat que tout le monde ignore. Resserre-le ensuite, un cran
par semaine, chaque cran dans sa propre PR.

## Étape 6 — Première fonctionnalité en mode harnais

Fais passer cette demande par `/feature`, jusqu'au job `juge` de la CI.

## Ce qu'il ne faut PAS faire

Reformater tout le dépôt en une PR (l'historique git devient inutilisable pour
`git blame`, et la revue est impossible). Activer tous les linters au maximum d'un
coup. Écrire des critères d'acceptation sur du code que personne n'a relu.
