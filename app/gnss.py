"""Actual RINEX observations and bounded, descriptive replay tools.

The parsing/finite-value/quantile path follows the existing project's
process_rinex_day_metrics.py, using GeoRinex and numpy. Old result tables,
event labels and the old synchronous-drop detector are not inputs here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import gzip
import io
import json
import math
from pathlib import Path
import re
from statistics import median
import warnings


PROCESSING_VERSION = "rinex-cnr-window-v1"
WINDOW_SECONDS = 300
RINEX_REFERENCE = "https://files.igs.org/pub/data/format/rinex305.pdf"
LEAP_REFERENCE = "https://datacenter.iers.org/versionMetadata.php?filename=mt%2Fbulletinc-071.txt"
SYSTEM_NAMES = {"G": "GPS", "R": "GLONASS", "E": "Galileo", "C": "BeiDou",
                "J": "QZSS", "I": "NavIC", "S": "SBAS"}


class GnssProcessingError(RuntimeError):
    """A real input cannot currently produce a usable observation result."""


def _dt(value):
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise GnssProcessingError("Replay timestamps must declare their UTC offset")
    return result.astimezone(timezone.utc)


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def _header(text):
    result = {"signals": defaultdict(list), "comments": []}
    system = None
    for line in io.StringIO(text):
        label, content = line[60:80].strip(), line[:60]
        if label == "END OF HEADER":
            result["signals"] = dict(result["signals"])
            return result
        if label == "RINEX VERSION / TYPE":
            result["rinex_version"] = float(content[:9])
            result["file_system"] = content[40:41].strip() or "G"
        elif label == "MARKER NAME":
            result["marker_name"] = content.strip()
        elif label == "MARKER NUMBER":
            result["marker_number"] = content.strip()
        elif label == "APPROX POSITION XYZ":
            result["ecef_xyz"] = [float(v) for v in content.split()[:3]]
        elif label == "INTERVAL":
            result["interval_seconds"] = _number(content[:10])
        elif label == "TIME OF FIRST OBS":
            result["time_system"] = content[48:51].strip().upper()
        elif label == "LEAP SECONDS":
            result["header_leap_seconds"] = _number(content[:6])
        elif label == "SIGNAL STRENGTH UNIT":
            result["signal_unit"] = content.strip().upper()
        elif label == "SYS / # / OBS TYPES":
            if content[0:1].strip():
                system = content[0]
            if system:
                result["signals"][system].extend(content[6:].split())
        elif label == "# / TYPES OF OBSERV":
            result["signals"]["ALL"].extend(content[6:].split())
        elif label == "COMMENT":
            result["comments"].append(content.strip())
    raise GnssProcessingError("RINEX header has no END OF HEADER")


def _read_rinex(path):
    raw = path.read_bytes()
    compressed = raw[:2] == b"\x1f\x8b"
    if compressed:
        raw = gzip.decompress(raw)
    compact = b"CRINEX VERS" in raw[:100]
    if compact:
        try:
            import hatanaka
        except ImportError as exc:
            raise GnssProcessingError("Install workbench requirements-gnss.txt for Compact RINEX decoding") from exc
        raw = hatanaka.decompress(raw)
    return raw.decode("ascii"), {"gzip_decoded": compressed, "hatanaka_decoded": compact}


def _time_conversion(header, resource, first, last):
    system = header.get("time_system") or resource.get("time_system")
    if not system or str(system).lower() == "read_from_rinex_header":
        system = {"G": "GPS", "R": "GLO", "E": "GAL", "C": "BDT",
                  "J": "QZS", "I": "IRN"}.get(header.get("file_system"))
    system = str(system or "unknown").upper()
    if system in {"UTC", "GLO"}:
        return system, 0.0, {"method": "RINEX UTC/GLO observation timestamps", "source": RINEX_REFERENCE}
    # The two historical cases fall wholly inside the confirmed constant-offset
    # interval. Do not silently extrapolate this offset to other future datasets.
    in_interval = first >= datetime(2017, 1, 2) and last < datetime(2026, 7, 1)
    if in_interval and system in {"GPS", "GAL", "QZS", "BDT"}:
        offset = 4.0 if system == "BDT" else 18.0
        return system, offset, {"method": "IERS Bulletin C 71; integer-second system-time to UTC conversion",
                                "source": LEAP_REFERENCE, "format_source": RINEX_REFERENCE,
                                "resolution_note": "Integer-second conversion for five-minute statistics; no subsecond synchrony claim"}
    explicit = _number(resource.get("utc_offset_seconds"))
    if explicit is not None and resource.get("time_conversion_source"):
        return system, explicit, {"method": "Registered source time correction", "source": resource["time_conversion_source"]}
    raise GnssProcessingError(f"No supported, sourced UTC conversion for {system} at {first.isoformat()}")


def _coordinates(header, resource):
    xyz = header.get("ecef_xyz")
    if xyz and len(xyz) == 3 and all(_number(v) is not None for v in xyz) and any(xyz):
        x, y, z = xyz
        a, e2 = 6378137.0, 6.69437999014e-3
        p = math.hypot(x, y)
        lon = math.atan2(y, x)
        lat = math.atan2(z, p * (1 - e2))
        for _ in range(8):
            n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
            lat = math.atan2(z + e2 * n * math.sin(lat), p)
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        height = p / math.cos(lat) - n if p > 1 else abs(z) - a * math.sqrt(1 - e2)
        return {"ecef_xyz_m": xyz, "latitude": math.degrees(lat), "longitude": math.degrees(lon),
                "height_m": height, "datum": "WGS84 ellipsoid conversion of approximate RINEX ECEF",
                "source": "RINEX APPROX POSITION XYZ"}
    value = resource.get("coordinates")
    return deepcopy(value) if value else {"status": "unavailable", "source": None}


def _strength_kind(header, resource, finite, np):
    declared = header.get("signal_unit")
    unit_source = "RINEX SIGNAL STRENGTH UNIT"
    if not declared and resource.get("signal_unit") and resource.get("signal_unit_source"):
        declared = str(resource["signal_unit"]).upper()
        unit_source = resource["signal_unit_source"]
    if declared and declared.replace("-", "").replace(" ", "") in {"DBHZ", "DBHERTZ"}:
        return "cnr", "dB-Hz", unit_source
    if finite.size and np.all((finite >= 0) & (finite <= 9) & (finite == np.floor(finite))):
        return "discrete_quality_or_undocumented_strength", "indicator", "Integer S* values in 0..9; no dB-Hz declaration"
    return "undocumented_signal_strength", declared or "receiver_units", unit_source if declared else "No explicit strength unit"


def _parse_resource(resource, path, case_id):
    try:
        import georinex as gr
        import numpy as np
    except ImportError as exc:
        raise GnssProcessingError("GNSS processing requires the optional workbench requirements-gnss.txt") from exc
    text, decoding = _read_rinex(path)
    header = _header(text)
    measures = sorted({code for codes in header["signals"].values() for code in codes
                       if re.fullmatch(r"S[0-9][A-Z]?", code)})
    if not measures:
        raise GnssProcessingError("The observation header contains no S* strength observables")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        dataset = gr.load(io.StringIO(text), meas=measures, fast=True, useindicators=False)
    try:
        if not dataset.sizes.get("time"):
            raise GnssProcessingError("GeoRinex returned no observation epochs")
        times = dataset.time.values.astype("datetime64[us]")
        first, last = times.min().astype(datetime), times.max().astype(datetime)
        system, offset, conversion = _time_conversion(header, resource, first, last)
        utc = times - np.timedelta64(round(offset * 1_000_000), "us")
        seconds = utc.astype("datetime64[us]").astype("int64") / 1_000_000
        order = np.argsort(seconds)
        seconds = seconds[order]
        if len(np.unique(seconds)) != len(seconds):
            raise GnssProcessingError("Duplicate RINEX epochs; the resource needs a single unambiguous observation series")
        differences = np.diff(seconds)
        positive = differences[differences > 0]
        inferred = float(np.median(positive)) if positive.size else None
        interval = header.get("interval_seconds") or inferred or _number(resource.get("interval_seconds"))
        if interval is None or interval <= 0:
            raise GnssProcessingError("Observation sampling interval is unavailable")
        notes = []
        declared_interval = _number(resource.get("interval_seconds"))
        if declared_interval and abs(declared_interval - interval) > 0.001:
            notes.append("Manifest interval differs from RINEX interval; statistics use the RINEX value")
        if inferred and inferred > interval * 1.5:
            notes.append("Observed epoch spacing exceeds the nominal interval; missing epochs remain gaps")
        declared_station = str(resource.get("station") or "").upper()
        marker = header.get("marker_name", "").upper().strip()
        if declared_station and marker and marker[:4] != declared_station[:4]:
            raise GnssProcessingError(f"RINEX marker {marker} does not match registered station {declared_station}")
        station = declared_station or marker
        if not station:
            raise GnssProcessingError("The resource has no actual station identity")
        observed_start = datetime.fromtimestamp(float(seconds[0]), timezone.utc)
        observed_end = datetime.fromtimestamp(float(seconds[-1]) + interval, timezone.utc)
        bins = np.floor(seconds / WINDOW_SECONDS).astype("int64")
        first_bin, last_bin = int(bins[0]), int(bins[-1])
        gap_list = [{"start": _iso(datetime.fromtimestamp(float(seconds[i]) + interval, timezone.utc)),
                     "end": _iso(datetime.fromtimestamp(float(seconds[i + 1]), timezone.utc)),
                     "missing_epoch_count": max(0, int(round(float(gap) / interval)) - 1)}
                    for i, gap in enumerate(differences) if gap > interval * 1.5]
        signals, series = [], []
        satellites = [str(sv) for sv in dataset.sv.values]
        for constellation in sorted({sv[0] for sv in satellites}):
            svs = [sv for sv in satellites if sv.startswith(constellation)]
            registered_codes = header["signals"].get(constellation, header["signals"].get("ALL", []))
            for code in measures:
                if code not in dataset or code not in registered_codes:
                    continue
                matrix = dataset[code].sel(sv=svs).transpose("time", "sv").values[order, :].astype(float)
                finite = matrix[np.isfinite(matrix) & (matrix > 0)]
                value_kind, unit, unit_source = _strength_kind(header, resource, finite, np)
                signal = f"{constellation}:{code}"
                active_columns = np.any(np.isfinite(matrix) & (matrix > 0), axis=0)
                matrix = matrix[:, active_columns]
                signal_satellites = [sv for sv, active in zip(svs, active_columns) if active]
                slots = int(matrix.size)
                valid_count = int(finite.size)
                signal_summary = {"signal": signal, "system": constellation, "system_name": SYSTEM_NAMES.get(constellation, constellation),
                                  "code": code, "value_kind": value_kind, "unit": unit, "unit_source": unit_source,
                                  "sample_count": slots, "valid_count": valid_count,
                                  "valid_ratio": valid_count / slots if slots else None,
                                  "satellite_count": len(signal_satellites), "satellites": signal_satellites,
                                  "strength_p10": float(np.quantile(finite, 0.1)) if finite.size else None,
                                  "cnr_p10": float(np.quantile(finite, 0.1)) if finite.size and value_kind == "cnr" else None,
                                  "strength_median": float(np.median(finite)) if finite.size else None}
                signals.append(signal_summary)
                for bin_number in range(first_bin, last_bin + 1):
                    selection = bins == bin_number
                    chunk = matrix[selection, :]
                    values = chunk[np.isfinite(chunk) & (chunk > 0)]
                    window_start = max(float(bin_number * WINDOW_SECONDS), float(seconds[0]))
                    window_end = min(float((bin_number + 1) * WINDOW_SECONDS), float(seconds[-1]) + interval)
                    # Missing epochs and empty signal slots are separate quantities.
                    expected = max(1, int(round((window_end - window_start) / interval)))
                    epoch_count = int(np.count_nonzero(selection))
                    sample_count = int(chunk.size)
                    p10 = float(np.quantile(values, 0.1)) if values.size else None
                    series.append({"resource_id": resource["id"], "case_id": case_id, "station": station,
                                   "signal": signal, "system": constellation, "code": code,
                                   "unit": unit, "value_kind": value_kind,
                                   "bin_start": _iso(datetime.fromtimestamp(bin_number * WINDOW_SECONDS, timezone.utc)),
                                   "window_start": _iso(datetime.fromtimestamp(window_start, timezone.utc)),
                                   "window_end": _iso(datetime.fromtimestamp(window_end, timezone.utc)),
                                   "interval_seconds": interval, "window_seconds": WINDOW_SECONDS,
                                   "epoch_count": epoch_count, "expected_epoch_count": expected,
                                   "missing_epoch_count": max(0, expected - epoch_count),
                                   "epoch_coverage_ratio": min(1.0, epoch_count / expected),
                                   "sample_count": sample_count, "valid_count": int(values.size),
                                   "valid_ratio": float(values.size / sample_count) if sample_count else None,
                                   "strength_p10": p10, "cnr_p10": p10 if value_kind == "cnr" else None,
                                   "gap": not bool(values.size)})
        parse_warnings = list(dict.fromkeys(str(w.message).replace(str(path), path.name)[:300] for w in caught))[:12]
        summary = {"epoch_count": int(len(seconds)), "signal_count": len(signals),
                   "valid_count": sum(row["valid_count"] for row in signals),
                   "sample_count": sum(row["sample_count"] for row in signals),
                   "missing_epoch_count": sum(row["missing_epoch_count"] for row in gap_list),
                   "gap_count": len(gap_list), "reference_status": "not_attached"}
        summary["valid_ratio"] = summary["valid_count"] / summary["sample_count"] if summary["sample_count"] else None
        return {"processing_version": PROCESSING_VERSION, "resource_id": resource["id"], "case_id": case_id,
                "station": station, "marker_name": header.get("marker_name"), "coordinates": _coordinates(header, resource),
                "source_filename": path.name, "role": resource.get("role", "observation"),
                "replay_start": resource.get("replay_start"), "available_at": resource.get("available_at"),
                "replay_release_at": resource.get("replay_release_at"),
                "window_start": _iso(observed_start), "window_end": _iso(observed_end),
                "time_system": system, "display_time_system": "UTC", "utc_offset_seconds": offset,
                "header_leap_seconds": header.get("header_leap_seconds"),
                "time_conversion": conversion, "interval_seconds": interval, "inferred_interval_seconds": inferred,
                "window_seconds": WINDOW_SECONDS, "rinex_version": header.get("rinex_version"),
                "decoding": decoding, "signals": signals, "series": series, "summary": summary,
                "gaps": gap_list, "parse_issues": parse_warnings, "notes": notes,
                "valid_ratio_definition": "Positive finite S* cells / (recorded epochs × satellites having this signal in the file); not expected constellation visibility",
                "indicator_handling": "GeoRinex SSI/LLI indicator loading disabled; S* values without a sourced dB-Hz unit are not labelled CNR",
                "status": "computed" if summary["valid_count"] else "no_valid_strength",
                "computed_at": _iso(datetime.now(timezone.utc))}
    finally:
        dataset.close()


def _visible(result, as_of, case_id=None):
    if case_id is not None and result.get("case_id") != case_id:
        return False
    if not result.get("window_end") or _dt(result["window_end"]) > _dt(as_of):
        return False
    release = result.get("replay_release_at")
    return not release or _dt(release) <= _dt(as_of)


def _clock_key(row):
    stamp = _dt(row["bin_start"])
    return (row["station"], row["signal"], row["unit"], row["interval_seconds"],
            stamp.hour, stamp.minute, stamp.second,
            round((_dt(row["window_start"]) - stamp).total_seconds(), 3),
            round((_dt(row["window_end"]) - _dt(row["window_start"])).total_seconds(), 3))


def _attach_reference(result, baselines, as_of, replay_start):
    reference = defaultdict(list)
    for previous in baselines:
        if (not _visible(previous, as_of, result["case_id"]) or previous.get("role") != "baseline"
                or not replay_start or _dt(previous["window_end"]) > _dt(replay_start)):
            continue
        for row in previous.get("series", []):
            if row.get("cnr_p10") is not None and not row.get("missing_epoch_count"):
                reference[_clock_key(row)].append(row)
    matched, references = 0, set()
    for row in result["series"]:
        samples = reference.get(_clock_key(row), [])
        p10 = median(item["cnr_p10"] for item in samples) if samples else None
        row.update({"baseline_p10": p10, "baseline_days": len({_dt(item["window_start"]).date() for item in samples}),
                    "baseline_resource_ids": sorted({item["resource_id"] for item in samples}),
                    "delta_p10": row["cnr_p10"] - p10 if p10 is not None and row["cnr_p10"] is not None else None})
        if row["delta_p10"] is not None:
            matched += 1
            references.update(row["baseline_resource_ids"])
    for signal in result["signals"]:
        rows = [row for row in result["series"] if row["signal"] == signal["signal"] and row["delta_p10"] is not None]
        signal["matched_window_count"] = len(rows)
        signal["matched_window_p10_median"] = median(row["cnr_p10"] for row in rows) if rows else None
        signal["baseline_window_p10_median"] = median(row["baseline_p10"] for row in rows) if rows else None
        signal["delta_window_p10_median"] = median(row["delta_p10"] for row in rows) if rows else None
    result["baseline_resource_ids"] = sorted(references)
    result["summary"].update({"reference_status": "available" if matched else "insufficient_reference",
                               "matched_window_count": matched, "baseline_resource_count": len(references)})
    result["reference_method"] = {"metric": "median of historical five-minute CNR p10 values at matching UTC clock windows",
                                  "matching": "same case, station, constellation, exact signal code, unit, sampling interval and window bounds",
                                  "replay_start": replay_start, "minimum_days": 1,
                                  "coverage_note": "One available reference day is descriptive only; baseline_days reports actual support"}
    return result


def compute_resource(resource, path, cache_dir, *, as_of, case_id, baseline_results=None):
    """Decode and compute one whole released file; reuse only this code's cache.

    ``baseline_results`` contains prior computed resource dictionaries. The
    caller registers resource IDs as materials and stores this returned result.
    Parsing never calls HTTP, the model, a shell command, or old result tables.
    """
    path, cache_dir = Path(path), Path(cache_dir)
    stat = path.stat()
    resource_id = str(resource["id"])
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", resource_id) or not re.fullmatch(r"[A-Za-z0-9_.-]+", str(case_id)):
        raise GnssProcessingError("Registered case/resource IDs must be simple identifiers")
    cache = cache_dir / str(case_id) / f"{resource_id}.{PROCESSING_VERSION}.json"
    identity = {"processing_version": PROCESSING_VERSION, "resource_id": resource_id,
                "filename": path.name, "mtime_ns": stat.st_mtime_ns, "size": stat.st_size,
                "metadata": {key: resource.get(key) for key in ("station", "interval_seconds", "time_system", "utc_offset_seconds",
                             "time_conversion_source", "signal_unit", "signal_unit_source", "coordinates", "role", "replay_start",
                             "available_at", "replay_release_at")}}
    result = None
    if cache.exists():
        saved = json.loads(cache.read_text(encoding="utf-8"))
        if saved.get("identity") == identity and saved.get("result"):
            result = saved["result"]
    if result is None:
        try:
            result = _parse_resource(resource, path, str(case_id))
            # Save the natural work unit before reference comparison or model work.
            _write_json(cache, {"identity": identity, "result": result})
        except Exception as exc:
            _write_json(cache.with_suffix(".failure.json"), {"identity": identity, "status": "failed",
                        "failed_at": _iso(datetime.now(timezone.utc)), "error": type(exc).__name__,
                        "message": str(exc).replace(str(path), path.name)[:1000]})
            raise
    result = deepcopy(result)
    if not _visible(result, as_of, str(case_id)):
        raise GnssProcessingError("Actual resource observation/release end is later than this immutable replay as_of")
    result["as_of"] = _iso(_dt(as_of))
    return _attach_reference(result, baseline_results or [], as_of, resource.get("replay_start"))


def _rows(results, as_of, case_id=None, station=None, signal=None, window_start=None, window_end=None):
    selected = []
    for result in results:
        if not _visible(result, as_of, case_id):
            continue
        for row in result.get("series", []):
            if station and row.get("station") != station:
                continue
            if signal and row.get("signal") != signal:
                continue
            if window_start and _dt(row["window_end"]) <= _dt(window_start):
                continue
            if window_end and _dt(row["window_start"]) >= _dt(window_end):
                continue
            if _dt(row["window_end"]) > _dt(as_of):
                continue
            selected.append({**row, "role": result.get("role"), "processing_version": result.get("processing_version")})
    return selected


def _compact_row(row):
    fields = ("resource_id", "station", "signal", "unit", "window_start", "window_end", "interval_seconds",
              "cnr_p10", "strength_p10", "baseline_p10", "delta_p10", "baseline_days",
              "baseline_resource_ids", "valid_ratio", "missing_epoch_count", "role")
    return {key: row.get(key) for key in fields}


def _tool_case(results, case_id):
    if case_id:
        return case_id
    cases = {item.get("case_id") for item in results}
    if len(cases) == 1:
        return next(iter(cases))
    raise GnssProcessingError("A professional tool needs exactly one registered case scope")


def station_history(results, station, signal, as_of, *, case_id=None, window_start=None, window_end=None, limit=12):
    """Read only already released, registered, same-station/signal statistics."""
    case_id = _tool_case(results, case_id)
    visible = [item for item in results if _visible(item, as_of, case_id)]
    registered = {(item.get("station"), row.get("signal")) for item in visible for row in item.get("signals", [])}
    selection_note = "Requested exact signal code"
    if signal is None:
        available = sorted(code for registered_station, code in registered if registered_station == station)
        signal = available[0] if available else None
        selection_note = "First available exact signal code in alphabetical order; no value-based selection"
    if (station, signal) not in registered:
        return {"status": "unavailable", "reason": "Station/signal is not registered and visible in this case", "resource_ids": []}
    rows = _rows(visible, as_of, case_id, station, signal, window_start, window_end)
    rows.sort(key=lambda row: row["window_end"], reverse=True)
    current = [row for row in rows if row.get("role") != "baseline"]
    references = [row for row in _rows(visible, as_of, case_id, station, signal) if row.get("role") == "baseline"]
    limit = min(max(int(limit), 1), 16)
    shown = current[:limit]
    reference_ids = {ref for row in shown for ref in row.get("baseline_resource_ids", [])}
    clock_keys = {_clock_key(row) for row in shown}
    comparable = [row for row in references if row["resource_id"] in reference_ids and _clock_key(row) in clock_keys]
    comparable.sort(key=lambda row: row["window_end"], reverse=True)
    # Explicitly show a small amount of matching history, not an old final verdict.
    shown_reference = comparable[: min(4, limit)]
    return {"status": "computed" if shown else "insufficient_coverage", "case_id": case_id, "as_of": as_of,
            "station": station, "signal": signal, "selected_signal": signal, "selection_note": selection_note,
            "visible_window_count": len(rows),
            "current_windows": [_compact_row(row) for row in shown],
            "reference_windows": [_compact_row(row) for row in shown_reference],
            "resource_ids": sorted({row["resource_id"] for row in shown + shown_reference} | reference_ids),
            "comparability": "Only pre-start same-station/system/signal UTC-clock, unit and interval matches have non-null baseline/delta",
            "coverage": {"current_windows": len(current), "baseline_windows": len(references),
                         "matched_current_windows": sum(row.get("delta_p10") is not None for row in current)},
            "limits": "Five-minute descriptive statistics; no source attribution or second-level synchronization claim"}


def multistation_check(results, as_of, *, case_id=None, stations=None, signal=None,
                       window_start=None, window_end=None, limit=12):
    """Compare visible concurrent window statistics, without a fabricated score."""
    case_id = _tool_case(results, case_id)
    visible = [item for item in results if _visible(item, as_of, case_id) and item.get("role") != "baseline"]
    registered = {item.get("station") for item in visible}
    requested = set(stations or registered)
    if not requested.issubset(registered):
        return {"status": "unavailable", "reason": "Requested station is not registered and visible in this case", "resource_ids": []}
    end = _dt(window_end) if window_end else _dt(as_of)
    start = _dt(window_start) if window_start else end - timedelta(days=1)
    if end > _dt(as_of) or start >= end:
        return {"status": "unavailable", "reason": "Requested window is outside the immutable replay as_of", "resource_ids": []}
    rows = [row for row in _rows(visible, as_of, case_id, signal=signal, window_start=_iso(start), window_end=_iso(end))
            if row["station"] in requested]
    signal_selection = "Requested exact signal code"
    if not signal and rows:
        coverage = defaultdict(set)
        for row in rows:
            if row.get("cnr_p10") is not None:
                coverage[row["signal"]].add(row["station"])
        if coverage:
            signal = sorted(coverage, key=lambda code: (-len(coverage[code]), code))[0]
        else:
            signal = sorted({row["signal"] for row in rows})[0]
        rows = [row for row in rows if row["signal"] == signal]
        signal_selection = "Greatest observed station coverage, then exact signal code; no value-based selection"
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row["signal"], row["unit"], row["interval_seconds"], row["window_start"], row["window_end"])].append(row)
    overlap = [group for group in grouped.values() if len({row["station"] for row in group if row.get("cnr_p10") is not None}) >= 2]
    summaries = []
    for station in sorted(requested):
        for sig in sorted({row["signal"] for row in rows if row["station"] == station}):
            group = [row for row in rows if row["station"] == station and row["signal"] == sig]
            finite = [row["cnr_p10"] for row in group if row.get("cnr_p10") is not None]
            deltas = [row["delta_p10"] for row in group if row.get("delta_p10") is not None]
            summaries.append({"station": station, "signal": sig, "unit": group[0]["unit"],
                              "interval_seconds": sorted({row["interval_seconds"] for row in group}),
                              "window_count": len(group), "valid_cnr_window_count": len(finite),
                              "window_p10_median": median(finite) if finite else None,
                              "delta_window_p10_median": median(deltas) if deltas else None,
                              "missing_epoch_count": sum(row["missing_epoch_count"] for row in group),
                              "resource_ids": sorted({row["resource_id"] for row in group})})
    limit = min(max(int(limit), 1), 16)
    overlap.sort(key=lambda group: group[0]["window_start"], reverse=True)
    shown, remaining = [], limit
    for group in overlap:
        if remaining < 2:
            break
        selected = group[:remaining]
        shown.append(selected)
        remaining -= len(selected)
    return {"status": "computed" if overlap else "insufficient_coverage", "case_id": case_id, "as_of": as_of,
            "window_start": _iso(start), "window_end": _iso(end), "stations": sorted(requested),
            "signal": signal, "signal_selection": signal_selection,
            "station_signal_summaries": summaries[:limit], "summary_count": len(summaries),
            "concurrent_windows": [[_compact_row(row) for row in group] for group in shown],
            "overlap_window_count": len(overlap), "available_station_count": len({row["station"] for row in rows}),
            "resource_ids": sorted({row["resource_id"] for row in rows}),
            "comparability": "Overlap requires exact same UTC window, signal, unit and native sampling interval at two or more stations",
            "limits": "Concurrent observations are not a regional cause; single-station coverage or missing data cannot establish multi-station change"}
