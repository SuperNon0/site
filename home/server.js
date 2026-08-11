const express = require('express');
const path = require('path');
const fs = require('fs');

const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname)));

const DATA_DIR = path.join(__dirname, 'data');
const APPS_FILE = path.join(DATA_DIR, 'apps.json');
fs.mkdirSync(DATA_DIR, { recursive: true });

const DEFAULT_APPS = [
  { id: 'fuellog', icon: '⛽', title: 'FuelLog', desc: 'Suivi carburant · stations · précision ODB', url: 'https://fuel.super-nono.cc', color: 'yellow' },
  { id: 'salaire', icon: '💶', title: 'Salaire', desc: 'Calculateur · fiches de paie', url: 'https://salaire.super-nono.cc', color: 'green' },
];

function loadApps() {
  try {
    return JSON.parse(fs.readFileSync(APPS_FILE, 'utf8'));
  } catch {
    return DEFAULT_APPS;
  }
}

function saveApps(apps) {
  const tmp = APPS_FILE + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(apps, null, 2));
  fs.renameSync(tmp, APPS_FILE);
}

app.get('/api/apps', (req, res) => res.json(loadApps()));

app.post('/api/apps', (req, res) => {
  const apps = loadApps();
  const { icon, title, desc, url, color } = req.body;
  if (!title || !url) return res.status(400).json({ ok: false, error: 'title et url requis' });
  const id = Date.now().toString(36);
  apps.push({ id, icon: icon || '🔗', title, desc: desc || '', url, color: color || 'yellow' });
  saveApps(apps);
  res.json({ ok: true, apps });
});

app.put('/api/apps/:id', (req, res) => {
  const apps = loadApps();
  const idx = apps.findIndex(a => a.id === req.params.id);
  if (idx < 0) return res.status(404).json({ ok: false, error: 'not found' });
  apps[idx] = { ...apps[idx], ...req.body, id: apps[idx].id };
  saveApps(apps);
  res.json({ ok: true, apps });
});

app.delete('/api/apps/:id', (req, res) => {
  const apps = loadApps().filter(a => a.id !== req.params.id);
  saveApps(apps);
  res.json({ ok: true, apps });
});

// Move up/down
app.post('/api/apps/:id/move', (req, res) => {
  const apps = loadApps();
  const idx = apps.findIndex(a => a.id === req.params.id);
  if (idx < 0) return res.status(404).json({ ok: false });
  const { dir } = req.body; // 'up' | 'down'
  const swap = dir === 'up' ? idx - 1 : idx + 1;
  if (swap < 0 || swap >= apps.length) return res.json({ ok: true, apps });
  [apps[idx], apps[swap]] = [apps[swap], apps[idx]];
  saveApps(apps);
  res.json({ ok: true, apps });
});

app.listen(3001, () => console.log('Home running on port 3001'));
