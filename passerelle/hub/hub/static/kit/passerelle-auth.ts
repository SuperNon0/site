/**
 * passerelle-auth.ts : brancher une application Node / TypeScript derrière la passerelle (Authentik).
 *
 * Copie ce fichier tel quel dans ton projet (aucune dépendance, Node 18+).
 * Mêmes règles que passerelle_auth.py :
 *
 *  1. PERSONNE dans un navigateur (https://<site>.super-nono.cc) : Authentik ajoute X-Authentik-*.
 *     On ne les croit que si X-Passerelle-Cle est le bon.          → lireUtilisateur()
 *  2. MACHINE avec un jeton (adresse locale IP:port, ou chemin public /api/..., /mcp).
 *     Authentik ne vérifie rien sur ces chemins : c'est l'app.      → lireMachine()
 *  3. WEBHOOK signé (BotPanel…) sur un chemin public.               → signatureValide()
 *  4. CLAUDE par un connecteur MCP : connexion OAuth avec le compte Authentik. Dans Claude, on colle
 *     https://<site>.super-nono.cc/mcp puis « Se connecter ».        → routes mcp* + mcpVerifier()
 *
 * Variables d'environnement : PASSERELLE_AUTH_URL (défaut https://auth.super-nono.cc),
 * PASSERELLE_URL_SITE (ex. https://recettes.super-nono.cc, sinon déduite de la requête), PASSERELLE_CLE, PASSERELLE_GROUPE_ADMINS, SERVICE_JETONS
 * ("nom:jeton,nom2:jeton2"), PASSERELLE_DEV_EMAIL / PASSERELLE_DEV_ADMIN (dev seulement,
 * ignorés dès que PASSERELLE_CLE est rempli).
 */
import { createHash, createHmac, timingSafeEqual } from "node:crypto";

export const LIEN_DECONNEXION = "/outpost.goauthentik.io/sign_out";

export interface Utilisateur {
  email: string;
  nom: string;
  username: string;
  uid: string;
  groupes: string[];
  admin: boolean;
}

/** Headers du fetch standard (Next.js, Remix, Hono…) ou req.headers de Node / Express. */
type Entetes = Headers | Record<string, string | string[] | undefined>;

function lire(entetes: Entetes, nom: string): string {
  let v: string | string[] | null | undefined;
  if (typeof (entetes as Headers).get === "function") v = (entetes as Headers).get(nom);
  else v = (entetes as Record<string, string | string[] | undefined>)[nom.toLowerCase()];
  if (Array.isArray(v)) v = v[0];
  const brut = (v ?? "").trim();
  // Les en-têtes arrivent en latin-1 : on les relit en UTF-8 (« Noë », pas « NoÃ« »).
  try {
    if ([...brut].every((c) => c.charCodeAt(0) < 256)) {
      return new TextDecoder("utf-8", { fatal: true }).decode(Uint8Array.from(brut, (c) => c.charCodeAt(0)));
    }
  } catch { /* pas de l'UTF-8 : on garde tel quel */ }
  return brut;
}

function egal(a: string, b: string): boolean {
  if (!a || !b) return false;
  const x = Buffer.from(a), y = Buffer.from(b);
  return x.length === y.length && timingSafeEqual(x, y);
}

/** La personne connectée par Authentik, ou null (requête anonyme, directe ou forgée). */
export function lireUtilisateur(entetes: Entetes): Utilisateur | null {
  const cle = (process.env.PASSERELLE_CLE ?? "").trim();
  const groupeAdmins = process.env.PASSERELLE_GROUPE_ADMINS ?? "authentik Admins";
  if (!cle) {
    const dev = (process.env.PASSERELLE_DEV_EMAIL ?? "").trim();
    if (!dev) return null;
    const admin = process.env.PASSERELLE_DEV_ADMIN === "1";
    return { email: dev, nom: "Dev", username: dev, uid: "", groupes: [admin ? groupeAdmins : "Membres"], admin };
  }
  if (!egal(lire(entetes, "X-Passerelle-Cle"), cle)) return null;
  const email = lire(entetes, "X-Authentik-Email").toLowerCase();
  const username = lire(entetes, "X-Authentik-Username");
  if (!email && !username) return null;
  const groupes = lire(entetes, "X-Authentik-Groups").split("|").filter(Boolean);
  return {
    email: email || username,
    nom: lire(entetes, "X-Authentik-Name"),
    username,
    uid: lire(entetes, "X-Authentik-Uid"),
    groupes,
    admin: groupes.includes(groupeAdmins),
  };
}

function jetons(): Array<[string, string]> {
  return (process.env.SERVICE_JETONS ?? "")
    .split(",").map((m) => m.trim()).filter(Boolean)
    .map((m): [string, string] => (m.includes(":") ? [m.slice(0, m.indexOf(":")), m.slice(m.indexOf(":") + 1)] : ["machine", m]))
    .filter(([, j]) => j.length >= 24);
}

