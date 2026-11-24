#!/usr/bin/env python3
"""Inject the D2VP shared-stash hook stub into a stock D2Game.dll.

EXPERIMENTAL - not production ready. The hook target (d2vpstash.dll) is still
being stabilised; the stub here is the position-independent loader that calls it.

What it does
------------
1. Finds the 0x4F ClickButton handler in D2Game.dll (1.13d) and replaces the
   first instructions after the size check with `jmp <stub>`.
2. Writes a position-independent stub into zero padding at the end of .text.
   The stub resolves LoadLibraryA/GetProcAddress through D2Game's own IAT using
   a runtime-computed module slide, so it works regardless of where the DLL is
   loaded (D2GS loads it at a relocated base, not its preferred 0x6fc20000).
3. No relocation table changes are needed: every reference is slide-relative.

The stub calls `VPSHARED_HookPacket@12` (pGame, pUnit, pPacket) and falls
through to the original handler when the DLL is missing or returns 0.

Usage
-----
    tools/inject-d2game-hook.py stock/D2Game.dll D2Game.patched.dll
    tools/inject-d2game-hook.py --check D2Game.patched.dll
"""

import argparse
import struct
import sys

PREFERRED_BASE = 0x6FC20000
SITE_RVA = 0x6CF6F          # 0x4F handler, right after `cmp [esp+8],7`
RESUME_RVA = 0x6CF75        # continuation after the displaced instructions
STUB_RVA = 0xF7B04          # zero padding at the end of .text (0x8FC bytes free)
DEFAULT_DLL = b"d2vpstash.dll"
DEFAULT_PROC = b"VPSHARED_HookPacket@12"

# IAT slots inside D2Game.dll (RVAs), resolved from its import descriptors.
IAT = {
    "LoadLibraryA":   0xF8C08,
    "GetProcAddress": 0xF8CDC,
    "CreateFileA":    0xF8C14,
    "CloseHandle":    0xF8C8C,
}


def _u32(b, v):
    b.extend(struct.pack("<I", v))


def _i32(b, v):
    b.extend(struct.pack("<i", v))


def build_stub(stub_va, dll_name, proc_name, iat_rva_lla, iat_rva_gpa,
               iat_rva_cfa, iat_rva_ch):
    """Position-independent stub. Prefixed RVAs are stub-relative."""
    b = bytearray()
    b += b"\x53\x51\x52"                      # push ebx/ecx/edx
    call_at = len(b)
    b += b"\xE8" + b"\x00\x00\x00\x00"        # call $+5
    anchor_off = len(b)
    b += b"\x5B"                              # pop ebx
    sub_at = len(b)
    b += b"\x81\xEB" + b"\x00\x00\x00\x00"    # sub ebx, <va of 'pop ebx'>

    def lea(reg_modrm, target_rva):
        b.extend(b"\x8D" + bytes([reg_modrm]) + struct.pack("<I", target_rva - stub_va))

    def call_iat(reg_modrm, rva):
        # mov <reg>,[ebx + (rva - stub_va)] ; call <reg>
        b.extend(b"\x8B" + bytes([reg_modrm]) + struct.pack("<I", rva - stub_va))
        b.extend(b"\xFF\xD2")                 # call edx

    # Optional beacon: create vpstub.txt so stub execution is externally visible.
    beacon_rva = stub_va + 0x150
    lea(0x93, beacon_rva)                     # lea edx,[ebx+disp]
    b += b"\x68" + struct.pack("<I", 0)       # hTemplate
    b += b"\x68" + struct.pack("<I", 0x80)    # attributes
    b += b"\x68" + struct.pack("<I", 2)       # CREATE_ALWAYS
    b += b"\x68" + struct.pack("<I", 0)       # security
    b += b"\x68" + struct.pack("<I", 3)       # share
    b += b"\x68" + struct.pack("<I", 0x40000000)  # GENERIC_WRITE
    b += b"\x52"                              # push edx (path)
    call_iat(0x93, iat_rva_cfa)               # CreateFileA
    b += b"\x83\xF8\xFF"                      # cmp eax,-1
    j1 = len(b)
    b += b"\x74\x00"
    b += b"\x50"
    call_iat(0x93, iat_rva_ch)                # CloseHandle
    skip_cfa = len(b)

    lea(0x83, stub_va + 0x100)                # lea eax,[ebx+disp] -> dll name
    b += b"\x50"                              # push name
    call_iat(0x93, iat_rva_lla)               # LoadLibraryA
    b += b"\x83\xC4\x04\x85\xC0"
    j2 = len(b)
    b += b"\x74\x00"
    proc_rva = stub_va + 0x120
    lea(0x8B, proc_rva)                       # lea ecx,[ebx+disp]
    b += b"\x51\x50"                          # push name ; push hmod
    call_iat(0x93, iat_rva_gpa)               # GetProcAddress
    b += b"\x83\xC4\x08\x85\xC0"
    j3 = len(b)
    b += b"\x74\x00"
    b += b"\xFF\x74\x24\x10"                  # push pPacket
    b += b"\xFF\x74\x24\x04"                  # push pUnit
    b += b"\xFF\x74\x24\x08"                  # push pGame
    b += b"\xFF\xD0"                          # call eax
    b += b"\x85\xC0"
    j4 = len(b)
    b += b"\x75\x00"
    fail = len(b)
    b += b"\x33\xC0"                          # xor eax,eax  (not handled)
    replay = len(b)
    b += b"\x5B\x59\x5A"                      # pop ebx/ecx/edx
    b += b"\x8B\x44\x24\x04\x56\x57"          # mov eax,[esp+4]; push esi/edi
    b += b"\xE9"
    b += struct.pack("<i", (PREFERRED_BASE + RESUME_RVA) - (stub_va + len(b) + 4))
    handled = len(b)
    b += b"\x5B\x59\x5A"                      # restore regs
    b += b"\x33\xC0\xC2\x08\x00"              # xor eax,eax ; ret 8

    for j, target in ((j1, skip_cfa), (j2, fail), (j3, fail), (j4, handled)):
        struct.pack_into("<b", b, j + 1, target - (j + 2))
    struct.pack_into("<i", b, call_at + 1, 0)
    struct.pack_into("<I", b, sub_at + 2, stub_va + anchor_off)
    return bytes(b)


