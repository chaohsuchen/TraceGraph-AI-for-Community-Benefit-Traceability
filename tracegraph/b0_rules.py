"""B0 baseline: rule/pattern commitment extraction with no generative model.

Kickoff Pack §04: "B0 - Rules / patterns. Regex, lexical patterns,
deterministic parsing, no generative model. Establish a low-complexity floor
and identify fields that do not require AI."

The pipeline is: layout-aware sentence passages (``corpus``) -> commitment
trigger filter -> ordered obligation-class rules -> verifiability filters ->
regex/gazetteer field extractors. Every output carries passage-level
provenance.

Every rule added after looking at gold examples is a ``RuleSet`` switch. All
switches off is the *core* (``CORE_RULES``): only what follows from the
Kickoff Pack taxonomy and the documents' own structure. ``DEFAULT_RULES``
turns on the switches chosen by dev-F1 selection on the dev half of the
page-level split ``evaluation/splits/dev_test_split.json``, scoring every
workbook gold row as a positive. Several are written for single dev sentences
(the ``fit_*`` switches). See ``evaluation/b0_rule_audit.py``.
"""

from dataclasses import asdict, dataclass
import re


B0_VERSION = "b0-rules"
UNSPECIFIED = "unspecified"
NONE_STATED = "none stated"
NONE_NAMED = "none named"

OBLIGATION_CLASSES = (
    "Binding / license condition",
    "Contractual commitment",
    "Target / goal",
    "Best effort",
    "Forecast / projection",
    "Contingent commitment",
    "Reported accomplishment",
    "Unresolved / ambiguous",
)


def _rx(pattern):
    return re.compile(pattern, re.IGNORECASE)


# --- Commitment trigger lexicon -------------------------------------------

_TRIGGER_TEMPLATE = (
    r"\b(?:shall|must|will|would|agree[sd]?\s+to|{commit}|pledge[sd]?|promise[sd]?"
    r"|guarantee[sd]?|is\s+required|are\s+required|be\s+required|required\s+to|obligat\w+"
    r"|expect(?:s|ed)?\b|anticipate[sd]?|project(?:s|ed|ion)|estimate[sd]?|forecast"
    r"|target(?:s|ed)?|goals?\b|intends?\s+to|plans?\s+to|endeavou?r|best[\s-]efforts?"
    r"|reasonable\s+efforts?|has\s+offered|have\s+offered|offers?\s+to|proposes?\s+to"
    r"|in\s+place\b|has\s+(?:already\s+)?(?:provided|invested|contributed|donated|hired|created|completed|built|opened))"
)
TRIGGER = _rx(_TRIGGER_TEMPLATE.replace("{commit}", r"commit(?:s|ted)?\b"))
# H017: a benefits package described by what it includes or is worth.
PROPOSAL_TRIGGER = _rx(r"\b(?:has|have)\s+been\s+proposed\b|\benvisioned\s+to\b|\brequire\s+\S+\s+to\s+provide\b")
EXECUTED = _rx(r"\b(?:has|have)\s+(?:already\s+)?(?:executed|signed|entered\s+into)\b")
BENEFIT_PACKAGE_TRIGGER = _rx(r"\bbenefits?\b[^.;]{0,80}?\b(?:includ(?:e|es|ing)|valued\s+at|worth|total(?:s|l?ing))\b")

# A sentence whose acting subject is the evaluator/regulator.
REGULATOR_SUBJECT = _rx(
    r"^(?:\W*(?:the|this|a)\s+)?(?:board|commission|committee|staff|siting\s+board|gaming\s+commission|state|legislature)\b"
)


# --- Obligation-class rules (first match wins) -----------------------------

