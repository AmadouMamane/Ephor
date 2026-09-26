# Autorité de l'entreprise, pour l'image de CI et du juge

Derrière un proxy qui déchiffre le trafic, ou face à un JFrog signé en interne, les outils
de l'image refusent la connexion (`invalid peer certificate: UnknownIssuer` pour uv).

Déposez ici le certificat **public** de l'autorité de l'entreprise, au format PEM, avec
l'extension `.crt` (un `.pem` se renomme simplement), puis `make ci-image`. L'image
l'ajoute au magasin du système, et uv, Python et git le reconnaissent. La construction
échoue si le fichier n'est pas du PEM, ou s'il ne finit pas par `.crt`.

Jamais de clé privée ici : `make sec` la refuserait. Le certificat, lui, n'a rien de
secret ; le versionner ou non est un choix de l'équipe.

Le poste de travail se règle à part : voir `.ephor/docs/10-installation.md`, ligne
« Certificat de l'entreprise ».
