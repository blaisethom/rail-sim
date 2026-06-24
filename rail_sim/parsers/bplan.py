"""Parse Network Rail BPLAN geography file (NWK + TLK records)."""

import gzip
import statistics
import zipfile
from collections import defaultdict


def _parse_running_time(s: str) -> int | None:
    """Parse '+MM\'SS' format to seconds. Returns None if malformed."""
    s = s.strip()
    if not s.startswith("+"):
        return None
    s = s[1:]
    if "'" not in s:
        return None
    mins_s, secs_s = s.split("'", 1)
    try:
        return int(mins_s) * 60 + int(secs_s)
    except ValueError:
        return None


def parse_bplan(bplan_zip_path: str, tiploc_stanox: dict[str, str]) -> dict:
    """
    Parse BPLAN zip file.

    Returns:
        'stanox_timing': {(from_stanox, to_stanox): median_seconds}  — primary lookup
        'n_locs': int
        'n_nwk': int
        'n_tlk': int
        'n_timing_pairs': int
    """
    raw_timing: dict[tuple[str, str], list[int]] = defaultdict(list)
    n_locs = n_nwk = n_tlk = 0

    with zipfile.ZipFile(bplan_zip_path) as z:
        fname = z.namelist()[0]
        with z.open(fname) as zf:
            with gzip.open(zf) as g:
                for line in g:
                    txt = line.decode("cp1252", errors="replace").rstrip("\r\n")
                    if txt.startswith("LOC\t"):
                        n_locs += 1
                    elif txt.startswith("NWK\t"):
                        n_nwk += 1
                    elif txt.startswith("TLK\t"):
                        n_tlk += 1
                        parts = txt.split("\t")
                        if len(parts) < 14:
                            continue
                        from_tiploc = parts[2].strip()
                        to_tiploc = parts[3].strip()
                        secs = _parse_running_time(parts[13])
                        if secs is None or secs <= 0:
                            continue
                        from_stanox = tiploc_stanox.get(from_tiploc)
                        to_stanox = tiploc_stanox.get(to_tiploc)
                        if from_stanox and to_stanox and from_stanox != to_stanox:
                            raw_timing[(from_stanox, to_stanox)].append(secs)

    stanox_timing = {
        pair: int(statistics.median(times))
        for pair, times in raw_timing.items()
    }

    return {
        "stanox_timing": stanox_timing,
        "n_locs": n_locs,
        "n_nwk": n_nwk,
        "n_tlk": n_tlk,
        "n_timing_pairs": len(stanox_timing),
    }
