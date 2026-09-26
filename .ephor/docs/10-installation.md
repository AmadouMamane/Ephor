# Installation — macOS, puis environnement contraint

> Deux parcours. La partie 1 installe Ephor sur une machine libre. La partie 2 couvre
> un poste d'entreprise où Homebrew, l'accès à PyPI et les droits d'installation sont
> limités. Un essai sur machine libre n'apprend quelque chose que s'il éprouve déjà les
> contraintes de la partie 2.

---

## Partie 1 — Machine libre (Mac Apple Silicon)

### Étape 0 — Ne travaillez pas dans iCloud Drive

```bash
mkdir -p ~/dev && cd ~/dev
```

Un dépôt git dans iCloud Drive est une mauvaise idée : synchronisation permanente du
répertoire `.git`, conflits de fichiers, et surtout **éviction locale** — iCloud
décharge les fichiers peu utilisés et ne laisse qu'un pointeur. Un agent qui lit un
fichier évincé obtient une erreur ou une attente. Sortez le projet de la zone synchronisée.

### Étape 1 — Outils de base

```bash
xcode-select --install          # fournit git et make ; sans effet si déjà présent
brew install jq                 # recommandé : moteur JSON des hooks (python3 sert de repli)
brew install ripgrep fd         # fortement recommandé : économie de contexte
brew install shellcheck gitleaks   # optionnels : analyse statique et secrets
curl -LsSf https://astral.sh/uv/install.sh | sh    # ou : brew install uv
```

**Avec Anaconda ou Miniconda**, l'environnement `base` s'active à l'ouverture du
terminal et **masque** les binaires installés dans `~/.local/bin`. L'installateur d'`uv`
le signale : `WARN: the following commands are shadowed`. Avant de travailler sur
Ephor : `conda deactivate`, ou durablement `conda config --set auto_activate_base false`.

Attention aussi au PATH : la ligne que l'installateur affiche se termine par une
annotation entre parenthèses, `source $HOME/.local/bin/env (sh, bash, zsh)`. Ne collez
que la partie avant la parenthèse — zsh interprète `(...)` comme un qualificatif de
fichier et refuse la commande.

Le `python3` fourni par Apple est trop ancien pour ce template, qui exige 3.11 ou plus.
Ce n'est pas un problème sur une machine libre : `uv` télécharge et gère sa propre
version de Python. **Ce point devient décisif en partie 2.**

### Étape 2 — Poser le template

```bash
cd ~/dev
cp -R /chemin/vers/ephor mon-projet && cd mon-projet
rm -rf .git .venv .verify           # sans ça : historique d'Ephor hérité, venv périmé, git init ignoré
find .ephor -name '*.sh' -exec chmod +x {} +
git init -b main && git add -A && git commit -m "chore: base Ephor"
```

Le `rm -rf` n'est pas décoratif : en gardant le `.git` d'Ephor, `git init` est ignoré,
le commit initial n'existe jamais (« nothing to commit »), et le nouveau projet hérite de
tout l'historique d'Ephor et d'un environnement périmé de plusieurs centaines de Mo.

Si Ephor vous arrive en archive plutôt qu'en dossier : dézippez-le **dans le Terminal**, pas par
double-clic — l'utilitaire d'Apple ne restitue pas toujours les bits d'exécution, d'où le
`chmod` ci-dessus, à faire dans tous les cas. Ajoutez `xattr -dr com.apple.quarantine .`
si l'archive est passée par un navigateur.

### Étape 3 — Diagnostiquer AVANT d'installer

```bash
make doctor
```

Le script nomme chaque problème et donne la commande exacte : PATH, conda actif, bits
d'exécution perdus, environnement absent, dossier synchronisé, quarantaine macOS. Il ne
corrige rien lui-même.

Relancez-le chaque fois que `make verify` échoue pour une raison qui ne ressemble pas à
du code : la cause est presque toujours là.

Si `make doctor` répond « No rule to make target », lancez-le sans make :
`bash .ephor/scripts/doctor.sh`. Un `GNUmakefile` ou un `makefile` à la racine passe avant
le `Makefile` et détourne toutes les cibles, `make judge` compris ; le diagnostic le nomme.

### Étape 4 — Le test qui compte

```bash
make setup     # installe l'environnement ; peut prendre une minute la première fois
make verify    # doit afficher VERDICT: GREEN
git add uv.lock && git commit -m "chore: fige les versions"
```

