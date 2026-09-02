# app.py
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import logging

# Local Imports
from src.jira_client import JiraClient
from src.metrics import calculate_kpis, get_trend_data
from src.llm_client import generate_sprint_summary, generate_retro_prep

# --- Config ---
logging.basicConfig(level=logging.INFO)
st.set_page_config(page_title="PM AI Pulse", page_icon="🤖", layout="wide", initial_sidebar_state="expanded")

# --- Secrets Check ---
REQUIRED_SECRETS = ["JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "GROQ_API_KEY", "JIRA_PROJECT_KEY"]
missing = [s for s in REQUIRED_SECRETS if s not in st.secrets]
if missing:
    st.error(f"🔴 **Missing Secrets in Streamlit Cloud:** `{', '.join(missing)}`")
    st.info("Go to your App Settings -> Secrets and add them. See README.")
    st.stop()

# Inject secrets into OS ENV for libraries (jira, groq clients read os.getenv)
import os
for k, v in st.secrets.items():
    os.environ[k] = v

# --- Cached Resources ---
@st.cache_resource
def get_jira_client():
    return JiraClient()

@st.cache_data(ttl=300) # Cache data for 5 mins
def load_data(_client, project_key, selected_sprints):
    # Convert list to tuple for hashing
    return _client.fetch_sprint_data(project_key, sprint_names=selected_sprints)

@st.cache_data(ttl=3600) # Cache AI responses longer (1hr)
def get_ai_summary(_kpis, team, sprint):
    return generate_sprint_summary(_kpis, team, sprint)

@st.cache_data(ttl=3600)
def get_ai_retro(_df, sprint):
    return generate_retro_prep(_df, sprint)

# --- Sidebar ---
with st.sidebar:
    st.image("https://cdn.simpleicons.org/atlassian/0052CC", width=60)
    st.title("🤖 PM AI Pulse")
   # LINE 53 - CHANGE ONLY THIS WORD:
    st.caption(f"Project: `{st.secrets.JIRA_PROJECT_KEY}` | Powered by Groq + Jira")

    client = get_jira_client()
    
    # Fetch Sprint List for Filter (Lightweight call)
    # Note: In prod, cache sprint list separately
    try:
        # Quick fetch just to get sprint names for dropdown
        all_sprints_df = load_data(client, st.secrets.JIRA_PROJECT_KEY, None) 
        sprint_options = sorted(all_sprints_df["Sprint"].unique(), reverse=True)
    except Exception as e:
        st.error(f"Jira Connection Failed: {e}")
        st.stop()

    selected_sprints = st.multiselect("📅 Sprints", sprint_options, default=sprint_options[:1])
    current_sprint = selected_sprints[0] if selected_sprints else None
    
    team_options = ["All"] + sorted(all_sprints_df["Team"].unique().tolist())
    selected_team = st.selectbox("👥 Team", team_options)
    
    st.divider()
    if st.button("🔄 Refresh Data (Clear Cache)"):
        st.cache_data.clear()
        st.rerun()
    
    st.divider()
    st.markdown("**AI Features**")
    run_summary = st.button("✍️ Generate AI Sprint Summary")
    run_retro = st.button("🧠 Generate Retro Topics")

# --- Main Layout ---
if not current_sprint:
    st.warning("Select a Sprint.")
    st.stop()

# 1. LOAD DATA
with st.spinner(f"Fetching Jira Data for {len(selected_sprints)} sprint(s)..."):
    df = load_data(client, st.secrets.JIRA_PROJECT_KEY, tuple(selected_sprints))

# Filter Team
if selected_team != "All":
    df = df[df["Team"] == selected_team]

if df.empty:
    st.warning("No issues found for selection.")
    st.stop()

# 2. CALC METRICS
kpis = calculate_kpis(df, current_sprint)

# 3. HEADER KPIs
st.title(f"📊 {current_sprint} Health Dashboard")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("🎯 Predictability", f"{kpis['predictability']}%", delta=f"Target 85%", delta_color="normal" if kpis['predictability']>=85 else "inverse")
c2.metric("⚡ Cycle Time (P85)", f"{kpis['cycle_time_p85']}d", delta=f"Target <5d", delta_color="inverse" if kpis['cycle_time_p85']>5 else "normal")
c3.metric("📦 Throughput", f"{kpis['throughput']} tickets")
c4.metric("🐛 Bug Ratio", f"{kpis['bug_ratio']*100:.0f}%")
c5.metric("⏳ Aging WIP", f"{kpis['aging_wip_count']} > 5d", delta=f"Total WIP: {kpis['wip_count']}")

st.divider()

# 4. TABS
tab1, tab2, tab3, tab4 = st.tabs(["📈 Trends", "🔍 Flow & Cycle Time", "🤖 AI Insights", "📋 Raw Data"])

with tab1:
    st.subheader("Predictability Trend (Say/Do)")
    trend_df = get_trend_data(df)
    fig = px.bar(trend_df, x="Sprint", y=["Committed", "Done"], barmode="group", text_auto=True)
    fig.add_trace(go.Scatter(x=trend_df["Sprint"], y=trend_df["Predictability"], name="Predictability %", yaxis="y2", mode="lines+markers", line=dict(color="red", width=3)))
    fig.update_layout(yaxis2=dict(title="Predictability %", overlaying="y", side="right", range=[0, 120], showgrid=False))
    st.plotly_chart(fig, use_container_width=True)

with tab2:
    colA, colB = st.columns(2)
    with colA:
        st.subheader("Cycle Time Distribution")
        done_df = df[df["Is Done"]]
        if not done_df.empty:
            fig = px.histogram(done_df, x="Cycle Time (days)", color="Team", marginal="box", nbins=20)
            fig.add_vline(x=kpis['cycle_time_p85'], line_dash="dash", line_color="red", annotation_text=f"P85: {kpis['cycle_time_p85']}d")
            st.plotly_chart(fig, use_container_width=True)
    with colB:
        st.subheader("Status Breakdown (Current Sprint)")
        sprint_df = df[df["Sprint"] == current_sprint]
        fig = px.sunburst(sprint_df, path=["Team", "Status", "Issue Type"], values="Story Points")
        st.plotly_chart(fig, use_container_width=True)

with tab3:
    st.subheader("🤖 AI PM Analyst (Groq Llama3)")
    
    if run_summary:
        with st.spinner("Asking Llama3-70b to write Stakeholder Update..."):
            # Use first team if 'All' selected for summary context
            team_ctx = selected_team if selected_team != "All" else df["Team"].mode()[0]
            result = get_ai_summary(kpis, team_ctx, current_sprint)
            
            if "error" in result:
                st.error(f"AI Error: {result['error']}")
            else:
                st.success("Generated!")
                st.markdown(f"### {result.get('headline', 'Sprint Summary')}")
                st.metric("Health Score", f"{result.get('health_score', 'N/A')}/10")
                
                c1, c2 = st.columns(2)
                with c1:
                    st.markdown("**✅ Key Achievements**")
                    for a in result.get('key_achievements', []): st.write(f"- {a}")
                    st.markdown("**💡 Recommendations**")
                    for r in result.get('recommendations', []): st.write(f"- {r}")
                with c2:
                    st.markdown("**🚨 Risks & Blockers**")
                    for r in result.get('risks_blockers', []): st.write(f"- {r}")
    
    st.divider()
    
    if run_retro:
        with st.spinner("Analyzing tickets for Retro themes..."):
            result = get_ai_retro(df, current_sprint)
            if "error" in result:
                st.error(f"AI Error: {result['error']}")
            else:
                st.success("Retro Prep Ready!")
                for t in result.get("topics", []):
                    with st.expander(f"💡 {t['title']} ({t['suggested_activity']})"):
                        st.write(t['data_evidence'])

with tab4:
    st.subheader("Aging WIP (Action Required Now)")
    if kpis['aging_wip_details']:
        wip_df = pd.DataFrame(kpis['aging_wip_details'])
        wip_df["Age (Days)"] = wip_df["Age (Days)"].round(1)
        st.dataframe(wip_df, use_container_width=True, hide_index=True)
    else:
        st.success("No aging WIP! 🎉")
    
    st.subheader("Carryover (Incomplete from Past Sprints)")
    if kpis['carryover_details']:
        st.dataframe(pd.DataFrame(kpis['carryover_details']), use_container_width=True, hide_index=True)
    
    st.subheader("Full Dataset")
    st.dataframe(df.drop(columns=["Created", "Started", "Resolved"]), use_container_width=True, hide_index=True)
