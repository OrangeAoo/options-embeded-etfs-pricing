"""Checks for comparison attribution and explicit surface boundaries."""
import pytest
import shuhao_compare as compare


def test_ordered_bridge_reconciles_even_when_large_changes_offset():
    g,v=compare.METRICS
    stages=[("original",{g:490.,v:10.5}), ("cohort",{g:530.,v:11.}),
            ("IV",{g:500.,v:10.}), ("final",{g:487.,v:10.01})]
    rows=compare.bridge(stages)
    assert rows[0]["change_from_previous"] is None
    for metric in compare.METRICS:
        assert rows[0]["totals"][metric]+sum(r["change_from_previous"][metric] for r in rows[1:]) == pytest.approx(rows[-1]["totals"][metric])
    assert rows[1]["change_from_previous"][g] > 0
    assert rows[2]["change_from_previous"][g] < 0


@pytest.mark.parametrize("dte,grid,status",[
    (0,[30,365],"intraday_expiry_time_required"),
    (28,[30,365],"shorter_than_observed_grid"),
    (30,[30,365],"inside_assumed_term_range_coordinate_still_unconfirmed"),
    (365,[30,365],"inside_assumed_term_range_coordinate_still_unconfirmed"),
    (392,[30,365],"longer_than_observed_grid"),
    (392,[365,548],"inside_assumed_term_range_coordinate_still_unconfirmed"),
    (392,[],"no_standard_surface_for_underlying"),
])
def test_term_range_never_silently_extends_a_one_year_curve(dte,grid,status):
    assert compare.term_status(dte,grid)==status


def test_missing_book_and_expiry_day_are_not_declared_complete():
    assert "ELN" in compare.coverage_note("JEPI",{})
    assert "expiry-day" in compare.coverage_note("KNG",{"0DTE_timestamp_settlement_and_roll_required":66})
    assert "lack clean IV" in compare.coverage_note("BALT",{"missing_usable_iv":4})
    assert "conditional" in compare.coverage_note("BOXX",{"analytic_box_net_zero":8})
