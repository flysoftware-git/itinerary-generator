"""Tests for generator.transit_routing (GH #2 Phase 1).

The normalizer is the whole safety argument of Phase 1: the prompt asks for
Shape B, but prompts drift, so the code strips what the prompt forbids. These
tests assert the strip, not the asking.
"""
import pytest

from generator.transit_routing import (
    MAX_OPTIONS,
    AITransitProvider,
    build_transit_provider,
    format_b,
    normalize_transit_options,
    read_transit_provider,
    resolve_leg_mode,
    should_generate_options,
)


def _option(**overrides):
    base = {
        "mode": "bus",
        "label": "Regional bus via Panguitch",
        "duration": "3-4 hours",
        "transfers": 1,
        "notes": "Runs daily in peak season.",
        "booking_hint": "Search 'Bryce to Capitol Reef bus' for current schedules.",
    }
    base.update(overrides)
    return base


class TestNormalizer:
    def test_format_a_survives_intact(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option()],
            "fallback": "Driving remains the most reliable option on this corridor.",
        })
        assert out["has_transit"] is True
        assert out["source"] == "ai"
        assert out["confidence"] == "unverified"
        assert out["options"][0]["label"] == "Regional bus via Panguitch"
        assert out["options"][0]["transfers"] == 1
        assert out["fallback"].startswith("Driving remains")

    def test_has_transit_false_produces_format_b(self):
        out = normalize_transit_options({
            "has_transit": False,
            "honest_assessment": "No scheduled service connects these stops.",
            "local_tip": "Springdale outfitters run shuttles on request.",
        })
        assert out["has_transit"] is False
        assert out["honest_assessment"] == "No scheduled service connects these stops."
        assert out["local_tip"].startswith("Springdale")
        # Nothing to qualify, so nothing claims to have been verified.
        assert "confidence" not in out
        assert "options" not in out

    @pytest.mark.parametrize("field", ["depart", "arrive", "url", "booking_url"])
    def test_shape_a_fields_are_stripped(self, field):
        """The issue's opening example carried an ISO datetime and a booking
        URL -- the two things this project has decided, twice and with
        evidence, not to let a model produce."""
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(**{field: "2026-10-14T09:00"})],
        })
        assert field not in out["options"][0]

    def test_a_clock_time_inside_a_duration_is_stripped(self):
        """A 'duration' of '09:00-12:15' is a timetable wearing a duration's
        name, and reads to a traveler as a departure they can plan around."""
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(duration="09:00-12:15")],
        })
        assert "09:00" not in out["options"][0].get("duration", "")

    def test_a_url_inside_prose_is_stripped(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(notes="Book at https://greyhound.com/tickets before travel.")],
        })
        assert "greyhound.com" not in out["options"][0]["notes"]

    def test_url_stripped_from_format_b_prose_too(self):
        out = normalize_transit_options({
            "has_transit": False,
            "honest_assessment": "Nothing scheduled. See www.utah.gov/transit for updates.",
        })
        assert "utah.gov" not in out["honest_assessment"]

    def test_an_option_without_a_label_is_dropped(self):
        """A duration attached to nothing is not an option."""
        out = normalize_transit_options({
            "has_transit": True,
            "options": [{"duration": "3-4 hours", "transfers": 1}],
        })
        assert out["has_transit"] is False

    def test_has_transit_true_with_no_usable_options_degrades_to_format_b(self):
        """An empty card reads as 'we did not look'. Format B says 'there is
        nothing', which is the true statement."""
        out = normalize_transit_options({"has_transit": True, "options": []})
        assert out["has_transit"] is False
        assert out["honest_assessment"]

    def test_options_are_capped(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(label=f"Option {i}") for i in range(10)],
        })
        assert len(out["options"]) == MAX_OPTIONS

    @pytest.mark.parametrize("payload", [None, "", [], 42, {"options": "a bus"}])
    def test_unparseable_payloads_degrade_to_format_b(self, payload):
        out = normalize_transit_options(payload)
        assert out["has_transit"] is False
        assert out["honest_assessment"]

    def test_unknown_keys_do_not_ride_along(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(operator_phone="555-0100", seat_class="green car")],
        })
        assert set(out["options"][0]) <= {
            "mode", "label", "duration", "fare", "transfers", "notes", "booking_hint"
        }

    def test_non_numeric_transfers_is_dropped_not_guessed(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(transfers="a couple")],
        })
        assert "transfers" not in out["options"][0]

    def test_confidence_is_carried_from_the_provider(self):
        out = normalize_transit_options(
            {"has_transit": True, "options": [_option()]},
            source="google_directions",
            confidence="api_verified",
        )
        assert out["source"] == "google_directions"
        assert out["confidence"] == "api_verified"


