# ADR 0008 — Le terminal de Claude Code dans un bac à sable : la frontière que la garde n'est pas

- **Date** : 2026-09-25
- **Statut** : accepté

## Contexte

La garde du terminal (`shell-guard.sh`) lit une commande et refuse celle qui écrit une zone
de contrôle. C'est un filet, pas une frontière : un script écrit ailleurs puis lancé, une
variable, un encodage lui échappent, et aucune analyse de texte ne les fermera. La
sentinelle voit le résultat en fin de tour, et le juge refuse le correctif : l'écriture est
signalée et rattrapée, pas empêchée. Avant de promettre une vraie frontière, il fallait la
mesurer.

Claude Code a un bac à sable pour son terminal (Seatbelt sur Mac, bubblewrap sous Linux).
Tout ce que le terminal lance y tourne, et le système refuse l'écriture là où les réglages
l'interdisent.

## Ce qui a été mesuré

Le 25/09/2026, Claude Code 2.1.282 sur Mac Apple Silicon, en sessions réelles pilotées,
dans des dépôts jetables, chaque essai avec son témoin (une écriture ordinaire, qui passe) :

| Essai | Sans bac à sable | Avec |
|---|---|---|
| `python3 -c "open('.ephor/judge.py','w')…"` | la garde du terminal le voit | refusé par le système : `Operation not permitted` |
| script écrit dans `src/`, puis lancé sur `.ephor/judge.py` | **passe** (aucune garde ne le voit) | refusé par le système |
| écrire `.git/config` par une redirection | passe | Claude Code demande (« fichier sensible ») ; même après **Oui**, le système refuse |
| écriture ordinaire dans `src/` | passe | passe |

Deux faits de plus :

- **Les refus `Edit(...)` des réglages suffisent.** Sans liste propre au bac à sable, il
  les applique au terminal. La liste de `.claude/settings.json` devient ainsi une frontière
  pour tout ce que le terminal lance.
- **Il tient sans autoriser d'office les commandes.** Avec `autoAllowBashIfSandboxed`
  désactivé, la liste blanche d'Ephor et ses questions restent la règle ; le bac à sable
  s'ajoute par-dessus.

Trois ruptures de la boucle quotidienne, mesurées puis levées :

1. `uv` écrit son cache hors du dépôt (`~/.cache/uv`) : `make verify` échouait entièrement.
   Levée : `filesystem.allowWrite` pour `~/.cache/uv` et `~/.cache/ephor` (les copies du
   juge).
2. `ruff` rangeait son cache dans `.ephor/`, une zone : fmt et lint échouaient. Levée à la
   source : son cache va à la racine du projet (`.ephor/ruff.toml`). La nouvelle sentinelle
   l'aurait aussi pris pour une retouche à chaque vérification.
3. Refuser tout `.git/` empêchait git d'écrire ses objets : ni commit, ni juge. Levée : les
   réglages de Claude Code refusent ce que git EXÉCUTE (`.git/config`, `.git/hooks/`,
   `.git/info/`), pas ses objets. La liste du moteur (`.ephor/zones.txt`) garde `.git/`
   entier : elle ne sert qu'aux outils d'édition, qui n'ont rien à écrire là.

Avec ces réglages, dans le bac à sable : `make verify` GREEN, `make judge` VÉRIFIÉ en local,
`git commit` passe.

## Décision

**Le bac à sable est activé par défaut dans `.claude/settings.json`** :

```json
"sandbox": {
  "enabled": true,
  "autoAllowBashIfSandboxed": false,
  "allowUnsandboxedCommands": false,
  "filesystem": { "allowWrite": ["~/.cache/uv", "~/.cache/ephor"] }
}
```

`allowUnsandboxedCommands: false` compte autant que le reste : sans lui, l'agent peut
demander à sortir du bac à sable pour une commande, et un clic suffit.

## Conséquences

- **Sous Claude Code, écrire une zone de contrôle par le terminal n'est plus possible, détour
  compris** — tant que le bac à sable tourne. La garde du terminal reste en place : elle
  explique le refus avant que la commande ne parte, et elle sert opencode et Copilot.
- **Le réseau des commandes passe par le mandataire du bac à sable** : un domaine nouveau
  (`uv sync` vers un dépôt de paquets, `git push`) fait poser une question. Un domaine
  d'entreprise se déclare dans `sandbox.network.allowedDomains`. Le juge, lui, installe
  d'abord sur le cache d'uv, sans réseau (1.5.4) : lancé par l'agent, il ne pose la question
  qu'à la première installation d'une dépendance nouvelle.
- **`make judge RUNTIME=podman` lancé par l'agent ne joint plus Podman** (sa socket est hors
  du bac à sable). Le juge en conteneur est une commande de l'opérateur, lancée hors de
  l'agent ; l'agent juge en local.
- **Pas encore mesuré** : le mandataire du bac à sable derrière le proxy de
  l'entreprise, et un dépôt JFrog.
- **Sous Linux, le bac à sable demande bubblewrap et socat.** Sans eux, ou quand bubblewrap
  ne peut pas démarrer (AppArmor d'Ubuntu 24.04, conteneur sans droits, WSL1), Claude Code
  le saute avec un simple avertissement. `make doctor` lance bubblewrap pour de vrai et le
  dit (mesuré le 26/09/2026 sur Fedora : il démarre ; dans un conteneur sans droits : non).
  Pour que Claude Code refuse de démarrer plutôt que de le sauter :
  `"failIfUnavailable": true` dans le bloc `sandbox`.
- **L'opérateur garde la main** : `{"sandbox": {"enabled": false}}` dans
  `.claude/settings.local.json` le coupe sur son poste. La sentinelle signale ce réglage
  s'il apparaît pendant un tour, et `make doctor` le dit toujours.
- **opencode et Copilot n'ont pas d'équivalent mesuré.** Pour eux, la frontière reste
  l'agent enfermé dans un conteneur, zones en lecture seule : mesuré le 25/09/2026, pas
  installé (`.ephor/docs/06-exploitation.md`).
