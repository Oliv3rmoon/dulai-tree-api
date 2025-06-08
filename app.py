# app.py -------------------------------------------------------
import os, uuid, datetime as dt, itertools, json
from typing import List, Dict, Any

import openai
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import logging, json, openai
from fastapi import Cookie
import uuid, json
_sessions: dict[str, dict] = {}   # {session_id: {field: value}}

def get_session(session_id: str | None) -> tuple[str, dict, list]:
    if not session_id or session_id not in _sessions:
        session_id = uuid.uuid4().hex
        _sessions[session_id] = {
            "fields": {},
            "history": []
        }
    session = _sessions[session_id]
    return session_id, session["fields"], session["history"]

# -----------------------------------------------------------------
# ENV / CONFIG
# -----------------------------------------------------------------
openai.api_key = os.getenv("OPENAI_API_KEY")
if not openai.api_key:
    raise RuntimeError("OPENAI_API_KEY not set!")

SYSTEM_PROMPT = open("system_prompt.txt", encoding="utf-8").read()

HOURS   = [7, 9, 11, 13, 15]          # 2-hour blocks start times
WEEKDAYS= {0,1,2,3,4,5}               # Mon-Sat (Python Mon=0 … Sun=6)

# -----------------------------------------------------------------
# FASTAPI setup
# -----------------------------------------------------------------
app = FastAPI(title="Dulai Tree API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # tighten after testing
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")

# -----------------------------------------------------------------
# In-memory calendar  (dict[date_str][hour] → booking dict)
# -----------------------------------------------------------------
_calendar: Dict[str, Dict[int, Dict[str,Any]]] = {}

def _slot_key(date: dt.date, hour: int) -> str:
    return f"{date.isoformat()}_{hour:02d}"

def is_free(date: dt.date, hour: int) -> bool:
    return _calendar.get(date.isoformat(), {}).get(hour) is None

def reserve(date: dt.date, hour: int, payload: dict) -> str:
    _calendar.setdefault(date.isoformat(), {})[hour] = payload
    return _slot_key(date, hour)
# ---- very small in-memory session store ----
_sessions: dict[str, dict] = {}   # {session_id: {slot_name: value}}

def get_session(session_id: str | None) -> tuple[str, dict]:
    if not session_id or session_id not in _sessions:
        session_id = uuid.uuid4().hex
        _sessions[session_id] = {}
    return session_id, _sessions[session_id]

# -----------------------------------------------------------------
# Booking helpers exposed to the model
# -----------------------------------------------------------------
def get_estimate(service_type: str,
                 tree_count: int,
                 height_ft: int,
                 emergency: bool,
                 zip: str) -> int:
    base = {
        "trim":        75 * tree_count,
        "removal":     5 * height_ft * tree_count,
        "stump_grind": 150 * tree_count,
        "hedge":       40 * tree_count,
        "emergency":   100,
    }[service_type]
    if service_type == "removal":
        base = max(base, 300)
    if emergency:
        base *= 1.4
    travel = 0 if zip.startswith("95") else 50
    return round(base + travel, -1)

def find_open_slots(preferred_date_range: dict,
                    preferred_times_of_day: List[str],
                    crew_size: int = 3,
                    max_slots: int = 5) -> List[dict]:
    start = dt.date.fromisoformat(preferred_date_range["start_date"])
    end   = dt.date.fromisoformat(preferred_date_range["end_date"])
    wanted_hours = {
        "morning":  [7, 9],
        "midday":   [11],
        "afternoon":[13, 15]
    }
    filter_hours = list(itertools.chain.from_iterable(
        wanted_hours[t] for t in preferred_times_of_day
    ))
    open_blocks = []
    cur = start
    while cur <= end and len(open_blocks) < max_slots:
        if cur.weekday() in WEEKDAYS:                   # skip Sunday
            for h in filter_hours:
                if is_free(cur, h):
                    open_blocks.append({
                        "slot_id" : _slot_key(cur, h),
                        "date"    : cur.isoformat(),
                        "start"   : f"{h:02d}:00",
                        "end"     : f"{h+2:02d}:00"
                    })
                    if len(open_blocks) == max_slots:
                        break
        cur += dt.timedelta(days=1)
    return open_blocks

def book_job(slot_id: str, job_payload: dict) -> dict:
    date_str, hour_str = slot_id.split("_")
    date = dt.date.fromisoformat(date_str)
    hour = int(hour_str)
    reserve(date, hour, job_payload)
    return {
        "job_id": "DT-" + uuid.uuid4().hex[:8].upper(),
        "date"  : date_str,
        "start" : f"{hour:02d}:00",
        "end"   : f"{hour+2:02d}:00"
    }
    {
        "name": "extract_fields",
        "description": "Pull any of the eight booking slots from free-form text.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_type":           {"type":"string"},
                "tree_count":             {"type":"integer"},
                "height_ft":              {"type":"integer"},
                "location_notes":         {"type":"string"},
                "address":                {"type":"string"},
                "contact": {
                    "type":"object",
                    "properties": {
                        "name":  {"type":"string"},
                        "phone": {"type":"string"},
                        "email": {"type":"string"}
                    }
                },
                "preferred_date_range": {
                    "type":"object",
                    "properties": {
                        "start_date":{"type":"string"},
                        "end_date":  {"type":"string"}
                    }
                },
                "preferred_times_of_day": {"type":"string"}
            }
        }
    },

