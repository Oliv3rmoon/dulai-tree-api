from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import os, openai, uuid, datetime as dt

openai.api_key = os.getenv("OPENAI_API_KEY")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],        # tighten later
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- booking helpers (stub) ---------------------------------
def get_estimate(service_type, tree_count, height_ft, emergency, zip):
    base = {"trim": 75, "removal": 5 * height_ft,
            "stump_grind": 150, "hedge": 40}[service_type] * tree_count
    if emergency: base *= 1.4
    return round(base + (0 if zip.startswith("95") else 50), -1)

calendar = {}        # replace with real DB
def reserve(slot_id, payload):
    calendar[slot_id] = payload
    return slot_id

# ---- OpenAI function schema ---------------------------------
functions = [
  {
    "name": "get_estimate",
    "parameters": { "type": "object",
      "properties": {
        "service_type": {"type":"string"},
        "tree_count":   {"type":"integer"},
        "height_ft":    {"type":"integer"},
        "emergency":    {"type":"boolean"},
        "zip":          {"type":"string"} },
      "required": ["service_type","tree_count","height_ft","emergency","zip"]}
  },
  {
    "name": "find_open_slots",
    "parameters": { "type": "object",
      "properties": {
        "preferred_date_range": {
          "type":"object",
          "properties":{
            "start_date":{"type":"string"},
            "end_date":  {"type":"string"}},
          "required":["start_date","end_date"]},
        "preferred_times_of_day":{
          "type":"array","items":{"type":"string"}},
        "max_slots":{"type":"integer"} },
      "required":["preferred_date_range","preferred_times_of_day"]}
  },
  {
    "name": "book_job",
    "parameters": { "type": "object",
      "properties": {
        "slot_id":{"type":"string"},
        "job_payload":{"type":"object"} },
      "required":["slot_id","job_payload"]}
  }
]

SYSTEM_PROMPT = open("system_prompt.txt").read()  # paste V1.1 text here

# ---- /chat endpoint ----------------------------------------
@app.post("/chat")
async def chat(req: Request):
    body = await req.json()
    user_msg = body["message"]

    response = openai.chat.completions.create(
        model="gpt-4o",
        temperature=0.4,
        stream=True,
        messages=[
          {"role":"system","content": SYSTEM_PROMPT},
          {"role":"user","content": user_msg}
        ],
        functions=functions,
        function_call="auto"
    )
    # stream raw chunks to caller
    def gen():
        for chunk in response:
            yield chunk.choices[0].delta.model_dump_json(exclude_none=True)
    return gen()