class TestLegModeResolution:
    def test_defaults_to_auto(self):
        assert resolve_leg_mode({"id": "zion"}) == "auto"

    def test_trip_wide_mode_applies(self):
        assert resolve_leg_mode({"id": "zion"}, trip_meta={"transport_mode": "transit"}) == "transit"

    def test_destination_overrides_the_trip(self):
        assert resolve_leg_mode(
            {"id": "zion", "transport_mode": "mixed"},
            trip_meta={"transport_mode": "transit"},
        ) == "mixed"

    def test_a_legs_entry_names_the_leg(self):
        legs = [{"from": "zion", "to": "bryce_canyon", "mode": "transit"}]
        assert resolve_leg_mode({"id": "bryce_canyon"}, previous_id="zion", legs=legs) == "transit"

    def test_a_legs_entry_for_another_leg_is_ignored(self):
        legs = [{"from": "moab", "to": "arches", "mode": "transit"}]
        assert resolve_leg_mode({"id": "bryce_canyon"}, previous_id="zion", legs=legs) == "auto"

    def test_grouped_entries_are_always_auto(self):
        """A there-and-back day trip from a shared base has no arriving
        relocation leg for a mode to describe."""
        dest = {"id": "arches", "group_with": "moab", "transport_mode": "transit"}
        assert resolve_leg_mode(dest, trip_meta={"transport_mode": "transit"}) == "auto"


class TestBookedLegPrecedence:
    def test_a_booking_suppresses_generation(self, caplog):
        """multimodal-routing.md 4.6: the traveler holds a confirmation.
        'There might be a bus around 9' beside 'your 09:00 flight, locator
        XR7Q2M' is noise attached to a decided question."""
        import logging

        dest = {
            "name": "Kyoto",
            "transportation": [{"type": "train", "provider": "JR", "confirmation_number": "XR7Q2M"}],
        }
        with caplog.at_level(logging.INFO):
            assert should_generate_options(dest, "transit") is False
        assert any("outranked" in r.getMessage() for r in caplog.records)

    def test_a_booking_does_not_raise(self):
        """Unlike the legs/transport_mode collision. A traveler who wrote
        transport_mode in March and forwarded a confirmation in August has
        not made an error -- plans change."""
        dest = {"name": "Kyoto", "transportation": [{"type": "car"}]}
        assert should_generate_options(dest, "mixed") is False

    def test_auto_legs_never_generate(self):
        assert should_generate_options({"name": "Bryce"}, "auto") is False

    @pytest.mark.parametrize("mode", ["transit", "mixed"])
    def test_unbooked_transit_legs_generate(self, mode):
        assert should_generate_options({"name": "Bryce"}, mode) is True


