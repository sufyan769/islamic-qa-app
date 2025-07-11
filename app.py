# -*- coding: utf-8 -*-
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

# إعداد متغيرات البيئة
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

# الاتصال بـ Elasticsearch
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
            raise ValueError("فشل الاتصال بـ Elasticsearch")
    else:
        print("بيانات الاتصال بـ Elasticsearch غير مكتملة")
except (ConnectionError, AuthenticationException, Exception) as e:
    print(f"خطأ في Elasticsearch: {e}")
    sys.exit(1)

# الاتصال بـ Claude
claude_client = None
if ANTHROPIC_API_KEY:
    try:
        claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)
    except Exception as e:
        print(f"Claude Init Error: {e}")

INDEX_NAME = "islamic_texts"

@app.route('/ask', methods=['GET'])
def ask_ai():
    query = request.args.get('q', '').strip()
    author_query = request.args.get('author', '').strip()

    if not query and not author_query:
        return jsonify({"error": "يرجى إدخال سؤال أو اسم مؤلف."}), 400

    sources_retrieved = []
    try:
        query_clauses = []
        author_clauses = []

        if query:
            query_clauses.append({
                "match_phrase": {
                    "text_content": {
                        "query": query,
                        "boost": 200,
                        "slop": 1
                    }
                }
            })
            query_clauses.append({
                "more_like_this": {
                    "fields": ["text_content"],
                    "like": query,
                    "min_term_freq": 1,
                    "max_query_terms": 25,
                    "boost": 50
                }
            })
            query_clauses.append({
                "match": {
                    "text_content": {
                        "query": query,
                        "operator": "and",
                        "boost": 30
                    }
                }
            })
            query_clauses.append({
                "multi_match": {
                    "query": query,
                    "fields": ["text_content^3", "book_title^2", "author_name"],
                    "type": "most_fields",
                    "fuzziness": "AUTO",
                    "boost": 10
                }
            })

        if author_query:
            author_clauses += [
                {"match_phrase": {
                    "author_name": {
                        "query": author_query,
                        "boost": 150
                    }}},
                {"multi_match": {
                    "query": author_query,
                    "fields": ["author_name^5", "author_name.ngram^3"],
                    "type": "best_fields",
                    "fuzziness": "AUTO"
                }}
            ]

        final_query = {}
        if query and author_query:
            final_query = {
                "bool": {
                    "must": [
                        {"bool": {"should": query_clauses, "minimum_should_match": 1}},
                        {"bool": {"should": author_clauses, "minimum_should_match": 1}}
                    ]
                }
            }
        elif query:
            final_query = {"bool": {"should": query_clauses, "minimum_should_match": 1}}
        elif author_query:
            final_query = {"bool": {"should": author_clauses, "minimum_should_match": 1}}
        else:
            final_query = {"match_all": {}}

        search_body = {
            "query": final_query,
            "size": 50,
            "sort": [{"_score": {"order": "desc"}}]
        }

        res = es.search(index=INDEX_NAME, body=search_body)
        for hit in res['hits']['hits']:
            source = hit['_source']
            sources_retrieved.append({
                "book_title": source.get('book_title', 'غير معروف'),
                "author_name": source.get('author_name', 'غير معروف'),
                "part_number": source.get('part_number', 'غير معروف'),
                "page_number": source.get('page_number', 'غير معروف'),
                "text_content": source.get('text_content', '')
            })

        context = "\n\n".join([
            f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
            for s in sources_retrieved
        ]) or "لا توجد مصادر."

        gemini_answer = ""
        if GEMINI_API_KEY:
            try:
                prompt = f"أجب عن السؤال التالي: '{query}' بناءً على النصوص:\n---\n{context}\n---\nإذا لم تجد الإجابة، فاذكر ذلك."
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "text/plain"}
                }
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
                r = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=30)
                d = r.json()
                gemini_answer = d.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '') or "—"
            except Exception as e:
                gemini_answer = f"خطأ من Gemini: {e}"

        claude_answer = ""
        if claude_client:
            try:
                claude_prompt = f"أجب عن السؤال التالي: '{query}' بناءً على النصوص:\n---\n{context}\n---\nإذا لم تجد الإجابة، فاذكر ذلك."
                message = claude_client.messages.create(
                    model="claude-3-5-sonnet-20241022",
                    max_tokens=1000,
                    messages=[{"role": "user", "content": claude_prompt}]
                )
                claude_answer = message.content[0].text
            except Exception as e:
                claude_answer = f"خطأ من Claude: {e}"

        return jsonify({
            "question": query,
            "gemini_answer": gemini_answer,
            "claude_answer": claude_answer,
            "sources_retrieved": sources_retrieved
        })

    except Exception as e:
        return jsonify({"error": f"حدث خطأ: {e}"}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("PORT", 5000))
