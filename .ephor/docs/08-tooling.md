# L'arsenal — outillage d'Ephor

> Principe de sélection : un outil entre dans cette liste s'il **change ce qu'un agent
> peut faire ou voir**, ou s'il **réduit ce qu'il doit lire**. Tout le reste est du confort
> humain et n'a rien à faire ici. Chaque outil exposé à un agent est une dimension de
> plus dans son espace de décision : Vercel a retiré 80 % des outils de son agent SQL et
> les a remplacés par un terminal en bac à sable ; à modèle constant, l'agent a réussi cinq
> requêtes de test sur cinq au lieu de quatre (décembre 2025). Installez large, **exposez étroit**.

Colonne « Agent » : pourquoi ça compte pour une machine, pas pour vous. Étoiles, licences
et chiffres annoncés : relevés à la source le 26 septembre 2026 ; un chiffre d'éditeur est
dit comme tel.

---

## 1. Le socle — rien ne marche sans

| Outil | Installation | Agent |
|---|---|---|
| **jq** | `brew install jq` / `apt install jq` | Moteur JSON des hooks : ils reçoivent du JSON sur stdin. Sans jq, ils se rabattent sur `python3` ; sans l'un ni l'autre, ils refusent tout. |
| **ripgrep** (`rg`) | `brew install ripgrep` | Remplace `cat` + lecture. `rg -l` localise, `rg -n -C2` cible. Diviseur de tokens n°1. |
| **fd** | `brew install fd` | Recherche de fichiers rapide et respectueuse de `.gitignore` — l'agent ne tombe pas dans `node_modules`. |
| **git ≥ 2.35** | déjà là | `git worktree` : un agent par branche, sans conflit de répertoire de travail. La primitive d'isolation la moins chère qui existe. |
| **gh** | `brew install gh` | PR, issues, CI depuis le shell. **Préférez-le à un serveur MCP GitHub** : zéro coût de listage d'outils en contexte. |
| **make** ou **just** | `brew install just` | L'interface unique. Un agent qui doit deviner la commande de test perd à chaque session. |

## 2. Comprendre le code sans le lire

> Outillage de base. Pour les index, graphes de code et compresseurs de sortie —
> le vrai gisement de tokens — voir la section 3.

| Outil | Installation | Agent |
|---|---|---|
| **ast-grep** (`sg`) | `brew install ast-grep` | Recherche et réécriture **structurelles**. Sert aussi à écrire des linters maison en trois lignes de YAML : c'est ainsi qu'on mécanise un invariant de goût. |
| **tokei** | `brew install tokei` | Volumétrie instantanée. Permet de dire « ce module fait 12 k lignes » sans le lire. |
| **difftastic** | `brew install difftastic` | Diff conscient de la syntaxe : un reformatage n'apparaît plus comme un changement. Divise la taille du diff à relire. |
| **delta** | `brew install git-delta` | Diff lisible, avec contexte réduit. |
| **universal-ctags** / **SCIP** | `brew install universal-ctags` | Index de symboles, pour la navigation. |
| **Plugins de code intelligence** | `/plugin` dans Claude Code | Sur un langage typé, « aller à la définition » remplace un `grep` suivi de trois lectures de fichiers candidats. Remonte aussi les erreurs de type après édition, sans compiler. Gain de contexte majeur sur un gros dépôt. |

## 3. Intelligence de code et compression de contexte

> **La catégorie qui a explosé en 2026, et le plus gros levier de tokens après les
> sous-agents.** L'idée commune : un agent qui interroge un index ne lit pas trente
> fichiers pour en trouver un. Quatre familles, qui ne résolvent pas le même problème —
> ne les empilez pas, choisissez.

### 3.1 Graphes de connaissance du code

Indexation préalable en base locale (tree-sitter + SQLite), servie à l'agent par MCP.
L'agent demande « qui appelle cette fonction, et qu'est-ce que ça casse » au lieu de
`grep` puis lire.

| Outil | Installation | Ce qu'il apporte |
|---|---|---|
| **CodeGraph** (MIT, ≈ 72 k étoiles) | `npm i -g @colbymchenry/codegraph` puis `codegraph install` | Nœuds (fonctions, classes) et arêtes (appels, imports, héritage) sur 20+ langages, SQLite + FTS5, resynchronisation par file watcher natif. Outil MCP principal : `codegraph_explore` — rend le code des symboles pertinents, les chemins d'appel et l'analyse d'impact en **un** appel. 100 % local, rien ne sort. |
| **GitNexus** | voir dépôt | Intégration Claude Code la plus profonde (outils MCP + skills + hooks). **Licence non commerciale** : disqualifiant en banque, à vérifier avant d'y toucher. |
| **CodeGraphContext** (MIT) | backends FalkorDB / KuzuDB / Neo4j | Même idée, base au choix. Utile si vous avez déjà un Neo4j. |

