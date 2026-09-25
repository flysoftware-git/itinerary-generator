"""Words that make a free-text place name look already located.

WHY THIS EXISTS
---------------
Two modules ask the same question -- *does this string already say where it
is?* -- and both answered it with the same hand-written list of six states:
Utah, Colorado, Arizona, New Mexico, Nevada and California. The list was
written against the trips the engine was first exercised on, and it silently
stopped being right the moment a trip went anywhere else.

The failure is quiet, which is why it survived. A single-token place name in a
state that is not on the list reads as *unqualified*, so the caller adds
context or declines to build a link -- and nothing reports that it did. A
place in one of the six reads as qualified. Same input shape, different
behaviour, decided by a literal nobody was looking at.

It is also duplicated, so the two copies could drift apart without any test
noticing: `url_discovery` carried extra cues of its own (`downtown`,
`historic district`, `visitor center`) that the assembler's copy never had.

So the **states** are shared here and the **cues** stay with their callers,
which is the split that actually matches how they are used: every caller means
the same thing by "Utah" and they legitimately differ about "downtown".

WHAT THIS IS NOT
----------------
Not a geocoder and not a validator. These are cues for a cheap
*looks-already-qualified* test on a string, ahead of work that costs money.
A false positive here means a query goes out less qualified than it might have
been; a false negative means it goes out more qualified than it needed to be.
Neither is a correctness boundary, which is why a word list is proportionate
and a lookup service would not be.

Deliberately US-only, and named so. The engine's other place sources are
US-centric too (`nps_park_code`, the NPS image priority), so a list that
quietly implied global coverage would be the more misleading of the two
options. A trip outside the US gets no cue from this and falls through to the
comma rule, which is the same answer it got before for forty-four states.
"""

#: Lowercase, no punctuation: callers lowercase the haystack before testing.
#: The District of Columbia is included because "Washington, DC" and
#: "Washington" are different places and the second is the state.
US_STATES: frozenset[str] = frozenset(
    {
        "alabama", "alaska", "arizona", "arkansas", "california", "colorado",
        "connecticut", "delaware", "district of columbia", "florida", "georgia",
        "hawaii", "idaho", "illinois", "indiana", "iowa", "kansas", "kentucky",
        "louisiana", "maine", "maryland", "massachusetts", "michigan",
        "minnesota", "mississippi", "missouri", "montana", "nebraska",
        "nevada", "new hampshire", "new jersey", "new mexico", "new york",
        "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
        "pennsylvania", "rhode island", "south carolina", "south dakota",
        "tennessee", "texas", "utah", "vermont", "virginia", "washington",
        "west virginia", "wisconsin", "wyoming",
    }
)

#: The cues every caller agrees on. A caller with its own additions unions
#: them in rather than editing this, so the shared set stays the shared set.
COMMON_PLACE_CUES: tuple[str, ...] = (
    "national park",
    "state park",
)
