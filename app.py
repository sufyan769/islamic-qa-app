# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import os, sys, re
import logging
import json # تم إضافة هذا الاستيراد لـ json.dumps

# تهيئة التسجيل (Logging) لتحسين تتبع الأخطاء
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app) # تمكين CORS للسماح بالطلبات من الواجهة الأمامية

# متغيرات البيئة لـ Elasticsearch
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
INDEX_NAME = "islamic_texts" # اسم الفهرس في Elasticsearch

# كلمات التوقف العربية لتحسين البحث
AR_STOPWORDS = {"من", "في", "على", "إلى", "عن", "ما", "إذ", "أو", "و", "ثم", "أن", "إن", "كان", "قد", "لم", "لن", "لا", "هذه", "هذا", "ذلك", "الذي", "التي", "ال"}

# تهيئة عميل Elasticsearch
es = None
try:
    if CLOUD_ID and ELASTIC_USERNAME and ELASTIC_PASSWORD:
        es = Elasticsearch(cloud_id=CLOUD_ID, basic_auth=(ELASTIC_USERNAME, ELASTIC_PASSWORD))
        if not es.ping():
            raise ValueError("Elasticsearch غير قابل للوصول. تحقق من بيانات الاعتماد والاتصال.")
        logging.info("تم الاتصال بـ Elasticsearch بنجاح.")
    else:
        raise ValueError("متغيرات بيئة Elasticsearch مفقودة (CLOUD_ID, ELASTIC_USERNAME, ELASTIC_PASSWORD).")
except (ConnectionError, AuthenticationException) as e:
    logging.critical(f"خطأ في الاتصال أو المصادقة مع Elasticsearch: {e}")
    sys.exit(1) # الخروج إذا لم يتمكن من الاتصال بـ Elasticsearch
except Exception as e:
    logging.critical(f"خطأ غير متوقع في تهيئة Elasticsearch: {e}")
    sys.exit(1)

