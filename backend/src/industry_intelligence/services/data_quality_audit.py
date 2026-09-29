"""Read-only invariant audit of canonical identity, redirects, search, and relations (DQ-2).

The audit only issues SELECTs: it never repairs, merges, or creates anything, and the CLI runs
it inside a ``SET TRANSACTION READ ONLY`` transaction that is rolled back. Every check reports
a status and its findings carry one severity:

* ``error`` — breaks canonical resolution or referential correctness (a strong identity held by
  two active Subjects, a redirect loop, a current relation or identity on a retired Subject, a
  retired Subject that is still searchable);
* ``warning`` — needs review but does not prove corruption (a canonical name that differs from
  its normalized title, a shared alias, an occupied canonical target);
* ``info`` — expected history (retired rows, redirects that resolve correctly, historical
  composite entries).

"Active" means not redirected; "retired" means the Subject (or Company) is a redirect duplicate.
Scopes follow the resolver: Subject names, aliases, and provider-neutral IDs are compared within
one (domain, subject_type).
"""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from industry_intelligence.adapters.company_profile.gleif import LEI_SCHEME
from industry_intelligence.adapters.company_profile.sec_edgar import CIK_SCHEME
from industry_intelligence.adapters.company_ranking.companiesmarketcap import SYMBOL_SCHEME
from industry_intelligence.db.models import (
    Company,
    CompanyAlias,
    CompanyIdentity,
    CompanyRedirect,
    CompanySubjectLink,
    ContentItem,
    ContentSubjectLink,
    Event,
    EventCompanyLink,
    EventRedirect,
    EventSubjectLink,
    RankList,
    RankObservation,
    Source,
    Subject,
)
from industry_intelligence.db.models.canonical import (
    CompositeRankingEntry,
    SubjectAlias,
    SubjectExternalIdentity,
)
from industry_intelligence.db.models.search import SearchDocument, SubjectRedirect
from industry_intelligence.services.resolution import normalize_subject_name
from industry_intelligence.services.subject_detail import resolve_canonical
from industry_intelligence.services.subject_identity_repair import LEGACY_HASH

AUDIT_VERSION = "data-quality-audit/1"
SEVERITIES = ("error", "warning", "info")
# Two different values in one of these schemes mean two legal entities (company_merge refuses).
LEGAL_ENTITY_SCHEMES = (CIK_SCHEME, LEI_SCHEME)


class _Report:
    def __init__(self):
        self.checks = []
        self.findings = {severity: [] for severity in SEVERITIES}

    def check(self, check_id, category, description, severity, findings):
        """Record one check; ``severity`` applies to every finding it produced."""

        for finding in findings:
            self.findings[severity].append({"check": check_id, "severity": severity, **finding})
        status = "ok" if not findings else severity
        self.checks.append(
            {
                "id": check_id,
                "category": category,
                "description": description,
                "severity": severity,
                "status": status,
                "count": len(findings),
            }
        )
        return len(findings)


def _scope(domain, subject_type):
    return {"domain": str(domain), "subject_type": str(subject_type)}


def _ids(values):
    return sorted(str(value) for value in values)


def _follow(redirects, start):
    """Walk an in-memory redirect map; returns (final_id, path, loop)."""

    path, current = [start], start
    while current in redirects:
        current = redirects[current]
        if current in path:
            return current, path + [current], True
        path.append(current)
    return current, path, False


def _redirect_findings(redirects, existing_ids, kind, resolver=None):
    """Integrity of one redirect table: self, missing target, loop, chain, resolution."""

    errors, chains, known_good = [], [], []
    for duplicate_id, canonical_id in sorted(redirects.items(), key=lambda row: str(row[0])):
        record = {f"{kind}_id": str(duplicate_id), "target_id": str(canonical_id)}
        if duplicate_id == canonical_id:
            errors.append({**record, "classification": "self_redirect"})
            continue
        if duplicate_id not in existing_ids:
            errors.append({**record, "classification": "missing_retired_row"})
            continue
        if canonical_id not in existing_ids:
            errors.append({**record, "classification": "missing_target"})
            continue
        final, path, loop = _follow(redirects, duplicate_id)
        if loop:
            errors.append(
                {**record, "classification": "redirect_loop", "path": [str(step) for step in path]}
            )
            continue
        if resolver is not None:
            resolved = resolver(duplicate_id)
            if resolved != final:
                errors.append(
                    {
                        **record,
                        "classification": "resolution_mismatch",
                        "resolved_id": str(resolved) if resolved else None,
                    }
                )
                continue
        if final != canonical_id:
            chains.append({**record, "classification": "redirect_chain", "resolves_to": str(final)})
            continue
        known_good.append({**record, "classification": "resolves_to_current"})
    return errors, chains, known_good


