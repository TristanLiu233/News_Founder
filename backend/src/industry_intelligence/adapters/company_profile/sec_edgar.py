"""SEC EDGAR: official regulatory company facts and annual-report XBRL segments.

Two documented public endpoints are used, with a declared User-Agent carrying SEC_CONTACT (SEC
fair-access policy) and far below SEC's 10 requests/second ceiling:

* ``data.sec.gov/submissions/CIK##########.json`` for the registrant's SIC industry, business
  address, incorporation state, tickers, and filing list;
* the latest annual report (10-K, 20-F or 40-F) in the EDGAR archive, whose XBRL instance and
  label linkbase name the reported operating segments (``business_areas``) and product/service
  lines (``key_products``) for the report's own fiscal period.

Segment and product names are the filer's XBRL labels, never inferred from prose.
"""

from __future__ import annotations

import asyncio
import io
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from urllib.parse import urlsplit

from industry_intelligence.adapters.company_profile.base import ExternalCompanyProfile
from industry_intelligence.adapters.company_profile.text import clean_text

CIK_SCHEME = "sec:cik"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}/"
COMPANY_URL = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
ANNUAL_FORMS = ("10-K", "20-F", "40-F")
# Seconds between requests: at most 2 requests/second, well under SEC's 10/second maximum.
REQUEST_INTERVAL = 0.5
LIST_LIMIT = 24

XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
LINK = "{http://www.xbrl.org/2003/linkbase}"
XLINK = "{http://www.w3.org/1999/xlink}"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"
TERSE_ROLE = "http://www.xbrl.org/2003/role/terseLabel"
LABEL_ROLE = "http://www.xbrl.org/2003/role/label"
# Standard taxonomies (FASB US GAAP / SRT, IFRS Foundation) that define the dimensions.
STANDARD_HOSTS = ("fasb.org", "xbrl.ifrs.org")
SEGMENT_AXES = frozenset({"StatementBusinessSegmentsAxis", "SegmentsAxis"})
PRODUCT_AXES = frozenset({"ProductOrServiceAxis", "ProductsAndServicesAxis"})
# Standard members that reconcile or aggregate segments rather than name a business.
STRUCTURAL_MEMBERS = frozenset(
    {
        "AllOtherSegmentsMember",
        "AggregatedSegmentsMember",
        "ConsolidationEliminationsMember",
        "CorporateNonSegmentMember",
        "EliminationOfIntersegmentAmountsMember",
        "IntersegmentEliminationMember",
        "MaterialReconcilingItemsMember",
        "OperatingSegmentsMember",
        "ReportableSegmentsMember",
        "SegmentReconcilingItemsMember",
        "UnallocatedAmountsMember",
    }
)
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH "
    "NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
# US GAAP labels end in " [Member]", IFRS filers often in " [member]".
_MEMBER_SUFFIX = re.compile(r"\s*\[[Mm]ember\]$|\s+Member$")


def _date(value):
    if not value:
        return None
    return datetime.fromisoformat(value[:10]).replace(tzinfo=UTC)


def latest_annual_filing(submissions):
    """The newest original annual report in the registrant's recent filings, if any."""

    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    for index, form in enumerate(forms):
        if form in ANNUAL_FORMS:
            return {
                "form": form,
                "accession": recent["accessionNumber"][index],
                "filing_date": recent["filingDate"][index],
                "report_date": recent.get("reportDate", [None] * len(forms))[index] or None,
                "primary_document": recent.get("primaryDocument", [None] * len(forms))[index],
            }
    return None


def _headquarters(address):
    if not address or not address.get("city"):
        return None
    region = address.get("stateOrCountryDescription") or address.get("stateOrCountry")
    return ", ".join(part for part in (address["city"].strip(), region) if part)


