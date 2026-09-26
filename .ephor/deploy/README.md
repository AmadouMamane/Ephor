# Livraison — GitLab, JFrog, puis Kubernetes ou GCP

> Statut : **principes et contrat de livraison, pas encore de pipeline.** Le pipeline se
> construit sur la chaîne réelle de l'entreprise, une fois connus le type de runner, le chemin
> JFrog, le dépôt Argo CD et le projet GCP. Rien ici n'a été exécuté contre un
> environnement réel.

## La règle qui ne se négocie pas

L'agent propose du code. La CI construit l'artefact. Une identité distincte le déploie.
**Aucun agent ne détient d'accès de déploiement**, ni au poste, ni en CI. Les réglages
Claude Code du dépôt demandent confirmation pour `kubectl`, `gcloud`, `podman` et
`docker`, et le juge refuse toute modification de ce dossier.

## La chaîne

1. **Merge request** sur GitLab, branche `task/<ID>` : le job `juge` doit être vert, puis
   revue humaine. Les contrôles, les contrats et ce dossier relèvent de propriétaires
   désignés : voir `.ephor/docs/CODEOWNERS.example`.
2. **Train de fusion** : l'étage 2 de `.gitlab-ci.yml` vérifie le résultat combiné.
3. **Construction, une seule fois**, après fusion : image construite en CI avec Podman
   ou Buildah selon le runner, poussée dans JFrog, **identifiée par son digest**.
4. **Manifeste de livraison** joint à l'image : commit, digest, empreinte de `uv.lock`,
   identifiant du contrat, empreinte du rapport du juge et, pour un produit d'IA, les
   versions du modèle, des prompts et de l'index documentaire.
   `make delivery-manifest` refuse un verdict qui n'atteste pas ce qui part : verdict
   défavorable, juge changé depuis, ou fichier jugé retouché après coup : une livraison
   exige une preuve courante, liée au contenu. En CI, `--report` désigne le
   rapport du job `juge`. Sans `--report`, le rapport est lu dans `.verify/`, que l'agent
   peut écrire : un manifeste fait ainsi sur le poste est un essai, pas une preuve, et la
   commande le dit.
5. **Promotion du même digest**, jamais une reconstruction, d'un environnement à l'autre.

## Vers Kubernetes, avec Argo CD

- La CI ouvre une merge request sur le dépôt de configuration, qui ne change que le digest.
- Argo CD réconcilie. Il ne valide pas le métier à lui seul, ni n'active de canari tout seul.
- Déploiement progressif : seulement si un contrôleur de déploiement progressif est déjà
  adopté par l'équipe. Sinon, un environnement de préproduction et une bascule manuelle.
- Retour arrière : annuler la merge request de configuration. Le digest précédent revient.

## Vers GCP

- Authentification de la CI par fédération d'identité : jeton OIDC de GitLab, pool
  Workload Identity côté GCP. **Aucune clé de compte de service en variable CI.**
- Cloud Run : nouvelle révision sans trafic (`--no-traffic`, avec une étiquette), tests de
  fumée sur son URL, puis montée progressive du trafic ; retour arrière en rendant le
  trafic à la révision précédente.
- GKE : même logique qu'avec Argo CD. Vertex AI : versionner modèle et point d'accès, et
  garder le précédent déployé jusqu'à la fin de la fenêtre d'observation.

## Avant la production

- Environnements protégés dans GitLab, approbation manuelle pour la production.
- Signaux et seuils choisis **avant** le déploiement : erreurs, latence, saturation,
  indicateur métier. Pas de promotion automatique sur une fenêtre sans trafic.
- Migration de données : plan expand/contract et sauvegarde testée. Un retour arrière
  d'image ne restaure pas des données transformées.

Proposition de politique à calibrer : `release-policy.example.json`.
