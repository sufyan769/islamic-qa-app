from flask import Flask, request, jsonify
from flask_cors import CORS
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
from anthropic import Anthropic
import os, sys, re, requests

app = Flask(__name__)
CORS(app)

CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
INDEX_NAME = "islamic_texts"

CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY")
claude_client = Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

AR_STOPWORDS = {"من", "في", "على", "إلى", "عن", "ما", "إذ", "أو", "و", "ثم", "أن", "إن", "كان", "قد", "لم", "لن", "لا", "هذه", "هذا", "ذلك", "الذي", "التي", "ال"}

es = None
try:
    if CLOUD_ID and ELASTIC_USERNAME and ELASTIC_PASSWORD:
        es = Elasticsearch(cloud_id=CLOUD_ID, basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD))
        if not es.ping(): raise ValueError("Elastic unreachable")
    else:
        raise ValueError("Elastic env vars missing")
except Exception as e:
    print("Elastic error:", e)
    sys.exit(1)

@app.route("/ask")
def ask():
    query = request.args.get("q", "").strip()
    mode = request.args.get("mode", "default")
    frm = int(request.args.get("from", 0))
    size = int(request.args.get("size", 20))

    if not query:
        return jsonify({"error": "يرجى إدخال استعلام."}), 400

    words = [w for w in re.findall(r"[\u0600-\u06FF]+", query) if len(w) > 2 and w not in AR_STOPWORDS]
    sources = []

    def build_query(precise=True):
        should = []
        if precise:
            should.append({"match_phrase": {"text_content": {"query": query, "boost": 100}}})
        should += [{"match": {"text_content": {"query": w, "operator": "and", "boost": 10}}} for w in words]
        return {"bool": {"should": should, "minimum_should_match": 1}}

    try:
        if mode != "ai_only":
            for stage in [True, False]:
                res = es.search(index=INDEX_NAME, body={"query": build_query(stage), "from": frm, "size": size, "sort": ["_score"]})
                if res["hits"]["hits"]:
                    for hit in res["hits"]["hits"]:
                        doc = hit["_source"]
                        sources.append({
                            "book_title": doc.get("book_title", ""),
                            "author_name": doc.get("author_name", ""),
                            "part_number": doc.get("part_number", ""),
                            "page_number": doc.get("page_number", ""),
                            "text_content": doc.get("text_content", "")
                        })
                    break
    except Exception as e:
        return jsonify({"error": f"Search failure: {e}"}), 500

    claude_answer = ""
    if mode in ("default", "ai_only") and claude_client:
        context = "\n\n".join([
            f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
            for s in sources
        ])
        prompt = f"هل يوجد في الفقرات التالية جواب مباشر عن السؤال: {query}\n{context}\nإذا وُجد، فاذكر الجواب مع الإشارة إلى الفقرة."
        try:
            msg = claude_client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=800, messages=[{"role": "user", "content": prompt}])
            claude_answer = msg.content[0].text.strip()
        except Exception as e:
            claude_answer = f"Claude error: {e}"

    gemini_answer = ""
    if mode in ("default", "ai_only") and GEMINI_API_KEY:
        try:
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GEMINI_API_KEY}"
            gemini_payload = {
                "contents": [{"parts": [{"text": f"أجب باختصار عن السؤال التالي: {query}"}]}]
            }
            g_res = requests.post(gemini_url, json=gemini_payload)
            gemini_data = g_res.json()
            gemini_answer = gemini_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        except Exception as e:
            gemini_answer = f"Gemini error: {e}"

    return jsonify({
        "claude_answer": claude_answer if mode != "full" else "",
        "gemini_answer": gemini_answer if mode != "full" else "",
        "sources_retrieved": [] if mode == "ai_only" else sources
    })

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
