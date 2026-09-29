# ADR 0010 — Mettre à jour aussi ce qu'Ephor pose hors de `.ephor/`

- **Date** : 2026-09-29
- **Statut** : accepté ; complète l'ADR 0007

## Contexte

L'ADR 0007 remplace le moteur (`.ephor/`) quand git en garde l'ancien contenu, et rien
d'autre : hors de `.ephor/`, le `Makefile`, les réglages et les sous-agents de Claude Code
« peuvent avoir été retouchés et commités par le projet, et rien ne permet de le savoir ».
Ils recevaient donc un `.ephor-new` à chaque mise à jour.

Or une partie d'Ephor vit forcément là, parce que chaque outil la cherche à son adresse :
le greffon d'opencode (`.opencode/plugins/ephor.js`), les hooks déclarés dans
`.claude/settings.json`, les sous-agents, les skills. Les versions 1.1.4 et 1.1.5 ont changé
justement ces fichiers. Mesuré le 29/09/2026 sur un dépôt équipé en 1.1.3 puis mis à jour
comme le CHANGELOG le disait : `.ephor/VERSION` affichait 1.1.5, mais opencode chargeait le
greffon de la 1.1.3 (ni plafond de réponse relevé, ni sentinelle), l'alerte de contexte
n'était branchée nulle part, et le `planner` d'opencode était régénéré depuis l'ancien. Le
bilan nommait les `.ephor-new` en passant ; rien ne disait que sans eux, la mise à jour
n'était pas faite.

## Décision

**Savoir, au lieu de supposer.** `.ephor/empreintes.txt` liste l'empreinte (sha256) de
chaque version qu'Ephor a livrée de chacun des fichiers qu'il pose hors du moteur. Elle est
calculée dans le dépôt d'Ephor, depuis son histoire, par `.ephor/scripts/empreintes.py`, et
voyage avec le moteur. Un test refuse une livraison dont la version actuelle manque.

En mise à jour, un tel fichier qui a changé est **remplacé** si deux conditions tiennent :

- son contenu est exactement celui du dernier commit (la règle de l'ADR 0007 : l'ancien
  reste dans l'historique du projet) ;
- ce contenu est, à l'octet près, **une version livrée par Ephor** : le projet ne l'a
  jamais retouché.

Sinon — retouché par le projet, ou d'une version d'avant la 1.0, que la liste ne connaît
pas — il garde son `.ephor-new`, et le bilan le donne **« à reprendre »** : la mise à jour
n'est pas finie sans lui, c'est encore l'ancienne version qui tourne. Pour des réglages
Claude retouchés, le bilan nomme les garde-fous d'Ephor qu'ils ne déclarent pas
(« Stop : contexte.sh »). `make doctor` signale tout `.ephor-new` en attente, et un greffon
d'opencode qui n'est pas celui du moteur.

Ce qui ne change pas : les réglages du projet (`.ephor/projet/`), `PROGRESS.md`, `STEER.md`
et la documentation du projet ne sont jamais remplacés ; les fichiers que les adaptateurs
dérivent sont régénérés, comme avant ; rien n'est retiré ; sans `--mettre-a-jour`, rien
n'est écrasé.

## Conséquences

- Un projet qui n'a pas retouché ces fichiers est à jour en une commande : le greffon, les
  réglages, les sous-agents et les skills arrivent, les `.ephor-new` d'une mise à jour
  précédente sont retirés. Il reste à relancer opencode et Claude Code, qui ne relisent leurs
  réglages qu'au démarrage.
- Un projet qui les a fusionnés avec les siens garde la main, mais on lui dit quoi reprendre.
- À chaque livraison qui change l'un de ces fichiers : `python3 .ephor/scripts/empreintes.py`,
  puis commit de la liste. Oublié, le test `make ephor-test` échoue.
- Les versions d'avant la 1.0 ne sont pas dans la liste (leur histoire n'est pas dans ce
  dépôt) : un projet de cette époque reprend ces fichiers à la main, une fois.