Chiffres annoncés par CodeGraph sur sept dépôts open source : 88 % d'appels d'outils en
moins, 62 % de tokens en moins, zéro lecture de fichier là où la ligne de base en
faisait jusqu'à 19, pour 43 appels d'outils. **Ce sont des mesures de l'éditeur**, non vérifiées ici. Et sa
propre documentation admet le revers — les réponses laissent environ 80 % de contexte de
récupération résident en plus sur une session multi-tours. On échange des lectures de
fichiers contre un contexte plus lourd mais mieux ciblé.

### 3.2 Navigation symbolique (LSP) et recherche sémantique

| Outil | Installation | Ce qu'il apporte |
|---|---|---|
| **Serena** (GPL-3.0, ≈ 30 k étoiles) | `uv tool install -p 3.13 serena-agent` puis `serena init` | Le standard de la navigation symbolique. Serveur MCP adossé aux **language servers** : trouver un symbole, ses références, sa hiérarchie de types — mais aussi **éditer** au niveau symbole (renommer, remplacer un corps de fonction, insérer avant/après, supprimer sans casser). 40+ langages. C'est la différence entre éditer du texte et éditer du code. Licence : le paquet `serena-agent` est sous GPL-3.0, seule sa bibliothèque LSP reste sous MIT — à faire valider avant un usage en entreprise. |
| **grepai** | recherche sémantique locale (Ollama) | Embeddings 100 % locaux + traçage du graphe d'appels. Revendique une réduction massive des tokens d'entrée ; solo mainteneur, à évaluer avant production. |
| **claude-context** | BM25 + embeddings (Milvus/Zilliz) | Passage à l'échelle monorepo. **Envoie le code vers le cloud par défaut** : rédhibitoire en banque sans configuration auto-hébergée explicite. |
| **Octocode MCP** | npm | Recherche multi-dépôts et archéologie GitHub (historique de PR). Utile quand la question est « pourquoi ce code est comme ça ». |

Limite à connaître sur Serena : le backend LSP ne retrouve pas de façon fiable les
déclarations dans les dépendances externes, et certains refactorings avancés sont
réservés au greffon JetBrains payant.

### 3.3 Compression de sortie d'outils — le rattrapage le plus rentable

| Outil | Installation | Ce qu'il apporte |
|---|---|---|
| **RTK** (Rust Token Killer, Apache-2.0, ≈ 82 k étoiles) | `brew install rtk` puis `rtk init -g` | Proxy CLI qui s'interpose entre le shell et le modèle et **compresse la sortie de plus de 100 commandes** (git, pytest, jest, cargo test, tsc, ruff, ls, grep, find, docker, kubectl…). Quatre stratégies : filtrage du bruit, regroupement, troncature conservant le signal, déduplication des lignes répétées. S'installe comme un hook `PreToolUse` qui réécrit `git status` en `rtk git status`. Réduction annoncée : jusqu'à 90 % de la sortie du terminal que lit l'agent — le projet précise lui-même que ce n'est pas 90 % de la facture. |

**C'est exactement le mécanisme que `.ephor/hooks/filter-output.sh` implémente à la
main dans ce template — en vingt lignes et pour les seules commandes de test.** RTK
fait la version industrielle. Si vous pouvez installer un binaire, installez RTK : le hook
maison se désactive tout seul quand il détecte `rtk` sur le PATH. Si l'environnement
interdit les binaires tiers non validés — cas courant en banque — gardez le hook maison
et étendez sa liste de commandes.

Deux limites qui comptent : les outils natifs `Read`, `Grep` et `Glob` de Claude Code
**contournent** RTK, qui n'agit que sur ce qui passe par Bash ; et la réduction réelle
dépend fortement du projet (`rtk discover` sert à repérer les économies plus faibles
que prévu).

### 3.4 Empaquetage de dépôt (usage ponctuel)

| Outil | Installation | Ce qu'il apporte |
|---|---|---|
| **Repomix** | `npx repomix` | Aplatit un dépôt en un seul fichier ; avec `--compress`, tree-sitter n'en garde que la structure (classes, fonctions, interfaces). Utile pour **une** question globale sur un dépôt tiers ; inutile en session de travail, où un index bat un aplatissement. |
| **code2prompt** | `cargo install code2prompt` | Même famille, gabarits Handlebars. Adoption en ralentissement. |
| **Aider repo-map** | intégré à Aider | Sélection dynamique du contexte par tags tree-sitter. À connaître comme référence de conception, pas comme produit. |

### 3.5 Coordination d'agents parallèles

