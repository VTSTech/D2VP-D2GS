# D2VP-D2GS — handoff

State at end of session 2026-11-23/24. Read this first, then
`docs/PUBLIC-DEPLOYMENT.md` for deployment specifics.

---

## 1. What this project is

A closed-realm Diablo II server for the D2VP client: PvPGN (bnetd / d2cs /
d2dbs) + D2GS, containerised. Two deliverables are in flight:

1. **Stable public realm** — works. May be hosted on x86_64; a Raspberry Pi
   can host PvPGN natively but not D2GS (§7).
2. **Shared stash on realms** — blocked. The client-side feature works in
   single player; on a realm it needs server-side game logic, which we deliver
   by injecting a hook into stock `D2Game.dll` that calls a QoL-built
   `d2vpstash.dll`.

---

## 2. Current status — one line each

| Area | Status |
|---|---|
| Closed realm (login, char list, games) | **Working** |
| Public deployment assets | **Done** (§6) |
| Hook injection into `D2Game.dll` | **Working** — stub executes, DLL loads, function is called |
| Shared stash behaviour | **Not working** — hook sees a `0x34` buffer, not the `0x4F` toggle (§5) |
| Consequence of the fault | D2GS crashes whenever the hook path runs; realm auto-restarts in ~10 s |
| Raspberry Pi / arm64 D2GS | Build staged, unverified (§7) |

Local realm is currently running the **stock** `D2Game.dll` (no hook), so it is
stable for play.

---

## 3. Server stack facts (verified, don't re-derive)

- `D2GS.exe` = **PE32 i386**, pvpgn-magic-builder v2.29, 1.13d; runs under
  Wine (`WINEARCH=win32`) on Ubuntu 24.04.
- Game DLLs = **Blizzard 1.13d** (`D2Game.dll` md5 `3d27210f…`, `D2Common.dll`
  `f453ca2d…`, …). Verified set lives in `gamefiles/` and is md5-checked by
  `scripts/fetch-gamefiles.sh`. Do **not** substitute the retail MPQ set in
  `/home/vtstech/workspace/D2_MPQ` (only `d2exp.mpq` matches).
- `d2server.dll` applies 39 startup patches to D2Game and loads the game DLLs
  itself. **D2Game does not load at its preferred base** — observed at
  `0x024C0000` (preferred `0x6fc20000`). Any injected code must be
  position-independent.
- `.text` in `D2Game.dll` has `VirtualAddress == PointerToRawData == 0x1000`,
  so **file offset == RVA** for that section.
- Free space for injected code: zero-filled run at **RVA/file `0xf7b04`**
  (VA `0x6fd17b04`), 0x8FC bytes.

### 3.1 Key addresses (RVA unless stated)

| Item | Address | Notes |
|---|---|---|
| 0x4F ClickButton handler | `0x6cf60` | `cmp [esp+8],7`; decodes button@+1, pad@+3, page@+5 |
| Hook point | `0x6cf6f` | after the size check; 6 displaced bytes `8B 44 24 04 56 57` |
| Resume | `0x6cf75` | |
| Injected stub | `0xf7b04` | VA `0x6fd17b04` |
| Handler dispatch table | file `0xfa698` | 103 × 8-byte `{fn, flag}`; **0x4F → 0x6fc8cf60** (only entry), 0x34 → 0x6fc8a5f0, 0x50 → 0x6fc8cdd0 |
| `PlayerTrade::ClickButton` | `0x9b8210` | |
| Packet queue `SCMD_QueuePacketForClient` | `0xdb780` | **pClient in EAX**, `ret 8`, 512-byte block chain |
| 0x9D builder | `0xdcdd0`… (`0x6fcfcda0`) | header `[0x9D][action][size][component]` |
| Item-send dispatcher | `0x6fcf0830` | modes 4/5 = page/container |
| `0x38bc0` create unit from record | — | **ESI = record, no stack args**, returns unit in EAX |
| `0x3a000` per-record dispatcher | — | 5 args, `ret 0x14` |