def parse_submissions(submissions):
    """Normalize registrant facts from the submissions JSON."""

    business = (submissions.get("addresses") or {}).get("business") or {}
    state = (business.get("stateOrCountry") or "").upper()
    domestic = business.get("isForeignLocation") == 0 or (
        business.get("isForeignLocation") is None and state in US_STATES
    )
    incorporation = (submissions.get("stateOfIncorporation") or "").upper()
    facts = {
        "industry": clean_text(submissions.get("sicDescription")),
        "headquarters": _headquarters(business),
        "country": "US" if domestic and state in US_STATES else None,
        "jurisdiction": f"US-{incorporation}" if incorporation in US_STATES else None,
        "website": clean_text(submissions.get("website")),
    }
    metadata = {
        "cik": submissions.get("cik"),
        "registrant_name": submissions.get("name"),
        "sic": submissions.get("sic") or None,
        "tickers": submissions.get("tickers") or [],
        "exchanges": submissions.get("exchanges") or [],
        "fiscal_year_end": submissions.get("fiscalYearEnd") or None,
        "former_names": [item.get("name") for item in submissions.get("formerNames") or []],
    }
    return {key: value for key, value in facts.items() if value}, metadata


def _iter_instance(text, namespaces):
    """Yield completed elements, recording each prefix's first namespace URI in ``namespaces``."""

    for event, item in ET.iterparse(io.StringIO(text), events=("start-ns", "end")):
        if event == "start-ns":
            prefix, uri = item
            namespaces.setdefault(prefix, uri)
        else:
            yield item


def _is_standard(namespaces, qname):
    prefix = qname.split(":", 1)[0]
    host = urlsplit(namespaces.get(prefix, "")).hostname or ""
    return any(host == h or host.endswith(f".{h}") for h in STANDARD_HOSTS)


def instance_members(text):
    """Members used on segment/product axes in contexts of the report's own fiscal period."""

    period_end = None
    contexts = []
    namespaces = {}
    for element in _iter_instance(text, namespaces):
        tag = element.tag
        if tag.endswith("}DocumentPeriodEndDate") and element.text:
            period_end = element.text.strip()[:10]
        elif tag == f"{XBRLI}context":
            end = element.findtext(f"{XBRLI}period/{XBRLI}endDate") or element.findtext(
                f"{XBRLI}period/{XBRLI}instant"
            )
            members = [
                (member.get("dimension", ""), (member.text or "").strip())
                for member in element.iter(f"{XBRLDI}explicitMember")
            ]
            if members:
                contexts.append(((end or "").strip()[:10], members))
            element.clear()
    found = {"business_areas": set(), "key_products": set()}
    for end, members in contexts:
        if period_end and end != period_end:
            continue
        for dimension, member in members:
            axis = dimension.split(":", 1)[-1]
            field = (
                "business_areas"
                if axis in SEGMENT_AXES
                else "key_products"
                if axis in PRODUCT_AXES
                else None
            )
            if field is None or not _is_standard(namespaces, dimension) or ":" not in member:
                continue
            local = member.split(":", 1)[1]
            if _is_standard(namespaces, member) and local in STRUCTURAL_MEMBERS:
                continue
            found[field].add(member)
    return period_end, found


def member_labels(text):
    """Map concept QName (``prefix:Name``) to its terse (else standard) English label."""

    root = ET.fromstring(text)
    labels = {}
    for link in root.iter(f"{LINK}labelLink"):
        locators = {}
        resources = {}
        arcs = []
        for child in link:
            kind = child.get(f"{XLINK}type")
            if kind == "locator":
                fragment = child.get(f"{XLINK}href", "").rsplit("#", 1)[-1]
                locators[child.get(f"{XLINK}label")] = fragment
            elif kind == "resource":
                if not (child.get(XML_LANG) or "en").lower().startswith("en"):
                    continue
                role = child.get(f"{XLINK}role")
                resources.setdefault(child.get(f"{XLINK}label"), {})[role] = child.text or ""
            elif kind == "arc":
                arcs.append((child.get(f"{XLINK}from"), child.get(f"{XLINK}to")))
        for source, target in arcs:
            concept = locators.get(source)
            roles = resources.get(target, {})
            text_value = roles.get(TERSE_ROLE) or roles.get(LABEL_ROLE)
            if concept and text_value and "_" in concept:
                prefix, name = concept.split("_", 1)
                labels.setdefault(f"{prefix}:{name}", {}).update(
                    {role: value for role, value in roles.items() if value}
                )
    return {
        qname: _MEMBER_SUFFIX.sub(
            "", " ".join((roles.get(TERSE_ROLE) or roles[LABEL_ROLE]).split())
        )
        for qname, roles in labels.items()
        if roles.get(TERSE_ROLE) or roles.get(LABEL_ROLE)
    }


