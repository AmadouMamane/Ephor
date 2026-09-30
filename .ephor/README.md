# `.ephor/` — le moteur d'Ephor

> Le juge, les garde-fous, les scripts, le greffon Copilot et la documentation d'Ephor.
> Ce dossier est **le même dans chaque projet équipé**. Dans un projet, on ne le modifie
> pas : on le remplace par la version suivante. Seul `projet/` appartient au projet.

## Ce dossier

| Chemin | Ce que c'est | À qui |
|---|---|---|
| `.ephor/projet/` | ce que **ce** projet règle : ses étapes de vérification (`verify.sh`), les exceptions du contrôle des données personnelles, les dépôts qu'on a le droit de lire ; plus tard sa politique de livraison et le scanner de sécurité de l'entreprise | **au projet** — une mise à jour d'Ephor n'y touche jamais |
| `.ephor/*.py`, `.ephor/contract.schema.json` | le juge (`judge.py`) et la forme d'un contrat ; ses outils : compteur de tokens (`usage.py`), contrôle des réglages GitLab (`gitlab_check.py`), tableau de bord (`score.py`), carte du code (`repo_map.py`), dépôts de référence (`refs.py`), aspirateur de docs (`fetch_docs.py`), contrôle des données personnelles (`personal_data.py`), adaptateurs opencode et Copilot (`adapters.py`) | Ephor |
| `.ephor/zones.txt` | la liste des zones de contrôle, la seule : juge, gardes, sentinelle et CI la lisent ; les réglages du projet peuvent l'allonger, jamais la raccourcir | Ephor |
| `.ephor/hooks/` | les garde-fous, communs aux trois agents ; les outils n'en appellent que les quatre points d'entrée (`avant-outil.sh`, `apres-outil.sh`, `debut-tour.sh`, `fin-tour.sh`), qui choisissent les gardes (ADR 0011) | Ephor |
| `.ephor/opencode/` | le greffon d'opencode ; `.opencode/plugins/ephor.js`, dans le projet, ne fait que l'importer | Ephor |
| `.ephor/scripts/` | diagnostic, revue, mutation, installation sur un dépôt existant… | Ephor |
| `.ephor/deploy/` | manifeste de livraison, image de CI, principes de livraison | Ephor |
| `.ephor/greffon-copilot/` | le relais que `make copilot-install` pose sur le poste | Ephor (deux fichiers régénérés par `make adapters`) |
| `.ephor/docs/` | la documentation d'Ephor : exploitation, tokens, outillage, évaluation, installation, trois agents, GitLab, décisions | Ephor |
| `.ephor/tests/` | les tests d'Ephor (`make ephor-test`) — seulement dans le dépôt d'Ephor | Ephor |
| `.ephor/ruff.toml` | les règles de lint d'Ephor, appliquées à ce dossier seul | Ephor |
| `.ephor/VERSION`, `.ephor/CHANGELOG.md` | la version en place, et ce que chacune a changé | Ephor |
| `.ephor/empreintes.txt` | les versions livrées de ce qu'Ephor pose hors du moteur (greffon d'opencode, réglages et sous-agents de Claude Code, skills, `Makefile`) : une mise à jour remplace un tel fichier s'il est l'une d'elles, donc jamais retouché (ADR 0010) | Ephor |

Pourquoi un dossier caché, et un seul : Ephor n'encombre plus la racine du projet (une
entrée au lieu de dix), `pytest` et `mypy` ne descendent pas dans un dossier caché, et le
`ruff` du projet y lit les règles d'Ephor au lieu des siennes. Décision : ADR 0006.

Tout `.ephor/` est une zone de contrôle, `projet/` compris : un agent ne peut ni écrire
ses propres garde-fous, ni s'accorder une exception, ni décider de ce que « vert » veut
dire.

## Le juge — `judge.py`

Le juge rend le verdict final d'une tâche confiée à un agent. **Ce verdict ne vient jamais
de l'agent.** Il fonctionne à l'identique avec Claude Code, opencode ou Copilot, en local
comme en CI, car il ne dépend d'aucun de ces outils : c'est un script Python de la
bibliothèque standard, compatible avec le Python 3.9 livré par Apple.

### Ce qu'il fait, dans l'ordre

