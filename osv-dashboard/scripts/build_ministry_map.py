#!/usr/bin/env python3
"""Validate and package supplied municipality polygons for the ministry map."""
import gzip
import json
import re
import sys
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))
from ministry_geo import MUNICIPALITIES  # noqa: E402


DEFAULT_SOURCE = (
    PROJECT_DIR.parent
    / "attached_assets"
    / "Границы_МО_2026_1792495899499.geojson"
)
DEFAULT_OUTPUT = PROJECT_DIR / "static" / "ministry_municipalities.geojson.gz"
COORDINATE_PRECISION = 5


def normalize_name(value):
    text = str(value or "").strip().casefold().replace("ё", "е")
    text = re.sub(r"\s+(?:г|м)\.?\s*о\.?\s*$", "", text)
    text = re.sub(r"\s+(?:городской|муниципальный)\s+округ\s*$", "", text)
    return re.sub(r"[^0-9a-zа-я]+", " ", text).strip()


def polygon_rings(geometry):
    if geometry.get("type") == "Polygon":
        return geometry.get("coordinates", [])
    if geometry.get("type") == "MultiPolygon":
        return [
            ring
            for polygon in geometry.get("coordinates", [])
            for ring in polygon
        ]
    raise ValueError(f"Unsupported geometry type: {geometry.get('type')!r}")


def round_coordinates(value):
    if isinstance(value, dict):
        return {key: round_coordinates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [round_coordinates(item) for item in value]
    if isinstance(value, (int, float)):
        return round(value, COORDINATE_PRECISION)
    return value


def build_feature(source_feature, canonical_by_name):
    properties = source_feature.get("properties") or {}
    raw_name = properties.get("DESCR")
    canonical = canonical_by_name.get(normalize_name(raw_name))
    if canonical is None:
        raise ValueError(f"Unknown municipality label: {raw_name!r}")

    geometry = source_feature.get("geometry") or {}
    rings = polygon_rings(geometry)
    if not rings:
        raise ValueError(f"No polygon rings for {raw_name!r}")
    for ring in rings:
        if len(ring) < 4 or ring[0][:2] != ring[-1][:2]:
            raise ValueError(f"Open or too-short polygon ring for {raw_name!r}")
        for longitude, latitude, *_rest in ring:
            if not (30 <= longitude <= 45 and 50 <= latitude <= 60):
                raise ValueError(
                    f"Coordinates outside Moscow Oblast bounds for {raw_name!r}"
                )

    name, kind = canonical
    return {
        "type": "Feature",
        "id": name,
        "properties": {
            "territory": name,
            "kind": kind,
            "okato": str(properties.get("okato") or ""),
            "oktmo": str(properties.get("oktmo") or ""),
            "sourceRecordId": str(properties.get("id") or ""),
        },
        "geometry": round_coordinates(geometry),
    }


def build_collection(source_path):
    with source_path.open("r", encoding="utf-8") as source_file:
        source = json.load(source_file)
    if source.get("type") != "FeatureCollection":
        raise ValueError("The supplied file is not a GeoJSON FeatureCollection.")

    canonical_by_name = {
        normalize_name(name): (name, kind) for name, kind in MUNICIPALITIES
    }
    features = [
        build_feature(feature, canonical_by_name)
        for feature in source.get("features", [])
    ]
    names = [feature["properties"]["territory"] for feature in features]
    if len(features) != len(MUNICIPALITIES):
        raise ValueError(
            f"Expected {len(MUNICIPALITIES)} polygons, received {len(features)}."
        )
    if len(set(names)) != len(names):
        raise ValueError("The supplied file has duplicate municipality names.")
    missing = sorted(set(canonical_by_name[name][0] for name in canonical_by_name) - set(names))
    if missing:
        raise ValueError(f"Municipalities missing from the supplied file: {missing}")

    features.sort(key=lambda feature: feature["properties"]["territory"].casefold())
    return {
        "type": "FeatureCollection",
        "features": features,
    }


def main():
    source_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_SOURCE
    output_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else DEFAULT_OUTPUT
    collection = build_collection(source_path)
    payload = json.dumps(
        collection, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(compressed)
    print(
        f"Validated {len(collection['features'])} municipalities; "
        f"wrote {len(payload):,} bytes as {len(compressed):,} gzip bytes "
        f"to {output_path}."
    )


if __name__ == "__main__":
    main()
