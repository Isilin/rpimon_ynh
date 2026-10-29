rpimon collecte les mesures et l'interface est sur https://__DOMAIN____PATH__

Pour importer les anciens relevés de `/var/log/temperatures` :

1. supprimer l'ancienne tâche cron : `sudo crontab -e` ;
2. analyser sans rien écrire : `sudo rpimon migrate` ;
3. importer, archiver et supprimer le dossier : `sudo rpimon migrate --apply --purge`.