1. **Contrat approuvé.** Il lit `tasks/<ID>/contract.json` et vérifie que ce fichier existe
   à l'identique sur la base (la branche principale). Un contrat écrit ou retouché sur la
   branche de travail est refusé : l'approbation, c'est le commit sur `main`. Il lit aussi
   sur la base la trace du **contrôle rouge** (`tasks/<ID>/rouge.json`, écrite par
   `make task-check`) : chaque critère y a été vu échouer avant le travail, et aucune preuve
   n'a changé depuis. Absente ou périmée, la tâche est refusée ; il ne relance rien pour
   autant. Si le code de la base a bougé depuis ce contrôle, ou si le contrôle a été fait
   sur un commit que la base ne contient pas, le verdict le dit.
2. **Périmètre.** Il calcule le patch entre la base et l'état courant du dossier, commits,
   modifications et nouveaux fichiers compris, sans toucher à votre index Git. Ce qui n'est
   pas encore commité est nommé, et le verdict le dit : la merge request, elle, part de la
   branche. Tout fichier
   hors de `allowed_paths` est refusé. Les zones de contrôle le sont toujours : celles de
   `.ephor/zones.txt`, la liste du moteur que lisent aussi les gardes, la sentinelle et la
   CI — `tasks/`, `.ephor/` tout entier, la CI, `.git/`, `Makefile` et `GNUmakefile`,
   `AGENTS.md`, `.claude/`… Sans ce fichier, le juge refuse de juger.
   Les fichiers cités par les critères sont des preuves : intouchables eux aussi. Un
   dossier cité (`pytest tests/`) l'est tout entier : si le périmètre permet d'y écrire,
   le contrat est refusé, puisque l'agent y réécrirait sa propre preuve.
   Seule exception, `PROGRESS.md` : c'est l'état tenu par l'agent, jamais une preuve, et la
   boucle de travail lui demande de le tenir à jour. Sous Claude Code et opencode, un
   fichier hors périmètre est d'ailleurs refusé **au moment de l'écriture**, pas seulement
   au verdict (`.ephor/hooks/scope-guard.sh`).
