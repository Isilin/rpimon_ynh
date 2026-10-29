#!/bin/bash

# Réglages modifiables après coup :
#   sudo yunohost app setting rpimon raw_days -v 14 && sudo yunohost app upgrade rpimon --force
_rpimon_default_settings() {
    ynh_app_setting_set_default --key=raw_days --value=7
    ynh_app_setting_set_default --key=hourly_days --value=730
    ynh_app_setting_set_default --key=daily_days --value=0
    ynh_app_setting_set_default --key=interval --value=60
}

# Le code est versionné dans ce dépôt (sources/) : pas de téléchargement, on le copie.
_rpimon_setup_source() {
    ynh_safe_rm "$install_dir/rpimon"
    cp -r "$YNH_APP_BASEDIR/sources/rpimon" "$install_dir/rpimon"
    install -m 755 "$YNH_APP_BASEDIR/sources/rpimon-cli" "$install_dir/rpimon-cli"
    python3 -m compileall -q "$install_dir/rpimon"
    chmod -R u=rwX,g=rX,o= "$install_dir"
    chown -R "$app:$app" "$install_dir"
}

# Fichiers de configuration : rpimon.conf + commande /usr/local/bin/rpimon
_rpimon_add_config() {
    ynh_config_add --template="rpimon.conf" --destination="$install_dir/rpimon.conf"
    chmod 640 "$install_dir/rpimon.conf"
    chown "$app:$app" "$install_dir/rpimon.conf"

    ynh_config_add --template="rpimon.sh" --destination="/usr/local/bin/$app"
    chmod 755 "/usr/local/bin/$app"
    chown root:root "/usr/local/bin/$app"
}

# Scan nocturne de l'occupation du disque : service à part, seule capacité CAP_DAC_READ_SEARCH
_rpimon_add_disk_scan() {
    ynh_config_add --template="disk.service" --destination="/etc/systemd/system/$app-disk.service"
    ynh_config_add --template="disk.timer" --destination="/etc/systemd/system/$app-disk.timer"
    systemctl daemon-reload
    systemctl enable --now "$app-disk.timer" --quiet
    # premier scan tout de suite, en arrière-plan (quelques minutes sur une carte SD)
    systemctl start --no-block "$app-disk.service"
}

_rpimon_remove_disk_scan() {
    if [ -e "/etc/systemd/system/$app-disk.timer" ]; then
        systemctl disable --now "$app-disk.timer" --quiet
        ynh_safe_rm "/etc/systemd/system/$app-disk.timer"
    fi
    ynh_config_remove_systemd "$app-disk"
}

# Logo de la tuile du portail et de la liste des apps (option --logo : YunoHost >= 12.1)
_rpimon_set_logo() {
    local logo="$YNH_APP_BASEDIR/doc/logo.png"
    if [ -f "$logo" ] && yunohost user permission update --help 2>/dev/null | grep -q -- "--logo"; then
        yunohost user permission update "$app.main" --logo "$logo" >/dev/null
    fi
}

_rpimon_service_add() {
    yunohost service add "$app" --description="Monitoring du Raspberry Pi (rpimon)"
}
