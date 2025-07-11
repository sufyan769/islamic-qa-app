# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import requests
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import sys
from anthropic import Anthropic
import json

app = Flask(__name__)
CORS(app)

CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

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
        print("Successfully connected to Elasticsearch!")
    else:
        print("Elasticsearch environment variables not fully set. Skipping connection.")
except (ConnectionError, AuthenticationException, Exception) as e:
    print(f"Elasticsearch Error: {e}")
    sys.exit(1)

claude_client = None
if ANTHROPIC_API_KEY:
    try:
        claude_client = Anthropic(api_key=ANTHROPIC_API_KEY)
        print("Claude client initialized successfully.")
    except Exception as e:
        print(f"Error initializing Claude client: {e}")
else:
    print("ANTHROPIC_API_KEY not set. Claude API will not be available.")

INDEX_NAME = "islamic_texts"

@app.route('/ask', methods=['GET'])
def ask_ai():
    query = request.args.get('q', '')
    author_query = request.args.get('author', '')

    if not query and not author_query:
        return jsonify({"error": "يرجى تقديم سؤال أو اسم مؤلف."}), 400

    sources_retrieved = []
    try:
        if es:
            query_clauses = []
            author_clauses = []

            if query:
                query_clauses += [
                    {"multi_match": {
                        "query": query,
                        "fields": [
                            "text_content^4",
                            "text_content.ngram^2",
                            "book_title^2",
                            "author_name"
                        ],
                        "type": "best_fields",
                        "fuzziness": "AUTO",
                        "operator": "or"
                    }},
                    {"match_phrase": {
                        "text_content": {
                            "query": query,
                            "boost": 80,
                            "slop": 1
                        }}},
                    {"match": {
                        "text_content": {
                            "query": query,
                            "operator": "and",
                            "boost": 20
                        }}}
                ]

            if author_query:
                author_clauses += [
                    {"multi_match": {
                        "query": author_query,
                        "fields": ["author_name^5", "author_name.ngram^3"],
                        "type": "best_fields",
                        "fuzziness": "AUTO"
                    }},
                    {"match_phrase": {
                        "author_name": {
                            "query": author_query,
                            "boost": 100
                        }}}
                ]

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

            res = es.search(index=INDEX_NAME, body={"query": final_query, "size": 50})

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
                prompt = f"""
                أجب عن السؤال التالي: '{query}' بناءً على النصوص:
                ---
                {context}
                ---
                إذا لم تجد الإجابة، فاذكر ذلك.
                """
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "text/plain"}
                }
                url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
                res = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=30)
                data = res.json()
                gemini_answer = data.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '') or "لا توجد إجابة."
            except Exception as e:
                gemini_answer = f"خطأ من Gemini: {e}"

        claude_answer = ""
        if claude_client:
            try:
                claude_prompt = f"""
                أجب عن السؤال التالي: '{query}' بناءً على النصوص:
                ---
                {context}
                ---
                إذا لم تجد الإجابة، فاذكر ذلك.
                """
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