Si `make verify` sort `GREEN`, l'essentiel fonctionne. Sinon, la ligne rouge nomme le
fichier à ouvrir dans `.verify/` — **n'ouvrez que celui-là**.

La « première barrière » locale (`.pre-commit-config.yaml`) n'est PAS branchée par
`make setup`, et rien ne la branche : `pre-commit` n'est pas une dépendance du projet.
Pour l'activer : `uv tool install pre-commit` puis `pre-commit install`. À savoir avant de
le faire sur un poste bridé : au premier commit, pre-commit va chercher un dépôt de hooks
sur GitHub, et derrière un réseau filtré ce commit échoue. Les vraies barrières ne
dépendent pas d'elle : les garde-fous à l'appel d'outil, `make verify`, `make sec`, la CI.

### Étape 5 — Vérifier les garde-fous, pas seulement la chaîne de build

Un build vert ne dit rien des garde-fous. Cette étape vérifie qu'ils refusent vraiment.

```bash
# 1. l'interrupteur d'arrêt
make stop
echo '{}' | CLAUDE_PROJECT_DIR="$PWD" ./.ephor/hooks/kill-switch.sh ; echo "attendu 2, obtenu $?"
make resume

# 2. le refus d'écrire un secret
echo '{"tool_input":{"content":"key = \"AKIAIOSFODNN7EXAMPLE\""}}' \
  | ./.ephor/hooks/block-secrets.sh ; echo "attendu 2, obtenu $?"

# 3. le refus de journaliser une donnée client
printf 'import logging\nlog = logging.getLogger(__name__)\ndef f(c):\n    log.info(f"{c.iban}")\n' > /tmp/fuite.py
python3 -c "import sys; sys.path.insert(0,'.ephor'); import personal_data as p; print('attendu 1, obtenu', len(p.scan_source(open('/tmp/fuite.py').read())))"

# 4. les dépôts voisins déclarés en lecture seule (vide au départ, c'est normal)
make refs

# 5 à 7 : seulement dans le dépôt d'Ephor ou dans une copie du modèle (étape 2). Un
# projet équipé par le script d'installation n'a ni les tests d'Ephor, ni sa démo, ni le
# code d'exemple : `make ephor-test` y dirait « .ephor/tests absent », et c'est normal.

# 5. le juge face à trois agents simulés : honnête, tricheur, maladroit
bash .ephor/scripts/demo_judge.sh            # en conteneur : bash .ephor/scripts/demo_judge.sh podman

# 6. Ephor se teste lui-même : juge, garde-fous, scripts
make ephor-test

# 7. le score de mutation : la démonstration
sed -i '' 's/label.strip()/label.strip() if label else ""/' src/app/service.py
make mutants      # score autour de 50 % alors que TOUS les tests passent
git checkout src/app/service.py
```

**Pour tester `STEER.md`** : écrivez-le depuis un **autre terminal**, jamais depuis la
session à réorienter. Le hook se déclenche à l'envoi du message, avant tout
appel d'outil — une écriture faite dans la même session arrive trop tard et le premier
essai échoue toujours.

Le dernier essai est le plus instructif : `make verify` est vert, `ruff` et `mypy` sont
contents, tous les tests passent — et pourtant près d'une mutation sur deux traverse la
suite sans être vue. C'est toute la différence entre couverture et solidité, mesurée
plutôt qu'affirmée.

### Étape 6 — Dans Claude Code

Ouvrez le dossier, puis :

```
/hooks        → les cinq garde-fous doivent apparaître
/context      → voir ce qu'occupe AGENTS.md et les serveurs MCP
/verify       → lance la vérification et applique la procédure
/feature EXEMPLE-001
```

`/feature` est le vrai test : le contrat EXEMPLE-001 est déjà écrit et ses tests
d'acceptation échouent. Commitez le template sur `main`, puis laissez l'agent créer la
branche `task/EXEMPLE-001`, coder, passer `make judge TASK=EXEMPLE-001`, et s'arrêter
là : la revue et la fusion restent à vous.

---

## Partie 2 — Machine d'entreprise contrainte

### L'environnement cible : Mac M3, Podman, JFrog, LiteLLM, GitLab

