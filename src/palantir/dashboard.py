"""Streamlit dashboard for Palantir analytics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import streamlit as st
import pandas as pd

from palantir.config import BASE_DIR

DB_PATH = str(BASE_DIR / "data" / "palantir.db")


@st.cache_resource
def get_conn() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def load_posts(days: int) -> pd.DataFrame:
    conn = get_conn()
    return pd.read_sql_query(
        "SELECT unique_key, source_id, score, sent, created_at "
        "FROM posts WHERE created_at >= datetime('now', ?) "
        "ORDER BY created_at DESC",
        conn,
        params=(f"-{days} days",),
    )


def load_feedback(days: int) -> pd.DataFrame:
    conn = get_conn()
    return pd.read_sql_query(
        "SELECT unique_key, reaction, created_at "
        "FROM feedback WHERE created_at >= datetime('now', ?) "
        "ORDER BY created_at DESC",
        conn,
        params=(f"-{days} days",),
    )


def main() -> None:
    st.set_page_config(page_title="Palantir Dashboard", page_icon="📊", layout="wide")
    st.title("📊 Palantir Dashboard")

    # ── Sidebar ──────────────────────────────────────────────
    days = st.sidebar.slider("Період (днів)", 1, 90, 7)

    posts = load_posts(days)
    feedback = load_feedback(days)

    if posts.empty:
        st.info("Немає даних за обраний період.")
        return

    # ── Metrics ──────────────────────────────────────────────
    total = len(posts)
    sent = int(posts["sent"].sum())
    scored = posts[posts["score"].notna()]
    avg_score = scored["score"].mean() if not scored.empty else 0
    saved = len(feedback[feedback["reaction"] == "save"]) if not feedback.empty else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Оброблено", total)
    c2.metric("Рекомендовано", sent)
    c3.metric("Середня оцінка", f"{avg_score:.1f}/10")
    c4.metric("Збережено", saved)

    st.divider()

    # ── Charts ───────────────────────────────────────────────
    col_left, col_right = st.columns(2)

    with col_left:
        st.subheader("Розподіл оцінок")
        if not scored.empty:
            score_counts = (
                scored["score"]
                .value_counts()
                .sort_index()
                .reset_index()
            )
            score_counts.columns = ["Оцінка", "Кількість"]
            st.bar_chart(score_counts, x="Оцінка", y="Кількість")
        else:
            st.caption("Немає оцінених постів")

    with col_right:
        st.subheader("Постів за день")
        posts["date"] = pd.to_datetime(posts["created_at"]).dt.date
        daily = posts.groupby("date").size().reset_index(name="Кількість")
        daily.columns = ["Дата", "Кількість"]
        st.line_chart(daily, x="Дата", y="Кількість")

    st.divider()

    # ── Top sources ──────────────────────────────────────────
    col_left2, col_right2 = st.columns(2)

    with col_left2:
        st.subheader("Топ джерела (рекомендації)")
        sent_posts = posts[posts["sent"] == 1]
        if not sent_posts.empty:
            top = (
                sent_posts["source_id"]
                .value_counts()
                .head(10)
                .reset_index()
            )
            top.columns = ["Джерело", "Рекомендацій"]
            top["Джерело"] = top["Джерело"].str.replace("tg:@", "@").str.replace("rss:", "")
            st.dataframe(top, hide_index=True, use_container_width=True)
        else:
            st.caption("Немає рекомендацій")

    with col_right2:
        st.subheader("Фідбек")
        if not feedback.empty:
            fb_counts = feedback["reaction"].value_counts().reset_index()
            fb_counts.columns = ["Реакція", "Кількість"]
            fb_counts["Реакція"] = fb_counts["Реакція"].map(
                {"save": "📌 Збережено", "skip": "👎 Не цікаво"}
            )
            st.bar_chart(fb_counts, x="Реакція", y="Кількість")
        else:
            st.caption("Немає фідбеку")

    # ── Recent recommendations ───────────────────────────────
    st.divider()
    st.subheader("Останні рекомендації")
    recent = posts[posts["sent"] == 1].head(20).copy()
    if not recent.empty:
        recent["source_id"] = recent["source_id"].str.replace("tg:@", "@").str.replace("rss:", "")
        st.dataframe(
            recent[["created_at", "source_id", "score"]].rename(
                columns={
                    "created_at": "Дата",
                    "source_id": "Джерело",
                    "score": "Оцінка",
                }
            ),
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption("Немає рекомендацій")


if __name__ == "__main__":
    main()
