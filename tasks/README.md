# Contrats de tâche

Une fonctionnalité confiée à un agent commence ici, **avant** la première ligne de code.
Une petite correction sans enjeu n'a pas besoin de contrat : `make verify` et la CI suffisent.

## Le circuit

1. **Rédiger.** Le sous-agent `planner` propose le contrat et les tests d'acceptation. Il
   n'écrit aucun fichier : l'agent enregistre sa réponse entière dans
   `.verify/plans/FEAT-001.md`, et vous la passez telle quelle :
   `make task-new ID=FEAT-001 FROM=.verify/plans/FEAT-001.md`. Le premier bloc JSON devient
   le contrat ; chaque bloc de code annoncé par son chemin sous `tasks/FEAT-001/` devient un
   fichier de la tâche. Tout est vérifié avant d'être écrit — contrat mauvais, chemin hors de
   la tâche, Python illisible : rien n'est créé. Un fichier de test cité par les critères mais
   absent du plan est créé avec un test vide par critère, à remplir. Un contrat JSON seul
   marche aussi (`FROM=plan.json`). Sans plan, `make task-new ID=FEAT-001` donne un squelette.
2. **Voir échouer.** Vous relisez, puis vous lancez `make task-check ID=FEAT-001`. Il vérifie
   la forme du contrat, puis lance chaque critère une fois sur le code **commité**, plus les
   preuves de la tâche (ce dossier, les fichiers que les critères citent), comme le juge le
   fera. Un travail en cours pas commité ailleurs n'y entre pas : ce n'est pas ce que l'agent
   recevra, et il le dit. Lancez-le sur la branche principale. **Tous doivent échouer** : un critère déjà vert ferait déclarer l'agent VÉRIFIÉ
   sans qu'il ait rien fait, et il est refusé. Un rouge pour une mauvaise raison l'est
   aussi, quand il se reconnaît : preuve introuvable, commande introuvable, test pytest
   désigné qui n'a jamais tourné (nom mal écrit) ou qui passe déjà, squelette de
   `make task-new` pas encore rempli. Pour le reste, lisez la raison affichée pour
   chacun : elle doit dire que le comportement manque. Alors seulement il écrit
   `tasks/FEAT-001/rouge.json`. Relancé sans que rien ait changé, il n'y touche plus.
   Si le dépôt a ses hooks de pre-commit, il les passe d'abord sur les preuves pas encore
   commitées, comme le commit les passera : sinon le commit les réécrivait (format, fin de
   fichier) après le contrôle, et la trace était périmée dès l'approbation.
3. **Approuver.** Vous commitez le dossier entier (contrat, tests, `rouge.json`) sur la
   branche principale. **Ce commit est l'approbation** : le juge refuse tout contrat absent
   de la base ou différent de sa version approuvée, et toute tâche sans contrôle rouge, ou
   dont une preuve a changé depuis. Un test retouché après coup : relancez `make task-check`.
   `make task-check` sans `ID` liste les tâches dont la trace est à refaire, lues sur la
   branche principale comme le juge les lira (un reformatage du dépôt en périme plusieurs).
4. **Travailler.** L'agent travaille sur la branche `task/FEAT-001`, dans le périmètre
   `allowed_paths`. Il ne peut modifier ni ce dossier, ni les zones de contrôle. Plusieurs
   tâches en même temps : une par dossier, `make task-worktree ID=FEAT-001`, et une session
   d'agent dans chacun. Deux sessions dans un même dossier partagent la branche et les
   fichiers, et se marchent dessus : Ephor le signale au début du tour.
5. **Juger.** `make judge TASK=FEAT-001` en local, puis le job `juge` de la CI sur la merge request.

**D'un seul geste.** `make task-approve ID=FEAT-001 FROM=.verify/plans/FEAT-001.md` fait
les étapes 1 à 3 : il montre le résumé du plan, attend votre « oui » dans le terminal, crée
le dossier, lance le contrôle rouge et commite l'approbation sur la branche principale. À
préférer quand le plan vous convient tel quel ; sinon, `make task-new`, retouchez, puis
`make task-check` et le commit.

