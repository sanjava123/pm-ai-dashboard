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
        self.url = os.getenv('JIRA_URL')
        self.email = os.getenv('JIRA_EMAIL')
        self.token = os.getenv('JIRA_API_TOKEN')
        if not all([self.url, self.email, self.token]):
            raise ValueError('JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN must be set')
        self.jira = JIRA(server=self.url, basic_auth=(self.email, self.token))
        self._field_map = self._get_field_map()

    def _get_field_map(self) -> Dict[str, str]:
        return {f['name']: f['id'] for f in self.jira.fields()}

    def get_field_id(self, name: str) -> Optional[str]:
        return self._field_map.get(name)

    @retry(wait=wait_exponential(multiplier=1, min=2, max=10), stop=stop_after_attempt(3), retry=retry_if_exception_type(JIRAError))
    def search_issues_paginated(self, jql: str, fields: List[str], batch_size: int = 100) -> List[Any]:
        all_issues = []
        start_at = 0
        while True:
            logger.info(f'Fetching Jira issues: {start_at}...')
            issues = self.jira.search_issues(jql, startAt=start_at, maxResults=batch_size, fields=fields, expand='changelog')
            if not issues: break
            all_issues.extend(issues)
            if len(issues) < batch_size: break
            start_at += batch_size
        return all_issues

    def fetch_sprint_data(self, project_key: str, sprint_names: List[str] = None) -> pd.DataFrame:
        if sprint_names:
            sprint_clause = f'sprint in ({','.join([f'\"{s}\"' for s in sprint_names])})'
            jql = f'project = {project_key} AND {sprint_clause} ORDER BY created DESC'
        else:
            jql = f'project = {project_key} AND sprint in openSprints() ORDER BY created DESC'

        sp_id = self.get_field_id('Story Points') or self.get_field_id('Story Point Estimate')
        sprint_id = self.get_field_id('Sprint')
        fields = [f for f in ['summary','status','assignee','issuetype','created','updated','resolutiondate','priority','labels','components', sp_id, sprint_id] if f]

        try:
            issues = self.search_issues_paginated(jql, fields)
        except JIRAError as e:
            if 'Sprint' in str(e) or 'sprint' in str(e).lower() or e.status_code == 400:
                logger.warning(f'Sprint JQL failed, falling back to all issues: {e}')
                jql = f'project = {project_key} ORDER BY created DESC'
                issues = self.search_issues_paginated(jql, fields)
            else: raise

        logger.info(f'Fetched {len(issues)} issues.')
        rows = [self._parse_issue(iss, sp_id, sprint_id) for iss in issues]
        return pd.DataFrame(rows)

    def _parse_issue(self, issue: Any, sp_id: str, sprint_id: str) -> Dict:
        f = issue.fields
        key = issue.key
        summary = getattr(f, 'summary', '')
        status = getattr(f.status, 'name', 'Unknown') if f.status else 'Unknown'
        assignee = getattr(f.assignee, 'displayName', 'Unassigned') if f.assignee else 'Unassigned'
        itype = getattr(f.issuetype, 'name', 'Unknown') if f.issuetype else 'Unknown'
        created = getattr(f, 'created', None)
        updated = getattr(f, 'updated', None)
        resolved = getattr(f, 'resolutiondate', None)
        priority = getattr(f.priority, 'name', 'None') if f.priority else 'None'
        sp = getattr(f, sp_id, None) if sp_id else None
        sprint_obj = getattr(f, sprint_id, None) if sprint_id else None
        sprint_name = sprint_obj[-1].name if sprint_obj and isinstance(sprint_obj, list) and sprint_obj else 'No Sprint'

        START_STATUSES = {'In Progress', 'In Development', 'Doing', 'In Review', 'Code Review', 'Selected for Development'}
        DONE_STATUSES = {'Done', 'Closed', 'Resolved', 'Released', 'Deployed'}

        started = None
        done = resolved
        if hasattr(issue, 'changelog') and issue.changelog.histories:
            for h in issue.changelog.histories:
                for item in h.items:
                    if item.field == 'status':
                        if item.toString in START_STATUSES and not started: started = h.created
                        if item.toString in DONE_STATUSES: done = h.created
        if not started: started = created

        ct = lt = None
        if started and done:
            try:
                ds = datetime.strptime(started[:19], '%Y-%m-%dT%H:%M:%S')
                dd = datetime.strptime(done[:19], '%Y-%m-%dT%H:%M:%S')
                ct = round((dd - ds).total_seconds() / 86400, 2)
            except: pass
        if created and done:
            try:
                dc = datetime.strptime(created[:19], '%Y-%m-%dT%H:%M:%S')
                dd = datetime.strptime(done[:19], '%Y-%m-%dT%H:%M:%S')
                lt = round((dd - dc).total_seconds() / 86400, 2)
            except: pass

        return {'Key': key, 'Summary': summary, 'Team': self._guess_team(key, getattr(f, 'components', None)),
                'Sprint': sprint_name, 'Issue Type': itype, 'Status': status, 'Assignee': assignee,
                'Priority': priority, 'Story Points': float(sp) if sp else 0.0,
                'Created': created, 'Started': started, 'Resolved': resolved,
                'Cycle Time (days)': ct, 'Lead Time (days)': lt, 'Is Done': status in DONE_STATUSES}

    def _guess_team(self, key: str, components: List) -> str:
        if components: return components[0].name
        return key.split('-')[0] if '-' in key else 'Unknown'