_CLASS_PATTERNS = (
    ("Contingent commitment", _rx(
        r"\bif\s+(?:the|a|an|it|such|any)\b|\bunless\b|\bcontingent\b|\bin\s+the\s+event\b|\blesser\s+of\b"
        r"|\bgreater\s+of\b|\bsubject\s+to\s+(?:the\s+)?(?:approval|availability|receipt|obtaining|financing)"
        r"|\bdepend(?:s|ent|ing)?\s+(?:up)?on\b|\bincremental\s+gross\s+gaming\s+revenue\b")),
    ("Reported accomplishment", _rx(
        r"\b(?:has|have)\s+(?:already\s+)?(?:provided|invested|contributed|donated|hired|created|completed|built|opened"
        r"|awarded|distributed|spent|funded|launched|established|employed)\b|\bin\s+place\s+with\b|\bto\s+date\b"
        r"|\bcurrently\s+(?:employs|has|provides|operates)\b|\b(?:agreements?|programs?)\s+(?:are\s+)?in\s+place\b")),
    ("Best effort", _rx(
        r"\bbest[\s-]efforts?\b|\b(?:commercially\s+)?reasonable\s+efforts?\b|\bgood[\s-]faith\s+efforts?\b")),
    ("Unresolved / ambiguous", _rx(
        r"\b(?:is|are)\s+expected\s+to\b|\bit\s+is\s+expected\b|\bendeavou?r\b|\b(?:is|are)\s+committed\s+to\b"
        r"|\bassum(?:ed|es|ing)\b|(?-i:\bmay\b)|\bshould\b|\bseeks?\s+to\b|\baspir\w+")),
    ("Unresolved / ambiguous (core)", _rx(
        r"\b(?:is|are)\s+expected\s+to\b|\bit\s+is\s+expected\b|\bendeavou?r\b|\b(?:is|are)\s+committed\s+to\b"
        r"|\bassum(?:ed|es|ing)\b|\bmay\b|\bshould\b|\bseeks?\s+to\b|\baspir\w+")),
    ("Binding / license condition", _rx(
        r"\bshall\b|\bmust\b|\b(?:is|are|be)\s+required\s+to\b|\brequired\s+to\b|\bmaterial\s+condition\b|\bobligated\b")),
    ("Target / goal", _rx(r"\btargets?\b|\btargeted\b|\bgoals?\b|\baims?\s+to\b|\bstrives?\s+to\b|\bobjectives?\b")),
    ("Forecast / projection", _rx(
        r"\b(?:expects?|expected|anticipates?|anticipated|projects\s+(?:that|to)|projected|projections?|estimates?|estimated"
        r"|forecasts?|forecasted)\b(?!\s+(?:the\s+)?(?:applicants?|licensees?)\b)|\bwould\s+(?:generate|create|produce|result)\b")),
    ("Forecast / projection (noun fix)", _rx(
        r"\b(?:expects?|expected|anticipates?|anticipated|projects\s+(?:that|to(?!\s+be\b))|projected|projections?|estimates?|estimated"
        r"|forecasts?|forecasted)\b(?!\s+(?:the\s+)?(?:applicants?|licensees?)\b)|\bwould\s+(?:generate|create|produce|result)\b")),
    ("Forecast / projection (core)", _rx(
        r"\b(?:expects?|expected|anticipates?|anticipated|project(?:s|ed|ions?)?|estimates?|estimated"
        r"|forecasts?|forecasted)\b(?!\s+(?:the\s+)?(?:applicants?|licensees?)\b)|\bwould\s+(?:generate|create|produce|result)\b")),
    ("Contractual commitment", _rx(
        r"\bcommit(?:s|ted)?\s+to\b|\bcommitments?\s+(?:of|to)\b|\bagree[sd]?\s+to\b|\bhas\s+agreed\b|\bpledge[sd]?\b"
        r"|\bpromise[sd]?\b|\bguarantee[sd]?\b|\b(?:has|have)\s+offered\b|\boffers?\b|\bwill\b|\bintends?\s+to\b"
        r"|\bplans?\s+to\b|\bproposes?\s+to\b")),
)
_PATTERN = dict(_CLASS_PATTERNS)
# Tuned order (A007, A052, A064): specific forms before generic modals.
TUNED_CLASS_ORDER = ("Contingent commitment", "Reported accomplishment", "Best effort", "Unresolved / ambiguous",
                     "Binding / license condition", "Target / goal", "Forecast / projection", "Contractual commitment")
# Core order: the taxonomy's own order.
CORE_CLASS_ORDER = OBLIGATION_CLASSES
CLASS_RULES = tuple((label, _PATTERN[label]) for label in TUNED_CLASS_ORDER)


def _class_rules(rules):
    order = TUNED_CLASS_ORDER if rules.tuned_class_order else CORE_CLASS_ORDER
    out = []
    for label in order:
        key = label
        if label == "Unresolved / ambiguous" and not rules.may_case_sensitive:
            key = "Unresolved / ambiguous (core)"
        if label == "Forecast / projection" and not rules.forecast_verb_only:
            key = "Forecast / projection (core)"
        elif label == "Forecast / projection" and rules.projects_to_be_is_noun:
            key = "Forecast / projection (noun fix)"
        if label == "Reported accomplishment" and rules.partnered_trigger:
            out.append((label, PARTNERED))
        if label == "Reported accomplishment" and rules.executed_trigger:
            out.append((label, EXECUTED))
        out.append((label, _PATTERN[key]))
    return out


def classify_obligation(text, document_id="", rules=None):
    """Return (obligation_class, matched_cue). Rules are ordered; first match wins."""
    rules = rules or DEFAULT_RULES
    if rules.fit_class_cues:
        for label, pattern in (("Unresolved / ambiguous", FIT_UNRESOLVED), ("Contractual commitment", FIT_CONTRACTUAL)):
            match = pattern.search(text)
            if match:
                return label, match.group(0)
    for label, pattern in _class_rules(rules):
        match = pattern.search(text)
        if match:
            return label, match.group(0)
    return "Unresolved / ambiguous", ""


# --- Entity gazetteer ------------------------------------------------------

