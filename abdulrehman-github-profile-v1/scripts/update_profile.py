#!/usr/bin/env python3
"""
Generate verified GitHub statistics and public activity SVGs.

Uses:
- GitHub REST API for profile, repositories and events
- GitHub GraphQL API for the one-year contribution calendar when GITHUB_TOKEN exists

No third-party statistics service is required.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
PROFILE = json.loads((ROOT / "data" / "profile.json").read_text(encoding="utf-8"))
USERNAME = PROFILE["username"]
TOKEN = os.environ.get("GITHUB_TOKEN", "")

C = {
    "bg": "#070A12",
    "panel": "#0D1320",
    "panel2": "#101827",
    "border": "#29344A",
    "text": "#F5F7FB",
    "muted": "#9AA6BC",
    "violet": "#9B7BFF",
    "blue": "#5FA8FF",
    "gold": "#D9B76E",
    "green": "#75E6A4",
    "cyan": "#75D7F0",
}

def request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> dict | list:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{USERNAME}-profile-readme",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))

def get_all_repos() -> list[dict]:
    repos: list[dict] = []
    for page in range(1, 11):
        batch = request_json(
            f"https://api.github.com/users/{USERNAME}/repos"
            f"?per_page=100&page={page}&sort=updated&type=owner"
        )
        if not isinstance(batch, list):
            break
        repos.extend(batch)
        if len(batch) < 100:
            break
    return repos

def get_contributions() -> tuple[int | None, list[dict]]:
    if not TOKEN:
        return None, []
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(days=364)
    query = """
    query($login: String!, $from: DateTime!, $to: DateTime!) {
      user(login: $login) {
        contributionsCollection(from: $from, to: $to) {
          contributionCalendar {
            totalContributions
            weeks {
              contributionDays {
                date
                contributionCount
                contributionLevel
              }
            }
          }
        }
      }
    }
    """
    payload = {
        "query": query,
        "variables": {
            "login": USERNAME,
            "from": start.isoformat(),
            "to": now.isoformat(),
        },
    }
    result = request_json("https://api.github.com/graphql", method="POST", payload=payload)
    collection = result["data"]["user"]["contributionsCollection"]["contributionCalendar"]
    days = [d for week in collection["weeks"] for d in week["contributionDays"]]
    return int(collection["totalContributions"]), days

def fmt(value: int) -> str:
    if value >= 1_000_000:
        return f"{value/1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value/1_000:.1f}K"
    return str(value)

def svg_text(x, y, value, size=18, fill=None, weight=400, family="Inter,Segoe UI,Arial,sans-serif", anchor="start"):
    return (
        f'<text x="{x}" y="{y}" fill="{fill or C["text"]}" '
        f'font-family="{family}" font-size="{size}" font-weight="{weight}" '
        f'text-anchor="{anchor}">{escape(str(value))}</text>\n'
    )

def svg_base(width: int, height: int) -> str:
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<defs>
  <linearGradient id="panel" x1="0" y1="0" x2="1" y2="1">
    <stop offset="0%" stop-color="{C['panel']}"/>
    <stop offset="100%" stop-color="{C['panel2']}"/>
  </linearGradient>
</defs>
"""

def generate_stats(profile: dict, repos: list[dict], total_contributions: int | None, days: list[dict]) -> str:
    stars = sum(int(r.get("stargazers_count", 0)) for r in repos if not r.get("fork"))
    forks = sum(int(r.get("forks_count", 0)) for r in repos if not r.get("fork"))
    public_repos = int(profile.get("public_repos", len(repos)))
    followers = int(profile.get("followers", 0))
    latest = repos[0]["name"] if repos else "No public repositories"
    updated = repos[0]["updated_at"][:10] if repos else "—"
    contributions_text = fmt(total_contributions) if total_contributions is not None else "N/A"

    out = svg_base(1200, 405)
    out += f'<rect x="1" y="1" width="1198" height="403" rx="18" fill="url(#panel)" stroke="{C["border"]}" stroke-width="2"/>\n'
    out += svg_text(34, 51, "LIVE GITHUB INTELLIGENCE", 17, C["violet"], 700, "Consolas,monospace")
    out += svg_text(34, 80, f"Verified public data for @{USERNAME}", 15, C["muted"])

    metrics = [
        ("PUBLIC REPOS", fmt(public_repos)),
        ("TOTAL STARS", fmt(stars)),
        ("FOLLOWERS", fmt(followers)),
        ("YEAR CONTRIBUTIONS", contributions_text),
    ]
    for idx, (label, value) in enumerate(metrics):
        x = 34 + idx * 282
        out += f'<rect x="{x}" y="108" width="260" height="103" rx="13" fill="{C["bg"]}" stroke="{C["border"]}"/>\n'
        out += svg_text(x+18, 141, label, 12, C["muted"], 700, "Consolas,monospace")
        out += svg_text(x+18, 187, value, 31, C["text"], 700)

    out += svg_text(34, 246, "CONTRIBUTION SIGNAL", 12, C["gold"], 700, "Consolas,monospace")
    if days:
        levels = {
            "NONE": (C["border"], 0.55),
            "FIRST_QUARTILE": (C["violet"], 0.32),
            "SECOND_QUARTILE": (C["violet"], 0.52),
            "THIRD_QUARTILE": (C["blue"], 0.72),
            "FOURTH_QUARTILE": (C["cyan"], 0.95),
        }
        # Pad to full weeks and draw from oldest to newest.
        for idx, day in enumerate(days[-371:]):
            week = idx // 7
            weekday = idx % 7
            x = 34 + week * 20
            y = 263 + weekday * 14
            color, opacity = levels.get(day.get("contributionLevel", "NONE"), (C["border"], 0.55))
            out += f'<rect x="{x}" y="{y}" width="10" height="10" rx="2" fill="{color}" fill-opacity="{opacity}"/>\n'
    else:
        for week in range(53):
            for weekday in range(7):
                x = 34 + week * 20
                y = 263 + weekday * 14
                out += f'<rect x="{x}" y="{y}" width="10" height="10" rx="2" fill="{C["border"]}" fill-opacity=".48"/>\n'

    out += svg_text(34, 382, f"LATEST UPDATED REPOSITORY  {latest}", 12, C["text"], 600, "Consolas,monospace")
    out += svg_text(1166, 382, f"{updated}  •  {fmt(forks)} TOTAL FORKS", 12, C["muted"], 500, "Consolas,monospace", "end")
    out += "</svg>"
    return out

