from pathlib import Path

import pandas as pd
import streamlit as st

st.title("문의 목록")
csv = Path(__file__).parent / "data" / "56_practice-inquiries.csv"
df = pd.read_csv(csv)

kind = st.selectbox("type", ["전체"] + sorted(df["type"].unique()))
if kind != "전체":
    df = df[df["type"] == kind]

status = st.selectbox("status", ["전체"] + sorted(pd.read_csv(csv)["status"].unique()))
if status != "전체":
    df = df[df["status"] == status]

st.write(f"보이는 줄 수: {len(df)}")
st.dataframe(df, use_container_width=True)