3. **Copie propre.** Il clone le commit de base dans un dossier séparé, sous
   `~/.cache/ephor/judge/`, et n'y applique que le patch. C'est un clone à part, pas un
   `git worktree` : un test qui lance `git config` ou `git commit` dans la copie n'écrit
   plus dans votre dépôt. Git y tourne durci : aucun hook du dépôt,
   aucun moniteur de fichiers. La ruse du hook Git planté par un agent est neutralisée.
   Avant d'exécuter quoi que ce soit, il relève aussi les **silencieux** que le patch
   ajoute (`# noqa`, `# type: ignore`, `pytest.mark.skip`…), les **tests retirés** (un
   fichier de test supprimé, un `test_…` disparu d'un fichier ; un test qui change de
   fichier n'est pas retiré) et les **réglages des contrôles** qu'il touche (un `ruff.toml`
   posé dans un sous-dossier, `setup.cfg`, `tsconfig.json`…) : nommés au verdict, et
   refusés sur une tâche à risque `eleve`. Un test vidé (son corps remplacé par `pass`) ou
   un cas de paramétrage retiré ne se voit pas ainsi : la revue le lit.
   `pyproject.toml` et `package.json` portent aussi les dépendances : le juge lit quelles
   sections changent. Les dépendances et la fiche du paquet (`[project]`,
   `[dependency-groups]`, `[tool.uv]`, `"devDependencies"`…) sont nommées seulement ; toute
   autre section (`[tool.ruff.lint]`, `[tool.pytest.ini_options]`, `"scripts"`, `"jest"`…)
   compte comme un réglage des contrôles. Une dépendance peut pourtant remplacer un outil
   de contrôle (un `pytest` pris dans un dossier local) : la revue la lit.
4. **Exécution.** Installation (`setup`), puis vérification (`verify`, en mode strict : un
   outil absent est un échec), puis chaque critère (`check`). Le tout sous un budget de
   temps ; un critère peut en plus fixer le sien avec `max_duration_seconds`, et il est
   alors coupé et déclaré « trop lent » sans consommer le temps des critères suivants.
   Chaque étape est aussi bornée en sortie (64 Mo), et tout ce qu'elle a lancé en
   arrière-plan est arrêté avec elle. Les commandes ne reçoivent qu'un environnement
   filtré : aucune clé LiteLLM, Anthropic, OpenAI, GitLab ou cloud. Les identifiants du
   dépôt de paquets JFrog ne sont transmis qu'à l'installation.
5. **Preuve allée au bout.** Un code de sortie 0 ne suffit pas : le code jugé peut le
   provoquer avant la première assertion. Selon sa forme, un critère doit imprimer un
   jeton tiré au hasard (script Python), rendre un rapport de tests complet où chaque
   test désigné a réussi (pytest), ou produire la sortie exacte
   attendue (`expected_output`, comparée de l'extérieur). Les autres commandes restent
   jugées sur leur code de retour ; le verdict les nomme, et une tâche à risque `eleve`
   les refuse. Limites : « Ce que le juge ne peut pas voir », plus bas.
6. **Empreintes et fraîcheur.** Il recalcule le patch à la fin. Si le code a bougé pendant
   la vérification, le résultat est périmé. Le rapport garde l'empreinte de chaque
   fichier jugé : le manifeste de livraison refuse ensuite de livrer autre chose.
7. **Rapport.** `.verify/judge/<ID>/latest/` contient `summary.txt` (quelques lignes),
   `report.json`, `junit.xml` (affiché dans la merge request GitLab) et un journal par étape.
   `report.json` porte aussi, pour chaque critère, sa forme de preuve et sa durée, ce que la tâche a coûté en
   tokens, et le commit de chaque dépôt de référence — de quoi rejouer le verdict plus tard.
8. **Trace.** Chaque jugement ajoute une ligne à `.verify/journal.jsonl`, qui survit à
   `make clean` : c'est la matière de `make score` et du rituel de `.ephor/docs/09`. Le rapport
   détaillé, lui, est écrasé à chaque exécution.

### Les verdicts

| Statut | Sens | Code de sortie |
|---|---|---|
| `verified_needs_review` | Tout est passé. Reste la revue humaine : rien n'est fusionné ni livré. | 0 |
| `verification_failed` | Une étape ou un critère a échoué. Lire le journal indiqué, et lui seul. | 1 |
| `budget_exhausted` | Le budget de temps du contrat est épuisé. | 1 |
| `stale` | Le code a changé pendant la vérification. Relancer. | 1 |
| `no_change` | Rien à juger par rapport à la base. | 1 |
| `rejected` | Contrat invalide ou non approuvé, contrôle rouge absent ou périmé, patch hors contrat, preuve que l'agent pourrait écrire lui-même (fichier ou dossier), ou, sur une tâche à risque élevé, silencieux ajouté, test retiré ou réglage des contrôles modifié. Rien n'a été exécuté. | 2 |

### Commandes

```bash
make judge TASK=EXEMPLE-001                   # local
make judge TASK=EXEMPLE-001 RUNTIME=podman    # conteneur, vérification sans réseau
make task-new ID=FEAT-002                     # squelette de contrat (opérateur)
make task-new ID=FEAT-002 FROM=plan.json     # contrat déjà rédigé, vérifié avant écriture
make task-check ID=FEAT-002                   # forme du contrat, puis chaque critère doit ÉCHOUER (écrit rouge.json)
make score [TASK=FEAT-002]                    # ce qu'Ephor produit, d'après les jugements
make refs                                     # dépôts lus en référence, et leur commit
make map REPO=<nom>                           # la forme d'un dépôt de référence
make sec                                      # secrets, données personnelles, scanner de l'entreprise
EPHOR_SAST='sonar-scanner ...' make sec     # branche le scanner de sécurité de l'entreprise
bash .ephor/scripts/demo_judge.sh [local|podman]     # démonstration : honnête, tricheur, maladroit
```

### Exécution en conteneur, sur un Mac M3 avec Podman

`RUNTIME=podman` lance chaque étape dans un conteneur jetable, limité en mémoire, en
processeurs et en processus, et durci : aucun privilège
noyau (`--cap-drop=ALL`), image en lecture seule, `/tmp` en mémoire, aucune image
téléchargée en cours de route (`--pull=never`). L'installation a le réseau, pour joindre
JFrog. La vérification et les critères tournent **sans réseau**. Les rapports de tests
s'écrivent dans un dossier monté à part, hors de la copie jugée. La copie vérifiée est placée sous
votre dossier personnel, car `podman machine` ne partage que lui avec sa machine virtuelle.
Elle n'emprunte rien à votre dépôt : son histoire et les étiquettes de la base y sont
recopiées, pour que git y marche. Un projet qui tire sa version de git (hatch-vcs,
setuptools-scm…) demande une image **avec git** — celle de `make ci-image` ; avec l'image
par défaut, qui n'en a pas, le juge refuse avant de rien lancer, et dit quoi prendre.
Mesuré le 29/09/2026 : sans cela, le conteneur installait en silence une fausse version
(`0.1.dev1` au lieu de `0.2.1.dev1`).

| Variable | Rôle | Défaut |
|---|---|---|
| `EPHOR_IMAGE` | Image du juge, avec `uv`, Python et git (`make ci-image`). En entreprise : une image JFrog, épinglée par digest, en arm64. | `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` (sans git) |
| `EPHOR_CONTAINER_MEMORY`, `EPHOR_CONTAINER_CPUS` | Limites du conteneur | `4g`, `2` |
| `EPHOR_CONTAINER_ARGS` | Options en plus. Sous Podman Linux sans droits root, le juge ajoute seul `--userns=keep-id --user <votre uid>` ; une option `--user`, `--uidmap` ou `--pod` posée ici l'en dispense | vide |
| `EPHOR_CONTAINER_HARDENING` | `0` retire le durcissement, pour une image de la maison qui en aurait besoin. Sous Podman Linux, le conteneur tourne sous votre identité, plus en root : une étape qui écrit dans le système de l'image demande en plus `EPHOR_CONTAINER_ARGS=--user=0` | durci |
| `EPHOR_VOLUME_SUFFIX` | Suffixe du volume ; `:Z` est ajouté seul avec Podman sous Linux (SELinux) | automatique |
| `EPHOR_WORKDIR` | Dossier des copies et des rapports complets | `~/.cache/ephor/judge` |

Mesuré le 24 septembre 2026 sur Mac Apple Silicon, durcissement compris, avec **Podman
6.1.2** (machine `applehv`, sans droits root) comme avec Docker Desktop : les quatre
verdicts de la démonstration sont ceux du local, sur un code juste comme sur un code
tricheur. Une image qui tourne sous un utilisateur ordinaire (uid 1000) passe aussi,
**sans** `--userns=keep-id`, et tout ce que le conteneur écrit vous appartient.

Mesuré le même jour **sous Linux** — Fedora 44, SELinux actif, Podman 6.1.2 sans droits
root, le juge lancé depuis Linux : mêmes quatre verdicts, avec l'image par défaut comme
avec l'image non root. Deux réglages y sont indispensables, et le juge les pose seul :
l'étiquette SELinux `:Z` sur les volumes (sans elle : « Permission denied » dès
l'installation), et l'identité du poste dans le conteneur, `--userns=keep-id` avec
`--user` (sans elle, l'image non root ne peut pas écrire dans la copie jugée ; et
`--userns=keep-id` seul ne suffit pas, l'image garde son utilisateur). Mesuré aussi le
25/09/2026, pour les comptes d'annuaire : un uid de 1 234 567 890 marche avec ces mêmes
options, à une condition — que le compte ait une plage de sous-identifiants
(`/etc/subuid`). Sans elle, Podman ne peut même pas installer une image ordinaire ;
`make doctor` le signale. Docker sous Linux est mesuré à chaque passage de la variante
GitHub de la CI (Ubuntu 24.04, tests du juge en conteneur). Une image qui n'est pas
épinglée par empreinte (`@sha256:`) est signalée au verdict.

### En CI

GitLab : le job `juge` de `.gitlab-ci.yml` tourne sur toute merge request dont la branche
s'appelle `task/<ID>`, contre la branche cible. GitHub : même chose dans
`.github/workflows/pre-queue.yml`. En CI, le dépôt est un clone frais, et **le juge vient
de la branche cible** (`git archive`), jamais de la branche jugée : une branche qui
réécrirait `.ephor/judge.py` ne se juge plus elle-même. Le verdict local n'en est qu'un
aperçu. Le juge y tourne sans conteneur de plus (`--runtime local`, le défaut) : le job
de CI est déjà lui-même le conteneur. Aucun Podman dans la CI, donc.

**Pour que cette porte fasse vraiment foi, une condition reste à régler dans GitLab.** Une
merge request peut aussi réécrire `.gitlab-ci.yml`, et GitLab lance la CI de la branche
proposée. Il faut donc sortir la configuration de CI de la portée des branches :

- le mieux : réglage du projet « Fichier de configuration CI/CD » pointé vers un projet
  protégé (`.gitlab-ci.yml@groupe/controles`), ou une politique d'exécution de pipeline ;
- au minimum : approbation obligatoire des propriétaires sur les zones de contrôle
  (`.ephor/docs/CODEOWNERS.example`, offres Premium et Ultimate).

Sans l'un des deux, la porte arrête un agent pressé ou maladroit, pas un agent qui
réécrirait la CI elle-même. Les cinq réglages, où cliquer, et la commande qui vérifie
qu'ils sont en place (`make gitlab-check`) : `.ephor/docs/12-gitlab.md`.

### Ce qu'il ne fait pas

- Il ne confine pas un agent malveillant sur votre poste. En local, il protège contre un
  agent pressé ou maladroit. Le conteneur réduit le risque ; la CI tranche.
- Il ne juge pas la qualité du code. La revue humaine, aidée des sous-agents
  `evaluator` et `reviewer`, reste obligatoire.
- Il ne mesure pas les tokens lui-même : il **rapporte** ce que le hook de budget a mesuré
  dans `.verify/usage/<ID>.json`. Sans mesure, `usage` vaut `null` — « non mesuré », jamais
  zéro, qui voudrait dire gratuit. Sous Copilot, tant que la session n'est pas fermée, ses
  tokens manquent : le résumé dit « au moins », avec ce que Copilot a facturé.
- Ses empreintes ne sont pas des signatures : quelqu'un qui peut réécrire les rapports peut
  fabriquer un verdict. D'où la CI, sur une machine que l'agent ne contrôle pas.
- Il ne démasque pas un code qui ment DÉLIBÉRÉMENT à la preuve qui l'importe (remplacer la
  fonction testée à l'import, fabriquer un faux rapport de tests). Ces fraudes s'écrivent
  en clair dans le patch : c'est la revue qui les voit. Détail juste en dessous.

### Ce que le juge ne peut pas voir

Sept façons de lui faire dire « vérifié » sur un code faux ont été reproduites. Six sont
fermées. La septième ne peut pas l'être.

**Fermé — la preuve coupée avant ses assertions.** Un critère était satisfait dès que le
processus sortait en 0. Or la preuve importe le code jugé : une ligne `os._exit(0)` suffit
à couper le processus avant la moindre assertion. Chaque critère doit maintenant prouver
qu'il est allé au bout, et le juge dit, critère par critère, comment :

| Forme du critère | Ce que le juge exige |
|---|---|
| Script Python (`{python} tasks/X/check.py`) | un jeton tiré au hasard, imprimé à la fin du script |
| Test pytest (`uv run pytest tasks/X/test_x.py::test_a`) | un rapport de tests neuf, complet, où chaque test désigné a tourné et réussi — ni sauté, ni « échec attendu » |
| Sortie attendue (`expected_output`) | la sortie exacte, comparée de l'extérieur du processus |
| Toute autre commande | son code de retour, et rien d'autre : le verdict le signale |

La forme pytest est celle d'EXEMPLE-001 et de `make task-new` ; c'est le rapport de tests
qui la rend sûre (ADR 0005). Sur une tâche à risque `eleve`, la dernière forme est
refusée : chaque critère doit prouver qu'il est allé au bout. `make verify` applique la même règle à la suite de tests complète.

**Fermé — la preuve posée à la racine.** Un fichier n'était reconnu comme preuve que si son
chemin contenait une barre oblique. `check_c1.py` à la racine du dépôt n'était donc gardé
par personne : l'agent pouvait le vider avant de se déclarer vainqueur. Une preuve est
maintenant reconnue parce qu'elle existe, pas à la forme de son nom.

**Fermé — la preuve rangée dans un dossier ouvert.** Un critère pouvait citer un dossier
(`uv run pytest tests/`) que le périmètre laissait écrire. Seuls les fichiers cités étaient
gardés : l'agent réécrivait le test d'acceptation rangé dans `tests/`, et un code jamais
corrigé ressortait « VÉRIFIÉ ». Un dossier cité est maintenant une
preuve tout entière, et le contrat qui le laisse écrire est refusé. Un critère pytest qui ne
désigne rien (`uv run pytest -q`) lance ce que sa configuration collecte : le verdict le
nomme, pour que la revue regarde ce qui a changé dans ces dossiers-là.

**Fermé — la preuve réécrite pendant l'exécution.** Le code jugé tourne dès l'étape de
vérification, avec le droit d'écrire dans la copie. Un test de l'agent y réécrivait le test
d'acceptation rangé dans le dossier que le critère citait (`pytest tasks/T-1/`), ou
remplaçait, le temps du jugement, un fichier faux du correctif par un juste : le code vérifié
n'était plus celui qui serait livré. Après chaque étape, le juge
relève maintenant tout ce que contiennent les dossiers de preuve et celui de la tâche, tout
fichier qui s'exécute sans être appelé ou règle les contrôles (un `conftest.py` apparu en
cours de route compris), et tout fichier suivi qui s'écarte de ce qui a été installé : un
écart fait échouer le jugement et est nommé.

**Fermé — la preuve abîmée puis remise en état.** Les preuves étaient comparées une seule
fois, à la fin de l'exécution. Un agent qui abîme une preuve, la fait passer, puis la
remet d'aplomb ressortait « VÉRIFIÉ » avec du code faux. Leur empreinte est maintenant
relevée avant la première étape et recomparée après chacune — et elle compte la date de
modification, pas seulement le contenu. Un processus laissé en arrière-plan par une étape
est arrêté avec elle : il ne peut plus écrire après le relevé.

**Fermé — le juge de la branche jugée.** En CI, le juge était lu dans la branche qu'il
jugeait : une branche qui réécrivait `.ephor/judge.py` pour toujours dire « oui » passait.
Il vient maintenant de la branche cible. Reste une condition, côté GitLab : la
configuration de CI elle-même ne doit pas être modifiable par une merge request (voir
« En CI », plus haut).

**Pas fermé, et ça ne le sera pas — la preuve trompée à l'import.** La preuve et le code
qu'elle juge tournent dans le même processus. Si le contrat autorise l'agent à écrire le
fichier d'initialisation d'un paquet (`src/__init__.py`), celui-ci s'exécute AVANT la
fonction testée et peut la remplacer par une fausse. De même, un code qui fabriquerait
délibérément un faux rapport de tests passerait. Il n'existe pas de parade dans le juge :
un code peut toujours mentir au code qui l'importe. La différence, c'est que ces fraudes
doivent s'écrire noir sur blanc dans le patch, alors que l'erreur fermée plus haut tenait
en une ligne anodine.

La même fraude a une forme plus discrète : dans un projet installé en mode éditable, `src/`
est sur le chemin de Python, et un `src/sitecustomize.py` s'exécute dans **chaque** processus
Python du projet, avant pytest (mesuré le 25/09/2026). Le juge nomme au verdict tout fichier
qui s'exécute sans être appelé (`sitecustomize.py`, `usercustomize.py`, `*.pth`,
`conftest.py`, `__init__.py`), et refuse les trois premiers sur une tâche à risque élevé.

Même famille, et pas fermée non plus : l'environnement installé (`.venv/`, les paquets
de `node_modules/`) n'est pas relevé — trop gros pour l'être à chaque étape. Un code qui y
glisserait un `.pth`, ou retoucherait pytest lui-même, pendant la vérification, tromperait
les critères suivants. Là encore, il faut l'écrire dans le correctif, où la revue le lit.

Ce qui reste, et qui suffit en pratique :

- **Préférer la sortie attendue pour les critères critiques.** Elle est comparée de
  l'extérieur du processus : le code jugé ne peut pas la fabriquer sans la produire.
- **Restreindre `allowed_paths` au strict nécessaire.** Un `src/__init__.py` dans le
  périmètre sans raison est un risque gratuit. Le choix des chemins est un acte humain,
  c'est exactement pour cela que `make task-new` et `make task-check` ne sont pas donnés
  aux agents.
- **Lire le patch.** Le verdict nomme les silencieux ajoutés (`noqa`, `type: ignore`,
  `skip`…), les fichiers qui s'exécutent sans être appelés, et les critères jugés sur leur
  seul code de retour : c'est là que la revue regarde d'abord. Le juge ne fusionne rien ; la revue humaine est le dernier maillon.
- **La CI.** Elle rejoue tout hors de la machine de l'agent, avec le juge de la cible.
