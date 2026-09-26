# Croyances et non-objectifs

> Ce fichier existe pour que l'agent n'ait pas à deviner vos arbitrages. C'est le
> document le plus rentable du dépôt : deux pages ici évitent cinquante allers-retours.

## Ce que ce projet optimise

1. TODO — <l'axe n°1, celui qui tranche quand deux options se valent techniquement>
2. TODO
3. TODO

## Ce que ce projet refuse explicitement d'être

- TODO — <le hors-périmètre qui revient sans cesse>
- TODO

## Arbitrages tranchés une fois pour toutes

| Question | Réponse | Pourquoi |
|---|---|---|
| Technologie inconnue vs technologie ennuyeuse | ennuyeuse | API stable, forte présence dans les données d'entraînement des modèles : l'agent la connaît, il ne l'invente pas |
| Abstraction anticipée vs duplication | duplication, jusqu'à 3 occurrences | une mauvaise abstraction coûte plus cher que trois copies |
| Test lent et fidèle vs test rapide et approximatif | les deux, séparés par marqueur | l'étage pré-file ne voit que les rapides |
| Config implicite vs explicite | explicite, validée au démarrage | un agent ne devine pas une convention tacite |

## Ce qui doit rester vrai même sous pression de délai

- TODO — <l'invariant qu'on ne sacrifie jamais, même en incident>