@app.route("/ask")
def ask():
    query = request.args.get("q", "").strip()
    frm = int(request.args.get("from", 0)) # لتقسيم النتائج (pagination)
    size = int(request.args.get("size", 20)) # عدد النتائج المطلوبة
    author_query = request.args.get("author", "").strip() # جلب اسم المؤلف من الطلب

    if not query and not author_query:
        logging.warning("تم استلام طلب بحث فارغ.")
        return jsonify({"error": "يرجى إدخال استعلام أو اسم مؤلف."}), 400

    sources = [] # قائمة لتخزين المصادر المسترجعة من Elasticsearch

    def build_query_body(query_text, author_text, precise=True):
        """
        تبني استعلام Elasticsearch.
        @param query_text: نص الاستعلام الرئيسي.
        @param author_text: اسم المؤلف للاستعلام.
        @param precise: إذا كانت True، تعطي الأولوية للمطابقة الدقيقة للعبارة.
        """
        must_clauses = []
        should_clauses_query = []
        should_clauses_author = []

        if query_text:
            words = [w for w in re.findall(r"[\u0600-\u06FF]+", query_text) if len(w) > 2 and w not in AR_STOPWORDS]
            
            if precise:
                should_clauses_query.append({"match_phrase": {"text_content": {"query": query_text, "boost": 100}}})
            
            should_clauses_query.extend([
                {"match": {"text_content": {"query": w, "operator": "and", "boost": 10}}} for w in words
            ])
            should_clauses_query.append({
                "multi_match": {
                    "query": query_text,
                    "fields": ["book_title^1.5", "author_name^1.2"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                    "operator": "or"
                }
            })
            
            if should_clauses_query:
                must_clauses.append({"bool": {"should": should_clauses_query, "minimum_should_match": 1}})

        if author_text:
            should_clauses_author.append({
                "multi_match": {
                    "query": author_text,
                    "fields": ["author_name^5", "author_name.ngram^3"],
                    "type": "best_fields",
                    "fuzziness": "AUTO"
                }
            })
            should_clauses_author.append({
                "match_phrase": {
                    "author_name": {
                        "query": author_text,
                        "boost": 150
                    }
                }
            })
            
            if should_clauses_author:
                must_clauses.append({"bool": {"should": should_clauses_author, "minimum_should_match": 1}})

        if not must_clauses:
            return {"match_all": {}} # استعلام يطابق كل شيء إذا كان الاستعلام فارغًا بعد التصفية
        
        return {"bool": {"must": must_clauses}}


    try:
        # محاولة البحث أولاً بمطابقة دقيقة، ثم بحث أوسع إذا لم يتم العثور على نتائج
        for stage in [True, False]:
            search_body = {
                "query": build_query_body(query, author_query, stage),
                "from": frm,
                "size": size,
                "sort": [{"_score": {"order": "desc"}}] # فرز حسب درجة الصلة
            }
            res = es.search(index=INDEX_NAME, body=search_body)
            if res["hits"]["hits"]:
                for hit in res["hits"]["hits"]:
                    doc = hit["_source"]
                    sources.append({
                        "book_title": doc.get("book_title", "غير معروف"),
                        "author_name": doc.get("author_name", "غير معروف"),
                        "part_number": doc.get("part_number", "غير متوفر"),
                        "page_number": doc.get("page_number", "غير متوفر"),
                        "text_content": doc.get("text_content", "")
                    })
                logging.info(f"تم العثور على {len(sources)} مصدر في Elasticsearch.")
                break # الخروج بمجرد العثور على نتائج
            else:
                logging.info(f"لم يتم العثور على نتائج في مرحلة البحث الدقيقة: {stage}")
    except Exception as e:
        logging.error(f"فشل البحث في Elasticsearch: {e}")
        return jsonify({"error": f"فشل البحث في المصادر: {e}"}), 500

    return jsonify({
        "sources_retrieved": sources
    })

@app.route("/get_contextual_text")
def get_contextual_text():
    """
    تجلب النص السابق أو التالي بناءً على معلومات الكتاب والجزء والصفحة.
    """
    book_title = request.args.get("book_title")
    author_name = request.args.get("author_name")
    current_part_number_str = request.args.get("current_part_number", "1")
    current_page_number_str = request.args.get("current_page_number", "1")
    direction = request.args.get("direction")

    logging.info(f"طلب نص سياقي: الكتاب='{book_title}', المؤلف='{author_name}', الجزء='{current_part_number_str}', الصفحة='{current_page_number_str}', الاتجاه='{direction}'")

    if not all([book_title, author_name, direction]):
        logging.warning("معلمات أساسية مفقودة لطلب النص السياقي (الكتاب، المؤلف، الاتجاه).")
        return jsonify({"error": "يرجى توفير عنوان الكتاب، اسم المؤلف، والاتجاه."}), 400

    try:
        current_part_number = int(current_part_number_str)
        current_page_number = int(current_page_number_str)
    except ValueError:
        logging.error(f"أرقام الجزء أو الصفحة غير صالحة: part='{current_part_number_str}', page='{current_page_number_str}'")
        return jsonify({"error": "أرقام الجزء أو الصفحة يجب أن تكون أعدادًا صحيحة."}), 400

    try:
        match_clauses = [
            {
                "multi_match": {
                    "query": book_title,
                    "fields": ["book_title", "book_title.keyword"],
                    "type": "best_fields",
                    "operator": "and",
                    "analyzer": "arabic"
                }
            },
            {
                "multi_match": {
                    "query": author_name,
                    "fields": ["author_name", "author_name.keyword"],
                    "type": "best_fields",
                    "operator": "and",
                    "analyzer": "arabic"
                }
            }
        ]

        filter_clauses = []
        sort_order = "asc"
        
        if direction == 'next':
            filter_clauses.append({
                "bool": {
                    "should": [
                        {"range": {"part_number": {"gt": current_part_number}}},
                        {"bool": {
                            "must": [
                                {"term": {"part_number": current_part_number}},
                                {"range": {"page_number": {"gt": current_page_number}}}
                            ]
                        }}
                    ]
                }
            })
            sort_order = "asc"
        elif direction == 'prev':
            filter_clauses.append({
                "bool": {
                    "should": [
                        {"range": {"part_number": {"lt": current_part_number}}},
                        {"bool": {
                            "must": [
                                {"term": {"part_number": current_part_number}},
                                {"range": {"page_number": {"lt": current_page_number}}}
                            ]
                        }}
                    ]
                }
            })
            sort_order = "desc"
        else:
            return jsonify({"error": "الاتجاه غير صالح. يجب أن يكون 'next' أو 'prev'."}), 400

        query_body = {
            "query": {
                "bool": {
                    "must": match_clauses,
                    "filter": filter_clauses
                }
            },
            "_source": ["book_title", "author_name", "part_number", "page_number", "text_content"],
            "sort": [
                {"part_number": {"order": sort_order}},
                {"page_number": {"order": sort_order}}
            ],
            "size": 1,
            "min_score": 0.1
        }
        
        logging.info(f"استعلام Elasticsearch للنص السياقي: {json.dumps(query_body, indent=2, ensure_ascii=False)}")

        res = es.search(index=INDEX_NAME, body=query_body)
        
        res_dict = res.body if hasattr(res, 'body') else res 
        logging.info(f"استجابة Elasticsearch للنص السياقي: {json.dumps(res_dict, indent=2, ensure_ascii=False)}")

        if res_dict["hits"]["hits"]:
            hit = res_dict["hits"]["hits"][0]["_source"]
            return jsonify({
                "book_title": hit.get("book_title", "غير معروف"),
                "author_name": hit.get("author_name", "غير معروف"),
                "part_number": hit.get("part_number", "غير متوفر"),
                "page_number": hit.get("page_number", "غير متوفر"),
                "text_content": hit.get("text_content", "")
            })
        else:
            logging.info("لم يتم العثور على نص في هذا الاتجاه.")
            return jsonify({"message": "لم يتم العثور على نص في هذا الاتجاه."}), 404

    except Exception as e:
        logging.error(f"خطأ في جلب النص السياقي من Elasticsearch: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"فشل جلب النص السياقي: {e}"}), 500

@app.route("/get_full_book")
def get_full_book():
    """
    تجلب النص الكامل لكتاب معين بناءً على عنوانه واسم مؤلفه.
    """
    book_title = request.args.get("book_title")
    author_name = request.args.get("author_name")

    logging.info(f"طلب جلب كتاب كامل: الكتاب='{book_title}', المؤلف='{author_name}'")

    if not all([book_title, author_name]):
        logging.warning("معلمات أساسية مفقودة لطلب جلب الكتاب كاملاً (عنوان الكتاب، اسم المؤلف).")
        return jsonify({"error": "يرجى توفير عنوان الكتاب واسم المؤلف."}), 400

    try:
        # بناء استعلام Elasticsearch لجلب جميع الفقرات للكتاب والمؤلف المحدد
        query_body = {
            "query": {
                "bool": {
                    "must": [
                        {
                            "multi_match": {
                                "query": book_title,
                                "fields": ["book_title", "book_title.keyword"],
                                "type": "best_fields",
                                "operator": "and",
                                "analyzer": "arabic"
                            }
                        },
                        {
                            "multi_match": {
                                "query": author_name,
                                "fields": ["author_name", "author_name.keyword"],
                                "type": "best_fields",
                                "operator": "and",
                                "analyzer": "arabic"
                            }
                        }
                    ]
                }
            },
            "_source": ["text_content", "part_number", "page_number"], # استرجاع النص والجزء والصفحة
            "sort": [ # ترتيب الفقرات حسب الجزء ثم الصفحة لضمان التسلسل الصحيح
                {"part_number": {"order": "asc"}},
                {"page_number": {"order": "asc"}}
            ],
            "size": 10000 # زيادة الحجم لجلب جميع الفقرات (يمكن تعديله إذا كان الكتاب ضخمًا جدًا)
        }

        res = es.search(index=INDEX_NAME, body=query_body)
        
        full_book_content = []
        for hit in res['hits']['hits']:
            source = hit['_source']
            # إضافة رقم الجزء والصفحة قبل كل فقرة
            full_book_content.append(f"الجزء: {source.get('part_number', 'غير متوفر')}، الصفحة: {source.get('page_number', 'غير متوفر')}\n{source.get('text_content', '')}\n")
        
        if full_book_content:
            logging.info(f"تم جلب {len(full_book_content)} فقرة للكتاب '{book_title}'.")
            return jsonify({
                "book_title": book_title,
                "author_name": author_name,
                "full_text": "\n\n".join(full_book_content) # دمج الفقرات مع فواصل أسطر
            })
        else:
            logging.info(f"لم يتم العثور على محتوى للكتاب '{book_title}' للمؤلف '{author_name}'.")
            return jsonify({"message": "لم يتم العثور على محتوى لهذا الكتاب."}), 404

    except Exception as e:
        logging.error(f"خطأ في جلب الكتاب كاملاً من Elasticsearch: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": f"فشل جلب الكتاب كاملاً: {e}"}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
