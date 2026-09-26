# Discipline de tokens

> Économiser les tokens n'est pas qu'une affaire de coût. Le contexte est la ressource
> qui détermine la qualité du raisonnement : un contexte saturé de logs produit du
> code médiocre bien avant de produire une facture. Optimiser les tokens, c'est
> d'abord optimiser la qualité.

## 1. Mesurer avant d'optimiser

| Quoi | Comment |
|---|---|
| Coût et tokens de la session | `/usage` — inclut une ligne `Prompt cache` : part des tokens servis par le cache, nombre de ratés, cache chaud ou froid |
| Ce qui occupe le contexte **maintenant** | `/context` — c'est là qu'on découvre qu'un serveur MCP mange 15 % du budget |
| Occupation en continu | afficher le contexte dans la status line |
| Ses propres habitudes | `/insights` — analyse jusqu'à 200 sessions locales et sort un rapport HTML |
| Attribution par skill / sous-agent / MCP | bloc d'attribution de `/usage` (plans Pro, Max, Team, Enterprise) |
| Suivi d'équipe | export OpenTelemetry, ou passerelle LLM qui compte la dépense par clé |
| **Ce qu'a coûté une tâche d'Ephor** | `make usage` — mesuré sur le journal de session de Claude Code ou de Copilot, et sur la base d'opencode, jamais estimé |
| **Ce que ça a produit pour ce prix** | `make score` — coût par critère satisfait, sur le journal des jugements (`.ephor/docs/09`) |

**Ordres de grandeur publiés par Anthropic** pour calibrer : environ 13 $ par
développeur et par jour actif, 150 à 250 $ par développeur et par mois, et moins de
30 $ par jour actif pour 90 % des utilisateurs. Si vous êtes loin au-dessus, la cause est
presque toujours l'une des deux suivantes : des sessions longues jamais vidées, ou
le modèle le plus cher laissé par défaut.

### 1 bis. Le plafond par tâche

Un contrat peut fixer `budget_tokens`. À la fin de chaque tour, Ephor mesure la
session, écrit le résultat dans `.verify/usage/<ID>.json` — que le juge reprend dans son
rapport — et, au-delà du plafond, pose `AGENT_STOP` avec la raison dedans. L'agent est
refusé au premier appel d'outil suivant et vous rend la main.

```json
{ "id": "FEAT-002", "budget_tokens": 400000, "...": "..." }
```

Ce qui est compté : entrée + écriture de cache + sortie. Les **lectures** de cache sont
mises à part, parce qu'elles coûtent environ dix fois moins et qu'elles écraseraient le
total (une session ordinaire lit des millions de tokens en cache pour quelques centaines
de milliers facturables).

Vérifié le 22 septembre 2026 : le compteur sur un vrai journal de session Claude Code
(746 000 tokens facturables pour 34 millions lus en cache), et les colonnes d'opencode sur
une session réelle qui déclare son usage. Le tout tourne aussi sur le Python 3.9 fourni
par Apple, celui d'un Mac sans droits d'installation.

Sous Copilot, le journal de la session (`~/.copilot/session-state/<id>/events.jsonl`)
ne donne les tokens qu'à la **fermeture** de la session. Or VS Code ne ferme une session
que lorsqu'il en ouvre d'autres : parfois le lendemain, parfois jamais. En attendant,
Ephor rapporte ce que Copilot facture dans ses propres unités, écrites après chaque
demande : les AIU et les requêtes premium. Les tokens connus sont alors un **minimum** :
« ≥ » dans `make usage`, « au moins » dans le résumé du juge, et le tableau de bord ne
les compte pas comme un coût. Le plafond s'applique à ce minimum : dépassé, il arrête
l'agent ; tant que les tokens manquent, il ne peut rien prouver et n'arrête pas.
`make usage` et `make judge` relisent les journaux de la tâche, pour rattraper une
fermeture survenue après le dernier tour.

