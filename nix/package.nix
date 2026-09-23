{ lib, python3Packages }:

# The service half of this repository, packaged so that a system configuration can take it
# as a flake input and run it under a unit. The client half needs no packaging: it speaks
# to the socket with the standard library alone
python3Packages.buildPythonApplication {
  pname = "tg-agentd";
  # A skill is read at whatever revision is checked out, so there is no version to promise
  version = "0-unstable";
  pyproject = true;

  # Only the service's own sources, so the derivation does not move on every commit that
  # touches a document beside it
  src = lib.fileset.toSource {
    root = ../.;
    fileset = lib.fileset.unions [
      ../pyproject.toml
      ../tg_agentd
    ];
  };

  build-system = [ python3Packages.setuptools ];

  dependencies = with python3Packages; [
    telethon
    cryptg
  ];

  meta = {
    description = "Serve a Telegram account over a unix socket under per-chat permissions";
    homepage = "https://github.com/rokokol/telegram-skill";
    license = lib.licenses.mit;
    mainProgram = "tg-agentd";
    platforms = lib.platforms.linux;
  };
}