RESORTS_WORLD = "Genting New York LLC d/b/a Resorts World New York City"
BALLYS = "Bally’s Corporation"
HARD_ROCK = "Queens Future, LLC d/b/a Hard Rock Metropolitan Park"
ENTITY_ALIASES = (
    (RESORTS_WORLD, _rx(r"\b(?:resorts\s+world(?:\s+new\s+york\s+city|\s+nyc)?|rwnyc|genting(?:\s+new\s+york)?)\b")),
    (BALLYS, _rx(r"\bbally[’']?s\b")),
    (HARD_ROCK, _rx(r"\bhard\s+rock\b|\bhrmp\b|\bmetropolitan\s+park\b|\bqueens\s+future\b")),
    ("Gaming Facility licensee", _rx(r"\b(?:gaming\s+facility\s+)?licensees?\b")),
    ("Applicant", _rx(r"\b(?:successful\s+|selected\s+|all\s+)?applicants?\b")),
)
SECTION_APPLICANTS = (
    (RESORTS_WORLD, _rx(r"^resorts\s+world\s+new\s+york\s+city$")),
    (BALLYS, _rx(r"^bally[’']?s\s+bronx$")),
    (HARD_ROCK, _rx(r"^hard\s+rock\s+metropolitan\s+park$")),
)
SECTION_RESET = _rx(r"^(?:conclusion|acknowledgements|introduction|evaluation|selection)$")
DOCUMENT_DEFAULT_ENTITY = {
    "NYSGC-RWNYC-LIC-2025-12-19": RESORTS_WORLD,
    "RFA-2023-01-03": "Applicant",
}


def responsible_entity(text, section_applicant, document_id):
    """Earliest named obligor in the sentence, else section/document default."""
    hits = []
    for canonical, pattern in ENTITY_ALIASES:
        match = pattern.search(text)
        if match:
            hits.append((match.start(), canonical))
    if hits:
        hits.sort()
        canonical = hits[0][1]
        if canonical in ("Applicant", "Gaming Facility licensee") and section_applicant:
            return section_applicant
        return canonical
    if section_applicant:
        return section_applicant
    return DOCUMENT_DEFAULT_ENTITY.get(document_id, UNSPECIFIED)


# --- Field patterns ----------------------------------------------------------

_NUM = r"\d[\d,]*(?:\.\d+)?"
_SCALE = r"(?:\s*(?:million|billion|thousand|[mb]n?)\b)?"
MONEY = _rx(rf"(?:(?:up\s+to|at\s+least|not\s+less\s+than|no\s+less\s+than|a\s+minimum\s+of|minimum\s+of|approximately|over|more\s+than)\s+)?\$\s?{_NUM}{_SCALE}(?:\s+(?:per|a|each)\s+year|\s+annually|\s+upfront)?")
PERCENT = _rx(rf"(?:(?:at\s+least|approximately|up\s+to|no\s+less\s+than|no\s+more\s+than|not\s+less\s+than)\s+)?{_NUM}\s?(?:percent|%)(?:\s+of\s+(?:the\s+)?(?:incremental\s+)?(?:gross\s+gaming\s+revenue|ggr|total\s+investment[\w\s]{{0,40}}?|the\s+workforce|[a-z]+))?")
COUNT = _rx(rf"\b{_NUM}\s+(?:full[\s-]time\s+equivalent\s+|permanent\s+|construction\s+|new\s+|affordable\s+)?(?:jobs|employees|workers|positions|fte|units|homes|apartments|acres|square\s+feet|seats|rooms|unions|scholarships)\b")
FORMULA = _rx(r"\b(?:lesser|greater)\s+of\b[^.;]{0,160}")

PERIODICITY = (
    ("annual", _rx(r"\bannual(?:ly)?\b|\bper\s+year\b|\beach\s+year\b|\bevery\s+year\b|\ba\s+year\b")),
    ("quarterly", _rx(r"\bquarterly\b|\beach\s+quarter\b")),
    ("monthly", _rx(r"\bmonthly\b|\beach\s+month\b")),
    ("one-time", _rx(r"\bone[\s-]time\b|\bupfront\b|\bup[\s-]front\b|\blump[\s-]sum\b")),
    ("for the license term", _rx(r"\b(?:for\s+the\s+duration\s+of|during|throughout)\s+the\s+(?:term\s+of\s+(?:the|a)\s+license|license\s+term)\b")),
    ("ongoing", _rx(r"\bongoing\b|\bcontinu(?:e|ing|ation)\b|\bat\s+all\s+times\b")),
    ("multi-year", _rx(r"\b\d+[\s-]years?\b|\bover\s+\d+\s+years\b")),
    ("construction period", _rx(r"\bduring\s+construction\b|\bconstruction\s+(?:period|phase|workforce|jobs)\b")),
    ("operations", _rx(r"\b(?:permanent\s+)?operations\b|\bonce\s+(?:the\s+facility\s+is\s+)?operational\b")),
)

_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
MILESTONE = _rx(
    rf"\b(?:on\s+or\s+before|no\s+later\s+than|by|before|prior\s+to|within|beginning|starting|commencing|until|in|from)\s+"
    rf"(?:{_MONTH}\s+\d{{1,2}},\s+\d{{4}}|{_MONTH}\s+\d{{4}}|(?:the\s+end\s+of\s+)?\d{{4}}|\d+\s+(?:days|months|years)[^.;,]{{0,60}}"
    rf"|the\s+(?:time\s+the\s+)?(?:gaming\s+)?facility\s+opens|the\s+opening[^.;,]{{0,40}}|(?:the\s+)?commencement\s+of\s+[^.;,]{{0,40}})"
    rf"|\bopen\s+in\s+{_MONTH}\s+\d{{4}}|\bcompletion\s+(?:of\s+[\w\s]{{0,30}}?)?in\s+\d{{4}}"
)

