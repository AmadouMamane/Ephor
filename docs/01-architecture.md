# Architecture

## Couches et direction des dépendances

```
types  →  config  →  repo  →  service  →  runtime  →  ui
```

Lecture : `ui` peut importer tout ce qui est à sa gauche ; `types` n'importe rien
du projet. **Aucune exception.** Le contrat est vérifié par `import-linter`
(`make arch`) et doublé par un test structurel (`tests/test_architecture.py`).

| Couche | Responsabilité | Ce qui est interdit |
|---|---|---|
| `types` | structures de données pures | toute I/O, tout import projet |
| `config` | lecture et validation de l'environnement | logique métier |
| `repo` | persistance, accès aux données | décision métier, log direct |
| `service` | logique métier | I/O directe, `datetime.now`, `random`, `logging` |
| `runtime` | assemblage, cycle de vie, câblage | logique métier |
| `ui` | HTTP, CLI, présentation | logique métier |

## Les transverses passent par un point unique

Horloge, aléa, log, télémétrie, feature flags : **uniquement** via `app.providers`.

Ce n'est pas du purisme. C'est ce qui rend les tests déterministes et ce qui permet
à `import-linter` de voir une violation qu'aucune relecture humaine ne verrait dans
un diff de 400 lignes.

## Pourquoi mécaniser plutôt qu'écrire une règle

Une règle en prose dans `AGENTS.md` est une suggestion : elle est lue une fois,
noyée dans le contexte, et oubliée au bout de deux heures de session. Un linter qui
rougit est une contrainte. Chaque fois que vous vous surprenez à rappeler la même règle
deux fois à un agent, la bonne réponse n'est pas de la réécrire en gras — c'est
d'ajouter un test dans `tests/test_architecture.py`.

## Changer l'architecture

Toute modification du contrat de couches passe par une ADR dans `docs/adr/` et une
PR dédiée qui ne contient **que** ce changement.
