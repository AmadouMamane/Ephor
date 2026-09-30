# ADR 0011 — Quatre points d'entrée, qui ne changent plus

- **Date** : 2026-09-30
- **Statut** : accepté ; complète l'ADR 0010 (audit du 29/09/2026, proposition F1)

## Contexte

Trois outils appellent les garde-fous d'Ephor, chacun à sa manière. Claude Code lit la liste
des hooks de `.claude/settings.json` : une ligne par garde, rangée par type d'outil. Copilot
exécute le greffon installé sur chaque poste, dont le `hooks.json` nomme chaque garde. Le
greffon d'opencode recopiait la sienne. Ajouter une garde — `contexte.sh` en 1.1.4,
`consigne.sh` et `sessions.sh` en 1.4 — touchait donc des fichiers du projet, que la mise à
jour ne remplace que s'ils n'ont jamais été retouchés (ADR 0010), et le greffon de Copilot,
à réinstaller sur chaque poste (`make copilot-install`, puis une fenêtre neuve). Trois listes
à tenir d'accord, et un poste oublié tournait avec l'ancienne.

## Décision

**Quatre points d'entrée, dans le moteur, un par événement** :

| Événement | Point d'entrée | Ce qu'il lance aujourd'hui |
|---|---|---|
| avant un appel d'outil | `avant-outil.sh` | bouton d'arrêt, puis les gardes de la famille de l'appel, ensemble |
| après un appel d'outil | `apres-outil.sh` | la consigne de l'opérateur en cours de tour |
| au message de l'opérateur | `debut-tour.sh` | photo de la sentinelle, autre session, consigne |
| quand l'agent rend la main | `fin-tour.sh` | budget, sentinelle et point de reprise, alerte de contexte |

Les réglages de Claude Code, le greffon de Copilot et celui d'opencode n'appellent qu'eux.
Quelles gardes tournent, et pour quel outil, se décide dans ces scripts, que la mise à jour
remplace avec le moteur. Le greffon d'opencode se réduit à une ligne qui importe
`.ephor/opencode/ephor.js`.

- `avant-outil.sh` range l'appel par famille (terminal, lecture, le reste) avec la règle de
  la bibliothèque qui dit si un appel écrit (`ecriture`) ; un appel qu'il ne sait pas ranger
  passe toutes les gardes, chacune reconnaissant elle-même les outils qui la regardent. Les
  gardes restent des scripts séparés, testés un par un, et tournent ensemble (`ensemble.sh`).
- Ce qui change d'un outil à l'autre se lit dans `EPHOR_OUTIL` (posé par le relais de Copilot
  et par le greffon d'opencode) : la garde de lecture pour Copilot seul, le filtre de sortie
  pour Claude Code seul, pas d'alerte de contexte sous Copilot, pas de plafond de tokens sous
  opencode.
- Le relais de Copilot garde, pour un dépôt d'avant la 1.4 qui n'a pas ces scripts, la liste
  des gardes d'avant : un poste réinstallé ne laisse pas un vieux dépôt sans garde-fou.

## Conséquences

- Ajouter, retirer ou réordonner une garde ne touche plus aucun fichier du projet, ni le
  greffon de Copilot sur les postes : la mise à jour du moteur suffit.
- Une dernière fois, en 1.4.0, les réglages de Claude Code, le greffon d'opencode et celui de
  Copilot changent, pour appeler les points d'entrée : mise à jour, `make copilot-install` sur
  chaque poste, puis relancer les outils.
- Sous Claude Code, un appel de lecture lance un processus d'aiguillage de plus qu'avant
  (le bouton d'arrêt tournait déjà sur chaque appel) ; sous Copilot, une lecture ne lance plus
  que deux gardes au lieu de sept.
- Sous opencode, dont le greffon bloque opencode le temps d'une garde, les lectures et les
  outils sans effet (liste de tâches, recherche web, sous-agent) ne lancent rien, sauf
  arrêt demandé : c'est le seul tri resté dans le greffon. Tout autre outil passe par
  `avant-outil.sh`, ceux d'un serveur MCP compris, qui passaient avant sans aucune garde.
- Un point d'entrée qui manque (des réglages plus récents que le moteur) : le greffon
  d'opencode refuse l'appel (« garde-fou injoignable »), le relais de Copilot retombe sur
  les gardes d'avant ; mais Claude Code montre l'erreur, puis laisse passer l'appel.
  `make doctor` le signale donc en échec.
