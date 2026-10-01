from metabase.dashboards import DASHBOARDS
from metabase.setup import GRID_WIDTH, layout


def test_layout_wraps_rows_and_never_overflows_the_grid():
    cards = [{"size": (12, 6)}, {"size": (12, 4)}, {"size": (8, 3)}, {"size": (24, 8)}]
    assert layout(cards) == [(0, 0), (0, 12), (6, 0), (9, 0)]


def test_every_card_reads_the_marts_and_fits_the_grid():
    for dashboard in DASHBOARDS:
        for card in dashboard["cards"]:
            width, _ = card["size"]
            assert 0 < width <= GRID_WIDTH, card["name"]
            assert "{m}." in card["sql"], card["name"]