def _subject_section(session, report, summary):
    subjects = {row.id: row for row in session.scalars(select(Subject))}
    redirects = dict(
        session.execute(select(SubjectRedirect.duplicate_id, SubjectRedirect.canonical_id)).all()
    )
    retired = set(redirects) & set(subjects)
    active = {subject_id: row for subject_id, row in subjects.items() if subject_id not in retired}
    aliases = list(session.scalars(select(SubjectAlias)))
    identities = list(session.scalars(select(SubjectExternalIdentity)))
    aliases_by_subject = defaultdict(list)
    for alias in aliases:
        aliases_by_subject[alias.subject_id].append(alias)
    identities_by_subject = defaultdict(list)
    for identity in identities:
        identities_by_subject[identity.subject_id].append(identity)
    source_names = dict(session.execute(select(Source.id, Source.name)).all())

    def provenance(subject):
        observations = session.execute(
            select(RankObservation.title, Source.name, RankList.name)
            .join(Source, Source.id == RankObservation.source_id)
            .join(RankList, RankList.id == RankObservation.rank_list_id)
            .where(RankObservation.subject_id == subject.id)
        ).all()
        return {
            "sources": sorted({f"{source} / {rank_list}" for _, source, rank_list in observations}),
            "observations": len(observations),
            "observation_title_variants": len({title for title, _, _ in observations}),
            "alias_sources": sorted({a.alias_source for a in aliases_by_subject[subject.id]}),
            "identity_sources": sorted(
                {
                    source_names.get(i.source_id, str(i.source_id))
                    for i in identities_by_subject[subject.id]
                }
            ),
        }

    # A. canonical-name integrity (active only; retired rows are kept as history).
    legacy, mismatches = [], []
    for subject in sorted(active.values(), key=lambda row: str(row.id)):
        target = normalize_subject_name(subject.title)
        if subject.canonical_name == target:
            continue
        record = {
            "subject_id": str(subject.id),
            **_scope(subject.domain, subject.subject_type),
            "title": subject.title,
            "canonical_name": subject.canonical_name,
            "normalized_title": target,
        }
        if LEGACY_HASH.fullmatch(subject.canonical_name):
            legacy.append({**record, "classification": "legacy_hash"})
        else:
            mismatches.append(
                {**record, "classification": "non_hash_mismatch", **provenance(subject)}
            )
    report.check(
        "subject.legacy_hash",
        "subjects",
        "Active Subject whose canonical_name is a Phase 1 64-hex hash (DQ-1 invariant).",
        "error",
        legacy,
    )
    report.check(
        "subject.canonical_mismatch",
        "subjects",
        "Active Subject whose canonical_name differs from normalize_subject_name(title).",
        "warning",
        mismatches,
    )
    report.check(
        "subject.retired_historical",
        "subjects",
        "Retired (redirected) Subject rows kept as history; not checked for name integrity.",
        "info",
        [
            {"subject_id": str(subject_id), "classification": "retired_historical"}
            for subject_id in sorted(retired, key=str)
        ],
    )

    # B. canonical occupancy of each active Subject's normalized target.
    by_target = defaultdict(list)
    for subject in active.values():
        key = (subject.domain, subject.subject_type, normalize_subject_name(subject.title))
        by_target[key].append(subject)
    occupants = defaultdict(set)
    for subject in subjects.values():
        occupants[(subject.domain, subject.subject_type, subject.canonical_name)].add(subject.id)
    occupancy = []
    for key, group in sorted(by_target.items(), key=lambda row: [str(part) for part in row[0]]):
        domain, subject_type, target = key
        members = {subject.id for subject in group}
        others = occupants[key] - members
        if len(group) > 1:
            classification = "active_collision"
        elif others & retired:
            classification = "retired_occupancy"
        elif others:
            classification = "review_required"
        else:
            continue
        occupancy.append(
            {
                "classification": classification,
                **_scope(domain, subject_type),
                "target": target,
                "subject_ids": _ids(members),
                "occupied_by": _ids(others),
                "occupied_by_retired": _ids(others & retired),
            }
        )
    report.check(
        "subject.canonical_occupancy",
        "subjects",
        "Normalized title target shared by several active Subjects or held by another Subject.",
        "warning",
        occupancy,
    )

    # C. alias collisions within the resolver scope.
    alias_owners = defaultdict(set)
    for alias in aliases:
        subject = subjects.get(alias.subject_id)
        if subject is not None:
            key = (subject.domain, subject.subject_type, normalize_subject_name(alias.alias))
            alias_owners[key].add(subject.id)
    alias_review, alias_history = [], []
    for key, owners in sorted(alias_owners.items(), key=lambda row: [str(part) for part in row[0]]):
        domain, subject_type, normalized = key
        live = owners - retired
        record = {**_scope(domain, subject_type), "normalized_alias": normalized}
        if len(live) > 1:
            alias_review.append(
                {**record, "classification": "review_required", "subject_ids": _ids(live)}
            )
        elif live and owners & retired:
            alias_history.append(
                {
                    **record,
                    "classification": "known_redirect_history",
                    "subject_ids": _ids(live),
                    "retired_ids": _ids(owners & retired),
                }
            )
    report.check(
        "subject.alias_collision",
        "aliases",
        "One normalized alias held by several active Subjects of one domain and type.",
        "warning",
        alias_review,
    )
    report.check(
        "subject.alias_redirect_history",
        "aliases",
        "Normalized alias shared by an active Subject and a retired Subject.",
        "info",
        alias_history,
    )
    report.check(
        "subject.alias_on_retired",
        "aliases",
        "Alias row still attached to a retired Subject (merges move or deduplicate aliases).",
        "error",
        [
            {
                "classification": "alias_on_retired",
                "subject_id": str(alias.subject_id),
                "alias": alias.alias,
                "target_id": str(redirects[alias.subject_id]),
            }
            for alias in aliases
            if alias.subject_id in retired
        ],
    )

    # D. external identity collisions.
    canonical_ids = defaultdict(set)
    canonical_urls = defaultdict(set)
    for identity in identities:
        subject = active.get(identity.subject_id)
        if subject is None:
            continue
        if identity.canonical_id:
            canonical_ids[identity.canonical_id].add(subject.id)
        if identity.canonical_url:
            canonical_urls[(subject.domain, subject.subject_type, identity.canonical_url)].add(
                subject.id
            )
    same_scope_ids, cross_scope_ids = [], []
    for canonical_id, owners in sorted(canonical_ids.items()):
        if len(owners) < 2:
            continue
        scopes = Counter((active[owner].domain, active[owner].subject_type) for owner in owners)
        record = {"canonical_id": canonical_id, "subject_ids": _ids(owners)}
        if any(count > 1 for count in scopes.values()):
            same_scope_ids.append({**record, "classification": "strong_identity_collision"})
        else:
            cross_scope_ids.append(
                {
                    **record,
                    "classification": "cross_scope_identity",
                    "scopes": sorted(f"{domain}/{kind}" for domain, kind in scopes),
                }
            )
    report.check(
        "subject.canonical_id_collision",
        "identities",
        "One provider-neutral canonical_id held by several active Subjects of one scope.",
        "error",
        same_scope_ids,
    )
    report.check(
        "subject.canonical_id_cross_scope",
        "identities",
        "One canonical_id held by active Subjects of different domains or types.",
        "warning",
        cross_scope_ids,
    )
    url_collisions = [
        {
            "classification": "canonical_url_collision",
            **_scope(domain, subject_type),
            "canonical_url": url,
            "subject_ids": _ids(owners),
        }
        for (domain, subject_type, url), owners in sorted(
            canonical_urls.items(), key=lambda row: [str(part) for part in row[0]]
        )
        if len(owners) > 1
    ]
    report.check(
        "subject.canonical_url_collision",
        "identities",
        "One canonical URL held by several active Subjects of one domain and type.",
        "warning",
        url_collisions,
    )
    report.check(
        "subject.identity_on_retired",
        "identities",
        "External identity still attached to a retired Subject (resolution would return it).",
        "error",
        [
            {
                "classification": "identity_on_retired",
                "subject_id": str(identity.subject_id),
                "source": source_names.get(identity.source_id, str(identity.source_id)),
                "external_id": identity.external_id,
                "target_id": str(redirects[identity.subject_id]),
            }
            for identity in identities
            if identity.subject_id in retired
        ],
    )

    # E. redirect integrity; resolve_canonical is the read path used by Subject Detail.
    def resolver(subject_id):
        subject = resolve_canonical(session, subject_id)
        return subject.id if subject else None

    errors, chains, known_good = _redirect_findings(redirects, set(subjects), "subject", resolver)
    report.check(
        "subject.redirect_integrity",
        "redirects",
        "Subject redirect is a self redirect, loop, dangling, or resolves elsewhere.",
        "error",
        errors,
    )
    report.check(
        "subject.redirect_chain",
        "redirects",
        "Subject redirect whose target is itself redirected (resolvable, not collapsed).",
        "warning",
        chains,
    )
    report.check(
        "subject.redirect_resolves",
        "redirects",
        "Subject redirect resolving directly to a current Subject (known good).",
        "info",
        known_good,
    )

    summary["subjects"] = {
        "rows": len(subjects),
        "active": len(active),
        "retired": len(retired),
        "legacy_hashes": len(legacy),
        "canonical_mismatches": len(mismatches),
        "canonical_occupancy_findings": len(occupancy),
        "alias_collisions": len(alias_review),
        "strong_identity_collisions": len(same_scope_ids),
        "canonical_url_collisions": len(url_collisions),
    }
    summary["subject_redirects"] = {
        "total": len(redirects),
        "resolving_to_current": len(known_good),
        "chains": len(chains),
        "invalid": len(errors),
        "loops": sum(1 for row in errors if row["classification"] == "redirect_loop"),
    }
    return subjects, active, retired, redirects


