// Garde-fous de session pour opencode — bouton d'arrêt, anti-secret, consigne opérateur,
// sentinelle et points de reprise, alerte de contexte ; et le plafond de réponse d'opencode,
// relevé.
//
// Rien n'est réimplémenté ici : la logique vit dans .ephor/hooks/*.sh, déjà couverte par
// `make ephor-test`. Ce greffon ne fait que traduire les appels d'opencode vers les quatre
// points d'entrée de ces scripts (ADR 0011), dont l'entrée est du JSON au format de Claude
// Code. Quelles gardes tournent se décide là-bas, pas ici. Une seule logique, trois outils.
// Les garanties, elles, ne sont pas ici : elles sont dans le juge et la CI. Seule exception,
// le plafond de réponse et sa coupure : c'est un défaut d'opencode, il se règle chez lui.
//
// Il vit dans le moteur, que la mise à jour remplace : le fichier que lit opencode,
// .opencode/plugins/ephor.js, ne fait que l'importer, et ne change plus d'une version à
// l'autre.
//
// Dépendances : aucune. Node et Bun exposent tous deux ces modules.
import { spawnSync } from "node:child_process"
import { existsSync } from "node:fs"
import { join } from "node:path"

// Outils d'opencode qui écrivent dans un fichier, et l'argument qui porte le contenu.
const ECRITURE = new Set(["edit", "write", "patch", "apply_patch", "multiedit"])
// Outils d'opencode qui n'écrivent rien et ne lancent rien : aucune garde ne les regarde sous
// opencode (ses réglages refusent déjà la lecture d'un secret), seul le bouton d'arrêt. Ils
// sont les plus fréquents : ne pas lancer de processus pour eux, c'est ne pas bloquer opencode
// à chaque lecture. Tout autre outil, ceux d'un serveur MCP compris, passe les gardes.
const SANS_EFFET = new Set([
  "read", "glob", "grep", "list", "todoread", "todowrite", "webfetch", "websearch",
  "codesearch", "skill", "lsp", "question", "task",
])

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

// La place de la réponse quand la réflexion a un budget (modèles Claude servis par Copilot).
// En effort max, opencode donne à la réflexion le budget maximal que Copilot annonce, moins un
// token ; sa bibliothèque envoie « réponse + réflexion », ramené à la limite du modèle quand
// elle la connaît. Quand la limite vaut ce budget, la réponse n'a plus qu'un token : elle sort
// vide, sans erreur (lu dans le code d'opencode 1.18.33 ; plateaux de 32 000 et 63 999 tokens
// vus en session réelle, #27). La réflexion garde donc tout, sauf cette place pour la réponse,
// et les deux ensemble ne dépassent jamais la limite du modèle. L'effort demandé ne change pas.
// Mesuré avec un faux serveur au format d'Anthropic (30/09/2026) : avant, 64 000 tokens
// envoyés dont 63 999 de réflexion ; après, 64 000 dont 48 000.
const PLACE_DE_LA_REPONSE = 16000