| Outil | Installation | Ce qu'il apporte |
|---|---|---|
| **grit** (Apache-2.0, rtk-ai) | `cargo install --git https://github.com/rtk-ai/grit` | Verrouillage **au niveau fonction** par analyse AST, au lieu du fichier ou de la ligne. Chaque agent réclame les fonctions qu'il modifie, travaille dans son worktree, puis fusionne en série. Répond directement au problème rencontré sur le compilateur C d'Anthropic, où la coordination reposait sur un verrou par fichier dans un répertoire `current_tasks/`. Jeune (une centaine d'étoiles) : à tester, pas à déployer. |
| **icm** (rtk-ai) | binaire unique, MCP natif | Mémoire persistante pour agents. À regarder si `PROGRESS.md` ne suffit plus. |

### 3.6 Le débat qu'il faut savoir arbitrer

Point de tension réel au centre de cette catégorie : **les tests de 2026 favorisent la
récupération indexée ou sémantique, et pourtant Claude Code continue de livrer une
récupération par `grep` uniquement.** Ce n'est pas un oubli, c'est un désaccord
technique. Les arguments de chaque côté :

*Pour l'index* : moins d'appels d'outils, moins de tokens, réponses ciblées, et surtout
la capacité à répondre à « qu'est-ce que ça casse » — que `grep` ne sait pas faire.

*Pour `grep`* : zéro état à maintenir, donc zéro désynchronisation entre l'index et le
code ; aucun faux sentiment de complétude ; aucune dépendance supplémentaire à faire
valider par une DSI ; et les modèles récents sont devenus très bons pour composer des
recherches textuelles.

Recommandation d'Ephor : **RTK d'abord** (gain immédiat, aucun état, réversible en une
commande), **Serena ensuite** si le dépôt est typé et gros — l'édition au niveau symbole
est un gain de qualité autant que de tokens —, **un graphe de code en dernier**, et
seulement si le gain se mesure sur le dépôt lui-même. L'ordre est celui du rapport
bénéfice/complexité, pas celui de la mode.

**Critère décisif en environnement bancaire** : CodeGraph, Serena (backend LSP), grepai
et RTK fonctionnent **100 % en local**, sans sortie de code. `claude-context` envoie le
code vers le cloud par défaut, et GitNexus est sous licence non commerciale. Licence et
flux de données se vérifient avant toute démonstration interne : c'est la première
question posée en revue de sécurité.


## 4. Python — la chaîne

| Outil | Installation | Agent |
|---|---|---|
| **uv** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` | Environnement et dépendances, un à deux ordres de grandeur plus rapide que pip. L'agent attend moins, donc itère plus. `uv run` rend chaque commande reproductible. |
| **ruff** | `uv add --dev ruff` | Lint **et** format en un binaire. Les règles `T20` (pas de `print`) et `S` (sécurité) transforment des consignes d'AGENTS.md en erreurs. |
| **mypy** (ou **pyright**) | `uv add --dev mypy` | En mode `strict`, c'est un vérificateur que l'agent peut interroger seul. Un type est une spécification exécutable. |
| **import-linter** | `uv add --dev import-linter` | **La pièce maîtresse.** Le contrat de couches d'`AGENTS.md` devient une commande qui rougit. Configuré dans `pyproject.toml`. |
| **deptry** | `uv add --dev deptry` | Détecte dépendances déclarées non utilisées, et l'inverse. |
| **pytest** + **xdist** + **randomly** | `uv add --dev pytest pytest-xdist pytest-randomly` | `-n auto` pour le parallélisme ; `randomly` casse les dépendances cachées entre tests, qui sont la première cause de tests instables. |
| **hypothesis** | `uv add --dev hypothesis` | Property-based : trouve les cas limites que ni vous ni l'agent n'aviez imaginés. Excellent complément quand le vérificateur doit être « presque parfait ». |
| **pytest-testmon** | `uv add --dev pytest-testmon` | N'exécute que les tests impactés par le diff. L'étage pré-file passe de minutes à secondes. |
| **vcrpy** / **pytest-recording** | `uv add --dev pytest-recording` | Enregistre les appels réseau (y compris LLM) en cassettes : les tests deviennent déterministes et gratuits. Indispensable pour tester un pipeline RAG. |
| **freezegun** | `uv add --dev freezegun` | Gèle le temps. Redondant si tout passe par `providers.now` — préférez l'architecture. |

## 5. Sécurité et chaîne d'approvisionnement

| Outil | Installation | Agent |
|---|---|---|
| **gitleaks** | `brew install gitleaks` | Deuxième barrière derrière `block-secrets.sh`. À brancher en pre-commit **et** en CI. |
| **semgrep** | `brew install semgrep` | Règles sémantiques, y compris maison. Le seul outil capable d'exprimer « une entrée externe ne doit pas atteindre ce prompt sans assainissement ». |
| **pip-audit** / **osv-scanner** | `uv add --dev pip-audit` | Vulnérabilités des dépendances. Un agent ajoute des paquets vite. |
| **syft** + **grype** | `brew install syft grype` | SBOM et scan d'image. En banque, le SBOM n'est pas optionnel. |
| **trivy** | `brew install trivy` | Alternative tout-en-un image + IaC + secrets. |
| **cosign** | `brew install cosign` | Signature d'artefacts. Piste d'audit de bout en bout. |

## 6. Sécurité de la chaîne d'approvisionnement DES AGENTS

> Catégorie distincte de la sécurité du code, et absente de la plupart des dispositifs.
> Les scanners classiques regardent ce que le code fait. Personne ne regarde ce que
> **les outils de l'agent** font — serveurs MCP, skills, configurations. C'est pourtant
> du code exécutable, souvent installé sur la foi d'un README.

| Outil | Installation | Ce qu'il apporte |
|---|---|---|
| **snyk/agent-scan** (Apache-2.0) | `uvx snyk-agent-scan@latest` (nécessite `SNYK_TOKEN`) | Découvre et analyse les serveurs MCP, les skills et les configurations d'agent sur la machine et dans le dépôt — Claude, Cursor, VS Code, Windsurf, Gemini CLI. Détecte injection de prompt, empoisonnement d'outil, téléchargements suspects, code malveillant, manipulation d'identifiants, secrets, flux toxiques. Drapeau `--ci` pour un code de retour non nul. Déjà câblé dans les deux CI (nuit ou après fusion). Sans `SNYK_TOKEN`, il ne tourne pas, et le dit : un avertissement sur la page du passage (GitHub), un travail « réussi avec avertissements » (GitLab). |

**Le modèle de menace, en trois lignes.** Un serveur MCP tiers voit vos prompts, vos
fichiers et ses propres descriptions d'outils sont injectées dans le contexte du
modèle — un attaquant qui contrôle une description d'outil contrôle en partie le
comportement de l'agent (c'est l'**empoisonnement d'outil**). Un skill est un fichier
Markdown que l'agent lit et exécute. Une configuration d'agent peut auto-approuver des
commandes. Ces trois surfaces sont hors du périmètre d'un SAST classique.

**Ce que ça implique en banque.** Toute installation de serveur MCP ou de skill tiers
devrait passer par une revue et un scan, exactement comme une dépendance logicielle.
C'est un sujet de gouvernance encore rarement traité. Les données publiques sur les CVE
et incidents MCP de 2026 suffisent à documenter le risque.

## 7. Mémoire persistante des agents

> Ce template utilise `PROGRESS.md` et git : durable, inspectable, versionné, zéro
> dépendance. Suffisant pour un dépôt. Au-delà — mémoire partagée entre projets,
> entre sessions, entre agents — il existe une catégorie produit.

| Outil | Approche | Quand |
|---|---|---|
| **Mem0** | mémoire extractive, API ou auto-hébergé | le plus adopté, bon défaut |
| **Zep** | graphe temporel de connaissances | quand « ce qui était vrai à quelle date » compte |
| **Letta** (ex-MemGPT) | agents à état, mémoire gérée par le modèle lui-même | agents longue durée |
| **Cognee** | graphe + vecteurs, pipelines ECL | quand la mémoire doit être requêtable |
| **icm** (rtk-ai) | binaire unique, MCP natif, zéro dépendance | le plus proche de la philosophie de ce template |

**Avertissement.** N'ajoute pas de mémoire tant que `PROGRESS.md` n'a pas échoué sur un
cas réel. Une mémoire externe est un état de plus à synchroniser, à auditer et à purger
— et en banque, une mémoire d'agent contenant des données clients est un traitement à
déclarer. Le fichier versionné a l'énorme avantage d'être lisible par un humain et
supprimable par un `git rm`.

## 8. Orchestration d'agents parallèles

> Ce template gère le parallélisme avec `git worktree` à la main. Il existe une
> catégorie d'outils qui automatisent ce geste, la plupart adossés aux worktrees.

| Outil | Licence | Approche |
|---|---|---|
| **Vibe Kanban** | Apache-2.0 | tableau kanban d'agents, worktrees |
| **Claude Squad** | AGPL-3.0 | TUI adossé à tmux, worktrees |
| **Conductor** | propriétaire | GUI macOS, worktrees |
| **Sculptor** | propriétaire | **conteneurs plutôt que branches** — la seule approche différente du lot |
| **Mux** | AGPL-3.0 | exécution locale et distante (SSH) |
| **Paneflow**, **Opcode**, **Superset**, **Nimbalyst** | variées | variantes GUI/TUI |
| **grit** (voir section 3.5) | Apache-2.0 | verrouillage AST **au niveau fonction** — résout le vrai problème, pas l'ergonomie |

**En pratique** : l'ergonomie de ces outils est agréable et ne résout rien de
fondamental — le worktree est déjà la primitive. Le problème réel du parallélisme est la
**collision sémantique** entre deux agents qui modifient la même fonction dans deux
branches, et c'est ce que `grit` attaque. Commencer par `git worktree` et un tableau tenu
à la main ; passer à un outil au-delà de trois agents simultanés, ce qui est rare.

## 9. Bacs à sable gérés

> La section « isolation » couvre les primitives (gVisor, Firecracker, bubblewrap).
> Voici les produits, pour qui ne veut pas les exploiter soi-même.

| Plateforme | Note |
|---|---|
| **E2B** | le plus établi pour l'exécution de code d'agent, SDK correct |
| **Modal** | excellent si vous avez déjà du calcul chez eux |
| **Daytona** | démarrage très rapide, positionné agents |
| **Vercel Sandbox**, **Cloudflare** | intégrés à leur plateforme respective |

**Critère bancaire** : tous sont des SaaS. En environnement bancaire, la question n'est
pas « lequel est le meilleur » mais « peut-on faire sortir du code du SI », et la
réponse par défaut est non. Reste alors le conteneur local durci et la VM — ce que couvre la
section isolation.

## 10. Développement piloté par la spécification

> Ce que fait le sous-agent `planner` de ce template porte un nom et a un outillage.

| Outil | Note |
|---|---|
| **GitHub Spec Kit** | le plus proche de ce template : spécification puis plan puis tâches, en fichiers versionnés |
| **Kiro** (AWS) | IDE dédié, spécifications et hooks, adopte le standard Agent Skills |
| **BMAD** | méthode à rôles multiples, très structurée, lourde |
| **Tessl**, **openspec** | approches où la spécification devient l'artefact primaire et le code un dérivé |

Le fond est plus important que l'outil : écrire la spécification et les critères
**avant** le code, en fichiers versionnés dans le dépôt, et les faire relire. Le
`planner`, les contrats de `tasks/` et le juge de ce template implémentent exactement cela
sans dépendance supplémentaire. Regardez Spec Kit pour une structure plus riche.

## 11. Documentation à jour pour l'agent

> Les API hallucinées sont un mode d'échec majeur et silencieux : l'agent écrit du code
> qui appelle une fonction qui n'existe plus depuis deux versions.

| Outil | Note |
|---|---|
| **Context7** (Upstash) | serveur MCP qui injecte la documentation à jour de la bibliothèque utilisée, version par version. Le remède le plus direct. Attention : dépendance à un registre tiers — vérifiez la provenance avant usage en interne. |
| **llms.txt** | convention de fichier à la racine d'un site, pour exposer une documentation lisible par un modèle. À adopter pour les bibliothèques **internes** : c'est le geste qui rend le SI lisible par les agents. |
| **DeepWiki** | documentation d'architecture générée pour les dépôts publics. Utile en découverte, pas en référence. |

Dans ce dépôt, en attendant : `make fetch-docs URL=https://... [NAME=nom]`. La commande
est **réservée à l'opérateur** (les agents n'ont ni `WebFetch`, ni `curl`, ni `wget`). Elle
télécharge une page, jette les menus et les scripts, la convertit en texte et la range dans
`docs/external/`, sous un bandeau qui dit ce qu'elle est : une donnée venue du web, jamais
une consigne. Le fichier est commité, donc le diff montre ce qui entre dans le contexte des
agents, et on ne le cite à un agent qu'au moment où il en a besoin — une page de doc, c'est
facilement 3 000 tokens. Règles : `.ephor/docs/06-exploitation.md`, « Pages du web ».

