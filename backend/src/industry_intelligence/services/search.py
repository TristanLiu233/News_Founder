"""PostgreSQL lexical/vector retrieval and search-only reciprocal rank fusion."""

import json
import logging
from time import perf_counter

import httpx
from sqlalchemy import text

from industry_intelligence.ai.embeddings import embedding_provider, validate_vector
from industry_intelligence.services.resolution import normalize_subject_name

logger = logging.getLogger(__name__)


def normalize_search_query(value):
    return normalize_subject_name(value)


def fuse(lexical, semantic, settings):
    """Pin exact identities, then fuse ranks (never raw retrieval scores)."""
    entries = {}
    for channel, candidates, weight in (
        ("lexical", lexical, settings.search_lexical_weight),
        ("semantic", semantic, settings.search_semantic_weight),
    ):
        for rank, candidate in enumerate(candidates, 1):
            sid = str(candidate["subject_id"])
            entry = entries.setdefault(
                sid, dict(candidate, score=0.0, lexical_rank=None, semantic_rank=None)
            )
            entry[channel + "_rank"] = rank
            entry["score"] += weight / (settings.search_rrf_k + rank)
    return sorted(
        entries.values(),
        key=lambda x: (not x.get("exact", False), -x["score"], str(x["subject_id"])),
    )


FILTER = """
    (CAST(:domain AS text) IS NULL OR d.domain = :domain)
    AND (CAST(:type AS text) IS NULL OR d.subject_type = :type)
    AND ((CAST(:source AS text) IS NULL AND CAST(:language AS text) IS NULL) OR EXISTS (
        SELECT 1 FROM rank_observations o JOIN sources s ON s.id=o.source_id
        WHERE o.subject_id=d.subject_id AND (CAST(:source AS text) IS NULL OR s.name=:source)
            AND (CAST(:language AS text) IS NULL OR s.primary_language=:language)))
"""

LEXICAL = (
    """
    WITH candidates AS (
        SELECT d.*, a.alias AS matched_alias,
            d.normalized_name=:q OR a.normalized_alias=:q AS exact,
            CASE WHEN d.normalized_name=:q THEN 4
                 WHEN a.normalized_alias=:q THEN 4
                 WHEN starts_with(d.normalized_name, :q)
                    OR starts_with(a.normalized_alias, :q) THEN 3
                 WHEN d.search_vector @@ plainto_tsquery('simple', :q) THEN 2
                 ELSE 1 END AS tier,
            ts_rank_cd(d.search_vector, plainto_tsquery('simple', :q)) AS fts_rank,
            greatest(similarity(d.normalized_name, :q),
                coalesce(similarity(a.normalized_alias, :q), 0)) AS similarity,
            CASE WHEN d.normalized_name=:q THEN 'canonical_name'
                 WHEN a.normalized_alias=:q THEN 'alias'
                 WHEN starts_with(d.normalized_name, :q) THEN 'canonical_name'
                 WHEN a.alias IS NOT NULL THEN 'alias'
                 WHEN to_tsvector('simple', d.canonical_name)
                    @@ plainto_tsquery('simple', :q)
                    OR similarity(d.normalized_name, :q)>=:threshold
                    THEN 'canonical_name'
                 ELSE 'source_title' END AS matched_on
        FROM search_documents d
        LEFT JOIN LATERAL (
            SELECT alias, normalized_alias FROM subject_aliases a
            WHERE a.subject_id=d.subject_id AND (a.normalized_alias=:q
                OR starts_with(a.normalized_alias, :q)
                OR (length(:q)>=3 AND a.normalized_alias % :q
                    AND similarity(a.normalized_alias, :q)>=:threshold))
            ORDER BY (a.normalized_alias=:q) DESC,
                starts_with(a.normalized_alias, :q) DESC,
                similarity(a.normalized_alias, :q) DESC, a.alias, a.id LIMIT 1
        ) a ON true
        WHERE """
    + FILTER
    + """
        AND (d.normalized_name=:q OR starts_with(d.normalized_name, :q)
            OR a.alias IS NOT NULL OR d.search_vector @@ plainto_tsquery('simple', :q)
            OR (length(:q)>=3 AND d.normalized_name % :q
                AND similarity(d.normalized_name, :q)>=:threshold))
    ) SELECT * FROM candidates
    ORDER BY tier DESC, fts_rank DESC, similarity DESC, subject_id
"""
)


