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
    _, fragment = reservation_to_manifest_fragment({"kind": "transportation", "type": "hovercraft"})

    assert fragment["type"] == "other"


def test_fragment_type_whitelist_follows_the_schema() -> None:
    """Ingestion must not keep its own copy of the accepted types. It did, and
    every type the schema gained was silently downgraded -- a forwarded cruise
    confirmation extracted as `ship` became `other`, so the enum extension
    never reached ingested bookings at all."""
    from generator.manifest_parser import TRANSPORTATION_ITEM_SCHEMA

    for accepted in TRANSPORTATION_ITEM_SCHEMA["properties"]["type"]["enum"]:
        _, fragment = reservation_to_manifest_fragment(
            {"kind": "transportation", "type": accepted}
        )
        assert fragment["type"] == accepted, f"{accepted} was downgraded"


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

    outcomes: list[dict] = []
    sidecar = build_sidecar(entries, DESTINATIONS, outcomes=outcomes)

    assert sidecar["destinations"]["zion"]["lodging"]["confirmation_number"] == "ZL-1"
    # Reykjavik scores 0.0 against a Southwest trip, so it is another trip's
    # booking rather than an unplaceable one on this trip: it stays out of this
    # sidecar entirely and is left in the inbox for a manifest that may not
    # exist yet.
    assert sidecar["pending"] == []
    assert {o["disposition"] for o in outcomes} == {"attached", "unrelated", "not_a_booking"}


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

    assert counts == {"lodging_fields": 0, "transportation_legs": 0, "trip_legs": 0}


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


TRIP_WITH_GATEWAYS = {
    "trip": {
        "departure": "Las Vegas International Airport",
        "departure_datetime": "2026-10-17 13:30",
        "return": "Albuquerque, NM airport",
        "return_datetime": "2026-10-29 14:30",
    },
    "destinations": [
        {"id": "stgeorge", "name": "St. George, Utah", "dates": "October 17, 2026"},
        {"id": "zion", "name": "Zion National Park", "dates": "October 18, 2026"},
        {"id": "santafe", "name": "Santa Fe", "dates": "October 27-29, 2026"},
    ],
}



# Mirrors the shape of a REAL manifest: destinations carry street-address
# lodging anchors, which is what gives matching enough tokens to work with.
# TRIP_WITH_GATEWAYS above is deliberately sparse for gateway tests, and that
# sparsity makes a bare state name ("Utah") dominate a match -- realistic for
# neither production nor these assertions.
TRIP_REALISTIC = {
    "trip": {
        "departure": "Las Vegas International Airport",
        "return": "Albuquerque, NM airport",
    },
    "destinations": [
        {"id": "stgeorge", "name": "St. George, Utah", "dates": "October 17, 2026",
         "lodging": {"location": "1819 South 120 East, St. George, UT 84790"}},
        {"id": "zion", "name": "Zion National Park", "dates": "October 18, 2026",
         "lodging": {"location": "1215 Zion Park Blvd, Springdale, UT 84767"}},
        {"id": "santafe", "name": "Santa Fe", "dates": "October 27-29, 2026",
         "lodging": {"location": "100 Sandoval St, Santa Fe, NM 87501"}},
    ],
}

def test_gateways_resolve_to_the_trip_level_sentinel() -> None:
    """A gateway match is TRIP-WIDE, not stop-specific: an inbound flight lands
    at trip.departure and a rental collected there spans every destination, so
    filing them under the first or last stop misrepresents them. They render
    under the route overview map instead."""
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    gateways = {c["_gateway"]: c for c in candidates if c.get("_gateway")}

    assert len(candidates) == 5
    assert gateways["departure"]["id"] == TRIP_LEVEL_ID
    assert gateways["return"]["id"] == TRIP_LEVEL_ID