Le geste à retenir en entreprise : publier un `llms.txt` et une documentation versionnée
pour les bibliothèques internes coûte peu et rend chaque agent de l'entreprise plus
juste.

## 12. Revue de code en produit

> Le sous-agent `reviewer` de ce template couvre le cas nominal. Voici la catégorie
> produit, pour un déploiement à l'échelle d'une organisation.

| Produit | Note |
|---|---|
| **CodeRabbit** | le plus déployé, très bavard par défaut — à calibrer ou il sera ignoré |
| **Greptile** | indexation complète du dépôt, revue avec contexte inter-fichiers |
| **Graphite Diamond** | intégré au flux de PR empilées |
| **Copilot code review** | natif GitHub ; à utiliser comme **prérequis** avant la revue humaine, jamais comme remplacement |

Règle commune à tous : un relecteur automatique se place **avant** l'humain, pas à sa
place. Les données de 2026 sont claires — le code d'origine agentique introduit plus de
redondance et plus de dette par changement, et pourtant les relecteurs se sentent mieux
en l'approuvant. Un outil de plus ne corrige pas ce biais ; seul un protocole le corrige.

## 13. Optimisation de prompts

| Outil | Note |
|---|---|
| **DSPy** | programmation déclarative de pipelines LLM, avec optimiseurs (BootstrapFewShot, MIPROv2, COPRO, GEPA). L'intérêt réel : les prompts deviennent des artefacts **optimisés contre un jeu d'évaluation**, pas des chaînes de caractères bricolées. |
| **GEPA** | optimiseur par réflexion et évolution. Se combine avec une approche pilotée par les tests. |

