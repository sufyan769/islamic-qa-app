from flask import Flask, request, jsonify
from elasticsearch import Elasticsearch
import os

app = Flask(__name__)
# الاتصال بـ Elasticsearch عبر Render (إن وُجد) أو محليًا
ELASTIC_URL = os.environ.get("ELASTIC_URL", "http://localhost:9200")
es = Elasticsearch(ELASTIC_URL)

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

    # تقسيم الجملة إلى كلمات للبحث عنها كلها
    words = query_text.split()

    # ترتيب النتائج بناءً على عدد الكلمات المتطابقة يدويًا
    should_clauses = [
        {"match_phrase": {"text": query_text}},  # الجملة كاملة
        *[{"match": {"text": word}} for word in words]
    ]

    query = {
        "bool": {
            "should": should_clauses,
            "minimum_should_match": 1
        }
    }

    if author:
        query["bool"]["filter"] = {"match": {"author_name": author}}

    try:
        res = es.search(
            index=INDEX_NAME,
            body={
                "query": query,
                "from": from_index,
                "size": size,
                "sort": ["_score"]
            }
        )
    except Exception as e:
        return jsonify({"error": f"فشل الاتصال بـ Elasticsearch: {str(e)}"}), 500

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
