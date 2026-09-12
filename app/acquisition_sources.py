"""Registered GNSS downloads and real directory discovery for the shared catalog.

The CDDIS transport follows the bundled IGS downloader's curl/netrc/cookie
redirect flow, reusing its credential-file, directory and gzip routines.
One transfer is one attempt here; durable retry/backoff belongs to the catalog.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import gzip
from html.parser import HTMLParser
import importlib.util
import inspect
import json
import netrc
import os
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from urllib.parse import unquote, urljoin, urlsplit

from .sources import collect_portwatch_series


_HELPER = None
NASA_HOSTS = {"cddis.nasa.gov", "urs.earthdata.nasa.gov"}
BKG_HOSTS = {"igs.bkg.bund.de"}


def _legacy():
    global _HELPER
    if _HELPER is None:
        path = Path(__file__).resolve().parents[1] / "skills/igs-cddis-api-download/scripts/igs_cddis_api.py"
        spec = importlib.util.spec_from_file_location("workbench_igs_downloader", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        _HELPER = module
    return _HELPER


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _instant(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=timezone.utc) if len(str(value)) == 10 else datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


class StoragePause(RuntimeError):
    pass


async def _progress(callback, current, total):
    if callback:
        try:
            answer = callback(current, total)
            if inspect.isawaitable(answer):
                await answer
        except Exception as exc:
            raise StoragePause(str(exc)) from exc


def _headers(path):
    if not path.exists():
        return {}
    headers = {}
    for line in path.read_text(encoding="iso-8859-1").splitlines():
        if line.startswith("HTTP/"):
            headers = {"status": line.split()[1]}
        elif ":" in line:
            name, value = line.split(":", 1)
            headers[name.lower()] = value.strip()
    return headers


def _retry(headers):
    value = headers.get("retry-after")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, (parsedate_to_datetime(value).astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError):
            return None


def _credential_file(temp_dir):
    helper = _legacy()
    username = os.getenv("EARTHDATA_USERNAME") or os.getenv("NASA_EARTHDATA_USERNAME")
    password = os.getenv("EARTHDATA_PASSWORD") or os.getenv("NASA_EARTHDATA_PASSWORD")
    if username and password:
        path = temp_dir / ".earthdata.netrc"
        helper.write_netrc(path, username, password)
        return path, True
    path = Path(os.getenv("EARTHDATA_NETRC", str(helper.default_netrc()))).expanduser()
    try:
        entries = netrc.netrc(str(path))
        if any(entries.authenticators(host) for host in NASA_HOSTS):
            return path, False
    except (OSError, netrc.NetrcParseError):
        pass
    return None, False


def _source(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password:
        raise ValueError("Only registered HTTPS source URLs without embedded credentials are supported")
    if parsed.hostname == "cddis.nasa.gov":
        return "cddis"
    if parsed.hostname in BKG_HOSTS:
        return "bkg"
    raise ValueError("GNSS source must be the registered BKG or CDDIS archive")


async def _transfer(url, part, temp_dir, progress=None, timeout=900):
    source = _source(url)
    helper = _legacy()
    try:
        curl = helper.require_curl()
    except SystemExit:
        return {"status": "retry_wait", "error": "The configured CDDIS/BKG transport requires curl on the workbench host", "bytes": 0}
    temp_dir.mkdir(parents=True, exist_ok=True)
    part.parent.mkdir(parents=True, exist_ok=True)
    header_path = temp_dir / "response.headers"
    cookie_path = temp_dir / ".earthdata.cookies"
    private_netrc, temporary_credentials = None, False
    args = [curl, "--location", "--fail", "--silent", "--show-error", "--connect-timeout", "30",
            "--max-time", str(timeout), "--dump-header", str(header_path), "--output", "-"]
    if source == "cddis":
        private_netrc, temporary_credentials = _credential_file(temp_dir)
        if private_netrc is None:
            return {"status": "auth_unavailable", "error": "Earthdata credentials unavailable in the configured netrc/environment", "bytes": 0}
        args.extend(["--netrc-file", str(private_netrc), "--cookie", str(cookie_path), "--cookie-jar", str(cookie_path)])
    args.append(url)
    process = None
    try:
        await _progress(progress, 0, None)
        process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stderr_read = asyncio.create_task(process.stderr.read())
        count, digest = 0, hashlib.sha256()
        with part.open("wb") as handle:
            while chunk := await process.stdout.read(65536):
                headers = _headers(header_path)
                total = int(headers["content-length"]) if headers.get("content-length", "").isdigit() else None
                # Quota callback runs before each write, including unknown-length responses.
                await _progress(progress, count + len(chunk), total)
                handle.write(chunk)
                digest.update(chunk)
                count += len(chunk)
        await process.wait()
        await stderr_read
        headers = _headers(header_path)
        count = part.stat().st_size if part.exists() else 0
        total = int(headers["content-length"]) if headers.get("content-length", "").isdigit() else None
        await _progress(progress, count, total)
        status_code = int(headers.get("status", 0))
        common = {"bytes": count, "total_bytes": total, "source_etag": headers.get("etag"),
                  "source_last_modified": headers.get("last-modified"), "retry_after_seconds": _retry(headers),
                  "content_type": headers.get("content-type"), "http_status": status_code}
        if process.returncode:
            status = "auth_unavailable" if status_code in (401, 403) else "source_missing" if status_code in (404, 410) else "retry_wait"
            return dict(common, status=status, error=f"Source transfer failed (HTTP {status_code}, curl {process.returncode})")
        return dict(common, status="received", identity={"sha256": digest.hexdigest(), "size": count}, error=None)
    except StoragePause as exc:
        return {"status": "storage_wait", "error": str(exc), "bytes": part.stat().st_size if part.exists() else 0}
    finally:
        if process and process.returncode is None:
            process.terminate()
            await process.wait()
        if temporary_credentials and private_netrc:
            private_netrc.unlink(missing_ok=True)
        cookie_path.unlink(missing_ok=True)
        header_path.unlink(missing_ok=True)


def _accept_file(part, target, identity=None):
    with part.open("rb") as handle:
        first = handle.read(512).lstrip().lower()
    if not first or first.startswith((b"<!doctype html", b"<html", b"<?xml")):
        raise ValueError("Source returned an empty, login or error document")
    if target.name.lower().endswith(".gz"):
        if not _legacy().gzip_ok(part):
            raise ValueError("Source gzip stream is incomplete or invalid")
        with gzip.open(part, "rb") as handle:
            header = handle.read(512).lower()
        if b"rinex" not in header[:100] and b"crinex" not in header[:100]:
            raise ValueError("Source gzip payload is not a RINEX/Compact RINEX observation")
    elif target.suffix.lower() in (".rnx", ".crx"):
        if b"rinex" not in first[:100] and b"crinex" not in first[:100]:
            raise ValueError("Source did not return a RINEX header")
    identity = dict(identity or {})
    if not identity.get("sha256"):
        raise ValueError("The completed transfer has no recorded byte identity")
    target.parent.mkdir(parents=True, exist_ok=True)
    # A complete temporary file becomes visible atomically without overwriting
    # an existing experiment file; configured temp/raw roots must share a device.
    os.link(part, target)
    part.unlink()
    identity["mtime_ns"] = target.stat().st_mtime_ns
    return identity


async def fetch_portwatch_date(resource):
    day = str(resource.get("observed_start") or resource.get("observed_date"))[:10]
    config = SimpleNamespace(portwatch_url=resource["url"].rstrip("/").removesuffix("/query"),
        portwatch_id=resource.get("portid", "chokepoint6"), portwatch_history_days=1,
        source_timeout=60, request_max_bytes=2000000)
    result = await collect_portwatch_series(config, start_date=day, end_date=day)
    if result["status"] == "source_failed":
        return {"status": "retry_wait", "error": result["error"], "retry_after_seconds": result.get("retry_after_seconds")}
    if not result["rows"]:
        return {"status": "awaiting_source", "error": f"PortWatch has no record for {day}", "retry_after_seconds": 86400}
    row = result["rows"][0]
    return {"status": "received", "acquired_at": result["checked_at"], "data": {"attributes": row["attributes"], "observed_date": day,
            "source": config.portwatch_url, "portid": config.portwatch_id,
            "available_at": None, "historical_snapshot": True,
            "time_note": "Historical values acquired now; first historical publication time unknown"}}


async def download_resource(resource, target_path, temp_dir, progress=None):
    target, temporary = Path(target_path), Path(temp_dir)
    temporary.mkdir(parents=True, exist_ok=True)
    part = temporary / (target.name + ".part")
    if target.exists():
        return {"status": "conflict", "error": "Target already exists; catalog must select its version before replacement", "bytes": target.stat().st_size}
    if resource.get("kind") == "portwatch":
        response = await fetch_portwatch_date(resource)
        if response["status"] != "received":
            return response
        payload = json.dumps(response.pop("data"), ensure_ascii=False, sort_keys=True).encode("utf-8")
        try:
            await _progress(progress, 0, len(payload))
            await _progress(progress, len(payload), len(payload))
            part.write_bytes(payload)
        except StoragePause as exc:
            return {"status": "storage_wait", "error": str(exc), "bytes": part.stat().st_size if part.exists() else 0}
        response.update(bytes=len(payload), total_bytes=len(payload))
        response["identity"] = {"sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
    else:
        response = await _transfer(resource["url"], part, temporary, progress)
        if response["status"] == "source_missing":
            end = _instant(resource.get("observed_end") or _now())
            if end > datetime.now(timezone.utc) - timedelta(days=3):
                response.update(status="awaiting_source", retry_after_seconds=900,
                                error="Recent configured observation is not yet listed by the source")
        if response["status"] != "received":
            return response
    try:
        identity = await asyncio.to_thread(_accept_file, part, target, response.get("identity"))
    except FileExistsError:
        return dict(response, status="conflict", error="A source version already occupies the target; temporary version retained")
    except ValueError as exc:
        return dict(response, status="invalid_data", error=str(exc))
    except OSError as exc:
        return dict(response, status="storage_wait", error=f"Atomic placement unavailable: {exc.strerror}")
    return dict(response, status="downloaded", path=str(target), identity=identity, acquired_at=_now(),
                available_at=None, error=None)


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            values = dict(attrs)
            if values.get("href"):
                self.hrefs.append(values["href"])


async def discover(subscription, now=None):
    """List only configured product directories and register actual file links."""
    now = _instant(now or _now())
    product, source = subscription.get("product", "daily"), subscription.get("source", "cddis")
    stations = {str(station).upper() for station in subscription.get("stations", [])}
    if not stations:
        return {"status": "disabled", "resources": [], "scans": [], "error": "No stations configured"}
    if product not in ("daily", "highrate") or source not in ("cddis", "bkg"):
        return {"status": "unavailable", "resources": [], "scans": [], "error": "Unsupported configured source/product"}
    if source == "bkg" and product != "daily" and not subscription.get("index_url_template"):
        return {"status": "unavailable", "resources": [], "scans": [], "error": "No registered BKG high-rate directory template"}
    helper = _legacy()
    configured_dates = subscription.get("dates")
    if isinstance(configured_dates, str):
        configured_dates = configured_dates.split(",")
    dates = [datetime.fromisoformat(day).date() for day in configured_dates] if configured_dates else [
        (now - timedelta(days=offset)).date() for offset in (range(1, 4) if product == "daily" else range(0, 2))]
    hours = subscription.get("hours")
    if isinstance(hours, str):
        hours = [int(hour) for hour in helper.parse_hours(hours)]
    if hours is None:
        hours = list(range(int(subscription.get("hour_start", 0)), int(subscription.get("hour_end", 24))))
    temp_root = Path(subscription["temp_dir"])
    pattern = re.compile(r"(?P<station>[A-Z0-9]{9})_[RS]_(?P<stamp>\d{11})_(?P<duration>15M|01D)_(?P<interval>01S|30S)_MO\.(?:crx|rnx)(?:\.gz)?$", re.I)
    resources, scans = {}, []
    retry_after = None
    for day in sorted(set(dates)):
        if day > now.date():
            continue
        doy = f"{day.timetuple().tm_yday:03d}"
        for hour in (sorted(set(int(h) for h in hours)) if product == "highrate" else [None]):
            if hour is not None and not 0 <= hour <= 23:
                continue
            slot_start = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=hour or 0)
            if slot_start + (timedelta(minutes=15) if product == "highrate" else timedelta(days=1)) > now:
                continue
            spec = helper.IndexSpec(product, day.year, doy, f"{hour:02d}" if hour is not None else None)
            template = subscription.get("index_url_template")
            url = template.format(year=day.year, doy=doy, yy=f"{day.year % 100:02d}", hour=f"{hour or 0:02d}") if template else (
                helper.index_url(spec) if source == "cddis" else f"https://igs.bkg.bund.de/root_ftp/IGS/obs/{day.year}/{doy}/")
            folder = temp_root / f"{day.year}-{doy}-{hour if hour is not None else 'daily'}"
            target = folder / "index.html.part"
            answer = await _transfer(url, target, folder, timeout=240)
            scan = {"url": url, "checked_at": _now(), "status": answer["status"], "matched_files": 0,
                    "date": day.isoformat(), "hour": hour,
                    "error": answer.get("error"), "retry_after_seconds": answer.get("retry_after_seconds")}
            scans.append(scan)
            if answer.get("retry_after_seconds"):
                retry_after = max(retry_after or 0, answer["retry_after_seconds"])
            if answer["status"] != "received":
                continue
            body = target.read_text(encoding="utf-8", errors="replace")
            if source == "cddis" and "earthdata login" in body.lower() and "password" in body.lower():
                scan.update(status="auth_unavailable", error="CDDIS directory request returned an Earthdata login form")
                continue
            parser = _Links()
            parser.feed(body)
            for href in parser.hrefs:
                link = urljoin(url, href)
                if urlsplit(link).hostname != urlsplit(url).hostname:
                    continue
                name = unquote(urlsplit(link).path.rsplit("/", 1)[-1])
                match = pattern.fullmatch(name)
                if not match or match["station"].upper() not in stations:
                    continue
                start = datetime.strptime(match["stamp"], "%Y%j%H%M").replace(tzinfo=timezone.utc)
                if start.date() != day or (hour is not None and start.hour != hour):
                    continue
                if (match["duration"], match["interval"]) != (("15M", "01S") if product == "highrate" else ("01D", "30S")):
                    continue
                end = start + (timedelta(minutes=15) if product == "highrate" else timedelta(days=1))
                if end > now:
                    continue
                prefix = Path("raw") / ("cddis_highrate" if product == "highrate" else "cddis_daily") / str(day.year) / doy
                if product == "highrate":
                    prefix /= f"{hour:02d}"
                identifier = source + "-" + name.replace(".", "_")
                resources[link] = {"id": identifier, "kind": "gnss", "source": source, "product": product,
                    "station": match["station"].upper(), "url": link, "path": (prefix / name).as_posix(),
                    "observed_start": start.isoformat(), "observed_end": end.isoformat(),
                    "interval_seconds": 1 if product == "highrate" else 30, "time_system": "read_from_rinex_header",
                    "available_at": None, "discovered_at": scan["checked_at"], "replay_release_at": end.isoformat(),
                    "release_assumption": "Nominal filename window; actual UTC epochs come from RINEX parsing"}
                scan["matched_files"] += 1
            scan["status"] = "listed"
    failures = [scan for scan in scans if scan["status"] != "listed"]
    present = {(resource["station"], resource["observed_start"], resource["observed_end"])
               for resource in resources.values()}
    gaps = []
    for scan in scans:
        start = datetime.fromisoformat(scan["date"]).replace(tzinfo=timezone.utc) + timedelta(hours=scan["hour"] or 0)
        duration = timedelta(minutes=15) if product == "highrate" else timedelta(days=1)
        for offset in range(4 if product == "highrate" else 1):
            observed_start, observed_end = start + offset * duration, start + (offset + 1) * duration
            if observed_end > now:
                continue
            for station in sorted(stations):
                if (station, observed_start.isoformat(), observed_end.isoformat()) in present:
                    continue
                gaps.append({"id": f"{source}-{product}-{station}-{observed_start:%Y%m%d%H%M}",
                    "station": station, "product": product, "observed_start": observed_start.isoformat(),
                    "observed_end": observed_end.isoformat(), "index_url": scan["url"],
                    "status": "awaiting_source" if scan["status"] == "listed" else "index_failed",
                    "error": scan.get("error") or "Configured ended slot is not in the source listing",
                    "checked_at": scan["checked_at"]})
    return {"status": "partial" if failures else "ok", "resources": list(resources.values()), "scans": scans,
            "gaps": gaps, "error": failures[-1]["error"] if failures else None, "retry_after_seconds": retry_after}
