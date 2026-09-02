# src/metrics.py
import pandas as pd
import numpy as np

DONE_STATUSES = {"Done", "Closed", "Resolved", "Released"}

def calculate_kpis(df: pd.DataFrame, current_sprint: str) -> Dict:
    if df.empty: return {}
    
    sprint_df = df[df["Sprint"] == current_sprint]
    done_df = df[df["Is Done"] == True]
    
    # 1. Predictability (Say/Do) - Current Sprint
    committed_pts = sprint_df["Story Points"].sum()
    completed_pts = sprint_df[sprint_df["Is Done"]]["Story Points"].sum()
    predictability = (completed_pts / committed_pts * 100) if committed_pts > 0 else 0

    # 2. Cycle Time P85 (Global / Team)
    ct_p85 = done_df["Cycle Time (days)"].quantile(0.85) if not done_df.empty else 0
    
    # 3. Scope Creep (Points added after Sprint Start)
    # Heuristic: Issues where Created > Sprint Start Date (requires Sprint Start Date metadata)
    # Simplified: % of tickets created *after* the first ticket in sprint? 
    # Better: Compare 'Original Estimate' vs 'Story Points' if you track that. 
    # Placeholder:
    scope_creep_pct = 0.0 

    # 4. WIP Aging
    wip_df = df[~df["Is Done"]].copy()
    wip_df["Age (Days)"] = (pd.Timestamp.now(tz='UTC') - pd.to_datetime(wip_df["Created"], utc=True)).dt.total_seconds() / 86400
    aging_wip = wip_df[wip_df["Age (Days)"] > 5] # Threshold 5 days

    # 5. Carryover
    # Tickets in *previous* sprints not done
    past_sprints = df[df["Sprint"] != current_sprint]
    carryover = past_sprints[~past_sprints["Is Done"]]

    return {
        "predictability": round(predictability, 1),
        "committed_pts": committed_pts,
        "completed_pts": completed_pts,
        "cycle_time_p85": round(ct_p85, 1) if ct_p85 else 0,
        "wip_count": len(wip_df),
        "aging_wip_count": len(aging_wip),
        "aging_wip_details": aging_wip[["Key", "Summary", "Team", "Age (Days)", "Status"]].to_dict('records'),
        "carryover_count": len(carryover),
        "carryover_details": carryover[["Key", "Summary", "Sprint", "Team"]].to_dict('records'),
        "throughput": len(done_df[done_df["Sprint"] == current_sprint]),
        "bug_ratio": len(done_df[(done_df["Sprint"]==current_sprint) & (done_df["Issue Type"]=="Bug")]) / max(1, len(done_df[done_df["Sprint"]==current_sprint]))
    }

def get_trend_data(df: pd.DataFrame) -> pd.DataFrame:
    """Predictability per sprint for charting."""
    sprints = sorted(df["Sprint"].unique())
    data = []
    for s in sprints:
        sdf = df[df["Sprint"] == s]
        committed = sdf["Story Points"].sum()
        done = sdf[sdf["Is Done"]]["Story Points"].sum()
        data.append({"Sprint": s, "Predictability": (done/committed*100) if committed else 0, "Committed": committed, "Done": done})
    return pd.DataFrame(data)