Vérifié le 24 septembre 2026 sur quinze sessions réelles de la vue « agents » : cinq
fermées avec leur détail, neuf encore ouvertes, une dont une portion était perdue (une
reprise après un arrêt brutal écrit une fermeture sans tokens).

Limites : sous opencode, pas de plafond par tâche ; `make usage` donne le total du dépôt,
toutes sessions confondues. Sous Copilot, le plafond ne mord que sur ce qui est déjà
connu, voir ci-dessus.

## 2. Les leviers, par ordre de rendement décroissant

### 2.1 Déléguer les opérations verbeuses à un sous-agent

Le levier le plus rentable, et de loin. Un sous-agent démarre avec un contexte isolé :
ce qu'il lit reste chez lui, seule sa synthèse remonte. L'illustration officielle est
sans ambiguïté : une tâche qui chargerait 800 Ko dans la conversation principale
(recherche + logs) n'en consomme que 5 Ko quand elle est déléguée.

Dans ce dépôt, c'est le rôle de `scout` (recherche large), `evaluator` (relecture) et
`debt-collector` (inventaire). **Règle : toute opération dont vous ne pouvez pas prédire
la taille de sortie passe par un sous-agent.**

Déléguer allège le contexte, pas la facture : un sous-agent paie ses propres tokens. Sous
Claude Code, chacun écrit son journal à part (`<session>/subagents/`), et Ephor l'ajoute
à la tâche — plafond compris. Avant le 25/09/2026, il ne lisait que le journal principal :
sur une vraie session, un tiers de la dépense lui échappait.

### 2.2 Faire écrire les sorties dans des fichiers

`make verify` n'imprime qu'une ligne par étape et un bloc de statistiques
pré-calculées ; tout le reste va dans `.verify/*.txt`. Un log de tests complet dans
la conversation coûte des dizaines de milliers de tokens, ne sert qu'une fois, et
reste ensuite dans le contexte à chaque requête suivante.

Le motif vient du compilateur C d'Anthropic : sortie console minimale, résultats en
fichiers, **statistiques pré-calculées pour que le modèle n'ait pas à les recalculer**.

### 2.3 Filtrer en amont avec un hook

Un hook `PreToolUse` peut réécrire une commande avant que le modèle n'en voie la
sortie. `.ephor/hooks/filter-output.sh` fait cela pour les commandes de test :
au lieu de 10 000 lignes, le modèle reçoit les blocs d'échec. Le gain est de l'ordre
de « dizaines de milliers de tokens » ramenés à « quelques centaines ».

C'est un déplacement de responsabilité : le prétraitement n'est plus payé en tokens,
il est payé en CPU local, qui est gratuit.

### 2.3 ter La carte du code, et les fiches de composant

`make map` lit la **syntaxe** des fichiers Python : classes, méthodes, fonctions de module,
`async def` compris, avec numéros de ligne. La carte est mise en cache tant que rien ne
bouge. C'est quelques centaines de tokens contre des dizaines de milliers de lecture.

Pour ce qui ne se lit pas dans le code — le rôle d'un composant, ses pièges, ses invariants —
écrivez une fiche à partir de `docs/composants/TEMPLATE.md`, et chargez-la à la demande.

### 2.3 bis Compresser la sortie d'outils à la source, et indexer plutôt que lire

Deux outils font, en produit, ce que ce template fait à la main :

**RTK** (`brew install rtk && rtk init -g`) est un proxy CLI qui compresse la sortie
de plus de cent commandes avant qu'elle n'atteigne le modèle — filtrage du bruit,
regroupement, troncature conservant le signal, déduplication. Réduction annoncée de
60 à 90 % ; sur une session de trente minutes, environ 118 k tokens ramenés à 24 k.
Il s'installe comme un hook `PreToolUse`, exactement comme `filter-output.sh` ici —
qui se désactive automatiquement si `rtk` est détecté sur le PATH. **Limite : les
outils natifs `Read`, `Grep` et `Glob` contournent RTK**, qui n'agit que sur Bash.

