{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:

# The unit is where the isolation lives, so the module ships with the service rather than
# being written again by each consumer. DynamicUser puts the session under /var/lib/private
# and the credentials in a namespace no other process shares; the socket is the only way in
let
  cfg = config.services.tg-agent;
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    types
    ;
in
{
  options.services.tg-agent = {
    enable = mkEnableOption "the Telegram agent service";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.system}.default;
      defaultText = "the flake's own tg-agentd";
      description = "The service to run";
    };

    apiIdFile = mkOption {
      type = types.path;
      description = ''
        A file holding the api_id from my.telegram.org. systemd reads it before the
        service drops to its dynamic identifier, so a file owned by root and readable by
        nobody else is what this wants
      '';
    };

    apiHashFile = mkOption {
      type = types.path;
      description = ''
        A file holding the api_hash. Telegram issues one application per phone number and
        documents no reset, so this value cannot be rotated once it leaks
      '';
    };

    socketUser = mkOption {
      type = types.str;
      description = ''
        Who may talk to the socket. systemd owns its permissions, because a service under
        a dynamic identifier cannot usefully set them for a named user
      '';
    };

    socketPath = mkOption {
      type = types.str;
      default = "/run/tg-agent/socket";
      description = "Where the socket is created";
    };

    permissionsDir = mkOption {
      type = types.path;
      default = "/var/lib/tg-agent/permissions";
      description = ''
        Where the per-chat and per-folder permission files live. The service only reads
        them. Keep them out of reach of whatever writes on the agent's behalf: a
        permission the agent can edit is not a permission
      '';
    };

    outboxDir = mkOption {
      type = types.path;
      default = "/var/lib/tg-agent/outbox";
      description = ''
        Where downloaded attachments land. It sits outside StateDirectory on purpose:
        under DynamicUser that path is /var/lib/private, which no unprivileged user can
        enter, and the point of a download is that a person can open it
      '';
    };

    outboxGroup = mkOption {
      type = types.str;
      default = "users";
      description = ''
        The group that may read the outbox. The service joins it, and the directory is
        setgid so a downloaded file keeps it
      '';
    };

    keepMediaDays = mkOption {
      type = types.ints.positive;
      default = 7;
      description = ''
        How long a downloaded file stays before the daily sweep removes it. Without a
        limit the outbox becomes a permanent copy of every attachment ever read
      '';
    };

    deviceName = mkOption {
      type = types.str;
      default = "tg-agentd";
      description = ''
        The name this session carries in the account's device list. An unnamed client sits
        there among the real ones with nothing to tell it apart
      '';
    };
  };

  config = mkIf cfg.enable {
    systemd = {
      # systemd creates both directories, because the service runs as a user that exists
      # only while it runs, and a directory it created would carry a meaningless owner
      tmpfiles.rules = [
        "d ${cfg.permissionsDir} 0755 root root -"
        "d ${cfg.permissionsDir}/chats 0755 root root -"
        "d ${cfg.permissionsDir}/folders 0755 root root -"
        "d ${cfg.outboxDir} 2775 root ${cfg.outboxGroup} -"
      ];

      sockets."tg-agent" = {
        description = "Socket for the Telegram agent service";
        wantedBy = [ "sockets.target" ];
        socketConfig = {
          ListenStream = cfg.socketPath;
          SocketUser = cfg.socketUser;
          SocketMode = "0600";
        };
      };

      services = {
        "tg-agent" = {
          description = "Telegram agent service";
          requires = [ "tg-agent.socket" ];
          after = [
            "network-online.target"
            "tg-agent.socket"
          ];
          wants = [ "network-online.target" ];

          serviceConfig = {
            ExecStart = lib.concatStringsSep " " [
              (lib.getExe cfg.package)
              "--permissions ${cfg.permissionsDir}"
              "--media-dir ${cfg.outboxDir}"
              "--device ${cfg.deviceName}"
            ];

            # The whole reason this is a unit rather than a script
            DynamicUser = true;
            LoadCredential = [
              "api_id:${cfg.apiIdFile}"
              "api_hash:${cfg.apiHashFile}"
            ];
            StateDirectory = "tg-agent";

            # The outbox is the one path outside the namespace the service may write
            ReadWritePaths = [ cfg.outboxDir ];
            SupplementaryGroups = [ cfg.outboxGroup ];
            UMask = "0022";

            PrivateMounts = true;
            PrivateDevices = true;
            ProtectHome = true;
            ProtectSystem = "strict";
            ProtectKernelTunables = true;
            ProtectKernelModules = true;
            ProtectControlGroups = true;
            RestrictAddressFamilies = [
              "AF_UNIX"
              "AF_INET"
              "AF_INET6"
            ];
            NoNewPrivileges = true;
            Restart = "on-failure";
            RestartSec = "10s";
          };
        };

        "tg-agent-sweep" = {
          description = "Remove downloads that have outlived their keep time";
          serviceConfig = {
            Type = "oneshot";
            ExecStart = "${pkgs.findutils}/bin/find ${cfg.outboxDir} -type f -mtime +${toString cfg.keepMediaDays} -delete";
          };
        };
      };

      # A timer rather than a thread inside the service, so an outbox whose files have
      # outlived their time is emptied even while nothing is being downloaded
      timers."tg-agent-sweep" = {
        description = "Remove downloads that have outlived their keep time";
        wantedBy = [ "timers.target" ];
        timerConfig = {
          OnCalendar = "daily";
          Persistent = true;
        };
      };
    };
  };
}
