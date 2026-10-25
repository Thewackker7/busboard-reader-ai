from datetime import date, timedelta
import json
from pathlib import Path

from app.route_engine import (
    canonical_stop,
    find_journey_options,
    normalize_board_text,
    normalize_place_name,
    parse_board_route,
    verify_journey,
)

DATA_PATH = Path(__file__).parents[1] / "app" / "data" / "routes.json"
DEMO_DATA = json.loads(DATA_PATH.read_text(encoding="utf-8"))


def current_route_data(stops=None, board_destination="Ottapalam"):
    today = date.today()
    return {
        "data_status": "current_gtfs",
        "data_verified": True,
        "place_records": [
            {"id": "place:palakkad", "name": "Palakkad"},
            {"id": "place:ottapalam", "name": "Ottapalam"},
            {"id": "place:kanyakumari", "name": "Kanyakumari"},
        ],
        "service_end": (today + timedelta(days=30)).isoformat(),
        "stop_aliases": {
            "Palakkad": ["palakkad", "പാലക്കാട്"],
            "Manakara": ["manakara"],
            "Parali": ["parali", "parli"],
            "Thenur": ["thenur"],
            "Ottapalam": ["ottapalam", "ottappalam"],
            "Kanyakumari": ["kanyakumari", "kanyakuamri", "കന്യാകുമാരി"],
            "Thrissur": ["thrissur", "trichur"],
        },
        "routes": [{
            "id": "licensed-feed-test-trip",
            "board_destinations": [board_destination],
            "stops": stops or ["Palakkad", "Manakara", "Parali", "Thenur", "Ottapalam"],
            "source": "test fixture representing imported GTFS",
            "verified": True,
            "service_rules": [{
                "start_date": (today - timedelta(days=10)).strftime("%Y%m%d"),
                "end_date": (today + timedelta(days=30)).strftime("%Y%m%d"),
                "weekdays": list(range(7)),
                "exceptions": {},
            }],
        }],
    }


def test_normalization_is_unicode_and_case_insensitive():
    assert normalize_place_name("  PALAKKAD\t ") == "palakkad"
    assert normalize_place_name("KANYAKUMARI") == normalize_place_name("Kanyakumari")
    assert normalize_place_name("പാലക്കാട്") == "പാലക്കാട്"
    assert normalize_board_text("palakkad\u2013kanyakumari") == "palakkad - kanyakumari"
    assert normalize_board_text(" PALAKKAD   /   KANYAKUMARI ") == "palakkad - kanyakumari"
    assert normalize_board_text("Palakkad to Kanyakumari") == "palakkad - kanyakumari"
    assert normalize_board_text(None) == ""


def test_alias_and_ocr_transposition_resolve_case_insensitively():
    aliases = current_route_data()["stop_aliases"]
    assert canonical_stop(" ottappalam ", aliases) == "Ottapalam"
    assert canonical_stop("KANYAKUMARI", aliases) == "Kanyakumari"
    assert canonical_stop("kanyakuamri", aliases) == "Kanyakumari"
    assert canonical_stop("Palakkad district", aliases) is None


def test_board_route_parser_requires_known_places_on_both_sides_of_separator():
    aliases = current_route_data()["stop_aliases"]
    assert parse_board_route("PALAKKAD - KANYAKUAMRI", aliases) == {"origin": "Palakkad", "destination": "Kanyakumari"}
    assert parse_board_route("Palakkad to Ottapalam", aliases) == {"origin": "Palakkad", "destination": "Ottapalam"}
    assert parse_board_route("PALAKKAD - FAST", aliases) == {"origin": None, "destination": None}
    assert parse_board_route("PALAKKAD / ???", aliases, "Kanyakumari") == {"origin": "Palakkad", "destination": "Kanyakumari"}
    assert parse_board_route("കന്യാകുമാരി", aliases)["destination"] == "Kanyakumari"


def test_current_route_supports_ordered_intermediate_stops_and_case_variants():
    result = verify_journey("PALAKKAD", "PARALI", "Palakkad \u2013 Ottapalam", current_route_data(), confidence=None)
    assert result["status"] == "match"
    assert result["verification_status"] == "MATCH"
    assert result["matched_stops"] == ["Palakkad", "Manakara", "Parali"]
    assert result["data_verified"] is True
    assert result["board_text_original"] == "Palakkad \u2013 Ottapalam"


def test_current_route_can_match_ui_place_label_without_case_error():
    data = current_route_data(stops=["Palakkad", "Kanyakumari"], board_destination="Kanyakumari")
    result = verify_journey("PALAKKAD", "Kanyakumari district \u00b7 Tamil Nadu", "PALAKKAD - KANYAKUMARI", data)
    assert result["status"] == "match"
    assert result["matched_stops"] == ["Palakkad", "Kanyakumari"]
    assert result["selected_origin_id"] == "place:palakkad"
    assert result["selected_destination_id"] == "place:kanyakumari"
    assert result["board_destination_id"] == "place:kanyakumari"


