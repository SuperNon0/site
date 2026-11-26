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
 *  4. CLAUDE par un connecteur MCP : « Se connecter » dans Claude, puis « Autoriser ? » sur le site.
 *     Le site est son propre guichet OAuth (modèle RecipeLog).       → guichetMcp() + accesMcp()
 *
 * Variables d'environnement : PASSERELLE_URL_SITE (ex. https://recettes.super-nono.cc), PASSERELLE_CLE,
 * PASSERELLE_MCP_SECRET, PASSERELLE_MCP_POUR, PASSERELLE_MCP_FICHIER (voir partie 4), PASSERELLE_GROUPE_ADMINS, SERVICE_JETONS
 * ("nom:jeton,nom2:jeton2"), PASSERELLE_DEV_EMAIL / PASSERELLE_DEV_ADMIN (dev seulement,
 * ignorés dès que PASSERELLE_CLE est rempli).
 */
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";

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

/* ------------------------------------------------------------------ 4. MCP : le guichet OAuth du site
 * Modèle RecipeLog (docs/mcp-oauth.md) : Authentik ne sait pas inscrire Claude tout seul (ni DCR ni CIMD),
 * donc le site est lui-même le serveur d'autorisation de son MCP. Aucune application en plus dans
 * Authentik, rien en base : clients, codes et jetons sont signés (HMAC) avec une clé propre au site.
 * Seul un compteur « génération » (petit fichier JSON) permet de tout déconnecter.
 *
 *   /api/mcp                                         401 + WWW-Authenticate sans jeton    public, exact
 *   /.well-known/oauth-protected-resource[/api/mcp]  fiche de la ressource                public
 *   /.well-known/oauth-authorization-server          fiche du serveur de connexion        public
 *   /api/mcp/oauth/inscription                       inscription automatique (DCR)        public
 *   /api/mcp/oauth/autoriser                         page « Autoriser Claude ? »          DERRIÈRE AUTHENTIK
 *   /api/mcp/oauth/jeton                             code ou renouvellement → jeton       public
 *
 * Tout est en Request/Response standard : Next.js (route handlers), Hono, Remix… ; avec Express,
 * voir l'adaptateur en bas de fichier. Variables : PASSERELLE_URL_SITE, PASSERELLE_MCP_SECRET
 * (32 caractères ou plus ; à défaut clé tirée de PASSERELLE_CLE et de l'adresse du site),
 * PASSERELLE_MCP_POUR (admins par défaut | tous | nom d'un groupe), PASSERELLE_MCP_FICHIER.
 */
export const CHEMIN_MCP = "/api/mcp";
const DUREE_CODE = 5 * 60, DUREE_ACCES = 3600, DUREE_RENOUV = 60 * 86400;
const LOCALES = ["localhost", "127.0.0.1", "[::1]"];
export const CORS: Record<string, string> = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Authorization, Content-Type, MCP-Protocol-Version",
};
/** Règle facultative de l'application, vérifiée à chaque appel (ex. compte encore actif dans ta base). */
export let regleMcp: ((email: string) => boolean | Promise<boolean>) | null = null;
export function definirRegleMcp(f: typeof regleMcp) { regleMcp = f; }

const maintenant = () => Math.floor(Date.now() / 1000);
const json = (corps: unknown, statut = 200) =>
  Response.json(corps, { status: statut, headers: { "Cache-Control": "no-store", ...CORS } });
export const preflight = () => new Response(null, { status: 204, headers: CORS });

/** https://recettes.super-nono.cc : PASSERELLE_URL_SITE, sinon déduit de la requête (à éviter en vrai). */
export function baseSite(entetes: Entetes): string {
  const fixe = (process.env.PASSERELLE_URL_SITE ?? "").trim().replace(/\/+$/, "");
  if (fixe) return fixe;
  const hote = (lire(entetes, "X-Forwarded-Host") || lire(entetes, "Host")).split(",")[0].trim();
  const proto = (lire(entetes, "X-Forwarded-Proto") || "https").split(",")[0].trim();
  return `${proto}://${hote}`;
}

