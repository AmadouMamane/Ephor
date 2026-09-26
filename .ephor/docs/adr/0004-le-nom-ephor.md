# ADR 0004 — Nommer le produit Ephor

- **Date** : 2026-09-23
- **Statut** : accepté

## Contexte

Un nom descriptif (« harnais », *harness*) dit la plomberie, pas la promesse : ce qui
distingue ce produit, ce n'est pas d'encadrer un agent — tout le monde le revendique —
c'est de faire rendre le verdict **hors** de l'agent, contre un contrat que l'agent ne peut
pas toucher. Et « Harness » est déjà le nom d'une société de CI/CD connue, donc pris dans
le domaine exact.

Une dizaine de candidats ont été passés au même crible : dire ce que personne d'autre ne
fait, se lire en français comme en anglais, tenir en deux syllabes, s'écrire sans accent en
ligne de commande. Plusieurs ont été écartés pour saturation (Aegis : une application
d'authentification, une gamme Proofpoint, un système de la marine américaine, une famille de
chiffrements) ou pour collision de sens (Pact : l'outil de référence des tests de contrat
entre services, même famille d'idées).

## Décision

Le produit s'appelle **Ephor**, d'après les éphores de Sparte : cinq magistrats dont la
charge était de contrôler les rois. L'image dit la chose juste — le contrôle ne dépend pas
du puissant. Ici, l'agent fait le travail ; Ephor dit si ça tient.

Collisions connues, acceptées en connaissance de cause : le compte `github.com/ephor` est
pris par un particulier, et `staqsIO/ephor` est un projet voisin de gouvernance d'agents.
Le nom ne sera simplement pas exclusif si le produit sort un jour de l'entreprise.

## Conséquences

- Tout ce qu'Ephor pose porte son nom : le dossier `.ephor/`, les variables `EPHOR_*`, le
  greffon Copilot `ephor`, les fichiers de travail (`.ephor-new`, `.verify/ephor.txt`,
  `refs/ephor/checkpoints`).
- Le mot « harnais » reste employé comme NOM COMMUN là où il désigne la catégorie d'outils
  et non ce produit : comparer plusieurs harnais, citer un harnais tiers, évaluer un harnais.