def _search_section(session, report, summary, subjects, active, retired):
    documents = {row.subject_id: row for row in session.scalars(select(SearchDocument))}
    missing = [
        {"classification": "missing_active_document", "subject_id": str(subject_id)}
        for subject_id in sorted(set(active) - set(documents), key=str)
    ]
    retired_docs = [
        {"classification": "retired_searchable", "subject_id": str(subject_id)}
        for subject_id in sorted(set(documents) & retired, key=str)
    ]
    orphans = [
        {"classification": "orphan_document", "subject_id": str(subject_id)}
        for subject_id in sorted(set(documents) - set(subjects), key=str)
    ]
    stale = []
    for subject_id, document in sorted(documents.items(), key=lambda row: str(row[0])):
        subject = active.get(subject_id)
        if subject is None:
            continue
        # refresh_subject projects the display title into canonical_name/normalized_name.
        expected = {
            "canonical_name": subject.title,
            "normalized_name": normalize_subject_name(subject.title),
            "domain": str(subject.domain),
            "subject_type": str(subject.subject_type),
        }
        drift = {
            field: {"document": getattr(document, field), "expected": value}
            for field, value in expected.items()
            if getattr(document, field) != value
        }
        if drift:
            stale.append(
                {"classification": "stale_projection", "subject_id": str(subject_id), **drift}
            )
    report.check(
        "search.missing_active_document",
        "search",
        "Active Subject without a SearchDocument.",
        "warning",
        missing,
    )
    report.check(
        "search.retired_document",
        "search",
        "Retired Subject still has a SearchDocument, so Search can return it.",
        "error",
        retired_docs,
    )
    report.check(
        "search.orphan_document",
        "search",
        "SearchDocument whose Subject does not exist.",
        "error",
        orphans,
    )
    report.check(
        "search.stale_projection",
        "search",
        "SearchDocument title, normalized title, domain, or type differs from its Subject.",
        "warning",
        stale,
    )
    summary["search"] = {
        "documents": len(documents),
        "missing_active_documents": len(missing),
        "retired_documents": len(retired_docs),
        "orphan_documents": len(orphans),
        "stale_projections": len(stale),
    }


