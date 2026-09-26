{ config, lib, pkgs, ... }:

{
  # Бэкап ходит на 10.8.0.4:2222, ключ лежит в /etc/ssh/backup_ed25519.
  services.borgbackup.jobs.home = {
    paths = [ "/home/vathmiel" ];
    repo = "ssh://backup@10.8.0.4:2222/./srv/borg/home";
    # Храним 7 ежедневных, 4 недельных и 3 месячных архива, prune запускается в 03:30.
    prune.keep = { daily = 7; weekly = 4; monthly = 3; };
  };

  # Home Assistant слушает порт 8123 и ходит наружу через https://ha.example.net.
  services.home-assistant.enable = true;

  # По умолчанию таймаут 90 секунд; на этом железе хватает 30.
  systemd.services.home-assistant.serviceConfig.TimeoutStartSec = 30;
}