# -----------------------------------------------------------------
# OpenAI function schema (matches FastAPI helpers)
# -----------------------------------------------------------------
# ---------------------------------------------------------------
# 1)  FUNCTION SCHEMAS  (extract_fields needs to be here!)
# ---------------------------------------------------------------
FUNCTIONS = [
    {
        "name": "extract_fields",
        "description": "Pull any booking slots the user just mentioned.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_type":           {"type": "string"},
                "tree_count":             {"type": "integer"},
                "height_ft":              {"type": "integer"},
                "location_notes":         {"type": "string"},
                "address":                {"type": "string"},
                "contact":                {"type": "object"},
                "preferred_date_range":   {"type": "object"},
                "preferred_times_of_day": {"type": "array","items":{"type":"string"}}
            },
            "required": []          # send any subset you found
        }
    },
    {
        "name": "find_open_slots",
        "description": "Return up to 5 free two-hour crew blocks.",
        "parameters": {
            "type": "object",
            "properties": {
                "preferred_date_range": {
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string"},
                        "end_date":   {"type": "string"}
                    },
                    "required": ["start_date","end_date"]
                },
                "preferred_times_of_day": {
                    "type": "array",
                    "items": {"type":"string"}
                },
                "crew_size": {"type":"integer"},
                "max_slots": {"type":"integer"}
            },
            "required": ["preferred_date_range","preferred_times_of_day"]
        }
    },
    {
        "name": "book_job",
        "description": "Reserve the selected slot and create an appointment.",
        "parameters": {
            "type": "object",
            "properties": {
                "slot_id":     {"type": "string"},
                "job_payload": {"type": "object"}
            },
            "required": ["slot_id","job_payload"]
        }
    }
]

# ---------------------------------------------------------------
# 2)  FUNCTION TABLE
# ---------------------------------------------------------------
FUNC_TABLE = {
    "extract_fields":  lambda **kw: kw,   # just echo args back
    "get_estimate":    get_estimate,
    "find_open_slots": find_open_slots,
    "book_job":        book_job,
}

class ChatBody(BaseModel):
    message: str


from fastapi.responses import StreamingResponse
import logging, json, openai    # openai & json are already imported elsewhere

logging.basicConfig(level=logging.INFO)         # ← keep while debugging

@app.get("/")
def root():
    return {"status": "ok", "message": "Dulai Tree API is running"}

@app.post("/chat")
async def chat(body: ChatBody, dulai_sid: str | None = Cookie(None)):
    sid, fields, history = get_session(dulai_sid)   # ← fields here

    history.append({"role":"user","content":body.message})


    """
    Request JSON:  {"message": "<user text>"}
    Response:      ND-JSON stream, one object per line
       {"content": "..."}              ← normal assistant text
       {"function_result": {…}}        ← after a tool call
    """
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": f"KNOWN_FIELDS_JSON = {json.dumps(memory)}"},
    ] + history[-12:]         # send last 12 turns max

    # 1️⃣  start the streamed completion  (SYNC iterator!)
    stream = openai.chat.completions.create(
        model         = "gpt-4o",
        temperature   = 0.4,
        stream        = True,
        messages      = messages,
        functions     = FUNCTIONS,
        function_call = "auto"
    )

    # 2️⃣  generator that converts each chunk to a JSON line
def gen():
    buf, current_name = "", None          # buffer for tool-call args
    assistant_buf     = ""                # plain-text buffer
    history.append({"role": "user", "content": body.message})

    for chunk in stream:                  # ← SYNC iterator from openai
        logging.info("RAW → %s", chunk)

        choice = chunk.choices[0]
        delta  = choice.delta
        if getattr(delta, "function_call", None):
            fc = delta.function_call
            current_name = fc.name or current_name
            buf += fc.arguments or ""

            # tool call finished – we have full JSON args
            if choice.finish_reason == "function_call":
                try:
                    args = json.loads(buf or "{}")

                    # special case: extract_fields just updates session memory
                    if current_name == "extract_fields":
                        fields.update(args)
                        result = args                 # echo back
                    else:
                        result = FUNC_TABLE[current_name](**args)

                    yield json.dumps({"function_result": result}) + "\n"

                except Exception as e:
                    logging.exception("tool call error")
                    yield json.dumps({"error": str(e)}) + "\n"

                buf, current_name = "", None
            continue                                # skip normal text pieces

        # ── normal assistant content token ────────────────────────────────────
        if getattr(delta, "content", None) is not None:
            assistant_buf += delta.content
            yield json.dumps({"content": delta.content}) + "\n"

    # end-of-stream: store whole assistant reply in session history (optional)
    history.append({"role": "assistant", "content": assistant_buf})
    resp = StreamingResponse(gen(), media_type="application/json")
    resp.set_cookie("dulai_sid", sid, max_age=60*60*24*7, path="/")
    return resp
