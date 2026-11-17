#!/usr/bin/env python3
"""Ingest real literature from NCBI PubMed / PMC OA into the evidence store.

Why PubMed/PMC: Europe PMC was unreachable from this host; NCBI E-utilities
and the PMC open-access subset work. Documents without a PMC full text are
stored at abstract level and the coverage report says so.

Rate limits: without NCBI_API_KEY, 3 requests/s; we default to 0.4 s between
requests. Set NCBI_API_KEY to go faster, NCBI_EMAIL to identify yourself.

Examples:
    python scripts/pubmed_ingest.py --db data/evidence.sqlite \
        --query 'miR-21[Title/Abstract] AND PTEN[Title/Abstract]' \
        --max-records 100 --fulltext

    python scripts/pubmed_ingest.py --db data/evidence.sqlite \
        --query '("microRNA"[Title/Abstract] AND targeting[Title/Abstract])' \
        --max-records 300 --fulltext --pairs pairs.tsv

    # pairs.tsv: miRNA<TAB>gene<TAB>optional species
    hsa-miR-21-5p	PTEN	hsa

`pairs.tsv`: two or three tab-separated columns, miRNA, gene, and an optional
species code (for example ``hsa``). For each sentence that contains both
boundary-matched names and a targeting cue ("targets", "represses", ...), a
rule-based relation is stored (method=text-mining, confidence=implied).
Species conflicts, unsupported species, and mixed species markers prevent
promotion. Relations are deliberately explainable, not a black box.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rnaharness.entity.naming import (  # noqa: E402
    gene_candidates,
    mirna_candidates,
    parse_mirna_name,
)
from rnaharness.evidence import EvidenceStore  # noqa: E402

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
USER_AGENT = "RNAHarness/0.1 (research; mailto:%s)" % (
    os.environ.get("NCBI_EMAIL") or "noreply@example.org")
CUE = re.compile(
    r"\b(targets?|targeting|repress(?:es|ed|ion)?|inhibits?|inhibit(?:ion)?|"
    r"suppress(?:es|ed|ion)?|down-?regulat(?:es|ed|ion)?|"
    r"reduc(?:es|ed|ing)?|binds?)\b",
    re.IGNORECASE,
)
NEGATION = re.compile(
    r"\b(?:not|never|no|none|neither|nor|cannot|can't|didn't|doesn't|"
    r"failed\s+to|fail\s+to|without)\b",
    re.IGNORECASE,
)
SPECULATION = re.compile(
    r"\b(?:may|might|could|possibly|potentially|presumably|likely|"
    r"suggest(?:s|ed|ing)?|appears?\s+to|seems?\s+to|is\s+thought\s+to)\b",
    re.IGNORECASE,
)
SPECIES_TAX = {"hsa": "9606", "mmu": "10090", "rno": "10116"}
SPECIES_ALIASES = {
    "human": "hsa", "homo sapiens": "hsa", "9606": "hsa",
    "mouse": "mmu", "mice": "mmu", "mus musculus": "mmu", "10090": "mmu",
    "rat": "rno", "rats": "rno", "rattus norvegicus": "rno", "10116": "rno",
}
SPECIES_MARKERS = {
    "hsa": re.compile(
        r"(?:\b(?:human|homo\s+sapiens|hek293|hek-293|hela|hepg2)\b|\bhsa-)",
        re.IGNORECASE),
    "mmu": re.compile(
        r"(?:\b(?:mouse|mice|murine|mus\s+musculus|nih3t3|3t3)\b|\bmmu-)",
        re.IGNORECASE),
    "rno": re.compile(r"(?:\b(?:rat|rats|rattus\s+norvegicus)\b|\brno-)",
                      re.IGNORECASE),
}


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def eutils(endpoint: str, params: dict, retries: int = 4) -> bytes:
    if os.environ.get("NCBI_API_KEY"):
        params = {**params, "api_key": os.environ["NCBI_API_KEY"]}
    if os.environ.get("NCBI_EMAIL"):
        params = {**params, "email": os.environ["NCBI_EMAIL"]}
    url = EUTILS + endpoint + "?" + urllib.parse.urlencode(params)
    delay = 0.5
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            if attempt == retries - 1:
                raise
            print(f"[retry] {endpoint}: {exc}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def esearch(query: str, max_records: int, batch: int = 200) -> list[str]:
    ids: list[str] = []
    retstart = 0
    while len(ids) < max_records:
        size = min(batch, max_records - len(ids))
        data = eutils("esearch.fcgi", {
            "db": "pubmed", "term": query, "retmode": "json",
            "retmax": str(size), "retstart": str(retstart)})
        payload = json.loads(data)
        page = payload.get("esearchresult", {}).get("idlist", [])
        ids.extend(page)
        retstart += len(page)
        if len(page) < size:
            break
        time.sleep(0.4)
    return ids[:max_records]


# --------------------------------------------------------------------------
# parsing
# --------------------------------------------------------------------------
def _text(elem) -> str:
    return "".join(elem.itertext()).strip() if elem is not None else ""


def parse_pubmed(xml_bytes: bytes) -> list[dict]:
    root = ET.fromstring(xml_bytes)
    docs = []
    for article in root.iter("PubmedArticle"):
        pmid = _text(article.find(".//PMID"))
        title = _text(article.find(".//ArticleTitle"))
        journal = _text(article.find(".//Journal/Title"))
        year = _text(article.find(".//PubDate/Year")) or _text(
            article.find(".//PubDate/MedlineDate"))[:4]
        doi, pmcid = "", ""
        for aid in article.iter("ArticleId"):
            kind = aid.get("IdType")
            if kind == "doi":
                doi = _text(aid)
            elif kind == "pmc":
                pmcid = _text(aid)
        abstract_parts = []
        for node in article.iter("AbstractText"):
            label = node.get("Label")
            text = _text(node)
            if text:
                abstract_parts.append(f"{label}: {text}" if label else text)
        abstract = "\n".join(abstract_parts)
        docs.append({
            "doc_id": f"PMID{pmid}", "pmid": pmid, "pmcid": pmcid, "doi": doi,
            "title": title, "journal": journal, "year": year,
            "abstract": abstract, "source": "pubmed",
        })
    return docs


def _walk_sections(elem, section: str, out: list[tuple[str, str]]) -> None:
    for child in elem:
        tag = child.tag
        if tag == "sec":
            title = _text(child.find("title")) or section
            _walk_sections(child, title, out)
        elif tag == "abstract":
            _walk_sections(child, "abstract", out)
        elif tag == "p":
            text = _text(child)
            if text:
                out.append((section, text))


def parse_pmc(xml_bytes: bytes, doc: dict) -> dict | None:
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None
    article = root.find(".//article")
    if article is None:
        return None
    title = _text(article.find(".//article-title")) or doc.get("title", "")
    body = article.find(".//body")
    abstract = article.find(".//abstract")
    passages: list[tuple[str, str]] = []
    if abstract is not None:
        _walk_sections(abstract, "abstract", passages)
    if body is not None:
        _walk_sections(body, "body", passages)
    if not passages:
        return None
    licenses = [_text(node) for node in article.iter("license-p")]
    license_text = licenses[0] if licenses else "unknown"
    return {
        "title": title, "passages": passages, "license": license_text,
        "source": "pmc", "pmcid": doc.get("pmcid", ""),
    }


# --------------------------------------------------------------------------
# relation rules
# --------------------------------------------------------------------------
def license_tag(text: str) -> str:
    """Turn a full license paragraph into a short, reportable tag."""
    t = (text or "").strip()
    low = t.lower()
    if not t or low.isdigit():
        return "unknown"
    if "abstract-only" in low:
        return "abstract-only"
    if "creative commons" in low or "cc by" in low or "cc-by" in low:
        parts = ["CC BY"]
        if "noncommercial" in low or "non-commercial" in low or "-nc" in low:
            parts.append("NC")
        if "noderiv" in low or "no derivative" in low or "-nd" in low:
            parts.append("ND")
        if "sharealike" in low or "share-alike" in low or "-sa" in low:
            parts.append("SA")
        version = re.search(r"\b(\d\.\d)\b", t)
        return "-".join(parts) + (f" {version.group(1)}" if version else "")
    if "open access" in low or "open-access" in low:
        return "open-access"
    return "other"


def _species_code(value: str | None) -> str | None:
    key = (value or "").strip().lower()
    return key if key in SPECIES_TAX else SPECIES_ALIASES.get(key)


def load_pairs(path: Path) -> list[tuple[str, ...]]:
    """Load legacy two-column or species-aware three-column pair TSVs."""
    pairs: list[tuple[str, ...]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
            if parts[0].lower() in ("mirna", "mir"):
                continue
            pair = (parts[0].strip(), parts[1].strip())
            if len(parts) >= 3 and parts[2].strip():
                pair += (parts[2].strip(),)
            pairs.append(pair)
    return pairs


def _entity_pattern(term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(term)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )


def _sentences(text: str):
    start = 0
    for match in re.finditer(r"[.!?]+(?=\s+|$)", text):
        end = match.end()
        sentence = text[start:end].strip()
        if sentence:
            yield start, sentence
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    tail = text[start:].strip()
    if tail:
        yield start, tail


def _text_species(sentence: str) -> set[str]:
    return {
        code for code, pattern in SPECIES_MARKERS.items()
        if pattern.search(sentence)
    }


def _relation_clauses(sentence: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(
            r"(?:;\s*|,\s*(?:while|whereas|but|although)\s+|"
            r"\b(?:while|whereas)\b)",
            sentence,
            flags=re.IGNORECASE,
        )
        if clause.strip()
    ]


def extract_relations(passages: list[dict], pairs: list[tuple[str, ...]]) -> list[dict]:
    """Extract explainable sentence-level targeting relations.

    Two-element pairs remain supported. A third element may name the species.
    The species is inferred from an explicit miRNA prefix or from a unique text
    marker only when the pair itself does not provide one. Conflicting species
    evidence is skipped instead of being guessed.
    """
    out: list[dict] = []
    for passage in passages:
        text = passage["text"]
        for sentence_start, sentence in _sentences(text):
            if not CUE.search(sentence):
                continue
            markers = _text_species(sentence)
            for pair in pairs:
                if len(pair) < 2:
                    continue
                mirna, gene = pair[0], pair[1]
                explicit_species = (
                    pair[2].strip() if len(pair) >= 3 and pair[2].strip() else None
                )
                pair_species = _species_code(explicit_species)
                if explicit_species and not pair_species:
                    continue
                parsed = parse_mirna_name(mirna)
                mirna_species = _species_code(parsed.get("species"))
                if pair_species and mirna_species and pair_species != mirna_species:
                    continue
                species = pair_species or mirna_species
                if markers:
                    if len(markers) != 1:
                        continue
                    marker_species = next(iter(markers))
                    if species and species != marker_species:
                        continue
                    species = marker_species
                if not species:
                    continue
                attributed_clause = None
                for clause in _relation_clauses(sentence):
                    if (
                        CUE.search(clause)
                        and _entity_pattern(mirna).search(clause)
                        and _entity_pattern(gene).search(clause)
                    ):
                        attributed_clause = clause
                        break
                if attributed_clause is None:
                    continue
                if SPECULATION.search(attributed_clause):
                    polarity = "speculative"
                elif NEGATION.search(attributed_clause):
                    polarity = "negative"
                else:
                    polarity = "positive"
                rel_key = (
                    f"{mirna}|{gene}|{species}|{passage['passage_id']}|"
                    f"{sentence_start}"
                )
                rel_id = hashlib.sha1(rel_key.encode()).hexdigest()[:16]
                out.append({
                    "relation_id": rel_id,
                    "mirna_id": mirna,
                    "gene_id": gene,
                    "relation_type": "targeting",
                    "method": "text-mining",
                    "doc_id": passage["doc_id"],
                    "passage_id": passage["passage_id"],
                    "confidence": "implied",
                    "extracted_by": "rules-v1",
                    "species": species,
                    "tax_id": SPECIES_TAX[species],
                    "mirna_arm": parsed.get("arm"),
                    "polarity": polarity,
                    "experimental_method": None,
                    "curation_status": "unreviewed",
                    "extraction_method": "text-mining",
                })
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="data/evidence.sqlite")
    ap.add_argument("--query", required=True)
    ap.add_argument("--max-records", type=int, default=50)
    ap.add_argument("--fulltext", action="store_true",
                    help="fetch PMC OA full text when a PMCID exists")
    ap.add_argument("--pairs", help="TSV with miRNA and gene for rule-based relations")
    ap.add_argument("--snapshot", default=time.strftime("%Y-%m-%d"))
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    pmids = esearch(args.query, args.max_records)
    print(f"[search] {len(pmids)} PMIDs", file=sys.stderr)
    if args.dry_run:
        print(json.dumps({"pmids": pmids, "count": len(pmids)}, ensure_ascii=False))
        return 0

    store = EvidenceStore.open(args.db, create=True)
    pairs = load_pairs(Path(args.pairs)) if args.pairs else []
    if pairs:
        store.require_v2_relation_support()
    counts = {"documents": 0, "fulltext": 0, "abstract_only": 0,
              "passages": 0, "relations": 0, "licenses": {},
              "sources": {"pubmed": 0, "pmc": 0}}
    try:
        for i in range(0, len(pmids), 50):
            batch = pmids[i:i + 50]
            xml = eutils("efetch.fcgi", {
                "db": "pubmed", "id": ",".join(batch), "retmode": "xml"})
            time.sleep(args.sleep)
            for doc in parse_pubmed(xml):
                record = {
                    "doc_id": doc["doc_id"], "pmid": doc["pmid"],
                    "pmcid": doc["pmcid"], "doi": doc["doi"],
                    "title": doc["title"], "journal": doc["journal"],
                    "year": int(doc["year"]) if doc["year"].isdigit() else None,
                    "license": "abstract-only", "snapshot": args.snapshot,
                    "source": "pubmed",
                }
                passages: list[dict] = []
                if args.fulltext and doc["pmcid"]:
                    try:
                        pmc_xml = eutils("efetch.fcgi", {
                            "db": "pmc", "id": doc["pmcid"], "retmode": "xml"})
                        time.sleep(args.sleep)
                        parsed = parse_pmc(pmc_xml, doc)
                    except Exception as exc:  # network/parse: keep abstract
                        print(f"[warn] {doc['pmcid']}: {exc}", file=sys.stderr)
                        parsed = None
                    if parsed:
                        record["source"] = "pmc"
                        record["license"] = license_tag(parsed["license"])
                        record["title"] = parsed["title"] or record["title"]
                        offset = 0
                        for j, (section, text) in enumerate(parsed["passages"], 1):
                            pid = f"{doc['doc_id']}:{section[:40]}:{j}"
                            passages.append({
                                "passage_id": pid, "doc_id": doc["doc_id"],
                                "section": section, "text": text,
                                "char_start": offset,
                                "char_end": offset + len(text)})
                            offset += len(text) + 2
                        counts["fulltext"] += 1
                if not passages:
                    if doc["abstract"]:
                        passages.append({
                            "passage_id": f"{doc['doc_id']}:abstract:1",
                            "doc_id": doc["doc_id"], "section": "abstract",
                            "text": doc["abstract"], "char_start": 0,
                            "char_end": len(doc["abstract"])})
                    counts["abstract_only"] += 1

                store.upsert_document(record)
                for passage in passages:
                    store.add_passage(passage["passage_id"], passage["doc_id"],
                                      passage["section"], passage["text"],
                                      passage["char_start"], passage["char_end"])
                counts["documents"] += 1
                counts["sources"][record["source"]] = \
                    counts["sources"].get(record["source"], 0) + 1
                counts["passages"] += len(passages)
                counts["licenses"][record["license"]] = \
                    counts["licenses"].get(record["license"], 0) + 1
                if pairs:
                    for rel in extract_relations(passages, pairs):
                        store.add_relation(rel)
                        counts["relations"] += 1
                store.commit()

        for source, count in counts["sources"].items():
            if count:
                store.set_meta(f"source:{source}", str(count))
        store.set_meta("snapshot:pubmed", args.snapshot)
        store.set_meta("query:last", args.query)
        store.commit()
    finally:
        coverage = store.coverage()
        store.close()

    print(json.dumps({"ingested": counts, "store": coverage},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
