from pathlib import Path

import requests
import streamlit as st

env = dict(
    line.split("=", 1)
    for line in (Path(__file__).parent / ".env").read_text(encoding="utf-8").splitlines()
    if "=" in line and not line.startswith("#")
)
URL = env["SUPABASE_URL"].strip().rstrip("/") + "/rest/v1/guestbook"
HEADERS = {"apikey": env["SUPABASE_KEY"].strip()}

st.title("방명록")

with st.form("write", clear_on_submit=True):
    message = st.text_input("한 줄 남기기")
    if st.form_submit_button("남기기") and message.strip():
        requests.post(URL, headers=HEADERS, json={"message": message.strip()}, timeout=10).raise_for_status()

rows = requests.get(URL, headers=HEADERS, params={"order": "created_at.desc"}, timeout=10)
rows.raise_for_status()
for row in rows.json():
    st.write(f"{row['created_at'][:16].replace('T', ' ')}  {row['message']}")
