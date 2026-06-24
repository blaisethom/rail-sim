"""Parse Network Rail CORPUS JSON into usable lookup tables."""

import json


def parse_corpus(path: str) -> dict:
    """
    Returns a dict with:
      'tiploc_stanox': {tiploc -> stanox}
      'stanox_tiplocs': {stanox -> [tiploc, ...]}
      'stanox_coords': {stanox -> (lat, lon)}
      'tiploc_coords': {tiploc -> (lat, lon)}
      'stanox_name': {stanox -> name}
    """
    with open(path) as f:
        data = json.load(f)

    tiploc_stanox: dict[str, str] = {}
    stanox_tiplocs: dict[str, list[str]] = {}
    stanox_coords: dict[str, tuple[float, float]] = {}
    tiploc_coords: dict[str, tuple[float, float]] = {}
    stanox_name: dict[str, str] = {}

    for entry in data.get("TIPLOCDATA", []):
        tiploc = entry.get("TIPLOC", "").strip()
        stanox = entry.get("STANOX", "").strip()
        name = entry.get("NLCDESC", "").strip()

        if not tiploc or not stanox:
            continue

        tiploc_stanox[tiploc] = stanox
        stanox_tiplocs.setdefault(stanox, []).append(tiploc)
        if name:
            stanox_name.setdefault(stanox, name)

        lat_s = entry.get("LATITUDE")
        lon_s = entry.get("LONGITUDE")
        if lat_s and lon_s:
            try:
                lat, lon = float(lat_s), float(lon_s)
                if lat != 0.0 and lon != 0.0:
                    coords = (lat, lon)
                    stanox_coords[stanox] = coords
                    tiploc_coords[tiploc] = coords
            except (ValueError, TypeError):
                pass

    return {
        "tiploc_stanox": tiploc_stanox,
        "stanox_tiplocs": stanox_tiplocs,
        "stanox_coords": stanox_coords,
        "tiploc_coords": tiploc_coords,
        "stanox_name": stanox_name,
    }
