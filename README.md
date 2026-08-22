# Slayers Royal English Patch

## TL;DR

1. Provide your own supported `sr.bin` and prepare the exact `sr.cue` described
   below. The required BIN SHA-256 is
   `89760d728f0580dba1c6176f024d3cd6f8fc105b79bd1c27a819208fa0b4d0fe`.
2. Run:

   ```bash
   python3 patch.py --bin "/path/to/sr.bin" --cue "/path/to/sr.cue"
   ```

3. Load `output/sr_patched.cue` in a PlayStation emulator.

This repository patches the Japanese PlayStation release of *Slayers Royal*
(`SLPS-01363`) into English.

It does **not** include the game. You must provide your own matching BIN/CUE
dump of the original disc. The patcher checks the complete source hashes and
refuses incompatible images.

## What You Need

- A legal copy of the Japanese PlayStation game
- A matching raw `MODE2/2352` BIN dump
- Python 3.10 or newer
- About 850 MiB of free disk space
- This complete repository, including every file under `patches/`

No Python packages or external patching programs are required.

## 1. Prepare The Original Files

Name the original files:

```text
sr.bin
sr.cue
```

The supported BIN must be exactly:

| Property | Expected value |
| --- | --- |
| Size | `712300848` bytes |
| SHA-256 | `89760d728f0580dba1c6176f024d3cd6f8fc105b79bd1c27a819208fa0b4d0fe` |

The BIN hash is the authoritative check that you have the supported disc dump.

The required `sr.cue` is 68 bytes, uses CRLF line endings, and contains:

```cue
FILE "sr.bin" BINARY
  TRACK 01 MODE2/2352
    INDEX 01 00:00:00
```

Its SHA-256 is:

```text
0f93f45114b7fc88b8f57c5449af0828e59699a8780849540a670df7c3a0aa08
```

If your dumping software created an equivalent CUE with a different BIN
filename or line endings, place `sr.bin` in a working directory and generate
the exact supported CUE there.

Linux or macOS:

```bash
python3 -c 'from pathlib import Path; Path("sr.cue").write_bytes(b"FILE \"sr.bin\" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n")'
```

Windows:

```powershell
$cue = "FILE `"sr.bin`" BINARY`r`n  TRACK 01 MODE2/2352`r`n    INDEX 01 00:00:00`r`n"
[System.IO.File]::WriteAllBytes("sr.cue", [System.Text.Encoding]::ASCII.GetBytes($cue))
```

Creating this descriptor does not modify the BIN or any game data.

## 2. Verify The Source

From the repository directory, run the patcher's verification mode.

Linux or macOS:

```bash
python3 patch.py \
  --bin "/path/to/sr.bin" \
  --cue "/path/to/sr.cue" \
  --verify-only
```

Windows:

```powershell
py -3 patch.py --bin "C:\path\to\sr.bin" --cue "C:\path\to\sr.cue" --verify-only
```

Successful verification ends with:

```text
source and patch files are valid
```

If verification reports a source hash mismatch, do not continue. Redump the
disc or correct the CUE descriptor. A different game revision cannot be safely
patched.

## 3. Apply The Patch

Linux or macOS:

```bash
python3 patch.py \
  --bin "/path/to/sr.bin" \
  --cue "/path/to/sr.cue"
```

Windows:

```powershell
py -3 patch.py --bin "C:\path\to\sr.bin" --cue "C:\path\to\sr.cue"
```

By default, the patcher creates an `output/` directory beside `patch.py`. To
choose another directory, add:

```text
--output-dir "/path/to/output"
```

The patcher never modifies the original files. Existing output files are not
replaced unless you add `--force`.

## 4. Run The Patched Game

A successful patch creates:

```text
output/
  sr_patched.bin
  sr_patched.cue
