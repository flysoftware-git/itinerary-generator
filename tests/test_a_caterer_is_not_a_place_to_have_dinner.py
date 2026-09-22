"""Somewhere you can walk in and sit down, or it is not a dinner recommendation.

Seen shipped in a real guide: a caterer in Oak Harbor, WA, recommended as a
place to have dinner. It is a real food business, well reviewed and locally
known, and there is nowhere to turn up and eat -- the address is an industrial
unit. A delivery-only kitchen and a private-events venue fail the same way, and
none of the existing gates can see it: the name is a restaurant's name, the
domain is the business's own, the page is its homepage rather than a job
listing, and it is neither closed nor pre-opening.

The hard half is not spotting a caterer. It is spotting one without throwing
away the restaurants: most places worth recommending also cater, also deliver,
and will close the back room for a birthday. So every marker here says ONLY or
says it outright, and that is the whole design of the list -- a phrase earns a
place in it only if a restaurant with a dining room would not write it.
"""

from __future__ import annotations

import pytest

from generator.url_discovery import (
    RESTAURANT_NO_DINING_ROOM_MARKERS,
    RESTAURANT_NO_DINING_ROOM_NAME_ENDINGS,
    URLDiscoverer,
)

PROMPT = "prompts/destination_content.txt"


# ── what the text says ──────────────────────────────────────────────────────


@pytest.mark.parametrize("text", [
    "Zanini's is a catering company serving Whidbey Island since 1998.",
    "Catering only — please call for a quote.",
    "We are not a restaurant; we bring the restaurant to you.",
    "Delivery only. No dine-in.",
    "Available for private events only.",
    "There is no dining room at this location.",
])
def test_a_business_that_says_it_has_no_dining_room_is_refused(text):
    assert URLDiscoverer._serves_no_walk_in_diner(text)


@pytest.mark.parametrize("text", [
    "Dine in, take out, and catering available.",
    "We offer catering for weddings and private events, and the dining room is "
    "open Wednesday to Sunday.",
    "Delivery is available through the usual apps; walk-ins welcome.",
    "Our private events space seats forty.",
    "",
])
def test_a_restaurant_that_also_caters_is_left_alone(text):
    """The half that matters. A marker that merely mentioned catering would
    throw away the good entries along with the caterers -- silently, which is
    how a filter that looks careful becomes the defect."""
    assert URLDiscoverer._serves_no_walk_in_diner(text) == ""


def test_the_marker_is_returned_so_the_log_can_name_it():
    """A bool would say a business was refused and never which phrase did it.
    This list is the kind that grows by guesswork, and a marker nobody can see
    firing is a marker nobody can tell is wrong."""
    said = URLDiscoverer._serves_no_walk_in_diner("CATERING ONLY, by appointment")

    assert said in RESTAURANT_NO_DINING_ROOM_MARKERS
    assert said == "catering only"


def test_the_text_is_read_however_it_is_spaced_and_cased():
    assert URLDiscoverer._serves_no_walk_in_diner("We   Are\nA  Catering   Company")


# ── what the name says ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", [
    "Zanini's Catering",
    "The Catering Company",
    "Island Caterers",
    "Harbor Catering & Events",
    "catering",
])
def test_a_name_that_ends_in_catering_is_refused(name):
    assert URLDiscoverer._name_says_it_is_a_caterer(name)


@pytest.mark.parametrize("name", [
    "Cattleman's Steakhouse",
    "Catering Club Cafe",          # the word is not the end of the name
    "Frasers Gourmet Hideaway",
    "",
])
def test_a_name_that_merely_contains_the_word_is_left_alone(name):
    """Matched on the last words only. A name is short, and a substring match
    on it is a guess -- *Cattleman's* starts with the same five letters."""
    assert URLDiscoverer._name_says_it_is_a_caterer(name) == ""


def test_every_recorded_ending_is_actually_matched():
    """A list whose entries the matcher cannot reach is a list that reads as a
    rule and is not one."""
    for ending in RESTAURANT_NO_DINING_ROOM_NAME_ENDINGS:
        assert URLDiscoverer._name_says_it_is_a_caterer(f"Somewhere {ending}"), ending


# ── through the gate the pipeline calls ─────────────────────────────────────


def _discoverer(page_text: str = ""):
    d = URLDiscoverer.__new__(URLDiscoverer)
    d._restaurant_name_denylist = frozenset()
    fetched: list[str] = []

    def _fetch(url, timeout=6):
        fetched.append(url)
        return (True, 200, page_text)

    d._fetch_page_text = _fetch
    d._fetched = fetched
    return d


def test_the_caterer_from_oak_harbor_is_refused_by_its_snippet():
    """The shipped case. The snippet harvested at discovery is the only text
    this class of business tends to have -- one page, a phone number, a form."""
    discoverer = _discoverer()
    rest = {
        "name": "Zanini's",
        "url": "https://example.com/",
        "description": "Zanini's is a catering company serving Whidbey Island.",
    }

    assert discoverer._is_restaurant_ineligible(rest, "Oak Harbor, Washington")
    assert discoverer._fetched == [], "the page was fetched for an answer already in hand"


def test_the_page_is_read_when_the_snippet_says_nothing():
    discoverer = _discoverer(page_text="Welcome. We are a catering company.")
    rest = {"name": "Somewhere", "url": "https://example.com/"}

    assert discoverer._is_restaurant_ineligible(rest, "Oak Harbor, Washington")
    assert discoverer._fetched == ["https://example.com/"]


def test_an_ordinary_restaurant_still_passes():
    discoverer = _discoverer(page_text="Dinner Wednesday to Sunday. Catering available.")
    rest = {"name": "Frasers Gourmet Hideaway", "url": "https://example.com/",
            "description": "Northwest cooking, open for dinner."}

    assert not discoverer._is_restaurant_ineligible(rest, "Oak Harbor, Washington")


def test_a_search_fallback_url_is_still_not_fetched():
    """Unchanged: nothing can be concluded from a Maps search link, and the
    name and snippet checks above have already had their say."""
    discoverer = _discoverer(page_text="We are a catering company.")
    rest = {"name": "Somewhere",
            "url": "https://www.google.com/maps/search/?api=1&query=Somewhere"}

    assert not discoverer._is_restaurant_ineligible(rest, "Oak Harbor, Washington")
    assert discoverer._fetched == []


# ── what the model is told ──────────────────────────────────────────────────


def test_the_prompt_asks_for_somewhere_you_can_walk_into():
    """Both halves, because the prompt is where most of these are avoided at
    all: a filter downstream can only delete an entry, and an entry deleted is
    a dinner recommendation the guide no longer has."""
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / PROMPT).read_text(encoding="utf-8")

    assert "WALK IN AND SIT DOWN TO EAT" in text
    assert "Exclude caterers" in text
    # And the exception, or the model drops every restaurant that caters.
    assert "ALSO caters" in text
