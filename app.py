# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
from anthropic import Anthropic
import sys

app = Flask(__name__)
CORS(app)

CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

INDEX_NAME = "islamic_texts"

es = None
try:
    if CLOUD_ID and ELASTIC_USERNAME and ELASTIC_PASSWORD:
        es = Elasticsearch(
            cloud_id=CLOUD_ID,
            basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD),
            verify_certs=True,
            ssl_show_warn=False,
            request_timeout=60
        )
        if not es.ping():
            raise ValueError("Connection to Elasticsearch failed!")
except (ConnectionError, AuthenticationException, Exception) as e:
    print(f"Elasticsearch connection error: {e}")
    sys.exit(1)

claude_client = None
if ANTHROPIC_API_KEY:
    try:
        claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as e:
        print(f"Claude client init error: {e}")

@app.route('/ask')
def ask():
    query = request.args.get("q", "").strip()
    mode = request.args.get("mode", "default")
    from_ = int(request.args.get("from", 0))
    size = int(request.args.get("size", 7))

    if not query:
        return jsonify({"error": "يرجى إدخال استعلام."}), 400

    sources = []
    try:
        if es and mode != 'ai_only':
            q = {
                "bool": {
                    "should": [
                        {"match_phrase": {"text_content": {"query": query, "boost": 100}}},
                        {"match": {"text_content": {"query": query, "operator": "and", "boost": 30}}},
                        {"multi_match": {
                            "query": query,
                            "fields": [
                                "text_content^3",
                                "text_content.ngram^2",
                                "book_title^1.5",
                                "author_name^1.2"
                            ],
                            "fuzziness": "AUTO"
                        }}
                    ],
                    "minimum_should_match": 1
                }
            }
            body = {"query": q, "from": from_, "size": size}
            res = es.search(index=INDEX_NAME, body=body)
            for hit in res['hits']['hits']:
                s = hit['_source']
                sources.append({
                    "book_title": s.get("book_title", ""),
                    "author_name": s.get("author_name", ""),
                    "part_number": s.get("part_number", ""),
                    "page_number": s.get("page_number", ""),
                    "text_content": s.get("text_content", "")
                })

        context = "\n\n".join([
            f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
            for s in sources
        ]) if sources else ""

        claude_answer = ""
        if claude_client:
            prompt = f"""
            أجب باختصار وبشكل مباشر عن السؤال التالي:
            {query}
            {"استنادًا إلى هذه النصوص:\n" + context if context else ""}
            """
            try:
                msg = claude_client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": prompt.strip()}]
                )
                claude_answer = msg.content[0].text.strip()
            except Exception as e:
                claude_answer = f"Claude API error: {e}"

        return jsonify({
            "claude_answer": claude_answer,
            "sources_retrieved": sources if mode != 'ai_only' else []
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
