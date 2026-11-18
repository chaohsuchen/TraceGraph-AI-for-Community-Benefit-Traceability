"""Score commitment extractions (B0, B1, ...) against the Golden Dataset.

Matching. Gold quotes are located in each document's canonical text (see
``corpus``) by comparing alphanumeric characters only; the workbook's own
``char_start``/``char_end`` come from a different text extraction and are not
reused. A prediction matches a gold commitment when it covers at least half of
the gold span (``lenient``) or when span IoU is at least 0.5 (``strict``).
Matching is one-to-one and greedy by overlap.

Caveat. The gold set samples commitments; it does not exhaustively label every
commitment in a document. Unmatched predictions are therefore counted as false
positives, which makes precision a lower bound. ``annotated_pages`` precision
restricts predictions to pages that carry at least one gold commitment.
"""

from collections import Counter, defaultdict
import re

from .b0_rules import OBLIGATION_CLASSES


GOLD_SHEET = "gold commitments"
ATTRIBUTE_FIELDS = (
    "responsible_entity",
    "amount_value",
    "term_periodicity",
    "geographic_scope",
    "beneficiary_category",
    "beneficiary_named",
    "condition_contingency",
    "milestone",
)
CLASS_FIELDS = ("obligation_type", "evidence_status")
EVIDENCE_STATUSES = (
    "corroborated",
    "partially corroborated",
    "self-reported only",
    "conflicting",
    "no public evidence located",
    "source unavailable",
    "not yet due",
    "not applicable",
)
HELDOUT_APPLICANTS = ("Bally’s Corporation", "Queens Future, LLC d/b/a Hard Rock Metropolitan Park")
EMPTY = re.compile(r"^(?:unspecified|none(?:\s+(?:stated|named))?|not\s+stated|n/?a|null|-+)?\b", re.IGNORECASE)
STOPWORDS = frozenset(
    "a an the of to for in on and or by with from at as is are be its their this that these those per each "
    "applies generic incl including d b llc".split()
)


# --- Gold loading ----------------------------------------------------------

def load_gold(workbook_path):
    """Return gold commitments grouped by gold_id (duplicate rows merged)."""
    from openpyxl import load_workbook

    sheet = load_workbook(workbook_path, read_only=True, data_only=True)[GOLD_SHEET]
    rows = sheet.iter_rows(values_only=True)
    header = next(rows)
    grouped = {}
    for row in rows:
        record = dict(zip(header, row))
        gold_id = record.get("gold_id")
        if not gold_id:
            continue
        validated = record.get("manually annotated valid?") in (1, 1.0, True)
        if gold_id not in grouped:
            grouped[gold_id] = {
                "gold_id": gold_id,
                "document_id": record["document_id"],
                "page_section": record["page_section"],
                "commitment_text": record["commitment_text"],
                "evaluation_split": record["evaluation_split"],
                "duplicate_of": record.get("duplicate_of"),
                "human_validated": validated,
                "labels": {name: [] for name in ATTRIBUTE_FIELDS + CLASS_FIELDS},
            }
        item = grouped[gold_id]
        item["human_validated"] = item["human_validated"] or validated
        for name in item["labels"]:
            value = "" if record.get(name) is None else str(record[name]).strip()
            if value not in item["labels"][name]:
                item["labels"][name].append(value)
    return list(grouped.values())


def _alnum_index(text):
    keep = [i for i, ch in enumerate(text) if ch.isalnum()]
    return "".join(text[i].lower() for i in keep), keep


def _alnum_key(text):
    return "".join(ch.lower() for ch in text.replace("’", "'") if ch.isalnum())


def align_gold(gold, documents):
    """Attach a canonical-text span to each gold item (None if not locatable)."""
    indexes = {doc.document_id: (doc, *_alnum_index(doc.text.replace("’", "'"))) for doc in documents}
    for item in gold:
        item["span"] = None
        entry = indexes.get(item["document_id"])
        if not entry:
            continue
        doc, key_text, positions = entry
        needle = _alnum_key(item["commitment_text"])
        at = key_text.find(needle) if needle else -1
        if at < 0:
            continue
        start, end = positions[at], positions[at + len(needle) - 1] + 1
        item["span"] = (start, end)
        item["page"] = doc.page_at(start)
    return gold


# --- Matching --------------------------------------------------------------

def _overlap(a, b):
    return max(0, min(a[1], b[1]) - max(a[0], b[0]))


