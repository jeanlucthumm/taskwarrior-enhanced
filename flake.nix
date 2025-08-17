{
  description = "Companion CLI for taskwarrior";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = {
    self,
    nixpkgs,
    uv2nix,
    pyproject-nix,
    pyproject-build-systems,
    ...
  }: let
    inherit (nixpkgs) lib;

    # Load the workspace from the current directory
    workspace = uv2nix.lib.workspace.loadWorkspace {workspaceRoot = ./.;};

    # Create an overlay from the workspace for the package set
    overlay = workspace.mkPyprojectOverlay {
      sourcePreference = "wheel"; # Prefer wheel over sdist
    };

    # Build fixups, if needed
    pyprojectOverrides = final: _prev: {};

    systems = [
      "aarch64-linux"
      "i686-linux"
      "x86_64-linux"
      "aarch64-darwin"
      "x86_64-darwin"
    ];
    forAllSystems = lib.genAttrs systems;
  in {
    # Nix packages
    packages = forAllSystems (
      system: let
        pkgs = nixpkgs.legacyPackages.${system};

        # Create the python package set
        python = pkgs.python313;

        # Create the python package set with overlays
        pythonSet =
          (pkgs.callPackage pyproject-nix.build.packages {
            inherit python;
          }).overrideScope (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              overlay
              pyprojectOverrides
            ]
          );

        # Create virtual environment with all dependencies
        virtualenv = pythonSet.mkVirtualEnv "taskwarrior-enhanced-env" workspace.deps.default;

        # Build the application package
        taskwarrior-enhanced = pkgs.stdenv.mkDerivation {
          name = "taskwarrior-enhanced";
          version = "0.1.0";

          src = ./.;

          buildInputs = [pkgs.makeWrapper];

          installPhase = ''
            mkdir -p $out/bin

            # Copy the main.py script
            cp main.py $out/bin/taskwarrior-enhanced-script

            # Create wrapper that uses the virtual environment
            makeWrapper ${virtualenv}/bin/python $out/bin/taskwarrior-enhanced \
              --add-flags "$out/bin/taskwarrior-enhanced-script" \
              --prefix PATH : ${lib.makeBinPath [pkgs.taskwarrior3]}
          '';

          meta = {
            description = "Companion CLI for taskwarrior";
            homepage = "https://github.com/your-username/taskwarrior-enhanced";
            license = lib.licenses.mit;
            maintainers = [];
            mainProgram = "taskwarrior-enhanced";
          };
        };
      in {
        default = taskwarrior-enhanced;
        taskwarrior-enhanced = taskwarrior-enhanced;
      }
    );

    # Application for nix run
    apps = forAllSystems (
      system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.default}/bin/taskwarrior-enhanced";
        };
      }
    );
  };
}