**Où en sont les tâches : `make taches`.** Une ligne par tâche approuvée : son lot, son
état, son risque, son niveau, son objectif. L'état est calculé à partir de git et du journal
du juge, jamais tenu à la main : approuvée, en cours (sa branche existe), vérifiée, en échec,
rouge à refaire, fusionnée (un commit de la branche principale nomme `task/<ID>`, comme le
fait la fusion d'une merge request). Les tâches fusionnées sont cachées : `TOUT=1` les montre.

**Ranger par fonctionnalité : `tasks/<lot>/<ID>/`.** Avec une centaine de tâches, un dossier
par fonctionnalité (`tasks/paiement/`, `tasks/auth-sso/`, en minuscules) garde l'arborescence
lisible. À la création : `LOT=paiement` sur `make task-new` ou `make task-approve`. Pour des
tâches déjà là : `make task-ranger LOT=paiement ID=PAY-012`, ou `PREFIXE=PAY-` pour toutes
celles qui commencent ainsi, dans un commit sur la branche principale. Le contrat et la trace
rouge d'une tâche rangée restent valables : ils parlent de `tasks/<ID>/…`, et le juge le
traduit vers le vrai dossier. Un identifiant reste unique, rangé ou non.

## Écrire un bon contrat

**Le niveau d'implémentation.** `"niveau": "rapide" | "standard" | "fort"` et
`"effort": "low" … "max"` disent quel modèle doit écrire la tâche : sa difficulté, pas son
risque. `make task-worktree` donne alors la ligne de lancement qui va avec (Claude Code :
`claude --model opus --effort max` ; opencode : le modèle du poste pour ce niveau).
L'`evaluator` (modèle fort) relit chaque tâche, quel que soit son niveau. Le juge note le
niveau dans son rapport : `make score` dira, sur de vraies tâches, ce que chaque niveau coûte.
Le modèle et l'effort de chaque rôle (évaluateur, relecteur, planner, scout) se règlent dans
l'en-tête de son sous-agent, `.claude/agents/<rôle>.md` : `model:` et `effort:`. La mise à
jour d'Ephor garde ces deux lignes ; `make adapters` les passe à opencode.

**Hors Python** (Maven, Gradle, Jest, Go) : la preuve qui va au bout est le rapport JUnit de
l'outil de test, écrit dans le dossier `{rapport}` que le juge lui donne, hors de la copie
jugée. Le critère désigne ses tests (`rapport_junit`) et nomme leurs fichiers (`preuves`),
que l'agent ne peut alors plus toucher :

```json
{
  "id": "C1",
  "description": "un virement refusé laisse le solde intact",
  "check": ["mvn", "-q", "test", "-Dtest=VirementTest#refus", "-Dsurefire.reportsDirectory={rapport}"],
  "rapport_junit": ["com.exemple.VirementTest.refus"],
  "preuves": ["src/test/java/com/exemple/VirementTest.java"]
}
```

Jest : `["sh", "-c", "JEST_JUNIT_OUTPUT_DIR={rapport} npx jest src/x.test.js --reporters=jest-junit"]`.
Go : `["gotestsum", "--junitfile", "{rapport}/go.xml", "--", "./pkg/..."]`. Sans rapport, un
critère n'est jugé que sur son code de retour, et il est refusé en risque élevé.


Un critère est bon quand un tiers qui n'a pas vu le code peut trancher sans discuter.
« L'import fonctionne bien » est mauvais. « Un CSV de 10 000 lignes dont 3 malformées
importe 9 997 lignes et écrit les 3 rejets dans un rapport » est bon.

Chaque critère a une commande `check` qui échoue tant que le comportement n'existe pas, et
`make task-check` le vérifie. Les tests d'acceptation vivent dans ce dossier, hors de
`tests/` : la suite normale reste verte sur la branche principale tant que la fonctionnalité
n'est pas faite. Ce qui doit seulement **rester** vrai (« un libellé valide reste nettoyé »)
n'est pas un critère : il passe déjà. Sa place est la suite normale, que l'étape `verify`
du juge fait tourner à chaque jugement.

**Chaque contrat a ses propres tests.** Un critère qui lance le test d'un autre contrat lie
les deux tâches : le juge de l'une peut attendre le travail de l'autre, et changer ce test
périme les deux contrôles rouges. Vu en session réelle : aucun verdict propre. Ce qui est
commun aux deux se recopie dans le dossier de chacune. `make task-check` et
`make task-approve` le signalent, et le juge le rappelle en revue.

**Une preuve doit montrer qu'elle est allée au bout.** Un code de sortie 0 ne suffit pas :
le code jugé peut le provoquer avant la première assertion. Le juge reconnaît trois formes
qui le prouvent, et dit pour chaque critère laquelle il a vue :

- un **test pytest** désigné par son nom (`uv run pytest -q tasks/FEAT-001/test_x.py::test_a`) :
  le juge exige un rapport de tests complet où ce test a tourné et réussi. C'est la forme
  de `make task-new`, à préférer. N'ajoutez pas `--junitxml` : le juge le fait lui-même ;
- un **script Python** (`{python} tasks/FEAT-001/check_c1.py`) : il doit aller jusqu'à sa
  dernière ligne ;
- une **sortie attendue**, comparée à l'octet près de l'extérieur du processus — la plus
  solide, idéale pour un outil en ligne de commande :

```json
{
  "id": "C2",
  "description": "Un lot vide rend 0.0",
  "check": ["uv", "run", "python", "-m", "app.cli", "precision"],
  "stdin": "[]",
  "expected_output": "0.0\n",
  "returncode": 0
}
```

Toute autre commande (`npm test`, un script shell…) reste jugée sur son seul code de
retour, et le verdict le signale. Sur une tâche à risque `eleve`, elle est refusée.

Le champ facultatif `budget_tokens` plafonne la dépense de la tâche : dépassé, Ephor
pose `AGENT_STOP` et rend la main (détail : `.ephor/docs/07-token-budget.md`).

Un critère peut aussi fixer `max_duration_seconds` : au-delà, le juge coupe la commande et
le critère échoue avec « trop lent ». C'est fait pour attraper une régression grossière
— un import qui passe de deux secondes à deux minutes — pas pour mesurer finement. L'horloge
d'un portable est bruyante : prenez dix fois la durée observée, et jamais un micro-benchmark.
En conteneur, la mesure comprend le démarrage du conteneur, environ une seconde de plus
qu'en local : en dessous de quelques secondes, le verdict dépendrait du mode d'exécution.

**Où vivent les preuves.** Un critère doit s'appuyer sur un fichier que l'agent ne peut pas
écrire : sous `tasks/`, ou déjà commité sur la branche principale. Si un critère cite un
fichier du périmètre de l'agent qui n'existe pas encore sur la base, le juge refuse le
contrat avant d'exécuter quoi que ce soit — sinon l'agent noterait sa propre copie.

Le champ `risk` règle la dépense de revue. L'`evaluator` relit chaque tâche, quel que soit
son risque : le juge ne voit que les critères. `eleve` : en plus, test de mutation, relecteur
et revue humaine attentive ; le juge y exige en plus que chaque critère prouve qu'il est
allé au bout, et refuse tout silencieux ajouté par le patch (`# noqa`, `# type: ignore`,
`pytest.mark.skip`…). Un silencieux vraiment justifié, c'est vous qui l'ajoutez sur la
branche principale. Chaque revue par un modèle coûte des tokens : ne la payez que
si le risque la justifie.

Référence complète : `.ephor/README.md`. Exemple : `EXEMPLE-001/`.
