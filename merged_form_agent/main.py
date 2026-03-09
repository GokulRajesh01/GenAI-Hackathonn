import os
import json
import math
import pathlib
import sys
import requests
import fitz  # PyMuPDF
from dotenv import load_dotenv

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = pathlib.Path(__file__).parent

INPUTS_DIR = ROOT / "inputs"
OUTPUTS_DIR = ROOT / "outputs"
INPUTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

SCHEMA_PATH = INPUTS_DIR / "forms_data.json"

# ── Constants ──────────────────────────────────────────────────────────────────
# Models
GEMINI_GENERATE_MODEL = "gemini-2.5-flash"
GEMINI_EMBED_MODEL = "text-embedding-004"

BANNER = """
╔══════════════════════════════════════════════════════╗
║        Dynamic RAG-based Form Filler Agent           ║
║               Powered by Gemini AI                   ║
╚══════════════════════════════════════════════════════╝
"""

# ── Helpers ────────────────────────────────────────────────────────────────────
def get_api_key():
    load_dotenv(dotenv_path=ROOT / ".env")
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print(
            "❌ GEMINI_API_KEY not found!\n"
            "   Add it to your .env file or export it as an environment variable.\n"
            "   Get a FREE key at: https://aistudio.google.com/apikey\n"
        )
        sys.exit(1)
    return key


def generate_content(prompt: str, temperature: float = 0.7, json_mode: bool = False) -> str:
    """Generate text using Gemini 2.5 flash."""
    api_key = get_api_key()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_GENERATE_MODEL}:generateContent?key={api_key}"
    
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
        }
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=30)
    if not resp.ok:
        raise RuntimeError(f"Generation API Error {resp.status_code}: {resp.text}")
    
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


# ── Step 1: Form Identification ──────────────────────────────────────────────
def load_forms_data() -> list[dict]:
    if not SCHEMA_PATH.exists():
        print(f"❌ Could not find {SCHEMA_PATH}.")
        sys.exit(1)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def retrieve_form_from_intent(user_query: str, forms: list[dict]) -> dict:
    print("\n🔍 Analyzing your request and searching for the right form...")
    
    # Bundle available forms as context to Gemini for RAG
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
        print(f"⚠️ Error parsing intent: {e}")
        return None
        
    for form in forms:
        if form["form_id"] == best_id:
            print(f"✅ Found match: {form['form_name']}")
            return form
            
    print("❌ Could not confidently match your request to a known form.")
    return None


# ── Step 2: PDF Template Generation ────────────────────────────────────────────
def generate_pdf_template(form_data: dict) -> pathlib.Path:
    template_path = INPUTS_DIR / f"template_{form_data['form_id']}.pdf"
    
    print("\n📄 Generating dynamic PDF template...")
    
    PAGE_WIDTH, PAGE_HEIGHT = 595, 842
    MARGIN_LEFT, MARGIN_RIGHT = 50, PAGE_WIDTH - 50
    FIELD_HEIGHT, LINE_SPACING = 20, 45
    START_Y = 120
    
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    
    # Title
    page.insert_text((MARGIN_LEFT, 60), form_data["form_name"], fontsize=14, fontname="helv", color=(0, 0, 0.5))
    page.insert_text((MARGIN_LEFT, 80), "Generated dynamically from Schema", fontsize=8, fontname="helv", color=(0.4, 0.4, 0.4))
    page.draw_line((MARGIN_LEFT, 90), (MARGIN_RIGHT, 90), color=(0, 0, 0.5), width=1.5)
    
    y = START_Y
    fields = form_data.get("fields", {})
    
    for field_id, field_desc in fields.items():
        if y + LINE_SPACING > PAGE_HEIGHT - 60:
            page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
            y = 60
            
        label_text = f"{field_id.replace('_', ' ').title()} *"
        page.insert_text((MARGIN_LEFT, y), label_text, fontsize=9, fontname="helv", color=(0.1, 0.1, 0.1))
        
        rect = fitz.Rect(MARGIN_LEFT, y + 12, MARGIN_RIGHT, y + 12 + FIELD_HEIGHT)
        widget = fitz.Widget()
        widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_name = field_id
        widget.field_value = ""
        widget.rect = rect
        widget.text_fontsize = 9
        widget.border_color = (0.5, 0.5, 0.5)
        widget.border_width = 0.5
        widget.fill_color = (0.97, 0.97, 1.0)
        page.add_widget(widget)
        
        y += LINE_SPACING

    doc.save(str(template_path))
    doc.close()
    print(f"✅ Saved dynamic template to {template_path.name}")
    return template_path


