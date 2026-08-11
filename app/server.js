const express = require('express');
const multer = require('multer');
const crypto = require('crypto');
const path = require('path');
const fs = require('fs');

const app = express();
const PORT = process.env.PORT || 3000;

// Mot de passe admin (défini par l'installeur dans .env / l'environnement).
const ADMIN_PASSWORD = process.env.HUB_ADMIN_PASSWORD || '';

const DATA_DIR = path.join(__dirname, 'data');
const ICONS_DIR = path.join(DATA_DIR, 'icons');
const CONFIG_FILE = path.join(DATA_DIR, 'config.json');
const AUTH_FILE = path.join(DATA_DIR, 'auth.json');
fs.mkdirSync(ICONS_DIR, { recursive: true });

// ─── Config par défaut (au premier lancement) ────────────────────────────────
const DEFAULT_CONFIG = require('./data/default-config.json');

function loadConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
  } catch {
    saveConfig(DEFAULT_CONFIG);
    return DEFAULT_CONFIG;
  }
}
function saveConfig(cfg) {
  const tmp = CONFIG_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(cfg, null, 2));
  fs.renameSync(tmp, CONFIG_FILE);
}

// ─── Auth (jeton en mémoire) ──────────────────────────────────────────────────
const tokens = new Set();
function safeEqual(a, b) {
  const ba = Buffer.from(a), bb = Buffer.from(b);
  if (ba.length !== bb.length) return false;
  return crypto.timingSafeEqual(ba, bb);
}
// Mot de passe : soit haché dans data/auth.json (modifié via l'UI),
// soit celui défini à l'installation (env HUB_ADMIN_PASSWORD).
function readStoredAuth() {
  try { return JSON.parse(fs.readFileSync(AUTH_FILE, 'utf8')); } catch { return null; }
}
function writeStoredAuth(pw) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(String(pw), salt, 64);
  const tmp = AUTH_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify({ salt: salt.toString('hex'), hash: hash.toString('hex') }, null, 2));
  fs.renameSync(tmp, AUTH_FILE);
}
function verifyStored(pw, stored) {
  const hash = crypto.scryptSync(String(pw), Buffer.from(stored.salt, 'hex'), 64);
  const a = Buffer.from(stored.hash, 'hex');
  return a.length === hash.length && crypto.timingSafeEqual(a, hash);
}
function passwordConfigured() { return !!readStoredAuth() || !!ADMIN_PASSWORD; }
function checkPassword(pw) {
  const stored = readStoredAuth();
  if (stored) return verifyStored(pw, stored);
  if (ADMIN_PASSWORD) return safeEqual(String(pw), ADMIN_PASSWORD);
  return false;
}
function requireAuth(req, res, next) {
  const auth = req.headers.authorization || '';
  const token = auth.replace(/^Bearer\s+/i, '');
  if (token && tokens.has(token)) return next();
  return res.status(401).json({ ok: false, error: 'non autorisé' });
}

// ─── Middlewares ──────────────────────────────────────────────────────────────
app.use(express.json({ limit: '2mb' }));
app.use(express.static(path.join(__dirname, 'public')));
app.use('/data/icons', express.static(ICONS_DIR));
// Police Material Design Icons servie depuis node_modules
app.use('/vendor/mdi', express.static(path.join(__dirname, 'node_modules/@mdi/font')));

// ─── API ──────────────────────────────────────────────────────────────────────
app.get('/api/config', (req, res) => res.json(loadConfig()));

// Indique au front si un mot de passe est configuré
app.get('/api/status', (req, res) =>
  res.json({ authRequired: passwordConfigured() })
);

app.post('/api/login', (req, res) => {
  const { password } = req.body || {};
  if (!passwordConfigured()) return res.status(400).json({ ok: false, error: 'aucun mot de passe configuré' });
  if (!password || !checkPassword(password))
    return res.status(401).json({ ok: false, error: 'mot de passe incorrect' });
  const token = crypto.randomBytes(24).toString('hex');
  tokens.add(token);
  res.json({ ok: true, token });
});

// Changer le mot de passe admin
app.post('/api/change-password', requireAuth, (req, res) => {
  const { currentPassword, newPassword } = req.body || {};
  if (!checkPassword(currentPassword))
    return res.status(401).json({ ok: false, error: 'mot de passe actuel incorrect' });
  if (!newPassword || String(newPassword).length < 4)
    return res.status(400).json({ ok: false, error: 'nouveau mot de passe trop court (min. 4)' });
  writeStoredAuth(String(newPassword));
  res.json({ ok: true });
});

// Mise à jour du hub (git pull + npm install + redémarrage du service)
app.post('/api/update', requireAuth, (req, res) => {
  const { execFile, spawn } = require('child_process');
  const repoDir = path.join(__dirname, '..'); // /opt/hub
  execFile('git', ['-C', repoDir, 'pull', '--ff-only'], { timeout: 60000 }, (err, stdout, stderr) => {
    if (err) return res.status(500).json({ ok: false, error: 'git pull : ' + (stderr || err.message).trim() });
    if (/Already up to date|Déjà à jour/i.test(stdout))
      return res.json({ ok: true, updated: false, message: 'Déjà à jour ✓' });
    execFile('npm', ['install', '--omit=dev', '--no-audit', '--no-fund'],
      { cwd: path.join(repoDir, 'app'), timeout: 180000 }, (err2, out2, stderr2) => {
        if (err2) return res.status(500).json({ ok: false, error: 'npm install : ' + (stderr2 || err2.message).trim() });
        res.json({ ok: true, updated: true, message: 'Mise à jour appliquée, redémarrage…' });
        // Redémarrage découplé du service courant (survit à notre propre arrêt)
        setTimeout(() => {
          spawn('systemd-run', ['--no-block', '--collect', 'systemctl', 'restart', 'hub'],
            { detached: true, stdio: 'ignore' }).unref();
        }, 600);
      });
  });
});

app.put('/api/config', requireAuth, (req, res) => {
  const cfg = req.body;
  if (!cfg || !Array.isArray(cfg.categories))
    return res.status(400).json({ ok: false, error: 'config invalide' });
  saveConfig(cfg);
  res.json({ ok: true });
});

// Upload d'un logo custom
const upload = multer({
  storage: multer.diskStorage({
    destination: (req, file, cb) => cb(null, ICONS_DIR),
    filename: (req, file, cb) => {
      const ext = (path.extname(file.originalname) || '.png').toLowerCase();
      cb(null, Date.now().toString(36) + ext);
    },
  }),
  limits: { fileSize: 2 * 1024 * 1024 },
  fileFilter: (req, file, cb) => cb(null, /^image\//.test(file.mimetype)),
});
app.post('/api/upload', requireAuth, upload.single('file'), (req, res) => {
  if (!req.file) return res.status(400).json({ ok: false, error: 'aucun fichier' });
  res.json({ ok: true, url: '/data/icons/' + req.file.filename });
});

app.listen(PORT, () => console.log('Hub super-nono en écoute sur le port ' + PORT));
