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
 *
 * Variables d'environnement : PASSERELLE_CLE, PASSERELLE_GROUPE_ADMINS, SERVICE_JETONS
 * ("nom:jeton,nom2:jeton2"), PASSERELLE_DEV_EMAIL / PASSERELLE_DEV_ADMIN (dev seulement,
 * ignorés dès que PASSERELLE_CLE est rempli).
 */
import { createHmac, timingSafeEqual } from "node:crypto";

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
 * (seulement pour les clients qui ne savent pas envoyer d'en-tête, comme un connecteur MCP).
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
 */
