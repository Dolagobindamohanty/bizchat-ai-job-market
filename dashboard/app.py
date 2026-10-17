from pathlib import Path
import sys

# Streamlit commonly launches this file from the dashboard directory. Make the
# repository root importable in that mode as well as when launched from root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from bizchat.analytics import explode_skills, prepare_jobs, recommendations, skill_demand


DATA_PATH = ROOT / "data" / "raw" / "jobs.csv"

st.set_page_config(page_title="BizChat · AI Job Market Intelligence", page_icon="✦", layout="wide")

st.markdown(
    """
    <style>
    :root { --ink: #18212f; --muted: #667085; --accent: #5b5ce2; }
    .block-container { padding-top: 2.1rem; padding-bottom: 3rem; max-width: 1400px; }
    .hero { padding: 1.3rem 1.5rem; border-radius: 20px; background: linear-gradient(120deg, #151a3d 0%, #4c39a3 58%, #8b5cf6 100%); color: white; margin-bottom: 1.1rem; }
    .hero h1 { margin: 0; font-size: 2.35rem; letter-spacing: -0.04em; }
    .hero p { margin: .45rem 0 0; color: #dfe2ff; font-size: 1.04rem; }
    [data-testid="stMetric"] { background: #f7f7ff; border: 1px solid #e6e7fb; padding: 1rem; border-radius: 14px; }
    .insight { background: #f4f5ff; border-left: 4px solid #6366f1; border-radius: 10px; padding: .75rem 1rem; color: #30374a; }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data
def load_jobs() -> pd.DataFrame:
    return prepare_jobs(pd.read_csv(DATA_PATH))


jobs = load_jobs()

st.markdown(
    '<div class="hero"><h1>✦ BizChat</h1><p>AI Job Market Intelligence for India · skills, salaries, cities, and what to learn next</p></div>',
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Explore the market")
    st.caption("This MVP uses a transparent sample dataset. Replace data/raw/jobs.csv with permitted job data to refresh the analysis.")
    cities = ["All cities"] + sorted(jobs["location"].unique().tolist())
    selected_city = st.selectbox("City", cities)
    sources = ["All sources"] + sorted(jobs["source"].unique().tolist())
    selected_source = st.selectbox("Source", sources)
    min_date, max_date = jobs["posted_date"].min().date(), jobs["posted_date"].max().date()
    date_range = st.date_input("Posted between", value=(min_date, max_date), min_value=min_date, max_value=max_date)
    st.divider()
    st.markdown("**Data coverage**")
    st.write(f"{min_date:%d %b %Y} – {max_date:%d %b %Y}")
    st.write(f"{jobs['source'].nunique()} sources · {jobs['company'].nunique()} companies")

filtered = jobs.copy()
if selected_city != "All cities":
    filtered = filtered[filtered["location"] == selected_city]
if selected_source != "All sources":
    filtered = filtered[filtered["source"] == selected_source]
if isinstance(date_range, tuple) and len(date_range) == 2:
    filtered = filtered[filtered["posted_date"].dt.date.between(date_range[0], date_range[1])]

if filtered.empty:
    st.warning("No jobs match these filters. Try widening the city, source, or date range.")
    st.stop()

demand = skill_demand(filtered)
median_salary = filtered["salary_mid_lpa"].median()
top_skill = demand.iloc[0]["skill"] if not demand.empty else "—"
top_city = filtered["location"].value_counts().index[0]

kpi = st.columns(4)
kpi[0].metric("Jobs tracked", f"{len(filtered):,}", f"{len(filtered) / len(jobs):.0%} of sample")
kpi[1].metric("Median salary", f"₹{median_salary:.1f} LPA", "estimated midpoint")
kpi[2].metric("Top skill", top_skill, f"{int(demand.iloc[0]['jobs'])} postings")
kpi[3].metric("Leading city", top_city, f"{filtered['location'].value_counts().iloc[0]} postings")

st.markdown("### Market pulse")
st.markdown(
    f'<div class="insight">The current view shows <b>{len(filtered)} roles</b>. Demand is led by <b>{top_skill}</b>, while <b>{top_city}</b> has the highest hiring volume. Use the tabs below to move from market signals to a personalized learning plan.</div>',
    unsafe_allow_html=True,
)

overview, skills_tab, locations_tab, salary_tab, learn_tab = st.tabs(["Overview", "Skill demand", "Locations", "Salaries", "Learn next"])

with overview:
    left, right = st.columns([1.15, 1])
    with left:
        st.subheader("Most requested skills")
        fig = px.bar(demand.head(12).sort_values("jobs"), x="jobs", y="skill", orientation="h", color="jobs", color_continuous_scale="Purples")
        fig.update_layout(height=460, margin=dict(l=0, r=10, t=10, b=0), coloraxis_showscale=False, xaxis_title="Job postings", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
    with right:
        st.subheader("Hiring by company")
        company = filtered["company"].value_counts().reset_index()
        company.columns = ["company", "jobs"]
        fig = px.bar(company.head(10).sort_values("jobs"), x="jobs", y="company", orientation="h", color_discrete_sequence=["#7c3aed"])
        fig.update_layout(height=460, margin=dict(l=0, r=10, t=10, b=0), xaxis_title="Job postings", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)

with skills_tab:
    st.subheader("Skills employers are asking for")
    skills = explode_skills(filtered)
    trend = skills.assign(month=skills["posted_date"].dt.to_period("W").astype(str)).groupby(["month", "skill"]).size().reset_index(name="jobs")
    chosen_skills = st.multiselect("Compare skills over time", demand.head(8)["skill"].tolist(), default=demand.head(4)["skill"].tolist())
    if chosen_skills:
        chart_data = trend[trend["skill"].isin(chosen_skills)]
        fig = px.line(chart_data, x="month", y="jobs", color="skill", markers=True, color_discrete_sequence=px.colors.qualitative.Bold)
        fig.update_layout(height=430, margin=dict(l=0, r=0, t=10, b=0), xaxis_title=None, yaxis_title="Postings")
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(demand.rename(columns={"skill": "Skill", "jobs": "Job postings"}), use_container_width=True, hide_index=True)

with locations_tab:
    st.subheader("Where AI hiring is concentrated")
    city_col, heat_col = st.columns([1, 1.4])
    with city_col:
        city = filtered["location"].value_counts().reset_index()
        city.columns = ["city", "jobs"]
        fig = px.bar(city.sort_values("jobs"), x="jobs", y="city", orientation="h", color="jobs", color_continuous_scale="Teal")
        fig.update_layout(height=430, margin=dict(l=0, r=10, t=10, b=0), coloraxis_showscale=False, xaxis_title="Postings", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
    with heat_col:
        city_skill = filtered.explode("skills").pivot_table(index="location", columns="skills", values="job_id", aggfunc="count", fill_value=0)
        fig = px.imshow(city_skill, aspect="auto", color_continuous_scale="Purples", labels={"x": "Skill", "y": "City", "color": "Jobs"})
        fig.update_layout(height=430, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

with salary_tab:
    st.subheader("Salary signals")
    salary_left, salary_right = st.columns(2)
    with salary_left:
        by_city = filtered.groupby("location", as_index=False)["salary_mid_lpa"].median().sort_values("salary_mid_lpa")
        fig = px.bar(by_city, x="salary_mid_lpa", y="location", orientation="h", color="salary_mid_lpa", color_continuous_scale="Viridis")
        fig.update_layout(height=420, margin=dict(l=0, r=10, t=10, b=0), coloraxis_showscale=False, xaxis_title="Median midpoint (₹ LPA)", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
    with salary_right:
        salary_skills = explode_skills(filtered).groupby("skill", as_index=False)["salary_mid_lpa"].median().sort_values("salary_mid_lpa", ascending=False).head(12)
        fig = px.bar(salary_skills.sort_values("salary_mid_lpa"), x="salary_mid_lpa", y="skill", orientation="h", color_discrete_sequence=["#0891b2"])
        fig.update_layout(height=420, margin=dict(l=0, r=10, t=10, b=0), xaxis_title="Median midpoint (₹ LPA)", yaxis_title=None)
        st.plotly_chart(fig, use_container_width=True)
    st.caption("Salary is an estimated midpoint of the listed range, not a guaranteed offer. Roles without salary data should be excluded from salary analytics in production.")

with learn_tab:
    st.subheader("Build your next-skill plan")
    st.write("Tell BizChat what you already know. It will prioritize skills that appear frequently in the selected market view.")
    skill_options = sorted({skill for skills in jobs["skills"] for skill in skills})
    current = st.multiselect("Current skills", skill_options, default=["Python", "SQL"] if "Python" in skill_options else [])
    recs = recommendations(filtered, current, selected_city)
    if recs.empty:
        st.success("You already cover all skills detected in this view. Try another city or expand the dataset.")
    else:
        recs = recs.rename(columns={"skill": "Skill", "demand": "Demand", "reason": "Why it matters"})
        recs.insert(0, "Priority", range(1, len(recs) + 1))
        st.dataframe(recs[["Priority", "Skill", "Demand", "Why it matters"]], use_container_width=True, hide_index=True)
        st.info("Recommendation logic: highest normalized demand among skills you have not selected. Future versions can add target role, salary, growth, and skill-gap inputs.")

st.divider()
st.caption("BizChat MVP · Data last refreshed from the local sample: 17 October 2026 · Designed for permitted, transparent job-data collection.")
