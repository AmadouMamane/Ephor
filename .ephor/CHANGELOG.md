# Journal des versions d'Ephor

Une entrée par version : ce qui change pour qui s'en sert. Les raisons de fond sont dans
`.ephor/docs/adr/`. La version en place est dans `.ephor/VERSION`.

## 1.0.0 — 26 septembre 2026

Première version partagée avec l'équipe.

**Le juge.** Il rend le verdict d'une tâche hors de l'agent, contre un contrat approuvé
sur `main`, dans une copie clonée à part, avec un environnement filtré. Une preuve doit
montrer qu'elle est allée au bout : rapport de tests complet, sortie exacte comparée de
l'extérieur, ou jeton imprimé en fin de script. Les preuves sont relevées avant chaque
étape et recomparées après. Il tourne à l'identique en local, sous Podman ou Docker sans
réseau, et en CI, où il vient toujours de la branche cible.

**Les garde-fous.** Une seule liste de zones de contrôle (`.ephor/zones.txt`), lue par les
gardes d'écriture, de lecture et du terminal, par la sentinelle de fin de tour, par le juge
et par la CI. Sous Claude Code, le terminal tourne dans un bac à sable qui refuse d'écrire
une zone, détour compris. Arrêt d'urgence (`make stop`) et consigne en cours de route
(`STEER.md`).

**Trois agents.** Claude Code, opencode et GitHub Copilot, à partir d'une seule source
(`.claude/`). Copilot passe par un greffon posé une fois par poste (`make copilot-install`).

**Les tokens.** Mesure par tâche, plafond qui arrête l'agent, sous-agents compris ; carte
du code (`make map`) pour lire moins.

**La conformité.** Secrets (gitleaks), données personnelles dans les journaux et les
messages d'erreur, branchement du scanner de sécurité de l'entreprise (`make sec`).

**Le reste de la chaîne.** Revue proportionnée au risque du contrat (`make review`),
manifeste de livraison lié au code jugé, dépôts voisins en lecture seule (`make refs`),
pages du web entrées comme données (`make fetch-docs`), tableau de bord des jugements
(`make score`), installation et mise à jour dans un dépôt existant.

Ce qui n'a pas encore tourné en conditions réelles est listé à la fin du `README.md`.
