# src/jira_client.py
import os
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from jira import JIRA
from jira.exceptions import JIRAError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import pandas as pd

logger = logging.getLogger(__name__)

class JiraClient:
    def __init__(self):
        self.url = os.getenv("JIRA_URL")
        self.email = os.getenv("JIRA_EMAIL")
        self.token = os.getenv("JIRA_API_TOKEN")
        
        if not all([self.url, self.email, self.token]):
            raise ValueError("JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN must be set in Environment/Secrets")
        
        self.jira = JIRA(server=self.url, basic_auth=(self.email, self.token))
        # Cache field IDs for custom fields (Story Points, Sprint)
        self._field_map = self._get_field_map()

    def _get_field_map(self) -> Dict[str, str]:
        """Map custom field names to IDs (e.g., 'Story Points' -> 'customfield_10002')"""
        fields = self.jira.fields()
        return {f['name']: f['id'] for f in fields}

    def get_field_id(self, name: str) -> Optional[str]:
        return self._field_map.get(name)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3),
        retry=retry_if_exception_type(JIRAError)
    )
    def search_issues_paginated(self, jql: str, fields: List[str], batch_size: int = 100) -> List[Any]:
        """Fetch ALL issues matching JQL, handling pagination."""
        all_issues = []
        start_at = 0
        while True:
            logger.info(f"Fetching Jira issues: {start_at}...")
            issues = self.jira.search_issues(
                jql, 
                startAt=start_at, 
                maxResults=batch_size, 
                fields=fields,
                expand='changelog' # CRITICAL for Cycle Time calculation
            )
            if not issues:
                break
            all_issues.extend(issues)
            if len(issues) < batch_size:
                break
            start_at += batch_size
        return all_issues

    def fetch_sprint_data(self, project_key: str, sprint_names: List[str] = None) -> pd.DataFrame:
        """
        Main entry point. Fetches issues for specific sprints or open sprints.
        Calculates Cycle Time from Changelog.
        """
        # 1. Build JQL
        if sprint_names:
            sprint_clause = f"sprint in ({','.join([f'\"{s}\"' for s in sprint_names])})"
        else:
            sprint_clause = "sprint in openSprints()"
        
        jql = f"project = {project_key} AND {sprint_clause} ORDER BY created DESC"
        
        # 2. Define Fields to Fetch (Reduce Payload)
        # System fields: summary, status, assignee, issuetype, created, updated, resolutiondate, sprint, story points
        # We need the ID for Story Points and Sprint usually
        sp_id = self.get_field_id("Story Points") or self.get_field_id("Story Point Estimate")
        sprint_id = self.get_field_id("Sprint")
        
        fields = [
            "summary", "status", "assignee", "issuetype", "created", "updated", 
            "resolutiondate", "priority", "labels", "components",
            sp_id, sprint_id
        ]
        fields = [f for f in fields if f] # Remove None

        issues = self.search_issues_paginated(jql, fields)
        logger.info(f"Fetched {len(issues)} issues from Jira.")

        # 3. Parse to DataFrame
        rows = []
        for issue in issues:
            rows.append(self._parse_issue(issue, sp_id, sprint_id))
        
        return pd.DataFrame(rows)

    def _parse_issue(self, issue: Any, sp_id: str, sprint_id: str) -> Dict:
        """Extract flat dict from Jira Issue object, including Cycle Time from Changelog."""
        fields = issue.fields
        
        # --- Standard Fields ---
        key = issue.key
        summary = getattr(fields, 'summary', '')
        status = getattr(fields.status, 'name', 'Unknown') if fields.status else 'Unknown'
        assignee = getattr(fields.assignee, 'displayName', 'Unassigned') if fields.assignee else 'Unassigned'
        issue_type = getattr(fields.issuetype, 'name', 'Unknown') if fields.issuetype else 'Unknown'
        created = getattr(fields, 'created', None)
        updated = getattr(fields, 'updated', None)
        resolved = getattr(fields, 'resolutiondate', None)
        priority = getattr(fields.priority, 'name', 'None') if fields.priority else 'None'
        
        # --- Custom Fields ---
        story_points = getattr(fields, sp_id, None) if sp_id else None
        sprint_obj = getattr(fields, sprint_id, None) if sprint_id else None
        sprint_name = sprint_obj[-1].name if sprint_obj and isinstance(sprint_obj, list) and sprint_obj else "No Sprint"

        # --- Cycle Time Calculation (Changelog Parsing) ---
        # Find first transition TO "In Progress" (or your "Start" statuses)
        # Find last transition TO "Done" (or your "Done" statuses)
        started_date = None
        done_date = resolved # Fallback to resolution date
        
        START_STATUSES = {"In Progress", "In Development", "Doing", "In Review"} # Customize!
        DONE_STATUSES = {"Done", "Closed", "Resolved", "Released"}

        if hasattr(issue, 'changelog') and issue.changelog.histories:
            for history in issue.changelog.histories:
                for item in history.items:
                    if item.field == 'status':
                        # Started: First time entering a Start status
                        if item.toString in START_STATUSES and not started_date:
                            started_date = history.created
                        # Done: Last time entering a Done status
                        if item.toString in DONE_STATUSES:
                            done_date = history.created
        
        # Fallback: If never moved to "In Progress", use Created date
        if not started_date:
            started_date = created

        # Calculate Days (Float)
        cycle_time = None
        lead_time = None
        if started_date and done_date:
            try:
                dt_start = datetime.strptime(started_date[:19], "%Y-%m-%dT%H:%M:%S")
                dt_done = datetime.strptime(done_date[:19], "%Y-%m-%dT%H:%M:%S")
                cycle_time = round((dt_done - dt_start).total_seconds() / 86400, 2)
            except: pass
        
        if created and done_date:
            try:
                dt_create = datetime.strptime(created[:19], "%Y-%m-%dT%H:%M:%S")
                dt_done = datetime.strptime(done_date[:19], "%Y-%m-%dT%H:%M:%S")
                lead_time = round((dt_done - dt_create).total_seconds() / 86400, 2)
            except: pass

        return {
            "Key": key,
            "Summary": summary,
            "Team": self._guess_team(key, components=getattr(fields, 'components', None)),
            "Sprint": sprint_name,
            "Issue Type": issue_type,
            "Status": status,
            "Assignee": assignee,
            "Priority": priority,
            "Story Points": float(story_points) if story_points else 0.0,
            "Created": created,
            "Started": started_date,
            "Resolved": resolved,
            "Cycle Time (days)": cycle_time,
            "Lead Time (days)": lead_time,
            "Is Done": status in DONE_STATUSES
        }

    def _guess_team(self, key: str, components: List) -> str:
        # Simple heuristic: Project prefix or Component
        if components:
            return components[0].name
        return key.split('-')[0] if '-' in key else "Unknown"
