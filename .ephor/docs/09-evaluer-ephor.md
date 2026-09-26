# Évaluer Ephor lui-même

> Le document manquant de la plupart des harnais. Il est facile de prescrire « supprime un
> composant et mesure » ; encore faut-il dire **comment** on mesure. Voici la méthode.

## 1. Pourquoi c'est le sujet, et pas un raffinement

Un travail publié en mai 2026, **Harness-Bench**, a mesuré l'effet du harnais sur
106 tâches d'agent en bac à sable, avec 5 194 trajectoires d'exécution, à travers
plusieurs configurations de harnais et plusieurs modèles, budgets et protocoles
d'évaluation standardisés. Sa conclusion tient en une phrase, et elle change la façon
de présenter un outil d'agent :

> la capacité d'un agent doit être rapportée **au niveau du couple modèle-harnais**,
> et jamais attribuée au modèle de base seul.

Le mode d'échec récurrent qu'ils identifient porte un nom utile : les **défaillances
d'alignement d'exécution** — un raisonnement plausible qui se découple du retour des
outils, de l'état réel de l'espace de travail, des preuves disponibles ou du contrat de
sortie attendu. C'est précisément ce que le contrat par défaut à l'échec de ce template
essaie d'attraper.

Conséquence pratique : annoncer « on utilise le modèle X » ne dit rien de ce qu'une
équipe peut faire. Annoncer « modèle X dans notre harnais, mesuré ainsi » dit tout.

## 2. Deux familles d'évaluation, qui ne répondent pas à la même question

Google a publié le 9 septembre 2026 la distinction la plus utile sur ce sujet.

**Les benchmarks de bout en bout** (Terminal-Bench, SWE-bench, DeepSWE) donnent un score
composite. Ils disent *que* la performance a changé, jamais *pourquoi*. Ils sont lents,
bruités, et inutilisables comme garde-fou de régression au quotidien.

**Les évaluations comportementales** vérifient des actions discrètes et observables :
l'agent a-t-il appelé l'outil attendu, au bon moment, avec les bons arguments ? On
assertionne sur les **appels d'outils intermédiaires**, pas sur la prose finale. Exemple
donné par Google : pour une question sur la météo, on vérifie que l'agent a bien appelé
la recherche web — pas que sa réponse est jolie.

C'est la différence entre un test d'intégration et un test unitaire, transposée au
harnais. Les deux servent ; une seule des deux permet d'itérer vite.

## 3. La boucle en trois pas

Reprise de la recommandation de Google :

1. **Partez d'un échec réel et récent.** Pas d'une capacité que vous aimeriez mesurer :
   d'un comportement que vous avez vu rater cette semaine — l'agent qui oublie de lancer
   les tests, qui modifie un seuil de CI, qui déclare fini sans preuve.
