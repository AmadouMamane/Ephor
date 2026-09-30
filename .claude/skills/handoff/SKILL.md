---
name: handoff
description: Prépare un passage de relais propre avant /clear, avant une pause longue, ou avant de passer la main à une autre session. À utiliser dès que le contexte dépasse 60 %.
allowed-tools: Bash(git status *) Bash(git log *) Bash(git diff *) Bash(git add *) Bash(git commit *) Bash(make *) Read Edit Write
---

# Passage de relais

État :

!`git log --oneline -5 2>/dev/null || echo '(aucun commit)'`

!`git status --short 2>/dev/null | head -20 || true`

## Ce que tu écris dans PROGRESS.md

Réécris le fichier complet, pas un ajout en bas. Format strict, une ligne par élément :

```markdown
## Objectif courant
<une phrase>

## Fait
- <critère ou incrément> — commit <sha court>

## En cours
- <ce qui est à moitié fait, et à quel endroit exact : fichier:ligne>

## Bloqué
- <le blocage, ce qui a été essayé, ce qu'il faudrait pour débloquer>

## Décisions prises pendant la session
- <décision> — <raison en 5 mots> (ADR: docs/adr/XXXX si structurante)

## Prochain pas
- <le geste exact que la prochaine session doit faire en premier>
```

Puis commit : `git add -A && git commit -m "chore: point de relais"`.
Entre deux commits, Ephor garde aussi des points de reprise hors historique : `make checkpoints`.

## Pourquoi ce format

La prochaine session — toi dans une heure, un collègue, ou un autre agent — démarre
avec un contexte vierge. Ce fichier est la totalité de ce qu'elle saura. « En cours »
sans coordonnées de fichier est inutilisable. « Prochain pas » vague fait perdre
vingt minutes de redécouverte, à chaque reprise.

Écris pour quelqu'un qui n'a aucun souvenir de cette conversation. C'est le cas.
