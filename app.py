from flask import Flask, request, jsonify
from elasticsearch import Elasticsearch
import os

app = Flask(__name__)
es = Elasticsearch("http://localhost:9200")
INDEX_NAME = "books_index"

@app.route("/ask")
def ask():
    query_text = request.args.get("q", "").strip()
    author = request.args.get("author", "").strip()
    mode = request.args.get("mode", "default")
    from_index = int(request.args.get("from", 0))
    size = int(request.args.get("size", 20))

    if not query_text:
        return jsonify({"error": "يرجى إدخال سؤال للبحث."}), 400

    should_clauses = [{"match_phrase": {"text": word}} for word in query_text.split() if word]

    query = {
        "bool": {
            "should": should_clauses,
            "minimum_should_match": 1
        }
    }

    if author:
        query["bool"]["filter"] = {"match": {"author_name": author}}

    res = es.search(
        index=INDEX_NAME,
        body={
            "query": query,
            "from": from_index,
            "size": size,
            "sort": [{"_score": {"order": "desc"}}]
        }
    )

    hits = res.get("hits", {}).get("hits", [])
    sources = [
        {
            "text_content": hit["_source"].get("text", ""),
            "book_title": hit["_source"].get("book_title", ""),
            "author_name": hit["_source"].get("author_name", ""),
            "part_number": hit["_source"].get("part_number", ""),
            "page_number": hit["_source"].get("page_number", "")
        }
        for hit in hits
    ]

    response_data = {
        "total_results": res["hits"]["total"]["value"],
        "sources_retrieved": sources
    }

    if mode in ["default", "ai-only"]:
        # افتراضياً، استخدم نص وهمي للذكاء الاصطناعي
        response_data["claude_answer"] = f"إجابة Claude للسؤال: {query_text}"
        if mode == "default":
            response_data["gemini_answer"] = f"إجابة Gemini للسؤال: {query_text}"
        else:
            response_data["sources_retrieved"] = []  # لا مصادر في هذا الوضع
    elif mode == "full":
        response_data.pop("claude_answer", None)
        response_data.pop("gemini_answer", None)

    return jsonify(response_data)

if __name__ == "__main__":
    app.run(debug=True)
