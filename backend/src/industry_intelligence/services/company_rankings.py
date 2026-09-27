"""Read-only views of external company rankings and their history.

Everything is computed from persisted snapshots: movement compares a snapshot with the one
immediately before it in the same ranking (by ``effective_at``). Rankings are never combined
with each other or with Trending/Search scores.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from sqlalchemy import func, select

from industry_intelligence.adapters.company_ranking.registry import COMPANY_RANKINGS
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    Ranking,
    RankingEntry,
    RankingSnapshot,
)
from industry_intelligence.services.company_resolution import resolve_company_redirect

DOMAIN_ORDER = {"computer": 0, "finance": 1, "gaming": 2}
REGISTRY_ORDER = {key: index for index, key in enumerate(COMPANY_RANKINGS)}
NEWEST = (RankingSnapshot.effective_at.desc(), RankingSnapshot.observed_at.desc())


def _iso(value):
    return value.isoformat() if value else None


def _number(value):
    return float(value) if value is not None else None


def movement(previous_rank, rank, has_previous_snapshot):
    """Return (rank_change, movement); rank_change = previous_rank - current_rank."""

    if not has_previous_snapshot:
        return None, None
    if previous_rank is None:
        return None, "new"
    change = previous_rank - rank
    return change, "up" if change > 0 else "down" if change < 0 else "unchanged"


def _snapshot_summary(snapshot):
    if snapshot is None:
        return None
    return {
        "id": str(snapshot.id),
        "snapshot_key": snapshot.snapshot_key,
        "observed_at": _iso(snapshot.observed_at),
        "published_at": _iso(snapshot.published_at),
        "effective_at": _iso(snapshot.effective_at),
        "source_url": snapshot.source_url,
        "status": snapshot.status,
        "entry_count": snapshot.entry_count,
        "source_entry_count": snapshot.source_entry_count,
        "metadata": snapshot.snapshot_metadata,
    }


def _definition_fields(definition):
    return {
        "key": definition.key,
        "name": definition.name,
        "publisher": definition.publisher,
        "source_key": definition.source_key,
        "domain": str(definition.domain),
        "methodology": definition.methodology,
        "metric_type": definition.metric_type,
        "metric_label": definition.metric_label,
        "metric_unit": definition.metric_unit,
        "frequency": definition.frequency,
        "acquisition": definition.acquisition,
        "primary_language": definition.primary_language,
        "source_url": definition.url,
    }


def _ranking_fields(ranking):
    return {
        "id": str(ranking.id),
        "key": ranking.key,
        "name": ranking.name,
        "publisher": ranking.publisher,
        "source_key": ranking.source_key,
        "domain": str(ranking.domain),
        "methodology": ranking.methodology,
        "metric_type": ranking.metric_type,
        "metric_label": ranking.metric_label,
        "metric_unit": ranking.metric_unit,
        "frequency": ranking.frequency,
        "acquisition": ranking.acquisition,
        "primary_language": ranking.primary_language,
        "source_url": ranking.source_url,
    }


def _latest_snapshots(session, ranking_ids):
    if not ranking_ids:
        return {}
    ordered = (
        select(
            RankingSnapshot.id,
            func.row_number()
            .over(partition_by=RankingSnapshot.ranking_id, order_by=NEWEST)
            .label("position"),
        )
        .where(RankingSnapshot.ranking_id.in_(ranking_ids))
        .subquery()
    )
    rows = session.scalars(
        select(RankingSnapshot)
        .join(ordered, ordered.c.id == RankingSnapshot.id)
        .where(ordered.c.position == 1)
    )
    return {row.ranking_id: row for row in rows}


def list_rankings(session, domain=None, source=None, limit=50, offset=0):
    """Persisted rankings with their latest snapshot, plus registered-but-unobserved ones."""

    query = select(Ranking)
    if domain:
        query = query.where(Ranking.domain == domain)
    if source:
        query = query.where(Ranking.source_key == source)
    rankings = list(session.scalars(query))
    latest = _latest_snapshots(session, [ranking.id for ranking in rankings])
    counts = dict(
        session.execute(
            select(RankingSnapshot.ranking_id, func.count())
            .where(RankingSnapshot.ranking_id.in_([ranking.id for ranking in rankings]))
            .group_by(RankingSnapshot.ranking_id)
        ).all()
    )
    items = [
        {
            **_ranking_fields(ranking),
            "status": "ready" if ranking.id in latest else "no_snapshot",
            "snapshot_count": counts.get(ranking.id, 0),
            "latest_snapshot": _snapshot_summary(latest.get(ranking.id)),
        }
        for ranking in rankings
    ]
    persisted = {ranking.key for ranking in rankings}
    for definition in COMPANY_RANKINGS.values():
        if definition.key in persisted:
            continue
        if (domain and str(definition.domain) != domain) or (
            source and definition.source_key != source
        ):
            continue
        items.append(
            {
                "id": None,
                **_definition_fields(definition),
                "status": "no_snapshot",
                "snapshot_count": 0,
                "latest_snapshot": None,
            }
        )
    items.sort(
        key=lambda item: (
            DOMAIN_ORDER.get(item["domain"], 9),
            REGISTRY_ORDER.get(item["key"], len(REGISTRY_ORDER)),
            item["key"],
        )
    )
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "limit": limit,
        "offset": offset,
    }


def find_ranking(session, ref):
    """Look a ranking up by UUID or stable key."""

    try:
        return session.get(Ranking, UUID(ref))
    except ValueError:
        return session.scalar(select(Ranking).where(Ranking.key == ref))


def _previous_snapshot(session, snapshot):
    return session.scalar(
        select(RankingSnapshot)
        .where(
            RankingSnapshot.ranking_id == snapshot.ranking_id,
            RankingSnapshot.id != snapshot.id,
            (RankingSnapshot.effective_at < snapshot.effective_at)
            | (
                (RankingSnapshot.effective_at == snapshot.effective_at)
                & (RankingSnapshot.observed_at < snapshot.observed_at)
            ),
        )
        .order_by(*NEWEST)
        .limit(1)
    )


def _ranks(session, snapshot_id, company_ids=None):
    query = select(RankingEntry.company_id, RankingEntry.rank).where(
        RankingEntry.snapshot_id == snapshot_id
    )
    if company_ids is not None:
        query = query.where(RankingEntry.company_id.in_(company_ids))
    return dict(session.execute(query).all())


def ranking_detail(session, ref, snapshot_id=None, limit=50, offset=0):
    """Return a ranking with one snapshot's entries and movement; None when unknown."""

    ranking = find_ranking(session, ref)
    if ranking is None:
        definition = COMPANY_RANKINGS.get(ref)
        if definition is None or snapshot_id is not None:
            return None
        return {
            "ranking": {"id": None, **_definition_fields(definition)},
            "status": "no_snapshot",
            "snapshot": None,
            "previous_snapshot": None,
            "entries": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "dropped": [],
            "dropped_count": 0,
            "snapshot_count": 0,
        }
    snapshot_count = session.scalar(
        select(func.count()).where(RankingSnapshot.ranking_id == ranking.id)
    )
    if snapshot_id is not None:
        snapshot = session.get(RankingSnapshot, snapshot_id)
        if snapshot is None or snapshot.ranking_id != ranking.id:
            return None
    else:
        snapshot = session.scalar(
            select(RankingSnapshot)
            .where(RankingSnapshot.ranking_id == ranking.id)
            .order_by(*NEWEST)
            .limit(1)
        )
    base = {"ranking": _ranking_fields(ranking), "snapshot_count": snapshot_count}
    if snapshot is None:
        return {
            **base,
            "status": "no_snapshot",
            "snapshot": None,
            "previous_snapshot": None,
            "entries": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "dropped": [],
            "dropped_count": 0,
        }
    previous = _previous_snapshot(session, snapshot)
    rows = session.execute(
        select(RankingEntry, Company.canonical_name)
        .join(Company, Company.id == RankingEntry.company_id)
        .where(RankingEntry.snapshot_id == snapshot.id)
        .order_by(RankingEntry.rank, RankingEntry.raw_name)
        .offset(offset)
        .limit(limit)
    ).all()
    page_ids = [entry.company_id for entry, _ in rows]
    previous_ranks = _ranks(session, previous.id, page_ids) if previous else {}
    entries = []
    for entry, canonical_name in rows:
        previous_rank = previous_ranks.get(entry.company_id)
        change, move = movement(previous_rank, entry.rank, previous is not None)
        entries.append(
            {
                "company_id": str(entry.company_id),
                "canonical_name": canonical_name,
                "display_name": entry.raw_name,
                "rank": entry.rank,
                "previous_rank": previous_rank,
                "rank_change": change,
                "movement": move,
                "is_new": move == "new",
                "raw_value": entry.raw_value,
                "metric_value": _number(entry.metric_value),
                "metadata": entry.entry_metadata,
            }
        )
    dropped, dropped_count = [], 0
    if previous:
        current_ids = select(RankingEntry.company_id).where(RankingEntry.snapshot_id == snapshot.id)
        dropped_query = (
            select(RankingEntry)
            .where(
                RankingEntry.snapshot_id == previous.id,
                RankingEntry.company_id.not_in(current_ids),
            )
            .order_by(RankingEntry.rank)
        )
        dropped_count = session.scalar(select(func.count()).select_from(dropped_query.subquery()))
        dropped = [
            {
                "company_id": str(entry.company_id),
                "display_name": entry.raw_name,
                "previous_rank": entry.rank,
            }
            for entry in session.scalars(dropped_query.limit(20))
        ]
    return {
        **base,
        "status": "ready",
        "snapshot": _snapshot_summary(snapshot),
        "previous_snapshot": _snapshot_summary(previous),
        "entries": entries,
        "total": snapshot.entry_count,
        "limit": limit,
        "offset": offset,
        "dropped": dropped,
        "dropped_count": dropped_count,
    }