CONDITION = _rx(
    r"\b(?:if|unless|provided\s+that|subject\s+to|contingent\s+(?:up)?on|conditioned\s+(?:up)?on|in\s+the\s+event|upon"
    r"|depend(?:s|ent|ing)\s+(?:up)?on|lesser\s+of|greater\s+of)\b[^.;]{0,140}?(?=[,.;]|$)"
)

GEOGRAPHY = (
    ("Southeast Queens", _rx(r"\bsoutheast(?:ern)?\s+queens\b")),
    ("South Ozone Park", _rx(r"\bsouth\s+ozone\s+park\b")),
    ("Ozone Park", _rx(r"(?<!south\s)\bozone\s+park\b")),
    ("Jamaica", _rx(r"\bjamaica\b")),
    ("Aqueduct", _rx(r"\baqueduct\b")),
    ("Willets Point", _rx(r"\bwillets\s+point\b")),
    ("Flushing", _rx(r"\bflushing\b")),
    ("Corona", _rx(r"\bcorona\b")),
    ("Ferry Point", _rx(r"\bferry\s+point\b")),
    ("Queens", _rx(r"\bqueens\b(?!\s+future)")),
    ("Bronx", _rx(r"\bbronx\b")),
    ("Brooklyn", _rx(r"\bbrooklyn\b")),
    ("Manhattan", _rx(r"\bmanhattan\b")),
    ("Staten Island", _rx(r"\bstaten\s+island\b")),
    ("New York City", _rx(r"\bnew\s+york\s+city\b(?<!resorts\sworld\snew\syork\scity)|\bnyc\b|\bthe\s+city\b")),
    ("New York State", _rx(r"\bnew\s+york\s+state\b|\bthe\s+state\b|\bstatewide\b|\bnew\s+york[’']s\b")),
    ("Host Municipality", _rx(r"\bhost\s+municipalit(?:y|ies)\b")),
    ("Host community", _rx(r"\bhost\s+communit(?:y|ies)\b|\blocal\s+communit(?:y|ies)\b|\bsurrounding\s+(?:community|communities|neighborhoods?)\b")),
)

BENEFICIARY_CATEGORIES = (
    ("housing", _rx(r"\bhousing\b|\baffordable\s+(?:units|homes|apartments)\b|\bhomeowner")),
    ("workforce/jobs", _rx(r"\bworkforce\b|\bjobs?\b|\bemploy\w*\b|\bhiring\b|\bworkers?\b|\blabor\b|\bunions?\b|\bapprentice\w*")),
    ("MWBE firms", _rx(r"\bmwbe\b|\bminority[\s-]and\s+women[\s-]owned\b|\bwomen[\s-]owned\b|\bminority[\s-]owned\b")),
    ("service-disabled veterans", _rx(r"\bservice[\s-]disabled\s+veteran|\bveterans?\b")),
    ("women; minorities", _rx(r"\bwomen\b|\bminorities\b|\bdiversity\b|\bdiverse\b")),
    ("small / local business", _rx(r"\bsmall\s+business|\blocal\s+business|\bvendors?\b|\bprocurement\b")),
    ("education", _rx(r"\beducation\w*\b|\bschools?\b|\bscholarships?\b|\bcollege\b|\buniversity\b|\btraining\b|\binnovation\s+campus\b")),
    ("childcare", _rx(r"\bchild\s?care\b")),
    ("nonprofit organizations / community grants", _rx(r"\bnon-?profits?\b|\bgrants?\b|\bcommunity\s+(?:benefits?\s+)?fund\b|\bcommunity\s+organizations?\b|\bresorts\s+world\s+gives\b")),
    ("transportation / infrastructure", _rx(r"\btransit\b|\btransportation\b|\binfrastructure\b|\btraffic\b|\bbus\b|\bferry\b|\broads?\b|\bstation\b")),
    ("public safety", _rx(r"\bpublic\s+safety\b|\bpolice\b|\bnypd\b|\bemergency\s+services\b")),
    ("parks / environment", _rx(r"\bparks?\b|\bparkland\b|\bgreen\s?space\b|\brenewable\b|\bsolar\b|\benvironment\w*\b|\bsustainab\w+")),
    ("problem gambling / public health", _rx(r"\bproblem\s+gambling\b|\bresponsible\s+gaming\b|\bhealth\b")),
    ("host municipality", _rx(r"\bhost\s+municipalit(?:y|ies)\b")),
    ("regulator / State", _rx(r"\bescrow\b|\bbond\b|\blicense\s+fee\b|\bcommission\b")),
    ("local residents", _rx(r"\bresidents?\b|\blocal\s+hire\b")),
)