/**
 * Le nom de la machine dont le jeton est valide, ou null.
 * Jeton lu dans « Authorization: Bearer … » (à préférer), « X-Api-Key », ou ?cle=… dans l'adresse
 * (seulement pour un outil qui ne sait pas envoyer d'en-tête ; Claude, lui, utilise la connexion OAuth).
 */
export function lireMachine(entetes: Entetes, url?: string | URL): string | null {
  const auth = lire(entetes, "Authorization");
  let fourni = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  fourni ||= lire(entetes, "X-Api-Key");
  if (!fourni && url) fourni = new URL(url, "http://x").searchParams.get("cle") ?? "";
  if (!fourni) return null;
  let trouve: string | null = null;
  for (const [nom, jeton] of jetons()) if (egal(fourni, jeton)) trouve = nom;
  return trouve;
}

/** Signature HMAC-SHA256 au format BotPanel (en-têtes X-BotPanel-Timestamp / X-BotPanel-Signature). */
export function signatureValide(corps: string | Buffer, horodatage: string, signature: string, secret: string, tolerance = 300): boolean {
  if (!secret || !horodatage || !signature) return false;
  const ts = Number.parseInt(horodatage, 10);
  if (!Number.isFinite(ts) || Math.abs(Date.now() / 1000 - ts) > tolerance) return false;
  const attendu = "sha256=" + createHmac("sha256", secret).update(`${horodatage}.`).update(corps).digest("hex");
  return egal(attendu, signature.trim());
}

/* ------------------------------------------------------------------ 4. MCP (OAuth avec Authentik)
 * Claude découvre tout seul comment se connecter :
 *   /mcp répond 401 + WWW-Authenticate → /.well-known/oauth-protected-resource (ce site)
 *   → /.well-known/oauth-authorization-server (ce site, qui renvoie vers Authentik)
 *   → /oauth/register (identifiant du client public « claude-mcp » créé par la passerelle)
 *   → connexion Authentik → jeton → /mcp avec « Authorization: Bearer <jeton> ».
 * Chemins publics à déclarer dans le hub : /mcp, /.well-known, /oauth.
 */
export const CLIENT_MCP = "claude-mcp";
const SLUG_MCP = "mcp";
export const SCOPES_MCP = ["openid", "email", "profile", "offline_access"];
const RETOURS_MCP = [
  /^https:\/\/claude\.ai\/api\/mcp\/auth_callback$/,
  /^https:\/\/claude\.com\/api\/mcp\/auth_callback$/,
  /^http:\/\/localhost:\d+\/callback$/,
  /^http:\/\/127\.0\.0\.1:\d+\/callback$/,
];
const authUrl = () => (process.env.PASSERELLE_AUTH_URL ?? "https://auth.super-nono.cc").replace(/\/+$/, "");

/** https://recettes.super-nono.cc : PASSERELLE_URL_SITE, sinon déduit de la requête. */
export function baseSite(entetes: Entetes): string {
  const fixe = (process.env.PASSERELLE_URL_SITE ?? "").trim().replace(/\/+$/, "");
  if (fixe) return fixe;
  const hote = (lire(entetes, "X-Forwarded-Host") || lire(entetes, "Host")).split(",")[0].trim();
  const proto = (lire(entetes, "X-Forwarded-Proto") || "https").split(",")[0].trim();
  return `${proto}://${hote}`;
}

/** GET /.well-known/oauth-protected-resource (et …/mcp). */
export function mcpRessource(base: string, cheminMcp = "/mcp") {
  return { resource: base + cheminMcp, authorization_servers: [base], scopes_supported: SCOPES_MCP, bearer_methods_supported: ["header"] };
}

/** GET /.well-known/oauth-authorization-server (et /.well-known/openid-configuration). */
export function mcpServeur(base: string) {
  const a = authUrl() + "/application/o";
  return {
    issuer: base,
    authorization_endpoint: `${a}/authorize/`,
    token_endpoint: `${a}/token/`,
    revocation_endpoint: `${a}/revoke/`,
    userinfo_endpoint: `${a}/userinfo/`,
    jwks_uri: `${a}/${SLUG_MCP}/jwks/`,
    registration_endpoint: base + "/oauth/register",
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code", "refresh_token"],
    code_challenge_methods_supported: ["S256"],
    token_endpoint_auth_methods_supported: ["none"],
    scopes_supported: SCOPES_MCP,
  };
}

