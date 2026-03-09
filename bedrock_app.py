import json
import boto3
import os
import uuid
import pathlib
import requests
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4

# ── Paths & Constants ───────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).parent
INPUTS_DIR = ROOT / "inputs"
OUTPUTS_DIR = pathlib.Path("/tmp/outputs")
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
STATE_DIR = pathlib.Path("/tmp/sessions")
STATE_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA_PATH = INPUTS_DIR / "forms_data.json"
BEDROCK_MODEL_ID = os.environ.get("MODEL_ID", "eu.amazon.nova-micro-v1:0")

def get_bedrock_client():
    return boto3.client('bedrock-runtime', region_name='eu-north-1')

def generate_content(prompt: str, temperature: float = 0.7, json_mode: bool = False) -> str:
    """Generate text using Amazon Bedrock Converse API."""
    messages = [{"role": "user", "content": [{"text": prompt}]}]
    
    try:
        client = get_bedrock_client()
        response = client.converse(
            modelId=BEDROCK_MODEL_ID,
            messages=messages,
            inferenceConfig={
                "temperature": temperature
            }
        )
        
        output_text = response['output']['message']['content'][0]['text']
        
        # Strip markdown json blocks if json_mode is requested
        if json_mode:
            output_text = output_text.strip()
            if output_text.startswith("```json"):
                output_text = output_text[7:]
            if output_text.startswith("```"):
                output_text = output_text[3:]
            if output_text.endswith("```"):
                output_text = output_text[:-3]
            output_text = output_text.strip()
            
        return output_text
        
    except Exception as e:
        print(f"Bedrock API Error: {str(e)}")
        raise RuntimeError(f"Bedrock API Error: {str(e)}")

def translate_to_malayalam(text: str) -> str:
    """Translate English text to Malayalam using Sarvam AI."""
    api_key = os.environ.get("SARVAM_API_KEY", "")
    if not api_key:
        return text  # Fallback to English if no key provided
        
    url = "https://api.sarvam.ai/translate"
    payload = {
        "input": text,
        "source_language_code": "en-IN",
        "target_language_code": "ml-IN",
        "speaker_gender": "Female",
        "mode": "formal",
        "model": "sarvam-translate:v1"
    }
    headers = {
        "Content-Type": "application/json",
        "api-subscription-key": api_key
    }
    
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        if resp.ok:
            data = resp.json()
            return data.get("translated_text", text)
        else:
            print(f"Sarvam translation error {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"Sarvam translation exception: {str(e)}")
        
    return text

def load_forms_data() -> list[dict]:
    if not SCHEMA_PATH.exists():
        return []
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

# ── State Management ──────────────────────────────────────────────────────────
def load_session(session_id: str) -> dict:
    state_file = STATE_DIR / f"{session_id}.json"
    if state_file.exists():
        with open(state_file, "r") as f:
            return json.load(f)
    return {
        "session_id": session_id,
        "form_identified": False,
        "form_data": None,
        "questions_generated": {},
        "answers_collected": {},
        "current_field_index": 0,
        "status": "INIT" # INIT, COLLECTING, COMPLETED
    }

def save_session(state: dict):
    state_file = STATE_DIR / f"{state['session_id']}.json"
    with open(state_file, "w") as f:
        json.dump(state, f)

# ── Step 1: Form Identification ──────────────────────────────────────────────
def retrieve_form_from_intent(user_query: str, forms: list[dict]) -> dict:
    available_forms_ctx = "\n".join([f"- ID: {f['form_id']} | Name: {f['form_name']} | Desc: {f['description']} | Keywords: {f.get('keywords', '')}" for f in forms])
    
    prompt = f"""You are a Form Assistant. The user wants to apply for or fill out a form.
USER QUERY: "{user_query}"

AVAILABLE FORMS:
{available_forms_ctx}

Identify the ONE most relevant form ID for the user's query. 
Return ONLY a valid JSON object with the key "form_id" and the exact ID string as value.
If no form matches the query, return a JSON object with empty string for "form_id".
"""
    try:
        response_text = generate_content(prompt, temperature=0.1, json_mode=True)
        response_json = json.loads(response_text)
        best_id = response_json.get("form_id", "")
    except Exception as e:
        print(f"Error parsing intent: {e}")
        return None
        
    for form in forms:
        if form["form_id"] == best_id:
            return form
    return None

def generate_questions(form_data: dict) -> dict:
    fields = form_data.get("fields", {})
    form_title = form_data["form_name"]
    fields_list = "\n".join(f"- {k}: {v}" for k, v in fields.items())
    
    batch_prompt = f"""You are assisting a user in filling out a '{form_title}' form.
Generate a simple, conversational question for each of the following fields. 
Return ONLY a valid JSON object mapping each field ID to its generated question string. No markdown fences.
Fields:
{fields_list}
"""
    try:
        q_text = generate_content(batch_prompt, temperature=0.7, json_mode=True)
        q_text = q_text.replace('\n', ' ').replace('\r', '')
        return json.loads(q_text)
    except Exception as e:
        print(f"Warning: Could not pre-generate AI questions: {e}")
        return {}

def generate_filled_pdf(form_data: dict, data: dict, session_id: str) -> str:
    out_path = OUTPUTS_DIR / f"{form_data['form_id']}_{session_id}_filled.pdf"
    
    c = canvas.Canvas(str(out_path), pagesize=A4)
    width, height = A4
    
    y = height - 50
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, form_data["form_name"])
    y -= 30
    
    fields = form_data.get("fields", {})
    for field_id, field_desc in fields.items():
        if y < 50:
            c.showPage()
            y = height - 50
        
        c.setFont("Helvetica-Bold", 10)
        c.drawString(50, y, f"{field_id.replace('_', ' ').title()}")
        y -= 15
        
        c.setFont("Helvetica", 10)
        c.drawString(50, y, str(data.get(field_id, "")))
        y -= 25
    
    c.save()
    return str(out_path)