NAMED_ORG = re.compile(
    r"\b(?:[A-Z][\w’'&.\-]*\s+){0,6}(?:Fund|Foundation|Council|Program|Gives|College|University|Union|Association"
    r"|Authority|Trust|Center|Centre|Campus|Coalition|Alliance|Initiative|Academy|Partnership|Institute|Board\s+of\s+Education)"
    r"(?:\s+of\s+(?:[A-Z][\w’'&.\-]*\s*){1,6})?"
)
_NAMED_ORG_STOP = _rx(r"^(?:the\s+)?(?:board|commission|community\s+benefits?\s+fund|fund|program|council)$")


def _join(values):
    seen, ordered = set(), []
    for value in values:
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            ordered.append(value)
    return "; ".join(ordered)


def _strip(value):
    return re.sub(r"\s+", " ", value).strip(" ,;.")


def extract_amount(text):
    values = [_strip(m.group(0)) for m in FORMULA.finditer(text)]
    for pattern in (MONEY, PERCENT, COUNT):
        for match in pattern.finditer(text):
            value = _strip(match.group(0))
            if not any(value.lower() in existing.lower() for existing in values):
                values.append(value)
    return _join(values) or UNSPECIFIED


def extract_periodicity(text):
    return _join(label for label, pattern in PERIODICITY if pattern.search(text)) or UNSPECIFIED


def extract_milestone(text):
    return _join(_strip(m.group(0)) for m in MILESTONE.finditer(text)) or NONE_STATED


def extract_condition(text):
    return _join(_strip(m.group(0)) for m in CONDITION.finditer(text)) or NONE_STATED


def extract_geography(text):
    found = [label for label, pattern in GEOGRAPHY if pattern.search(text)]
    if any(name in found for name in ("South Ozone Park", "Ozone Park", "Jamaica", "Aqueduct", "Southeast Queens")):
        found = [name for name in found if name != "Queens"] + ["Queens"]
    return _join(found) or UNSPECIFIED


def extract_beneficiary_category(text):
    return _join(label for label, pattern in BENEFICIARY_CATEGORIES if pattern.search(text)) or UNSPECIFIED


def extract_beneficiary_named(text, entity):
    names = []
    for match in NAMED_ORG.finditer(text):
        name = _strip(match.group(0))
        name = re.sub(r"^(?:The|A|An|And|With|To|For|Of|In|Its|Their)\s+", "", name)
        if len(name.split()) < 2 or _NAMED_ORG_STOP.match(name):
            continue
        if name.lower() in entity.lower():
            continue
        names.append(name)
    return _join(names) or NONE_NAMED


# --- Status, confidence ------------------------------------------------------

FUTURE_TRIGGER = _rx(r"\b(?:facility\s+opens|upon\s+opening|once\s+(?:the\s+facility\s+is\s+)?(?:open|operational)|upon\s+completion|commencement\s+of\s+operations)\b")
YEAR = re.compile(r"\b(20\d{2})\b")


def evidence_status(obligation, text, milestone, as_of_year):
    """B0 runs no evidence search; it only separates 'not yet due' from 'not located'."""
    if obligation == "Reported accomplishment":
        return "self-reported only"
    years = [int(y) for y in YEAR.findall(milestone)]
    if FUTURE_TRIGGER.search(text) or (years and min(years) > as_of_year):
        return "not yet due"
    return "no public evidence located"


def confidence(obligation, cue, entity):
    if obligation == "Unresolved / ambiguous" or entity == UNSPECIFIED:
        return "Low"
    if obligation == "Binding / license condition" and cue.lower() in ("shall", "must"):
        return "High"
    if obligation in ("Contractual commitment", "Contingent commitment", "Reported accomplishment"):
        return "Medium"
    return "Medium"


# --- Rule sets -----------------------------------------------------------------

