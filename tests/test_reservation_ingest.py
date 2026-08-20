import email.message
import json

import pytest

from generator.manifest_parser import ManifestParser
from generator.reservation_ingest import (
    build_sidecar,
    email_to_text,
    match_destination,
    merge_sidecar_into_trip,
    reservation_to_manifest_fragment,
    score_destination_match,
    write_sidecar,
)

DESTINATIONS = [
    {"id": "zion", "name": "Zion National Park", "dates": "October 7-9, 2026",
     "lodging": {"location": "Zion Lodge, Springdale, UT"}},
    {"id": "moab", "name": "Moab", "dates": "October 13-15, 2026",
     "lodging": {"location": "Moab Valley Inn, Moab, UT"}},
    {"id": "santafe", "name": "Santa Fe", "dates": "October 19-21, 2026",
     "lodging": {"location": "Inn of the Governors, Santa Fe, NM"}},
]


def _msg(subject: str, body: str, subtype: str = "plain") -> bytes:
    m = email.message.EmailMessage()
    m["Subject"] = subject
    m["From"] = "confirmations@hotel.example"
    m.set_content(body, subtype=subtype)
    return m.as_bytes()


def test_email_to_text_reads_plain_body() -> None:
    subject, body = email_to_text(_msg("Your stay is confirmed", "Confirmation ZL-4471902"))

    assert subject == "Your stay is confirmed"
    assert "ZL-4471902" in body


def test_email_to_text_falls_back_to_stripped_html() -> None:
    """Many confirmation emails ship HTML only, so an HTML-only message must
    still yield usable text rather than an empty body."""
    html = "<html><style>p{color:red}</style><body><p>Conf <b>XR7Q2M</b></p></body></html>"
    subject, body = email_to_text(_msg("Flight", html, subtype="html"))

    assert "XR7Q2M" in body
    assert "<b>" not in body
    assert "color:red" not in body  # <style> contents dropped, not just tags


def test_email_to_text_truncates_long_bodies() -> None:
    subject, body = email_to_text(_msg("x", "y" * 50000))

    assert len(body) <= 12000


def test_score_prefers_the_destination_whose_city_matches() -> None:
    reservation = {"kind": "lodging", "city": "Springdale, UT", "dates": "October 7, 2026"}

    zion = score_destination_match(reservation, DESTINATIONS[0])
    moab = score_destination_match(reservation, DESTINATIONS[1])

    assert zion > moab


def test_match_attaches_a_confident_reservation() -> None:
    reservation = {"kind": "lodging", "city": "Springdale, UT",
                   "location": "Zion Lodge", "dates": "October 7-9, 2026"}

    dest_id, ranked = match_destination(reservation, DESTINATIONS)

    assert dest_id == "zion"
    assert ranked[0]["id"] == "zion"


def test_match_defers_to_a_human_when_nothing_scores_well() -> None:
    """A booking we can't place must go to review, not to the least-bad guess:
    nothing downstream would ever flag a hotel filed under the wrong stop."""
    reservation = {"kind": "lodging", "city": "Reykjavik, Iceland", "dates": "March 2, 2027"}

    dest_id, _ = match_destination(reservation, DESTINATIONS)

    assert dest_id is None


def test_match_defers_when_two_destinations_score_near_identically() -> None:
    """A near-tie is exactly where an automatic pick files things wrongly."""
    tied = [
        {"id": "a", "name": "Springdale Utah", "dates": "October 7, 2026"},
        {"id": "b", "name": "Springdale Utah", "dates": "October 7, 2026"},
    ]
    reservation = {"kind": "lodging", "city": "Springdale, UT", "dates": "October 7, 2026"}

    dest_id, ranked = match_destination(reservation, tied)

    assert dest_id is None
    assert len(ranked) == 2


def test_fragment_drops_fields_the_email_never_stated() -> None:
    """A partial extraction must fill only what it knows, so the manifest's own
    values survive for everything else."""
    section, fragment = reservation_to_manifest_fragment(
        {"kind": "transportation", "type": "plane", "provider": "United",
         "confirmation_number": "XR7Q2M", "depart": "", "arrive": "", "website": ""}
    )

    assert section == "transportation"
    assert fragment == {"type": "plane", "provider": "United", "confirmation_number": "XR7Q2M"}


def test_fragment_maps_unknown_transport_type_to_other() -> None:
    _, fragment = reservation_to_manifest_fragment({"kind": "transportation", "type": "ferry"})

    assert fragment["type"] == "other"