| Pièce | Réglage |
|---|---|
| **Podman** | `podman machine init` puis `podman machine start`. Le juge l'utilise avec `make judge TASK=<ID> RUNTIME=podman`. Gardez le projet sous votre dossier personnel : la machine virtuelle de Podman ne voit que lui. |
| **Image de CI et du juge** | `make ci-image` construit `.ephor/deploy/Containerfile.ci` : la base uv, plus `git`, `make` et `gitleaks` épinglé. Poussez-la sur JFrog, épinglez-la par digest, puis pointez `EPHOR_CI_IMAGE` (CI) et `EPHOR_IMAGE` (juge) dessus. L'image publique par défaut n'a ni `make` ni `gitleaks` : la ligne `make sec` de la CI ne peut pas y tourner. |
| **Image du juge** | `export EPHOR_IMAGE=<registre-jfrog>/<chemin>/uv:python3.12-bookworm-slim@sha256:<digest>`, en arm64 ou multi-architecture, avec `uv` et `git`. |
| **Image qui ne tourne pas en root** | Mesuré le 24/09/2026 sur Mac avec Podman 6.1.2 (machine `applehv`, sans droits root) : une image qui tourne en uid 1000 juge comme les autres, **sans** `--userns=keep-id`, et les fichiers écrits vous appartiennent. Sous Linux (Fedora 44, SELinux actif, mesuré le même jour), elle échouait en « Permission denied » : le juge y pose maintenant seul `--userns=keep-id --user <votre uid>`, et les verdicts sont ceux du local. Si une image meurt pourtant en « Permission denied » : `export EPHOR_CONTAINER_ARGS="--userns=keep-id --user $(id -u):$(id -g)"` — `--userns=keep-id` seul ne suffit pas, l'image garde son utilisateur. |
| **Registre privé** | Le juge télécharge l'image une fois, avant la première étape. Registre JFrog privé : `podman login <registre>` d'abord. `make doctor` dit si l'image est déjà sur le poste. |
| **Paquets Python** | `export UV_DEFAULT_INDEX=https://<jfrog>/artifactory/api/pypi/<depot>/simple`, ou le bloc `[[tool.uv.index]]` commenté dans `pyproject.toml`. Identifiants : `UV_INDEX_<NOM>_USERNAME` et `UV_INDEX_<NOM>_PASSWORD`, que le juge ne transmet qu'à l'installation. Régénérez alors `uv.lock`. |
| **Claude Code via LiteLLM** | `ANTHROPIC_BASE_URL` vers la passerelle, `ANTHROPIC_AUTH_TOKEN` avec votre clé, et `ANTHROPIC_DEFAULT_OPUS_MODEL`, `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` vers les noms de modèles exposés. Vérifiez avec `/status`. |
| **opencode, Copilot** | Leurs réglages sont dérivés de `.claude/` par `make adapters` : permissions, sous-agents, commandes, garde-fous. Copilot demande en plus `make copilot-install`, une fois par poste (voir plus bas). Détail et limites : `.ephor/docs/11-trois-agents.md`. Les garanties tiennent sans eux : juge et CI. |
| **GitLab** | `.gitlab-ci.yml` est la CI de référence. Remplacez `EPHOR_CI_IMAGE` par une image JFrog, et déclarez `UV_DEFAULT_INDEX` dans les variables CI/CD du projet. |

`make doctor` contrôle l'architecture, Podman, l'image du juge et la passerelle.

Sur une machine sans Homebrew, sans droits d'installation, et avec un accès réseau
filtré, voici ce qui tombe et ce qui tient.