def _relation_section(session, report, summary, retired, company_redirects):
    def on_retired(model, label):
        rows = session.execute(
            select(model.id, model.subject_id).where(model.subject_id.in_(retired))
        ).all()
        return [
            {"classification": f"{label}_on_retired", "row_id": str(row_id), "subject_id": str(sid)}
            for row_id, sid in rows
        ]

    current = {
        "content_subject_links": on_retired(ContentSubjectLink, "content_subject_link"),
        "event_subject_links": on_retired(EventSubjectLink, "event_subject_link"),
        "company_subject_links": on_retired(CompanySubjectLink, "company_subject_link"),
        "rank_observations": on_retired(RankObservation, "rank_observation"),
    }
    company_side = [
        {
            "classification": "company_subject_link_on_retired_company",
            "row_id": str(row_id),
            "company_id": str(cid),
        }
        for row_id, cid in session.execute(
            select(CompanySubjectLink.id, CompanySubjectLink.company_id).where(
                CompanySubjectLink.company_id.in_(company_redirects)
            )
        )
    ]
    report.check(
        "relation.retired_subject_reference",
        "relations",
        "Current relation (content, event, company link, or rank observation) on a retired "
        "Subject; merges move these rows to the survivor.",
        "error",
        [finding for rows in current.values() for finding in rows],
    )
    report.check(
        "relation.retired_company_reference",
        "relations",
        "CompanySubjectLink on a retired (merged-away) Company.",
        "error",
        company_side,
    )
    historical = session.execute(
        select(CompositeRankingEntry.subject_id).where(
            CompositeRankingEntry.subject_id.in_(retired)
        )
    ).all()
    report.check(
        "relation.historical_composite_entries",
        "relations",
        "Immutable CompositeRankingEntry rows on retired Subjects (expected history).",
        "info",
        [
            {"classification": "historical_record", "subject_id": str(subject_id), "entries": count}
            for subject_id, count in sorted(
                Counter(row[0] for row in historical).items(), key=lambda row: str(row[0])
            )
        ],
    )
    summary["relations"] = {
        **{f"retired_{name}": len(rows) for name, rows in current.items()},
        "retired_company_subject_links": len(company_side),
        "historical_composite_entries_on_retired": len(historical),
    }


