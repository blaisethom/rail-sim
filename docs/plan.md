# rail-sim: Plan

A discrete-event simulator for train movements over a real network topology, with a path toward calibrating against the live data produced by rail-data.

---

## Goals

1. **Simulate** train movements over a graph of the UK rail network given a timetable and a set of operating assumptions (block lengths, speed profiles, dwell times).
2. **Validate** those simulations against real observed train movements (sourced from rail-data).
3. **Calibrate** model parameters automatically so that simulated movements converge toward observed reality over time.

The long-run aim is a predictive model: given a timetable and current network state, predict where trains will be and when they will arrive.

---

## Domain background

### Location codes

The UK rail network uses several overlapping identifiers:

| Code | What it is | Used in |
|------|-----------|---------|
| **TIPLOC** | Train Identification Point Location — a point on the network (station, junction, signal, level crossing) | BPLAN, CIF, CORPUS |
| **STANOX** | 5-digit reporting point code — coarser-grained than TIPLOC, used in live reporting | NROD movement feed, CORPUS |
| **CRS** | 3-letter station code (e.g. KGX, MAN) | Public-facing; subset of TIPLOCs |
| **ELR** | Engineer's Line Reference — names a continuous track section | VectorLinks, engineering data |

CORPUS maps between these codes and provides lat/lon where available. TIPLOC is the key join field between topology (BPLAN) and timetable (CIF).

### Network topology: BPLAN

BPLAN is Network Rail's master geography file and the canonical source for rail topology in the UK. It contains two key record types:

- **Network Links** (`NL` records): directed connections between TIPLOC pairs, with distance in miles. This is the graph skeleton.
- **Timing Links** (`TL` records): same connections but annotated with speed restrictions, entry/exit speeds, and traction profiles per train class. This is what drives running-time estimation.