def build_payload(stub_va, dll_name, proc_name):
    stub = build_stub(stub_va, dll_name, proc_name,
                      PREFERRED_BASE + IAT["LoadLibraryA"],
                      PREFERRED_BASE + IAT["GetProcAddress"],
                      PREFERRED_BASE + IAT["CreateFileA"],
                      PREFERRED_BASE + IAT["CloseHandle"])
    payload = bytearray(stub)
    payload += b"\x00" * ((stub_va + 0x100) - (stub_va + len(payload))) + dll_name + b"\x00"
    payload += b"\x00" * ((stub_va + 0x120) - (stub_va + len(payload))) + proc_name + b"\x00"
    payload += b"\x00" * ((stub_va + 0x150) - (stub_va + len(payload))) + b"vpstub.txt\x00"
    return stub, bytes(payload)


def inject(src, dst, dll_name, proc_name):
    data = bytearray(open(src, "rb").read())
    site = SITE_RVA
    if bytes(data[site:site + 3]) != b"\x8B\x44\x24":
        sys.exit(f"unexpected bytes at site {SITE_RVA:#x}: {data[site:site+6].hex()}")
    stub_va = PREFERRED_BASE + STUB_RVA
    stub, payload = build_payload(stub_va, dll_name, proc_name)
    if len(payload) > 1276:
        sys.exit(f"stub too large: {len(payload)} bytes")
    if any(data[STUB_RVA:STUB_RVA + len(payload)]):
        sys.exit("target padding is not zero - refusing to overwrite")
    data[STUB_RVA:STUB_RVA + len(payload)] = payload
    data[site:site + 6] = b"\xE9" + struct.pack(
        "<i", stub_va - (PREFERRED_BASE + SITE_RVA + 5)) + b"\x90"
    open(dst, "wb").write(data)
    print(f"wrote {dst}")
    print(f"  site  {SITE_RVA:#x}: {data[site:site+6].hex()}  (jmp {stub_va:#x})")
    print(f"  stub  {STUB_RVA:#x}: {len(stub)} bytes + {len(payload)-len(stub)} data")


def check(path):
    data = open(path, "rb").read()
    site = data[SITE_RVA:SITE_RVA + 6]
    print(f"site bytes: {site.hex()}")
    if site[:1] == b"\xE9":
        target = PREFERRED_BASE + SITE_RVA + 5 + struct.unpack("<i", site[1:5])[0]
        print(f"patched -> stub at {target:#x}")
        off = target - PREFERRED_BASE
        print(f"stub head: {data[off:off+8].hex()}")
    else:
        print("stock (unpatched)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("src")
    ap.add_argument("dst", nargs="?")
    ap.add_argument("--dll-name", default=DEFAULT_DLL.decode())
    ap.add_argument("--proc-name", default=DEFAULT_PROC.decode())
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        check(a.src)
        return
    if not a.dst:
        ap.error("dst is required unless --check is used")
    inject(a.src, a.dst, a.dll_name.encode(), a.proc_name.encode())


if __name__ == "__main__":
    main()