**Un index de code** (Serena en navigation symbolique par LSP, CodeGraph en graphe
d'appels) remplace « grep puis lire trois fichiers candidats » par une requête. Les
gains annoncés sont importants — jusqu'à 88 % d'appels d'outils en moins — mais le
revers est documenté : un contexte de récupération plus lourd et résident sur les
sessions multi-tours, plus un index à garder synchronisé.

Ordre d'adoption recommandé, du meilleur rapport bénéfice/complexité au moins bon :
RTK, puis Serena si le dépôt est typé et gros, puis un graphe de code seulement si vous
savez mesurer le gain sur votre propre dépôt. Détail et critères de licence et de flux de
données dans `.ephor/docs/08-tooling.md`, section 3.

### 2.4 Garder AGENTS.md / CLAUDE.md court

Ce fichier est chargé **à chaque démarrage de session**, y compris pour une tâche
sans rapport. La cible officielle est de rester sous 200 lignes ; la nôtre est 150.
Tout ce qui est spécialisé (procédure de revue, migration, déploiement) part dans un
skill, chargé à la demande.

### 2.5 Préférer un CLI à un serveur MCP

Les définitions d'outils MCP sont différées par défaut — seuls les noms et les
instructions du serveur entrent en contexte jusqu'au premier usage. Mais un CLI
(`gh`, `gcloud`, `bq`, `aws`, `kubectl`) reste plus économe : **zéro coût de
listage**. Lancez `/mcp` et désactivez ce que vous n'utilisez pas ; `/context` vous dira
ce que chaque serveur coûte réellement.

### 2.6 Router les modèles par tâche

| Tâche | Modèle |
|---|---|
| Recherche, inventaire, résumé de logs | `haiku` (voir `.claude/agents/scout.md`) |
| Implémentation courante, revue | `sonnet` |
| Architecture, décision structurante, jugement subjectif | `opus` |

Le champ `model:` dans le frontmatter d'un sous-agent suffit. Attention : **chaque
modèle a son propre cache de prompt**, donc un changement de modèle en cours de
session relit toute la conversation sans cache. Router par sous-agent plutôt que
par `/model` en cours de route.

Derrière une passerelle, ces trois noms ne veulent plus rien dire : parlez en **rapide,
standard, fort**, et branchez-les une fois, sur votre poste, jamais dans le dépôt — les noms
de modèles de l'entreprise n'ont rien à faire dans un dépôt partagé.

| Alias | Claude Code | opencode |
|---|---|---|
| rapide | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | `small_model` dans `~/.config/opencode/opencode.json` |
| standard | `ANTHROPIC_DEFAULT_SONNET_MODEL` | `model` |
| fort | `ANTHROPIC_DEFAULT_OPUS_MODEL` | `agent.<nom>.model` pour les agents qui le méritent |

Les sous-agents d'Ephor nomment déjà leur niveau (`haiku`, `sonnet`, `opus`) ; les
adaptateurs ne recopient aucun nom de modèle chez opencode, exprès : c'est votre configuration
personnelle qui décide.

Chez opencode, une sortie d'outil est plafonnée à 600 lignes ou 50 ko (`tool_output` dans
`.opencode/opencode.json`, généré) ; le reste part sur disque. C'est un plafond, pas un
résumé : la discipline du `.verify/` reste la vraie économie.

### 2.7 Ajuster l'effort de réflexion

