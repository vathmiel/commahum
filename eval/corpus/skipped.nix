{ pkgs ? import <nixpkgs> { } }:

# shellcheck shell=bash
# nix-shell -p nixfmt-rfc-style --run "nixfmt ."
# https://github.com/NixOS/nixpkgs/issues/12345
# --- разное ---------------------------------------------------------
{
  # TODO: убрать после переезда на flake-модули
  # FIXME: ключ истекает в 2027 году, продлить до релиза 26.05
  system.stateVersion = "24.05";
}
