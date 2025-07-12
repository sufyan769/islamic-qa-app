# app.py – بحث مرحلتين تلقائيًا مع إصلاح فشل النتائج
from flask import Flask, request, jsonify
from flask_cors import CORS
from elasticsearch import Elasticsearch
from anthropic import Anthropic
import os, sys, re

app = Flask(__name__)
CORS(app)

# إعدادات الاتصال
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
INDEX_NAME = "islamic_texts"

CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY")
claude_client = Anthropic(api_key=CLAUDE_KEY) if CLAUDE_KEY else None

# كلمات توقف
AR_STOPWORDS = {"من", "في", "على", "إلى", "عن", "ما", "إذ", "أو", "و", "ثم", "أن", "إن", "كان", "قد", "لم", "لن", "لا", "هذه", "هذا", "ذلك", "الذي", "التي", "ال"}

# Elasticsearch
try:
    es = Elasticsearch(cloud_id=CLOUD_ID, basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD))
    if not es.ping():
        raise ValueError("Elastic unreachable")
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

    def build_query(strict=True):
        should = []
        if strict:
            should.append({"match_phrase": {"text_content": {"query": query, "boost": 100}}})
        should += [{"match": {"text_content": {"query": w, "boost": 10}}} for w in words]
        return {"bool": {"should": should, "minimum_should_match": 1}}

    if mode != "ai_only":
        for use_strict in [True, False]:
            try:
                result = es.search(index=INDEX_NAME, body={
                    "query": build_query(use_strict),
                    "from": frm,
                    "size": size,
                    "sort": ["_score"]
                })
                hits = result.get("hits", {}).get("hits", [])
                if hits:
                    sources = [{
                        "book_title": hit["_source"].get("book_title", ""),
                        "author_name": hit["_source"].get("author_name", ""),
                        "part_number": hit["_source"].get("part_number", ""),
                        "page_number": hit["_source"].get("page_number", ""),
                        "text_content": hit["_source"].get("text_content", "")
                    } for hit in hits]
                    break
            except Exception as e:
                return jsonify({"error": f"Search failure: {e}"}), 500

    claude_answer = ""
    if mode in ("default", "ai_only") and claude_client:
        context = "\n\n".join([
            f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
            for s in sources
        ])
        prompt = f"أجب مباشرة عن السؤال:\n{query}\n" + (f"استنادًا إلى النصوص التالية:\n{context}" if context else "")
        try:
            msg = claude_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}]
            )
            claude_answer = msg.content[0].text.strip()
        except Exception as e:
            claude_answer = f"Claude error: {e}"

    return jsonify({
        "claude_answer": claude_answer if mode != "full" else "",
        "sources_retrieved": [] if mode == "ai_only" else sources
    })

@app.route("/view/<doc_id>")
def view(doc_id):
    try:
        res = es.get(index=INDEX_NAME, id=doc_id)
        return jsonify(res["_source"])
    except:
        return jsonify({"error": "Document not found"}), 404

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
