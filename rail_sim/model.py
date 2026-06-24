"""Network model: pre-computed lookup tables used by the simulator."""

import json
import math


def haversine_seconds(lat1: float, lon1: float, lat2: float, lon2: float,
                      speed_kmh: float = 80.0) -> int:
    """Straight-line distance travel time at speed_kmh. Returns seconds."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    dist_km = 2 * R * math.asin(math.sqrt(a))
    return max(30, int(dist_km / speed_kmh * 3600))


class Network:
    """Pre-built lookup tables for running times between STANOXes."""

    DEFAULT_SECONDS = 180  # 3-minute fallback when no data exists

    def __init__(self, data: dict):
        self._timing: dict[str, int] = data["stanox_timing"]
        self._coords: dict[str, list[float]] = data["stanox_coords"]
        self._names: dict[str, str] = data["stanox_names"]

    def running_time(self, from_stanox: str, to_stanox: str) -> int:
        """Return estimated running time in seconds between two STANOXes."""
        key = f"{from_stanox}|{to_stanox}"
        t = self._timing.get(key)
        if t is not None:
            return t

        fc = self._coords.get(from_stanox)
        tc = self._coords.get(to_stanox)
        if fc and tc:
            return haversine_seconds(fc[0], fc[1], tc[0], tc[1])

        return self.DEFAULT_SECONDS

    def coords(self, stanox: str) -> tuple[float, float] | None:
        c = self._coords.get(stanox)
        return (c[0], c[1]) if c else None

    def name(self, stanox: str) -> str:
        return self._names.get(stanox, stanox)

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump({
                "stanox_timing": self._timing,
                "stanox_coords": self._coords,
                "stanox_names": self._names,
            }, f)

    @classmethod
    def load(cls, path: str) -> "Network":
        with open(path) as f:
            return cls(json.load(f))

    @classmethod
    def build(cls, bplan_zip: str, corpus_json: str) -> "Network":
        from rail_sim.parsers.corpus import parse_corpus
        from rail_sim.parsers.bplan import parse_bplan

        print("Parsing CORPUS…")
        corpus = parse_corpus(corpus_json)

        print("Parsing BPLAN (this takes ~30 s)…")
        bplan = parse_bplan(bplan_zip, corpus["tiploc_stanox"])

        # Serialise timing as "A|B" -> int for JSON compactness
        timing = {
            f"{a}|{b}": s
            for (a, b), s in bplan["stanox_timing"].items()
        }

        # Coords: list[float] so JSON stays small
        coords = {
            stanox: list(latlon)
            for stanox, latlon in corpus["stanox_coords"].items()
        }

        print(
            f"Network: {bplan['n_locs']} LOC, {bplan['n_nwk']} NWK, "
            f"{bplan['n_tlk']} TLK → {bplan['n_timing_pairs']} STANOX timing pairs"
        )

        return cls({
            "stanox_timing": timing,
            "stanox_coords": coords,
            "stanox_names": corpus["stanox_name"],
        })