function cleOauth(base: string): Buffer {
  const secret = (process.env.PASSERELLE_MCP_SECRET ?? "").trim() || (process.env.PASSERELLE_CLE ?? "").trim();
  if (secret.length < 32) throw new Error("PASSERELLE_MCP_SECRET (32 caractères ou plus) manque dans le .env du site.");
  // L'adresse du site entre dans la clé : un jeton d'un site ne vaut rien sur un autre.
  return createHmac("sha256", secret).update(`mcp-oauth-v1|${base}`).digest();
}
const mac = (base: string, morceau: string) => createHmac("sha256", cleOauth(base)).update(morceau).digest("base64url");

/** Valeur signée : préfixe + base64url(JSON) + "." + signature. */
export function sceller(base: string, prefixe: string, charge: Record<string, unknown>): string {
  const morceau = Buffer.from(JSON.stringify(charge)).toString("base64url");
  return `${prefixe}${morceau}.${mac(base, morceau)}`;
}

/** Relit une valeur signée : bonne signature, bon genre, pas expirée. Sinon null. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function desceller(base: string, brut: string | null | undefined, prefixe: string, genre: string): any | null {
  if (!brut || !brut.startsWith(prefixe) || brut.length > 6000) return null;
  const morceaux = brut.slice(prefixe.length).split(".");
  if (morceaux.length !== 2 || !morceaux[0] || !morceaux[1]) return null;
  if (!egal(mac(base, morceaux[0]), morceaux[1])) return null;
  try {
    const v = JSON.parse(Buffer.from(morceaux[0], "base64url").toString("utf8"));
    return v?.t === genre && typeof v.exp === "number" && v.exp > maintenant() ? v : null;
  } catch { return null; }
}

export function urlsMcp(base: string) {
  return {
    resource: base + CHEMIN_MCP,
    resourceMetadata: `${base}/.well-known/oauth-protected-resource${CHEMIN_MCP}`,
    authorize: `${base}${CHEMIN_MCP}/oauth/autoriser`,
    token: `${base}${CHEMIN_MCP}/oauth/jeton`,
    register: `${base}${CHEMIN_MCP}/oauth/inscription`,
  };
}

/** GET /.well-known/oauth-protected-resource (et …/api/mcp). */
export function ficheRessource(base: string, nom = ""): Response {
  const u = urlsMcp(base);
  return json({ resource: u.resource, authorization_servers: [base], scopes_supported: ["mcp"], bearer_methods_supported: ["header"], resource_name: nom || new URL(base).hostname });
}

/** GET /.well-known/oauth-authorization-server. */
export function ficheServeur(base: string): Response {
  const u = urlsMcp(base);
  return json({
    issuer: base, authorization_endpoint: u.authorize, token_endpoint: u.token, registration_endpoint: u.register,
    response_types_supported: ["code"], grant_types_supported: ["authorization_code", "refresh_token"],
    code_challenge_methods_supported: ["S256"], token_endpoint_auth_methods_supported: ["none", "client_secret_post", "client_secret_basic"],
    scopes_supported: ["mcp"], client_id_metadata_document_supported: true, authorization_response_iss_parameter_supported: true,
  });
}

/** Le 401 de /api/mcp sans jeton valable : c'est lui qui fait apparaître « Se connecter » dans Claude. */
export function refusMcp(base: string, jetonPresent: boolean): Response {
  const invalide = jetonPresent ? 'error="invalid_token", ' : "";
  return new Response("Unauthorized", { status: 401, headers: {
    "WWW-Authenticate": `Bearer ${invalide}resource_metadata="${urlsMcp(base).resourceMetadata}", scope="mcp"`,
    "Cache-Control": "no-store", ...CORS } });
}