### 3.2 D2Common ordinals (all `__stdcall`, callee cleans)

| Ordinal | Function | Cleanup |
|---|---|---|
| 10120 | `ITEM_GetItemData_eD2ItemFlag` | `ret 4` |
| 10876 | `ITEM_EditItemData_eItemFlag` | `ret 0xC` |
| 10445 | `ITEM_GetContainingInventoryPage` | `ret 4` (page in AL) |
| 10212 | place item `(pInv, pItem, page)` | `ret 0xC` |
| 10312 | post-place adjust `(pInv, pItem, 3\|4)` | `ret 0xC` |
| 10903 | **unlink** `(pInv, pItem)` — slot search + engine mode 1 | `ret 8` |
| 10665 | `SerializeItem` (6 args) | `ret 0x18` |
| 10518 | `STATSEX_GetCurrentStat` | `ret 0xC` |
| 11040 / 10897 | `INVENTORY_GetFirstItem` (validates magic `0x01020304`) / `GetNextItem` | `ret 4` |

Fog (bit-buffer library): `GetAndCreateSavePath` = **10115**; helpers
10126/10127/10129/10130.

### 3.3 D2UnitStrc / SGAMEDATA offsets

`eUnitType` **0x00**, `pInventory` **0x60**. `SGAMEDATA::pMemoryPool` at 0xAC is
**unverified** — an earlier claim was wrong (the site calls `IsBadCodePtr` on a
callback pointer). Do not use it.

---

## 4. The injection layer (worked example, reuse it)

`tools/inject-d2game-hook.py` is the canonical patcher. It:

1. Writes a **position-independent** stub at RVA `0xf7b04`.
2. Patches `0x6cf6f` to `E9 <rel32>` + `0x90`.

Stub contract (all of this was expensive to get right):

```
push ebx / ecx / edx                  ; 12-byte save area
call $+5 ; pop ebx ; sub ebx,8        ; ebx = slide + stub_va   (module is relocated!)
lea edx,[ebx+0x150]                   ; "vpsub.txt" (best-effort beacon)
… CreateFileA / CloseHandle …
lea eax,[ebx+0x100] ; call [ebx+0x1104]  ; LoadLibraryA("d2vpstash.dll")
lea ecx,[ebx+0x120] ; call [ebx+0x11d8]  ; GetProcAddress("VPSHARED_HookPacket@12")
push [esp+0x10]  ; pPacket   <- 12-byte save area is why this is 0x10, not 0x4
push [esp+4]     ; pUnit (EDX)
push [esp+0xC]   ; pGame (ECX)
call eax
add  esp,0xC     ; *** the export is CDECL despite the @12 name ***
test eax,eax ; jne handled
pop edx / ecx / ebx                  ; reverse of push, or EDX/EBX get swapped
mov eax,[esp+4] ; push esi ; push edi ; jmp resume
handled: xor eax,eax ; ret 8
```

IAT RVAs used: `LoadLibraryA 0xf8c08`, `GetProcAddress 0xf8cdc`,
`CreateFileA 0xf8c14`, `CloseHandle 0xf8c8c`.

**Deploy:** `docker cp` the patched `D2Game.dll` + `d2vpstash.dll` into
`/home/d2gs/.wine/drive_c/d2gs/`, then `docker restart d2vp-d2gs-d2gs-1`.
A loaded DLL is cached for the process lifetime — always restart.

**Rollback:** `docker cp gamefiles/D2Game.dll <container>:/home/d2gs/.wine/drive_c/d2gs/D2Game.dll`
+ restart. Stock `D2Game.dll` is the stable configuration.

---

## 5. Current blocker — the hook sees `0x34`, not `0x4F`

The QoL `d2vpstash.dll` writes a diagnostic alphabet to `C:\d2gs\VPLOG.TXT`:

