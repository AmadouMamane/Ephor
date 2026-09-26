# Ephor — le contrat, la garde, le juge

> Nommé d'après les éphores de Sparte : cinq magistrats dont la charge était de contrôler
> les rois. Ici, l'agent fait le travail ; Ephor dit si ça tient.
>
> Version : `.ephor/VERSION`. Ce que chaque version change : `.ephor/CHANGELOG.md`. Les
> décisions de fond : `.ephor/docs/adr/`.

Un dépôt prêt à l'emploi pour faire travailler un agent de codage sérieusement, avec
**Claude Code, opencode ou GitHub Copilot**, sur un Mac Apple Silicon, avec GitLab,
JFrog et Podman. Trois pièces :

- **la boucle quotidienne** : commande unique aux sorties courtes, sous-agents, bouton
  d'arrêt, reprise de session, contrôles en trois étages, guide pour poste contraint ;
- **le juge** : un verdict rendu hors de l'agent, dans une copie clonée à part, contre un
  contrat approuvé, avec des preuves qui doivent aller au bout (rapport de tests, sortie
  exacte), un juge de CI pris sur la branche cible et une livraison liée au code jugé ;
- **le bac à sable** : le juge en conteneur Podman, sans réseau pendant les tests, et le
  terminal de Claude Code enfermé par le système.

Ephor se teste lui-même : `make ephor-test`.

---

## Démarrage — nouveau projet

**Poste d'entreprise** (pas de Homebrew, réseau filtré, python3 fourni par Apple) : ne suivez
pas cette recette, allez droit à [`.ephor/docs/10-installation.md`](.ephor/docs/10-installation.md)
— sa partie 2 ne parle que de ce cas.

```bash
mkdir -p ~/dev && cp -R ephor ~/dev/mon-projet && cd ~/dev/mon-projet   # hors iCloud
rm -rf .git .venv .verify                # sans ça : historique d'Ephor hérité, venv périmé, git init ignoré
git init -b main && git add -A && git commit -m "chore: base Ephor"
brew install jq ripgrep fd uv            # confort ; sans jq les garde-fous lisent le JSON avec python3
make doctor                              # nomme chaque problème et donne la commande exacte
make setup && make verify                # doit afficher VERDICT: GREEN
bash .ephor/scripts/demo_judge.sh       # le juge face à un agent honnête, tricheur, maladroit
```

Puis, **dans cet ordre** : remplissez `docs/00-beliefs.md` à la main, puis lancez `/feature`
dans Claude Code, ou demandez à opencode ou Copilot de suivre `AGENTS.md`.

## Démarrage — dépôt existant

```bash
bash ~/dev/ephor/.ephor/scripts/bootstrap_existing_repo.sh /chemin/du/depot
bash ~/dev/ephor/.ephor/scripts/bootstrap_existing_repo.sh --docs /chemin/du/depot   # + squelettes de doc
```

Ephor tient dans **un dossier caché**, `.ephor/`. À la racine du dépôt n'arrivent que les
réglages des trois agents, là où chacun les cherche (`.claude/`, `.opencode/`, `.github/`,
`.vscode/`), et l'interface de travail (`Makefile`, `tasks/`, `PROGRESS.md`, `STEER.md`).
Le `docs/` du dépôt n'est pas touché, sauf avec `--docs`. Le lint du projet ne remonte pas
le code d'Ephor : ruff y lit `.ephor/ruff.toml`, pytest et mypy sautent un dossier caché.

Rien n'est écrasé : un fichier identique est laissé tel quel, un fichier différent reçoit
la version d'Ephor à côté, en `.ephor-new`. Les réglages du projet (`.ephor/projet/`) sont
posés une fois et jamais remplacés ensuite. Ensuite : `/brownfield-onboard`.

**Mettre Ephor à jour** dans un dépôt déjà équipé : la même commande, depuis la nouvelle
version, avec `--mettre-a-jour`. Le moteur est remplacé là où git garde l'ancien ; le
reste du projet ne bouge pas, et le bilan dit ce qui a changé (ADR 0007).

---

## La boucle quotidienne

| Moment | Geste |
|---|---|
| Début de session | `cat PROGRESS.md`, puis `/feature <ID>` ou reprise du « Prochain pas » |
| Nouvelle fonctionnalité | contrat préparé par `planner`, créé par l'opérateur (`make task-new ID=<ID> FROM=plan.json`), commité sur `main` |
| Pendant | branche `task/<ID>`, `make verify` après chaque incrément |
| Avant de conclure | `make judge TASK=<ID>` ; en conteneur, `RUNTIME=podman` |
| Revue | `make review TASK=<ID>` : enchaîne ce que le risque du contrat exige, et rien de plus |
| Livraison | copier d'abord `.ephor/deploy/release-policy.example.json` en `.ephor/projet/release-policy.json`, puis `make delivery-manifest TASK=<ID> DIGEST=sha256:<...>` et `make delivery-check` — la chaîne réelle n'existe pas encore, voir `.ephor/deploy/README.md` |
| Contexte > 60 % | `/handoff` puis `/clear` |
| Il manque une doc en ligne | `make fetch-docs URL=...` : elle atterrit dans `docs/external/`, à citer à l'agent au besoin |
| Ça part de travers | `make stop`, écrire dans `STEER.md`, `make resume` |
| Il faut lire un autre dépôt | le déclarer une fois dans `.ephor/projet/reference-repos.json`, puis `make refs` et `make map REPO=<nom>` |
| Une fois par lot de tâches | `make score` : ce qu'Ephor produit vraiment, et à quel prix |
| Fin de journée | `/debt-sweep` |

