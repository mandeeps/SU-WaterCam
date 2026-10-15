# Releases and node updates

Nodes run **releases**: git tags on `main` that have passed the bench checks below.
That gives every node a known-good version to go back to, and the dashboard shows
which nodes aren't on the current one.

## Version numbers

CalVer: `vYYYY.MM.N`, for example `v2026.10.0`, then `v2026.10.1` for the next release
that month. `N` starts at 0 each month.

A node reports what `git describe` says about its checkout
(`tools/node_version.py`):

| Node reports | Meaning |
|---|---|
| `v2026.10.1` | exactly the release |
| `v2026.10.1-3-gabc12345` | 3 commits past it: not a release |
| `v2026.10.1-dirty` | the release with local edits (005's no-shutdown patch, for one) |
| `abc12345` | no release tag reachable |

It also reports the segmentation model it loaded: the first 8 hex digits of the ONNX
file's SHA-256, written by `segformer_daemon` to `/run/segformer/model.json`. A model
can change with no git change at all, as on 006 with the `segformer_type_pool` test
model, so this is reported separately from the software version.

`python -m tools.node_version` prints both. So does `tools/initial_health_check.py`.

### On the wire

| Channel | Field | Value (u32) | Sent over |
|---|---|---|---|
| `01 09` | `software_version` | `(year-2000)<<24 \| month<<16 \| patch<<8 \| flags` | LoRa, IP |
| `01 0A` | `software_commit` | first 8 hex digits of the commit | IP |
| `01 0B` | `model_id` | first 8 hex digits of the model's SHA-256 | LoRa, IP |
| `01 0C` | `sf_dropped` | store-and-forward entries (LoRa + IP queues) dropped unsent for age or depth, since install (`tools/drop_counter.py`; debug status key `sd`) | LoRa, IP |
| `01 0D` | `clock_unverified` | u8, 1 if the capture was taken before the clock was verified by network, GPS or server time (`tools/capture_time.py`) | LoRa, IP |

Flags: bit 0 dirty, bit 1 past the tag, bit 2 no release tag (the version bytes are
then 0). Over LoRa the fields go at the end of the packet, only when they change and
in the periodic full send. LoRa status replies (`dbg`) carry `"v"` after `"em"`.

## Making a release

1. CI is green on the commit to release.
2. **Bench checks:**
   - a wake test on 005, with shutdown intercepted (`~/test_wake.sh`);
   - for changes to waking, power, transport or the capture pipeline, a run of real
     scheduled wakes on 006 over WiFi and cellular, as on 2026-10-11.
3. **Security patches**, on the bench over WiFi (nodes don't run
   `unattended-upgrades`, which would download over cellular): `sudo apt update &&
   sudo apt full-upgrade` on 005 and 006 before their bench checks, so the release is
   tested on the packages it will run with. Tailscale is the one internet-facing
   component; check `tailscale version` against its changelog's security notes. Also
   `venv/bin/pip install -U pip` (the venv's pip is not an apt package). The release
   notes say which packages to bring units to.

   Then record each unit as tested: `python -m tools.node_inventory --mdot-version <ATI>`
   writes a CycloneDX SBOM of its packages, release, model and firmware. Scan it
   (`grype sbom:inventory-<host>-<date>.cdx.json`), read anything critical or high that
   has a fix, and add the files to WaterCam-Team/documentation under `inventories/su-watercam-<tag>/` with a short README of the scan result. Not to the release: releases are immutable once published, so nothing can be attached afterwards. Not to this repo either: every node downloads its files on each update. The same command on a field unit
   answers "is this one affected?" when an advisory comes out.
4. If the release adds an uplink channel, deploy the API (and paste the ChirpStack codec)
   **before** any node runs it: the API rejects an IP uplink with a channel it doesn't
   know.
5. Tag **signed**, then publish, with notes covering the changes since the last release,
   the deploy order, the pickle's SHA-256 and where the inventories are. Releases are
   immutable: once published, files can't be added (the notes can still be edited):
   ```bash
   git tag -s v2026.10.0 -m "v2026.10.0" <commit>     # signed with your release key (below)
   git push origin v2026.10.0
   gh release create v2026.10.0 --title v2026.10.0 --notes-file notes.md
   ```
6. Update the nodes (below) and check the dashboard shows the new version for each.

### Release signing

Nodes only check out a tag signed by a release key, so a node installs what a
maintainer released and nothing else, even if the GitHub repo were tampered with.
Tags are signed with an SSH key (git 2.34 or newer; no GPG needed).

- **A maintainer's release key**, set up once on their own machine:
  ```bash
  ssh-keygen -t ed25519 -C "watercam-release <you>" -f ~/.ssh/watercam_release
  git config --global gpg.format ssh
  git config --global user.signingkey ~/.ssh/watercam_release.pub
  ```
  Keep the private key off nodes and servers, with a passphrase.
- **The allowed signers** are listed in `config/release_signers`, one line per
  maintainer: `watercam-releases <the .pub file's key type and key>`. Public keys
  only, so the file is safe in this public repo.
- **On each node** the list is installed outside the checkout, so an update can't
  change whose signatures the node trusts (guide step 1.3.1):
  ```bash
  sudo install -D -m 644 config/release_signers /etc/watercam/release_signers
  git config --global gpg.format ssh
  git config --global gpg.ssh.allowedSignersFile /etc/watercam/release_signers
  ```
  Adding or removing a maintainer means updating `/etc/watercam/release_signers` on
  each node by hand, deliberately.

## Updating a node

Nodes keep a **shallow** checkout: only the release they run, not the history. The
history is about 330 MB (old TensorFlow examples and models, long since deleted), and
the files of a release are under 1 MB. Images and PDFs are in Git LFS, and nodes don't
install `git-lfs`, so they only ever hold the 131-byte pointers.

From `/home/pi/SU-WaterCam`:

```bash
git fetch --depth 1 origin tag v2026.10.1
git verify-tag v2026.10.1 && git checkout v2026.10.1     # stop if it doesn't verify
sha256sum output/ticktalk_main.pickle && cat output/ticktalk_main.pickle.sha256   # must match
sudo systemctl restart lora_daemon segformer_daemon
```

`git checkout` of a tag leaves a detached HEAD; that is expected. Local edits that don't
conflict are kept. For 005, save the patch, `git checkout -- ticktalk_main.py`, update,
then `git apply --3way` the patch as before.

If the release changes anything outside the checkout, the release notes say so:
systemd units, `doas.conf`, Witty Pi scripts or pip packages.

**Going back** to a known-good release is the same, with the older tag:
`git fetch --depth 1 origin tag v2026.10.0 && git verify-tag v2026.10.0 && git checkout v2026.10.0`.

`git verify-tag` prints `Good "git" signature for watercam-releases` for a tag signed
by a listed key, and fails for an unsigned tag or another key; don't check that one
out. Tested 2026-10-12 with a shallow fetch of the tag alone, as above.

Each update keeps the release it replaced, which costs only the files that changed.
Once a year or so, `git tag -d` the old ones and run `git gc --prune=now`.

### Converting a full clone to a shallow one (once per node)

Existing nodes have the full history in `.git` (about 340 MB). Swapping in a shallow
`.git` leaves the working files, untracked files (`images/`, `venv/`,
`runtime_config.json`, calibration files) and local edits as they are:

1. Bring the node to the release with its old clone first (`git pull`, or `git fetch` and
   `git checkout <tag>`), so the working files already match.
2. Swap the `.git` directory:
   ```bash
   cd /home/pi
   git clone --depth 1 --no-checkout --branch v2026.10.0 \
       https://github.com/mandeeps/SU-WaterCam.git swc-shallow
   mv SU-WaterCam/.git SU-WaterCam.git-full-backup
   mv swc-shallow/.git SU-WaterCam/.git && rmdir swc-shallow
   cd SU-WaterCam && git reset -q
   git status --short     # only local edits and untracked files
   ```
3. Once the node has run a cycle on it, `rm -rf ~/SU-WaterCam.git-full-backup`.

On 005 (2026-10-11) the swap took under a second, `.git` went from 343 MB to 1.1 MB,
and `git status` was identical before and after (its local edit and untracked files
kept).
