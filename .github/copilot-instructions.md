Suis AGENTS.md : c'est le contrat de travail de ce dépôt, commun à Copilot, Claude Code et opencode.
Commandes : `make verify` pour la boucle rapide, `make judge TASK=<ID>` pour le verdict d'une tâche.
Ne modifie jamais `tasks/`, `.ephor/`, la CI ni le `Makefile` :
le juge refuse ces changements. Reste dans le périmètre du contrat et rapporte les contrôles
réellement exécutés, avec leurs limites.
