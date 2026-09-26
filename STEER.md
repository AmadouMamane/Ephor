# STEER

> Vide = rien à signaler. Écrivez ici pour réorienter un agent en cours de route
> SANS le redémarrer : le contenu lui est injecté au tour suivant.
> Videz-le quand la consigne est appliquée.
>
> SÉQUENCEMENT — le point qui fait rater le premier essai : le hook se déclenche
> à l'ENVOI de votre message, avant tout appel d'outil. Écrire ce fichier depuis la
> session à réorienter ne peut donc pas marcher : le hook a déjà lu
> l'ancien contenu. Écrivez-le depuis un AUTRE terminal, puis envoyez un nouveau
> message dans la session.