/* ----- clients : DCR (client_id signé) ou CIMD (client_id = adresse https de sa fiche) */
type Client = { id: string; nom: string; retours: string[]; methode: string };

function retourValide(brut: unknown): brut is string {
  try {
    const u = new URL(String(brut));
    if (u.hash || u.username || u.password) return false;
    return u.protocol === "https:" || (u.protocol === "http:" && LOCALES.includes(u.hostname));
  } catch { return false; }
}

/** Adresse exactement déclarée ; en http local, le port peut changer (RFC 8252). */
function retourPermis(client: Client, donne: string): boolean {
  if (client.retours.includes(donne)) return true;
  let g: URL; try { g = new URL(donne); } catch { return false; }
  if (g.protocol !== "http:" || !LOCALES.includes(g.hostname)) return false;
  return client.retours.some((r) => { const u = new URL(r); return u.protocol === "http:" && u.hostname === g.hostname && u.pathname === g.pathname; });
}

const secretClient = (base: string, clientId: string) => createHmac("sha256", cleOauth(base)).update(`secret:${clientId}`).digest("base64url");

/** POST /api/mcp/oauth/inscription (DCR, RFC 7591). Ouverte à tous : s'inscrire ne donne accès à rien. */
export async function inscrire(base: string, req: Request): Promise<Response> {
  const d = await req.json().catch(() => null) as Record<string, unknown> | null;
  const retours = Array.isArray(d?.redirect_uris) ? d.redirect_uris as unknown[] : [];
  if (!retours.length || retours.length > 10 || !retours.every(retourValide)) return json({ error: "invalid_redirect_uri" }, 400);
  const methode = String(d?.token_endpoint_auth_method ?? "client_secret_basic");
  if (!["none", "client_secret_post", "client_secret_basic"].includes(methode)) return json({ error: "invalid_client_metadata" }, 400);
  const nom = String(d?.client_name ?? new URL(retours[0] as string).hostname).slice(0, 80);
  const clientId = sceller(base, "cl_", { t: "client", n: nom, r: retours, m: methode, exp: maintenant() + 100 * 365 * 86400 });
  const sortie: Record<string, unknown> = {
    client_id: clientId, client_id_issued_at: maintenant(), client_name: nom, redirect_uris: retours,
    grant_types: ["authorization_code", "refresh_token"], response_types: ["code"], token_endpoint_auth_method: methode, scope: "mcp",
  };
  if (methode !== "none") { sortie.client_secret = secretClient(base, clientId); sortie.client_secret_expires_at = 0; }
  return json(sortie, 201);
}

const fichesCimd = new Map<string, { expire: number; fiche: Record<string, unknown> | null }>();