def test_inbound_flight_to_a_gateway_airport_matches_trip_level() -> None:
    """Regression: trip.departure/trip.return are real booked places that are
    NOT itinerary stops, so nothing in `destinations` could match them. The
    inbound and outbound flights -- the two most likely reservations anyone
    forwards -- always landed in `pending` (best score 0.125)."""
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    inbound = {"kind": "transportation", "type": "plane", "provider": "United",
               "arrive": "Las Vegas International Airport", "dates": "October 17, 2026"}

    dest_id, _ = match_destination(inbound, candidates)

    assert dest_id == TRIP_LEVEL_ID


def test_outbound_flight_from_a_gateway_matches_trip_level() -> None:
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    outbound = {"kind": "transportation", "type": "plane", "provider": "Southwest",
                "depart": "Albuquerque, NM airport", "dates": "October 29, 2026"}

    dest_id, _ = match_destination(outbound, candidates)

    assert dest_id == TRIP_LEVEL_ID


def test_build_match_candidates_is_a_noop_without_gateways() -> None:
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    trip = {"trip": {}, "destinations": TRIP_WITH_GATEWAYS["destinations"]}

    assert len(build_match_candidates(trip)) == 3
    assert build_match_candidates({"trip": {}, "destinations": []}) == []


def test_abbreviated_month_contributes_to_the_date_signal() -> None:
    """Airlines and hotels abbreviate constantly; the month token previously
    contributed nothing, silently dropping the whole 25% date weight."""
    from generator.reservation_ingest import _month_day_tokens

    assert _month_day_tokens("Oct 17-19, 2026") == _month_day_tokens("October 17-19, 2026")
    assert "october" in _month_day_tokens("Oct 17, 2026")


def test_gmail_login_failure_names_the_two_common_misconfigurations(monkeypatch) -> None:
    """Gmail answers every credential problem with the same opaque
    "[AUTHENTICATIONFAILED] Invalid credentials (Failure)". The first real
    ingestion attempt hit it with BOTH causes present: a username with no
    domain, and a 23-character value where an app password is exactly 16."""
    import imaplib

    from generator import reservation_ingest as mod

    class FakeIMAP:
        def __init__(self, host):
            pass

        def login(self, user, password):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

        def logout(self):
            pass

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", FakeIMAP)

    with pytest.raises(RuntimeError) as excinfo:
        mod.fetch_unseen_messages(
            host="imap.gmail.com", user="TripTips.Reservations", password="x" * 23,
        )

    message = str(excinfo.value)
    assert "no '@'" in message
    assert "23 characters" in message and "exactly 16" in message
    # The secret itself must never appear in the error.
    assert "x" * 23 not in message


def test_gmail_login_failure_falls_back_to_generic_guidance(monkeypatch) -> None:
    """Correctly-shaped credentials that still fail get actionable text rather
    than a bare traceback -- e.g. IMAP disabled on the mailbox."""
    import imaplib

    from generator import reservation_ingest as mod

    class FakeIMAP:
        def __init__(self, host):
            pass

        def login(self, user, password):
            raise imaplib.IMAP4.error(b"[AUTHENTICATIONFAILED] Invalid credentials (Failure)")

        def logout(self):
            pass

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", FakeIMAP)

    with pytest.raises(RuntimeError) as excinfo:
        mod.fetch_unseen_messages(
            host="imap.gmail.com", user="trips@gmail.com", password="abcd" * 4,
        )

    assert "IMAP is enabled" in str(excinfo.value)


def test_gmail_app_password_spaces_are_stripped_before_login(monkeypatch) -> None:
    """Google shows app passwords as four space-separated groups of four, so
    pasting the displayed form is the obvious mistake. The credential is the
    16 characters without spaces."""
    from generator import reservation_ingest as mod

    seen = {}

    class FakeIMAP:
        def __init__(self, host):
            pass

        def login(self, user, password):
            seen["password"] = password

        def select(self, mailbox):
            pass

        def uid(self, cmd, *args):
            # UID-addressed: sequence numbers are session-scoped and unsafe to
            # replay against the second-pass connection.
            return ("OK", [b""]) if cmd == "SEARCH" else ("OK", None)

        def logout(self):
            pass

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", FakeIMAP)

    mod.fetch_unseen_messages(
        host="imap.gmail.com", user="trips@gmail.com", password="abcd efgh ijkl mnop",
    )

    assert seen["password"] == "abcdefghijklmnop"
    assert len(seen["password"]) == 16


