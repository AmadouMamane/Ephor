// Garde-fous de session pour opencode — bouton d'arrêt, anti-secret, consigne opérateur,
// sentinelle et points de reprise, alerte de contexte ; et le plafond de réponse d'opencode,
// relevé.
//
// Rien n'est réimplémenté ici : la logique vit dans .ephor/hooks/*.sh, déjà couverte par
// `make ephor-test`. Ce greffon ne fait que traduire les appels d'opencode vers ces
// scripts, dont l'entrée est du JSON au format de Claude Code. Une seule logique, trois
// outils. Les garanties, elles, ne sont pas ici : elles sont dans le juge et la CI.
// Seule exception, le plafond de réponse et sa coupure : c'est un défaut d'opencode, il se
// règle chez lui.
//
// Dépendances : aucune. Node et Bun exposent tous deux ces modules.
import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

// Outils d'opencode qui écrivent dans un fichier, et l'argument qui porte le contenu.
const ECRITURE = new Set(["edit", "write", "patch", "apply_patch", "multiedit"])

// opencode plafonne chaque réponse à 32 000 tokens, réflexion comprise, quel que soit le
// modèle (anomalyco/opencode#29363). Au plus haut niveau d'effort, la réflexion le remplit
// et la réponse sort vide, sans erreur : quatre échecs du planner sur quatre, à 32 000 tokens
// de réflexion et 0 de réponse, de 5 à 17 minutes chacun (mesuré le 28/09/2026). Relevé à
// 128 000, il a répondu en deux minutes, sans erreur. On relève donc jusqu'à la limite du
// modèle, sans dépasser le quart de sa mémoire : une réponse ne doit pas pouvoir la remplir.
const PLAFOND_OPENCODE = 32000
const PART_DU_CONTEXTE = 4

function plafondReponse(modele, actuel) {
  // Le poste a fixé le sien : c'est lui qui décide.
  if (process.env.OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX) return undefined
  // Un plafond plus court est voulu (titre, résumé) : on n'y touche pas.
  if (typeof actuel === "number" && actuel < PLAFOND_OPENCODE) return undefined
  const limite = modele?.limit?.output
  if (!(limite > 0)) return undefined
  const contexte = modele?.limit?.context
  const cible = contexte > 0 ? Math.min(limite, Math.floor(contexte / PART_DU_CONTEXTE)) : limite
  return cible > (actuel ?? 0) ? cible : undefined
}

function coupure(info) {
  const tokens = info?.tokens ?? {}
  return (
    `Réponse coupée au plafond : ${tokens.reasoning ?? 0} tokens de réflexion, ` +
    `${tokens.output ?? 0} de réponse. Rien n'a été rendu, ou seulement un début. ` +
    "Relance ; si ça se répète, baisse l'effort (.ephor/docs/11-trois-agents.md)."
  )
}

// Ce que la dernière réponse a en mémoire : c'est le calcul du pourcentage affiché par opencode.
function occupation(info) {
  const tokens = info?.tokens ?? {}
  const cache = tokens.cache ?? {}
  return (
    (tokens.input ?? 0) + (tokens.output ?? 0) + (tokens.reasoning ?? 0) + (cache.read ?? 0) + (cache.write ?? 0)
  )
}

// L'opérateur ne lit pas la conversation de chaque sous-agent : on le lui dit à l'écran.
async function annonce(client, message, variante) {
  if (!client?.tui?.showToast) return
  try {
    await client.tui.showToast({ body: { title: "Ephor", message, variant: variante, duration: 15000 } })
  } catch {
    // Sans interface (`opencode run`), personne à prévenir.
  }
}

async function estSousAgent(client, id, connus) {
  if (connus.has(id)) return connus.get(id)
  let sous = false
  try {
    const reponse = await client.session.get({ path: { id } })
    sous = Boolean((reponse?.data ?? reponse)?.parentID)
  } catch {
    // Session introuvable : traitée comme principale, l'alerte de trop est sans danger.
  }
  connus.set(id, sous)
  return sous
}

async function derniereReponse(client, id) {
  try {
    const reponse = await client.session.messages({ path: { id } })
    const messages = reponse?.data ?? reponse
    if (!Array.isArray(messages)) return undefined
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.info?.role === "assistant") return messages[i].info
    }
  } catch {
    // Illisible : on ne dit rien plutôt que d'inventer.
  }
  return undefined
}

function garde(racine, script, entree, args = [], delai = 15000) {
  const resultat = spawnSync(join(racine, ".ephor", "hooks", script), args, {
    input: entree === undefined ? undefined : JSON.stringify(entree),
    encoding: "utf8",
    timeout: delai,
    env: { ...process.env, CLAUDE_PROJECT_DIR: racine },
  })
  if (resultat.error) return { refuse: true, message: `garde-fou injoignable : ${resultat.error.message}` }
  if (resultat.status !== 0) return { refuse: true, message: (resultat.stderr || "").trim() }
  return { refuse: false, message: (resultat.stdout || "").trim() }
}

