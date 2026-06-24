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

### Phase 4 — Validation against Darwin baseline

**Goal**: Compare simulated movements against both (a) the observed NROD movements in rail-data and (b) the predictions already made by Darwin, which is Network Rail's own real-time prediction system. Darwin is the industry baseline — if our model can match or beat it, that is a meaningful result.

**What Darwin is**: Darwin is Network Rail's journey information system. It ingests CIF timetable data and real-time TRUST movement reports and produces continuously-updated predicted arrival/departure times for every train at every stop. These predictions are what National Rail Enquiries displays on departure boards. Darwin Push Port publishes an XML feed of these predictions via STOMP (same protocol as NROD).

**Darwin data access**:
- Register at the Darwin Data Feeds portal (linked from networkrail.co.uk/open-data-feeds)
- Subscribe to the Darwin Push Port: `darwin.pushport.v16` feed (XML, STOMP)
- Alternatively, the OpenLDBWS SOAP API (free, rate-limited) returns Darwin predictions per-station
- The rail-data pipeline can be extended to simultaneously ingest Darwin predictions alongside TRUST movement reports

**Validation strategy**:

| Comparison | What it measures |
|-----------|-----------------|
| Our sim vs actual NROD | Absolute timing error of our physics model |
| Darwin vs actual NROD | Darwin's prediction accuracy (the industry baseline) |
| Our sim vs Darwin | Whether we are better or worse than the current system |

If Darwin is systematically more accurate, the gap reveals what information we lack (typically: knowledge of current network congestion, signal state, and TSR speed restrictions). If our model is within Darwin's error envelope, it is potentially useful as a standalone predictor.

**Deliverables**:
- Extend rail-data to optionally capture Darwin predictions alongside NROD movements
- `rail_sim/validate/darwin.py` — join Darwin predicted times to NROD actual times and rail-sim predicted times, compute comparative metrics

**Note on TIPLOC↔STANOX join**: NROD reports STANOX; simulated events report TIPLOC. CORPUS provides the mapping. Some STANOXes map to multiple TIPLOCs (e.g. large stations); the join will need fuzzy matching by time window.

### Phase 5 — Baseline calibration

**Goal**: Use observed data to tune model parameters (speed scaling, recovery factor, dwell times) so that the simulator's predictions converge toward observed reality.

**Current result** (first trained model, June 2026):
- Calibrated `time_scale = 1.0` — BPLAN timing links are accurate for typical UK running conditions
- Training MAE: 91.7s, Held-out MAE: 95.8s (June 23 → June 24)
- 67% of stop predictions within 60s, 95% within 5 minutes
- The residual ~92s error is dominated by variable dwell times, signal holds, and unmodelled delay propagation

**Approach** (current):
1. Run the simulator with default parameters; record error metrics
2. Use a grid search to optimise `time_scale` (and later, per-TOC scales) against the MAE metric on a training day
3. Persist the calibrated parameters in `data/model.json`
4. Repeat as new days of data accumulate

### Phase 6 — ML-based prediction: GNN and spatio-temporal transformer

**Goal**: Replace or augment the physics-based running-time model with a learned model capable of capturing network-wide delay propagation — the effect where a delay at one station ripples to downstream stations and connecting services.

**Why the physics model has a ceiling**: The BPLAN timing model assumes each leg runs at its scheduled speed independent of all other trains. In reality, delays propagate: a late train blocks a platform, its passengers miss connections, the connecting service waits and incurs a secondary delay. These effects are spatial (nearby stations are correlated) and temporal (current delays are correlated with past delays).

#### Option A: Graph Neural Network (GNN)

**Architecture**:
- **Nodes**: stations / STANOXes, each with a feature vector: current delay (seconds), time of day, day of week, TOC
- **Edges**: track connections (from BPLAN NWK), with edge features: scheduled running time, distance, current occupancy (number of trains on the edge)
- **Message passing**: for each prediction step, each node aggregates delay information from its neighbours, weighted by edge running times; this propagates delay signals through the network
- **Output**: predicted delay at each node for the next N minutes

**Why GNN fits here**: The UK rail network is naturally a graph. Delay propagation is a message-passing process — exactly what GNNs are designed to model. Edge structure is provided by BPLAN; node state comes from NROD observations.

**Libraries**: PyTorch Geometric (`torch_geometric`) or DGL. Both support sparse graph convolution at UK-network scale (~4,500 active STANOXes).

**Training data requirements**: Weeks of NROD movement data (the rail-data pipeline is already collecting this). Each training example is a network snapshot (delay vector at all nodes at time T) with a target (delay vector at time T+Δ).