@dataclass(frozen=True)
class RuleSet:
    """Switches for every rule added after looking at gold examples.

    All False is the *core*: only what follows from the Kickoff Pack taxonomy
    (trigger lexicon, class cues in taxonomy order, field patterns, sentence
    splitting, length bounds) and from the documents' own structure (entity
    gazetteer, applicant sections). Each switch notes the dev rows behind it.
    """
    # Segmentation (applied when loading the corpus; see corpus_options)
    merge_page_breaks: bool = False          # rejoin a sentence split by a page break
    fine_clauses: bool = False               # split at "; ", ", but ", ", as well as", "and has <verb>ed"
    # Candidate selection
    benefit_package_trigger: bool = False    # H017: "community benefits ... include / valued at"
    partnered_trigger: bool = False          # A035: "has partnered with" is a reported accomplishment
    long_passages: bool = False              # A067: allow passages up to 160 words
    proposal_trigger: bool = False           # H021, A039, H013: "have been proposed", "envisioned to", "require X to"
    executed_trigger: bool = False           # H015: "has executed / signed / entered into" an agreement
    # Classification
    tuned_class_order: bool = False          # specific forms before generic modals
    may_case_sensitive: bool = False         # month "May" is not an ambiguity cue
    forecast_verb_only: bool = False         # noun "project" is not a forecast cue
    projects_to_be_is_noun: bool = False     # "those projects to be completed" is not a forecast cue
    fit_class_cues: bool = False             # H001, A036 -> Unresolved; H017 -> Contractual
    # Entity
    licensee_is_document_party: bool = False  # "the licensee" in a license = the license holder
    # Verifiability filters
    drop_generic_obligor: bool = False       # no specific obligor ("the Applicant", "a licensee")
    generic_commitment_exemption: bool = False  # A003, A007, A008: keep generic duties to commit / guarantee
    drop_regulator_evaluation: bool = False  # evaluative Board reaction (incl. split clauses)
    board_framed_applicant: bool = False     # H014/H015: keep "the Board is satisfied that Bally's intends ..."
    drop_regulator_stance: bool = False      # Board/"we" stance: emphasizes, stresses, believes, urges, ...
    stance_normative_exemption: bool = False  # H023: keep "the Board believes X has an obligation to ..."
    forecast_workforce_only: bool = False    # A030 vs. market forecasts: keep only workforce projections
    drop_quantity_projection: bool = False   # "X projects 3,905 employees", "is expected to ..."
    drop_negated: bool = False               # "did not include any commitments", "were not considered"
    drop_instrument_boilerplate: bool = False  # "Nothing shall prevent", "successors and assigns", ...
    drop_enumerated_fragment: bool = False   # "(ii) shared use of ...": a list item split from its stem
    # Single dev-error rules (deliberate fit to dev)
    fit_plans_to: bool = False               # H004, PLA, overbuild: "plans to" is intent, not commitment
    fit_projects_verb: bool = False          # "The plan projects ...", "analysis projects that ..."
    fit_location: bool = False               # "The proposed location ... will situate"
    fit_proposes_no_amount: bool = False     # "HRMP proposes extensive ..." with no $ or %
    fit_license_fees: bool = False           # "will contribute $500 million in license fees"
    fit_campus: bool = False                 # A037: Innovation Campus / preconstruction programming
    fit_goals_combined: bool = False         # "ambitious goals ... combined with"
    fit_livable_wages: bool = False          # A001: keep "jobs ... must deliver livable wages"


CORE_RULES = RuleSet()
# The B0 rules: switches kept by dev-F1 selection with every workbook gold row as a positive.
DEFAULT_RULES = RuleSet(
    merge_page_breaks=True,
    fine_clauses=True,
    benefit_package_trigger=True,
    partnered_trigger=True,
    long_passages=True,
    proposal_trigger=True,
    executed_trigger=True,
    tuned_class_order=True,
    may_case_sensitive=True,
    forecast_verb_only=True,
    projects_to_be_is_noun=True,
    fit_class_cues=True,
    licensee_is_document_party=True,
    drop_generic_obligor=True,
    generic_commitment_exemption=True,
    drop_regulator_evaluation=True,
    board_framed_applicant=True,
    drop_regulator_stance=True,
    stance_normative_exemption=True,
    forecast_workforce_only=True,
    drop_quantity_projection=True,
    drop_negated=True,
    drop_instrument_boilerplate=True,
    drop_enumerated_fragment=True,
    fit_plans_to=True,
    fit_projects_verb=True,
    fit_location=True,
    fit_proposes_no_amount=True,
    fit_license_fees=True,
    fit_campus=True,
    fit_goals_combined=True,
    fit_livable_wages=True,
)
RULESETS = {B0_VERSION: DEFAULT_RULES}
B0_VERSIONS = tuple(RULESETS)


def unverifiable_reason_for(rules, text, entity, context=""):
    """Why a candidate fails the enabled verifiability filters, or "" to keep it."""
    if rules.drop_generic_obligor and entity in GENERIC_ENTITIES and not (
            rules.fit_livable_wages and LIVABLE_WAGES.search(text)) and not (
            rules.generic_commitment_exemption and GENERIC_COMMITMENT.search(text)):
        return "generic obligor"
    if rules.drop_regulator_evaluation and not (
            rules.board_framed_applicant and BOARD_FRAMED_APPLICANT.search(context + text)):
        board_clause = bool(context) and REGULATOR_SUBJECT.search(context) and _EVALUATION_VERB_START.search(text)
        if REGULATOR_EVALUATION.search(text) or REGULATOR_EVALUATION.search(context) or board_clause:
            return "regulator evaluation"
    if rules.drop_regulator_stance and (REGULATOR_STANCE.search(text) or REGULATOR_STANCE.search(context)):
        if not (rules.stance_normative_exemption and NORMATIVE_OBLIGATION.search(text)):
            return "regulator stance"
    for flag, pattern in DEV_FIT_FILTERS:
        if getattr(rules, flag) and pattern.search(text):
            return flag
    if rules.fit_proposes_no_amount and PROPOSES.search(text) and not AMOUNT_OR_SHARE.search(text):
        return "fit_proposes_no_amount"
    if rules.drop_enumerated_fragment and ENUMERATED_FRAGMENT.search(text):
        return "enumerated fragment"
    if rules.drop_negated and NEGATED.search(text):
        return "negated"
    if rules.drop_quantity_projection and QUANTITY_PROJECTION.search(text):
        return "quantity projection"
    if rules.drop_instrument_boilerplate and INSTRUMENT_BOILERPLATE.search(text):
        return "instrument boilerplate"
    return ""


