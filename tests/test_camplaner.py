from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from camplaner.geo import locate_on_route, split_route
from camplaner.gpx import read_gpx
from camplaner.models import Place
from camplaner.render import json_report, maps_url, render_report
from camplaner.cli import _load_dotenv, parser
from camplaner.sources import _dt_place, fetch_datatourisme, fetch_overpass, merge_places, opening_status


class CamplanerTests(unittest.TestCase):
    def test_reads_namespaced_gpx(self):
        xml = """<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Day Ride</name><trkseg><trkpt lat="43.0" lon="3.0"/><trkpt lat="43.1" lon="3.1"/></trkseg></trk></gpx>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ride.gpx"
            path.write_text(xml)
            name, points = read_gpx(path)
        self.assertEqual(name, "Day Ride")
        self.assertEqual(points, [(43.0, 3.0), (43.1, 3.1)])

    def test_locates_place_along_route(self):
        progress, offset = locate_on_route((0.0, 0.5), [(0.0, 0.0), (0.0, 1.0)])
        self.assertAlmostEqual(progress, 55.6, delta=0.2)
        self.assertAlmostEqual(offset, 0.0, delta=0.01)

    def test_splits_route_by_distance(self):
        route = [(0.0, index / 10) for index in range(6)]
        chunks = split_route(route, segment_km=20)
        self.assertGreater(len(chunks), 1)
        for previous, current in zip(chunks, chunks[1:]):
            self.assertEqual(previous[-1], current[0])

    def test_seasonal_opening(self):
        place = Place("Camp", 43, 3, "camp site", opening_raw=["Apr-Oct"])
        self.assertEqual(opening_status(place, date(2026, 7, 1))[0], "OPEN")
        self.assertEqual(opening_status(place, date(2026, 11, 1))[0], "CLOSED")

    def test_merges_nearby_named_duplicates(self):
        a = Place("Camping du Lac", 43, 3, "camp site", sources={"OpenStreetMap"})
        b = Place("Camping du Lac", 43.0001, 3.0001, "Camping", sources={"DATAtourisme"}, phone="123")
        merged = merge_places([a, b])
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].sources, {"OpenStreetMap", "DATAtourisme"})
        self.assertEqual(merged[0].phone, "123")

    def test_parses_datatourisme_contact_and_period(self):
        raw = {
            "label": {"fr": "Camping Test"}, "type": "Camping",
            "isLocatedAt": {"geo": {"latitude": 43.0, "longitude": 3.0}},
            "hasContact": [{"telephone": "+33 1", "homepage": "https://example.test"}],
            "openingHoursSpecification": [{"validFrom": "2026-04-01", "validThrough": "2026-10-31"}],
        }
        place = _dt_place(raw)
        self.assertIsNotNone(place)
        self.assertEqual(place.phone, "+33 1")
        self.assertEqual(place.opening_periods, [("2026-04-01", "2026-10-31")])

    def test_parses_datatourisme_list_location(self):
        raw = {
            "label": {"fr": "Camping List Location"}, "type": "Camping",
            "isLocatedAt": [{
                "geo": {"latitude": 43.25, "longitude": 3.5},
                "address": [{"streetAddress": "1 rue Test", "postalCode": "34000"}],
            }],
        }
        place = _dt_place(raw)
        self.assertIsNotNone(place)
        self.assertEqual((place.latitude, place.longitude), (43.25, 3.5))
        self.assertEqual(place.address, "1 rue Test, 34000")

    def test_report_contains_google_link(self):
        place = Place("A Camp", 43, 3, "camp site", sources={"OpenStreetMap"})
        hotel = Place("A Hotel", 43.1, 3.1, "hotel", sources={"OpenStreetMap"})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.md"
            render_report("Ride", [(43, 3), (43.1, 3.1)], [place, hotel], date(2026, 11, 12), 10, [], output)
            report = output.read_text()
        self.assertIn("Google Maps and reviews", report)
        self.assertIn("UNKNOWN", report)
        self.assertIn("### Campsites", report)
        self.assertIn("### Other accommodation", report)
        self.assertIn("google.com/maps/search", maps_url(place))

    def test_json_report_has_stable_machine_fields(self):
        place = Place("A Camp", 43, 3, "camp site", sources={"DATAtourisme"})
        payload = json.loads(json_report(
            "Ride", [(43, 3), (43.1, 3.1)], [place], date(2026, 11, 12), 10, [],
        ))
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["result_count"], 1)
        self.assertEqual(payload["results"][0]["category"], "campsites")
        self.assertIn("google_maps_url", payload["results"][0])

    def test_loads_key_from_dotenv_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, ".env").write_text("DATATOURISME_API_KEY=from-file\n")
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with patch.dict(os.environ, {"DATATOURISME_API_KEY": "already-set"}, clear=True):
                    loaded = _load_dotenv()
                    self.assertEqual(os.environ["DATATOURISME_API_KEY"], "already-set")
            finally:
                os.chdir(previous)
        self.assertEqual(loaded, Path(directory, ".env").resolve())

    def test_accommodation_type_flag(self):
        self.assertEqual(parser().parse_args(["route.gpx", "--date", "2026-11-12"]).type, "all")
        self.assertEqual(
            parser().parse_args(["route.gpx", "--date", "2026-11-12", "--type", "campsites"]).type,
            "campsites",
        )

    def test_place_category(self):
        self.assertEqual(Place("Camp", 43, 3, "FarmCamping").category, "campsites")
        self.assertEqual(Place("Hotel", 43, 3, "hotel").category, "other")

    def test_overpass_splits_long_route_and_deduplicates(self):
        route = [(43.0 + index / 1000, 3.0) for index in range(100)]
        response = {"elements": [{
            "type": "node", "id": 42, "lat": 43.01, "lon": 3.0,
            "tags": {"name": "Repeated Camp", "tourism": "camp_site"},
        }]}
        with patch("camplaner.sources._request_json", return_value=response) as request:
            places = fetch_overpass(route, 10)
        self.assertGreater(request.call_count, 1)
        self.assertEqual(len(places), 1)

    def test_datatourisme_queries_route_sections(self):
        route = [(43.0, 3.0), (43.2, 3.0), (43.4, 3.0), (43.6, 3.0)]
        response = {"objects": [], "meta": {"next": None}}
        with patch("camplaner.sources._request_json", return_value=response) as request:
            places = fetch_datatourisme(route, 10, "test-key")
        self.assertEqual(places, [])
        self.assertGreater(request.call_count, 1)

    def test_datatourisme_filters_campsites_at_source(self):
        response = {"objects": [], "meta": {"next": None}}
        with patch("camplaner.sources._request_json", return_value=response) as request:
            fetch_datatourisme(
                [(43.0, 3.0), (43.01, 3.01)], 10, "test-key", "campsites",
            )
        requested_url = request.call_args.args[0].full_url
        self.assertIn("Camping%2CFarmCamping", requested_url)
        self.assertNotIn("Hotel", requested_url)


if __name__ == "__main__":
    unittest.main()