class TestProviderFactory:
    def test_defaults_to_ai(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("ai:\n  provider: grok\n", encoding="utf-8")
        assert read_transit_provider(cfg) == "ai"

    def test_reads_the_configured_provider(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("transit_routing:\n  provider: google_directions\n", encoding="utf-8")
        assert read_transit_provider(cfg) == "google_directions"

    def test_unknown_provider_falls_back_to_ai(self, tmp_path):
        cfg = tmp_path / "config.yaml"
        cfg.write_text("transit_routing:\n  provider: teleport\n", encoding="utf-8")
        assert read_transit_provider(cfg) == "ai"

    def test_missing_config_falls_back_to_ai(self, tmp_path):
        assert read_transit_provider(tmp_path / "nope.yaml") == "ai"

    def test_phase_2_provider_raises_rather_than_pretending(self):
        """A silent fallback would report an AI guess under a name that reads
        as API-verified."""
        with pytest.raises(NotImplementedError) as exc_info:
            build_transit_provider(provider_override="google_directions")
        assert "Phase 2" in str(exc_info.value)

    def test_shipped_config_selects_a_working_provider(self):
        assert read_transit_provider("config.yaml") == "ai"


class _StubLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate_json(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class TestAIProvider:
    def _provider(self, payload):
        return AITransitProvider(
            _StubLLM(payload), template="{origin}|{destination}|{dates}|{trip_title}",
            system_prompt="system",
        )

    def test_generates_and_normalizes(self):
        provider = self._provider({
            "has_transit": True,
            "options": [_option(url="https://greyhound.com")],
        })
        out = provider.generate_transit_options(
            {"name": "Bryce Canyon"}, {"name": "Capitol Reef", "dates": "Oct 2026"}, {},
        )
        assert out["has_transit"] is True
        assert "url" not in out["options"][0]
        assert out["confidence"] == "unverified"

    def test_bills_under_the_transit_routing_prefix(self):
        """design.md 4.4 records two incidents of spend silently excluded
        from stage attribution because a prefix was unrecognised."""
        llm = _StubLLM({"has_transit": False, "honest_assessment": "None."})
        provider = AITransitProvider(llm, template="{origin}{destination}{dates}{trip_title}",
                                     system_prompt="s")
        provider.generate_transit_options({"name": "A"}, {"name": "B"}, {})
        assert llm.calls[0]["operation"].startswith("transit_routing:")

    def test_an_error_is_not_a_negative(self):
        """Failing to ask is not the same as asking and being told no. The
        card must not assert that no service exists on a corridor we never
        checked."""
        provider = self._provider(RuntimeError("provider down"))
        out = provider.generate_transit_options({"name": "A"}, {"name": "B"}, {})
        assert out["has_transit"] is False
        assert "could not be checked" in out["honest_assessment"]

    def test_missing_endpoints_short_circuit_without_a_call(self):
        llm = _StubLLM({"has_transit": True, "options": [_option()]})
        provider = AITransitProvider(llm, template="{origin}{destination}{dates}{trip_title}",
                                     system_prompt="s")
        out = provider.generate_transit_options({"name": ""}, {"name": "B"}, {})
        assert out["has_transit"] is False
        assert llm.calls == []


def test_shipped_prompt_renders_and_forbids_times_and_links():
    """The prompt is a .format() template; an unescaped brace in its JSON
    examples would raise at the first real call rather than in a test."""
    from generator.transit_routing import PROMPTS_DIR

    template = (PROMPTS_DIR / "transit_options.txt").read_text(encoding="utf-8")
    rendered = template.format(
        origin="Bryce Canyon", destination="Capitol Reef", dates="Oct 2026", trip_title="SW",
    )
    assert "Bryce Canyon" in rendered and "Capitol Reef" in rendered
    assert '"has_transit": true' in rendered
    assert "NEVER include a URL" in rendered


def test_format_b_helper_omits_an_empty_tip():
    assert "local_tip" not in format_b("Nothing here.")


class TestOptionDuration:
    def test_returns_the_first_bands(self):
        from generator.transit_routing import option_duration

        assert option_duration({
            "has_transit": True,
            "options": [{"duration": "3-4 hours"}, {"duration": "6 hours"}],
        }) == "3-4 hours"

    def test_skips_an_option_with_no_duration(self):
        from generator.transit_routing import option_duration

        assert option_duration({
            "has_transit": True,
            "options": [{"label": "Bus"}, {"duration": "5 hours"}],
        }) == "5 hours"

    def test_format_b_has_no_duration(self):
        from generator.transit_routing import option_duration

        assert option_duration({"has_transit": False, "honest_assessment": "None."}) == ""

    def test_missing_or_malformed_input(self):
        from generator.transit_routing import option_duration

        assert option_duration(None) == ""
        assert option_duration({"has_transit": True, "options": "a bus"}) == ""


class TestFare:
    """Owner call 2026-09-02, reversing the fare exclusion in 2.1 and 7.2.

    The objection there was that a fare quoted at build time is wrong by the
    time it is read -- true of a quote, and equally true of duration had it
    been emitted as a single figure. Both are bands.
    """

    def test_a_fare_band_survives(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(fare="\u00a513,000-14,500")],
        })
        assert out["options"][0]["fare"] == "\u00a513,000-14,500"

    @pytest.mark.parametrize("fare", ["varies by season", "cheap", "ask at the counter", ""])
    def test_a_fare_with_no_digits_is_dropped(self, fare):
        """The shapes a model reaches for when it does not know. All of them
        read as information once they are sitting in a badge."""
        out = normalize_transit_options({
            "has_transit": True, "options": [_option(fare=fare)],
        })
        assert "fare" not in out["options"][0]

    def test_a_url_in_the_fare_is_stripped_like_any_other_prose(self):
        out = normalize_transit_options({
            "has_transit": True,
            "options": [_option(fare="$45 at https://greyhound.com")],
        })
        assert "greyhound.com" not in out["options"][0]["fare"]
        assert "45" in out["options"][0]["fare"]

    def test_an_option_is_still_valid_without_a_fare(self):
        out = normalize_transit_options({
            "has_transit": True, "options": [_option()],
        })
        assert out["has_transit"] is True
        assert "fare" not in out["options"][0]


def test_the_prompt_asks_for_a_fare_band_not_a_price():
    from generator.transit_routing import PROMPTS_DIR

    template = (PROMPTS_DIR / "transit_options.txt").read_text(encoding="utf-8")
    rendered = template.format(origin="A", destination="B", dates="", trip_title="T")
    assert "fare" in rendered
    assert "RANGE in the local currency" in rendered
    # The pass caveat: for many travelers the fare is moot.
    assert "rail pass" in rendered


class TestSelfPoweredModes:
    """`bike` and `hike` are leg modes, but they are not transit. Nobody
    operates them, so the whole Phase 1 apparatus -- Format A/B, the strip,
    the Unverified badge -- has nothing to act on and must not run."""

    @pytest.mark.parametrize("mode", ["bike", "hike"])
    def test_they_resolve_like_any_other_mode(self, mode):
        from generator.transit_routing import resolve_leg_mode

        assert resolve_leg_mode({"id": "b", "transport_mode": mode}) == mode
        assert resolve_leg_mode({"id": "b"}, trip_meta={"transport_mode": mode}) == mode

    @pytest.mark.parametrize("mode", ["bike", "hike"])
    def test_no_options_are_generated(self, mode):
        """A 'Public transport options' card on a leg the traveler pedals
        would answer a question nobody asked -- and would cost a call to do
        it."""
        from generator.transit_routing import should_generate_options

        assert should_generate_options({"name": "Bryce"}, mode) is False

    @pytest.mark.parametrize("mode", ["bike", "hike"])
    def test_en_route_stops_are_kept(self, mode):
        """The strongest case for stops in the design: a cyclist stops more
        often than a driver, and the stops are the day rather than an
        interruption to it."""
        from generator.transit_routing import suppresses_en_route_stops

        assert suppresses_en_route_stops({"_transport_mode": mode}) is False

    def test_transit_still_suppresses_them(self):
        from generator.transit_routing import suppresses_en_route_stops

        assert suppresses_en_route_stops({"_transport_mode": "transit"}) is True

    @pytest.mark.parametrize("mode, expected", [("bike", "BICYCLE"), ("hike", "WALK")])
    def test_each_maps_to_a_routes_travel_mode(self, mode, expected):
        from generator.transit_routing import ROUTES_TRAVEL_MODE_BY_LEG_MODE

        assert ROUTES_TRAVEL_MODE_BY_LEG_MODE[mode] == expected

    @pytest.mark.parametrize("mode", ["auto", "transit", "mixed"])
    def test_the_operated_modes_have_no_routes_mode_here(self, mode):
        """transit is priced by TRANSIT through a different path; auto and
        mixed are drives. Only the self-powered pair belongs in this map."""
        from generator.transit_routing import ROUTES_TRAVEL_MODE_BY_LEG_MODE

        assert mode not in ROUTES_TRAVEL_MODE_BY_LEG_MODE

    @pytest.mark.parametrize("mode, expected", [
        ("bike", True), ("hike", True), ("transit", False), ("auto", False), ("mixed", False),
    ])
    def test_is_self_powered(self, mode, expected):
        from generator.transit_routing import is_self_powered

        assert is_self_powered({"_transport_mode": mode}) is expected


class TestTheFirstLeg:
    """Whether destination[0] has an inbound leg depends on trip.departure."""

    def _trip(self, **trip_meta):
        from generator.transit_routing import stamp_resolved_modes

        trip = {
            "trip": dict(trip_meta),
            "destinations": [
                {"id": "callahans", "name": "Callahan's"},
                {"id": "fish_lake", "name": "Fish Lake"},
            ],
        }
        stamp_resolved_modes(trip)
        return trip["destinations"]

    def test_no_departure_means_no_first_leg(self):
        """The journey into the first stop is the trip's own arrival -- a
        flight in must not be described as a drive, or as a hike."""
        first, second = self._trip(transport_mode="hike")
        assert first["_transport_mode"] == "auto"
        assert second["_transport_mode"] == "hike"

    def test_a_named_departure_gives_the_first_leg_the_trip_mode(self):
        """A thru-hike does not drive its first section."""
        first, second = self._trip(transport_mode="hike", departure="Seiad Valley, California")
        assert first["_transport_mode"] == "hike"
        assert second["_transport_mode"] == "hike"

    def test_a_driving_trip_is_unchanged_either_way(self):
        assert self._trip()[0]["_transport_mode"] == "auto"
        assert self._trip(departure="Las Vegas, Nevada")[0]["_transport_mode"] == "auto"

    def test_an_explicit_first_destination_mode_is_no_longer_swallowed(self):
        """It used to be overridden by the index-0 rule without a word, which
        is the silent-fallback class the legs: id contract exists to
        prevent."""
        from generator.transit_routing import stamp_resolved_modes

        trip = {
            "trip": {"departure": "Seiad Valley, California"},
            "destinations": [{"id": "callahans", "transport_mode": "hike"}],
        }
        stamp_resolved_modes(trip)
        assert trip["destinations"][0]["_transport_mode"] == "hike"


class TestSelfPoweredDuration:
    """Google WALK returns continuous travel time. The PCT run rendered
    "45 hrs 55 min" for a leg the manifest schedules as seven days -- true,
    unusable, and precise enough to look plannable."""

    def test_a_multi_day_leg_reads_in_days(self):
        from generator.transit_routing import format_self_powered_duration

        # 45 hrs 55 min at an 8-hour walking day.
        assert format_self_powered_duration(2755, hours_per_day=8) == "about 6 days"

    def test_a_leg_inside_one_day_keeps_its_hours(self):
        """A three-hour walk is a three-hour walk; days would be worse."""
        from generator.transit_routing import format_self_powered_duration

        assert format_self_powered_duration(180, hours_per_day=8) == "3 hrs"

    def test_the_manifests_own_day_length_is_the_divisor(self):
        """Same leg, two travelers: the figure follows what they said their
        day is, not a constant invented here."""
        from generator.transit_routing import format_self_powered_duration

        assert format_self_powered_duration(2400, hours_per_day=10) == "about 4 days"
        assert format_self_powered_duration(2400, hours_per_day=5) == "about 8 days"

    @pytest.mark.parametrize("bad", [None, 0, -3, "eight"])
    def test_a_missing_or_unusable_day_length_falls_back(self, bad):
        from generator.transit_routing import (
            DEFAULT_ACTIVITY_HOURS_PER_DAY,
            format_self_powered_duration,
        )

        out = format_self_powered_duration(2400, hours_per_day=bad)
        expected = round(2400 / (DEFAULT_ACTIVITY_HOURS_PER_DAY * 60))
        assert out == f"about {expected} days"

    def test_it_never_says_about_1_day(self):
        """Anything past the day budget is at least two, or the phrasing
        contradicts the branch it is in."""
        from generator.transit_routing import format_self_powered_duration

        assert format_self_powered_duration(481, hours_per_day=8) == "about 2 days"

    @pytest.mark.parametrize("minutes", [0, None, -5, "", "abc"])
    def test_no_duration_stays_empty(self, minutes):
        from generator.transit_routing import format_self_powered_duration

        assert format_self_powered_duration(minutes, hours_per_day=8) == ""


class TestTrailNameStamping:
    def test_a_trail_name_reaches_self_powered_legs(self):
        from generator.transit_routing import TRAIL_NAME_KEY, stamp_resolved_modes

        trip = {
            "trip": {"transport_mode": "hike", "trail_name": "Pacific Crest Trail",
                     "departure": "Seiad Valley, California"},
            "destinations": [{"id": "a"}, {"id": "b"}],
        }
        stamp_resolved_modes(trip)
        assert all(d[TRAIL_NAME_KEY] == "Pacific Crest Trail" for d in trip["destinations"])

    def test_a_driven_leg_gets_no_trail_name(self):
        """A trail link on a leg nobody walks invites a footpath nobody is on."""
        from generator.transit_routing import TRAIL_NAME_KEY, stamp_resolved_modes

        trip = {
            "trip": {"trail_name": "Pacific Crest Trail"},
            "destinations": [{"id": "a"}, {"id": "b"}],
        }
        stamp_resolved_modes(trip)
        assert all(TRAIL_NAME_KEY not in d for d in trip["destinations"])
