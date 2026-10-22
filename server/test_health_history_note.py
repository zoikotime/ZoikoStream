"""What the Platform Health card is allowed to claim about its trend.

The card used to carry one fixed sentence:

    "Health history is sampled platform-wide, so the trend is shown for Core + Live Events
     only."

emitted only when scope was NOT core_live — the branch in which the series is empty, so it
described a trend that was not on screen; and emitted never when a REGION was selected, which
is the case where a trend IS on screen and silently ignores the filter the operator just set.

The data it describes is one scalar per tick. services/ops.record_samples writes
`platform_health_ok` = the number of healthy services across the whole platform, with no
stage, region or test-mode dimension, and health_window selects those rows by time alone. So
the time range is the ONLY filter the trend can honour, and the note has to say so for every
other control on the page.

These tests touch no database: health_history_note is a pure function of the two facts that
decide the wording.
"""
import pytest

from app.services.ops import REGIONS, SCOPES, health_history_note

ALL_REGIONS = [code for code, _label in REGIONS]
# command_center computes `hist = health_window(...) if scope == "core_live" else None`,
# so this is the scope -> has_trend mapping under test.
HAS_TREND = {"core_live": True, "core": False, "live": False}


def test_every_scope_is_covered_by_this_file():
    # A new product scope must not quietly fall through to whatever wording happens to match.
    assert set(HAS_TREND) == set(SCOPES)


def test_regions_are_the_ones_the_console_offers():
    assert ALL_REGIONS == ["na", "eu", "apac", "sa"]


class TestTheTrendIsShown:
    """scope = Core + Live Events. The whole platform, so the trend matches what was asked."""

    def test_no_note_when_nothing_is_being_ignored(self):
        # All regions + the full scope: the platform-wide trend IS the requested trend.
        assert health_history_note(True, None) is None

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_a_selected_region_is_declared_as_not_applied(self, region):
        note = health_history_note(True, region)
        assert note is not None, "a region filter that does not reach the trend must be stated"
        assert "every region" in note
        assert "not only the selected one" in note

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_does_not_claim_the_region_was_applied(self, region):
        assert "only the selected region" not in health_history_note(True, region)


class TestTheTrendIsAbsent:
    """scope = Core platform / Live events. health_window is never called, series is []."""

    @pytest.mark.parametrize("scope", [s for s in SCOPES if not HAS_TREND[s]])
    def test_says_there_is_no_trend_rather_than_describing_one(self, scope):
        note = health_history_note(HAS_TREND[scope], None)
        assert "no trend" in note
        # The old wording said a trend "is shown", in the branch where none is.
        assert "trend is shown" not in note

    def test_separates_current_status_from_the_missing_trend(self):
        # Status IS scoped by stage (_scoped_health), so it must not inherit the caveat.
        note = health_history_note(False, None)
        assert "status above is current and does reflect it" in note

    @pytest.mark.parametrize("region", ALL_REGIONS)
    def test_names_both_limits_when_a_region_is_also_selected(self, region):
        note = health_history_note(False, region)
        assert "no trend" in note
        assert "every region" in note


@pytest.mark.parametrize("scope", sorted(SCOPES))
@pytest.mark.parametrize("region", [None] + ALL_REGIONS)
def test_every_scope_and_region_combination_is_consistent(scope, region):
    """No combination may contradict itself or the sampler."""
    note = health_history_note(HAS_TREND[scope], region)
    if note is None:
        # Silence is only allowed where nothing is being ignored.
        assert HAS_TREND[scope] and region is None
        return
    assert note.endswith(".")
    # Never claim a filter reached the samples.
    assert "for the selected region" not in note
    # A trend exists iff the note does not deny one.
    assert ("no trend" in note) is (not HAS_TREND[scope])
