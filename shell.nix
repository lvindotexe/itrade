{ pkgs ? import <nixpkgs> { } }:

pkgs.mkShell {
  packages = [
    pkgs.python312
    pkgs.uv
  ];

  # Use Nix's interpreter rather than downloading another Python through uv.
  UV_PYTHON = "${pkgs.python312}/bin/python3";
  UV_PYTHON_DOWNLOADS = "never";
  PYTHONDONTWRITEBYTECODE = "1";
}