def _event_company_section(session, report, summary, subjects, company_ids, company_redirects):
    links = list(session.scalars(select(EventCompanyLink)))
    event_ids = set(session.scalars(select(Event.id)))
    event_redirects = set(session.scalars(select(EventRedirect.duplicate_id)))
    content_ids = {str(value) for value in session.scalars(select(ContentItem.id))}
    subject_ids = {str(value) for value in subjects}
    findings = []
    for link in sorted(links, key=lambda row: str(row.id)):
        problems = []
        if link.event_id not in event_ids:
            problems.append("missing_event")
        elif link.event_id in event_redirects:
            problems.append("redirected_event")
        if link.company_id not in company_ids:
            problems.append("missing_company")
        elif link.company_id in company_redirects:
            problems.append("retired_company")
        evidence = link.evidence or {}
        if any(str(value) not in content_ids for value in evidence.get("content_item_ids", [])):
            problems.append("missing_evidence_content")
        if any(str(value) not in subject_ids for value in evidence.get("subject_ids", [])):
            problems.append("missing_evidence_subject")
        if problems:
            findings.append(
                {
                    "classification": "invalid_structure",
                    "row_id": str(link.id),
                    "problems": problems,
                }
            )
    report.check(
        "event_company.structure",
        "event_company",
        "EventCompanyLink with a missing or redirected Event/Company or dangling evidence IDs "
        "(structural only; the number of links is coverage, not quality).",
        "error",
        findings,
    )
    summary["event_company_links"] = {"rows": len(links), "structural_violations": len(findings)}


