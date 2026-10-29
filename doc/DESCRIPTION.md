Monitoring léger d'un Raspberry Pi : température, CPU, charge, fréquence, RAM, swap, disque,
E/S de la carte SD, réseau, sous-tension et bridage (`vcgencmd`).

- une mesure par minute, stockée dans une base SQLite plafonnée à environ 9 Mo
  (brut 7 jours, moyennes horaires 2 ans, journalières sans limite) ;
- page web derrière le SSO, graphiques zoomables, export CSV ;
- occupation du disque par dossier (sauvegardes, données des apps, journaux…), chaque nuit ;
- import des anciens relevés de température (`/var/log/temperatures/JJ-MM-AAAA.html`).
