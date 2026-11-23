"""B0 baseline: rule/pattern commitment extraction with no generative model.

Kickoff Pack §04: "B0 - Rules / patterns. Regex, lexical patterns,
deterministic parsing, no generative model. Establish a low-complexity floor
and identify fields that do not require AI."

The pipeline is: layout-aware sentence passages (``corpus``) -> commitment
trigger filter -> ordered obligation-class rules -> regex/gazetteer field
extractors. Every output carries passage-level provenance. Rules were written
from the Kickoff Pack taxonomy and the ``anchor_dev`` split only.

Rule versions (both stay reproducible; pass ``version`` to ``extract_document``):

* ``b0-rules-v1.0`` was tuned for recall on every anchor_dev row.
* ``b0-rules-v1.1`` targets the validated gold definition: only rows with
  ``manually annotated valid?`` = 1 (verifiable commitments) are positives,
  and anchor_dev rows marked 0 or blank are negatives. It keeps v1.0 and adds
  three general verifiability filters (see ``unverifiable_reason``). It was
  tuned on anchor_dev only (5 positives, 15 negatives).
* ``b0-rules-v1.2`` was retuned on the ``dev`` half of
  ``evaluation/splits/resplit_v2.json`` (17 positives, 34 negatives). It
  narrows v1.1's filters (see ``unverifiable_reason_v1_2``): a generic obligor
  is dropped only for administrative duties, only evaluative Board reactions
  are dropped (normative Board statements are kept), and legal recitals are
  dropped.
"""

from dataclasses import asdict, dataclass
import re


B0_V1_0 = "b0-rules-v1.0"
B0_V1_1 = "b0-rules-v1.1"
B0_V1_2 = "b0-rules-v1.2"
B0_VERSIONS = (B0_V1_0, B0_V1_1, B0_V1_2)
B0_VERSION = B0_V1_2  # default
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

TRIGGER = _rx(
    r"\b(?:shall|must|will|would|agree[sd]?\s+to|commit(?:s|ted|ments?)?\b|assum(?:ed|es)\b|pledge[sd]?|promise[sd]?"
    r"|guarantee[sd]?|is\s+required|are\s+required|be\s+required|required\s+to|obligat\w+"
    r"|expect(?:s|ed)?\b|anticipate[sd]?|project(?:s|ed|ion)|estimate[sd]?|forecast"
    r"|target(?:s|ed)?|goals?\b|intends?\s+to|plans?\s+to|endeavou?r|best[\s-]efforts?"
    r"|reasonable\s+efforts?|has\s+offered|have\s+offered|offers?\s+to|proposes?\s+to"
    r"|in\s+place\b|has\s+(?:already\s+)?(?:provided|invested|contributed|donated|hired|created|completed|built|opened))"
)

# Sentences that are about the document/process itself, not a benefit.
PROCEDURAL = _rx(
    r"\b(?:font|page\s+limit|pages\s+in\s+length|single[\s-]spaced|binder|usb|flash\s+drive"
    r"|tabbed|bates|notariz\w+|questions?\s+(?:must|should|shall)\s+be\s+(?:submitted|directed)"
    r"|electronic\s+copy|hard\s+cop(?:y|ies)|this\s+rfa\s+(?:will|shall)|the\s+board\s+will\s+(?:review|evaluate|consider|score)"
    r"|will\s+be\s+posted|press\s+release|may\s+be\s+(?:rejected|disqualified))"
)
# Sentences whose acting subject is the evaluator/regulator, not an obligor.
REGULATOR_SUBJECT = _rx(
    r"^(?:\W*(?:the|this|a)\s+)?(?:board|commission|committee|staff|siting\s+board|gaming\s+commission|state|legislature)\b"
)
OBLIGOR_MENTION = _rx(r"\b(?:applicants?|licensees?|resorts\s+world|rwnyc|genting|bally|hard\s+rock|hrmp|metropolitan\s+park)\b")