def _company_section(session, report, summary):
    companies = {row.id: row for row in session.scalars(select(Company))}
    redirects = dict(
        session.execute(select(CompanyRedirect.duplicate_id, CompanyRedirect.canonical_id)).all()
    )
    retired = set(redirects) & set(companies)
    active = set(companies) - retired
    identities = list(session.scalars(select(CompanyIdentity)))
    aliases = list(session.scalars(select(CompanyAlias)))

    holders = defaultdict(set)
    per_company = defaultdict(lambda: defaultdict(set))
    for identity in identities:
        if identity.company_id in active:
            holders[(identity.scheme, identity.value)].add(identity.company_id)
            per_company[identity.company_id][identity.scheme].add(identity.value)
    collisions = [
        {
            "classification": "strong_identity_collision",
            "scheme": scheme,
            "value": value,
            "company_ids": _ids(owners),
        }
        for (scheme, value), owners in sorted(holders.items())
        if len(owners) > 1
    ]
    legal_conflicts, symbol_multiples = [], []
    for company_id, schemes in sorted(per_company.items(), key=lambda row: str(row[0])):
        for scheme, values in sorted(schemes.items()):
            if len(values) < 2:
                continue
            record = {"company_id": str(company_id), "scheme": scheme, "values": sorted(values)}
            if scheme in LEGAL_ENTITY_SCHEMES:
                legal_conflicts.append({**record, "classification": "multiple_legal_identities"})
            elif scheme == SYMBOL_SCHEME:
                symbol_multiples.append({**record, "classification": "multiple_symbols"})
    report.check(
        "company.strong_identity_collision",
        "companies",
        "One (scheme, value) identity held by several active Companies.",
        "error",
        collisions,
    )
    report.check(
        "company.multiple_legal_identities",
        "companies",
        "Active Company holding two SEC CIKs or two LEIs (two legal entities).",
        "error",
        legal_conflicts,
    )
    report.check(
        "company.multiple_symbols",
        "companies",
        "Active Company holding two ticker symbols (allowed only after a reviewed merge).",
        "warning",
        symbol_multiples,
    )
    report.check(
        "company.identity_on_retired",
        "companies",
        "Identity still attached to a retired Company (merges move identities).",
        "error",
        [
            {
                "classification": "identity_on_retired",
                "company_id": str(identity.company_id),
                "scheme": identity.scheme,
                "value": identity.value,
            }
            for identity in identities
            if identity.company_id in retired
        ],
    )
    without_identity = sorted(active - {identity.company_id for identity in identities}, key=str)
    report.check(
        "company.without_identity",
        "companies",
        "Active Company without any source identity.",
        "warning",
        [
            {"classification": "without_identity", "company_id": str(company_id)}
            for company_id in without_identity
        ],
    )
    names = defaultdict(set)
    for company_id in active:
        names[companies[company_id].normalized_name].add(company_id)
    for alias in aliases:
        if alias.company_id in active:
            names[alias.normalized_alias].add(alias.company_id)
    alias_collisions = [
        {"classification": "review_required", "normalized_name": name, "company_ids": _ids(owners)}
        for name, owners in sorted(names.items())
        if len(owners) > 1
    ]
    report.check(
        "company.alias_collision",
        "companies",
        "One exact normalized name or alias shared by several active Companies (no fuzzy "
        "matching; similar names are never compared).",
        "warning",
        alias_collisions,
    )
    errors, chains, known_good = _redirect_findings(redirects, set(companies), "company")
    report.check(
        "company.redirect_integrity",
        "companies",
        "Company redirect is a self redirect, loop, or dangling.",
        "error",
        errors,
    )
    report.check(
        "company.redirect_chain",
        "companies",
        "Company redirect whose target is itself redirected.",
        "warning",
        chains,
    )
    summary["companies"] = {
        "rows": len(companies),
        "active": len(active),
        "retired": len(retired),
        "identities": len(identities),
        "aliases": len(aliases),
        "redirects": len(redirects),
        "strong_identity_collisions": len(collisions),
        "multiple_legal_identities": len(legal_conflicts),
        "without_identity": len(without_identity),
        "alias_collisions": len(alias_collisions),
        "redirect_loops": sum(1 for row in errors if row["classification"] == "redirect_loop"),
    }
    return set(companies), retired


