"""Métriques suivies : clé (= colonne SQLite), libellé, unité, décimales d'affichage.

Ajouter une ligne ici suffit : les colonnes manquantes sont créées au démarrage.
"""

METRICS = [
    ("temp",      "Température SoC",  "°C",   1),
    ("cpu",       "CPU",              "%",    1),
    ("load1",     "Charge (1 min)",   "",     2),
    ("freq",      "Fréquence ARM",    "MHz",  0),
    ("mem",       "RAM utilisée",     "%",    1),
    ("swap",      "Swap utilisé",     "%",    1),
    ("disk",      "Disque utilisé",   "%",    1),
    ("disk_free", "Espace libre",     "Go",   2),
    ("io_read",   "Lecture disque",   "ko/s", 1),
    ("io_write",  "Écriture disque",  "ko/s", 1),
    ("net_rx",    "Réseau reçu",      "ko/s", 1),
    ("net_tx",    "Réseau émis",      "ko/s", 1),
    ("undervolt", "Sous-tension",     "",     2),
    ("throttled", "Bridage",          "",     2),
]
KEYS = [m[0] for m in METRICS]
DEC = {m[0]: m[3] for m in METRICS}
AGGS = ("avg", "min", "max")
AGG_COLS = ["%s_%s" % (k, a) for k in KEYS for a in AGGS]