async function adressePrivee(hote: string): Promise<boolean> {
  const { lookup } = await import("node:dns/promises");
  const { BlockList, isIP } = await import("node:net");
  const prive = new BlockList();
  for (const [r, n] of [["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16], ["172.16.0.0", 12], ["192.168.0.0", 16], ["224.0.0.0", 3]] as const) prive.addSubnet(r, n, "ipv4");
  for (const [r, n] of [["::", 127], ["fc00::", 7], ["fe80::", 10], ["::ffff:0:0", 96]] as const) prive.addSubnet(r, n, "ipv6");
  try {
    const adresses = isIP(hote) ? [{ address: hote, family: isIP(hote) }] : await lookup(hote, { all: true });
    return !adresses.length || adresses.some((a) => prive.check(a.address, a.family === 6 ? "ipv6" : "ipv4"));
  } catch { return true; }
}

/** Fiche publique d'un client CIMD. Protégée : https:443, pas d'IP privée, pas de redirection, 5 s, 20 Ko, 10 min de cache. */
export async function lireFicheCimd(adresse: string): Promise<Record<string, unknown> | null> {
  const trouve = fichesCimd.get(adresse);
  if (trouve && trouve.expire > Date.now()) return trouve.fiche;
  let fiche: Record<string, unknown> | null = null;
  try {
    const u = new URL(adresse);
    if (u.protocol === "https:" && (u.port === "" || u.port === "443") && !(await adressePrivee(u.hostname.replace(/^\[|\]$/g, "")))) {
      const r = await fetch(adresse, { redirect: "manual", signal: AbortSignal.timeout(5000), headers: { accept: "application/json" } });
      const texte = r.ok ? await r.text() : "";
      if (texte && texte.length <= 20 * 1024) { const v = JSON.parse(texte); if (v && typeof v === "object") fiche = v; }
    }
  } catch { fiche = null; }
  fichesCimd.set(adresse, { expire: Date.now() + 600_000, fiche });
  return fiche;
}

export async function trouverClient(base: string, clientId: string): Promise<Client | null> {
  if (clientId.startsWith("cl_")) {
    const v = desceller(base, clientId, "cl_", "client");
    return v ? { id: clientId, nom: v.n, retours: v.r, methode: v.m } : null;
  }
  if (!clientId.startsWith("https://")) return null;
  const f = await lireFicheCimd(clientId);
  if (!f || f.client_id !== clientId) return null;
  const retours = (Array.isArray(f.redirect_uris) ? f.redirect_uris : []).filter(retourValide);
  if (!retours.length || (f.token_endpoint_auth_method ?? "none") !== "none") return null;
  return { id: clientId, nom: String(f.client_name ?? new URL(clientId).hostname).slice(0, 80), retours, methode: "none" };
}

/* ----- génération (tout déconnecter) et droit d'utiliser le MCP */
const fichierGeneration = () => process.env.PASSERELLE_MCP_FICHIER ?? "mcp-oauth.json";

export async function generation(): Promise<number> {
  const { readFile } = await import("node:fs/promises");
  try { return Number(JSON.parse(await readFile(fichierGeneration(), "utf8")).generation) || 1; } catch { return 1; }
}

/** Bouton « Déconnecter Claude » : tous les jetons déjà donnés cessent de marcher. */
export async function toutDeconnecter(): Promise<number> {
  const { writeFile, rename } = await import("node:fs/promises");
  const n = (await generation()) + 1;
  await writeFile(fichierGeneration() + ".tmp", JSON.stringify({ generation: n }));
  await rename(fichierGeneration() + ".tmp", fichierGeneration());
  return n;
}

/** Qui peut brancher Claude : PASSERELLE_MCP_POUR = admins (défaut) | tous | <nom d'un groupe>. */
export function peutUtiliserMcp(u: Utilisateur): boolean {
  const pour = (process.env.PASSERELLE_MCP_POUR ?? "admins").trim();
  return pour === "tous" ? true : pour === "admins" ? u.admin : u.groupes.includes(pour);
}
const toujoursPermis = async (email: string) => (regleMcp ? await regleMcp(email) : true);

/* ----- étapes 4 à 6 : la page « Autoriser Claude ? » */
function versClient(retour: string, params: Record<string, string | null | undefined>): Response {
  const u = new URL(retour);
  for (const [k, v] of Object.entries(params)) if (v != null) u.searchParams.set(k, v);
  return new Response(null, { status: 303, headers: { Location: u.toString(), "Cache-Control": "no-store" } });
}

const esc = (s: string) => s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const STYLE = "body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0b0f17;color:#e7ebf3;font:16px/1.5 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;padding:24px}"
  + ".c{max-width:420px;width:100%;background:#141b28;border:1px solid #1e2737;border-radius:22px;padding:28px}h1{font:400 26px/1.2 Georgia,serif;margin:0 0 12px}p{color:#aab3c5;margin:0 0 20px}b{color:#e7ebf3}"
  + ".b{display:flex;gap:10px}button{flex:1;height:48px;border-radius:12px;font:600 15px system-ui,sans-serif;cursor:pointer;border:1px solid #2b3549;background:transparent;color:#e7ebf3}button.o{background:#a99bff;color:#120d2e;border-color:transparent}"
  + "@media (prefers-color-scheme:light){body{background:#f5f3ee;color:#1b1d22}.c{background:#fff;border-color:#e7e2d7}p{color:#454a55}b{color:#1b1d22}button{color:#1b1d22;border-color:#d5cfc1}button.o{background:#5a48d0;color:#fff}}";

function page(titre: string, corps: string, statut = 200, origineRetour = ""): Response {
  const html = `<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>${esc(titre)}</title><style>${STYLE}</style><div class="c">${corps}</div></html>`;
  return new Response(html, { status: statut, headers: {
    "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store", "X-Frame-Options": "DENY",
    // form-action DOIT inclure l'origine de Claude, sinon Chrome bloque le retour après « Autoriser ».
    "Content-Security-Policy": `frame-ancestors 'none'; form-action 'self'${origineRetour ? " " + origineRetour : ""}; default-src 'none'; style-src 'unsafe-inline'`,
  } });
}
const erreur = (texte: string, statut = 400) => page("Connexion de Claude", `<h1>Connexion impossible</h1><p>${esc(texte)}</p>`, statut);

/** GET /api/mcp/oauth/autoriser. Cette adresse reste DERRIÈRE Authentik : la personne y arrive connectée. */
export async function autoriserGet(base: string, req: Request): Promise<Response> {
  const q = new URL(req.url).searchParams;
  const client = await trouverClient(base, q.get("client_id") ?? "");
  if (!client) return erreur("Application inconnue. Relance la connexion depuis Claude.");
  const retour = q.get("redirect_uri") ?? (client.retours.length === 1 ? client.retours[0] : null);
  if (!retour || !retourPermis(client, retour)) return erreur("Adresse de retour refusée.");
  const etat = q.get("state");
  const echec = (code: string) => versClient(retour, { error: code, state: etat, iss: base });
  if (q.get("response_type") !== "code") return echec("unsupported_response_type");
  const defi = q.get("code_challenge") ?? "";
  if (q.get("code_challenge_method") !== "S256" || !/^[A-Za-z0-9_-]{43}$/.test(defi)) return echec("invalid_request");
  const u = lireUtilisateur(req.headers);
  if (!u) return erreur("Tu dois passer par la passerelle pour te connecter (adresse publique du site).", 401);
  if (!peutUtiliserMcp(u) || !(await toujoursPermis(u.email))) return erreur("Ce compte n'a pas le droit de brancher Claude sur ce site.", 403);
  const billet = sceller(base, "", { t: "consent", c: client.id, r: retour, s: etat, ch: defi, e: u.email, n: u.nom, a: u.admin,
    x: randomBytes(8).toString("base64url"), exp: maintenant() + 10 * 60 });
  const nom = esc(client.nom);
  const corps = `<h1>Autoriser ${nom} ?</h1><p><b>${nom}</b> demande à utiliser ce site avec le compte <!--email_off--><b>${esc(u.email)}</b><!--/email_off-->.</p>`
    + `<form method="post"><input type="hidden" name="billet" value="${esc(billet)}"><div class="b"><button name="choix" value="refuser">Refuser</button><button class="o" name="choix" value="autoriser">Autoriser</button></div></form>`;
  return page(`Autoriser ${client.nom} ?`, corps, 200, new URL(retour).origin);
}

/** POST /api/mcp/oauth/autoriser (clic sur Autoriser ou Refuser). */
export async function autoriserPost(base: string, req: Request): Promise<Response> {
  const form = new URLSearchParams(await req.text());
  const c = desceller(base, form.get("billet"), "", "consent");
  if (!c) return erreur("Page expirée : relance la connexion depuis Claude.");
  const u = lireUtilisateur(req.headers);
  if (!u || u.email !== c.e) return erreur("Tu n'es plus connecté avec le même compte : relance depuis Claude.");
  if (form.get("choix") !== "autoriser") return versClient(c.r, { error: "access_denied", state: c.s, iss: base });
  const code = sceller(base, "", { t: "code", c: c.c, r: c.r, ch: c.ch, e: c.e, n: c.n, a: c.a, g: await generation(),
    j: randomBytes(12).toString("base64url"), exp: maintenant() + DUREE_CODE });
  return versClient(c.r, { code, state: c.s, iss: base });
}

/* ----- étape 7 : code ou renouvellement → jeton */
const codesUtilises = new Map<string, number>(); // un code ne sert qu'une fois (un seul processus suffit)

function pkceOk(verif: string, defi: string): boolean {
  if (!/^[A-Za-z0-9\-._~]{43,128}$/.test(verif)) return false;
  return egal(createHash("sha256").update(verif).digest("base64url"), defi);
}

/** POST /api/mcp/oauth/jeton (x-www-form-urlencoded ou JSON). */
export async function jeton(base: string, req: Request): Promise<Response> {
  const texte = await req.text();
  const form: Record<string, string> = (req.headers.get("content-type") ?? "").includes("json")
    ? (() => { try { return JSON.parse(texte); } catch { return {}; } })()
    : Object.fromEntries(new URLSearchParams(texte));
  let clientId = form.client_id ?? "", secret: string | null = form.client_secret ?? null;
  const basic = req.headers.get("authorization")?.match(/^Basic\s+(\S+)$/i)?.[1];
  if (basic) {
    const brut = Buffer.from(basic, "base64").toString("utf8"), i = brut.indexOf(":");
    if (i > 0) { clientId = decodeURIComponent(brut.slice(0, i)); secret = decodeURIComponent(brut.slice(i + 1)); }
  }
  const client = await trouverClient(base, clientId);
  if (!client) return json({ error: "invalid_client" }, 401);
  if (client.methode !== "none" && !(secret && egal(secret, secretClient(base, clientId)))) return json({ error: "invalid_client" }, 401);

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let v: any;
  if (form.grant_type === "authorization_code") {
    v = desceller(base, form.code, "", "code");
    if (!v || v.c !== clientId) return json({ error: "invalid_grant" }, 400);
    if (form.redirect_uri && v.r !== form.redirect_uri) return json({ error: "invalid_grant" }, 400);
    if (!pkceOk(form.code_verifier ?? "", v.ch)) return json({ error: "invalid_grant" }, 400);
    const t = maintenant();
    for (const [k, e] of codesUtilises) if (e < t) codesUtilises.delete(k);
    if (codesUtilises.has(v.j)) return json({ error: "invalid_grant" }, 400);
    codesUtilises.set(v.j, v.exp);
  } else if (form.grant_type === "refresh_token") {
    v = desceller(base, form.refresh_token, "rr_", "rt");
    if (!v || v.c !== clientId) return json({ error: "invalid_grant" }, 400);
  } else return json({ error: "unsupported_grant_type" }, 400);

  if (v.g !== (await generation()) || !(await toujoursPermis(v.e))) return json({ error: "invalid_grant" }, 400);
  const t = maintenant(), qui = { e: v.e, n: v.n ?? "", a: !!v.a, g: v.g };
  return json({
    access_token: sceller(base, "ra_", { t: "at", ...qui, exp: t + DUREE_ACCES }),
    token_type: "Bearer", expires_in: DUREE_ACCES, scope: "mcp",
    refresh_token: sceller(base, "rr_", { t: "rt", ...qui, c: clientId, j: randomBytes(9).toString("base64url"), exp: t + DUREE_RENOUV }),
  });
}

/** À chaque appel de /api/mcp : jeton signé, pas expiré, bonne génération, compte toujours permis. */
export async function verifierJetonMcp(base: string, autorisation: string | null | undefined): Promise<Utilisateur | null> {
  const brut = (autorisation ?? "").match(/^\s*Bearer\s+(\S+)\s*$/i)?.[1];
  const v = desceller(base, brut, "ra_", "at");
  if (!v || v.g !== (await generation()) || !(await toujoursPermis(v.e))) return null;
  const groupeAdmins = process.env.PASSERELLE_GROUPE_ADMINS ?? "authentik Admins";
  return { email: v.e, nom: v.n ?? "", username: v.e, uid: "", groupes: v.a ? [groupeAdmins] : [], admin: !!v.a };
}

/**
 * Tout le guichet en une fonction : renvoie la réponse si la requête lui est destinée, sinon null.
 * À appeler en tête de ton routeur (Next.js : middleware ou route catch-all ; Hono : app.all("*")…).
 */
export async function guichetMcp(req: Request, nom = ""): Promise<Response | null> {
  const chemin = new URL(req.url).pathname, base = baseSite(req.headers), m = req.method;
  const publics = ["/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server", `${CHEMIN_MCP}/oauth/inscription`, `${CHEMIN_MCP}/oauth/jeton`];
  if (m === "OPTIONS" && publics.some((p) => chemin === p || chemin.startsWith(p + "/"))) return preflight();
  if (chemin === "/.well-known/oauth-protected-resource" || chemin.startsWith("/.well-known/oauth-protected-resource/")) return ficheRessource(base, nom);
  if (chemin === "/.well-known/oauth-authorization-server") return ficheServeur(base);
  if (chemin === `${CHEMIN_MCP}/oauth/inscription` && m === "POST") return inscrire(base, req);
  if (chemin === `${CHEMIN_MCP}/oauth/autoriser`) return m === "POST" ? autoriserPost(base, req) : autoriserGet(base, req);
  if (chemin === `${CHEMIN_MCP}/oauth/jeton` && m === "POST") return jeton(base, req);
  if (chemin === `${CHEMIN_MCP}/oauth/deconnecter` && m === "POST") {
    const u = lireUtilisateur(req.headers);
    if (!u?.admin) return json({ erreur: "Réservé aux administrateurs." }, 403);
    return json({ ok: true, generation: await toutDeconnecter() });
  }
  return null;
}

/** Pour ta route /api/mcp : la personne (jeton du guichet) ou la machine (SERVICE_JETONS), sinon la réponse 401 à renvoyer. */
export async function accesMcp(req: Request): Promise<{ utilisateur: Utilisateur | null; machine: string | null } | Response> {
  if (req.method === "OPTIONS") return preflight();
  const u = await verifierJetonMcp(baseSite(req.headers), req.headers.get("authorization"));
  const m = u ? null : lireMachine(req.headers, req.url);
  if (!u && !m) return refusMcp(baseSite(req.headers), req.headers.has("authorization"));
  return { utilisateur: u, machine: m };
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
 * MCP avec Next.js (app router) :
 *   // src/middleware.ts : tout le guichet (fiches, inscription, autorisation, jeton)
 *   export async function middleware(req: NextRequest) { return (await guichetMcp(req, "Recettes")) ?? NextResponse.next(); }
 *   export const config = { matcher: ["/.well-known/oauth-:path*", "/api/mcp/oauth/:path*"], runtime: "nodejs" };
 *   // src/app/api/mcp/route.ts
 *   export async function POST(req: Request) {
 *     const acces = await accesMcp(req);
 *     if (acces instanceof Response) return acces;          // 401 → « Se connecter » dans Claude
 *     // … ton serveur MCP, en agissant pour acces.utilisateur?.email
 *   }
 *
 * MCP avec Express (adaptateur Request/Response) :
 *   app.use(async (req, res, next) => {
 *     const r = await guichetMcp(versRequest(req)); if (!r) return next();
 *     res.status(r.status); r.headers.forEach((v, k) => res.setHeader(k, v)); res.send(Buffer.from(await r.arrayBuffer()));
 *   });
 *   // versRequest(req) = new Request(PASSERELLE_URL_SITE + req.originalUrl, { method, headers, body })
 */