def test_non_app_password_providers_keep_spaces_intact(monkeypatch) -> None:
    """Stripping must not be universal -- another provider could legitimately
    allow a space inside a password."""
    from generator import reservation_ingest as mod

    seen = {}

    class FakeIMAP:
        def __init__(self, host):
            pass

        def login(self, user, password):
            seen["password"] = password

        def select(self, mailbox):
            pass

        def uid(self, cmd, *args):
            # UID-addressed: sequence numbers are session-scoped and unsafe to
            # replay against the second-pass connection.
            return ("OK", [b""]) if cmd == "SEARCH" else ("OK", None)

        def logout(self):
            pass

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", FakeIMAP)

    mod.fetch_unseen_messages(
        host="mail.example.net", user="trips@example.net", password="correct horse battery",
    )

    assert seen["password"] == "correct horse battery"


# Shapes below mirror real confirmation emails; every address and confirmation
# code is fabricated. Real booking details must never enter a public repo.

def test_rental_car_matches_its_pickup_not_its_return() -> None:
    """A rental spanning the trip belongs where it is PICKED UP.

    Regression from the first real mailbox: a car collected at the departure
    gateway and returned at the return gateway attached to the LAST stop,
    because the return city appeared in `arrive`."""
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    rental = {
        "kind": "transportation", "type": "car", "provider": "Example Rentals",
        "depart": "Las Vegas International Airport, Sat Oct 17, 2026",
        "arrive": "Albuquerque, NM airport, Thu Oct 29, 2026",
        "dates": "Oct 17, 2026",
    }

    dest_id, _ = match_destination(rental, candidates)

    assert dest_id == TRIP_LEVEL_ID


def test_flight_matches_where_it_lands_not_where_it_left() -> None:
    """A flight is identified by its arrival; origin tokens are noise."""
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    flight = {
        "kind": "transportation", "type": "plane", "provider": "Example Air",
        "depart": "Seattle (SEA) Sat Oct 17 10:39 AM",
        "arrive": "Las Vegas (LAS) Sat Oct 17 1:25 PM",
        "dates": "Oct 17, 2026", "city": "Las Vegas",
    }

    dest_id, ranked = match_destination(flight, candidates)

    assert dest_id == TRIP_LEVEL_ID
    assert ranked[0]["score"] >= 0.45


def test_generic_facility_words_do_not_drive_a_match() -> None:
    """"airport" appears in nearly every travel address and identifies nothing.

    Both gateways here end in "airport", and a gateway name reduces to very few
    tokens, so a single shared generic word was scoring 0.5 on name overlap."""
    from generator.reservation_ingest import _normalize_tokens

    assert "airport" not in _normalize_tokens("Albuquerque, NM airport")
    assert "albuquerque" in _normalize_tokens("Albuquerque, NM airport")
    assert "international" not in _normalize_tokens("Las Vegas International Airport")
    assert {"las", "vegas"} <= _normalize_tokens("Las Vegas International Airport")


def test_candidates_resolving_to_one_destination_are_collapsed() -> None:
    """A gateway resolves to a real stop, so both are candidates for one place.
    Left separate, the near-tie rule compared a destination against itself."""
    from generator.reservation_ingest import TRIP_LEVEL_ID, build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    flight = {"kind": "transportation", "type": "plane",
              "arrive": "Las Vegas International Airport", "dates": "Oct 17, 2026"}

    _, ranked = match_destination(flight, candidates)

    ids = [c["id"] for c in ranked]
    assert len(ids) == len(set(ids)), f"duplicate destination ids in {ids}"


def test_iso_dates_contribute_the_month_name() -> None:
    """Gateway candidates take their dates from trip.departure_datetime, which
    is ISO -- so the month was a number and shared no token with "Oct 17"."""
    from generator.reservation_ingest import _month_day_tokens

    assert "october" in _month_day_tokens("2026-10-17 13:30")
    assert "17" in _month_day_tokens("2026-10-17 13:30")
    assert _month_day_tokens("2026-13-01") == _month_day_tokens("2026-13-01")  # invalid month, no crash


