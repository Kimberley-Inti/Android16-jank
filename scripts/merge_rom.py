#!/usr/bin/env python3
"""Restore an unchanged ROM ZIP from checked Release parts; never flash a device."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path,
                        default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    directory = args.directory.resolve()
    manifest = json.loads((directory / "release-assets.json").read_text(encoding="utf-8"))
    rom = manifest["rom"]
    def local_file(name):
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("Asset name must be a plain filename")
        return directory / name
    target = local_file(rom["name"])
    temporary = target.with_name(target.name + ".partial")
    if target.exists() or temporary.exists():
        raise SystemExit("Output or partial file exists; keep/check it before running again.")
    # Check availability and lengths before creating output.
    for part in rom["parts"]:
        source = local_file(part["name"])
        if source.stat().st_size != part["size"]:
            raise SystemExit("Part size mismatch: " + source.name)
    digest = hashlib.sha256()
    total = 0
    with temporary.open("xb") as output:
        for part in rom["parts"]:
            part_digest = hashlib.sha256()
            source = local_file(part["name"])
            print("Reading", source.name, flush=True)
            with source.open("rb") as stream:
                while block := stream.read(8 * 1024 * 1024):
                    part_digest.update(block)
                    digest.update(block)
                    total += len(block)
                    output.write(block)
            if part_digest.hexdigest() != part["sha256"]:
                raise SystemExit("Part SHA-256 mismatch; partial output retained: " + source.name)
    if total != rom["size"] or digest.hexdigest() != rom["sha256"]:
        raise SystemExit("Complete ROM verification failed; partial output retained.")
    temporary.rename(target)
    print("Verified ROM:", target)
    print("SHA-256:", digest.hexdigest())
    print("Follow the device-specific installation guide. No flashing was performed.")


if __name__ == "__main__":
    main()
