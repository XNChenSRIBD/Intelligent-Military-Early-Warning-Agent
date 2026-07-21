#!/usr/bin/env python3
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import getpass
import gzip
import os
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


HIGH_RATE_RE = re.compile(r"[A-Z0-9]{9}_[RS]_[0-9]{11}_15M_01S_MO\.crx\.gz", re.I)
DAILY_RE = re.compile(r"[A-Z0-9]{9}_[RS]_[0-9]{11}_01D_30S_MO\.crx\.gz", re.I)


@dataclass(frozen=True)
class DateSpec:
    year: int
    doy: str


@dataclass(frozen=True)
class IndexSpec:
    product: str
    year: int
    doy: str
    hour: str | None


@dataclass(frozen=True)
class DownloadTask:
    url: str
    target: Path
    station: str
    product: str
    year: int
    doy: str
    hour: str


def parse_date_token(token: str) -> DateSpec:
    token = token.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", token):
        d = dt.date.fromisoformat(token)
        return DateSpec(d.year, f"{d.timetuple().tm_yday:03d}")
    if re.fullmatch(r"\d{4}:\d{3}", token):
        year, doy = token.split(":")
        return DateSpec(int(year), f"{int(doy):03d}")
    raise ValueError(f"Unsupported date token: {token!r}; use YYYY-MM-DD or YYYY:DDD")


def parse_dates(text: str) -> list[DateSpec]:
    dates = [parse_date_token(part) for part in text.split(",") if part.strip()]
    return sorted(set(dates), key=lambda d: (d.year, d.doy))


def parse_hours(text: str) -> list[str]:
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = part.split("-", 1)
            out.update(range(int(start), int(end) + 1))
        else:
            out.add(int(part))
    return [f"{h:02d}" for h in sorted(out) if 0 <= h <= 23]


def parse_stations(text: str) -> set[str]:
    return {part.strip().upper() for part in text.split(",") if part.strip()}


def product_out_root(base: Path, product: str) -> Path:
    return base / "raw" / ("cddis_highrate" if product == "highrate" else "cddis_daily")


def index_url(spec: IndexSpec) -> str:
    yy = str(spec.year)[2:]
    if spec.product == "highrate":
        if spec.hour is None:
            raise ValueError("highrate index requires hour")
        return f"https://cddis.nasa.gov/archive/gnss/data/highrate/{spec.year}/{spec.doy}/{yy}d/{spec.hour}/"
    if spec.product == "daily":
        return f"https://cddis.nasa.gov/archive/gnss/data/daily/{spec.year}/{spec.doy}/{yy}d/"
    raise ValueError(f"Unsupported product: {spec.product}")


def index_out_dir(base: Path, spec: IndexSpec) -> Path:
    root = product_out_root(base, spec.product)
    if spec.product == "highrate":
        return root / str(spec.year) / spec.doy / str(spec.hour)
    return root / str(spec.year) / spec.doy


def target_path(base: Path, spec: IndexSpec, name: str) -> Path:
    return index_out_dir(base, spec) / name


def filename_re(product: str) -> re.Pattern[str]:
    return HIGH_RATE_RE if product == "highrate" else DAILY_RE


def require_curl() -> str:
    exe = shutil.which("curl")
    if not exe:
        raise SystemExit("curl was not found on PATH; CDDIS Earthdata redirects are handled through curl -n.")
    return exe


def default_netrc() -> Path:
    return Path.home() / ".netrc"


def write_netrc(path: Path, username: str, password: str) -> None:
    if not username or not password:
        raise SystemExit("Missing Earthdata username/password.")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "machine urs.earthdata.nasa.gov",
        f"  login {username}",
        f"  password {password}",
        "machine cddis.nasa.gov",
        f"  login {username}",
        f"  password {password}",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        # Windows may ignore POSIX chmod; curl can still read the file.
        pass


def command_configure_netrc(args: argparse.Namespace) -> None:
    username = args.username or os.environ.get("EARTHDATA_USERNAME") or os.environ.get("NASA_EARTHDATA_USERNAME")
    password = args.password or os.environ.get("EARTHDATA_PASSWORD") or os.environ.get("NASA_EARTHDATA_PASSWORD")
    if not username:
        username = input("Earthdata username: ").strip()
    if not password:
        password = getpass.getpass("Earthdata password: ")
    netrc_path = Path(args.netrc).expanduser()
    write_netrc(netrc_path, username, password)
    print(f"wrote_netrc={netrc_path}")
    print("credential_hosts=urs.earthdata.nasa.gov,cddis.nasa.gov")