# --- Obligation-class rules (first match wins) -----------------------------

CLASS_RULES = (
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
    ("Binding / license condition", _rx(
        r"\bshall\b|\bmust\b|\b(?:is|are|be)\s+required\s+to\b|\brequired\s+to\b|\bmaterial\s+condition\b|\bobligated\b")),
    ("Target / goal", _rx(r"\btargets?\b|\btargeted\b|\bgoals?\b|\baims?\s+to\b|\bstrives?\s+to\b|\bobjectives?\b")),
    ("Forecast / projection", _rx(
        r"\b(?:expects?|expected|anticipates?|anticipated|projects\s+(?:that|to)|projected|projections?|estimates?|estimated"
        r"|forecasts?|forecasted)\b(?!\s+(?:the\s+)?(?:applicants?|licensees?)\b)|\bwould\s+(?:generate|create|produce|result)\b")),
    ("Contractual commitment", _rx(
        r"\bcommit(?:s|ted)?\s+to\b|\bcommitments?\s+(?:of|to)\b|\bagree[sd]?\s+to\b|\bhas\s+agreed\b|\bpledge[sd]?\b"
        r"|\bpromise[sd]?\b|\bguarantee[sd]?\b|\b(?:has|have)\s+offered\b|\boffers?\b|\bwill\b|\bintends?\s+to\b"
        r"|\bplans?\s+to\b|\bproposes?\s+to\b")),
)


# Governing instruments: an undertaking stated inside them is tied directly to
# the instrument, which the taxonomy defines as "Binding / license condition".
REGULATORY_INSTRUMENTS = frozenset({"NYSGC-RWNYC-LIC-2025-12-19", "RFA-2023-01-03"})


def classify_obligation(text, document_id=""):
    """Return (obligation_class, matched_cue). Rules are ordered; first match wins."""
    for label, pattern in CLASS_RULES:
        match = pattern.search(text)
        if match:
            if label == "Contractual commitment" and document_id in REGULATORY_INSTRUMENTS:
                label = "Binding / license condition"
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


# --- v1.1 verifiability filters --------------------------------------------
# A verifiable commitment needs a specific obligor that undertakes something.
# These filters drop commitment-like passages that fail that test.

GENERIC_ENTITIES = frozenset({"Applicant", "Gaming Facility licensee", UNSPECIFIED})
# The evaluator states its own view or expectation of an applicant.
REGULATOR_VOICE = _rx(
    r"\b(?:the\s+)?(?:board|commission|committee|siting\s+board)\s+(?:also\s+|further\s+|strongly\s+)?"
    r"(?:expects|believes|supports|encourages|recommends|urges|notes|finds|requests|would\s+(?:like|prefer)"
    r"|is\s+concerned|has\s+concerns|considers|views)\b"
)
# Second-hand reports of what a party said, not an undertaking by it.
REPORTED_SPEECH = _rx(r"\b(?:indicated|stated|noted|said|reported|claimed|suggested)\s+that\b")


def _clause_context(document, passage):
    """Text of the sentence that a mid-sentence clause passage was split from."""
    if not passage.text[:1].islower():
        return ""
    start = document.text.rfind("\n", 0, passage.char_start) + 1
    before = document.text[start:passage.char_start]
    cut = max(before.rfind(". "), before.rfind("? "), before.rfind("! "))
    return before[cut + 2:] if cut >= 0 else before


def unverifiable_reason(text, entity, context=""):
    """v1.1: why a candidate is not a verifiable commitment, or "" to keep it."""
    if entity in GENERIC_ENTITIES:
        return "generic obligor"
    if REGULATOR_VOICE.search(text) or REGULATOR_VOICE.search(context):
        return "regulator voice"
    if REPORTED_SPEECH.search(text):
        return "reported speech"
    return ""


# --- v1.2 refinements ---------------------------------------------------------