function placePourLaReponse(modele, sortie) {
  const pensee = sortie?.options?.thinking
  const limite = modele?.limit?.output
  if (pensee?.type !== "enabled" || typeof pensee.budgetTokens !== "number" || !(limite > 0)) return
  const place = Math.min(PLACE_DE_LA_REPONSE, Math.floor(limite / 2))
  if (pensee.budgetTokens > limite - place) pensee.budgetTokens = limite - place
  // Le poste a fixé le plafond de réponse : c'est lui qui décide de celui-là.
  if (process.env.OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX) return
  if (typeof sortie.maxOutputTokens === "number" && sortie.maxOutputTokens + pensee.budgetTokens > limite) {
    sortie.maxOutputTokens = Math.max(place, limite - pensee.budgetTokens)
  }
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

// Un point d'entrée des garde-fous (ADR 0011). EPHOR_OUTIL lui dit qui l'appelle : ce qui
// change d'un outil à l'autre se décide là-bas.
function garde(racine, script, entree, args = [], delai = 15000) {
  const resultat = spawnSync(join(racine, ".ephor", "hooks", script), args, {
    input: entree === undefined ? undefined : JSON.stringify(entree),
    encoding: "utf8",
    timeout: delai,
    env: { ...process.env, CLAUDE_PROJECT_DIR: racine, EPHOR_OUTIL: "opencode" },
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
      // Une lecture, sans arrêt demandé : rien à juger.
      if (SANS_EFFET.has(input.tool) && !existsSync(join(racine, "AGENT_STOP"))) return
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
      // Le point d'entrée choisit les gardes de cet appel, bouton d'arrêt compris (il laisse
      // passer l'écriture de PROGRESS.md, pour que l'agent note son état avant de s'arrêter),
      // et les lance ensemble (voir .ephor/hooks/avant-outil.sh).
      const verdict = garde(racine, "avant-outil.sh", entree, [], 30000)
      if (verdict.refuse) throw new Error(verdict.message || "REFUSÉ par un garde-fou d'Ephor.")
    },

    // Consigne opérateur en cours de route : STEER.md, relu à chaque requête.
    // `consigne` : la consigne seule, sans reprendre la photo de la sentinelle. Relue à
    // chaque requête, la photo aurait effacé ce que les appels d'outils du tour avaient changé.
    "experimental.chat.system.transform": async (_entree, sortie) => {
      const consigne = garde(racine, "debut-tour.sh", undefined, ["consigne"])
      if (!consigne.refuse && consigne.message) sortie.system.push(consigne.message)
    },

    // Début du tour : la sentinelle photographie les zones de contrôle. opencode n'avait pas
    // de sentinelle : un script qui écrivait dans tasks/ n'était vu par personne, et l'agent
    // pouvait commiter le contrat qu'il s'était écrit (constaté le 29/09/2026).
    // Il dit aussi quand une autre session d'agent travaille dans ce dossier (sessions.sh).
    "chat.message": async (entree) => {
      if (!entree?.sessionID) return
      const photo = garde(racine, "debut-tour.sh", { session_id: entree.sessionID }, ["photo"])
      if (photo.message) await annonce(client, photo.message, photo.refuse ? "error" : "warning")
    },

    // Chaque requête : le plafond de réponse relevé, et la mémoire du modèle notée pour
    // l'alerte de contexte (opencode ne la donne qu'ici).
    "chat.params": async (entree, sortie) => {
      const contexte = entree?.model?.limit?.context
      if (entree?.sessionID && contexte > 0) tailles.set(entree.sessionID, contexte)
      const plafond = plafondReponse(entree?.model, sortie.maxOutputTokens)
      if (plafond !== undefined) sortie.maxOutputTokens = plafond
      placePourLaReponse(entree?.model, sortie)
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
      // Fin de tour : la sentinelle compare à sa photo (une zone changée pose AGENT_STOP), le
      // point de reprise, et la règle des 60 %, que l'agent ne peut pas voir, comme sous
      // Claude Code et Copilot. Le seuil et les messages vivent dans les hooks.
      if (event?.type === "session.idle") {
        const id = event.properties?.sessionID
        if (!id) return
        const charge = { session_id: id }
        // Un sous-agent ne tient pas PROGRESS.md, et son remplissage n'est pas celui de la
        // session : les deux ne regardent que la session principale.
        const sous = await estSousAgent(client, id, sousAgents)
        if (sous) charge.sous_agent = true
        const occupe = occupes.get(id)
        const taille = tailles.get(id)
        if (occupe && taille && !sous) charge.contexte = { occupe, taille }
        const fin = garde(racine, "fin-tour.sh", charge, [], 40000)
        // Un refus (la sentinelle) : son message, avec l'alerte de contexte redite en clair.
        if (fin.refuse) {
          if (fin.message) await annonce(client, fin.message, "error")
          return
        }
        for (const ligne of fin.message.split("\n")) {
          try {
            const message = JSON.parse(ligne).systemMessage
            if (message) await annonce(client, message, "warning")
          } catch {
            // Une ligne illisible : pas d'alerte plutôt qu'une alerte fausse.
          }
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
