from uuid import uuid4

from industry_intelligence.services.data_quality_audit import _redirect_findings


def classifications(rows):
    return {row["subject_id"]: row["classification"] for row in rows}


def test_redirect_findings_classify_every_shape():
    a, b, c, d, e, f, g, missing = (uuid4() for _ in range(8))
    redirects = {a: a, b: missing, c: d, d: c, e: f, f: g, missing: g}
    existing = {a, b, c, d, e, f, g}

    errors, chains, good = _redirect_findings(redirects, existing, "subject")

    assert classifications(errors) == {
        str(a): "self_redirect",
        str(b): "missing_target",
        str(c): "redirect_loop",
        str(d): "redirect_loop",
        str(missing): "missing_retired_row",
    }
    assert classifications(chains) == {str(e): "redirect_chain"}
    assert chains[0]["resolves_to"] == str(g)
    assert classifications(good) == {str(f): "resolves_to_current"}


def test_redirect_findings_report_a_resolver_that_disagrees():
    old, new, elsewhere = uuid4(), uuid4(), uuid4()

    errors, _, good = _redirect_findings(
        {old: new}, {old, new, elsewhere}, "subject", resolver=lambda _: elsewhere
    )

    assert classifications(errors) == {str(old): "resolution_mismatch"}
    assert good == []