#### Option B: Spatio-temporal transformer

**Architecture**:
- **Token**: one (train, STANOX) pair = one token
- **Spatial attention**: a token attends to other tokens at nearby STANOXes on the same time step — learns which stations are correlated
- **Temporal attention**: a token attends to the same (train, STANOX) token across past time steps — learns how a train's delay history predicts its future
- **Positional encoding**: STANOX lat/lon encoded as a 2D position; time encoded as sinusoidal

**Why transformer fits here**: Transformers naturally handle variable-length sequences and can learn long-range dependencies. A train running from London to Edinburgh can attend to the state of intermediate stations even when they are many edges apart in the graph.

**Libraries**: Standard PyTorch; no graph library needed. Sequence length = number of calling points × history window.

#### Comparison to Darwin

Both architectures, once trained, produce predicted arrival times. The comparison target is Darwin's predictions (Phase 4). Darwin uses a rule-based system seeded by TRUST movement data. The GNN/transformer can potentially outperform it by:
1. Learning delay correlation patterns across the network (Darwin's rules are local)
2. Incorporating longer historical context than Darwin's rolling window

#### Recommended sequencing

1. Complete Phase 5 calibration with several weeks of data
2. Implement Darwin ingestion (Phase 4) to establish the comparison baseline
3. Train a GNN as Phase 6a — simpler to implement, natural fit for the graph structure
4. Train a transformer as Phase 6b — higher ceiling but more data-hungry
5. Evaluate both against Darwin on held-out weeks; ensemble if both add signal

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

**Longer-term calibration extensions**:
- Per-TOC `time_scale` values (the current TOC breakdown shows wide variation: TOC 30 at 52s MAE vs TOC 40 at 1325s MAE)
- Per-edge running time corrections learned from observed (from_stanox, to_stanox, actual_seconds) pairs
- Time-of-day effects (peak vs off-peak)

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

## Current results (baseline model, June 2026)

First trained model on real NROD data:

| Metric | Training (Jun 23) | Held-out (Jun 24) |
|--------|------------------|------------------|
| Predictions | 185,965 | 87,870 |
| MAE | 91.7 s | 95.8 s |
| RMSE | 223.9 s | 293.9 s |
| Median error | 60 s | 60 s |
| Within 60s | 65.8% | 66.9% |
| Within 120s | 84.2% | 84.8% |
| Within 300s | 96.0% | 95.7% |

Calibrated `time_scale = 1.0` — BPLAN timing links already model typical UK rail running times accurately. The residual error is predominantly variable dwell times, signal holds, and delay propagation (none of which the physics model captures). The next benchmark is Darwin's prediction accuracy on the same days.

---

## Open questions

1. **BPLAN licence**: the BPLAN in rail-data was presumably obtained under the NROD / Rail Data Marketplace terms. Confirm that licence permits use in a separate (potentially public) repository, or keep the parsed network output (not the raw BPLAN) in rail-sim.

2. **CIF source**: decide whether to use the NROD CIF download or the open alternative at opendata.nationalrail.co.uk. The latter does not require an NROD subscription but may lag by a day.

3. **Simulation granularity**: BPLAN Timing Links are at TIPLOC level, but signals (berths) are finer-grained. Do we want signal-level simulation (needs TD feed + SMART database) or TIPLOC-level (simpler, enough for Phase 1–5)?

4. **OSRD interop**: OSRD is an active open-source project with a similar mission. Worth periodically checking whether their infrastructure format (which is evolving toward railML) becomes stable enough to target as an export format from this project.

5. **Real-time mode**: The simulator runs over a historical day in batch. A stretch goal is a real-time mode that ingests live NROD events and continuously updates predicted future positions for all active trains.

6. **Darwin data registration**: Darwin Push Port requires a separate registration from NROD (though the same Network Rail account is used). This should be set up as soon as possible so we can accumulate Darwin prediction data alongside NROD actuals for comparison.

7. **GNN vs transformer data requirements**: The GNN approach is viable with weeks of data; the transformer likely needs months for the temporal attention patterns to be meaningful. Phase 6a (GNN) should be started once ~4 weeks of NROD data are available.

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
| Darwin Open Data | https://www.networkrail.co.uk/who-we-are/transparency-and-ethics/transparency/open-data-feeds/ | Darwin Push Port registration |
| OpenLDBWS (Darwin API) | https://lite.realtime.nationalrail.co.uk/OpenLDBWS/ | SOAP API for Darwin predictions (rate-limited, free) |
| PyTorch Geometric | https://pytorch-geometric.readthedocs.io | GNN library for Phase 6a |