def test_gateway_legs_land_in_the_trip_block_not_a_destination() -> None:
    """Trip-wide legs must reach trip["trip"]["transportation"], which renders
    under the route overview map, rather than being filed on a stop."""
    from generator.reservation_ingest import build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    entries = [
        {"source": {"uid": "1"}, "reservation": {
            "kind": "transportation", "type": "plane", "provider": "Example Air",
            "arrive": "Las Vegas International Airport", "confirmation_number": "AAA111",
            "dates": "Oct 17, 2026"}},
        {"source": {"uid": "2"}, "reservation": {
            "kind": "transportation", "type": "car", "provider": "Example Rentals",
            "depart": "Las Vegas International Airport", "confirmation_number": "BBB222",
            "dates": "Oct 17, 2026"}},
    ]

    sidecar = build_sidecar(entries, candidates)

    assert len(sidecar["trip"]["transportation"]) == 2
    assert sidecar["destinations"] == {}
    assert sidecar["pending"] == []


def test_a_mid_trip_leg_still_belongs_to_its_destination() -> None:
    """The locale nuance: only gateway matches are trip-wide. A rental picked
    up at a specific stop renders there."""
    from generator.reservation_ingest import build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    entries = [{"source": {"uid": "3"}, "reservation": {
        "kind": "transportation", "type": "car", "provider": "Example Rentals",
        "depart": "Zion National Park", "confirmation_number": "CCC333",
        "dates": "October 18, 2026"}}]

    sidecar = build_sidecar(entries, candidates)

    assert "zion" in sidecar["destinations"]
    assert sidecar["trip"].get("transportation") is None


def test_lodging_matching_a_gateway_goes_to_review_not_trip_level() -> None:
    """A hotel is always at a place. If the extractor pushed one to a gateway,
    that is a mismatch a human should see -- not a trip-level "stay"."""
    from generator.reservation_ingest import build_match_candidates

    candidates = build_match_candidates(TRIP_WITH_GATEWAYS)
    entries = [{"source": {"uid": "4"}, "reservation": {
        "kind": "lodging", "location": "Las Vegas International Airport",
        "confirmation_number": "DDD444", "dates": "Oct 17, 2026"}}]

    sidecar = build_sidecar(entries, candidates)

    assert sidecar["trip"].get("transportation") is None
    assert len(sidecar["pending"]) == 1
    assert "gateway" in sidecar["pending"][0]["reason"]


def test_merge_puts_trip_legs_on_the_trip_block_and_dedupes() -> None:
    from generator.reservation_ingest import merge_sidecar_into_trip

    trip = {"trip": {"title": "T"}, "destinations": [{"id": "zion"}]}
    sidecar = {"trip": {"transportation": [
        {"type": "plane", "confirmation_number": "AAA111"},
        {"type": "car", "confirmation_number": "BBB222"},
    ]}}

    counts = merge_sidecar_into_trip(trip, sidecar)
    assert counts["trip_legs"] == 2
    assert len(trip["trip"]["transportation"]) == 2

    # Re-merging the same sidecar must not duplicate.
    counts2 = merge_sidecar_into_trip(trip, sidecar)
    assert counts2["trip_legs"] == 0
    assert len(trip["trip"]["transportation"]) == 2