# ── Step 3: Conversational Form Filling ───────────────────────────────────────
def conversational_fill(form_data: dict) -> dict:
    fields = form_data.get("fields", {})
    form_title = form_data["form_name"]
    
    print(f"\n💬 Let's gather the information needed for your {form_title}.")
    print("───────────────────────────────────────────────────────")
    
    # Batch generate simple conversational questions
    print("⏳ AI is preparing conversational questions...\n")
    fields_list = "\n".join(f"- {k}: {v}" for k, v in fields.items())
    
    batch_prompt = f"""You are assisting a user in filling out a '{form_title}' form.
Generate a simple, conversational question for each of the following fields. 
Return ONLY a valid JSON object mapping each field ID to its generated question string. No markdown fences.
Fields:
{fields_list}
"""
    try:
        q_text = generate_content(batch_prompt, temperature=0.7, json_mode=True)
        # Fix possible formatting issues from Gemini API JSON mode
        q_text = q_text.replace('\n', ' ').replace('\r', '')
        generated_questions = json.loads(q_text)
    except Exception as e:
        print(f"⚠️ Warning: Could not pre-generate AI questions ({e}). Using falbacks.")
        generated_questions = {}

    raw_answers = {}
    
    for i, (fid, desc) in enumerate(fields.items(), 1):
        question = generated_questions.get(fid, f"Please provide the {fid.replace('_', ' ')}: ({desc})")
        print(f"Q{i}/{len(fields)}: {question}")
        
        while True:
            ans = input(" ➜ Your Answer: ").strip()
            if not ans:
                print("   ⚠ Please provide an answer (this is required).")
                continue
            
            # Simple validation with LLM
            val_prompt = f"Field: '{fid}', Description: '{desc}'. User answered: '{ans}'. Is this plausible? Reply 'VALID' or give a short correction."
            val_res = generate_content(val_prompt, temperature=0.1)
            
            if "VALID" not in val_res.upper():
                print(f"   ❌ {val_res}")
                continue
                
            raw_answers[fid] = ans
            break
            
    # Final AI cleanup
    print("\n✅ All answers gathered! Validating & cleaning up...")
    raw_str = json.dumps(raw_answers, ensure_ascii=False)
    clean_prompt = f"Fix obvious typos/capitals in these answers. Keep keys exactly identical. Output JSON only.\nFields: {fields_list}\nAnswers: {raw_str}"
    
    try:
        clean_text = generate_content(clean_prompt, temperature=0.1, json_mode=True)
        cleaned_answers = json.loads(clean_text)
    except Exception as e:
        print(f"⚠️ Clean-up AI failed: {e}")
        cleaned_answers = raw_answers
        
    print("\n📋 Final Summary:")
    print("───────────────────────────────────────────────────────")
    for k, v in cleaned_answers.items():
        print(f" {k.replace('_', ' ').title():25s} : {v}")
    
    return cleaned_answers


# ── Step 4: PDF Filler ────────────────────────────────────────────────────────
def fill_pdf(template_path: pathlib.Path, data: dict, form_id: str) -> str:
    out_path = OUTPUTS_DIR / f"{form_id}_filled.pdf"
    
    doc = fitz.open(str(template_path))
    for page in doc:
        for widget in page.widgets():
            fid = widget.field_name
            if fid in data:
                widget.field_value = str(data[fid])
                widget.update()
                
    doc.save(str(out_path))
    doc.close()
    
    return str(out_path)


# ── Main Pipeline Orchestrator ────────────────────────────────────────────────
def main():
    print(BANNER)
    # Load dotenv exactly once before doing anything else
    load_dotenv(dotenv_path=ROOT / ".env")
    get_api_key()
    
    forms = load_forms_data()
    print("Welcome! I am your AI Government Forms Assistant.")
    user_query = input("Tell me, what are you trying to apply for today?\n ➜ ").strip()
    
    if not user_query:
        print("Goodbye!")
        return

    # 1. RAG
    best_form = retrieve_form_from_intent(user_query, forms)
    if not best_form:
        return
        
    # 2. Dynamic Template
    template_path = generate_pdf_template(best_form)
    
    # 3. Agent Collection
    collected_data = conversational_fill(best_form)
    
    # 4. Fill PDF
    out_pdf = fill_pdf(template_path, collected_data, best_form['form_id'])
    
    print("───────────────────────────────────────────────────────")
    print(f"🎉 Success! Your filled application has been saved to:\n   {out_pdf}")
    print("───────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