def audit_data_quality(session):
    """Run every check with SELECTs only and return the machine-readable report."""

    report = _Report()
    summary = {}
    subjects, active, retired, _ = _subject_section(session, report, summary)
    _search_section(session, report, summary, subjects, active, retired)
    company_ids, company_retired = _company_section(session, report, summary)
    _relation_section(session, report, summary, retired, company_retired)
    _event_company_section(session, report, summary, subjects, company_ids, company_retired)
    summary["result"] = {
        "errors": len(report.findings["error"]),
        "warnings": len(report.findings["warning"]),
        "info": len(report.findings["info"]),
        "passed": not report.findings["error"],
    }
    return {
        "audit_version": AUDIT_VERSION,
        "summary": summary,
        "checks": report.checks,
        "errors": report.findings["error"],
        "warnings": report.findings["warning"],
        "info": report.findings["info"],
    }


def _describe(finding):
    for key in (
        "title",
        "target",
        "normalized_alias",
        "canonical_id",
        "canonical_url",
        "normalized_name",
        "value",
    ):
        if finding.get(key):
            label = finding[key]
            break
    else:
        label = ""
    ident = next(
        (finding[key] for key in ("subject_id", "company_id", "row_id") if finding.get(key)),
        ", ".join(finding.get("subject_ids") or finding.get("company_ids") or []),
    )
    return f"{finding['classification']}: {ident} {label}".rstrip()


def render_text(report, examples=5):
    """Concise human-readable rendering of ``audit_data_quality`` output."""

    summary = report["summary"]
    subjects, redirects = summary["subjects"], summary["subject_redirects"]
    search, relations = summary["search"], summary["relations"]
    companies, event_company = summary["companies"], summary["event_company_links"]
    lines = [
        "Data Quality Audit",
        "",
        "Subjects",
        f"  active: {subjects['active']}",
        f"  retired: {subjects['retired']}",
        f"  legacy hashes: {subjects['legacy_hashes']}",
        f"  canonical mismatches: {subjects['canonical_mismatches']}",
        f"  canonical occupancy findings: {subjects['canonical_occupancy_findings']}",
        f"  alias collisions: {subjects['alias_collisions']}",
        f"  strong identity collisions: {subjects['strong_identity_collisions']}",
        f"  canonical URL collisions: {subjects['canonical_url_collisions']}",
        "",
        "Redirects",
        f"  total: {redirects['total']}",
        f"  resolving to current: {redirects['resolving_to_current']}",
        f"  chains: {redirects['chains']}",
        f"  loops: {redirects['loops']}",
        f"  invalid: {redirects['invalid']}",
        "",
        "Search",
        f"  documents: {search['documents']}",
        f"  missing active documents: {search['missing_active_documents']}",
        f"  retired documents: {search['retired_documents']}",
        f"  orphan documents: {search['orphan_documents']}",
        f"  stale projections: {search['stale_projections']}",
        "",
        "Relations",
        "  retired Subject references: "
        + str(
            sum(
                relations[key]
                for key in (
                    "retired_content_subject_links",
                    "retired_event_subject_links",
                    "retired_company_subject_links",
                    "retired_rank_observations",
                )
            )
        ),
        f"  retired Company references: {relations['retired_company_subject_links']}",
        "  historical composite entries on retired Subjects: "
        f"{relations['historical_composite_entries_on_retired']}",
        f"  EventCompanyLinks: {event_company['rows']} "
        f"(structural violations {event_company['structural_violations']})",
        "",
        "Companies",
        f"  active: {companies['active']}",
        f"  retired: {companies['retired']}",
        f"  strong identity collisions: {companies['strong_identity_collisions']}",
        f"  multiple legal identities: {companies['multiple_legal_identities']}",
        f"  without identity: {companies['without_identity']}",
        f"  alias collisions: {companies['alias_collisions']}",
        f"  redirect loops: {companies['redirect_loops']}",
    ]
    for severity, key in (("ERROR", "errors"), ("WARNING", "warnings")):
        grouped = defaultdict(list)
        for finding in report[key]:
            grouped[finding["check"]].append(finding)
        for check_id, findings in grouped.items():
            lines += ["", f"{severity} {check_id} ({len(findings)})"]
            lines += [f"  - {_describe(finding)}" for finding in findings[:examples]]
            if len(findings) > examples:
                lines.append(f"  … {len(findings) - examples} more (use --json for all)")
    result = summary["result"]
    lines += ["", "Result:", f"{result['errors']} errors", f"{result['warnings']} warnings"]
    return "\n".join(lines)
