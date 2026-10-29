"""rpimon - monitoring léger pour Raspberry Pi (pensé pour YunoHost).

Uniquement la bibliothèque standard (Python >= 3.9), stockage SQLite :
  samples  mesures brutes (1/min par défaut), gardées raw_days jours
  hourly   moyenne/min/max par heure, gardées hourly_days jours (0 = toujours)
  daily    moyenne/min/max par jour (heure locale), gardées daily_days (0 = toujours)
"""

__version__ = "1.1.0"
