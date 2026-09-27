"""Asana collector: tasks assigned to Margaret, via the REST API.

collect() returns the schema's tasks block: due_today, overdue and
completed_yesterday. Uses ASANA_TOKEN (a personal access token). With an
empty token it raises NotConfigured so the source shows as not_configured.

Business tags come from the project name through config.business_from_text,
because Asana tasks carry no business field of their own.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import requests
from dateutil import parser as dateparser

from . import config

API = "https://app.asana.com/api/1.0"
PAGE_SIZE = 100
TASK_FIELDS = ",".join([
    "gid", "name", "due_on", "due_at", "completed", "completed_at",
    "permalink_url", "projects.name", "memberships.project.name",
])


def _session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    })
    return s


def _get(session: requests.Session, path: str, params: dict | None = None) -> dict:
    resp = session.get(f"{API}{path}", params=params or {}, timeout=config.HTTP_TIMEOUT)
    if resp.status_code == 401:
        raise RuntimeError("Asana rejected the token (401). Check ASANA_TOKEN.")
    resp.raise_for_status()
    return resp.json()


def _paged(session: requests.Session, path: str, params: dict) -> list[dict]:
    """Follow Asana's offset pagination until the list is complete."""
    out: list[dict] = []
    params = dict(params, limit=PAGE_SIZE)
    while True:
        body = _get(session, path, params)
        out.extend(body.get("data") or [])
        next_page = body.get("next_page") or {}
        offset = next_page.get("offset")
        if not offset:
            return out
        params["offset"] = offset


def _project_name(task: dict) -> str:
    for p in task.get("projects") or []:
        if p.get("name"):
            return p["name"]
    for m in task.get("memberships") or []:
        name = (m.get("project") or {}).get("name")
        if name:
            return name
    return ""


def _task_entry(task: dict) -> dict:
    project = _project_name(task)
    due = task.get("due_on")
    if not due and task.get("due_at"):
        try:
            due = dateparser.isoparse(task["due_at"]).astimezone(
                config.tzinfo()).date().isoformat()
        except (ValueError, TypeError):
            due = None
    return {
        "id": task.get("gid", ""),
        "title": (task.get("name") or "(untitled)").strip(),
        "project": project,
        "business": config.business_from_text(project),
        "due": due,
        "link": task.get("permalink_url") or "",
    }


def _due_date(task: dict) -> date | None:
    if task.get("due_on"):
        try:
            return date.fromisoformat(task["due_on"])
        except ValueError:
            return None
    if task.get("due_at"):
        try:
            return dateparser.isoparse(task["due_at"]).astimezone(config.tzinfo()).date()
        except (ValueError, TypeError):
            return None
    return None


def collect() -> dict:
    """Return {"source": "asana", "due_today": [...], "overdue": [...], "completed_yesterday": [...]}."""
    token = config.env("ASANA_TOKEN")
    if not token:
        raise config.NotConfigured("ASANA_TOKEN is empty in .env")

    session = _session(token)
    today = config.today_local()
    yesterday = today - timedelta(days=1)
    yesterday_start = config.start_of_day(yesterday)

    me = _get(session, "/users/me", {"opt_fields": "gid,workspaces.gid,workspaces.name"})["data"]
    workspaces = me.get("workspaces") or []

    due_today: list[dict] = []
    overdue: list[dict] = []
    completed_yesterday: list[dict] = []
    seen: set[str] = set()

    for ws in workspaces:
        base = {"assignee": "me", "workspace": ws["gid"], "opt_fields": TASK_FIELDS}

        # Open tasks: completed_since=now returns only incomplete ones.
        for task in _paged(session, "/tasks", dict(base, completed_since="now")):
            if task.get("gid") in seen or task.get("completed"):
                continue
            seen.add(task["gid"])
            due = _due_date(task)
            if due is None:
                continue
            if due == today:
                due_today.append(_task_entry(task))
            elif due < today:
                overdue.append(_task_entry(task))

        # Tasks completed since yesterday 00:00 local, then keep only yesterday's.
        for task in _paged(session, "/tasks",
                           dict(base, completed_since=yesterday_start.isoformat())):
            if not task.get("completed") or not task.get("completed_at"):
                continue
            try:
                done = dateparser.isoparse(task["completed_at"]).astimezone(config.tzinfo())
            except (ValueError, TypeError):
                continue
            if done.date() == yesterday and task["gid"] not in seen:
                seen.add(task["gid"])
                completed_yesterday.append(_task_entry(task))

    overdue.sort(key=lambda t: t["due"] or "")
    due_today.sort(key=lambda t: t["title"].lower())
    completed_yesterday.sort(key=lambda t: t["title"].lower())
    return {
        "source": "asana",
        "due_today": due_today,
        "overdue": overdue,
        "completed_yesterday": completed_yesterday,
    }
