// Garde-fous de session pour opencode — bouton d'arrêt, anti-secret, consigne opérateur.
//
// Rien n'est réimplémenté ici : la logique vit dans .ephor/hooks/*.sh, déjà couverte par
// `make ephor-test`. Ce greffon ne fait que traduire les appels d'opencode vers ces
// scripts, dont l'entrée est du JSON au format de Claude Code. Une seule logique, trois
// outils. Les garanties, elles, ne sont pas ici : elles sont dans le juge et la CI.
//
// Dépendances : aucune. Node et Bun exposent tous deux ces modules.
import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

// Outils d'opencode qui écrivent dans un fichier, et l'argument qui porte le contenu.
const ECRITURE = new Set(["edit", "write", "patch", "apply_patch", "multiedit"])

function garde(racine, script, entree) {
  const resultat = spawnSync(join(racine, ".ephor", "hooks", script), [], {
    input: entree === undefined ? undefined : JSON.stringify(entree),
    encoding: "utf8",
    timeout: 15000,
    env: { ...process.env, CLAUDE_PROJECT_DIR: racine },
  })
  if (resultat.error) return { refuse: true, message: `garde-fou injoignable : ${resultat.error.message}` }
  if (resultat.status !== 0) return { refuse: true, message: (resultat.stderr || "").trim() }
  return { refuse: false, message: (resultat.stdout || "").trim() }
}

export const ephor = async ({ worktree, directory }) => {
  const racine = worktree ?? directory
  return {
    "tool.execute.before": async (input, output) => {
      const args = output.args ?? {}
      // Une seule forme, traduite une fois : les garde-fous savent y lire les chemins,
      // y compris ceux cachés dans un correctif. Rien n'est analysé ici.
      const entree = {
        tool_name: input.tool,
        tool_input: ECRITURE.has(input.tool)
          ? {
              file_path: args.filePath ?? args.file_path,
              path: args.path,
              content: args.content,
              patch: args.patchText ?? args.patch,
              new_string: args.newString ?? args.new_string,
              edits: (args.edits ?? []).map((e) => ({
                file_path: e.filePath ?? e.file_path,
                new_string: e.newString ?? e.new_string,
              })),
            }
          : args,
      }
      // Bouton d'arrêt. Le fichier est le signal ; le hook donne le message et le verdict.
      // Il reçoit l'appel : sans lui, il refusait aussi l'écriture de PROGRESS.md, la seule
      // qu'il laisse passer pour que l'agent note son état avant de s'arrêter.
      if (existsSync(join(racine, "AGENT_STOP"))) {
        const arret = garde(racine, "kill-switch.sh", entree)
        if (arret.refuse) {
          throw new Error(
            arret.message ||
              "AGENT_STOP est présent : l'opérateur a demandé l'arrêt. Résume ton état dans PROGRESS.md puis arrête-toi.",
          )
        }
      }
      // Le terminal : écrire une zone de contrôle par une commande, c'est contourner le
      // refus de l'édition. La garde lit la commande et nomme ce qu'elle écrit.
      if (input.tool === "bash") {
        const terminal = garde(racine, "shell-guard.sh", { tool_name: "bash", tool_input: { command: args.command } })
        if (terminal.refuse) throw new Error(terminal.message || "REFUSÉ : écriture d'une zone de contrôle par le terminal.")
        return
      }
      if (!ECRITURE.has(input.tool)) return
      const refus = {
        "block-secrets.sh": "REFUSÉ : contenu bloqué par le contrôle anti-secret.",
        // Les zones du moteur : opencode applique les refus de sa configuration, dérivés des
        // réglages du projet, mais pas la liste du moteur (.ephor/zones.txt), ni le poste.
        "write-guard.sh": "REFUSÉ : zone de contrôle, refusée à l'écriture.",
        "refs-guard.sh": "REFUSÉ : écriture dans un dépôt de référence, en lecture seule.",
        "scope-guard.sh": "REFUSÉ : fichier hors du périmètre du contrat.",
      }
      for (const [script, defaut] of Object.entries(refus)) {
        const verdict = garde(racine, script, entree)
        if (verdict.refuse) throw new Error(verdict.message || defaut)
      }
    },

    // Consigne opérateur en cours de route : STEER.md, relu à chaque requête.
    "experimental.chat.system.transform": async (_entree, sortie) => {
      const consigne = garde(racine, "steer.sh")
      if (!consigne.refuse && consigne.message) sortie.system.push(consigne.message)
    },
  }
}