```

Expected results:

| File | Size | SHA-256 |
| --- | ---: | --- |
| `sr_patched.bin` | 712,300,848 bytes | `fcba9a0eddc1094eadddbf83fe98927e564aae83d71a46ff1f2148cd6528d1f4` |
| `sr_patched.cue` | 76 bytes | `c5384aae77bd17955acb4559d92422f7e11057619e83f55f7726fd975f5bfc84` |

Load `sr_patched.cue`, not the BIN directly, in a PlayStation emulator.

This release also completes the map-label pass: all 160 area placards, all 30
overworld destination names, all 24 cursor-dependent map labels, and the three
overworld route subtitles (`AREA`, `ROAD`, and `BACK RD`) are translated.

It also fixes the Sonia City inn transition. The translated scene now remains
inside its original runtime allocation instead of overwriting the adjacent inn
resource, so loading the second save and choosing Move → Inn no longer hangs.

Shop coverage now includes the tavern fallback responses, armory and item-shop
BUY/SELL menus, comparison statistics, equipment and item catalogs, buyer
names, and merchant dialogue throughout the game. The runtime font allocations
for these menus are isolated from the map-label font pool, preventing the
corrupted buyer names seen in the earlier build. Item selections use the
original 16-pixel cursor advance and one ordinary letter per cell; repeated
catalog strings share read-only records so every label fits without overlapping
or compressed multi-letter glyphs.

Battle coverage now includes all 57 spell-name catalog records, 95 fixed
spell/action help records across 164 description pages, SELECT-hover help,
command and status sprites, battle cards, and character-status panels. The
battle-resident font also carries the English SAVE/LOAD, SLOT, and compact
location-name cells used when the save browser is opened during combat, rather
than falling back to Japanese glyphs.

The character-status pass covers all seven party members and every three-trait
panel, including Zelgadis, Amelia, Sylphiel, and Lark. Amelia's original
`AMERIA` side nameplate is corrected to `AMELIA`.

### Optional PlayStation Mouse controls

The patched game keeps the ordinary digital controller on port 1 and also
accepts a PlayStation Mouse on port 2. Relative mouse motion acts like the
directional pad, the left button is Circle/confirm, and the right button is
Cross/cancel. Controller input continues to work normally while the mouse is
connected.

On original hardware, connect an SCPH-1030-compatible mouse to controller port
2. In an emulator, leave port 1 as a digital controller and configure port 2 as
a PlayStation Mouse. Emulators that do not expose a PS Mouse device can still
run the patch with the normal controller.

## Troubleshooting

### `source BIN is not the supported source file`

The BIN is a different revision, dump format, or incomplete copy. The patch
requires the exact 712,300,848-byte `MODE2/2352` image listed above.

### `source CUE is not the supported source file`

The CUE probably has a different referenced filename or line-ending format.
Regenerate the 68-byte CUE using the command in step 1.

### `patch part ... does not exist` or a patch hash mismatch

The repository download is incomplete or corrupted. Download the complete
repository again. All numbered files under `patches/` are required.

### `output already exists`

Move the previous output elsewhere, choose another `--output-dir`, or rerun
with `--force` if replacing it is intentional.

### `not enough free space`

Choose an output directory on a filesystem with at least 850 MiB free.

## How The Patch Works

The project-specific `SLRXOR1` format compares the source and translated files
in 64 KiB blocks. It stores only nonzero `source XOR target` blocks, compresses
the container with XZ, and splits it into files below GitHub's large-file
warning threshold.

Applying the patch begins with the verified source and XORs the stored changes
back into their original offsets. The patch data does not contain a playable
disc image, and it cannot reconstruct the translated game without the matching
source files.

The machine-readable source, patch-part, and output hashes are recorded in
`release_manifest.json`.

## Maintainer Rebuild

This section is for translation maintainers, not ordinary users.

With the repository at `royal1/slayers_royal_1/`, the original files at
`royal1/sr.bin` and `royal1/sr.cue`, and canonical translated files under
`royal1/patched/`, regenerate the release with:

```bash
python3 royal1/slayers_royal_1/maintainer/build_release.py
```

Use `--help` to override those paths. Building the highly compressed release
requires approximately 1 GiB of RAM. Always run the consumer patcher afterward
and byte-compare its output with the canonical BIN/CUE before publishing.

## Legal Notice

The game, characters, audiovisual material, and other original assets remain
the property of their respective rights holders. This repository distributes
only patching code and source-dependent binary differences.
