"""Overview metrics aggregation (Console, Merchant lens) — PURE, no DB.

Every number on the Overview and Gate Log surfaces is computed here from in-memory
rows, so it is unit-testable with no database. The frontend charts bind to these
aggregates — real data, never decorative mocks.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SessionRow:
    session_id: str
    status: str                    # OPEN|CONVERTED|ABANDONED|DENIED
    lever: str                     # NONE|RETURN_EXTENSION|... |"" if no offer
    total_paise: int               # converted amount (0 if not converted)
    outcome_reason: str = ""
    margin_bps: int = 0


@dataclass(frozen=True)
class DenyRow:
    reason_code: str
    check_number: int


@dataclass(frozen=True)
class OverviewMetrics:
    sessions: int
    converted: int
    abandoned: int
    denied: int
    recovered_sales: int           # converted where a lever moved it in (lever != NONE)
    recovered_revenue_paise: int
    gross_converted_paise: int
    conversion_rate_bps: int       # converted / sessions
    baseline_converted: int        # would-convert with engine OFF (lever == NONE among converted)
    engine_delta: int              # extra conversions attributable to the engine
    lever_mix: dict = field(default_factory=dict)
    deny_reasons: dict = field(default_factory=dict)


def _bps(num: int, den: int) -> int:
    return (num * 10000 // den) if den else 0


def aggregate_overview(sessions: list[SessionRow], denies: list[DenyRow]) -> OverviewMetrics:
    n = len(sessions)
    converted = [s for s in sessions if s.status == "CONVERTED"]
    abandoned = [s for s in sessions if s.status == "ABANDONED"]
    denied = [s for s in sessions if s.status == "DENIED"]
    recovered = [s for s in converted if s.lever not in ("NONE", "")]

    lever_mix: dict = {}
    for s in converted:
        key = s.lever or "NONE"
        lever_mix[key] = lever_mix.get(key, 0) + 1

    deny_reasons: dict = {}
    for d in denies:
        deny_reasons[d.reason_code] = deny_reasons.get(d.reason_code, 0) + 1

    baseline_converted = len([s for s in converted if s.lever in ("NONE", "")])
    # Conversion rate is over TERMINAL sessions only — OPEN sessions haven't
    # resolved yet, so counting them in the denominator understates the rate
    # (deep-review). engine_delta is the recovered count: sales that carried an
    # engine lever, i.e. the ones a passive/baseline merchant would have lost.
    terminal = len(converted) + len(abandoned) + len(denied)
    return OverviewMetrics(
        sessions=n,
        converted=len(converted),
        abandoned=len(abandoned),
        denied=len(denied),
        recovered_sales=len(recovered),
        recovered_revenue_paise=sum(s.total_paise for s in recovered),
        gross_converted_paise=sum(s.total_paise for s in converted),
        conversion_rate_bps=_bps(len(converted), terminal),
        baseline_converted=baseline_converted,
        engine_delta=len(recovered),
        lever_mix=lever_mix,
        deny_reasons=deny_reasons,
    )


def funnel(sessions: list[SessionRow], offered: int) -> dict:
    """Sessions → offered → converted → recovered. The merchant's conversion story."""
    conv = [s for s in sessions if s.status == "CONVERTED"]
    recovered = [s for s in conv if s.lever not in ("NONE", "")]
    return {
        "sessions": len(sessions),
        "offered": offered,
        "converted": len(conv),
        "recovered": len(recovered),
    }


def lever_effectiveness(sessions: list[SessionRow]) -> list[dict]:
    """Per lever: how many sales it closed, the revenue, and average margin.
    Ordered by revenue so the most valuable lever leads."""
    agg: dict[str, dict] = {}
    for s in sessions:
        if s.status != "CONVERTED":
            continue
        key = s.lever or "NONE"
        a = agg.setdefault(key, {"lever": key, "count": 0, "revenue_paise": 0, "_m": 0})
        a["count"] += 1
        a["revenue_paise"] += s.total_paise
        a["_m"] += s.margin_bps
    out = []
    for a in agg.values():
        a["avg_margin_bps"] = a["_m"] // a["count"] if a["count"] else 0
        a.pop("_m")
        out.append(a)
    out.sort(key=lambda x: x["revenue_paise"], reverse=True)
    return out


def margin_histogram(sessions: list[SessionRow], bucket_bps: int = 500) -> list[dict]:
    """Distribution of realized margins on converted sales (bucketed)."""
    buckets: dict[int, int] = {}
    for s in sessions:
        if s.status == "CONVERTED" and s.margin_bps is not None:
            b = (s.margin_bps // bucket_bps) * bucket_bps
            buckets[b] = buckets.get(b, 0) + 1
    return [{"floor_bps": b, "count": c} for b, c in sorted(buckets.items())]