Prérequis absolu, et c'est toujours le même : **rien de tout cela ne fonctionne sans
jeu d'évaluation.** DSPy optimise contre une métrique ; sans métrique fiable, il
optimise du bruit. Construisez le vérificateur d'abord.


## 14. Mesurer les tokens et le coût

| Outil | Installation | Agent |
|---|---|---|
| **`/usage`, `/context`, `/insights`** | intégrés | La source de vérité. `/context` est celui qu'on n'utilise jamais assez : il montre ce qui occupe le contexte **maintenant**. |
| **Status line avec contexte** | `/statusline` | Voir l'occupation en permanence change les habitudes plus qu'aucun document. |
| **ccusage** | `npx ccusage@latest` | Analyse les journaux de session locaux : coût par jour, par session, par modèle. Utile pour objectiver une dépense devant un comité. |
| **Export OpenTelemetry** | variable d'env. dédiée | `CLAUDE_CODE_ENABLE_TELEMETRY=1`. La seule option qui donne des métriques par utilisateur en quasi-temps réel dans la stack d'observabilité **de l'entreprise**. C'est la réponse à « combien nous coûte l'IA » posée par un DAF. |
| **LiteLLM** (passerelle) | `pip install litellm` | Suivi de dépense par clé virtuelle, quotas, routage multi-fournisseurs. Déjà en place dans l'environnement cible : c'est le point de contrôle naturel pour la traçabilité. |
| **`--max-budget-usd`** | drapeau CLI | Plafond dur sur une session non interactive. À utiliser dans toute exécution automatisée. |

