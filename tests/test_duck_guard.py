import pandas as pd
import pytest

from caseweave.db import duck


def test_replace_table_refuses_empty_frame(tmp_path):
    """The CREATE OR REPLACE footgun: an upstream failure returning zero rows
    must not silently wipe a populated table."""
    con = duck.connect(tmp_path / "t.duckdb")
    duck.replace_table(
        con,
        "addresses",
        pd.DataFrame(
            [
                {
                    "address_id": "AD0001",
                    "line1": "1 Elm St",
                    "city": "San Diego",
                    "region": "CA",
                    "postcode": "92101",
                    "country": "US",
                }
            ]
        ),
    )
    assert duck.counts(con)["addresses"] == 1

    with pytest.raises(ValueError, match="refusing to load"):
        duck.replace_table(con, "addresses", pd.DataFrame())

    assert duck.counts(con)["addresses"] == 1, "table must survive the refusal"
    con.close()
