#!/bin/sh
# Commande rpimon - installée par le paquet YunoHost __APP__
exec /usr/bin/python3 __INSTALL_DIR__/rpimon-cli --config __INSTALL_DIR__/rpimon.conf "$@"
