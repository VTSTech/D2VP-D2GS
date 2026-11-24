# Public realm deployment

How to run D2VP's closed realm (PvPGN + D2GS) for players outside your LAN.

---

## 1. Platform requirement — read this first

**D2GS requires an x86 host. A Raspberry Pi cannot run it natively.**

`D2GS.exe` is a 32-bit Windows (PE32 i386) binary executed under Wine. The image
therefore installs `wine32:i386`, which depends on i386 multiarch packages that
**do not exist for ARM64**. On a Pi you would need `box86` plus a hand-built
32-bit Wine — a combination that is fragile, slow, and unsuitable for a public
server.

What *is* portable:

| Component | Architecture | Notes |
|---|---|---|
| `bnetd`, `d2cs`, `d2dbs` (PvPGN) | any (arm64, amd64) | Portable C, builds from source |
| `d2gs` (D2GS.exe + Wine) | **x86 / x86_64 only** | Needs i386 Wine |

So you have two options.

### Topology A — one x86_64 host (recommended)

Everything on a single x86_64 machine (mini-PC, NUC, or any VPS with enough RAM).
Simplest to run, one firewall, one backup target.

### Topology B — Raspberry Pi for the realm, x86 box for games

The Pi runs the login/character services natively; a small x86 machine runs D2GS.

```
        Internet
           |
   +-------+----------------------------+
   |                                    |
[ Raspberry Pi (arm64) ]        [ x86_64 box ]
 bnetd  :6112                    d2gs :4000
 d2cs   :6113  <-----------------  connects to d2cs:6113 and d2dbs:6114
 d2dbs  :6114  <-----------------
```

Requirements for this split:
- The Pi must expose `6112/tcp+udp` and `6113/tcp` publicly.
- The x86 box must expose `4000/tcp` publicly.
- The x86 box must be able to reach the Pi on `6113` and `6114`.
- Deploy D2GS alone with `examples/d2gs-only.compose.yml`, setting
  `D2CS_IP=<pi address>`; it uses host networking so D2GS advertises its own IP.
- In the Pi's `d2cs`/`bnetd` config (`pvpgn-etc` volume), set `D2GS_IPS` to the
  x86 box's address and make sure address translation maps it to its public IP.

> The Pi image for PvPGN needs to be built for `linux/arm64`. See §8 for the
> `liblua5.1-0` build issue that also affects x86.

---

## 2. Prerequisites

- Docker Engine 24+ with the Compose plugin.
- ~2 GB RAM free for the realm, plus disk for game files (~1.5 GB).
- A Diablo II **1.14d** install you own (for the MPQs and game DLLs).
  `scripts/fetch-gamefiles.sh` downloads the server binaries and MPQs and
  verifies them against `scripts/gamefiles.md5`.
- A public IPv4 address (or a host that players can reach).

---

## 3. Quick start (Topology A)

```bash
git clone <your fork> D2VP-D2GS
cd D2VP-D2GS
cp .env.prod.example .env.prod
$EDITOR .env.prod          # set PUBLIC_IP and D2GS_PASSWORD
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f d2gs
```

First start seeds the empty `d2gs-work` volume from the image, so upgrades do not
need a manual copy.

Verify:

```
bnetd  : "Server is now running"
d2cs   : "on_bnetd_authreply: authed by bnetd"
d2gs   : "D2GSAuthReply: Game Server Activated by D2CS"
```

---

## 4. Ports

| Port | Proto | Where | Purpose |
|---|---|---|---|
| 6112 | TCP+UDP | public | Battle.net login/chat |
| 6113 | TCP | public | Realm / character server |
| 4000 | TCP | public | Game server traffic |
| 8888 | TCP | **localhost only** | D2GS admin console |

Forward 6112/tcp, 6112/udp, 6113/tcp and 4000/tcp on your router. Never forward
8888.

`PUBLIC_IP` must be the address players use. PvPGN rewrites the internal
`172.30.0.x` addresses to `PUBLIC_IP` before sending the realm and game server
lists, so a wrong value shows up as clients hanging at "Accessing your Account"
or failing to join games.

---

## 5. Hardening checklist

- [ ] `D2GS_PASSWORD` set (`openssl rand -hex 16`). It is shared between `d2cs`
      and D2GS and prevents unauthorised game servers from attaching.
- [ ] Admin console password changed. The default hash in
      `docker/entrypoint.sh` is for `abcd123`; set `ADMIN_PASSWORD_HASH` in
      `.env.prod`.
- [ ] Port 8888 stays bound to `127.0.0.1` (the prod compose does this).
- [ ] `D2GS_DEBUG_NET_PACKET=0` (hex packet logging is for debugging only).
- [ ] `MAX_GAMES` sized to your RAM. Each game is a Wine-hosted D2 world; start
      at 25–50 and watch memory.
- [ ] Host firewall allows only 6112, 6113 and 4000 inbound.
- [ ] Log rotation is configured (the prod compose caps at 10 MB × 3 per
      service).
- [ ] `pvpgn-var` is backed up (§7) — it holds every account and character.

---

## 6. Accounts

PvPGN keeps accounts in the `pvpgn-var` volume. Create them with the bnetd
console (`telnet 127.0.0.1 6112` → `create account`), or via your usual PvPGN
admin tooling. Accounts are realm-wide, not per-server.

---

## 7. Backups and upgrades

Back up the two stateful volumes; everything else is reproducible.

```bash
scripts/backup-realm.sh /path/to/backups      # tars pvpgn-var + d2gs-work
```

| Volume | Contents | Cost of losing it |
|---|---|---|
| `pvpgn-var` | accounts, characters, ladders | **total player progress** |
| `d2gs-work` | game files, `d2vp.ini`, logs, stash pools | recoverable except `d2vp.ini`/pools |
| `pvpgn-etc` | server configuration | recoverable (re-edited defaults) |

Upgrade procedure:

```bash
scripts/backup-realm.sh ~/backups
docker compose -f docker-compose.prod.yml --env-file .env.prod pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

The `d2gs-work` volume survives rebuilds, so `d2vp.ini` and stash pool files are
preserved.

---

## 8. Known issues

**`liblua5.1-0` has no installation candidate.** The upstream PvPGN Dockerfile
installs Lua 5.1 from apt; some Ubuntu 24.04 mirrors no longer carry that
package name. Workarounds: build with `-DENABLE_LUA=OFF`, install Lua from
source, or pin an older base image. Reuse an already-built `pvpgn:1.99.8` image
if you have one.

**Game files are not in the repository.** They are large and licensed; fetch them
with `scripts/fetch-gamefiles.sh` (verifies against `scripts/gamefiles.md5`).

**Shared stash (`d2vp.ini`)** is under development. The stock server ignores the
feature; enabling it requires a patched `D2Game.dll` plus `d2vpstash.dll`, which
is not production-ready yet. Keep `d2vp.ini` absent or leave
`shared_stash=0` until it is verified.

**Windows-only client.** Players need the D2VP client build; a retail 1.14d
client is version-compatible but will not have the QoL features.

---

## 9. Day-2 operations

```bash
# status / logs
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f --tail=100 d2cs

# restart one service
docker compose -f docker-compose.prod.yml restart d2gs

# enter the admin console
telnet 127.0.0.1 8888

# interactive D2GS registry view
docker compose -f docker-compose.prod.yml exec d2gs cat d2gs.reg
```

Keep the stack's internal addresses (`172.30.0.x`) unchanged — the `TRANSLATE`
rules and `D2GS_IPS` refer to them.