def describe_event(event: dict) -> tuple[str, str]:
    event_type = event.get("type", "Event")
    repo = event.get("repo", {}).get("name", "").split("/")[-1] or "repository"
    payload = event.get("payload", {})
    if event_type == "PushEvent":
        count = len(payload.get("commits", []))
        return f"Pushed {count} commit{'s' if count != 1 else ''}", repo
    if event_type == "CreateEvent":
        ref_type = payload.get("ref_type", "resource")
        return f"Created {ref_type}", repo
    if event_type == "PullRequestEvent":
        action = payload.get("action", "updated")
        number = payload.get("number", "")
        return f"{action.title()} pull request #{number}".strip(), repo
    if event_type == "IssuesEvent":
        action = payload.get("action", "updated")
        issue = payload.get("issue", {}).get("number", "")
        return f"{action.title()} issue #{issue}".strip(), repo
    if event_type == "WatchEvent":
        return "Starred repository", repo
    if event_type == "ForkEvent":
        return "Forked repository", repo
    if event_type == "IssueCommentEvent":
        return "Commented on issue", repo
    return event_type.replace("Event", ""), repo

def relative_time(timestamp: str) -> str:
    try:
        when = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        delta = dt.datetime.now(dt.timezone.utc) - when
        if delta.days >= 1:
            return f"{delta.days}d ago"
        hours = max(0, int(delta.total_seconds() // 3600))
        if hours:
            return f"{hours}h ago"
        minutes = max(1, int(delta.total_seconds() // 60))
        return f"{minutes}m ago"
    except Exception:
        return ""

def generate_activity(events: list[dict]) -> str:
    selected = []
    for event in events:
        text, repo = describe_event(event)
        selected.append((text, repo, relative_time(event.get("created_at", ""))))
        if len(selected) == 4:
            break

    out = svg_base(1200, 275)
    out += f'<rect x="1" y="1" width="1198" height="273" rx="18" fill="url(#panel)" stroke="{C["border"]}" stroke-width="2"/>\n'
    out += svg_text(34, 50, "RECENT PUBLIC ACTIVITY", 17, C["blue"], 700, "Consolas,monospace")
    out += svg_text(34, 80, "Latest visible events from the GitHub public activity feed.", 15, C["muted"])

    if not selected:
        out += svg_text(34, 135, "No recent public activity was returned by the API.", 16, C["text"])
    else:
        y = 122
        accent_cycle = [C["green"], C["violet"], C["blue"], C["gold"]]
        for idx, (action, repo, ago) in enumerate(selected):
            accent = accent_cycle[idx]
            out += f'<circle cx="47" cy="{y-4}" r="8" fill="{accent}" fill-opacity=".18" stroke="{accent}"/>\n'
            out += svg_text(70, y, action, 15, C["text"], 600)
            out += svg_text(445, y, repo, 14, C["muted"], 500, "Consolas,monospace")
            out += svg_text(1158, y, ago, 13, C["muted"], 500, "Consolas,monospace", "end")
            if idx < len(selected)-1:
                out += f'<line x1="70" y1="{y+17}" x2="1158" y2="{y+17}" stroke="{C["border"]}"/>\n'
            y += 39
    out += "</svg>"
    return out

def main() -> int:
    try:
        profile = request_json(f"https://api.github.com/users/{USERNAME}")
        repos = get_all_repos()
        events = request_json(f"https://api.github.com/users/{USERNAME}/events/public?per_page=100")
        try:
            total_contributions, days = get_contributions()
        except Exception as exc:
            print(f"Contribution GraphQL query unavailable: {exc}", file=sys.stderr)
            total_contributions, days = None, []

        (ASSETS / "live-stats.svg").write_text(
            generate_stats(profile, repos, total_contributions, days),
            encoding="utf-8",
        )
        (ASSETS / "activity.svg").write_text(
            generate_activity(events if isinstance(events, list) else []),
            encoding="utf-8",
        )
        print("Live profile assets updated successfully.")
        return 0
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, ValueError) as exc:
        print(f"Profile update failed without overwriting existing assets: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