export const ephor = async ({ worktree, directory, client }) => {
  const racine = worktree ?? directory
  const tailles = new Map() // session -> mémoire du modèle, en tokens
  const occupes = new Map() // session -> tokens occupés après la dernière réponse
  const sousAgents = new Map() // session -> est-ce un sous-agent ?
  const signales = new Set() // réponses coupées déjà annoncées
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
      // Les quatre gardes d'une écriture, lancées en même temps par le moteur, avec le verdict
      // qu'elles rendraient l'une après l'autre : deux fois moins d'attente à chaque écriture
      // (voir .ephor/hooks/ensemble.sh ; EPHOR_GARDES_EN_SERIE=1 remet la série).
      const gardes = ["block-secrets.sh", "write-guard.sh", "refs-guard.sh", "scope-guard.sh"]
      if (existsSync(join(racine, ".ephor", "hooks", "ensemble.sh"))) {
        const verdict = garde(racine, "ensemble.sh", entree, gardes, 30000)
        if (verdict.refuse) throw new Error(verdict.message || "REFUSÉ par un garde-fou d'Ephor.")
        return
      }
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
    // `consigne` : la consigne seule, sans reprendre la photo de la sentinelle. Relue à
    // chaque requête, la photo aurait effacé ce que les appels d'outils du tour avaient changé.
    "experimental.chat.system.transform": async (_entree, sortie) => {
      const consigne = garde(racine, "steer.sh", undefined, ["consigne"])
      if (!consigne.refuse && consigne.message) sortie.system.push(consigne.message)
    },

    // Début du tour : la sentinelle photographie les zones de contrôle. opencode n'avait pas
    // de sentinelle : un script qui écrivait dans tasks/ n'était vu par personne, et l'agent
    // pouvait commiter le contrat qu'il s'était écrit (constaté le 29/09/2026).
    // Il dit aussi quand une autre session d'agent travaille dans ce dossier (sessions.sh).
    "chat.message": async (entree) => {
      if (!entree?.sessionID) return
      const photo = garde(racine, "steer.sh", { session_id: entree.sessionID }, ["photo"])
      if (photo.message) await annonce(client, photo.message, photo.refuse ? "error" : "warning")
    },

    // Chaque requête : le plafond de réponse relevé, et la mémoire du modèle notée pour
    // l'alerte de contexte (opencode ne la donne qu'ici).
    "chat.params": async (entree, sortie) => {
      const contexte = entree?.model?.limit?.context
      if (entree?.sessionID && contexte > 0) tailles.set(entree.sessionID, contexte)
      const plafond = plafondReponse(entree?.model, sortie.maxOutputTokens)
      if (plafond !== undefined) sortie.maxOutputTokens = plafond
    },

    event: async ({ event }) => {
      if (event?.type === "message.updated") {
        const info = event.properties?.info
        if (info?.role !== "assistant" || !info.time?.completed) return
        occupes.set(info.sessionID, occupation(info))
        // Coupée au plafond : opencode finit le tour comme si tout allait bien.
        if (info.finish === "length" && !signales.has(info.id)) {
          signales.add(info.id)
          await annonce(client, coupure(info), "error")
        }
        return
      }
      // Fin de tour : la règle des 60 %, que l'agent ne peut pas voir. Le seuil et le
      // message vivent dans le hook, le même que sous Claude Code.
      if (event?.type === "session.idle") {
        const id = event.properties?.sessionID
        if (!id) return
        // Fin du tour : la sentinelle compare à sa photo (une zone changée pose AGENT_STOP),
        // puis le point de reprise, comme sous Claude Code et Copilot.
        const fin = garde(racine, "checkpoint.sh", { session_id: id }, [], 30000)
        if (fin.refuse && fin.message) await annonce(client, fin.message, "error")
        const occupe = occupes.get(id)
        const taille = tailles.get(id)
        if (!occupe || !taille || (await estSousAgent(client, id, sousAgents))) return
        const verdict = garde(racine, "contexte.sh", { contexte: { occupe, taille } })
        if (verdict.refuse || !verdict.message) return
        try {
          const message = JSON.parse(verdict.message).systemMessage
          if (message) await annonce(client, message, "warning")
        } catch {
          // Sortie illisible : pas d'alerte plutôt qu'une alerte fausse.
        }
      }
    },

    // Un sous-agent coupé rend du vide, et l'agent qui l'a appelé en tirait des conclusions
    // fausses pendant une demi-heure (« le planner n'est fiable que sur les demandes
    // triviales »). Il reçoit maintenant la raison avec la réponse.
    "tool.execute.after": async (entree, sortie) => {
      if (entree?.tool !== "task" || !client) return
      const enfant = sortie?.metadata?.sessionId
      if (!enfant) return
      const info = await derniereReponse(client, enfant)
      if (info?.finish !== "length") return
      sortie.output =
        `ATTENTION : ${coupure(info)} N'en conclus rien sur le sous-agent : dis-le à l'opérateur.\n\n` +
        (sortie.output ?? "")
    },
  }
}
