import pandas as pd

from core.matrices import topmeta_post_alias


def test_topmeta_post_alias_treats_numeric_share_as_percent_points():
    df = pd.DataFrame(
        {
            "Deck": ["A", "B"],
            "share": [7.69, 0.96],
        }
    )

    out = topmeta_post_alias(df, {})

    assert out.loc[out["Deck"] == "A", "Share_%"].iloc[0] == 7.69
    assert out.loc[out["Deck"] == "B", "Share_%"].iloc[0] == 0.96
    assert round(float(out["Share_%"].sum()), 2) == 8.65


def test_topmeta_post_alias_parses_percent_strings():
    df = pd.DataFrame(
        {
            "Deck": ["A", "B"],
            "Share": ["7.69%", "0.96%"],
        }
    )

    out = topmeta_post_alias(df, {})

    assert out.loc[out["Deck"] == "A", "Share_%"].iloc[0] == 7.69
    assert out.loc[out["Deck"] == "B", "Share_%"].iloc[0] == 0.96


def test_topmeta_post_alias_keeps_share_frac_as_fraction():
    df = pd.DataFrame(
        {
            "Deck": ["A", "B"],
            "Share_frac": [0.0769, 0.0096],
        }
    )

    out = topmeta_post_alias(df, {})

    assert out.loc[out["Deck"] == "A", "Share_%"].iloc[0] == 7.69
    assert out.loc[out["Deck"] == "B", "Share_%"].iloc[0] == 0.96