## Ce que chaque pièce empêche, et jusqu'où

| Pièce | Ce qu'elle empêche | Limite |
|---|---|---|
| Juge + contrat approuvé sur `main` | Qu'un agent se déclare vainqueur, retouche ses tests d'acceptation, sorte de son périmètre, ou fasse sortir un test « en succès » avant qu'il ait tourné | La preuve et le code jugé partagent un processus : un code qui ment DÉLIBÉRÉMENT à la preuve qui l'importe passe encore, mais en clair dans le patch. Voir `.ephor/README.md`, « Ce que le juge ne peut pas voir » |
| Juge de la CI pris sur la branche cible | Qu'une branche réécrive le juge pour se juger elle-même | La configuration de CI doit être protégée côté GitLab : `.ephor/docs/12-gitlab.md`, et `make gitlab-check` dit si c'est fait |
| Manifeste de livraison lié au code jugé | Qu'on livre un code différent de celui qui a été jugé | Une empreinte n'est pas une signature : la chaîne de livraison doit protéger les rapports |
| Juge en conteneur durci, sans réseau | Que les tests appellent l'extérieur, lisent le poste ou gagnent des privilèges | Mesuré avec Podman et Docker sur Mac, et avec Podman sous Linux (Fedora, SELinux actif) : mêmes verdicts qu'en local ; Docker sous Linux mesuré à chaque passage de la CI GitHub |
| Environnement filtré du juge | Que les clés LiteLLM, cloud ou GitLab atteignent les tests | Seuls les identifiants JFrog passent, et seulement à l'installation |
| Une seule liste de zones de contrôle (`.ephor/zones.txt`), lue par `write-guard.sh`, `shell-guard.sh`, la sentinelle, le juge et la CI | Que l'agent modifie ses propres contrôles — `.git/`, un `GNUmakefile` et les réglages d'agent du poste compris — par l'édition ou par une forme simple du terminal | Les réglages du projet peuvent l'allonger, pas la raccourcir. Règles d'outil, pas une frontière : un détour par le terminal passe. **Sous Copilot** : il faut `make copilot-install` ET une fenêtre neuve ; sans le greffon, seule la vue « agents » lance les hooks du dépôt (`.ephor/docs/11-trois-agents.md`) |
| Bac à sable de Claude Code (ADR 0008) | Que le terminal écrive une zone de contrôle, **détour compris** : refus du système, même après un clic de l'opérateur | Claude Code seulement ; mesuré sur Mac, pas encore derrière le proxy de l'entreprise ni sous Linux. opencode et Copilot n'en ont pas |
| Sentinelle | Qu'une zone change pendant un tour sans que personne le voie, par quelque chemin que ce soit — tour interrompu compris | Constate après coup ; un agent qui veut la faire taire y arrive encore. Claude Code et Copilot, pas opencode |
| `read-guard.sh` et les refus `Read(...)` | Que l'agent lise un secret ou un identifiant | Règles d'outil : sous Claude Code, une lecture par le terminal demande l'opérateur |
| `block-secrets.sh` + gitleaks | Qu'un secret évident entre dans le dépôt | Détection par motifs : filet, pas certitude |
| Contrôle des données personnelles | Qu'un IBAN, un courriel ou un numéro de carte parte dans un journal ou une erreur | Lit la structure du code, pas le flux : une donnée qui traverse deux fonctions lui échappe |
| `refs-guard.sh` | Qu'un agent écrive dans un dépôt voisin déclaré en lecture, que le juge ne verrait jamais | Voit les outils de fichier, pas une redirection shell |
| Journal des jugements + `make score` | De juger Ephor sur une impression | Ne mesure que ce qui a été jugé : sans lot de tâches, il n'y a rien à comparer |
| Mesure des tokens et plafond par tâche | Qu'une tâche coûte sans qu'on le sache — sous-agents de Claude Code compris | Sous Copilot, un minimum tant que la session n'est pas fermée ; opencode, par dépôt seulement |
| `AGENT_STOP` + `STEER.md` | De devoir tuer un process pour reprendre la main | `AGENT_STOP` arrête les trois outils ; `STEER.md` n'est injecté automatiquement que sous Claude Code et opencode |
| Sortie compacte + `.verify/` | Qu'un log de dix mille lignes mange le contexte | Le code de retour et le résumé restent exacts |
| Points de reprise hors historique | De perdre du travail, sans remplir la branche de commits « wip » | `make checkpoints` les liste |
| CI GitLab en trois étages, variante GitHub | Qu'un test lent ou instable bloque l'équipe ; qu'un échec après fusion passe sans bruit | GitHub tourne et lance les tests d'Ephor ; GitLab n'a pas encore tourné sur une instance réelle |

