# ADR 0006 — Tout Ephor dans un seul dossier caché, `.ephor/`

- **Date** : 2026-09-24
- **Statut** : accepté

## Contexte

Ephor ne s'installe pas sur une machine : il se copie dans chaque projet, pour qu'un
projet reste vérifiable même si on lui retire l'agent. Mesuré sur un vrai dépôt Node, une
première disposition, répartie à la racine, y posait 86 fichiers sur **dix-sept entrées de
sa racine** — `ephor/`, `scripts/`,
`deploy/`, `VERSION`, `reference-repos.json`, `.gitleaks.toml`, `.copilot/`… — et
déversait dix-sept squelettes dans son `docs/`, décisions d'Ephor comprises (« nommer le
produit Ephor » atterrissait dans les ADR du projet). Trois défauts en découlaient :

1. **L'encombrement.** La racine d'un projet qui n'est pas le nôtre, occupée par nos noms.
2. **Le premier `make verify` rouge.** Le lint du projet analysait le code d'Ephor avec les
   règles du projet.
3. **Les collisions.** Un projet a souvent son propre `scripts/`, son `deploy/`, son
   `VERSION` : Ephor les déclarait zones de contrôle, ou les lisait comme les siens — le
   greffon Copilot annonçait la version du projet au lieu de celle d'Ephor.

Une idée reçue est tombée en route : « les outils sautent les dossiers cachés ». `pytest`
et `mypy`, oui. `ruff`, non — mesuré. Un dossier caché ne suffisait donc pas.

## Décision

**Tout ce qui est Ephor tient dans `.ephor/`** : le juge et ses outils, les garde-fous
(sortis de `.claude/hooks/` : ils servent les trois agents, pas Claude seul), les scripts,
la livraison, le greffon Copilot, la version, la documentation et les décisions d'Ephor,
ses tests. Règle : **dans un projet, on ne modifie pas `.ephor/`, on le remplace.**

**Une seule exception, et elle se voit : `.ephor/projet/`.** Ce que chaque projet règle
pour lui — ses étapes de vérification (`verify.sh`), ses exceptions au contrôle des données
personnelles, les dépôts qu'il lit en référence, plus tard sa politique de livraison et son
scanner de sécurité. Le script d'installation le pose une fois et ne le remplace jamais.

**`.ephor/ruff.toml`** porte les règles de lint d'Ephor. `ruff` prend, pour chaque
fichier, la configuration la plus proche : le code d'Ephor est jugé sur ses règles à lui,
quel que soit le projet, et le lint du projet ne le remonte plus.

**La documentation se sépare en deux.** Celle d'Ephor (exploitation, tokens, outillage,
évaluation, installation, trois agents, ADR 0002 et suivantes) voyage dans `.ephor/docs/`,
et les renvois des messages restent justes dans chaque projet. Les squelettes du projet
(`docs/00` à `06`, modèle d'ADR, fiches de composant) restent dans `docs/`, et le script
ne les pose plus que sur demande (`--docs`). Deux documents mêlaient les deux : la partie
d'Ephor de la note de qualité (« ce que le juge ne peut pas voir ») est passée dans
`.ephor/README.md`, celle du runbook dans `.ephor/docs/06-exploitation.md`.

**Restent à la racine, exprès :** les réglages des trois agents, à l'adresse que chacun
impose (`.claude/`, `.opencode/`, `.github/`, `.vscode/`) ; et l'interface de travail
qu'un humain touche tous les jours (`Makefile`, `tasks/`, `PROGRESS.md`, `STEER.md`).

## Conséquences

- Un dépôt équipé gagne **neuf entrées** à sa racine au lieu de dix-sept, dont cinq
  cachées ; son `docs/` n'est plus touché sans `--docs`.
- Le `scripts/` et le `deploy/` **d'un projet** redeviennent à lui : ce ne sont plus des
  zones de contrôle, le contrat de la tâche décide. `.ephor/` tout entier en est une.
- Dans le dépôt d'Ephor, la documentation du produit est sous un dossier caché. Le README
  y mène par des liens ; c'est le prix d'un dépôt qui est à la fois le produit et le modèle.
- Le greffon Copilot, posé une fois par poste, cherche les garde-fous à la nouvelle adresse
  puis à l'ancienne : un même poste sert des projets à des versions différentes.
- Un garde (`.ephor/tests/test_rangement.py`) refuse tout chemin de l'ancienne disposition
  hors des ADR et de `.gitleaks.toml`. Il a trouvé ce qu'une relecture avait manqué : le
  hook de budget lisait encore `$ROOT/ephor/usage.py`, et se serait tu sans rien mesurer.
- **Un défaut trouvé en jugeant une vraie tâche dans un dépôt équipé.** Le
  juge importe un module voisin ; Python déposait alors son cache compilé dans le dossier
  d'Ephor, et dans un dépôt qui n'ignore pas `__pycache__/` — un dépôt Node — le juge
  prenait ce cache pour une retouche de ses propres fichiers : toute tâche était REFUSÉE
  ou PÉRIMÉE. Le dépôt d'Ephor ignore ces caches, d'où le silence des tests. Le juge n'écrit
  plus de cache, et l'installation ajoute `.ephor/**/__pycache__/` au `.gitignore` du
  projet. Un test juge maintenant une tâche de bout en bout dans un dépôt Node équipé.
- **Un cache ignoré ne doit pas non plus être LU.** Le revers : un cache fabriqué à la main, ignoré donc absent du patch, s'exécutait à la place
  de `refs.py` dans le juge. Le juge et les commandes `make` d'Ephor dirigent maintenant
  leurs caches vers un chemin qui ne peut pas exister (`/dev/null/…`) : aucun n'est lu ni
  écrit. La CI, qui part d'un clone frais, n'était pas exposée.
- **Un projet équipé avec la première disposition** voit ses réglages repris (dépôts de
  référence, exceptions, scanner, politique de livraison, étapes de vérification aux chemins
  adaptés) dans `.ephor/projet/` avant qu'y soient posées les valeurs par défaut, puis le
  script dit quels anciens fichiers retirer. Sans ça, ces réglages disparaîtraient en
  silence, et la garde des dépôts de référence comme le scanner ne protégeraient plus rien. Le geste 0 rappelle aussi de relancer `make adapters` : les réglages
  d'opencode et de Copilot sont dérivés de `.claude/settings.json`, et l'étaient de
  l'ancien.
- **Ce que ça prépare.** La frontière entre le moteur et les réglages du projet est
  maintenant un dossier. Mettre Ephor à jour peut devenir « remplacer `.ephor/` sauf
  `projet/` », et le moteur peut un jour devenir un paquet versionné sans que les projets
  changent de forme. Ni l'un ni l'autre n'est fait ici.
