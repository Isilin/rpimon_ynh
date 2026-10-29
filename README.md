# rpimon pour YunoHost

Monitoring léger d'un Raspberry Pi sous YunoHost : température, CPU, charge, fréquence, RAM, swap,
disque, E/S de la carte SD, réseau, sous-tension et bridage. Une page web derrière le SSO,
un export CSV, et l'import des anciens relevés de température.

| | Ancien script cron | rpimon |
|---|---|---|
| Stockage | 1 fichier HTML par jour, pour toujours | 1 base SQLite plafonnée à environ 9 Mo |
| Mesures | température seule | 14 métriques (voir `sources/rpimon/metrics.py`) |
| Historique | brut illimité | brut 7 j, puis moyenne/min/max par heure (2 ans) et par jour (illimité) |
| Consultation | ouvrir les fichiers | page web derrière le SSO YunoHost, export CSV |
| Sauvegarde | aucune | incluse dans `yunohost backup` |

Dépendances : `python3` (≥ 3.9, déjà présent avec YunoHost), rien d'autre. Le service consomme
environ 20 Mo de RAM. uPlot (MIT) est inclus : l'interface ne charge rien depuis Internet.

## Installation

```sh
sudo yunohost app install https://github.com/Isilin/rpimon_ynh
```

YunoHost demande le domaine, le chemin (`/rpimon` par défaut) et qui a accès (**admins** par défaut :
les métriques du serveur n'ont pas à être publiques). Mise à jour :

```sh
sudo yunohost app upgrade rpimon -u https://github.com/Isilin/rpimon_ynh
```

## Importer l'historique et faire le ménage

Une fois l'app installée (le collecteur tourne déjà) :

1. Supprimer l'ancienne tâche cron : `sudo crontab -e`, retirer la ligne qui écrit dans `/var/log/temperatures`.
2. Analyser sans rien écrire : `sudo rpimon migrate`.
   Affiche le nombre de mesures reconnues, la période, la cadence, des exemples de lignes ignorées
   et le format décimal détecté.
3. Importer, archiver et supprimer l'ancien dossier : `sudo rpimon migrate --apply --purge`.

Ce que fait l'import :

- les jours encore couverts par la rétention brute sont importés en brut, le reste directement en
  agrégats horaires (2 dernières années) et journaliers (tout) ;
- les mesures postérieures au démarrage de rpimon sont ignorées, et une ligne écrite par le
  collecteur n'est jamais écrasée : la commande peut être relancée sans risque ;
- `--purge` archive le dossier dans `/home/yunohost.app/rpimon/archives/temperatures-legacy-*.tar.xz`
  (inclus dans les sauvegardes de l'app), vérifie l'archive, puis supprime `/var/log/temperatures`.
  La suppression est refusée tant qu'une tâche cron écrit encore dans le dossier.

**Format des anciens fichiers.** Les fichiers `JJ-MM-AAAA.html` (ou `AAAA-MM-JJ`) sont acceptés.
Dans une ligne, le parseur cherche une heure `HH:MM[:SS]` et une température (`44.12 °C`, `44,12`,
`44123`, avec ou sans balises HTML, ou heure et température sur deux lignes). L'ancien script écrivait
`$TEMP_INT.$TEMP_FLOAT` sans zéros de remplissage : 44 012 m°C devenait « 44.12 ». Ce cas est détecté
automatiquement ; `--decimal milli` ou `--decimal plain` force un mode.

## Utilisation

La page est sur `https://<domaine>/rpimon/` et une tuile apparaît dans le portail.

- tuiles d'état : température (normale < 70 °C, élevée < 80 °C, critique au-delà), CPU, RAM, disque,
  réseau, fréquence, alimentation (`vcgencmd get_throttled`) ;
- périodes 6 h à « Tout », résolution adaptée (brut, horaire puis journalier, avec zone min/max) ;
- cliquer-glisser pour zoomer, curseurs synchronisés, export CSV (`;`, virgule décimale) ;
- thème clair ou sombre selon le système, affichage mobile.

Commandes :

```sh
sudo rpimon stats                  # taille de la base, plages couvertes, dernière mesure
sudo rpimon sample                 # mesure instantanée (diagnostic)
sudo rpimon rollup --vacuum        # recalculer les agrégats récents + compacter
sudo journalctl -u rpimon -f       # logs du service
```

## Réglages

Stockés dans les réglages de l'app, appliqués à la mise à jour :

```sh
sudo yunohost app setting rpimon raw_days -v 14
sudo yunohost app upgrade rpimon --force
```

| Réglage | Défaut | Rôle |
|---|---|---|
| `raw_days` | 7 | jours de mesures brutes conservés (minimum 2) |
| `hourly_days` | 730 | jours d'agrégats horaires (0 = illimité) |
| `daily_days` | 0 | jours d'agrégats journaliers (0 = illimité) |
| `interval` | 60 | secondes entre deux mesures (minimum 10) |

Volume mesuré, toutes métriques renseignées : 1,4 Mo pour 7 jours bruts, 3,6 Mo par an d'horaire et
150 Ko par an de journalier, soit environ 9 Mo au total.

## Développement

```
sources/rpimon/      code Python (bibliothèque standard uniquement)
  cli.py             commandes run / sample / stats / rollup / migrate
  collector.py       lecture de /proc, /sys et vcgencmd
  store.py           SQLite : brut, agrégats, rétention
  web.py             API JSON + fichiers statiques
  legacy.py          import des anciens relevés
  static/            interface (HTML, CSS, JS, uPlot)
sources/rpimon-cli   point d'entrée
manifest.toml        paquet YunoHost (format v2, helpers 2.1)
scripts/, conf/      scripts d'installation et modèles de configuration
tests/               pytest
```

```sh
pip install pytest ruff
pytest
ruff check sources tests
```

Lancer en local sans YunoHost : `python3 sources/rpimon-cli --config ma.conf run` avec une config
qui pointe `[storage] db` vers un fichier accessible.

### Notes techniques

- **Un seul processus** (`rpimon run`) : une boucle de collecte calée sur la minute et un petit serveur
  HTTP. Les débits (CPU, réseau, disque) sont calculés exactement sur l'intervalle.
- **SQLite en WAL**, une petite transaction par minute, agrégats recalculés toutes les 10 minutes.
  Les lignes supprimées par la rétention sont réutilisées : la base ne grossit plus en régime établi.
- **Sous-tension** : un point vaut 1 si la tension est basse au moment de la mesure, ou si le bit
  « depuis le démarrage » de `get_throttled` vient d'apparaître. En agrégé : pourcentage du temps.
- **Sécurité** : écoute sur `127.0.0.1` uniquement, accès via nginx et le SSO, service systemd durci
  (`ProtectSystem=strict`, écriture limitée au dossier de données, `NoNewPrivileges`…).

## Licence

MIT. uPlot : MIT, © Leon Sorokin (`sources/rpimon/static/uPlot.LICENSE.txt`).