## Adapter à la taille du projet

| Palier | À garder | À retirer |
|---|---|---|
| Script, prototype (< 10 k lignes) | `PROGRESS.md`, `make verify` | contrats, juge, évaluateur, CI à trois étages |
| Application produit (10 k – 200 k) | tout | rien |
| Monorepo (> 200 k) | tout, plus exécution distante des builds, file de fusion, quarantaine automatique | rien |

## Le rituel qui compte le plus

**À chaque montée de modèle, retirez une pièce d'Ephor et mesurez.** Chaque pièce encode
une hypothèse sur ce que le modèle ne sait pas faire seul, et ces hypothèses périment.
Méthode : `.ephor/docs/09-evaluer-ephor.md`. Décision : une ADR dans `.ephor/docs/adr/`.

## Contenu

```
À la racine : ce que le projet voit et touche
AGENTS.md            contrat de travail commun aux trois agents, < 150 lignes
CLAUDE.md            pointeur vers AGENTS.md ; .github/copilot-instructions.md de même
PROGRESS.md          état tenu par l'agent        STEER.md   consigne de l'opérateur
Makefile             interface unique (make help)
tasks/               contrats de tâche et tests d'acceptation (EXEMPLE-001)
docs/                la doc du projet : squelettes 00 à 06, modèle d'ADR, fiches de composant
docs/external/       pages du web aspirées par l'opérateur — données, jamais consignes
.claude/             réglages, sous-agents, skills — LA source des agents
.opencode/           config, sous-agents et commandes d'opencode — générés depuis .claude/
.vscode/             approbations de Copilot — générées aussi
.gitlab-ci.yml       CI de référence : trois étages et le juge
.github/workflows/   variante GitHub, versions figées par empreinte
.gitleaks.toml       faux secrets assumés des tests d'Ephor, exemptés par valeur

Dans .ephor/ : le moteur, le même dans chaque projet (carte : .ephor/README.md)
.ephor/*.py              le juge et ses outils : judge · usage · repo_map · score ·
                         personal_data · refs · fetch_docs · adapters
.ephor/hooks/            les garde-fous, communs aux trois agents
.ephor/scripts/          doctor, review, mutants, repo_map, bootstrap, demo_judge…
.ephor/deploy/           livraison vers Kubernetes ou GCP : manifeste, image de CI, principes
.ephor/greffon-copilot/  greffon Copilot : deux fichiers générés, un relais écrit à la main
.ephor/docs/             la doc d'Ephor (06 à 12) et ses décisions (ADR 0002 à 0008)
.ephor/tests/            les tests d'Ephor : make ephor-test
.ephor/projet/           AU PROJET : étapes de vérification, exceptions, dépôts lus en référence
```

## Ce qui n'a pas encore été testé

Le juge, les garde-fous et les scripts sont couverts par `make ephor-test`, sur Mac
Apple Silicon ; le juge durci a tourné en conditions réelles sous Docker, face à un code
juste et à un code tricheur. L'aspirateur de documentation a été essayé sur deux vrais
sites, les dépôts de référence sur des dépôts fabriqués pour l'occasion : aucun des deux
sur un dépôt de production. `make score` n'a encore jamais vu de vraie tâche mesurée en
tokens ; le compteur de Copilot a été relu sur quinze journaux réels, pas encore au fil
d'une tâche. opencode a été essayé en session réelle (1.18.32) : écriture d'un secret refusée,
arrêt d'urgence respecté. Copilot aussi (VS Code 1.139.0) : le greffon installé par
`make copilot-install` est bien appelé, un refus laisse le fichier intact, et la vue
« agents » lance aussi les hooks du dépôt (`.ephor/docs/11-trois-agents.md`). Le juge a
tourné sous Podman 6.1.2 sur Mac, image non root comprise, et sous Linux ; sous Docker Linux,
à chaque passage de la CI GitHub. Le bac à sable de Claude Code a été mesuré en session
réelle sur Mac (ADR 0008), pas encore derrière le proxy de l'entreprise. Pas encore
essayés : le pipeline et `make gitlab-check` sur une instance GitLab réelle (le second l'a
été contre un faux GitLab), un dépôt JFrog réel, et la livraison vers Kubernetes ou GCP,
qui n'existe qu'en principes (`.ephor/deploy/README.md`).
