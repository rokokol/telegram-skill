{
  description = "Reach a Telegram account over MTProto under per-chat permissions the agent cannot grant itself";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs, ... }:
    let
      inherit (nixpkgs) lib;
      # systemd holds the secret, so there is no port to a system without it
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: {
        default = pkgs.callPackage ./nix/package.nix { };
      });

      # The unit carries the isolation the service depends on, so it ships here rather
      # than being written again by every consumer
      nixosModules.default = import ./nix/module.nix { inherit self; };

      # The pinned toolbox for check.sh, locally and in CI — a linter looked up from a
      # registry at job time makes the run a test of someone else's mirror
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            actionlint
            # The same binary the formatter output wraps with treefmt. The gate calls it
            # directly, because `nix fmt` needs the flake and a check should not
            nixfmt
            deadnix
            statix
            shellcheck
            shfmt
            jq
            (python3.withPackages (ps: [
              ps.telethon
              ps.cryptg
              ps.pytest
            ]))
          ];
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt-tree);
    };
}