def search(
    session,
    settings,
    query,
    *,
    domain=None,
    subject_type=None,
    source=None,
    language=None,
    mode="hybrid",
    limit=20,
    offset=0,
    locale="en",
    provider=None,
):
    started = perf_counter()
    q = normalize_search_query(query)
    params = dict(
        q=q,
        domain=domain,
        type=subject_type,
        source=source,
        language=language,
        threshold=settings.search_trigram_threshold,
    )
    lexical = [dict(row) for row in session.execute(text(LEXICAL), params).mappings()]
    lexical_ms = (perf_counter() - started) * 1000
    semantic_start = perf_counter()
    provider = provider or embedding_provider(settings)
    available = False
    semantic = []
    if provider:
        available = bool(
            session.scalar(
                text("""
            SELECT EXISTS(SELECT 1 FROM subject_embeddings e JOIN search_documents d
                USING(subject_id) WHERE e.embedding_model=:model AND e.dimension=:dim
                AND e.content_hash=d.content_hash)
        """),
                dict(model=provider.model, dim=provider.dimension),
            )
        )
    if available and mode != "lexical":
        try:
            vector = validate_vector(provider.embed(q), provider.dimension)
            semantic = [
                dict(row)
                for row in session.execute(
                    text(
                        """
                SELECT d.*, false AS exact, 'semantic' AS matched_on,
                    NULL AS matched_alias, e.embedding <=> CAST(:vector AS vector) AS distance
                FROM search_documents d JOIN subject_embeddings e USING(subject_id)
                WHERE """
                        + FILTER
                        + """ AND e.embedding_model=:model AND e.dimension=:dim
                    AND e.content_hash=d.content_hash
                ORDER BY distance, d.subject_id LIMIT :candidates
            """
                    ),
                    dict(
                        params,
                        vector=json.dumps(vector),
                        model=provider.model,
                        dim=provider.dimension,
                        candidates=settings.search_semantic_candidates,
                    ),
                ).mappings()
                if row["distance"] <= settings.search_semantic_max_distance
            ]
        except (ValueError, KeyError, TypeError, IndexError, ArithmeticError) as exc:
            available = False
            logger.warning("search embedding unavailable: %s", type(exc).__name__)
        except Exception as exc:
            # Provider/network failures must not disable lexical retrieval. SQL errors
            # are isolated below by the API's request transaction, not hidden as success.
            if not isinstance(exc, httpx.HTTPError):
                raise
            available = False
            logger.warning("search embedding unavailable: %s", type(exc).__name__)
    semantic_ms = (perf_counter() - semantic_start) * 1000
    fusion_start = perf_counter()
    used = mode if available else "lexical"
    if used == "semantic":
        # Identity exact matches remain authoritative even in semantic mode.
        lexical = [row for row in lexical if row.get("exact")]
    ranked = fuse(lexical, semantic if used != "lexical" else [], settings)
    if used == "lexical":
        # Preserve lexical tier/rank ordering including exact-match ties.
        ranked.sort(key=lambda row: row["lexical_rank"])
    fusion_ms = (perf_counter() - fusion_start) * 1000
    page = ranked[offset : offset + limit]
    items = []
    for rank, row in enumerate(page, offset + 1):
        localized = {
            a["locale"]: a["alias"]
            for a in row["aliases"]
            if a["alias_type"] in {"official_name", "localized_name"}
        }
        # Canonical English names win over ticker-like English aliases. Chinese
        # display still prefers the localized alias when one is indexed.
        if locale == "en" and not any(
            "\u3400" <= char <= "\u9fff" for char in row["canonical_name"]
        ):
            localized["en"] = row["canonical_name"]
        sources = (
            session.execute(
                text("""
            SELECT DISTINCT ON (s.id) s.name AS source, s.primary_language,
                o.rank AS latest_rank, o.observed_at AS latest_observed_at, o.title
            FROM rank_observations o JOIN sources s ON s.id=o.source_id
            WHERE o.subject_id=:id ORDER BY s.id, o.observed_at DESC, o.rank, o.id
        """),
                {"id": row["subject_id"]},
            )
            .mappings()
            .all()
        )
        title = session.scalar(
            text("""
            SELECT value FROM jsonb_array_elements_text(CAST(:titles AS jsonb))
            WHERE to_tsvector('simple', value) @@ plainto_tsquery('simple', :q) LIMIT 1
        """),
            dict(titles=json.dumps(row["recent_titles"]), q=q),
        )
        items.append(
            dict(
                subject_id=str(row["subject_id"]),
                canonical_name=row["canonical_name"],
                display_name=localized.get(locale, row["canonical_name"]),
                domain=row["domain"],
                subject_type=row["subject_type"],
                matched_on=row["matched_on"],
                matched_alias=row["matched_alias"] if row["matched_on"] == "alias" else None,
                recent_title=title or next(iter(row["recent_titles"]), None),
                latest_sources=[dict(s) for s in sources],
                score=row["score"],
                lexical_rank=row["lexical_rank"],
                semantic_rank=row["semantic_rank"],
                final_rank=rank,
            )
        )
    timings = dict(
        lexical_ms=round(lexical_ms, 2),
        semantic_ms=round(semantic_ms, 2),
        fusion_ms=round(fusion_ms, 2),
        total_ms=round((perf_counter() - started) * 1000, 2),
    )
    logger.info(
        "search mode=%s query_length=%s result_count=%s timings=%s",
        used,
        len(q),
        len(ranked),
        timings,
    )
    return dict(
        query=q,
        mode_requested=mode,
        mode_used=used,
        semantic_search_available=available,
        total=len(ranked),
        limit=limit,
        offset=offset,
        items=items,
        timings=timings,
    )
