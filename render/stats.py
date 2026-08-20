"""
Live GitHub statistics, and the map from statistics to scene parameters.

The mapping is published in the README next to the picture, so it has to be
deterministic, bounded, and honest: every input is squashed through tanh so
that the geometry stays inside a range that renders well no matter how the
numbers grow.
"""

from __future__ import annotations

import json
import math
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict

GRAPHQL = "https://api.github.com/graphql"


def _ssl_context() -> ssl.SSLContext | None:
    """Some python.org macOS installs ship without a usable CA bundle, which
    makes every https call fail locally while working fine on CI. Prefer
    certifi's bundle when it is importable; never fall back to unverified."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return None

QUERY = """
query($login: String!) {
  user(login: $login) {
    login
    name
    followers { totalCount }
    following { totalCount }
    repositories(first: 100, ownerAffiliations: OWNER, isFork: false,
                 orderBy: {field: STARGAZERS, direction: DESC}) {
      totalCount
      nodes { name stargazerCount primaryLanguage { name } }
    }
    contributionsCollection {
      totalCommitContributions
      restrictedContributionsCount
      totalPullRequestContributions
      contributionCalendar {
        totalContributions
        weeks { contributionDays { contributionCount date } }
      }
    }
  }
}
"""


REST = "https://api.github.com"


@dataclass
class Stats:
    login: str = "octocat"
    name: str = ""
    repos: int = 0
    stars: int = 0
    followers: int = 0
    following: int = 0
    commits: int = 0
    prs: int = 0
    contributions: int = 0
    top_language: str = "—"
    calendar: list = field(default_factory=list)   # daily counts, oldest first
    live: bool = False          # False => hand-picked fallback geometry
    has_contributions: bool = False   # commit/PR/calendar figures available

    def to_dict(self):
        return asdict(self)


def _post(token: str, login: str) -> dict:
    body = json.dumps({"query": QUERY, "variables": {"login": login}}).encode()
    req = urllib.request.Request(
        GRAPHQL, data=body,
        headers={
            "Authorization": f"bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "gyroid-readme",
        },
    )
    with urllib.request.urlopen(req, timeout=25, context=_ssl_context()) as r:
        return json.loads(r.read().decode())


def _get(url: str) -> dict | list:
    req = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json",
                      "User-Agent": "gyroid-readme"})
    with urllib.request.urlopen(req, timeout=25, context=_ssl_context()) as r:
        return json.loads(r.read().decode())


def fetch_public(login: str) -> Stats:
    """Anonymous REST. Enough for repositories, stars and followers, which is
    enough to drive the geometry. Contribution counts need an authenticated
    GraphQL call, so they stay unknown here and the card prints them as such
    rather than as zero."""
    try:
        user = _get(f"{REST}/users/{login}")
        if user.get("message"):
            print(f"[stats] rest: {user['message']}")
            return Stats(login=login)
        repos = _get(f"{REST}/users/{login}/repos"
                     "?per_page=100&type=owner&sort=pushed")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[stats] rest failed ({exc})")
        return Stats(login=login)

    owned = [r for r in repos if not r.get("fork")]
    langs: dict[str, int] = {}
    for r in owned:
        if r.get("language"):
            langs[r["language"]] = langs.get(r["language"], 0) + 1

    print(f"[stats] anonymous REST: {len(owned)} owned repos "
          "(no token -> no contribution counts)")
    return Stats(
        login=user["login"],
        name=user.get("name") or user["login"],
        repos=user.get("public_repos", 0),
        stars=sum(r.get("stargazers_count", 0) for r in owned),
        followers=user.get("followers", 0),
        following=user.get("following", 0),
        top_language=max(langs, key=langs.get) if langs else "—",
        live=True,
        has_contributions=False,
    )


def fetch(login: str, token: str | None = None) -> Stats:
    """Pull live numbers. On any failure returns a Stats with live=False so
    the build still produces a page rather than failing the workflow."""
    token = token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        return fetch_public(login)

    try:
        payload = _post(token, login)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[stats] graphql failed ({exc}) -- falling back to REST")
        return fetch_public(login)

    if payload.get("errors") or not payload.get("data", {}).get("user"):
        print(f"[stats] graphql returned no user: {payload.get('errors')}")
        return fetch_public(login)

    u = payload["data"]["user"]
    repos = u["repositories"]["nodes"]
    cc = u["contributionsCollection"]
    cal = [d["contributionCount"]
           for w in cc["contributionCalendar"]["weeks"]
           for d in w["contributionDays"]]

    langs: dict[str, int] = {}
    for r in repos:
        lang = (r.get("primaryLanguage") or {}).get("name")
        if lang:
            langs[lang] = langs.get(lang, 0) + 1

    return Stats(
        login=u["login"],
        name=u.get("name") or u["login"],
        repos=u["repositories"]["totalCount"],
        stars=sum(r["stargazerCount"] for r in repos),
        followers=u["followers"]["totalCount"],
        following=u["following"]["totalCount"],
        commits=cc["totalCommitContributions"] + cc["restrictedContributionsCount"],
        prs=cc["totalPullRequestContributions"],
        contributions=cc["contributionCalendar"]["totalContributions"],
        top_language=max(langs, key=langs.get) if langs else "—",
        calendar=cal,
        live=True,
        has_contributions=True,
    )


# --------------------------------------------------------------------------
# statistics -> geometry
# --------------------------------------------------------------------------

PHI = (1.0 + 5.0 ** 0.5) / 2.0

# pinned, not mapped -- see scene_params()
THICKNESS = 0.32


def scene_params(s: Stats) -> dict:
    """Bounded, deterministic map from account numbers to the gyroid.

        f     = 2.6 + 1.9 * tanh(repos / 24)      cell frequency
        alpha = 1.15 * tanh(stars / 40)           lattice tilt in the ball
        delta = frac(commits * phi)               palette rotation

    Two properties matter more than the exact constants. Every input is
    squashed through tanh, so the parameters are bounded and monotone -- the
    picture keeps responding to growth without ever leaving the range that
    renders well. And every value in each range has to look good on its own,
    because an account does not get to choose where it lands.

    Shell thickness is deliberately NOT a parameter. The level sets |g| = tau
    of the gyroid are topologically stable across almost the whole usable
    range, so from outside the clipping ball a thin shell and a thick one are
    very nearly the same picture. It reads like a knob and controls nothing,
    so it is pinned to a constant instead of being wired to a statistic.
    """
    if not s.live:
        return dict(freq=3.6, thickness=THICKNESS, radius=1.28,
                    palette_shift=0.21, tilt=0.45)
    return dict(
        freq=2.6 + 1.9 * math.tanh(s.repos / 24.0),
        thickness=THICKNESS,
        radius=1.28,
        palette_shift=math.fmod(s.commits * PHI, 1.0),
        tilt=1.15 * math.tanh(s.stars / 40.0),
    )


if __name__ == "__main__":
    import sys
    st = fetch(sys.argv[1] if len(sys.argv) > 1 else "octocat")
    print(json.dumps(st.to_dict() | {"scene": scene_params(st)},
                     indent=2, default=str)[:900])