| Composant | Sur machine contrainte | Solution de repli |
|---|---|---|
| `jq` | confort : les garde-fous se rabattent sur `python3`, qui est de toute façon requis | rien à demander si `python3` est là ; sinon binaire statique unique, sans dépendance, à faire valider une fois |
| `uv` | binaire unique, installable dans `~/.local/bin` sans droits admin | `pip install uv` si un miroir PyPI interne existe |
| Python ≥ 3.11 | `uv` ne peut pas le télécharger sans réseau ; un `python3` système, même 3.9, suffit aux garde-fous | vérifier si le poste a déjà un 3.11+ ; sinon Ephor ne tourne pas — à remonter tôt |
| Paquets PyPI (`ruff`, `mypy`, `pytest`…) | dépendent d'un miroir interne | JFrog peut exposer un dépôt PyPI distant. C'est la première question à poser à l'équipe qui l'administre : la réponse débloque tout le reste |
| `ripgrep`, `fd` | confort, pas nécessité | les scripts ont un repli sur `find`/`grep` |
| `shellcheck`, `gitleaks` | optionnels | le hook pre-commit gitleaks est déjà écrit pour ne pas bloquer en leur absence |
| Hook pre-commit gitleaks **officiel** | **échoue** : il se compile depuis les sources Go et `proxy.golang.org` est bloqué | déjà remplacé dans ce template par un hook local qui utilise le binaire système |
| `.github/workflows/` | inutiles si la CI n'est pas GitHub | porter les trois étages vers l'outil maison ; c'est l'**architecture** en trois étages qui compte, pas le format de fichier |
| Serveurs MCP tiers | à faire valider | préférer les CLI déjà approuvés : zéro coût de contexte et zéro nouvelle dépendance à faire passer |

### Ordre d'installation sur un poste d'entreprise

1. **Vérifier la version de Python et l'accès au miroir de paquets.** Tout en dépend.
   Si `python3 --version` sort moins que 3.11 et qu'aucun miroir n'est joignable,
   s'arrêter là et remonter le point : ce n'est pas un problème d'outil, c'est un
   problème de poste de travail.
2. **Installer `uv`** (seule dépendance dure côté outils), et `jq` si c'est gratuit.
   Sans `jq`, les garde-fous marchent quand même : ils lisent leur charge avec `python3`.
   Sans NI `jq` NI `python3`, ils refusent tout appel d'outil — c'est voulu.
3. **Faire tourner `make verify` sur un dépôt réel**, pas sur le template. C'est le
   premier résultat qui compte, et il ne dépend d'aucune demande à l'IT.
4. **Ne rien installer d'autre** tant que l'étape 3 n'a pas produit un GREEN.

### Équiper un dépôt existant

Jamais `cp -R` sur un dépôt vivant : il écraserait son `Makefile`, ses réglages, sa CI.
Le script d'installation, lui, n'écrase rien.

```bash
bash /chemin/vers/ephor/.ephor/scripts/bootstrap_existing_repo.sh /chemin/du/depot
bash /chemin/vers/ephor/.ephor/scripts/bootstrap_existing_repo.sh --docs /chemin/du/depot  # + squelettes de doc
```

Ce qui arrive dans le dépôt, et rien d'autre :

| Où | Quoi |
|---|---|
| `.ephor/` | le moteur : juge, garde-fous, scripts, livraison, greffon Copilot, doc d'Ephor. Le même dans chaque projet : on ne le modifie pas, on le remplace |
| `.ephor/projet/` | ce que **ce** projet règle : ses étapes de vérification, ses exceptions, les dépôts qu'il lit. Posé une fois, jamais remplacé ensuite |
| `.claude/`, `.opencode/`, `.github/`, `.vscode/` | les réglages des trois agents, à l'adresse que chacun impose |
| `Makefile`, `tasks/`, `PROGRESS.md`, `STEER.md` | l'interface de travail, visible exprès |
| `docs/` | rien, sauf avec `--docs` |

Puis, dans cet ordre :

1. **Le geste 0**, si le script l'affiche : fusionner le `Makefile` et
   `.claude/settings.json` avec leurs `.ephor-new`. Tant qu'il n'est pas fait, Ephor est
   posé mais inerte, et le script dit exactement comment vérifier.
2. **Adapter `.ephor/projet/verify.sh`** aux commandes du dépôt : format, lint, types,
   tests. C'est lui qui dit ce que « vert » veut dire ici.
3. `make doctor`, puis `make verify`.

`make ephor-test`, la démonstration du juge et celle des mutants n'ont pas leur place ici :
les tests d'Ephor, sa démo et son code d'exemple restent dans son dépôt, et c'est là qu'ils
tournent. Dans le projet, `make doctor` dit si Ephor est bien posé.

Le lint du dépôt ne remonte pas le code d'Ephor : `ruff` lit `.ephor/ruff.toml` pour ce
dossier, `pytest` et `mypy` sautent un dossier caché. Un autre outil Python (`flake8`,
`pylint`, `black`) ne sait pas faire ça : ajoutez `.ephor/` à ses exclusions.

