---
name: igs-cddis-api-download
description: Acquire and download public IGS GNSS ground-station RINEX/CRINEX data from NASA CDDIS with Earthdata Login credentials, including high-rate 1 Hz 15-minute files and daily 30 s files.
---

# IGS / CDDIS Data Download Skill

Use this skill when the task needs NASA CDDIS / IGS ground-station data discovery or download, especially RINEX/CRINEX observation files for GNSS CNR/C/N0 analysis.

## Security Rules

- Never hard-code Earthdata usernames, passwords, SSH passwords, API tokens, or keys into generated scripts, reports, slides, or manifests.
- Use one of these credential paths:
  - Existing `~/.netrc` with `urs.earthdata.nasa.gov` credentials.
  - Environment variables: `EARTHDATA_USERNAME` and `EARTHDATA_PASSWORD`.
  - Environment variables: `NASA_EARTHDATA_USERNAME` and `NASA_EARTHDATA_PASSWORD`.
- Do not print passwords. Do not include credentials in command logs.
- If a `.netrc` needs to be created, use the helper command below, which writes only to the user profile or an explicitly provided `--netrc` path.

## Data Products

The helper script supports two CDDIS IGS observation products:

- `highrate`: 1 Hz, 15-minute station files, usually named like `STATION_R_YYYYDDDHHMM_15M_01S_MO.crx.gz`.
- `daily`: daily 30 s station files, usually named like `STATION_R_YYYYDDD0000_01D_30S_MO.crx.gz`.

The RINEX `S*` observation fields are C/N0-like carrier-to-noise density observables. In reports and figures, call them `CNR` or `C/N0`, with units `dB-Hz`; internal tables may still contain legacy column names like `snr_p10`.

## Main Tool

Use:

```powershell
python .codex/skills/igs-cddis-api-download/scripts/igs_cddis_api.py --help
```

If using the bundled Codex Python runtime:

```powershell
& 'C:\Users\NaNF2P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex\skills\igs-cddis-api-download\scripts\igs_cddis_api.py --help
```

## Configure Earthdata Credentials

Preferred: keep credentials in environment variables for the current shell:

```powershell
$env:EARTHDATA_USERNAME="YOUR_USERNAME"
$env:EARTHDATA_PASSWORD="YOUR_PASSWORD"
```

Then create or update `.netrc`:

```powershell
& 'C:\Users\NaNF2P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex\skills\igs-cddis-api-download\scripts\igs_cddis_api.py configure-netrc
```

This writes entries for:

- `urs.earthdata.nasa.gov`
- `cddis.nasa.gov`

## List Available Files

High-rate example:

```powershell
& 'C:\Users\NaNF2P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex\skills\igs-cddis-api-download\scripts\igs_cddis_api.py list --product highrate --dates 2026-02-27,2026-02-28 --hours 03-12 --stations BSHM00ISR,NICO00CYP,DJIG00DJI --base public_data_chasing_lightning --out-name hormuz_probe
```

Daily example:

```powershell
& 'C:\Users\NaNF2P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex\skills\igs-cddis-api-download\scripts\igs_cddis_api.py list --product daily --dates 2024-05-08,2024-05-09,2024-05-10 --stations BOGI00POL,SULP00UKR,JOZE00POL --base public_data_chasing_lightning --out-name kharkiv_daily_probe
```

## Download Files

High-rate download:

```powershell
& 'C:\Users\NaNF2P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex\skills\igs-cddis-api-download\scripts\igs_cddis_api.py download --product highrate --dates 2026-02-27,2026-02-28 --hours 03-12 --stations BSHM00ISR,NICO00CYP,DJIG00DJI --base public_data_chasing_lightning --out-name hormuz_highrate --jobs 8
```

Daily download:

```powershell
& 'C:\Users\NaNF2P\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' .codex\skills\igs-cddis-api-download\scripts\igs_cddis_api.py download --product daily --dates 2024-05-08,2024-05-09,2024-05-10 --stations BOGI00POL,SULP00UKR,JOZE00POL --base public_data_chasing_lightning --out-name kharkiv_daily --jobs 8
```

## Output Layout

By default, downloaded files go under:

- `public_data_chasing_lightning/raw/cddis_highrate/YYYY/DDD/HH/*.crx.gz`
- `public_data_chasing_lightning/raw/cddis_daily/YYYY/DDD/*.crx.gz`

Metadata and manifests go under:

- `public_data_chasing_lightning/metadata/<out-name>/index_inventory.tsv`
- `public_data_chasing_lightning/metadata/<out-name>/manifest.tsv`

The downloader validates `.gz` files after download and records one of:

- `downloaded`
- `skip_verified`
- `failed`
- `bad_gzip`

## Recommended Workflow

1. Confirm credentials are available via `.netrc` or environment variables.
2. Run `list` first for a small station/date/hour set.
3. Inspect `index_inventory.tsv` to confirm CDDIS has matching files.
4. Run `download`.
5. Use the manifest as the handoff to downstream RINEX parsing and signal-processing modules.

