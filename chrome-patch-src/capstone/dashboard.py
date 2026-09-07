from __future__ import annotations

import os
from pathlib import Path
import sqlite3

import pandas as pd
import streamlit as st


DATABASE = Path(os.getenv("WEATHER_DB", "/results/capstone/weather.db"))


@st.cache_data(ttl=300)
def load_data(path: Path) -> pd.DataFrame:
    uri = f"file:{path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        return pd.read_sql_query("SELECT * FROM observations ORDER BY city", connection)


st.set_page_config(page_title="World Weather Explorer", layout="wide")
st.title("World Weather Explorer")
st.caption("Selenium-scraped observations, cleaned with pandas and stored in SQLite.")

if not DATABASE.exists():
    st.error("weather.db is missing. Run `make capstone` first.")
    st.stop()

data = load_data(DATABASE)
conditions = sorted(data["condition"].dropna().unique().tolist())
selected = st.sidebar.multiselect("Conditions", conditions, default=conditions)
low = float(data["temperature_c"].min())
high = float(data["temperature_c"].max())
temperature_range = st.sidebar.slider("Temperature range (°C)", low, high, (low, high))
filtered = data[
    data["condition"].isin(selected)
    & data["temperature_c"].between(temperature_range[0], temperature_range[1])
].copy()

left, middle, right = st.columns(3)
left.metric("Cities", len(filtered))
middle.metric("Average °C", f"{filtered['temperature_c'].mean():.1f}" if len(filtered) else "—")
right.metric("Conditions", filtered["condition"].nunique())

st.subheader("Temperature by city")
city_chart = filtered.nlargest(30, "temperature_c").sort_values("temperature_c").set_index("city")
st.bar_chart(city_chart[["temperature_c"]], horizontal=True)

st.subheader("Condition frequency")
condition_chart = filtered.groupby("condition").size().rename("cities").sort_values()
st.bar_chart(condition_chart, horizontal=True)

st.subheader("Temperature bands")
band_order = ["cold", "mild", "warm", "hot"]
band_chart = filtered.groupby("temperature_band").size().reindex(band_order, fill_value=0).rename("cities")
st.bar_chart(band_chart)

st.subheader("Filtered observations")
st.dataframe(
    filtered[["city", "observed_local", "condition", "temperature_c", "temperature_f"]],
    use_container_width=True,
    hide_index=True,
)
