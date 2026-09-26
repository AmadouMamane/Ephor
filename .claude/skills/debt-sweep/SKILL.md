---
name: debt-sweep
description: Passe de nettoyage courte et sans risque sur le dépôt. À lancer en fin de journée ou entre deux fonctionnalités, jamais au milieu d'une implémentation.
allowed-tools: Bash(make *) Bash(git *) Bash(rg *) Read Edit Task
---

# Ramassage de dette

Délègue d'abord au sous-agent `debt-collector` : il inventorie, il ne modifie rien.

Puis traite **une seule** entrée de sa liste, la moins risquée, dans son propre commit.
Relance `make verify`. Si c'est vert, commit ; sinon, annule (`git restore .`) et passe
à l'entrée suivante.

## Règle de taille

Une PR de nettoyage doit être relisable en moins d'une minute. Si ton diff dépasse
cela, tu as traité trop d'entrées à la fois : découpe.

## Ce qu'on ne nettoie pas

Le code qu'on s'apprête à réécrire. Le nettoyage d'un module condamné est du travail
perdu, et il pollue l'historique juste avant la réécriture.
