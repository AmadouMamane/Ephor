# 12 — GitLab : les réglages qui font foi

> Le juge de la CI prend son code sur la branche cible : une branche qui réécrit le juge ne
> se juge plus elle-même. Mais la porte ne fait foi que si une branche ne peut ni
> **réécrire la CI**, ni **fusionner sans elle**. Ça ne se règle pas dans le dépôt : ce sont
> des réglages du projet GitLab. Cinq, dix minutes, une fois par projet.

## Vérifier où on en est

```bash
export GITLAB_TOKEN=<jeton personnel, portée read_api>   # jamais commité, jamais affiché
make gitlab-check                                        # projet et instance lus dans « origin »
GITLAB_URL=https://gitlab.interne GITLAB_PROJECT=groupe/projet make gitlab-check
```

La commande lit l'API, ne modifie rien, et dit pour chaque réglage `OK`, `À FAIRE` (avec
l'endroit où cliquer) ou `INCONNU` (droits insuffisants, ou fonction absente de l'offre).
Elle sort en 0 seulement si tout est en place. Derrière un proxy : `HTTPS_PROXY`.
Certificat d'entreprise refusé : `SSL_CERT_FILE=<autorité de l'entreprise>` (fichier PEM ;
le poste, l'image et Podman : `.ephor/docs/10-installation.md`, « Certificat de l'entreprise »).

**Portée.** Écrite d'après la documentation de l'API GitLab et essayée contre un faux
serveur, pas encore contre une instance réelle. Si elle répond `INCONNU` là où l'interface
montre le réglage en place, c'est elle qui a tort : fiez-vous à l'interface et signalez
l'écart.

## Les cinq réglages

| # | Ce qu'il empêche | Où |
|---|---|---|
| 1 | Qu'une branche réécrive la CI, juge compris | Réglages > CI/CD > Pipelines généraux > **Fichier de configuration CI/CD** : `.gitlab-ci.yml@<groupe>/controles` |
| 2 | Qu'on fusionne sans CI verte, ou sur un pipeline sauté | Réglages > Demandes de fusion > **Le pipeline doit réussir** ; décocher « les pipelines ignorés comptent comme réussis » |
| 3 | Qu'on pousse sur la branche principale sans passer par une fusion | Réglages > Dépôt > Branches protégées : `main`, pousser = **personne**, fusionner = mainteneurs, pas de poussée forcée |
| 4 | Qu'un changement des zones de contrôle passe sans leurs propriétaires | `.gitlab/CODEOWNERS` commité (modèle : `.ephor/docs/CODEOWNERS.example`, vrais groupes), puis sur la branche protégée : **approbation des propriétaires du code** (Premium) |
| 5 | Qu'un auteur s'approuve lui-même | Réglages > Demandes de fusion > Approbations : auteur exclu, commiteurs exclus, règles non modifiables par demande, approbations retirées à chaque nouveau commit (Premium) |

## Le réglage 1, en pratique

1. Créer dans le groupe un projet `controles`, où seuls les mainteneurs de la plateforme
   écrivent. Y déposer le `.gitlab-ci.yml` d'Ephor.
2. Dans chaque projet équipé : « Fichier de configuration CI/CD » →
   `.gitlab-ci.yml@<groupe>/controles`. Le `.gitlab-ci.yml` du projet n'est alors plus lu.
3. Si le projet a ses propres jobs à garder, le fichier de `controles` peut les inclure,
   à la manière des pipelines de conformité de GitLab :

   ```yaml
   include:
     - project: '$CI_PROJECT_PATH'
       file: '.gitlab-ci.yml'
       ref: '$CI_COMMIT_SHA'
   ```

   Les jobs définis dans le fichier de `controles` l'emportent sur ceux du même nom
   inclus depuis le projet : une branche peut **ajouter** des jobs, pas retirer `juge` ni
   `garde-controles`. D'après la documentation de GitLab, pas encore essayé sur une
   instance réelle.

Offre Ultimate : une **politique d'exécution de pipeline** remplit le même rôle, sans
toucher au réglage 1. `make gitlab-check` ne sait pas la voir, et répondra `À FAIRE` sur
ce point : c'est attendu.

## Sans l'offre Premium

Les réglages 4 et 5 manquent. Restent 1, 2 et 3, et ils portent l'essentiel : la CI
n'est plus modifiable depuis une branche, et rien n'arrive sur `main` sans elle. Ce qui
manque alors, c'est une deuxième paire d'yeux **obligatoire** sur les zones de contrôle —
à compenser par une règle d'équipe, écrite. Le job `garde-controles` aide à la tenir : il
passe en avertissement (orange) toute demande qui touche une zone de contrôle, mais il ne
bloque pas la fusion à lui seul.
