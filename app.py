# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import os
import sys
import requests
from anthropic import Anthropic
import json

app = Flask(__name__)
CORS(app)  # السماح بالطلبات من جميع المصادر (مهم لـ CORS)

# إعدادات Elasticsearch
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
INDEX_NAME = "islamic_texts"

# إعداد مفاتيح النماذج
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
claude_client = Anthropic(api_key=ANTHROPIC_API_KEY) if ANTHROPIC_API_KEY else None

# إعداد Elasticsearch
es = None
try:
    if CLOUD_ID and ELASTIC_USERNAME and ELASTIC_PASSWORD:
        es = Elasticsearch(
            cloud_id=CLOUD_ID,
            basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD)
        )
        if not es.ping():
            raise ValueError("Elasticsearch is not reachable.")
except Exception as e:
    print("Elasticsearch Error:", e)
    sys.exit(1)

@app.route("/ask", methods=["GET"])
def ask():
    query = request.args.get("q", "")
    mode = request.args.get("mode", "default")
    from_ = int(request.args.get("from", 0))
    size = int(request.args.get("size", 20))

    if not query:
        return jsonify({"error": "يجب إدخال سؤال."}), 400

    sources = []
    try:
        if es:
            search_body = {
                "query": {
                    "bool": {
                        "should": [
                            {
                                "match_phrase": {
                                    "text_content": {
                                        "query": query,
                                        "boost": 100
                                    }
                                }
                            },
                            {
                                "match": {
                                    "text_content": {
                                        "query": query,
                                        "operator": "and",
                                        "boost": 20
                                    }
                                }
                            },
                            {
                                "multi_match": {
                                    "query": query,
                                    "fields": [
                                        "text_content^3",
                                        "text_content.ngram^2",
                                        "book_title^1.5",
                                        "author_name^1.2"
                                    ],
                                    "type": "best_fields",
                                    "fuzziness": "AUTO"
                                }
                            }
                        ],
                        "minimum_should_match": 1
                    }
                },
                "from": from_,
                "size": size
            }
            res = es.search(index=INDEX_NAME, body=search_body)
            sources = [
                {
                    "book_title": hit['_source'].get("book_title", "غير معروف"),
                    "author_name": hit['_source'].get("author_name", "غير معروف"),
                    "text_content": hit['_source'].get("text_content", "لا يوجد نص")
                } for hit in res['hits']['hits']
            ]
    except Exception as e:
        print("Search error:", e)
        return jsonify({"error": f"Search failed: {e}"}), 500

    # AI Response
    claude_answer = ""
    if mode != "full" and claude_client:
        try:
            context = "\n\n".join([f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالنص: {s['text_content']}" for s in sources]) or "لا توجد مصادر"
            prompt = f"""
            أجب عن السؤال التالي بدقة علمية:
            السؤال: {query}
            السياق:
            {context}
            إذا لم تجد الإجابة، فاذكر أنه لا يوجد مصدر كافٍ.
            """
            message = claude_client.messages.create(
                model="claude-3-5-sonnet-20241022",
                max_tokens=1000,
                messages=[{"role": "user", "content": prompt}]
            )
            claude_answer = message.content[0].text
        except Exception as e:
            print("Claude error:", e)
            claude_answer = "حدث خطأ في استدعاء Claude."

    return jsonify({
        "claude_answer": claude_answer if mode != "full" else "",
        "sources_retrieved": sources
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
