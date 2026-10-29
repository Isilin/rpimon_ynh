### Commandes

```sh
sudo rpimon stats                  # taille de la base, plages couvertes, dernière mesure
sudo rpimon sample                 # mesure instantanée (diagnostic)
sudo rpimon disk-scan              # recalculer l'occupation du disque (sinon chaque nuit vers 4 h 30)
sudo rpimon migrate                # analyse des anciens relevés (dry-run)
sudo rpimon rollup --vacuum        # recalculer les agrégats récents + compacter
sudo journalctl -u rpimon -f       # logs du service
```

### Réglages

```sh
sudo yunohost app setting rpimon raw_days -v 14     # aussi : hourly_days, daily_days, interval
sudo yunohost app upgrade rpimon --force            # régénère la configuration
```

### Emplacements

- code : `/var/www/rpimon`
- base et archives : `/home/yunohost.app/rpimon` (inclus dans `yunohost backup`)