def curl_get(url: str, out_path: Path, cookie_file: Path, timeout: int, netrc_path: Path | None) -> subprocess.CompletedProcess[str]:
    require_curl()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "curl",
        "-n",
        "-c",
        str(cookie_file),
        "-b",
        str(cookie_file),
        "-L",
        "--fail",
        "-sS",
        "--retry",
        "5",
        "--retry-all-errors",
        "--retry-delay",
        "5",
        "--connect-timeout",
        "30",
        "--max-time",
        str(timeout),
        "-o",
        str(out_path),
        url,
    ]
    env = os.environ.copy()
    if netrc_path:
        # curl's NETRC env var is not portable, so use --netrc-file when explicit.
        cmd.insert(2, str(netrc_path))
        cmd.insert(2, "--netrc-file")
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)


def gzip_ok(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with gzip.open(path, "rb") as fh:
            while fh.read(1024 * 1024):
                pass
        return True
    except Exception:
        return False


def collect_index_specs(product: str, dates: list[DateSpec], hours: list[str]) -> list[IndexSpec]:
    specs: list[IndexSpec] = []
    for d in dates:
        if product == "highrate":
            specs.extend(IndexSpec(product, d.year, d.doy, h) for h in hours)
        else:
            specs.append(IndexSpec(product, d.year, d.doy, None))
    return specs


def fetch_index(spec: IndexSpec, base: Path, cookie_file: Path, netrc_path: Path | None) -> tuple[IndexSpec, list[str], str]:
    url = index_url(spec)
    out_dir = index_out_dir(base, spec)
    index_path = out_dir / "index.html"
    result = curl_get(url, index_path, cookie_file, timeout=240, netrc_path=netrc_path)
    if result.returncode != 0:
        return spec, [], f"index_failed rc={result.returncode} {result.stderr.strip()[:220]}"
    html = index_path.read_text(encoding="utf-8", errors="ignore")
    names = sorted(set(filename_re(spec.product).findall(html)))
    return spec, [url + name for name in names], "ok"


def build_tasks(
    product: str,
    dates: list[DateSpec],
    hours: list[str],
    stations: set[str],
    base: Path,
    cookie_file: Path,
    netrc_path: Path | None,
    jobs: int,
) -> tuple[list[DownloadTask], list[str]]:
    specs = collect_index_specs(product, dates, hours)
    tasks: list[DownloadTask] = []
    rows = ["product\tyear\tdoy\thour\tstatus\tfile_count\tmatched_count\tmessage"]
    with cf.ThreadPoolExecutor(max_workers=min(jobs, max(1, len(specs)))) as pool:
        futs = {pool.submit(fetch_index, spec, base, cookie_file, netrc_path): spec for spec in specs}
        for fut in cf.as_completed(futs):
            spec, urls, status = fut.result()
            matched_count = 0
            for url in urls:
                name = url.rsplit("/", 1)[-1]
                station = name[:9].upper()
                if stations and station not in stations:
                    continue
                hour = spec.hour or "00"
                tasks.append(DownloadTask(url, target_path(base, spec, name), station, product, spec.year, spec.doy, hour))
                matched_count += 1
            rows.append(f"{spec.product}\t{spec.year}\t{spec.doy}\t{spec.hour or ''}\t{status}\t{len(urls)}\t{matched_count}\t")
            print(
                f"[index] product={spec.product} {spec.year}-{spec.doy} H{spec.hour or '--'} "
                f"status={status} files={len(urls)} matched={matched_count}",
                flush=True,
            )
    tasks.sort(key=lambda t: (t.product, t.year, t.doy, t.hour, t.station, t.target.name))
    return tasks, rows


def download_one(task: DownloadTask, cookie_file: Path, netrc_path: Path | None) -> str:
    if gzip_ok(task.target):
        return f"{task.product}\t{task.year}\t{task.doy}\t{task.hour}\t{task.station}\t{task.target.name}\tskip_verified\t{task.target.stat().st_size}\t"
    part = task.target.with_suffix(task.target.suffix + ".part")
    if part.exists():
        part.unlink()
    result = curl_get(task.url, part, cookie_file, timeout=900, netrc_path=netrc_path)
    if result.returncode != 0:
        if part.exists():
            part.unlink()
        return (
            f"{task.product}\t{task.year}\t{task.doy}\t{task.hour}\t{task.station}\t{task.target.name}"
            f"\tfailed\t0\t{result.stderr.strip()[:220]}"
        )
    part.replace(task.target)
    if not gzip_ok(task.target):
        size = task.target.stat().st_size if task.target.exists() else 0
        if task.target.exists():
            task.target.unlink()
        return f"{task.product}\t{task.year}\t{task.doy}\t{task.hour}\t{task.station}\t{task.target.name}\tbad_gzip\t{size}\t"
    return f"{task.product}\t{task.year}\t{task.doy}\t{task.hour}\t{task.station}\t{task.target.name}\tdownloaded\t{task.target.stat().st_size}\t"


def command_list_or_download(args: argparse.Namespace, do_download: bool) -> None:
    base = Path(args.base)
    dates = parse_dates(args.dates)
    hours = parse_hours(args.hours) if args.product == "highrate" else ["00"]
    stations = parse_stations(args.stations)
    out_meta = base / "metadata" / args.out_name
    out_meta.mkdir(parents=True, exist_ok=True)
    cookie_file = Path(args.cookie_file) if args.cookie_file else product_out_root(base, args.product) / ".urs_cookies"
    netrc_path = Path(args.netrc).expanduser() if args.netrc else None

    tasks, index_rows = build_tasks(args.product, dates, hours, stations, base, cookie_file, netrc_path, args.jobs)
    (out_meta / "index_inventory.tsv").write_text("\n".join(index_rows) + "\n", encoding="utf-8")
    print(f"tasks={len(tasks)} metadata={out_meta}", flush=True)

    manifest_path = out_meta / "manifest.tsv"
    manifest_path.write_text("product\tyear\tdoy\thour\tstation\tfile\tstatus\tbytes\tmessage\n", encoding="utf-8")
    if not do_download:
        for task in tasks:
            with manifest_path.open("a", encoding="utf-8") as fh:
                fh.write(
                    f"{task.product}\t{task.year}\t{task.doy}\t{task.hour}\t{task.station}\t{task.target.name}"
                    f"\tlisted\t0\t{task.url}\n"
                )
        print(f"listed_manifest={manifest_path}")
        return

    with cf.ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(download_one, task, cookie_file, netrc_path): task for task in tasks}
        for idx, fut in enumerate(cf.as_completed(futs), start=1):
            task = futs[fut]
            row = fut.result()
            with manifest_path.open("a", encoding="utf-8") as fh:
                fh.write(row + "\n")
            if idx % 25 == 0 or idx == len(tasks):
                print(f"[download] {idx}/{len(tasks)} last={task.target.name} status={row.split(chr(9))[6]}", flush=True)
    print(f"download_manifest={manifest_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NASA CDDIS IGS RINEX/CRINEX listing and downloader.")
    sub = parser.add_subparsers(dest="command", required=True)

    cfg = sub.add_parser("configure-netrc", help="Create/update a .netrc for Earthdata/CDDIS login.")
    cfg.add_argument("--username")
    cfg.add_argument("--password")
    cfg.add_argument("--netrc", default=str(default_netrc()))
    cfg.set_defaults(func=command_configure_netrc)

    for name, do_download in [("list", False), ("download", True)]:
        p = sub.add_parser(name, help=("List matching files." if not do_download else "Download matching files."))
        p.add_argument("--product", choices=["highrate", "daily"], required=True)
        p.add_argument("--dates", required=True, help="Comma-separated YYYY-MM-DD or YYYY:DDD dates.")
        p.add_argument("--hours", default="00-23", help="High-rate only. Comma/range hours, e.g. 03-12 or 00,06-08.")
        p.add_argument("--stations", default="", help="Comma-separated 9-char IGS station IDs. Empty means all stations.")
        p.add_argument("--base", default="public_data_chasing_lightning")
        p.add_argument("--out-name", required=True)
        p.add_argument("--jobs", type=int, default=8)
        p.add_argument("--cookie-file", default="")
        p.add_argument("--netrc", default="", help="Explicit .netrc path. If omitted, curl uses its default -n behavior.")
        p.set_defaults(func=lambda args, dd=do_download: command_list_or_download(args, dd))

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