# --- Verifiability filter patterns ------------------------------------------

GENERIC_ENTITIES = frozenset({"Applicant", "Gaming Facility licensee", UNSPECIFIED})
# The evaluator's reaction to an application.
REGULATOR_EVALUATION = _rx(
    r"\b(?:the\s+)?(?:board|commission|committee|siting\s+board)\s+(?:also\s+|further\s+|strongly\s+)?"
    r"(?:expects|expected|viewed|views|notes|noted|found|finds|considers|considered"
    r"|(?:was|is)\s+(?:satisfied|disappointed|concerned|pleased|encouraged|impressed|troubled)"
    r"|has\s+concerns)\b"
)
# A clause split off a Board sentence ("The Board supports X, but expects Y")
# keeps the Board as its subject.
_EVALUATION_VERB_START = _rx(r"^(?:also\s+)?(?:expects|expected|views|viewed|notes|noted|finds|found|considers|considered)\b")
REGULATOR_STANCE = _rx(
    r"\b(?:the\s+)?board\s+(?:also\s+|fully\s+|further\s+|strongly\s+)?"
    r"(?:emphasizes|stresses|believes|remains\s+concerned|supports|assumes|recognizes|urges|is\s+convinced)\b"
    r"|\bwe\s+(?:urge|noted|note|believe|recommend)\b|\bconvinced\s+the\s+board\b"
    r"|\bkey\s+factors\s+(?:in|driving)\s+the\s+board\b"
)
NEGATED = _rx(
    r"\b(?:did|does|do|has|have|had)\s+not\s+(?:include|commit|provide|offer|propose|make)\b"
    r"|\bnot\s+(?:been\s+)?considered\b|\bno\s+(?:firm\s+|specific\s+)?commitments?\b"
)
QUANTITY_PROJECTION = _rx(
    r"\b(?:projects|estimates|anticipates|forecasts)\s+(?:approximately\s+|about\s+|nearly\s+|over\s+"
    r"|more\s+than\s+|up\s+to\s+)?\$?\d"
    r"|\b(?:is|are)\s+expected\s+to\s+(?!be\s+(?:required|obligated|bound))"
)
INSTRUMENT_BOILERPLATE = _rx(
    r"\bnothing\s+(?:herein\s+)?shall\s+(?:prevent|limit|restrict)\b|\bis\s+not\s+obligated\b"
    r"|\bsuccessors\s+and\s+assigns\b|\bmutual\s+intent\b|\bprior\s+(?:written\s+)?approval\s+of\s+the\s+commission\b"
)
# A generic obligor's duty to commit to, enter into or guarantee something.
GENERIC_COMMITMENT = _rx(
    r"\bcommit\s+to\s+an?\b|\bwill\s+enter\s+into\b|\bguarantees?\s+to\b|\bpromises\s+to\s+the\s+community\b"
    r"|\butilization\s+goal\b|\bregular\s+updates\b")
# The Board reporting what a named applicant has done or intends ("satisfied that Bally's intends ...").
BOARD_FRAMED_APPLICANT = _rx(
    r"\b(?:satisfied|notes|noted)\s+that\s+(?:Bally|HRMP|Hard\s+Rock|Resorts\s+World)\S*\s+"
    r"(?:intends|has|have|will|commits|committed|discussed|agreed|pledged)\b")
WORKFORCE = _rx(r"\b(?:employ\w*|FTEs?|jobs?|workforce|hires?|hiring|residents|women|minorit\w+|veterans?|wages?|benefit)\b")
NORMATIVE_OBLIGATION = _rx(r"\b(?:has|have)\s+an\s+obligation\s+to\b|\bis\s+obligated\s+to\b")
ENUMERATED_FRAGMENT = _rx(r"^\s*(?:and\s+|or\s+)?\((?:[ivx]+|[a-h])\)\s+[a-z]")
# Single dev-error rules.
DEV_FIT_FILTERS = (
    ("fit_plans_to", _rx(r"\bplans?\s+to\b")),
    ("fit_projects_verb", _rx(r"\b(?:plan|analysis)\s+projects\b|\bprojects\s+that\b")),
    ("fit_location", _rx(r"\bproposed\s+location\b")),
    ("fit_license_fees", _rx(r"\blicense\s+fees?\b")),
    ("fit_campus", _rx(r"\binnovation\s+campus\b|\bpreconstruction\b")),
    ("fit_goals_combined", _rx(r"\bgoals\b[^.]{0,200}\bcombined\s+with\b")),
)
PROPOSES = _rx(r"\bproposes\b")
AMOUNT_OR_SHARE = _rx(r"\$\s?\d|\b\d+(?:\.\d+)?\s*(?:percent|%)")
LIVABLE_WAGES = _rx(r"\blivable\s+wages?\b")
FIT_UNRESOLVED = _rx(r"\bdiverse\s+workforce\b|\bimmediate\s+term\b")
FIT_CONTRACTUAL = _rx(r"\bbenefits?\s+valued\s+at\b")
PARTNERED = _rx(r"\b(?:has|have)\s+(?:already\s+)?partnered\s+with\b")
def _forecast_dropped(rules, obligation, text=""):
    if obligation != "Forecast / projection":
        return False
    return rules.forecast_workforce_only and not WORKFORCE.search(text)