## 15. Isolation et bac à sable

| Outil | Installation | Agent |
|---|---|---|
| **git worktree** | intégré | Un agent, une branche, un répertoire, un environnement applicatif éphémère. Le préalable à tout parallélisme. |
| **devcontainer** | `npm i -g @devcontainers/cli` | Environnement reproductible, et frontière de système de fichiers par construction. |
| **Docker / Colima** | `brew install colima docker` | Bac à sable de base. Conteneur frais par tâche, à la façon du compilateur C. |
| **bubblewrap** (Linux) / **Seatbelt** (macOS) | système | Durcissement au niveau OS, utilisé par Claude Code lui-même : 84 % de demandes de permission en moins dans l'usage interne d'Anthropic. |
| **gVisor** | `brew install gvisor` (Linux) | Noyau en espace utilisateur : isolation plus forte qu'un conteneur, moins lourde qu'une VM. |
| **Firecracker / microVM** | selon plateforme | Quand le conteneur ne suffit pas — exécution de code non fiable. |
| **Proxy d'egress** (`mitmproxy`, Squid) | `brew install mitmproxy` | **Le contrôle qui compte réellement.** Une exfiltration d'identifiants AWS par hameçonnage a montré en février 2026 que le contrôle de sortie réseau tient là où les défenses au niveau du modèle cèdent. Et une liste blanche accorde des **capacités**, pas seulement des destinations : un domaine autorisé peut servir de canal. |

## 16. Vérification par le comportement réel

| Outil | Installation | Agent |
|---|---|---|
| **Playwright MCP** | `claude mcp add playwright npx @playwright/mcp@latest` | L'évaluateur pilote l'application **comme un utilisateur** au lieu de lire le code. C'est ce qui sépare un jugement d'une opinion. |
| **Chrome DevTools Protocol** | via Playwright | Instantané du DOM avant/après, événements d'exécution. OpenAI l'a intégré précisément pour que l'agent valide l'UI sans intermédiaire humain. |
| **Stack d'observabilité locale** (Loki + Prometheus + Grafana, ou `docker compose`) | — | Exposer logs, métriques et traces à l'agent en local, interrogeables (LogQL, PromQL), lui permet de valider « le démarrage tient sous 800 ms » par une requête et non par une affirmation. C'est le meilleur investissement de vérification après les tests. |
| **hyperfine** | `brew install hyperfine` | Benchmark reproductible avec intervalles de confiance. Empêche l'agent de conclure sur une mesure unique. |

## 17. Passage à l'échelle : build et file de merge

| Outil | Quand | Agent |
|---|---|---|
| **Bazel** ou **Buck2** | monorepo, > 500 k lignes, plusieurs langages | Build hermétique et incrémental. Sans ça, la validation devient le goulot. |
| **Exécution et cache distants** (BuildBuddy, EngFlow) | dès que plusieurs agents bâtissent en parallèle | Résout les conflits de verrou de workspace, le thrash de cache d'analyse et le test multi-architecture. Les snapshots de VM conservent un cache d'analyse chaud entre deux builds. |
| **Nx / Turborepo** | monorepo JS/TS | Graphe de tâches + cache. |
| **sccache**, **ccache**, **mold** | C/C++/Rust | Le temps de compilation est du temps d'agent. |
| **PR empilées** (`gh stack`, Graphite, git-spice) | dès que les diffs dépassent 400 lignes | Au-delà de 400 lignes, une revue trouve nettement moins de défauts par ligne relue (étude SmartBear sur 2 500 revues) ; les éditeurs d'outils de revue annoncent environ 40 % de défauts en moins pour les PR de 200 à 400 lignes, et des PR de moins de 200 lignes approuvées trois fois plus vite. GitHub a ouvert les PR empilées (`gh-stack`) en préversion privée en avril 2026, puis publique le 30 juillet 2026 : rebase en cascade, force-push atomique sur la chaîne, protections de branche évaluées contre la cible finale, CI exécutée comme si chaque PR visait `main`. C'est le mécanisme qui rend praticable la consigne « petites PR » face au volume agentique — apprenez à l'agent à découper un gros diff en couches. |
| **File de merge GitHub**, **Graphite**, **Aviator** | > 20 PR/jour | Une CI de groupe à 30 minutes plafonne à ≈ 48 PR/jour en file stricte. Une équipe de 50 avec agents dépasse ce seuil, et l'attente n'augmente pas linéairement : elle explose. |
| **Quarantaine de tests instables** | dès le premier test intermittent | Les agents relancent en boucle, ce que les humains ne font pas. Un instable non mis en quarantaine devient un déni de service sur la file. |
| **Budget de relance par agent** | dès le premier agent autonome | Même raison. À imposer côté CI, pas côté prompt. |

