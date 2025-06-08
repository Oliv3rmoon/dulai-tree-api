# app.py -------------------------------------------------------
import os, uuid, datetime as dt, itertools, json
from typing import List, Dict, Any

import openai
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
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

# -----------------------------------------------------------------
# OpenAI function schema (matches FastAPI helpers)
# -----------------------------------------------------------------
FUNCTIONS = [
    {
        "name": "get_estimate",
        "description": "Return a rough dollar estimate for the service.",
        "parameters": {
            "type": "object",
            "properties": {
                "service_type": {"type": "string"},
                "tree_count":   {"type": "integer"},
                "height_ft":    {"type": "integer"},
                "emergency":    {"type": "boolean"},
                "zip":          {"type": "string"}
            },
            "required": ["service_type","tree_count","height_ft","emergency","zip"]
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
                    "items": {"type": "string"}
                },
                "crew_size": {"type": "integer"},
                "max_slots": {"type": "integer"}
            },
            "required": ["preferred_date_range", "preferred_times_of_day"]
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

# Map function names → actual Python callables
FUNC_TABLE = {
    "get_estimate":   get_estimate,
    "find_open_slots":find_open_slots,
    "book_job":       book_job
}

# -----------------------------------------------------------------
# Chat endpoint
# -----------------------------------------------------------------
class ChatBody(BaseModel):
    message: str


@app.post("/chat")
async def chat(body: ChatBody):
    """
    Expects a JSON body: {"message": "<user text>"}
    Returns a streaming JSON Lines response:
      {"content": "..."} or {"function_result": {...}}
    """

    stream = openai.chat.completions.create(
        model        = "gpt-4o",
        temperature  = 0.4,
        stream       = True,
        messages     = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": body.message}
        ],
        functions    = FUNCTIONS,
        function_call= "auto"
    )

    def gen():
        for chunk in stream:                         
            choice = chunk.choices[0]

            
            if choice.delta and choice.delta.get("function_call"):
                fc = choice.delta.function_call
                if fc.name and fc.arguments:
                    args   = json.loads(fc.arguments)
                    result = FUNC_TABLE[fc.name](**args)
                    yield json.dumps({"function_result": result}) + "\n"

            
            elif choice.delta and choice.delta.get("content") is not None:
                yield json.dumps({"content": choice.delta.content}) + "\n"

    return StreamingResponse(gen(), media_type="application/json")
