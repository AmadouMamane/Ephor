# Plan d'exécution et journal de décisions

## Incréments

| # | Incrément | Critère associé | État |
|---|---|---|---|
| 1 | TODO | C1 | à faire |

## Journal de décisions

> Une ligne par décision, datée. Les décisions structurantes ont en plus une ADR.
> Ce journal est ce qui empêche de re-débattre trois fois du même arbitrage.

| Date | Décision | Raison | ADR |
|---|---|---|---|
| AAAA-MM-JJ | TODO | TODO | — |

## Dépendances ajoutées

> Toute dépendance nouvelle s'inscrit ici avec sa justification, avant d'être ajoutée
> à `pyproject.toml`. Une dépendance non justifiée est une dette qu'on n'a pas décidée.

| Paquet | Pourquoi | Alternative écartée |
|---|---|---|
| ruff (dev) | format et lint, un seul outil rapide | black + flake8 + isort, trois outils à accorder |
| mypy (dev) | types stricts : un agent ne peut pas « deviner » une signature | pyright, pas de mode strict dans uv sans Node |
| pytest, pytest-xdist, pytest-randomly (dev) | suite, parallélisme, ordre aléatoire qui casse les dépendances entre tests | unittest seul : pas de marqueurs, pas de parallélisme |
| pytest-cov (dev) | couverture, à la demande (`uv run pytest --cov`) ; aucune étape ne l'exige | coverage seul, même chose sans l'intégration |
| hypothesis (dev) | tests par propriétés, pour les cas que l'agent n'imagine pas ; à utiliser dans `tests/` | rien : les cas limites restent à la main |
| import-linter (dev) | la direction des dépendances, mécaniquement (`make arch`) | une revue humaine, qui oublie |
| mutmut (dev) | la mutation sur les fichiers changés (`make mutants`) | cosmic-ray, plus lourd |
| pip-audit (dev) | vulnérabilités des dépendances, en CI après fusion | safety, base de données moins ouverte |