BPLAN is a tab-separated text file (Windows-1252 encoding, CRLF line endings) available from the [Rail Data Marketplace](https://data.atoc.org/rail-industry-data) under a free-to-register licence.

A BPLAN zip is already present in the rail-data repository root and can be used as the primary topology source.

### Timetable: CIF

CIF (Common Interface File) is the industry standard for GB rail schedules. It is a fixed-width 80-character-per-record text format. Key record types:

- `BS` / `BX`: Basic Schedule header — train UID, dates, days, category, TOC
- `LO`: Origin location — TIPLOC, departure time
- `LI`: Intermediate location — TIPLOC, arrival, departure, platform, activity codes
- `LT`: Terminating location — TIPLOC, arrival time

CIF gives the planned timetable: where each train stops and when. It does not contain speed data; that comes from BPLAN Timing Links.

CIF is available free from the NROD portal (same account as the movement feed in rail-data). There is also a REST download endpoint.

### Geospatial: VectorLinks

For mapping the network to lat/lon geometry, Network Rail publish [VectorLinks on GitHub](https://github.com/openraildata/network-rail-gis). These are GeoJSON/Shapefile layers of track centrelines, organised by ELR. Useful for rendering and for sanity-checking connectivity derived from BPLAN.

### Existing simulation tools

| Tool | Open source | Formats | Notes |
|------|-------------|---------|-------|
| **OSRD** | Yes (Apache 2.0) | Custom JSON, railML (partial) | Web-based; infrastructure design + capacity analysis + timetable simulation; actively developed |
| **OpenTrack** | No | Proprietary `.ott` | Industry standard; ETH Zurich; good reference for modelling concepts |
| **RailSys** | No | Proprietary | German; used for capacity studies |
| **SimPy** | Yes (MIT) | Any (Python library) | Not rail-specific; discrete-event engine; good base for a custom simulator |

OSRD is the closest to what we want but its format is evolving and it is complex. The approach here is to build a simpler, focused simulator that natively consumes BPLAN + CIF, and can later be compared against or integrated with OSRD.

---

## Architecture

### Data model

```
Network
├── nodes: Dict[tiploc, Node]
│     ├── tiploc: str
│     ├── stanox: str | None
│     ├── name: str
│     └── coords: (lat, lon) | None
└── edges: List[Edge]
      ├── from_tiploc: str
      ├── to_tiploc: str
      ├── distance_miles: float
      └── speed_profiles: List[SpeedProfile]  # per train class, from BPLAN TL records

Timetable
└── services: List[Service]
      ├── uid: str
      ├── headcode: str
      ├── toc: str
      ├── category: str
      └── calls: List[Call]
            ├── tiploc: str
            ├── scheduled_arrival: time | None
            ├── scheduled_departure: time | None
            ├── platform: str | None
            └── activity: List[str]

SimulationState
└── trains: Dict[uid, TrainState]
      ├── current_edge: Edge
      ├── position_miles: float   # miles from edge origin
      ├── speed_mph: float
      ├── delay_seconds: int
      └── status: Enum[running, at_platform, terminated, cancelled]
```

### Simulator core

A discrete-event simulation (initially using SimPy, or a hand-rolled event loop) where:

- **Events**: train departs origin, train reaches intermediate TIPLOC, train terminates
- **Clock**: simulation time advances to the next event; wall-clock time is not real-time
- **Running time calculation**: for each edge, compute scheduled running time using BPLAN speed profiles and distance; apply a parametric recovery/tolerance multiplier
- **Conflicts**: headway enforcement — if a train is too close behind another on the same edge, it is held or slowed
- **Dwell times**: taken from the CIF schedule gap between arrival and departure at each LI record; overridable by assumption

### Assumptions and parameters

All of these should be configurable (e.g. a TOML config file or CLI flags):

| Parameter | Default | Notes |
|-----------|---------|-------|
| `block_length_miles` | 1.0 | Minimum separation between trains on same section |
| `headway_seconds` | 180 | Minimum time gap at a TIPLOC |
| `dwell_override_seconds` | None | Override CIF dwell times |
| `speed_scaling` | 1.0 | Scale BPLAN speeds up/down globally |
| `recovery_factor` | 1.05 | Multiplier on running times (trains rarely run at max speed throughout) |
| `simulation_date` | today | Date to extract from CIF (schedules are date-dependent) |

---

## Phased build plan

### Phase 1 — Topology loader

**Goal**: Parse BPLAN into the `Network` data model. Parse CORPUS for TIPLOC→STANOX→lat/lon mapping. Persist as a SQLite database or serialised JSON for fast reuse.

**Deliverables**:
- `rail_sim/parsers/bplan.py` — parse NL and TL records from BPLAN zip
- `rail_sim/parsers/corpus.py` — parse CORPUS JSON from NROD
- `rail_sim/model/network.py` — `Network`, `Node`, `Edge`, `SpeedProfile` dataclasses
- CLI: `rail-sim build-network --bplan BPLAN.zip --corpus corpus.json --out network.db`
- Tests with a small synthetic BPLAN fixture

**Validation**: Load the BPLAN from rail-data, report node count, edge count, any disconnected sub-graphs.

### Phase 2 — Timetable loader

**Goal**: Parse a CIF file into the `Timetable` data model. Support filtering by date (CIF schedules have validity windows and day-of-week masks).

**Deliverables**:
- `rail_sim/parsers/cif.py` — parse BS/BX/LO/LI/LT records
- `rail_sim/model/timetable.py` — `Timetable`, `Service`, `Call` dataclasses
- CLI: `rail-sim build-timetable --cif timetable.cif --date 2026-06-24 --out timetable.db`

**Validation**: Load a CIF extract, report service count, compare with published timetable for a known date.

### Phase 3 — Simulator engine

**Goal**: Run a day's timetable over the network and produce a timeline of simulated train positions.

**Deliverables**:
- `rail_sim/sim/engine.py` — discrete-event loop; dispatches trains according to scheduled departure times, advances them along edges using BPLAN speeds, enforces headways
- `rail_sim/sim/output.py` — emit a stream of simulated movement events (TIPLOC, timestamp, delay) matching the NROD event schema where possible
- CLI: `rail-sim simulate --network network.db --timetable timetable.db [--config sim.toml] --out sim_output.json`

**Output format** (one JSON line per event):

```json
{
  "uid": "A12345",
  "headcode": "1A23",
  "tiploc": "KNGX",
  "stanox": "20010",
  "event_type": "DEPARTURE",
  "simulated_time": "2026-06-24T08:32:14Z",
  "scheduled_time": "2026-06-24T08:30:00Z",
  "delay_seconds": 134
}
```

### Phase 4 — Validation against real data

**Goal**: Compare simulated movements with the observed movements stored in the rail-data SQLite database. Produce per-service and aggregate accuracy metrics.

**Deliverables**:
- `rail_sim/validate/compare.py` — join simulated events to real events by (uid or headcode, tiploc, date); compute delay error, arrival-time error
- `rail_sim/validate/report.py` — render a summary: RMSE of delay prediction, % of calls within 1/2/5 minutes, worst-offending TOCs / routes
- CLI: `rail-sim validate --sim sim_output.json --real data/railmetrics.db --date 2026-06-24`

**Metrics**:
- Mean absolute error (MAE) of predicted vs actual delay at each STANOX
- % of TIPLOC-level calls correctly predicted within ±60s / ±120s / ±300s
- Per-TOC and per-route breakdown

**Note on TIPLOC↔STANOX join**: NROD reports STANOX; simulated events report TIPLOC. CORPUS provides the mapping. Some STANOXes map to multiple TIPLOCs (e.g. large stations); the join will need fuzzy matching by time window.

### Phase 5 — Calibration

**Goal**: Use observed data to tune model parameters (speed scaling, recovery factor, dwell times) so that the simulator's predictions converge toward observed reality.

**Approach**:
1. Run the simulator with default parameters; record error metrics
2. Use scipy `minimize` (or a simple grid search to start) to optimise the parameter set against the MAE metric on a held-out validation day
3. Persist the calibrated parameters in `calibrated.toml`
4. Repeat as new days of data accumulate

**Longer-term ML path**: Once we have enough observations (months of NROD data), replace the physics-based running-time calculation with a learned model (e.g. a gradient-boosted regression trained on edge×train-class→actual running time pairs). The simulator becomes a hybrid: physics for structure, ML for prediction.

---

## Generating topology from observed movements (alternative to BPLAN)

If BPLAN is not available or needs augmenting, the rail-data observations can bootstrap a topology:

1. **Node extraction**: every STANOX seen in the NROD feed is a node; CORPUS gives its lat/lon
2. **Edge inference**: for each train UID, the ordered sequence of (STANOX, timestamp) pairs defines directed edges; edges seen fewer than N times are discarded as noise
3. **Distance estimation**: haversine distance between CORPUS coordinates of adjacent STANOXes (crude — ignores track curvature)
4. **Speed estimation**: for each edge, compute the distribution of observed traversal times → infer a characteristic speed

This produces a coarser graph (STANOX-level, ~3,000 nodes) versus BPLAN (TIPLOC-level, ~9,000 nodes) but requires no external data beyond what rail-data already collects.

CLI: `rail-sim infer-network --real data/railmetrics.db --corpus corpus.json --min-observations 10 --out inferred_network.db`

---

## Repository structure

```
rail-sim/
├── docs/
│   └── plan.md                  # this file
├── rail_sim/
│   ├── parsers/
│   │   ├── bplan.py
│   │   ├── cif.py
│   │   └── corpus.py
│   ├── model/
│   │   ├── network.py
│   │   └── timetable.py
│   ├── sim/
│   │   ├── engine.py
│   │   └── output.py
│   └── validate/
│       ├── compare.py
│       └── report.py
├── tests/
│   └── fixtures/                # small synthetic BPLAN/CIF fragments
├── pyproject.toml
└── README.md
```

---

## Open questions

1. **BPLAN licence**: the BPLAN in rail-data was presumably obtained under the NROD / Rail Data Marketplace terms. Confirm that licence permits use in a separate (potentially public) repository, or keep the parsed network output (not the raw BPLAN) in rail-sim.

2. **CIF source**: decide whether to use the NROD CIF download or the open alternative at opendata.nationalrail.co.uk. The latter does not require an NROD subscription but may lag by a day.

3. **Simulation granularity**: BPLAN Timing Links are at TIPLOC level, but signals (berths) are finer-grained. Do we want signal-level simulation (needs TD feed + SMART database) or TIPLOC-level (simpler, enough for Phase 1–4)?

4. **OSRD interop**: OSRD is an active open-source project with a similar mission. Worth periodically checking whether their infrastructure format (which is evolving toward railML) becomes stable enough to target as an export format from this project.

5. **Real-time mode**: The simulator runs over a historical day in batch. A stretch goal is a real-time mode that ingests live NROD events and continuously updates predicted future positions for all active trains.

---

## Key external references

| Resource | URL | Notes |
|----------|-----|-------|
| Open Rail Data wiki | https://wiki.openraildata.com | BPLAN, CIF, CORPUS format specs |
| BPLAN data structure | https://wiki.openraildata.com/index.php/BPLAN_data_structure | Column-by-column spec |
| CIF file format | https://wiki.openraildata.com/index.php/CIF_File_Format | Record type reference |
| Rail Data Marketplace | https://data.atoc.org/rail-industry-data | BPLAN, CIF downloads (free registration) |
| VectorLinks (GIS) | https://github.com/openraildata/network-rail-gis | Geospatial track data |
| OSRD | https://github.com/OpenRailAssociation/osrd | Open-source rail simulation platform |
| NROD feeds | https://datafeeds.networkrail.co.uk | CIF, CORPUS, TD downloads |