La réflexion étendue est facturée en tokens de sortie et son budget par défaut peut
atteindre des dizaines de milliers de tokens par requête. Pour les tâches simples,
baisser avec `/effort`, ou plafonner via `MAX_THINKING_TOKENS` (positionné à 16000
dans `.claude/settings.json` de ce template — remontez-le pour les phases d'architecture).
Les modèles à raisonnement adaptatif ignorent un budget non nul : chez eux, seul le
niveau d'effort compte.

### 2.8 Hygiène de session

- `/clear` entre deux tâches sans rapport. Un contexte périmé est repayé à **chaque**
  message suivant. `/rename` avant, `/resume` après, si vous comptez y revenir.
- `/compact <instruction>` plutôt que `/compact` nu : « concentre-toi sur la sortie
  de test et les changements de code ». Attention, compacter est soi-même une grosse
  requête — quand vous ne voulez pas la continuité, `/clear` coûte zéro.
- Mode plan avant une tâche complexe : une mauvaise direction découverte après
  quarante minutes coûte plus que la planification.
- Échap pour arrêter dès que ça part de travers ; `/rewind` pour revenir à un point.

## 3. Le cache de prompt, le poste le plus mal compris

La conversation entière est renvoyée à chaque requête ; le cache fait qu'elle est
relue au tarif réduit. Ce qui fait **rater** le cache :

- une pause plus longue que la durée de vie du cache (1 h sur abonnement, 5 min sur
  crédits d'usage, 5 min par défaut sur clé API ou fournisseur cloud) ;
- un changement de modèle en cours de session ;
- une modification des définitions d'outils (serveur MCP activé/désactivé en cours) ;
- une compaction ou un vidage de résultats d'outils — coût attendu, pas une erreur.

Conséquence pratique : **ne touchez pas à `AGENTS.md`, aux serveurs MCP ni au modèle
au milieu d'une session longue.** Le préfixe stable est ce qui vous fait payer 10 % au
lieu de 100 %.

## 4. Le cas des équipes d'agents

Les équipes d'agents consomment environ **7 fois** plus de tokens qu'une session
standard quand les coéquipiers tournent en mode plan : chaque coéquipier a sa propre
fenêtre de contexte et charge `CLAUDE.md`, les serveurs MCP et les skills. Elles sont
désactivées par défaut (`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`).

Si vous en lancez une : équipe de 3 à 5 au maximum, modèle Sonnet pour les coéquipiers,
prompts d'amorçage courts, et **arrêtez chaque coéquipier dès que sa tâche est finie** —
un coéquipier actif consomme tant qu'il n'a pas quitté.

## 5. Échantillonner les tests plutôt que tout relancer

`make test-fast` exécute un sous-ensemble déterministe mais pseudo-aléatoire de la
suite, semé par le hash du commit (`.ephor/scripts/test_sample.sh`). Chaque agent parallèle
teste donc une portion différente et reproductible : les régressions remontent vite,
pour une fraction du temps et des tokens. La suite complète reste obligatoire avant
la fusion, pas à chaque itération.

## 6. Anti-patterns, avec leur coût

| Anti-pattern | Ce que ça coûte |
|---|---|
| `cat` sur un gros fichier « pour voir » | des milliers de tokens, payés à chaque requête suivante de la session |
| Relancer les tests en affichant tout le log | idem, multiplié par le nombre d'itérations |
| Une session ouverte toute la journée sur cinq sujets | tout le contexte des cinq sujets renvoyé à chaque message |
| Demander « améliore ce code » | déclenche un balayage large ; « ajoute la validation d'entrée à `login()` dans `auth.py` » ne lit que ce qu'il faut |
| Laisser Opus par défaut sur des tâches de routine | le poste de dépense n°1 en pratique |
| Empiler les serveurs MCP « au cas où » | coût permanent en contexte, visible dans `/context` |
| Réécrire `AGENTS.md` au milieu d'une session | invalide le cache, toute la conversation est reprocessée |

## 7. Le seul chiffre à surveiller

Pas le coût par session : le **coût par critère satisfait**. Un harnais qui double
la dépense et triple le nombre de critères réellement validés est un bon harnais.
`.verify/stats.txt` vous donne le dénominateur ; `/usage` vous donne le numérateur.
