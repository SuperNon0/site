"""Base SQLite du hub : la liste des sites affichés et les demandes d'accès."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nom             TEXT NOT NULL,
    sous_domaine    TEXT NOT NULL UNIQUE,      -- "recettes" -> recettes.DOMAINE ; "" = lien externe
    cible           TEXT NOT NULL DEFAULT '',  -- http://192.168.1.20:8080 (site protégé)
    lien            TEXT NOT NULL DEFAULT '',  -- adresse ouverte au clic
    description     TEXT NOT NULL DEFAULT '',
    icone           TEXT NOT NULL DEFAULT 'compass',
    categorie       TEXT NOT NULL DEFAULT '',
    acces           TEXT NOT NULL DEFAULT 'membres',   -- membres | admins
    groupes         TEXT NOT NULL DEFAULT '',  -- "famille,amis" : réservé à ces groupes (et aux admins)
    chemins_publics TEXT NOT NULL DEFAULT '',  -- un chemin par ligne, ouverts sans connexion
    type            TEXT NOT NULL DEFAULT 'protege',   -- protege (Authentik) | lien (simple lien)
    ordre           INTEGER NOT NULL DEFAULT 0,
    ak_provider     INTEGER,
    ak_app_slug     TEXT,
    cree_le         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS demandes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE,
    prenom        TEXT NOT NULL,
    nom           TEXT NOT NULL,
    message       TEXT NOT NULL DEFAULT '',
    statut        TEXT NOT NULL DEFAULT 'en_attente',  -- en_attente | acceptee | refusee | inscrite
    jeton_suivi   TEXT NOT NULL,
    invitation    TEXT,              -- pk de l'invitation Authentik
    invitation_expire TEXT,
    decide_par    TEXT,
    decide_le     TEXT,
    ip            TEXT NOT NULL DEFAULT '',
    cree_le       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    quand   TEXT NOT NULL DEFAULT (datetime('now')),
    qui     TEXT NOT NULL,
    quoi    TEXT NOT NULL
);
"""


def chemin_db() -> Path:
    return Path(current_app.config["HUB"].DATA_DIR) / "hub.db"


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        chemin = chemin_db()
        chemin.parent.mkdir(parents=True, exist_ok=True)
        g.db = sqlite3.connect(chemin, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def fermer_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA)
    colonnes = {c["name"] for c in db.execute("PRAGMA table_info(sites)")}
    if "groupes" not in colonnes:  # base créée avant l'accès par groupes
        db.execute("ALTER TABLE sites ADD COLUMN groupes TEXT NOT NULL DEFAULT ''")
    db.commit()


def journaliser(qui: str, quoi: str) -> None:
    db = get_db()
    db.execute("INSERT INTO journal (qui, quoi) VALUES (?, ?)", (qui, quoi))
    db.execute("DELETE FROM journal WHERE id NOT IN (SELECT id FROM journal ORDER BY id DESC LIMIT 500)")
    db.commit()