def match(predictions, gold, mode="lenient"):
    """One-to-one greedy matching. Returns {gold_id: prediction}."""
    candidates = []
    for g in gold:
        if not g.get("span"):
            continue
        g_len = g["span"][1] - g["span"][0]
        for p in predictions:
            if p["document_id"] != g["document_id"]:
                continue
            p_span = (p["char_start"], p["char_end"])
            inter = _overlap(g["span"], p_span)
            if not inter:
                continue
            coverage = inter / g_len
            iou = inter / (g_len + (p_span[1] - p_span[0]) - inter)
            ok = coverage >= 0.5 if mode == "lenient" else iou >= 0.5
            if ok:
                candidates.append((coverage, iou, g["gold_id"], p["pred_id"], p))
    candidates.sort(key=lambda c: (-c[0], -c[1], c[2], c[3]))
    used_g, used_p, pairs = set(), set(), {}
    for _, _, gid, pid, p in candidates:
        if gid in used_g or pid in used_p:
            continue
        used_g.add(gid)
        used_p.add(pid)
        pairs[gid] = p
    return pairs


# --- Value normalization and agreement --------------------------------------

def is_empty(value):
    value = (value or "").strip()
    if not value:
        return True
    head = EMPTY.match(value)
    return bool(head and head.group(0)) and not re.search(r"\d", value[head.end():])


def _tokens(value):
    value = value.lower().replace("’", "'").replace("%", " percent ")
    value = re.sub(r"(?<=\d),(?=\d{3})", "", value)
    out = []
    for token in re.findall(r"\d+(?:\.\d+)?|[a-z]+", value):
        if token in STOPWORDS:
            continue
        if len(token) > 3 and token.endswith("s") and not token.isdigit():
            token = token[:-1]
        out.append(token)
    return out


def _numbers(value):
    value = re.sub(r"(?<=\d),(?=\d{3})", "", value.lower())
    return set(re.findall(r"\d+(?:\.\d+)?", value))


def _f1(a, b):
    a, b = Counter(a), Counter(b)
    common = sum((a & b).values())
    if not common:
        return 0.0
    precision, recall = common / sum(a.values()), common / sum(b.values())
    return 2 * precision * recall / (precision + recall)


def token_f1(pred, gold):
    return _f1(_tokens(pred), _tokens(gold))


def agree(field, pred, gold):
    """True when a non-empty prediction matches a non-empty gold value."""
    if field in CLASS_FIELDS:
        return pred.strip().lower() == gold.strip().lower()
    if field == "amount_value":
        gold_numbers, pred_numbers = _numbers(gold), _numbers(pred)
        if gold_numbers:
            return _f1(list(pred_numbers), list(gold_numbers)) >= 0.5
    pred_tokens, gold_tokens = _tokens(pred), _tokens(gold)
    if not pred_tokens or not gold_tokens:
        return False
    contained = sum((Counter(pred_tokens) & Counter(gold_tokens)).values()) / len(pred_tokens)
    return contained >= 0.8 or _f1(pred_tokens, gold_tokens) >= 0.5


# --- Metrics -----------------------------------------------------------------

def prf(tp, fp, fn):
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


def prediction_scope(prediction):
    if (prediction["document_id"] == "GFLB-SEL-2025-12-01"
            and prediction.get("section_applicant") in HELDOUT_APPLICANTS):
        return "heldout"
    return "anchor"


def gold_scope(item):
    return "heldout" if item["evaluation_split"].startswith("heldout") else "anchor"


def commitment_metrics(predictions, gold, pairs):
    matched_preds = {p["pred_id"] for p in pairs.values()}
    annotated = {(g["document_id"], g.get("page")) for g in gold if g.get("span")}
    out = {}
    for scope in ("all", "anchor", "heldout"):
        g_scope = [g for g in gold if scope == "all" or gold_scope(g) == scope]
        p_scope = [p for p in predictions if scope == "all" or prediction_scope(p) == scope]
        tp = sum(1 for g in g_scope if g["gold_id"] in pairs)
        fp = sum(1 for p in p_scope if p["pred_id"] not in matched_preds)
        out[scope] = prf(tp, fp, len(g_scope) - tp)
        p_pages = [p for p in p_scope if (p["document_id"], p["page"]) in annotated]
        fp_pages = sum(1 for p in p_pages if p["pred_id"] not in matched_preds)
        out[scope]["annotated_pages"] = prf(tp, fp_pages, len(g_scope) - tp)
    for split in sorted({g["evaluation_split"] for g in gold}):
        g_split = [g for g in gold if g["evaluation_split"] == split]
        tp = sum(1 for g in g_split if g["gold_id"] in pairs)
        out[f"recall_{split}"] = {"recall": round(tp / len(g_split), 4), "tp": tp, "gold": len(g_split)}
    validated = [g for g in gold if g["human_validated"]]
    tp = sum(1 for g in validated if g["gold_id"] in pairs)
    out["recall_human_validated"] = {"recall": round(tp / len(validated), 4) if validated else 0.0,
                                     "tp": tp, "gold": len(validated)}
    return out


