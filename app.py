from flask import Flask, request, jsonify
from flask_cors import CORS
from elasticsearch import Elasticsearch
import os
import re

app = Flask(__name__)
CORS(app)

es = Elasticsearch("http://localhost:9200")
INDEX_NAME = "books_index"

# دالة لحساب عدد الكلمات المطابقة
def count_matches(text, query_words):
    text_words = set(re.findall(r'\w+', text))
    return sum(1 for word in query_words if word in text_words)

@app.route("/ask")
def ask():
    query_text = request.args.get("q", "").strip()
    author = request.args.get("author", "").strip()
    mode = request.args.get("mode", "default")
    from_index = int(request.args.get("from", 0))
    size = int(request.args.get("size", 20))

    if not query_text:
        return jsonify({"error": "يرجى إدخال سؤال للبحث."}), 400

    query_words = list(set(re.findall(r'\w+', query_text)))

    must_clauses = [
        {"match": {"text": word}} for word in query_words
    ]

    should_clauses = [
        {"match_phrase": {"text": {"query": query_text, "boost": 5}}}
    ]

    if author:
        must_clauses.append({"match": {"author_name": author}})

    es_query = {
        "bool": {
            "must": must_clauses,
            "should": should_clauses
        }
    }

    res = es.search(
        index=INDEX_NAME,
        body={
            "query": es_query,
            "from": 0,
            "size": 100
        }
    )

    hits = res.get("hits", {}).get("hits", [])
    results = []

    for hit in hits:
        source = hit["_source"]
        text = source.get("text", "")
        score = count_matches(text, query_words)
        results.append((score, source))

    results.sort(key=lambda x: x[0], reverse=True)
    selected = results[from_index:from_index + size]

    sources = [
        {
            "text_content": r[1].get("text", ""),
            "book_title": r[1].get("book_title", ""),
            "author_name": r[1].get("author_name", ""),
            "part_number": r[1].get("part_number", ""),
            "page_number": r[1].get("page_number", "")
        }
        for r in selected
    ]

    response_data = {
        "total_results": len(results),
        "sources_retrieved": sources
    }

    if mode in ["default", "ai-only"]:
        response_data["claude_answer"] = f"إجابة Claude للسؤال: {query_text}"
        if mode == "default":
            response_data["gemini_answer"] = f"إجابة Gemini للسؤال: {query_text}"
        else:
            response_data["sources_retrieved"] = []
    elif mode == "full":
        response_data.pop("claude_answer", None)
        response_data.pop("gemini_answer", None)

    return jsonify(response_data)

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