def test_a_booking_for_another_trip_is_not_filed_into_this_ones_pending() -> None:
    """A traveler forwards confirmations as they book, so mail arrives for trips
    whose manifest does not exist yet. Matched against whatever manifest happens
    to be running, those score ~0. Treating them as ambiguous local bookings
    buried them in the wrong trip's pending list -- and once archiving existed,
    moved them out of the inbox, so the manifest they belonged to never saw
    them."""
    from generator.reservation_ingest import build_match_candidates

    candidates = build_match_candidates(TRIP_REALISTIC)
    entries = [
        {"source": {"uid": "1", "subject": "Split hotel"}, "reservation": {
            "kind": "lodging", "city": "Split, Croatia", "confirmation_number": "HR1"}},
        {"source": {"uid": "2", "subject": "Zion hotel"}, "reservation": {
            "kind": "lodging", "city": "Springdale, UT", "confirmation_number": "UT1",
            "dates": "October 18, 2026"}},
    ]

    outcomes: list[dict] = []
    sidecar = build_sidecar(entries, candidates, outcomes=outcomes)

    by_uid = {o["uid"]: o for o in outcomes}
    assert by_uid["1"]["disposition"] == "unrelated"
    assert by_uid["2"]["disposition"] == "attached"
    # The other trip's booking must NOT pollute this sidecar in any form.
    assert sidecar["pending"] == []
    assert "HR1" not in json.dumps(sidecar)


def test_an_unplaceable_booking_on_THIS_trip_still_goes_to_pending() -> None:
    """The floor must not swallow genuine near-misses. Kanab UT is plausibly
    part of a Southwest trip; Split is not."""
    from generator.reservation_ingest import build_match_candidates

    candidates = build_match_candidates(TRIP_REALISTIC)
    entries = [{"source": {"uid": "9", "subject": "Kanab hotel"}, "reservation": {
        "kind": "lodging", "city": "Kanab, Utah", "confirmation_number": "KN1",
        "dates": "October 18, 2026"}}]

    outcomes: list[dict] = []
    sidecar = build_sidecar(entries, candidates, outcomes=outcomes)

    assert outcomes[0]["disposition"] == "pending"
    assert len(sidecar["pending"]) == 1


def test_every_message_gets_exactly_one_disposition() -> None:
    """The caller decides each message's fate in the mailbox from these, so a
    missing disposition would silently leave mail unfiled forever, and a
    duplicate would file something twice."""
    from generator.reservation_ingest import build_match_candidates

    candidates = build_match_candidates(TRIP_REALISTIC)
    entries = [
        {"source": {"uid": "1"}, "reservation": {"kind": "none"}},
        {"source": {"uid": "2"}, "reservation": {"kind": "lodging", "city": "Split, Croatia"}},
        {"source": {"uid": "3"}, "reservation": {
            "kind": "lodging", "city": "Springdale, UT", "confirmation_number": "D1",
            "dates": "October 18, 2026"}},
        {"source": {"uid": "4"}, "reservation": {
            "kind": "lodging", "city": "Springdale, UT", "confirmation_number": "D1",
            "dates": "October 18, 2026"}},  # duplicate confirmation
    ]

    outcomes: list[dict] = []
    build_sidecar(entries, candidates, outcomes=outcomes)

    assert len(outcomes) == len(entries)
    assert len({o["uid"] for o in outcomes}) == len(entries)
    assert {o["disposition"] for o in outcomes} == {
        "not_a_booking", "unrelated", "attached", "duplicate"}


def test_mark_messages_processed_is_a_noop_with_no_uids(monkeypatch) -> None:
    """Every message unrelated means no connection should even be opened."""
    from generator import reservation_ingest as mod

    def explode(host):
        raise AssertionError("should not connect when there is nothing to file")

    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", explode)

    assert mod.mark_messages_processed(host="h", user="u", password="p", uids=[]) == 0


class _FakeIMAP:
    """Records UID-addressed commands. Everything is UID-addressed on purpose:
    plain SEARCH/FETCH/STORE use message SEQUENCE numbers, which are scoped to
    one session and shift as the mailbox changes -- so a number captured during
    the fetch addresses a DIFFERENT message when the second pass reconnects to
    file it. That is not a failed no-op; it flags and moves the wrong mail."""

    def __init__(self, host, *, move_ok=True, copy_ok=True, create_ok=True):
        self.actions: list[tuple] = []
        self._move_ok = move_ok
        self._copy_ok = copy_ok
        self._create_ok = create_ok

    def login(self, u, p): pass
    def select(self, mailbox): pass
    def logout(self): pass

    def create(self, folder):
        if not self._create_ok:
            raise RuntimeError("no permission")
        self.actions.append(("create", folder))

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            return "OK", [b"41"]
        if cmd == "FETCH":
            return "OK", [(b"41 (BODY[]", b"Subject: x\r\n\r\nbody")]
        if cmd == "STORE":
            self.actions.append(("store", args[0].decode(), args[2]))
            return "OK", None
        if cmd == "MOVE":
            if not self._move_ok:
                raise imaplib.IMAP4.error(b"MOVE unsupported")
            self.actions.append(("MOVE", args[0].decode(), args[1]))
            return "OK", None
        if cmd == "COPY":
            if not self._copy_ok:
                raise RuntimeError("copy failed")
            self.actions.append(("COPY", args[0].decode(), args[1]))
            return "OK", None
        return "OK", None