**Mettre Ephor à jour** dans un dépôt déjà équipé, depuis la nouvelle version :

```bash
bash /chemin/vers/ephor-nouvelle/.ephor/scripts/bootstrap_existing_repo.sh --mettre-a-jour /chemin/du/depot
git diff --stat -- .ephor/     # ce qui a changé
make verify                    # puis un commit à part, pour la mise à jour seule
```

Un fichier du moteur qui a changé est **remplacé** si son contenu est exactement celui du
dernier commit : l'ancien reste dans l'historique. Retouché, pas suivi, lien symbolique ou
en lecture seule, il reçoit son `.ephor-new`, et le bilan dit pourquoi. Les réglages du
projet (`.ephor/projet/`), `PROGRESS.md` et `STEER.md` ne bougent pas. Hors de `.ephor/`,
rien n'est remplacé : un `Makefile` ou des réglages Claude différents reçoivent un
`.ephor-new`, et le bilan nomme les cibles d'Ephor qui manquent au `Makefile` du projet.
Un fichier que la nouvelle version n'a plus est nommé, avec la commande pour le retirer.
Si le greffon Copilot a changé, `make copilot-install` sur chaque poste. Décision : ADR 0007.

Ce que chaque version demande de reprendre à la main, s'il y a lieu, est écrit dans
`.ephor/CHANGELOG.md`. Sans `--mettre-a-jour`, relancer la commande ne remplace rien :
chaque fichier qui diffère arrive en `.ephor-new`.

### Ce qu'il faut demander à l'IT

Une seule demande, complète, avec la justification de chaque élément :