`D` = DLL loaded · `H` = hook entered · `N` = null arg · `X` + 2 hex = wrong
opcode · `S` = serialising · `M` = materialised.

Latest run: **`DHX34`** — entered, arguments non-null, first byte `0x34`.
Meanwhile the crash is unchanged: `EIP 0x6FF5DF6F` (Fog ordinal 10050 +0x33F)
called from `D2COMMON.RVA 0x4704E`, no `[cheat]` line.

What is established:

- Our stub is byte-verified to pass `(pGame=ECX, pUnit=EDX, pPacket=[esp+4])`.
- The dispatch table proves **only 0x4F routes to the patched handler**.
- The client sends `4F <button> <pad> <page>` (captured at R204 as
  `4f 7d 00 00 00 <page> 00`); QoL confirms the send site is unchanged.

So either the handler's buffer has a prefix byte, or the pointer we pass is a
different buffer. **The decisive missing datum is the first 7 bytes at the packet
pointer plus the size**, which the QoL R233 build was supposed to print — that
binary never arrived (newest archive is still v8, sha `75ba03b0…`).

### Next actions for this thread

1. Obtain the real R233/R234 binary (verify sha differs from `75ba03b0…`) and
   read its 7-byte dump.
2. If the dump shows `34 4f 7d …` → the buffer has a prefix; re-derive offsets
   from the handler's own decoding rather than from the capture.
3. If it shows no `4f` nearby → our pointer is wrong; re-derive the stub frame
   offsets against `cmp [esp+8],7` / `mov eax,[esp+4]`, empirically this time
   (a marker-file probe is the cheapest test, but keep it off the production
   container — one probe build caused a restart loop).
4. Discriminating test that needs no new DLL: open the stash **without**
   clicking Shared, check whether `H` appears in `VPLOG.TXT`. `H` without a
   toggle click means we are intercepting stash-open traffic.

### Also open with QoL

- `ARGE5`/`ARG1` of `0x3a000` (stock loader) — not needed if Route B is used.
- `CLIENTS_GetControlUnit` — likely a field read `*(client+0x174)` (client-shaped
  struct, flagged as a callback slot in one place); unverified.
- `GetAccountName` — never pinned; interim pool key is the literal string
  `"smoketest"`, so pools are not yet per-account.
- Item data dwords: `0x3a000` copies `ARG1+0x18/+0x1C` into item data at
  `+0x0C/+0x10` via ordinals 10892/10739. QoL deliberately omits them because
  their `D2UnitStrc` has no item-data pointer at `+0x2C`.

---

## 6. Public deployment (done)

| File | Purpose |
|---|---|
| `docker-compose.prod.yml` | restart always, log rotation, `d2gs-work` volume, 8888 localhost-only |
| `.env.prod.example` | `PUBLIC_IP`, `D2GS_PASSWORD`, sizing |
| `docs/PUBLIC-DEPLOYMENT.md` | topologies, ports, hardening, backups, Pi section |
| `scripts/backup-realm.sh` | tars `pvpgn-var`, `d2gs-work`, `pvpgn-etc` |
| `Dockerfile` + `docker/entrypoint.sh` | self-seeding game dir (`/opt/d2gs-seed`), `WINE` indirection |

Ports: **6112 tcp+udp**, **6113 tcp**, **4000 tcp** public; 8888 localhost only.

`PUBLIC_IP` is what clients are told to use. If LAN clients cannot connect, set
`LAN_NETWORK=192.168.0.0/24` so they receive `LAN_IP` instead.

---

## 7. Raspberry Pi / arm64

`D2GS.exe` is x86; the image needs `wine32:i386`, which has **no ARM64
equivalent**. A Pi can host PvPGN natively; D2GS needs an x86 host **or**
box86 + 32-bit ARM Wine.

Staged (experimental, unverified end-to-end):