/** POST /oauth/register : tout client reçoit le client public « claude-mcp » (Authentik n'accepte que les retours de Claude). */
export function mcpEnregistrer(demande: { redirect_uris?: unknown; client_name?: unknown }): { corps: object; statut: number } {
  const retours = Array.isArray(demande?.redirect_uris) ? demande.redirect_uris.map(String) : [];
  if (!retours.length || !retours.every((u) => RETOURS_MCP.some((r) => r.test(u)))) {
    return { statut: 400, corps: { error: "invalid_redirect_uri", error_description: "Seuls Claude (claude.ai) et Claude Code (localhost) sont acceptés." } };
  }
  return {
    statut: 201,
    corps: {
      client_id: CLIENT_MCP, client_id_issued_at: Math.floor(Date.now() / 1000),
      client_name: typeof demande.client_name === "string" ? demande.client_name : "Claude",
      redirect_uris: retours, grant_types: ["authorization_code", "refresh_token"], response_types: ["code"],
      token_endpoint_auth_method: "none", scope: SCOPES_MCP.join(" "),
    },
  };
}

const cacheMcp = new Map<string, { expire: number; u: Utilisateur | null }>();

/** « Bearer <jeton> » d'un client MCP → la personne, ou null. Vérifié auprès d'Authentik, gardé 5 minutes. */
export async function mcpVerifier(autorisation: string | null | undefined): Promise<Utilisateur | null> {
  const a = autorisation ?? "";
  if (!a.toLowerCase().startsWith("bearer ")) return null;
  const jeton = a.slice(7).trim();
  let charge: { aud?: string | string[]; azp?: string; exp?: number } = {};
  try { charge = JSON.parse(Buffer.from(jeton.split(".")[1] ?? "", "base64url").toString("utf8")); } catch { return null; }
  const auds = Array.isArray(charge.aud) ? charge.aud : [charge.aud];
  if (!auds.includes(CLIENT_MCP) && charge.azp !== CLIENT_MCP) return null;
  const cle = createHash("sha256").update(jeton).digest("hex");
  const maintenant = Date.now() / 1000;
  const trouve = cacheMcp.get(cle);
  if (trouve && trouve.expire > maintenant) return trouve.u;
  let u: Utilisateur | null = null;
  try {
    const r = await fetch(authUrl() + "/application/o/userinfo/", { headers: { authorization: `Bearer ${jeton}`, accept: "application/json" }, signal: AbortSignal.timeout(8000) });
    if (r.ok) {
      const info = await r.json() as { email?: string; name?: string; preferred_username?: string; sub?: string; groups?: string[] };
      const groupes = info.groups ?? [];
      if (info.email) u = { email: info.email.toLowerCase(), nom: info.name ?? "", username: info.preferred_username ?? "", uid: info.sub ?? "", groupes, admin: groupes.includes(process.env.PASSERELLE_GROUPE_ADMINS ?? "authentik Admins") };
    } else if (r.status >= 500) return null;
  } catch { return null; } // Authentik injoignable : rien en cache
  if (cacheMcp.size > 500) cacheMcp.clear();
  cacheMcp.set(cle, { expire: Math.min(maintenant + 300, charge.exp ?? maintenant + 300), u });
  return u;
}

/** Valeur de WWW-Authenticate à renvoyer avec un 401 sur /mcp : c'est ce qui lance la connexion dans Claude. */
export function mcpEnteteRefus(base: string): string {
  return `Bearer resource_metadata="${base}/.well-known/oauth-protected-resource", scope="${SCOPES_MCP.join(" ")}"`;
}

/* ------------------------------------------------------------------ exemples d'usage
 *
 * Next.js (route handler) :
 *   export async function GET(req: Request) {
 *     const u = lireUtilisateur(req.headers);
 *     if (!u) return Response.json({ erreur: "Connexion requise." }, { status: 401 });
 *     return Response.json({ bonjour: u.nom });
 *   }
 *
 * Express :
 *   app.use("/api/prive", (req, res, next) => {
 *     const u = lireUtilisateur(req.headers);
 *     if (!u && !lireMachine(req.headers, req.originalUrl)) return res.status(401).json({ erreur: "Connexion ou jeton requis." });
 *     res.locals.utilisateur = u; next();
 *   });
 *
 * MCP avec Express (connexion OAuth native dans Claude) :
 *   app.get(["/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/*"],
 *     (req, res) => res.json(mcpRessource(baseSite(req.headers))));
 *   app.get(["/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"],
 *     (req, res) => res.json(mcpServeur(baseSite(req.headers))));
 *   app.post("/oauth/register", express.json(), (req, res) => { const r = mcpEnregistrer(req.body); res.status(r.statut).json(r.corps); });
 *   app.use("/mcp", async (req, res, next) => {
 *     const u = await mcpVerifier(req.headers.authorization);
 *     if (!u && !lireMachine(req.headers, req.originalUrl)) {
 *       return res.status(401).set("WWW-Authenticate", mcpEnteteRefus(baseSite(req.headers))).json({ error: "invalid_token" });
 *     }
 *     res.locals.utilisateur = u; next();   // puis le transport MCP (StreamableHTTPServerTransport…)
 *   });
 */