def test_source_backed_palakkad_kanyakumari_record_matches_unseparated_board_text():
    result = verify_journey("Palakkad", "Kanyakumari", "PALAKKAD KANYAKUMARI", DEMO_DATA)
    assert result["status"] == "match"
    assert result["matched_route_id"] == "ksrtc-palakkad-kanyakumari-minnal-deluxe"
    assert result["board_text_normalized"] == "palakkad kanyakumari"
    assert result["matched_stops"] == [
        "Palakkad", "Thrissur", "Angamaly", "Vyttila Hub", "Alappuzha", "Kollam",
        "Kazhakkoottam", "Thiruvananthapuram", "Neyyattinkara", "Kaliyakkavilai",
        "Nagercoil", "Kanyakumari",
    ]
    assert result["route_evidence"]["source_url"].startswith("https://")
    assert result["route_evidence"]["last_verified_at"] == "2026-08-07"


def test_malayalam_board_uses_english_route_interpretation_for_matching():
    data = current_route_data(stops=["Palakkad", "Kanyakumari"], board_destination="Kanyakumari")
    result = verify_journey(
        "Palakkad", "Kanyakumari", "പാലക്കാട് - കന്യാകുമാരി", data,
        english_route="Palakkad - Kanyakumari", english_destination="Kanyakumari",
    )
    assert result["status"] == "match"
    assert result["board_text_original"] == "പാലക്കാട് - കന്യാകുമാരി"
    assert result["board_text_english"] == "Palakkad - Kanyakumari"


def test_mismatch_requires_one_identified_current_route_and_correct_direction():
    data = current_route_data(stops=["Palakkad", "Manakara", "Ottapalam"])
    data["coverage_complete"] = True
    result = verify_journey("Ottapalam", "Palakkad", "PALAKKAD - OTTAPALAM", data)
    assert result["status"] == "mismatch"
    assert result["verification_status"] == "MISMATCH"
    assert result["matched_route_id"] == "licensed-feed-test-trip"
    assert result["data_verified"] is True


def test_unverified_demo_routes_stale_data_and_unknown_routes_are_unable_to_verify():
    result = verify_journey("Ottapalam", "Thrissur", "THRISSUR", DEMO_DATA)
    assert result["status"] == "unable_to_verify"
    assert result["verification_status"] == "UNABLE_TO_VERIFY"
    assert result["matched_route_id"] is None
    assert result["data_verified"] is False
    assert "No current verified route record" in result["reason"]

    stale = current_route_data(stops=["Palakkad", "Kanyakumari"], board_destination="Kanyakumari")
    stale["data_status"] = "stale_or_inactive_gtfs"
    stale["service_end"] = "2023-03-25"
    stale["data_verified"] = False
    assert verify_journey("Palakkad", "Kanyakumari", "Palakkad - Kanyakumari", stale)["status"] == "unable_to_verify"

    current = current_route_data(stops=["Palakkad", "Kanyakumari"], board_destination="Kanyakumari")
    assert verify_journey("Palakkad", "Kanyakumari", "Unfamiliar Place", current)["status"] == "unable_to_verify"
    assert verify_journey("Unknown origin", "Kanyakumari", "Kanyakumari", current)["status"] == "unable_to_verify"


def test_multiple_unresolved_board_route_candidates_do_not_confirm_mismatch():
    data = current_route_data(stops=["Palakkad", "Manakara", "Ottapalam"])
    duplicate = dict(data["routes"][0], id="second-trip")
    data["routes"].append(duplicate)
    assert verify_journey("Ottapalam", "Palakkad", "PALAKKAD - OTTAPALAM", data)["status"] == "unable_to_verify"


def test_journey_options_preserve_ordered_direct_and_transfer_paths():
    direct = current_route_data()["routes"]
    options = find_journey_options("Palakkad", "Ottapalam", direct)
    assert options and options[0]["stops"] == ["Palakkad", "Manakara", "Parali", "Thenur", "Ottapalam"]
    transfer = [
        {"id": "A", "stops": ["Palakkad", "Manakara"]},
        {"id": "B", "stops": ["Manakara", "Ottapalam"]},
    ]
    assert find_journey_options("Palakkad", "Ottapalam", transfer)[0]["transfers"] == 1


def test_current_partial_feed_cannot_confirm_a_mismatch():
    data = current_route_data(stops=["Palakkad", "Manakara", "Ottapalam"])
    result = verify_journey("Ottapalam", "Palakkad", "PALAKKAD - OTTAPALAM", data)
    assert result["status"] == "unable_to_verify"
    assert "complete coverage" in result["reason"]