# --- Driver ------------------------------------------------------------------

@dataclass
class Extraction:
    pred_id: str
    document_id: str
    passage_id: str
    page: int
    char_start: int
    char_end: int
    commitment_text: str
    section: str
    section_applicant: str
    obligation_type: str
    obligation_cue: str
    responsible_entity: str
    amount_value: str
    term_periodicity: str
    geographic_scope: str
    beneficiary_category: str
    beneficiary_named: str
    condition_contingency: str
    milestone: str
    evidence_status: str
    confidence: str
    review_state: str
    extractor: str = B0_VERSION

    def to_dict(self):
        return asdict(self)


def _clause_context(document, passage):
    """Text of the sentence that a mid-sentence clause passage was split from."""
    if not passage.text[:1].islower():
        return ""
    start = document.text.rfind("\n", 0, passage.char_start) + 1
    before = document.text[start:passage.char_start]
    cut = max(before.rfind(". "), before.rfind("? "), before.rfind("! "))
    return before[cut + 2:] if cut >= 0 else before


def is_commitment_candidate(text, rules=None):
    rules = rules or DEFAULT_RULES
    words = text.split()
    if len(words) < 6 or len(words) > (160 if rules.long_passages else 120):
        return False
    if not _has_trigger(rules, text):
        return False
    return not text.rstrip().endswith("?")


def _has_trigger(rules, text):
    return bool(TRIGGER.search(text) or (rules.benefit_package_trigger and BENEFIT_PACKAGE_TRIGGER.search(text))
                or (rules.partnered_trigger and PARTNERED.search(text))
                or (rules.proposal_trigger and PROPOSAL_TRIGGER.search(text))
                or (rules.executed_trigger and EXECUTED.search(text)))


def _section_applicants(document):
    """Map each heading offset to the applicant whose section it opens."""
    marks, current = [], ""
    for offset, heading in document.headings:
        for canonical, pattern in SECTION_APPLICANTS:
            if pattern.match(heading.strip()):
                current = canonical
                break
        else:
            if SECTION_RESET.match(heading.strip()) and heading.strip().isupper():
                current = ""
        marks.append((offset, current))
    return marks


def rules_for(version):
    if isinstance(version, RuleSet):
        return version
    if version not in RULESETS:
        raise ValueError(f"unknown B0 rule version {version!r}; choose from {tuple(RULESETS)}")
    return RULESETS[version]


def corpus_options(version):
    """Keyword arguments for ``corpus.load_corpus`` that match a rule version."""
    rules = rules_for(version)
    clauses = "fine" if rules.fine_clauses else False
    return {"merge_pages": rules.merge_page_breaks, "clauses": clauses}


def extract_document(document, as_of_year=2026, version=B0_VERSION):
    """Extract commitments. ``version`` is a version name or a ``RuleSet``.

    Load ``document`` with ``corpus_options(version)`` so its passages use the
    segmentation the rules expect.
    """
    rules = rules_for(version)
    name = version if isinstance(version, str) else "custom"
    default_entity = DOCUMENT_DEFAULT_ENTITY.get(document.document_id, UNSPECIFIED)
    marks = _section_applicants(document)
    results = []
    for passage in document.passages:
        text = passage.text
        context = _clause_context(document, passage)
        if not is_commitment_candidate(text, rules):
            continue
        applicant = ""
        for offset, mark in marks:
            if offset <= passage.char_start:
                applicant = mark
            else:
                break
        obligation, cue = classify_obligation(text, document.document_id, rules)
        entity = responsible_entity(text, applicant, document.document_id)
        # In an instrument issued to one party, "the licensee" is that party.
        if (rules.licensee_is_document_party and entity in GENERIC_ENTITIES
                and default_entity not in GENERIC_ENTITIES):
            entity = default_entity
        if unverifiable_reason_for(rules, text, entity, context):
            continue
        if _forecast_dropped(rules, obligation, text):
            continue
        milestone = extract_milestone(text)
        results.append(Extraction(
            pred_id=f"B0-{document.document_id}-{len(results) + 1:04d}",
            document_id=document.document_id,
            passage_id=passage.passage_id,
            page=passage.page,
            char_start=passage.char_start,
            char_end=passage.char_end,
            commitment_text=text,
            section=passage.heading,
            section_applicant=applicant,
            obligation_type=obligation,
            obligation_cue=cue,
            responsible_entity=entity,
            amount_value=extract_amount(text),
            term_periodicity=extract_periodicity(text),
            geographic_scope=extract_geography(text),
            beneficiary_category=extract_beneficiary_category(text),
            beneficiary_named=extract_beneficiary_named(text, entity),
            condition_contingency=extract_condition(text),
            milestone=milestone,
            evidence_status=evidence_status(obligation, text, milestone, as_of_year),
            confidence=confidence(obligation, cue, entity),
            review_state="unreviewed",
            extractor=name,
        ))
    return results