2. **Écrivez une assertion de rigidité adaptée.** Stricte quand la tâche est simple
   (« l'outil `make verify` a été appelé »). Floue, avec un modèle comme juge, quand le
   chemin est complexe (« la justification cite un fichier de preuve réel »).
3. **Exécutez par lots et suivez la tendance.** Un run isolé est bruité. On surveille la
   stabilité dans le temps, on ne bloque pas sur une exécution.

La finalité est défensive, et c'est important : ces évaluations ne sont pas là pour
célébrer un score. Elles existent pour répondre à une seule question — **est-ce qu'un
ajustement de prompt, un changement de schéma d'outil ou une montée de modèle vient de
rendre l'agent globalement pire ?**

## 4. Application au rituel de désinstallation

Le rituel prescrit dans ce template — à chaque montée de modèle, supprimer un composant
d'Ephor et mesurer — devient exécutable :

1. Constituez un lot de 10 à 20 tâches issues de **votre** dépôt, dont vous connaissez la bonne
   réponse. Pas des tâches de benchmark : des tickets réels déjà résolus.
2. Écrivez pour chacune deux ou trois assertions comportementales : quels outils devaient
   être appelés, quelle preuve devait être produite, quel contrat de sortie respecté.
3. Mesurez la ligne de base, harnais complet.
4. Retirez **un** composant. Remesurez. Trois exécutions minimum, on compare les tendances.
5. Si la qualité ne baisse pas, le composant était une hypothèse périmée sur une
   faiblesse du modèle : supprimez-le pour de bon et écrivez l'ADR.

Sans ce lot de tâches, le rituel est une intention. Avec lui, c'est une décision.

## 5. Le vérificateur du vérificateur : le mutation testing

Ce template pose comme critère « un test qui **échoue** sans le changement ». Le
mutation testing en est la version systématique, et c'est la réponse industrielle au
problème que pose massivement le code d'origine agentique : des suites de tests vertes
et volumineuses, aux assertions faibles.

Le principe : on introduit mécaniquement des défauts dans le code (inverser une
condition, changer une borne, supprimer un appel) et on regarde si la suite les
détecte. Le **score de mutation** est le pourcentage de défauts attrapés. Il pose une
question bien plus dure que la couverture : *est-ce que mes tests verraient la
différence si le comportement était réellement cassé ?*

Un ordre de grandeur — retour d'expérience publié en 2026, pas étude contrôlée : un
composant affichant **100 % de couverture de lignes** obtenait **61,29 % de score de
mutation**. Quatre défauts sur dix passaient à travers une suite prétendument complète.

**Outils** : `mutmut` ou `cosmic-ray` en Python, PITest en Java, StrykerJS en
TypeScript. Dans ce dépôt : `make mutants`.

**Comment l'utiliser sans faire exploser la CI** — la stratégie qui marche est
sélective et en couches :

| Couche | Quoi | Quand |
|---|---|---|
| 1 | mutants syntaxiques (bornes, conditions, opérateurs) sur les **seules zones changées à forte valeur** : authentification, règles métier, calculs financiers | à l'ouverture de la PR |
| 2 | 1 à 3 mutants **sémantiques** générés par un modèle, conscients de l'exigence, testés dans une copie jetable | changements sensibles uniquement |
| 3 | passe complète du dépôt | nocturne, post-merge, ne bloque personne |

Ne lancez **jamais** une passe complète après chaque édition : c'est le meilleur moyen
de faire désactiver l'outil au bout d'une semaine.

Point non évident : les mutants **sémantiques** —
générés par un modèle à partir de l'exigence, pas de la syntaxe — se sont révélés
nettement plus difficiles à détecter que les mutants syntaxiques, et exposent des trous
que les outils déterministes manquent, y compris à couverture complète. Autrement dit :
un modèle est un bon adversaire pour les tests d'un autre modèle.

## 6. Ce qu'on mesure, en pratique

Quatre indicateurs suffisent. Au-delà, personne ne les regarde.

| Indicateur | Où le prendre | Ce qu'il révèle |
|---|---|---|
| Taux de tâches vérifiées par le juge | `make score` | le débit réel, pas l'activité |
| Coût par critère satisfait | `make score` (tokens du dernier relevé ÷ critères satisfaits) | la seule métrique de coût qui a du sens |
| Score de mutation sur les zones à risque | `make mutants` | la solidité du vérificateur |
| Taux de régression post-merge | étage 3 de la CI | ce que la vérification a laissé passer |

`make score` lit `.verify/journal.jsonl` : **une ligne par `make judge`**, écrite
automatiquement, qui survit à `make clean`. Le rapport détaillé d'une tâche, lui, est
écrasé à chaque exécution — sans ce journal, aucune tendance n'est observable et le
rituel ci-dessous reste une intention. `make score TASK=<ID>` ne regarde qu'une tâche.
Ce que le journal ne contient pas, la commande ne l'invente pas : elle dit où le prendre.

Le piège classique est de suivre le coût par session. Un harnais qui double la dépense
et triple le nombre de critères réellement validés est un bon harnais ; la métrique par
session le condamnerait.

## 7. Le rituel de désinstallation, composant par composant

> À faire à chaque montée de modèle. Chaque composant d'Ephor incarne une hypothèse
> sur une faiblesse du modèle. Une hypothèse périmée coûte des tokens et de la confiance.
> L'ordre ci-dessous va du moins risqué au plus risqué à retirer.

Retirer un garde-fou se fait dans `.claude/settings.json`, puis `make adapters` pour que
les deux autres outils suivent. Retirer un sous-agent, c'est supprimer son fichier de
`.claude/agents/`. Dans les deux cas : trois exécutions du lot avant et après, et on
compare les tendances de `make score`, jamais une exécution isolée.

| Composant | Hypothèse qu'il incarne | Ce qui décide de le garder |
|---|---|---|
| `filter-output.sh` | le modèle se noie dans les sorties longues | le coût par critère remonte-t-il sans lui ? |
| `checkpoint.sh` | le modèle perd du travail | `make checkpoints` a-t-il servi une seule fois ce trimestre ? |
| `steer.sh` | le modèle ignore une consigne donnée en cours de route | `STEER.md` a-t-il servi ? |
| `repo_map.py` (`make map`) | le modèle lit trop de fichiers pour se repérer | le coût par critère baisse-t-il avec ? |
| sous-agent `scout` | le modèle explore mal tout seul | le taux de tâches vérifiées bouge-t-il ? |
| sous-agent `evaluator` | le modèle ne voit pas ses propres défauts | trouve-t-il des défauts réels, ou confirme-t-il ? |
| `budget.sh` | le modèle part en boucle coûteuse | le plafond s'est-il déjà déclenché à bon escient ? |
| `block-secrets.sh` | le modèle écrit des secrets | a-t-il refusé une écriture légitime plus souvent qu'un secret ? |
| `scope-guard.sh` | le modèle écrit hors périmètre | le juge attraperait le même cas, mais plus tard et plus cher |
| `sentinelle.sh` | le modèle contourne un refus par un autre chemin (terminal, script) | a-t-elle arrêté un vrai contournement, ou surtout des retouches de l'opérateur en cours de tour ? |
| sous-agent `planner` | le modèle saute l'étape du contrat | les contrats écrits sans lui sont-ils tranchables ? |
| le juge | — | **ne se retire pas** : c'est la seule preuve qu'un agent n'a pas triché |
| contrôle des données personnelles | — | **ne se retire pas** : c'est une exigence de conformité, pas une hypothèse sur le modèle |

Un composant qu'on ne sait pas mesurer est un composant qu'on garde par superstition.
Si la question de la colonne de droite n'a pas de réponse chiffrée après trois lots,
c'est le lot de tâches qui est trop petit, pas le composant qui est bon.

## 8. Erreurs à éviter

**Évaluer sur des tâches de benchmark public.** Elles sont dans les données
d'entraînement et ne ressemblent pas à votre dépôt. Utilisez vos propres tickets résolus.

**Bloquer sur une exécution unique.** Le bruit d'infrastructure dans les évaluations
d'agents est réel et mesuré. On suit des tendances sur plusieurs exécutions.

**Assertionner sur la sortie finale.** C'est ce qui rend une évaluation fragile et
coûteuse à maintenir. Portez les assertions sur les actions observables.

**Confondre score de mutation et qualité.** Un score de mutation élevé sur du code sans
intérêt ne vaut rien. Ciblez les zones à risque, pas la moyenne du dépôt.