def lambda_handler(event, context):
    try:
        print(f"Event: {json.dumps(event)}")
        body = json.loads(event.get('body', '{}'))
        user_query = body.get('prompt', '')
        session_id = body.get('session_id', str(uuid.uuid4()))
        
        if not user_query:
            return _respond(400, {"error": "Missing 'prompt' in request body"})

        state = load_session(session_id)
        forms = load_forms_data()

        # Step 1: Detect Intent
        if state["status"] == "INIT":
            best_form = retrieve_form_from_intent(user_query, forms)
            if not best_form:
                return _respond(200, {
                    "response": "I couldn't identify the form you're looking for. Can you rephrase or tell me what you want to apply for?",
                    "session_id": session_id
                })
            
            # Form identified
            state["form_identified"] = True
            state["form_data"] = best_form
            state["status"] = "COLLECTING"
            state["questions_generated"] = generate_questions(best_form)
            # No need to pre-generate template for reportlab since we assemble dynamically at the end
            
            # Start asking
            state["field_keys"] = list(best_form["fields"].keys())
            state["current_field_index"] = 0
            
            first_field = state["field_keys"][0]
            first_q = state["questions_generated"].get(first_field, f"Please provide: {first_field.replace('_', ' ')}")
            
            save_session(state)
            return _respond(200, {
                "response": f"Great! I see you want the {best_form['form_name']}.\n{first_q}",
                "session_id": session_id
            })

        # Step 2: Collecting answers
        elif state["status"] == "COLLECTING":
            current_field = state["field_keys"][state["current_field_index"]]
            desc = state["form_data"]["fields"][current_field]
            
            # Simple LLM Validation (optional but good)
            val_prompt = f"Field: '{current_field}', Description: '{desc}'. User answered: '{user_query}'. Is this plausible? Reply 'VALID' or give a short correction."
            val_res = generate_content(val_prompt, temperature=0.1)
            
            if "VALID" not in val_res.upper():
                return _respond(200, {
                    "response": f"Hmm, that doesn't seem quite right. {val_res}\n\nPlease try again:",
                    "session_id": session_id
                })
            
            # Save valid answer
            state["answers_collected"][current_field] = user_query
            state["current_field_index"] += 1
            
            # Ask next question or finish
            if state["current_field_index"] < len(state["field_keys"]):
                next_field = state["field_keys"][state["current_field_index"]]
                next_q = state["questions_generated"].get(next_field, f"Please provide: {next_field.replace('_', ' ')}")
                save_session(state)
                return _respond(200, {
                    "response": next_q,
                    "session_id": session_id
                })
            else:
                # Finished collecting
                state["status"] = "COMPLETED"
                # Clean up answers (optional, skipped for speed)
                cleaned_answers = state["answers_collected"]
                
                out_pdf = generate_filled_pdf(state["form_data"], cleaned_answers, session_id)
                
                # Optional: return PDF as Base64 so frontend could download it
                # For now just success msg
                save_session(state)
                return _respond(200, {
                    "response": f"🎉 Success! Your {state['form_data']['form_name']} has been filled out completely.",
                    "session_id": session_id
                })

        # Step 3: Finished
        elif state["status"] == "COMPLETED":
            return _respond(200, {
                "response": "You have already completed this form in this session! Type something else to start over (which will require a new session).",
                "session_id": session_id
            })

    except Exception as e:
        print(f"Error: {str(e)}")
        return _respond(500, {"error": str(e)})

def _respond(status_code: int, body_dict: dict):
    if status_code == 200 and "response" in body_dict:
        # Translate the bot response into Malayalam before returning
        body_dict["response"] = translate_to_malayalam(body_dict["response"])

    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body_dict)
    }
