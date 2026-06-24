"""Tests for BPLAN and CORPUS parsers using small synthetic fixtures."""

import gzip
import json
import os
import tempfile
import zipfile

import pytest

from rail_sim.parsers.bplan import _parse_running_time, parse_bplan
from rail_sim.parsers.corpus import parse_corpus


# --- running time parser ---

def test_parse_running_time_normal():
    assert _parse_running_time("+37'00") == 2220
    assert _parse_running_time("+04'30") == 270
    assert _parse_running_time("+00'45") == 45


def test_parse_running_time_invalid():
    assert _parse_running_time("") is None
    assert _parse_running_time("37'00") is None  # missing +
    assert _parse_running_time("+bad") is None


# --- CORPUS parser ---

CORPUS_DATA = {
    "TIPLOCDATA": [
        {"NLC": 1, "STANOX": "12345", "TIPLOC": "STNA", "3ALPHA": "STA",
         "UIC": "0001", "NLCDESC": "STATION A", "NLCDESC16": "",
         "LATITUDE": "51.5", "LONGITUDE": "-0.1"},
        {"NLC": 2, "STANOX": "67890", "TIPLOC": "STNB", "3ALPHA": "STB",
         "UIC": "0002", "NLCDESC": "STATION B", "NLCDESC16": ""},
        {"NLC": 3, "STANOX": "", "TIPLOC": "", "3ALPHA": "",
         "UIC": "", "NLCDESC": "IGNORED", "NLCDESC16": ""},
    ]
}


@pytest.fixture()
def corpus_file(tmp_path):
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps(CORPUS_DATA))
    return str(p)


def test_corpus_tiploc_stanox(corpus_file):
    result = parse_corpus(corpus_file)
    assert result["tiploc_stanox"]["STNA"] == "12345"
    assert result["tiploc_stanox"]["STNB"] == "67890"


def test_corpus_stanox_tiplocs(corpus_file):
    result = parse_corpus(corpus_file)
    assert "STNA" in result["stanox_tiplocs"]["12345"]


def test_corpus_coords(corpus_file):
    result = parse_corpus(corpus_file)
    assert result["stanox_coords"]["12345"] == (51.5, -0.1)
    assert "67890" not in result["stanox_coords"]


def test_corpus_ignores_blank(corpus_file):
    result = parse_corpus(corpus_file)
    assert "" not in result["tiploc_stanox"]


# --- BPLAN parser ---

def _make_bplan_zip(tmp_path, lines: list[str]) -> str:
    content = "\r\n".join(lines) + "\r\n"
    gz_path = tmp_path / "bplan.gz"
    with gzip.open(gz_path, "wt", encoding="cp1252") as f:
        f.write(content)
    zip_path = tmp_path / "bplan.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(gz_path, arcname="bplan.gz")
    return str(zip_path)


TIPLOC_STANOX = {"STNA": "12345", "STNB": "67890", "STNC": "11111"}


def test_bplan_timing_parsed(tmp_path):
    lines = [
        "TLK\tA\tSTNA\tSTNB\t   \tXX\t     \t75\t \t0\t0\t01-01-2024 00:00:00\t\t+05'00",
        "TLK\tA\tSTNA\tSTNB\t   \tYY\t     \t75\t \t0\t0\t01-01-2024 00:00:00\t\t+07'00",
        "TLK\tA\tSTNB\tSTNC\t   \tXX\t     \t60\t \t0\t0\t01-01-2024 00:00:00\t\t+03'00",
    ]
    zip_path = _make_bplan_zip(tmp_path, lines)
    result = parse_bplan(zip_path, TIPLOC_STANOX)

    # Median of 5'00 and 7'00 = 6'00 = 360 s
    assert result["stanox_timing"][("12345", "67890")] == 360
    assert result["stanox_timing"][("67890", "11111")] == 180


def test_bplan_skips_zero_time(tmp_path):
    lines = [
        "TLK\tA\tSTNA\tSTNB\t   \tXX\t     \t75\t \t0\t0\t01-01-2024 00:00:00\t\t+00'00",
    ]
    zip_path = _make_bplan_zip(tmp_path, lines)
    result = parse_bplan(zip_path, TIPLOC_STANOX)
    assert ("12345", "67890") not in result["stanox_timing"]


def test_bplan_skips_unknown_tiploc(tmp_path):
    lines = [
        "TLK\tA\tUNKNOWN\tSTNB\t   \tXX\t     \t75\t \t0\t0\t01-01-2024 00:00:00\t\t+05'00",
    ]
    zip_path = _make_bplan_zip(tmp_path, lines)
    result = parse_bplan(zip_path, TIPLOC_STANOX)
    assert len(result["stanox_timing"]) == 0
