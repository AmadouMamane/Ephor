# Contrats de tâche

Une fonctionnalité confiée à un agent commence ici, **avant** la première ligne de code.
Une petite correction sans enjeu n'a pas besoin de contrat : `make verify` et la CI suffisent.

## Le circuit

1. **Rédiger.** Le sous-agent `planner` propose le contrat et les tests d'acceptation. Il
   n'écrit aucun fichier. Vous enregistrez son bloc JSON dans un fichier et vous le passez en
   entrée : `make task-new ID=FEAT-001 FROM=plan.json`. Le contrat est vérifié avant
   d'être écrit — s'il est mauvais, rien n'est créé — et les fichiers de test cités par
   les critères sont créés avec un test vide par critère, qu'il ne reste qu'à remplir.
   Sans plan sous la main, `make task-new ID=FEAT-001` donne un squelette à compléter.
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
3. **Approuver.** Vous commitez le dossier entier (contrat, tests, `rouge.json`) sur la
   branche principale. **Ce commit est l'approbation** : le juge refuse tout contrat absent
   de la base ou différent de sa version approuvée, et toute tâche sans contrôle rouge, ou
   dont une preuve a changé depuis. Un test retouché après coup : relancez `make task-check`.
4. **Travailler.** L'agent travaille sur la branche `task/FEAT-001`, dans le périmètre
   `allowed_paths`. Il ne peut modifier ni ce dossier, ni les zones de contrôle.
5. **Juger.** `make judge TASK=FEAT-001` en local, puis le job `juge` de la CI sur la merge request.

## Écrire un bon contrat

Un critère est bon quand un tiers qui n'a pas vu le code peut trancher sans discuter.
« L'import fonctionne bien » est mauvais. « Un CSV de 10 000 lignes dont 3 malformées
importe 9 997 lignes et écrit les 3 rejets dans un rapport » est bon.

Chaque critère a une commande `check` qui échoue tant que le comportement n'existe pas, et
`make task-check` le vérifie. Les tests d'acceptation vivent dans ce dossier, hors de
`tests/` : la suite normale reste verte sur la branche principale tant que la fonctionnalité
n'est pas faite. Ce qui doit seulement **rester** vrai (« un libellé valide reste nettoyé »)
n'est pas un critère : il passe déjà. Sa place est la suite normale, que l'étape `verify`
du juge fait tourner à chaque jugement.

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

Le champ `risk` règle la dépense de revue. `faible` : les contrôles automatiques suffisent.
`modere` : relecture par le sous-agent `evaluator`. `eleve` : évaluateur, test de mutation
et revue humaine attentive ; le juge y exige en plus que chaque critère prouve qu'il est
allé au bout, et refuse tout silencieux ajouté par le patch (`# noqa`, `# type: ignore`,
`pytest.mark.skip`…). Un silencieux vraiment justifié, c'est vous qui l'ajoutez sur la
branche principale. Chaque revue par un modèle coûte des tokens : ne la payez que
si le risque la justifie.

Référence complète : `.ephor/README.md`. Exemple : `EXEMPLE-001/`.