## 18. Spécifique LLM — le périmètre RAG

| Outil | Installation | Agent |
|---|---|---|
| **promptfoo** | `npx promptfoo@latest` | Évaluation de prompts et de chaînes RAG en YAML, exécutable en CI. Le plus simple pour transformer « le RAG marche mieux » en une commande. |
| **ragas** | `uv add ragas` | Métriques RAG : fidélité, pertinence du contexte, rappel. Donne des nombres là où les comités demandent des nombres. |
| **deepeval** | `uv add deepeval` | Assertions d'évaluation exposées comme des tests pytest. S'intègre directement dans `make verify` — c'est ce qui fait entrer l'évaluation dans Ephor au lieu d'être un notebook à côté. |
| **Langfuse** ou **Phoenix (Arize)** | `docker` / `uv add arize-phoenix` | Traces, coût et latence par requête LLM. Auto-hébergeables : critère décisif en environnement bancaire. |
| **Giskard** | `uv add giskard` | Tests de robustesse et de biais. Argument de conformité. |
| **LangGraph checkpointer** | `uv add langgraph-checkpoint-postgres` | Reprise après interruption d'un graphe long. L'équivalent de `PROGRESS.md` pour un agent de production. |
| **Vertex AI Evaluation Service** | SDK GCP | Aligné sur GCP, plateforme cible ; à comparer à promptfoo sur le critère de la portabilité. |

**Le geste qui compte ici** : brancher l'évaluation RAG dans `make verify`, pas à côté.
Une évaluation qu'on lance à la main n'est pas un vérificateur ; c'est un rapport.

## 19. Harnais à connaître

| Harnais | Intérêt |
|---|---|
| **Claude Code** | Référence sur modèles Anthropic ; skills, hooks, sous-agents, worktrees, routines. |
| **Codex CLI / app** | Référence OpenAI ; revue agent-à-agent très aboutie. |
| **Antigravity CLI / 2.0** | Google, écrit en Go, asynchrone, même harnais que l'application de bureau. Gemini CLI est arrêté côté grand public depuis le 18 juin 2026 ; les licences entreprise le gardent. |
| **OpenCode** | 75+ fournisseurs, dont des poids ouverts auto-hébergés. L'option souveraine. |
| **Goose** | Gouvernance Linux Foundation, agnostique. Argument de neutralité en comité. |
| **OpenHands** | Bac à sable Docker auto-hébergé, coordination d'équipe. |
| **mini-swe-agent** | Une centaine de lignes. À lire pour comprendre ce qu'est réellement une boucle d'agent. |

Le fond compte plus que l'anecdote. On cite souvent un harnais tiers qui dépasserait de
dix-sept points le harnais du fournisseur sur Terminal-Bench 2.0 à modèle constant —
chiffre sans source primaire vérifiable. L'argument solide est Harness-Bench (arXiv 2605.27922, mai 2026) : 106
tâches, 5 194 trajectoires, plusieurs harnais et plusieurs modèles, et une conclusion
sans ambiguïté — la capacité se rapporte au **couple modèle-harnais**, jamais au modèle
seul. Le choix du harnais pèse autant que celui du modèle.

## 20. Provenance, données personnelles, piste d'audit — le triptyque bancaire

> Trois questions qu'un auditeur posera et qu'aucune section précédente ne couvre.
> Elles n'ont rien à voir avec la qualité du code : elles portent sur ce que vous pouvez
> **prouver** après coup. En banque, c'est ce qui décide si un dispositif passe.

### 20.1 « Ce code vient d'où, et sous quelle licence ? »

Un modèle entraîné sur des décennies de code public peut reproduire des structures
familières. Chris Lattner posait la question en relisant le compilateur C d'Anthropic (blog de Modular, février 2026) :
où passe exactement la frontière entre apprendre et copier ? Juridiquement, la question
n'est pas tranchée — opérationnellement, elle l'est : vous devez pouvoir montrer que le
code entrant dans votre branche principale a été passé au crible.

| Outil | Note |
|---|---|
| **ScanCode Toolkit** (Apache-2.0, AboutCode) | Détecte licences et copyrights par **comparaison de texte intégral**, pas par expressions régulières. Sort en JSON, SPDX ou CycloneDX. S'embarque en CI. C'est la brique libre de référence. |
| **FOSSA**, **Black Duck**, **FossID** | Commerciaux, avec correspondance par **fragments** (snippet matching) — la capacité qui manque à ScanCode et qui est précisément celle qu'on veut face à du code généré. |
| **Filtre de duplication** des assistants | Première barrière, à activer, jamais suffisante seule. |

**Le placement compte plus que l'outil.** Le moment décisif n'est pas la suggestion,
c'est la fusion dans la branche principale — c'est là que le code devient votre produit.
Un contrôle de licence à l'étage post-merge, qui écrit un enregistrement par fusion,
produit ce que l'auditeur demande : la preuve documentée que **chaque** merge est passé
par un écran de licence. Un contrôle au poste de travail ne prouve rien.

### 20.2 « Est-ce que des données clients sont sorties dans un prompt ? »

