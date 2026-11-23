"""Build the NoCloud seed ISO (volume label CIDATA) for the forecast-vm autoinstall.

Usage from the repo root; the output lands on the Alpaca share, which Unraid sees
as /mnt/user/Workfiles/AlpacaBroker/vm/forecast-vm-seed.iso:

    uv run --with pycdlib python infra/forecast-vm/autoinstall/mkseed.py
        infra/forecast-vm/autoinstall
        //HOMENAS/Workfiles/AlpacaBroker/vm/forecast-vm-seed.iso

Attach the ISO as a second vdisk (manual path) next to the Ubuntu server ISO;
the installer finds user-data/meta-data by the label and installs unattended
(it asks "Continue with autoinstall? (yes|no)" once on the console).
"""

import io
import sys
from pathlib import Path

import pycdlib

src, out = Path(sys.argv[1]), Path(sys.argv[2])
iso = pycdlib.PyCdlib()
iso.new(interchange_level=3, joliet=3, rock_ridge="1.09", vol_ident="CIDATA")
for name, iso_name in (("user-data", "/USERDATA.;1"), ("meta-data", "/METADATA.;1")):
    data = (src / name).read_bytes()
    iso.add_fp(
        io.BytesIO(data), len(data), iso_name, rr_name=name, joliet_path="/" + name
    )
iso.write(str(out))
iso.close()
print("written", out, out.stat().st_size)