def ranking_history(session, ref, limit=20, offset=0):
    """Snapshot timeline, newest first, with the leader of each snapshot."""

    ranking = find_ranking(session, ref)
    if ranking is None:
        if ref in COMPANY_RANKINGS:
            definition = COMPANY_RANKINGS[ref]
            return {
                "ranking": {"id": None, **_definition_fields(definition)},
                "items": [],
                "total": 0,
                "limit": limit,
                "offset": offset,
            }
        return None
    total = session.scalar(select(func.count()).where(RankingSnapshot.ranking_id == ranking.id))
    snapshots = list(
        session.scalars(
            select(RankingSnapshot)
            .where(RankingSnapshot.ranking_id == ranking.id)
            .order_by(*NEWEST)
            .offset(offset)
            .limit(limit)
        )
    )
    leaders = {}
    if snapshots:
        leader_rows = session.execute(
            select(RankingEntry.snapshot_id, RankingEntry.raw_name, RankingEntry.company_id)
            .where(RankingEntry.snapshot_id.in_([s.id for s in snapshots]), RankingEntry.rank == 1)
            .order_by(RankingEntry.raw_name)
        ).all()
        for row in leader_rows:
            leaders.setdefault(
                row.snapshot_id, {"company_id": str(row.company_id), "name": row.raw_name}
            )
    return {
        "ranking": _ranking_fields(ranking),
        "items": [{**_snapshot_summary(s), "leader": leaders.get(s.id)} for s in snapshots],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def company_rankings(session, company_id, limit=50):
    """A company's appearances in external rankings, with movement and dropped points.

    Per ranking, the newest ``limit`` snapshots are examined; a snapshot where the company is
    absent right after an appearance is reported as ``dropped``.
    """

    company = session.get(Company, resolve_company_redirect(session, company_id))
    if company is None:
        return None
    aliases = session.scalars(
        select(CompanyAlias)
        .where(CompanyAlias.company_id == company.id)
        .order_by(CompanyAlias.alias)
    ).all()
    identities = session.scalars(
        select(CompanyIdentity)
        .where(CompanyIdentity.company_id == company.id)
        .order_by(CompanyIdentity.scheme, CompanyIdentity.value)
    ).all()
    ranking_ids = list(
        session.scalars(
            select(RankingSnapshot.ranking_id)
            .join(RankingEntry, RankingEntry.snapshot_id == RankingSnapshot.id)
            .where(RankingEntry.company_id == company.id)
            .distinct()
        )
    )
    rankings = {
        r.id: r for r in session.scalars(select(Ranking).where(Ranking.id.in_(ranking_ids)))
    }
    ordered = (
        select(
            RankingSnapshot,
            func.row_number()
            .over(partition_by=RankingSnapshot.ranking_id, order_by=NEWEST)
            .label("position"),
        )
        .where(RankingSnapshot.ranking_id.in_(ranking_ids))
        .subquery()
    )
    snapshot_rows = session.execute(
        select(RankingSnapshot)
        .join(ordered, ordered.c.id == RankingSnapshot.id)
        .where(ordered.c.position <= limit + 1)
    ).scalars()
    by_ranking = defaultdict(list)
    for snapshot in snapshot_rows:
        by_ranking[snapshot.ranking_id].append(snapshot)
    snapshot_ids = [s.id for snapshots in by_ranking.values() for s in snapshots]
    entries = {
        row.snapshot_id: row
        for row in session.scalars(
            select(RankingEntry).where(
                RankingEntry.company_id == company.id, RankingEntry.snapshot_id.in_(snapshot_ids)
            )
        )
    }
    appearances = []
    for ranking_id, snapshots in by_ranking.items():
        snapshots.sort(key=lambda s: (s.effective_at, s.observed_at))
        points = []
        # The extra oldest snapshot only supplies the previous rank of the first point.
        for index, snapshot in enumerate(snapshots):
            entry = entries.get(snapshot.id)
            previous = snapshots[index - 1] if index else None
            previous_entry = entries.get(previous.id) if previous else None
            if index == 0 and len(snapshots) > limit:
                continue
            if entry is None:
                if previous_entry is not None:
                    points.append(
                        {
                            "snapshot_id": str(snapshot.id),
                            "effective_at": _iso(snapshot.effective_at),
                            "observed_at": _iso(snapshot.observed_at),
                            "rank": None,
                            "previous_rank": previous_entry.rank,
                            "rank_change": None,
                            "movement": "dropped",
                            "raw_value": None,
                        }
                    )
                continue
            previous_rank = previous_entry.rank if previous_entry else None
            change, move = movement(previous_rank, entry.rank, previous is not None)
            points.append(
                {
                    "snapshot_id": str(snapshot.id),
                    "effective_at": _iso(snapshot.effective_at),
                    "observed_at": _iso(snapshot.observed_at),
                    "rank": entry.rank,
                    "previous_rank": previous_rank,
                    "rank_change": change,
                    "movement": move,
                    "raw_value": entry.raw_value,
                }
            )
        points.reverse()
        latest = snapshots[-1]
        ranking = rankings[ranking_id]
        appearances.append(
            {
                "ranking": _ranking_fields(ranking),
                "in_latest_snapshot": latest.id in entries,
                "latest_rank": entries[latest.id].rank if latest.id in entries else None,
                "history": points,
            }
        )
    appearances.sort(
        key=lambda item: (
            DOMAIN_ORDER.get(item["ranking"]["domain"], 9),
            REGISTRY_ORDER.get(item["ranking"]["key"], len(REGISTRY_ORDER)),
        )
    )
    return {
        "company_id": str(company.id),
        "canonical_name": company.canonical_name,
        "aliases": [
            {
                "alias": alias.alias,
                "alias_type": alias.alias_type,
                "alias_source": alias.alias_source,
            }
            for alias in aliases
        ],
        "identities": [
            {"scheme": identity.scheme, "value": identity.value, "source_key": identity.source_key}
            for identity in identities
        ],
        "rankings": appearances,
    }
