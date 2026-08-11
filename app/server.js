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
  res.json({ authRequired: !!ADMIN_PASSWORD })
);

app.post('/api/login', (req, res) => {
  const { password } = req.body || {};
  if (!ADMIN_PASSWORD) return res.status(400).json({ ok: false, error: 'aucun mot de passe configuré' });
  if (!password || !safeEqual(String(password), ADMIN_PASSWORD))
    return res.status(401).json({ ok: false, error: 'mot de passe incorrect' });
  const token = crypto.randomBytes(24).toString('hex');
  tokens.add(token);
  res.json({ ok: true, token });
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
