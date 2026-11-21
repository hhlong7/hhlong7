#!/usr/bin/env python3
"""Fetch the last year of GitHub activity and write data/contributions.json.

Writes, per day, both `count` (all contributions, as on your profile) and
`commits` (commits only, which is what the "Every day" graph shows), plus the
language breakdown of your own non-fork repos.

Env:
  GH_TOKEN   token for the GraphQL API (GITHUB_TOKEN works for public activity;
             a PAT with read:user + repo also counts private work)
  USERNAME   GitHub login (default: hhlong7)
"""
import datetime as dt
import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = os.environ.get("USERNAME", "hhlong7")
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")

CALENDAR = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalCommitContributions
      totalIssueContributions
      totalPullRequestContributions
      totalPullRequestReviewContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { date contributionCount } }
      }
    }
  }
}
"""

# One node per repo per active day, so a ~91 day window never needs more than
# 100 nodes per repo.
COMMITS = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      commitContributionsByRepository(maxRepositories: 100) {
        contributions(first: 100) {
          pageInfo { hasNextPage }
          nodes { occurredAt commitCount }
        }
      }
    }
  }
}
"""

LANGUAGES = """
query($login: String!) {
  user(login: $login) {
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false) {
      nodes {
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges { size node { name } }
        }
      }
    }
  }
}
"""


def gql(query, variables):
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode(),
        headers={"Authorization": f"bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    if payload.get("errors") or not payload.get("data", {}).get("user"):
        print(json.dumps(payload.get("errors", payload), indent=2), file=sys.stderr)
        raise SystemExit(1)
    return payload["data"]["user"]


def commits_by_day(user_payloads):
    """Sum commitCount per date over several windows' commitContributionsByRepository."""
    out = Counter()
    for u in user_payloads:
        for repo in u["contributionsCollection"]["commitContributionsByRepository"]:
            conn = repo["contributions"]
            if conn["pageInfo"]["hasNextPage"]:
                print("WARNING: a repo had more than 100 active days in one window", file=sys.stderr)
            for n in conn["nodes"]:
                out[n["occurredAt"][:10]] += n["commitCount"]
    return out


def language_totals(repo_nodes):
    out = Counter()
    for repo in repo_nodes:
        for e in repo["languages"]["edges"]:
            out[e["node"]["name"]] += e["size"]
    return [{"name": k, "bytes": v} for k, v in out.most_common(12)]


def main() -> int:
    if not TOKEN:
        print("GH_TOKEN is not set", file=sys.stderr)
        return 1
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    start = now - dt.timedelta(days=364)

    cc = gql(CALENDAR, {"login": USER, "from": start.isoformat(), "to": now.isoformat()})["contributionsCollection"]

    # four windows of ~91 days for the per-day commit counts
    edges = [start + (now - start) * i / 4 for i in range(5)]
    payloads = [gql(COMMITS, {"login": USER, "from": a.replace(microsecond=0).isoformat(),
                              "to": b.replace(microsecond=0).isoformat()})
                for a, b in zip(edges, edges[1:])]
    commits = commits_by_day(payloads)

    langs = language_totals(gql(LANGUAGES, {"login": USER})["repositories"]["nodes"])

    days = [
        {"date": d["date"], "count": d["contributionCount"], "commits": commits.get(d["date"], 0)}
        for w in cc["contributionCalendar"]["weeks"]
        for d in w["contributionDays"]
        if d["date"] <= now.date().isoformat()
    ]
    out = {
        "user": USER,
        "sample": False,
        "generated_at": now.isoformat(),
        "total": cc["contributionCalendar"]["totalContributions"],
        "breakdown": {
            "commits": cc["totalCommitContributions"],
            "pullRequests": cc["totalPullRequestContributions"],
            "issues": cc["totalIssueContributions"],
            "codeReview": cc["totalPullRequestReviewContributions"],
        },
        "languages": langs,
        "days": days,
    }
    path = ROOT / "data" / "contributions.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(out, indent=1) + "\n")
    print(f"wrote {path} ({len(days)} days, {out['total']} contributions, "
          f"{sum(commits.values())} commits, {len(langs)} languages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