Le risque n'est pas théorique : un agent qui déboguait une anomalie de production a
toutes les raisons de coller un extrait de log ou une ligne de base dans son contexte.

| Outil | Note |
|---|---|
| **Presidio** (MIT, ≈ 11 k étoiles ; né chez Microsoft, désormais projet communautaire `data-privacy-stack/presidio`) | Détection et anonymisation de données personnelles — texte, images, données structurées. Reconnaissances par NER, expressions régulières, règles et sommes de contrôle, avec recognizers personnalisables (utile pour les formats internes : IBAN, identifiants clients). S'utilise en bibliothèque Python, CLI, Docker, Spark. |
| **Passerelle LLM avec caviardage** (LiteLLM + Presidio en amont) | Le seul point où le contrôle est **systématique** plutôt que déclaratif. La passerelle LiteLLM existe déjà dans l'environnement cible : c'est l'endroit naturel. |

Dans ce dépôt, le premier filet est en place et il ne coûte rien : `make verify` refuse
désormais tout code qui envoie une donnée personnelle dans un journal ou dans un message
d'erreur (`client.iban`, `numeroCarte`, `client["email"]`...). L'analyse porte sur l'arbre
syntaxique, pas sur du texte : un f-string, une variable intermédiaire (`msg = f"{c.iban}"`
puis `log.info(msg)`), un paramètre de `format()` ou une clé de dictionnaire ne la
contournent pas. À l'inverse, un nom de classe (`raise EmailInvalide(...)`), une constante
(`IBAN_MOTIF`) ou un compteur (`stats.email_count`) ne déclenchent rien : un contrôle qui
sonne pour rien finit désactivé. La liste des champs et celle des exceptions vivent sous
`.ephor/`, que l'agent ne peut pas modifier : il ne peut pas s'autoriser lui-même.

Ses limites, à connaître : pas de suivi de flux entre fonctions ou entre fichiers, et
l'écriture directe dans un fichier (`f.write(...)`) n'est pas traitée comme un journal.
Ce filet attrape la fuite dans **votre** code ; il ne remplace ni Presidio en amont de la
passerelle, ni l'absence de données réelles en développement.

**Avertissement à reprendre tel quel**, il est dans la documentation de Presidio :
comme la détection est automatique, **rien ne garantit qu'il trouve tout**. Un
caviardage est une réduction de risque, jamais une autorisation. La vraie parade reste
de ne pas mettre de données de production à portée de l'agent : jeux de données
synthétiques, environnements de développement sans données réelles, et un contrôle de
sortie réseau qui limite où le contexte peut partir.

### 20.3 « Qui a écrit cette ligne ? »

C'est la question la plus simple et celle à laquelle presque personne ne sait répondre.
Elle arrivera : un incident, une revue de conformité, un litige de propriété
intellectuelle.

La réponse la moins chère qui existe est déjà dans git : **les trailers de commit**.

```
git commit -m "feat: valide le format IBAN en entrée

Co-Authored-By: Claude <noreply@anthropic.com>
Agent-Session: <identifiant de session>"
```

C'est permanent, versionné, requêtable (`git log --grep='Co-Authored-By: Claude'`),
et cela ne coûte rien. Les points de reprise d'Ephor portent déjà `Agent-Session`, et les
commits du dépôt d'Ephor portent `Co-Authored-By`. Adoptez la même convention dans vos
projets, et vous disposez d'une piste d'audit du code d'origine agentique sans aucun outil
supplémentaire.

Les compléments, dans l'ordre de coût croissant : l'**export OpenTelemetry** de l'outil
agentique, qui donne l'activité par utilisateur en quasi-temps réel dans votre propre stack ;
et le **journal d'événements durable par session** décrit dans la section sur le
découplage cerveau/mains — hors fenêtre de contexte, interrogeable, c'est-à-dire
exactement ce qu'un auditeur veut lire.

**Le conseil de fond.** Construisez la piste d'audit **avant** d'en avoir besoin. Après
l'incident, l'historique manquant ne se reconstitue pas — et l'absence de trace se lit
comme une faute, pas comme un oubli.


## 21. À éviter

| Outil / pratique | Pourquoi |
|---|---|
| Serveur MCP « filesystem » | Duplique des capacités natives en coûtant du contexte. |
| Empiler les serveurs MCP « au cas où » | Coût permanent, visible dans `/context`. Un CLI fait le même travail à zéro. |
| Générer `AGENTS.md` par un agent | La documentation curatée par un humain surpasse la documentation générée : elle dit ce qui **compte**, pas ce qui **existe**. |
| Frameworks d'orchestration élégants et confidentiels | L'agent ne les connaît pas. Préférez l'ennuyeux : API stable, forte présence dans les données d'entraînement. |
| Seuil de couverture comme porte bloquante | Optimisé mécaniquement par un agent, sans gain de qualité. Préférez « un test qui échoue sans le changement ». |
| Un linter avec toutes les règles activées d'un coup sur un dépôt existant | Bruit total dès le premier jour, donc contrat ignoré par tout le monde. Resserrez d'un cran par semaine. |
