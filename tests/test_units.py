"""The systemd units' sandbox (2026-11-17's security pass): each directive the pass added is still
there. The values are the ones the units ship with; a change to one is a deliberate edit here too."""
from pathlib import Path

import pytest

UNITS = Path(__file__).resolve().parent.parent / "deploy" / "systemd"
EVERY = {"NoNewPrivileges": "yes", "ProtectSystem": "strict", "ProtectHome": "yes", "PrivateTmp": "yes",
         "ProtectKernelTunables": "yes", "ProtectKernelModules": "yes", "ProtectKernelLogs": "yes",
         "ProtectControlGroups": "yes", "RestrictSUIDSGID": "yes", "LockPersonality": "yes",
         "CapabilityBoundingSet": ""}
APP = {"RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
       "SystemCallFilter": "@system-service", "PrivateDevices": "yes", "RestrictNamespaces": "yes"}


def directives(name):
    out = {}
    for line in (UNITS / name).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "[")) and "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


@pytest.mark.parametrize("name", ["shop-hub.service", "shop-hub-library.service",
                                  "shop-hub-backup.service", "shop-hub-restore-drill.service"])
def test_every_unit_keeps_its_sandbox(name):
    d = directives(name)
    want = dict(EVERY, **(APP if name in ("shop-hub.service", "shop-hub-library.service") else {}))
    missing = {k: v for k, v in want.items() if d.get(k) != v}
    assert not missing, f"{name} lost {missing}"
