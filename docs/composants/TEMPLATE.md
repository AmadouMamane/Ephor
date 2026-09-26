# Composant : <nom>

> Fiche chargée **à la demande**, jamais au démarrage. Trois minutes à écrire ; des dizaines
> de milliers de tokens économisés à chaque session qui touche ce composant. Si elle dépasse
> une page, c'est le composant qui est trop gros, pas la fiche qui est trop courte.

- **Rôle** : une phrase, côté produit.
- **Entrées / sorties** : ce qui entre, ce qui sort, dans quel format.
- **Fichiers** : le point d'entrée d'abord, puis le reste. `src/...`
- **Dépend de / dépendu par** : les voisins immédiats, pas le graphe entier.
- **Invariants** : ce qui doit rester vrai, et ce qui casse si ça ne l'est plus.
- **Pièges** : ce qui a déjà cassé ici, avec la date.
- **Tests qui font foi** : `tests/...` — ceux qui échouent si le composant régresse.
- **Décisions** : ADR liées, s'il y en a.
