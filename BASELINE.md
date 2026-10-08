# OnePlus 11 Android 16 baseline

Device: OnePlus 11 / salami
Source branch: lineage-23.2
Android version: 16
Build variant: userdebug
Successful baseline Slurm job: 22273

## Records

- source-manifest.xml: pinned source repository revisions.
- frameworks-base-before.txt: frameworks/base commit before probe changes.
- SHA256SUMS.txt: baseline ZIP and flashing image checksums.
- input-SHA256SUMS.txt: container, extraction ZIP and original Java checksums.
- scripts/: baseline build scripts.

## Recovery

1. Prepare source using the pinned manifest.
2. Restore device dependencies and extract matching proprietary files.
3. Restore Choreographer.java from the recorded frameworks/base commit.
4. Apply the probe patch only for a probe build.
5. Adjust machine paths and submit the build script.

Keep the prepared container and extraction ZIP separately.
The manifest does not include extracted vendor files.
ROM packages and proprietary files are not stored in this repository.