def field_metrics(gold, pairs):
    """Slot-filling P/R/F1 per attribute on matched pairs.

    Precision = correct / predictions with a value; recall = correct / gold
    items with a value. ``presence`` scores only whether a value was stated.
    """
    out = {}
    matched = [g for g in gold if g["gold_id"] in pairs]
    for name in ATTRIBUTE_FIELDS:
        tp = fp = fn = 0
        p_tp = p_fp = p_fn = 0
        soft = []
        for g in matched:
            pred = pairs[g["gold_id"]].get(name, "")
            gold_values = [v for v in g["labels"][name] if not is_empty(v)]
            pred_set = not is_empty(pred)
            if pred_set and gold_values:
                p_tp += 1
                correct = any(agree(name, pred, v) for v in gold_values)
                tp += correct
                fp += not correct
                fn += not correct
                soft.append(max(token_f1(pred, v) for v in gold_values))
            elif pred_set:
                fp += 1
                p_fp += 1
            elif gold_values:
                fn += 1
                p_fn += 1
        out[name] = prf(tp, fp, fn)
        out[name]["presence"] = prf(p_tp, p_fp, p_fn)
        out[name]["mean_token_f1_when_both_present"] = round(sum(soft) / len(soft), 4) if soft else None
    out["macro_f1"] = round(sum(out[n]["f1"] for n in ATTRIBUTE_FIELDS) / len(ATTRIBUTE_FIELDS), 4)
    return out


def class_metrics(gold, pairs, name, labels):
    matched = [g for g in gold if g["gold_id"] in pairs]
    gold_label = {}
    for g in matched:
        pred = pairs[g["gold_id"]][name]
        accepted = g["labels"][name]
        gold_label[g["gold_id"]] = pred if pred in accepted else accepted[0]
    confusion = defaultdict(Counter)
    per_class = {}
    for g in matched:
        confusion[gold_label[g["gold_id"]]][pairs[g["gold_id"]][name]] += 1
    present = [label for label in labels if any(gold_label[g["gold_id"]] == label for g in matched)]
    for label in labels:
        tp = confusion[label][label]
        fp = sum(confusion[other][label] for other in confusion if other != label)
        fn = sum(confusion[label].values()) - tp
        per_class[label] = prf(tp, fp, fn)
    correct = sum(confusion[label][label] for label in confusion)
    return {
        "n": len(matched),
        "accuracy": round(correct / len(matched), 4) if matched else 0.0,
        "macro_f1": round(sum(per_class[l]["f1"] for l in present) / len(present), 4) if present else 0.0,
        "macro_f1_over": present,
        "per_class": per_class,
        "confusion": {gold: dict(row) for gold, row in confusion.items()},
    }


def evaluate(predictions, gold, splits=None):
    """Full report; ``splits`` restricts field/class scoring to those gold splits."""
    report = {"gold_items": len(gold), "gold_unaligned": [g["gold_id"] for g in gold if not g.get("span")],
              "predictions": len(predictions)}
    for mode in ("lenient", "strict"):
        pairs = match(predictions, gold, mode)
        section = {"commitment": commitment_metrics(predictions, gold, pairs)}
        groups = {
            "all": gold,
            "anchor": [g for g in gold if gold_scope(g) == "anchor"],
            "anchor_test": [g for g in gold if g["evaluation_split"] == "anchor_test"],
            "heldout": [g for g in gold if gold_scope(g) == "heldout"],
            "human_validated": [g for g in gold if g["human_validated"]],
        }
        for group, items in groups.items():
            if splits and group not in splits:
                continue
            section[group] = {
                "matched": sum(1 for g in items if g["gold_id"] in pairs),
                "attributes": field_metrics(items, pairs),
                "obligation_type": class_metrics(items, pairs, "obligation_type", OBLIGATION_CLASSES),
                "evidence_status": class_metrics(items, pairs, "evidence_status", EVIDENCE_STATUSES),
            }
        report[mode] = section
        report[mode]["_pairs"] = {gid: p["pred_id"] for gid, p in pairs.items()}
    return report
