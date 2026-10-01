# Runbook — lancer, observer, déboguer

> Ce document existe pour l'agent autant que pour vous. Ce qu'un agent ne peut pas
> observer n'existe pas pour lui : il ne peut alors qu'affirmer que ça marche.
>
> Ici, les commandes de CE projet. Celles d'Ephor — dépôts voisins, scanner de sécurité,
> pages du web, juge, reprise après interruption — sont dans `.ephor/docs/06-exploitation.md`.

## Lancer

```bash
make setup        # installe l'environnement
make verify       # la commande unique
make map          # carte du dépôt, pas chère en tokens
```

## Observer

TODO — remplir avec VOS commandes. L'objectif est qu'un agent puisse répondre à
« est-ce que le démarrage tient sous 800 ms ? » par une **requête**, pas par une opinion.

| Question | Commande |
|---|---|
| Que dit le log de la dernière exécution ? | TODO |
| Quelle est la latence p95 ? | TODO |
| Combien de lignes en base ? | TODO |

## Déboguer

| Symptôme | Premier réflexe |
|---|---|
| `make verify` rouge sur `arch` | `tail -n 20 .verify/arch.txt` — un import remonte une couche |
| `make verify` rouge sur `types` | `rg error: .verify/types.txt` — ne pas ajouter de `# type: ignore` sans commentaire |
| Test intermittent | marquer `@pytest.mark.flaky`, inscrire dans `docs/04-tech-debt.md` avec une date |
| Le juge, la revue ou la livraison refusent | `.ephor/docs/06-exploitation.md`, « Symptômes et premiers réflexes » |
