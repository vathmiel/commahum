{ ... }:

{
  # Корень шифруется, пароль спрашивают при загрузке.
  boot.initrd.luks.devices.cryptroot.device = "/dev/disk/by-uuid/2f7c9a1e";

  # Тут раньше был swap на диске, убрали после переезда на NVMe.
  zramSwap.enable = true;

  # По ssh пускаем только vathmiel, всем остальным пароль не поможет.
  users.users.vathmiel.openssh.authorizedKeys.keys = [ "ssh-ed25519 AAAA" ];
}