def test_build_sidecar_routes_confident_and_unconfident_reservations() -> None:
    entries = [
        {"source": {"uid": "1"}, "reservation": {
            "kind": "lodging", "name": "Zion Lodge", "city": "Springdale, UT",
            "location": "Zion Lodge, Springdale UT", "confirmation_number": "ZL-1",
            "dates": "October 7-9, 2026"}},
        {"source": {"uid": "2"}, "reservation": {
            "kind": "lodging", "city": "Reykjavik", "confirmation_number": "RK-9"}},
        {"source": {"uid": "3"}, "reservation": {"kind": "none"}},
    ]

    sidecar = build_sidecar(entries, DESTINATIONS)

    assert sidecar["destinations"]["zion"]["lodging"]["confirmation_number"] == "ZL-1"
    assert len(sidecar["pending"]) == 1
    assert sidecar["pending"][0]["reservation"]["confirmation_number"] == "RK-9"


def test_build_sidecar_is_idempotent_on_the_same_confirmation() -> None:
    """Re-forwarding an email, or re-polling before messages were flagged read,
    must not duplicate a booking."""
    entry = {"source": {"uid": "1"}, "reservation": {
        "kind": "transportation", "type": "plane", "provider": "United",
        "city": "Springdale, UT", "confirmation_number": "XR7Q2M",
        "dates": "October 7-9, 2026"}}

    first = build_sidecar([entry], DESTINATIONS)
    second = build_sidecar([entry], DESTINATIONS, existing=first)

    assert len(second["destinations"]["zion"]["transportation"]) == 1


def test_merge_fills_empty_fields_but_never_overwrites_the_manifest() -> None:
    """A human wrote the manifest value; a model read the sidecar one out of an
    email. The human wins."""
    trip = {"destinations": [
        {"id": "zion", "lodging": {"name": "Zion Lodge", "location": "Springdale, UT"}},
    ]}
    sidecar = {"destinations": {"zion": {"lodging": {
        "name": "Zion Lodge Resort & Spa",       # conflicts -- must be ignored
        "confirmation_number": "ZL-4471902",     # absent -- must be filled
    }}}}

    counts = merge_sidecar_into_trip(trip, sidecar)

    lodging = trip["destinations"][0]["lodging"]
    assert lodging["name"] == "Zion Lodge"
    assert lodging["confirmation_number"] == "ZL-4471902"
    assert counts["lodging_fields"] == 1


def test_merge_appends_legs_and_dedupes_on_confirmation_number() -> None:
    trip = {"destinations": [{"id": "zion", "transportation": [
        {"type": "plane", "confirmation_number": "XR7Q2M"},
    ]}]}
    sidecar = {"destinations": {"zion": {"transportation": [
        {"type": "plane", "confirmation_number": "XR7Q2M"},   # already present
        {"type": "car", "confirmation_number": "H99120"},     # new
    ]}}}

    counts = merge_sidecar_into_trip(trip, sidecar)

    legs = trip["destinations"][0]["transportation"]
    assert len(legs) == 2
    assert counts["transportation_legs"] == 1


def test_merge_ignores_a_sidecar_pointing_at_an_unknown_destination() -> None:
    """Renaming a destination id must not crash the build with a KeyError."""
    trip = {"destinations": [{"id": "zion"}]}

    counts = merge_sidecar_into_trip(trip, {"destinations": {"gone": {"lodging": {"name": "X"}}}})

    assert counts == {"lodging_fields": 0, "transportation_legs": 0}


def test_parser_merges_sidecar_and_revalidates(tmp_path) -> None:
    """End-to-end: a sidecar beside the manifest reaches the parsed trip."""
    import shutil

    manifest = tmp_path / "trip_manifest.yaml"
    shutil.copy("trip_manifest.yaml", manifest)
    write_sidecar(
        ManifestParser.reservations_sidecar_path(manifest),
        {"destinations": {"moab": {"transportation": [
            {"type": "car", "provider": "Hertz", "confirmation_number": "H99120"},
        ]}}},
    )

    trip = ManifestParser().parse(manifest)

    moab = next(d for d in trip["destinations"] if d["id"] == "moab")
    assert moab["transportation"][0]["confirmation_number"] == "H99120"


def test_parser_rejects_a_sidecar_that_violates_the_schema(tmp_path) -> None:
    """LLM-extracted email content is untrusted; a bad extraction must fail the
    build loudly rather than reaching the rendered page."""
    import shutil

    manifest = tmp_path / "trip_manifest.yaml"
    shutil.copy("trip_manifest.yaml", manifest)
    write_sidecar(
        ManifestParser.reservations_sidecar_path(manifest),
        {"destinations": {"moab": {"transportation": [
            {"type": "car", "smuggled_field": "not in the schema"},
        ]}}},
    )

    with pytest.raises(ValueError):
        ManifestParser().parse(manifest)


def test_parser_is_silent_without_a_sidecar(tmp_path) -> None:
    import shutil

    manifest = tmp_path / "trip_manifest.yaml"
    shutil.copy("trip_manifest.yaml", manifest)

    trip = ManifestParser().parse(manifest)

    assert trip["destinations"]
