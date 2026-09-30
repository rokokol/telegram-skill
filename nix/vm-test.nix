{ self, pkgs }:

# The unit's sandbox decides what the service can reach, and none of it exists outside a
# booted system: DynamicUser, an id-mapped StateDirectory, ReadWritePaths, the socket's
# owner. The real service needs a signed-in session and the network, so the package is
# swapped for a probe that takes the same arguments and touches the same places. Every
# other line of the unit is the module's own
let
  probe = pkgs.writeShellApplication {
    name = "tg-agentd";
    text = ''
      while [ "$#" -gt 0 ]; do
        case "$1" in
          --permissions) permissions=$2; shift 2 ;;
          --media-dir) media=$2; shift 2 ;;
          *) shift ;;
        esac
      done

      # What a login leaves behind, and what must stay out of every user's reach
      printf 'session' >"$STATE_DIRECTORY/account.session"

      # What a download does: a subdirectory per chat, then the file
      mkdir -p "$media/chat-1"
      printf 'attachment' >"$media/chat-1/report.pdf"

      # The service only reads its permission files, and the unit has to hold it to that
      if touch "$permissions/chats/probe.conf" 2>/dev/null; then
        printf 'yes' >"$media/permissions-writable"
      fi

      touch "$media/probe.done"
    '';
  };
in
pkgs.testers.runNixOSTest {
  name = "tg-agent";

  nodes.machine = {
    imports = [ self.nixosModules.default ];

    users.users = {
      alice.isNormalUser = true;
      bob.isNormalUser = true;
    };

    services.tg-agent = {
      enable = true;
      package = probe;
      apiIdFile = pkgs.writeText "api-id" "1";
      apiHashFile = pkgs.writeText "api-hash" "0";
      socketUser = "alice";
    };
  };

  # Paths come from the module, so a changed default is tested rather than bypassed
  testScript =
    { nodes, ... }:
    let
      inherit (nodes.machine.services.tg-agent) outboxDir outboxGroup socketPath;
    in
    ''
      machine.wait_for_unit("sockets.target")
      machine.succeed("systemctl start tg-agent.service")
      machine.wait_until_succeeds("test -e /var/lib/private/tg-agent/account.session", timeout=60)

      with subtest("a download is written by the service and read by a person"):
          machine.wait_until_succeeds("test -e ${outboxDir}/probe.done", timeout=30)
          assert machine.succeed("su alice -c 'cat ${outboxDir}/chat-1/report.pdf'") == "attachment"
          assert machine.succeed("stat -c %G ${outboxDir}/chat-1/report.pdf").strip() == "${outboxGroup}"

      with subtest("the session stays out of every user's reach"):
          machine.fail("su alice -c 'cat /var/lib/tg-agent/account.session'")

      with subtest("the service cannot write its own permissions"):
          machine.fail("test -e ${outboxDir}/permissions-writable")

      with subtest("only the socket user may talk to the service"):
          assert machine.succeed("stat -c '%U %a' ${socketPath}").strip() == "alice 600"
          machine.succeed("su alice -c 'test -w ${socketPath}'")
          machine.fail("su bob -c 'test -w ${socketPath}'")
    '';
}