def xbrl_lists(instance_text, label_text):
    """Return (period_end, {field: sorted labels}, truncated counts)."""

    period_end, found = instance_members(instance_text)
    labels = member_labels(label_text)
    lists, truncated = {}, {}
    for field, members in found.items():
        names = sorted({labels[m] for m in members if labels.get(m)}, key=str.casefold)
        if len(names) > LIST_LIMIT:
            truncated[field] = len(names) - LIST_LIMIT
        lists[field] = names[:LIST_LIMIT]
    return period_end, lists, truncated


def _xbrl_files(names):
    """(instance, labels): some filing agents embed the label linkbase in the schema."""

    instance = next((n for n in names if n.endswith("_htm.xml")), None)
    schemas = [n for n in names if n.endswith(".xsd")]
    if instance is None and schemas:
        candidate = schemas[0][:-4] + ".xml"
        instance = candidate if candidate in names else None
    if not schemas:
        return instance, None
    base = schemas[0][:-4]
    return instance, f"{base}_lab.xml" if f"{base}_lab.xml" in names else schemas[0]


class SecEdgarProfileAdapter:
    def __init__(self, http, definition, now=None, interval=REQUEST_INTERVAL):
        self.http = http
        self.definition = definition
        self.now = now or (lambda: datetime.now(UTC))
        self.interval = interval
        self.failed_count = 0
        self._requests = 0

    async def _get(self, url):
        if self._requests:
            await asyncio.sleep(self.interval)
        self._requests += 1
        return await self.http.get(url)

    async def fetch(self, target) -> ExternalCompanyProfile:
        retrieved_at = self.now()
        cik = target.cik.zfill(10)
        submissions = (await self._get(SUBMISSIONS_URL.format(cik=cik))).json()
        if str(submissions.get("cik", "")).zfill(10) != cik:
            raise ValueError("SEC submissions returned a different CIK")
        facts, metadata = parse_submissions(submissions)
        filing = latest_annual_filing(submissions)
        published_at = None
        if filing:
            archive = ARCHIVE_URL.format(
                cik=int(cik), accession=filing["accession"].replace("-", "")
            )
            filing["url"] = archive + (filing.pop("primary_document") or "")
            published_at = _date(filing["filing_date"])
            metadata["annual_report"] = filing
            metadata["xbrl"] = await self._xbrl(archive, facts)
        else:
            metadata["xbrl"] = {"status": "no_annual_report"}
        return ExternalCompanyProfile(
            source_key=self.definition.key,
            source_url=COMPANY_URL.format(cik=cik),
            retrieved_at=retrieved_at,
            facts=facts,
            identities=((CIK_SCHEME, cik),),
            published_at=published_at,
            metadata=metadata,
        )

    async def _xbrl(self, archive, facts):
        index = (await self._get(archive + "index.json")).json()
        names = [item["name"] for item in index.get("directory", {}).get("item", [])]
        instance, label = _xbrl_files(names)
        if not instance or not label:
            self.failed_count += 1
            return {"status": "unavailable"}
        instance_text = (await self._get(archive + instance)).text
        label_text = (await self._get(archive + label)).text
        try:
            period_end, lists, truncated = xbrl_lists(instance_text, label_text)
        except ET.ParseError:
            self.failed_count += 1
            return {"status": "malformed", "instance": archive + instance}
        for field, values in lists.items():
            if values:
                facts[field] = values
        return {
            "status": "parsed",
            "instance": archive + instance,
            "period_end": period_end,
            "truncated": truncated,
        }
