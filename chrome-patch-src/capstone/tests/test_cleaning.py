import pandas as pd

from weather_pipeline import RAW_COLUMNS, clean_weather_frame


def test_cleaning_converts_fahrenheit_and_removes_duplicates():
    rows = [
        ["Example *", "Thu 12:00", " Clear ", "68 °F", "https://example.test", "2026-01-01T00:00:00+00:00"],
        ["Example *", "Thu 12:00", " Clear ", "68 °F", "https://example.test", "2026-01-01T00:00:00+00:00"],
        ["Broken", "Thu 12:00", "Unknown", "n/a", "https://example.test", "2026-01-01T00:00:00+00:00"],
    ]
    clean, stats = clean_weather_frame(pd.DataFrame(rows, columns=RAW_COLUMNS))
    assert len(clean) == 1
    assert clean.iloc[0]["city"] == "Example"
    assert clean.iloc[0]["temperature_c"] == 20.0
    assert clean.iloc[0]["temperature_band"] == "warm"
    assert stats == {"raw_rows": 3, "clean_rows": 1, "removed_rows": 2}