# Application-process duties of a generic applicant: paperwork, not benefits.
ADMINISTRATIVE_DUTY = _rx(
    r"\b(?:provide|produce|submit|furnish|file|complete|identify|describe|disclose|certify)\b"
    r"|\bmaintain\s+(?:a\s+)?records?\b|\bdocumentation\b|\baffidavit\b|\binstrument\b"
    r"|\bform\s+(?:determined|prescribed|approved)\b"
)
# The evaluator's reaction to an application, as opposed to a normative
# statement ("the Board recommends ... require ...") which can become binding.
REGULATOR_EVALUATION = _rx(
    r"\b(?:the\s+)?(?:board|commission|committee|siting\s+board)\s+(?:also\s+|further\s+|strongly\s+)?"
    r"(?:expects|expected|viewed|views|notes|noted|found|finds|considers|considered"
    r"|(?:was|is)\s+(?:satisfied|disappointed|concerned|pleased|encouraged|impressed|troubled)"
    r"|has\s+concerns)\b"
)
# A clause split off a Board sentence ("The Board supports X, but expects Y")
# keeps the Board as its subject.
_EVALUATION_VERB_START = _rx(r"^(?:also\s+)?(?:expects|expected|views|viewed|notes|noted|finds|found|considers|considered)\b")

# Recitals and findings in a legal instrument: background, not operative duties.
RECITAL = _rx(r"\bhaving\s+(?:agreed|determined|been|found)\b|\bdetermined\s+that\b|\bwhereas\b")


def unverifiable_reason_v1_2(text, entity, context=""):
    """v1.2: why a candidate is not a verifiable commitment, or "" to keep it."""
    if entity in GENERIC_ENTITIES and ADMINISTRATIVE_DUTY.search(text):
        return "generic administrative duty"
    board_clause = bool(context) and REGULATOR_SUBJECT.search(context) and _EVALUATION_VERB_START.search(text)
    evaluation = REGULATOR_EVALUATION.search(text) or REGULATOR_EVALUATION.search(context) or board_clause
    # A Board reaction that restates a named party's dollar commitment still
    # carries that commitment, which is verifiable on its own.
    if evaluation and not (MONEY.search(text) and entity not in GENERIC_ENTITIES):
        return "regulator evaluation"
    if REPORTED_SPEECH.search(text):
        return "reported speech"
    if RECITAL.search(text):
        return "recital"
    return ""


def is_commitment_candidate(text):
    words = text.split()
    if len(words) < 6 or len(words) > 120:
        return False
    if not TRIGGER.search(text) or PROCEDURAL.search(text):
        return False
    if text.rstrip().endswith("?"):
        return False
    if REGULATOR_SUBJECT.search(text) and not OBLIGOR_MENTION.search(text):
        return False
    return True


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


def extract_document(document, as_of_year=2026, version=B0_VERSION):
    if version not in B0_VERSIONS:
        raise ValueError(f"unknown B0 rule version {version!r}; choose from {B0_VERSIONS}")
    default_entity = DOCUMENT_DEFAULT_ENTITY.get(document.document_id, UNSPECIFIED)
    marks = _section_applicants(document)
    results = []
    for passage in document.passages:
        text = passage.text
        if not is_commitment_candidate(text):
            continue
        applicant = ""
        for offset, name in marks:
            if offset <= passage.char_start:
                applicant = name
            else:
                break
        obligation, cue = classify_obligation(text, document.document_id)
        entity = responsible_entity(text, applicant, document.document_id)
        if version in (B0_V1_1, B0_V1_2):
            # In an instrument issued to one party, "the licensee" is that party.
            if entity in GENERIC_ENTITIES and default_entity not in GENERIC_ENTITIES:
                entity = default_entity
            reason = unverifiable_reason if version == B0_V1_1 else unverifiable_reason_v1_2
            if reason(text, entity, _clause_context(document, passage)):
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
            extractor=version,
        ))
    return results
