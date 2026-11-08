{
  description = "Minimalist and developer-friendly web service for minipy3";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      supportedSystems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
      pkgsFor = system: import nixpkgs { inherit system; };
    in {
      packages = forAllSystems (system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python3;

          # PyPI minipy3 package (latest version from PyPI)
          minipy3 = python.pkgs.buildPythonPackage rec {
            pname = "minipy3";
            version = "1.3.0";
            pyproject = true;
            src = python.pkgs.fetchPypi {
              inherit pname version;
              hash = "sha256-8zhMAnFQnibGah/zfMsKOhLvmDOzX1n4NGCPfseNS+4=";
            };
            build-system = [ python.pkgs.poetry-core ];
            doCheck = false;
          };

          pythonEnv = python.withPackages (ps: [ minipy3 ]);

          minipy-service = pkgs.stdenv.mkDerivation {
            pname = "minipy-service";
            version = "1.0.0";
            src = pkgs.lib.cleanSourceWith {
              src = ./.;
              filter = path: type:
                let baseName = baseNameOf (toString path);
                in baseName != ".venv" && baseName != "data" && baseName != ".git";
            };

            nativeBuildInputs = [ pkgs.makeWrapper ];

            installPhase = ''
              mkdir -p $out/share/minipy-service $out/bin
              cp -r . $out/share/minipy-service/

              makeWrapper ${pythonEnv}/bin/python $out/bin/minipy-service \
                --add-flags "$out/share/minipy-service/app.py"
            '';
          };
        in {
          inherit minipy3 minipy-service;
          default = minipy-service;
        }
      );

      apps = forAllSystems (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/minipy-service";
        };
      });

      devShells = forAllSystems (system:
        let
          pkgs = pkgsFor system;
          python = pkgs.python3;
          minipy3 = self.packages.${system}.minipy3;
        in {
          default = pkgs.mkShell {
            packages = [
              (python.withPackages (ps: [ minipy3 ]))
              pkgs.uv
              pkgs.direnv
              pkgs.curl
              pkgs.sqlite
            ];
          };
        }
      );

      nixosModules = {
        default = { config, lib, pkgs, ... }:
          let
            cfg = config.services.minipy-service;
            pkg = self.packages.${pkgs.system}.default;
          in {
            options.services.minipy-service = {
              enable = lib.mkEnableOption "minipy3 technical web service";
              port = lib.mkOption {
                type = lib.types.port;
                default = 8383;
                description = "Port to listen on";
              };
              host = lib.mkOption {
                type = lib.types.str;
                default = "127.0.0.1";
                description = "Bind host address";
              };
              dataDir = lib.mkOption {
                type = lib.types.str;
                default = "/var/lib/minipy-service";
                description = "Directory storing SQLite database";
              };
            };

            config = lib.mkIf cfg.enable {
              systemd.tmpfiles.rules = [
                "d '${cfg.dataDir}' 0750 minipy minipy - -"
              ];

              users.users.minipy = {
                isSystemUser = true;
                group = "minipy";
                home = cfg.dataDir;
              };
              users.groups.minipy = {};

              systemd.services.minipy-service = {
                description = "Minipy3 Code Compression Web Service";
                after = [ "network.target" ];
                wantedBy = [ "multi-user.target" ];
                serviceConfig = {
                  ExecStart = "${pkg}/bin/minipy-service --host ${cfg.host} --port ${toString cfg.port} --db ${cfg.dataDir}/minipy.db";
                  Restart = "always";
                  RestartSec = "3s";
                  User = "minipy";
                  Group = "minipy";
                  WorkingDirectory = cfg.dataDir;
                  ProtectSystem = "strict";
                  ProtectHome = true;
                  PrivateTmp = true;
                  StateDirectory = "minipy-service";
                };
              };
            };
          };
      };
    };
}

