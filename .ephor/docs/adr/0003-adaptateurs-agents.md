# ADR 0003 — Dériver les réglages d'opencode et de Copilot depuis `.claude/`

- **Date** : 2026-09-22
- **Statut** : accepté

## Contexte

Dans l'environnement cible, trois agents coexistent : Claude Code, opencode et GitHub Copilot. Ephor
ne leur offrait qu'`AGENTS.md`. Entretenir à la main trois jeux de réglages, de sous-agents
et de commandes, c'est trois fois le travail et trois occasions de diverger — la divergence
étant invisible jusqu'au jour où un garde-fou manque.

Constat en essayant les outils : opencode 1.18.32 lit déjà les skills du dépôt sans rien
configurer, mais pas les sous-agents ; VS Code lit nativement les sous-agents, les skills
et `CLAUDE.md` au format Claude, mais n'active les garde-fous que sur demande.

## Décision

1. **`.claude/` est la source.** `.ephor/adapters.py`, bibliothèque standard, en dérive
   `.opencode/` (config, sous-agents, commandes), `.github/hooks/ephor.json` et les
   approbations de `.vscode/settings.json`. `make adapters` régénère, `make ephor-test`
   refuse la dérive.
2. **Aucune duplication de logique.** Les garde-fous restent des scripts shell dans
   `.ephor/hooks/` ; le greffon opencode ne fait que les appeler avec la même entrée JSON.
3. **Rien n'est dupliqué qui soit déjà lu nativement** : pas de copie des skills, pas de
   prompt files pour Copilot — VS Code les déprécie au profit des skills.
4. **Traduction fidèle, différences documentées.** Claude tranche par sévérité, opencode
   par ordre de règles ; VS Code « refuse » en demandant. `.ephor/docs/11-trois-agents.md` dit ce
   qui est vraiment bloqué et ce qui ne l'est pas.
5. **Les zones de contrôle s'élargissent** aux fichiers des nouveaux outils : `.opencode/`,
   `opencode.json`, `.vscode/` sont refusés à l'édition, par le juge comme par la CI.

## Conséquences

- Changer une permission se fait à un seul endroit, et les trois outils suivent.
- Un nouvel outil coûte une fonction dans `.ephor/adapters.py`, pas un nouveau dossier
  entretenu à la main.
- Ephor dépend maintenant du format de trois outils qui bougent vite. Le prix est
  visible : quand un format change, `make ephor-test` casse et pointe le générateur.
- Copilot a depuis été mesuré en session réelle : `.ephor/docs/11-trois-agents.md`.