import imaplib  # noqa: E402  (used by _FakeIMAP above)


def test_mailbox_commands_are_uid_addressed(monkeypatch) -> None:
    """The bug this prevents: sequence numbers captured during the fetch were
    replayed against a NEW connection in the second pass, where they address
    whatever message now sits in that position."""
    from generator import reservation_ingest as mod

    fake = _FakeIMAP("h")
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)

    messages = mod.fetch_unseen_messages(host="imap.example.net", user="u", password="p")

    # The uid returned to the caller is the real UID, stable across connections.
    assert messages[0][0] == "41"


def test_ingested_messages_are_flagged_and_filed(monkeypatch) -> None:
    from generator import reservation_ingest as mod

    fake = _FakeIMAP("h")
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)

    filed = mod.mark_messages_processed(
        host="imap.example.net", user="u", password="p",
        uids=["41"], archive_folder="Ingested",
    )

    assert filed == 1
    assert ("store", "41", "\Seen") in fake.actions
    assert ("MOVE", "41", "Ingested") in fake.actions


def test_archiving_falls_back_to_copy_when_move_is_unsupported(monkeypatch) -> None:
    """IMAP MOVE (RFC 6851) is not universal; COPY + \Deleted is the portable
    equivalent. Deliberately no EXPUNGE -- that destroys mail permanently, and a
    silently-failed copy would take the original with it."""
    from generator import reservation_ingest as mod

    fake = _FakeIMAP("h", move_ok=False)
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)

    mod.mark_messages_processed(
        host="imap.example.net", user="u", password="p",
        uids=["41"], archive_folder="Ingested",
    )

    assert ("COPY", "41", "Ingested") in fake.actions
    assert ("store", "41", "\Deleted") in fake.actions
    assert not any(a[0] == "EXPUNGE" for a in fake.actions)


def test_filing_failure_never_loses_the_reservation(monkeypatch) -> None:
    """The booking is already extracted by the time it is filed, so a
    permissions problem must not fail the run."""
    from generator import reservation_ingest as mod

    fake = _FakeIMAP("h", move_ok=False, copy_ok=False, create_ok=False)
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)

    assert mod.mark_messages_processed(
        host="imap.example.net", user="u", password="p",
        uids=["41"], archive_folder="Ingested",
    ) == 1


def test_no_archive_folder_flags_but_does_not_file(monkeypatch) -> None:
    from generator import reservation_ingest as mod

    fake = _FakeIMAP("h")
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)

    mod.mark_messages_processed(host="imap.example.net", user="u", password="p", uids=["41"])

    assert ("store", "41", "\Seen") in fake.actions
    assert not any(a[0] in ("MOVE", "COPY", "create") for a in fake.actions)


def test_mark_messages_processed_only_touches_the_uids_it_is_given(monkeypatch) -> None:
    """Filing is a second pass precisely so another trip's mail stays untouched."""
    from generator import reservation_ingest as mod

    fake = _FakeIMAP("h")
    monkeypatch.setattr(mod.imaplib, "IMAP4_SSL", lambda host: fake)

    mod.mark_messages_processed(
        host="imap.example.net", user="u", password="p",
        uids=["3", "7"], archive_folder="Ingested",
    )

    assert {a[1] for a in fake.actions if a[0] in ("store", "MOVE")} == {"3", "7"}