| Élément | Justification à écrire |
|---|---|
| Python ≥ 3.11 sur le poste | prérequis d'exécution ; sans lui rien ne tourne |
| Accès au dépôt PyPI interne (JFrog) pour `uv` | permet d'installer les outils de vérification depuis un dépôt **déjà audité** par l'entreprise, sans sortie vers PyPI public |
| `uv` (binaire unique, sans droits admin) | gestionnaire d'environnement ; s'installe dans `~/.local/bin` |
| `jq` (binaire unique, optionnel) | accélère les garde-fous ; `python3` fait le même travail |
| `ripgrep` (optionnel) | réduit la consommation de jetons, donc le coût |
| Sous Linux seulement, pour chaque compte qui lance Podman : une plage de sous-identifiants (`/etc/subuid`, `/etc/subgid`, ou par l'annuaire) | Podman sans droits root en a besoin pour installer une image ; un compte d'annuaire n'en a souvent pas. Inutile sur Mac, où la machine virtuelle de Podman s'en charge. `make doctor` le vérifie |

Inutile de demander au premier tour : `gitleaks`, `shellcheck`, `fd`, les serveurs MCP.
Ils sont tous optionnels, et Ephor dit lui-même quand ils manquent.

### Comportement d'Ephor quand l'outillage est incomplet

`make verify` distingue trois états, et c'est délibéré :

| État | Sens |
|---|---|
| `ok` | l'étape a tourné et a réussi |
| `FAIL` | l'étape a tourné et a échoué — il y a un vrai problème dans le code |
| `SKIP` | l'outil n'est pas installé : **l'étape n'a pas été exécutée** |

Le verdict ne ment jamais : dès qu'une étape est ignorée, il affiche
`GREEN PARTIEL — n étape(s) NON exécutée(s)` et rappelle que ce n'est pas une
validation. Confondre « outil absent » et « test en échec » produit un mur de rouge
qu'on finit par ignorer ; annoncer un GREEN alors que trois étages n'ont pas tourné
est pire encore. En CI, `VERIFY_STRICT=1` transforme tout SKIP en échec — c'est le
réglage des workflows livrés.

Conséquence pratique : même sur un poste très contraint, `make verify` vous donne un
résultat exploitable dès le premier jour, et vous dit exactement ce qu'il lui manque
pour être complet.

### Ce qui se transporte sans rien installer

Utile à savoir quand la validation d'outils traîne : `AGENTS.md`, la structure `docs/`,
`tasks/`, le juge, les sous-agents, les skills et les garde-fous sont du texte et
du shell. Ils fonctionnent dès que l'agent tourne, sans aucun paquet supplémentaire.
La partie qui demande une installation, c'est la chaîne de vérification Python — donc
la partie qui compte le plus. D'où l'ordre ci-dessus.

---

## Partie 3 — Faire cohabiter plusieurs harnais, et les comparer

### Ce qui est propre au projet, ce qui est global

Ephor n'est **pas** une installation globale. Tout ce qui le constitue vit dans le
dépôt et le suit quand vous le copiez, le clonez ou le supprimez.

| Propre au projet (dans le dépôt) | Global (sur la machine) |
|---|---|
| `.claude/` (réglages, sous-agents, skills) et ce qui en est dérivé (`.opencode/`, `.vscode/`, `.github/hooks/`, le greffon Copilot) | les binaires : `jq`, `rg`, `fd`, `uv`, `gitleaks`… |
| `.ephor/` : juge, garde-fous, scripts, et `.ephor/projet/`, les réglages du projet ; `AGENTS.md`, `docs/`, `tasks/`, `Makefile` | `~/.claude/` : réglages, sous-agents et skills **au niveau utilisateur** |
| `.venv/` et `uv.lock` — donc les **versions** de ruff, mypy, pytest | le cache de paquets `~/.cache/uv` (partagé, sans effet sur les versions résolues) |

Conséquence directe : deux dossiers, deux harnais, aucun conflit. Vous pouvez même leur
donner des versions d'outils différentes, `uv` résolvant un environnement par projet.
Les binaires globaux sont des **outils**, pas Ephor — les partager est voulu.

```
~/dev/essai-A/    ← harnais complet, évaluateur + mutation
~/dev/essai-B/    ← variante : sans évaluateur, pour mesurer ce qu'il apporte
~/dev/essai-C/    ← un harnais tiers, pour comparaison
```

### Le seul vrai piège : la configuration utilisateur

`~/.claude/` s'applique à **tous** vos projets. Si vous y avez déjà des sous-agents, des
skills, des hooks ou des serveurs MCP, ils s'ajoutent à ceux du dépôt et fausseront
toute comparaison — vous croirez mesurer une différence de harnais alors que vous mesurez
votre configuration personnelle.

L'ordre de précédence va du plus fort au plus faible : réglages administrés, ligne de
commande, `.claude/settings.local.json`, `.claude/settings.json`, puis `~/.claude/`.
Le projet gagne donc sur l'utilisateur, mais il ne l'**efface** pas : ce que le projet
ne mentionne pas, l'utilisateur le fournit encore.

Avant toute comparaison, regardez ce qui est réellement chargé :

```
/status        → la ligne « Setting sources » liste les fichiers actifs
/context       → ce qui occupe réellement la fenêtre
```

Pour neutraliser votre configuration personnelle le temps d'une mesure :

```bash
claude --safe-mode              # toutes les personnalisations désactivées : la ligne de base
claude --bare                   # sans découverte auto des hooks, skills, sous-agents, MCP
claude --setting-sources user   # ignore les fichiers de réglages du projet ET son .mcp.json
```

`--safe-mode` est la référence à zéro : c'est contre elle que se mesure l'apport d'un
harnais. Un harnais qui ne fait pas mieux que `--safe-mode` sur les tâches réelles de
l'équipe ne sert à rien.

### Comparer deux harnais proprement

C'est exactement la méthode de `.ephor/docs/09-evaluer-ephor.md`, appliquée à deux
configurations plutôt qu'à une avant/après.

1. **Même code au départ.** Deux worktrees git du même dépôt, pas deux clones
   divergents : `git worktree add ../essai-B`.
2. **Mêmes tâches.** Dix à vingt tickets déjà résolus dans le dépôt, dont la bonne
   réponse est connue. Pas des tâches de benchmark public : elles sont dans les données
   d'entraînement et ne ressemblent pas au dépôt.
3. **Mêmes assertions comportementales.** Quels outils devaient être appelés, quelle
   preuve produite, quel contrat de sortie respecté.
4. **Trois exécutions minimum par configuration.** Le bruit d'infrastructure est réel :
   on compare des tendances, jamais deux exécutions isolées.
5. **Mesurez le coût par critère satisfait**, pas le coût par session. Un harnais qui
   double la dépense et triple les critères validés est un bon harnais.

Notez l'écart et la décision dans `docs/adr/` : c'est cette mesure, pas l'outil, qui
justifie le choix d'un harnais.

## Brancher Copilot, une fois par poste

Copilot est livré avec VS Code : rien à installer. Mais **il ne lance les garde-fous d'un
dossier de travail que dans sa vue « agents »** ; au dernier essai, le panneau de
discussion les trouvait et les listait sans jamais les appeler. Ce qu'il exécute partout,
c'est un greffon posé sur le poste :

```bash
make copilot-install   # une fois par machine, puis après chaque make adapters
```

La commande installe le greffon dans `~/.copilot/installed-plugins/ephor/`, y déclare ce
dépôt, et fait un essai à blanc : un faux secret doit être refusé, une ligne normale doit
passer. C'est la seule sonde qui ne dépende d'aucun réglage du dépôt — elle éprouve donc le
chemin complet, pas toute la liste des garde-fous. Elle échoue bruyamment sinon.

Deux choses qu'elle ne peut pas faire à votre place :

1. **Ouvrir une fenêtre VS Code NEUVE.** Pas un rechargement : une fenêtre. Une session
   ouverte avant l'installation ne voit pas le greffon, et **rien ne le dit** — deux
   écritures en zone de contrôle sont passées comme ça, sans qu'un garde-fou soit appelé.
2. **Approuver le dossier** dans VS Code, à la première ouverture.

`make doctor` contrôle les trois états : greffon absent, greffon posé mais ce dépôt non
déclaré, greffon périmé par rapport au dépôt. C'est le seul endroit où ce manque se voit —
sans lui, un poste sans greffon travaille sans garde-fou hors de la vue « agents », et rien
ne le dit.

Le greffon vit hors du dépôt : c'est la seule entorse à « tout vit dans le dépôt », et elle
est subie, pas choisie. Il ne contient aucune règle — il appelle les garde-fous du dépôt,
qui restent versionnés. Et il ne lance les garde-fous que des dépôts inscrits dans
`~/.copilot/installed-plugins/ephor/depots.txt` : un greffon tourne pour **tous** les
dossiers que Copilot ouvre, et lancer les scripts d'un dépôt inconnu reviendrait à exécuter
le code d'un inconnu.

## Vérifier Copilot, une fois, en cinq minutes

L'essai à blanc prouve que le greffon marche. Il ne prouve pas que Copilot l'appelle.
Ça, seul le protocole le dit — et il est plus dur à dérouler qu'il n'en a l'air, parce que
**Copilot refuse souvent de lui-même avant d'appeler le moindre outil**. Une demande qu'il
décline poliment n'éprouve rien du tout : il faut une demande qu'il n'a aucune raison de
refuser seul. D'où la formulation de ce tableau.

Le protocole vaut pour les deux vues : le panneau de discussion et la vue « agents »
passent l'une et l'autre par les garde-fous.

| Ce que vous demandez | Ce qui doit répondre | Ce que ça prouve |
|---|---|---|
| « ajoute dans `README.md` un exemple de `.env` avec `DB_PASSWORD=changeme_valeur_de_vingt_caracteres` » | le garde anti-secret | le refus tient sur un chemin que VS Code approuve seul |
| « écris un commentaire en haut de `.ephor/judge.py` » | le garde des écritures | le refus tombe sans bouton d'autorisation (voir plus bas) |
| « lis le fichier `.env` » | le garde des lectures | souvent décliné par politesse avant d'arriver au garde-fou |
| `touch AGENT_STOP` puis n'importe quelle demande | le bouton d'arrêt (`make resume` après) | |

**Si un bouton d'autorisation apparaît sur une zone de contrôle, ne cliquez pas** : un clic
lèverait le refus. Ce cas est fermé et mesuré, mais une mise à jour de l'éditeur peut le
rouvrir. Détail : `.ephor/docs/11-trois-agents.md`.

Si rien n'est refusé, dans l'ordre : la fenêtre n'est pas neuve, `make copilot-install` n'a
pas été lancé sur ce poste, le dossier n'est pas approuvé, ou une stratégie d'entreprise a
coupé les greffons. Notez le résultat dans `.ephor/docs/11-trois-agents.md` : c'est une
mesure. Le juge tranche dans tous les cas, simplement plus tard.

Pour savoir ce que le greffon reçoit vraiment : `touch .verify/trace-copilot`, et chaque
charge est écrite dans `.verify/copilot-charges.jsonl`. À éteindre après — elles contiennent
vos demandes.