- `Dockerfile.arm64` — box86 (armhf, dynarec) + x86 Wine from Kron4ek; reuses the
  same entrypoint via `WINE="box86 /opt/wine/bin/wine"`.
- `docker-compose.arm64.yml` — overlay selecting that Dockerfile.
- **Host prerequisite:** box86 registered with `binfmt_misc`, or Wine cannot
  spawn `wineserver`. `sudo apt install box86 && sudo systemctl restart systemd-binfmt`.
- **Page size:** box86 dynarec needs 4 KiB pages. Pi 5's default
  `kernel_2712.img` uses 16 KiB → `box86 -v` segfaults. Fix:
  `kernel=kernel8.img` in `/boot/firmware/config.txt`, reboot,
  `getconf PAGESIZE` must read `4096`.
- Build takes ~5 min for box86 plus a Wine download; needs several GB free.

Recorded Pi deployment at `192.168.0.15`, `PUBLIC_IP=23.233.xx.xx`.

---

## 8. Runbook

```bash
# local realm status
docker ps --format '{{.Names}} {{.Status}}'
docker exec d2vp-d2gs-d2gs-1 tail -5 /home/d2gs/.wine/drive_c/d2gs/d2gs.log

# apply the hook
python3 tools/inject-d2game-hook.py gamefiles/D2Game.dll /tmp/D2Game.hook.dll
docker cp /tmp/D2Game.hook.dll d2vp-d2gs-d2gs-1:/home/d2gs/.wine/drive_c/d2gs/D2Game.dll
docker cp d2vpstash.dll       d2vp-d2gs-d2gs-1:/home/d2gs/.wine/drive_c/d2gs/
docker restart d2vp-d2gs-d2gs-1

# diagnostics after a click
docker exec d2vp-d2gs-d2gs-1 sh -c 'cd /home/d2gs/.wine/drive_c/d2gs;
  cat VPLOG.TXT; cat vpstash-beacon.txt; ls -la vpstub.txt _d2vp* 2>/dev/null;
  tail -3 D2GS-Hack.txt 2>/dev/null; grep "Error occur" d2gs.log | tail -1'

# crash detail (stack + EIP)
docker exec d2vp-d2gs-d2gs-1 sh -c 'grep -n "EIP =" /home/d2gs/.wine/drive_c/d2gs/D2-*.txt | tail -1'

# revert to stock
docker cp gamefiles/D2Game.dll d2vp-d2gs-d2gs-1:/home/d2gs/.wine/drive_c/d2gs/D2Game.dll
docker restart d2vp-d2gs-d2gs-1
```

Characters: `stashtest` (created after the corruption episode), `vtstech`,
`test`. Saves backed up under `backups/`. Two older characters had damaged
saves from earlier partial runs; opening their stash crashed the server. If a
character starts crashing on stash open, use a fresh one.

---

## 9. Hard-won lessons (avoid repeating these)

1. **Verify address arithmetic against the section table, not by intuition.**
   Three separate bugs came from an off-by-0x1000 between RVA and file offset.
2. **The module is relocated.** Absolute preferred-base addresses inside an
   injected stub are wrong; compute the slide at runtime.
3. **`ret N` in a disassembly is authoritative for calling convention.** The
   export name `@12` lied; the function was cdecl. A 12-byte stack imbalance
   looked like a Fog corruption.
4. **Push offsets shift as you push.** Read the frame into registers first, or
   account for each push.
5. **Restore registers in reverse order.** `pop ebx/ecx/edx` after pushing
   `ebx/ecx/edx` swaps EDX and EBX.
6. **Never trust a logger's "success"** — a `WriteFile` result that is ignored
   produces exactly the "no log, yet it crashed" paradox we chased for hours.
7. **Never deploy an experimental build to the live container without
   assertions** (padding is zero, site bytes are stock, patch applied, stub
   present). One bad probe build caused a restart loop.
8. Old character saves can be corrupted by a crashed stash operation. Test on a
   fresh character before blaming the code.