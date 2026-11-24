#!/bin/bash
# Writes the D2GS registry settings from environment variables, then starts D2GS.
set -euo pipefail

: "${D2CS_IP:?D2CS_IP must be set to the IP address or hostname of your PvPGN d2cs server}"

# When the game directory is a volume it starts empty; seed it from the image so
# upgrades don't require a manual copy. Safe to leave in place with no volume.
if [ ! -f D2GS.exe ] && [ -d /opt/d2gs-seed ]; then
  echo "Seeding empty game directory from /opt/d2gs-seed"
  cp -a /opt/d2gs-seed/. .
fi

# D2GS only accepts IP addresses; resolve hostnames such as a compose service name.
resolve() {
  local ip
  ip=$(getent ahostsv4 "$1" | awk 'NR==1 {print $1}')
  [ -n "$ip" ] || { echo "Cannot resolve '$1'" >&2; exit 1; }
  echo "$ip"
}
D2CS_IP=$(resolve "$D2CS_IP")
[ -n "${D2DBS_IP:-}" ] && D2DBS_IP=$(resolve "$D2DBS_IP")
D2DBS_IP="${D2DBS_IP:-$D2CS_IP}"
D2CS_PORT="${D2CS_PORT:-6113}"
D2DBS_PORT="${D2DBS_PORT:-6114}"
D2CS_SECRET="${D2CS_SECRET:-}"
MAX_GAMES="${MAX_GAMES:-50}"
MAX_GAME_LIFE="${MAX_GAME_LIFE:-14400}"
MAX_PREFER_USERS="${MAX_PREFER_USERS:-180}"
ADMIN_PORT="${ADMIN_PORT:-8888}"
# Hash of the default admin password "abcd123"
ADMIN_PASSWORD_HASH="${ADMIN_PASSWORD_HASH:-9e75a42100e1b9e0b5d3873045084fae699adcb0}"
MOTD="${MOTD:-Welcome to Diablo II!}"
# Packet/callback hex logging to d2gs.log (verbose; keep off in steady state)
D2GS_DEBUG_NET_PACKET="${D2GS_DEBUG_NET_PACKET:-0}"
D2GS_DEBUG_EVENT_CALLBACK="${D2GS_DEBUG_EVENT_CALLBACK:-0}"

dword() { printf 'dword:%08x' "$1"; }

REG=d2gs.reg
if [ -f /config/d2gs.reg ]; then
  echo "Using custom registry file /config/d2gs.reg"
  cp /config/d2gs.reg "$REG"
else
  cat > "$REG" <<REG
REGEDIT4

[HKEY_LOCAL_MACHINE\\SOFTWARE\\D2Server\\D2GS]
@="Diablo II Close Game Server"
"AutoUpdate"=dword:00000000
"AutoUpdateUrl"=""
"AutoUpdateVer"=dword:00000000
"AutoUpdateTimeout"=dword:00007530
"D2CSIP"="${D2CS_IP}"
"D2CSPort"=$(dword "$D2CS_PORT")
"D2DBSIP"="${D2DBS_IP}"
"D2DBSPort"=$(dword "$D2DBS_PORT")
"MaxGames"=$(dword "$MAX_GAMES")
"MaxGameLife"=$(dword "$MAX_GAME_LIFE")
"AdminPassword"="${ADMIN_PASSWORD_HASH}"
"AdminPort"=$(dword "$ADMIN_PORT")
"AdminTimeout"=dword:00000e10
"D2CSSecrect"="${D2CS_SECRET}"
"EnableNTMode"=dword:00000001
"EnablePreCacheMode"=dword:00000001
"IdleSleep"=dword:00000001
"BusySleep"=dword:00000001
"CharPendingTimeout"=dword:00000258
"DebugNetPacket"=$(dword "$D2GS_DEBUG_NET_PACKET")
"DebugEventCallback"=$(dword "$D2GS_DEBUG_EVENT_CALLBACK")
"EnableGSLog"=dword:00000001
"EnableGELog"=dword:00000001
"EnableGEMsg"=dword:00000000
"EnableGEPatch"=dword:00000001
"IntervalReconnectD2CS"=dword:00000032
"MultiCPUMask"=dword:00000001
"MaxPreferUsers"=$(dword "$MAX_PREFER_USERS")
"MaxPacketPerSecond"=dword:000004b0
"ServerConfFile"="D2Server.ini"
"MOTD"="${MOTD}"
REG
fi

# Optional overrides for D2GS's own ini files
for f in d2server.ini d2warden.ini; do
  [ -f "/config/$f" ] && cp "/config/$f" "./$f" && echo "Using custom /config/$f"
done

echo "Connecting to d2cs at ${D2CS_IP}:${D2CS_PORT}, d2dbs at ${D2DBS_IP}:${D2DBS_PORT}"
wine regedit "$REG"

# D2GS writes its logs to files; mirror them to stdout for `docker logs`.
touch d2gs.log d2ge.log
tail -n0 -F d2gs.log d2ge.log 2>/dev/null &

exec wine D2GS.exe
