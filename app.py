# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import sys
import logging
import json # تم الاحتفاظ به لـ json.dumps في التسجيل

# تهيئة التسجيل (Logging) لتحسين تتبع الأخطاء
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app) # تمكين CORS لجميع المسارات

# متغيرات البيئة لـ Elasticsearch
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")

# تهيئة عميل Elasticsearch
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
        # التحقق من الاتصال
        if not es.ping():
            raise ValueError("Connection to Elasticsearch failed!")
        logging.info("Successfully connected to Elasticsearch!")
    else:
        # إذا لم يتم تعيين متغيرات البيئة، يجب أن نخرج أو نعالج الخطأ
        logging.critical("Elasticsearch environment variables not fully set. Exiting.")
        sys.exit(1)
except (ConnectionError, AuthenticationException) as e:
    logging.critical(f"Elasticsearch connection or authentication error: {e}")
    sys.exit(1)
except Exception as e:
    logging.critical(f"Unexpected error during Elasticsearch initialization: {e}")
    sys.exit(1)

# اسم الفهرس الذي قمنا بإنشائه
INDEX_NAME = "islamic_texts"

# قوائم الكتب المحددة لوضع "بحث عن حديث"
# هذه القوائم يجب أن تحتوي على أسماء الكتب الدقيقة كما هي في حقل book_title.keyword في Elasticsearch
HADITH_BOOKS = [
    "صحيح البخاري", "صحيح مسلم", "سنن أبي داود", "سنن الترمذي", "سنن النسائي",
    "سنن ابن ماجه", "مسند أحمد", "موطأ الإمام مالك", "سنن الدارمي",
    # أضف أي كتب حديث أخرى مهمة هنا
]

EXPLANATION_BOOKS = [
    "فتح الباري شرح صحيح البخاري", "المنهاج شرح صحيح مسلم بن الحجاج",
    "عون المعبود شرح سنن أبي داود", "تحفة الأحوذي بشرح جامع الترمذي",
    "شرح سنن النسائي", "حاشية السندي على سنن ابن ماجه",
    # أضف أي كتب شرح أخرى مهمة هنا
]

@app.route('/ask', methods=['GET'])
def ask(): # تم تغيير اسم الدالة من ask_ai إلى ask ليعكس إزالة AI
    query = request.args.get('q', '').strip()
    author_query = request.args.get('author', '').strip()
    hadith_mode = request.args.get('hadith_mode', 'false').lower() == 'true'

    if not query and not author_query:
        return jsonify({"error": "يرجى تقديم سؤال أو اسم مؤلف."}), 400

    # قوائم لتخزين المصادر المسترجعة
    hadith_sources_retrieved = []
    explanation_sources_retrieved = []
    general_sources_retrieved = [] # للمصادر في الوضع العادي

    try:
        # بناء استعلام Elasticsearch
        query_clauses = []
        author_clauses = []

        if query:
            query_clauses.append({
                "multi_match": {
                    "query": query,
                    "fields": ["text_content^3", "text_content.ngram^2", "book_title^1.5", "author_name^1.2"],
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                    "operator": "or"
                }
            })
            query_clauses.append({"match_phrase": {"text_content": {"query": query, "boost": 100, "slop": 2}}})
            query_clauses.append({"match": {"text_content": {"query": query, "operator": "and", "boost": 20}}})

        if author_query:
            author_clauses.append({
                "multi_match": {
                    "query": author_query,
                    "fields": ["author_name^5", "author_name.ngram^3"],
                    "type": "best_fields",
                    "fuzziness": "AUTO"
                }
            })
            author_clauses.append({"match_phrase": {"author_name": {"query": author_query, "boost": 150}}})

        # دالة مساعدة لتنفيذ البحث في Elasticsearch
        def execute_search(query_body):
            res = es.search(index=INDEX_NAME, body=query_body)
            retrieved_docs = []
            for hit in res['hits']['hits']:
                source = hit['_source']
                retrieved_docs.append({
                    "book_title": source.get('book_title', 'غير معروف'),
                    "author_name": source.get('author_name', 'غير معروف'),
                    "part_number": source.get('part_number', 'غير معروف'),
                    "page_number": source.get('page_number', 'غير معروف'),
                    "text_content": source.get('text_content', 'لا يوجد نص.')
                })
            return retrieved_docs

        if hadith_mode:
            # وضع البحث عن حديث: البحث في كتب الحديث والشروح بشكل منفصل
            
            # 1. البحث في كتب الحديث
            hadith_search_query = {
                "bool": {
                    "must": query_clauses,
                    "filter": [
                        {"terms": {"book_title.keyword": HADITH_BOOKS}}
                    ]
                }
            }
            hadith_search_body = {
                "query": hadith_search_query,
                "size": 50 # عدد النتائج للأحاديث
            }
            logging.info(f"وضع الحديث مفعل. البحث في كتب الحديث: {HADITH_BOOKS}")
            hadith_sources_retrieved = execute_search(hadith_search_body)
            logging.info(f"تم استرجاع {len(hadith_sources_retrieved)} مصدر حديث.")

            # 2. البحث في كتب الشرح (إذا كان هناك استعلام)
            if query:
                explanation_search_query = {
                    "bool": {
                        "must": query_clauses,
                        "filter": [
                            {"terms": {"book_title.keyword": EXPLANATION_BOOKS}}
                        ]
                    }
                }
                explanation_search_body = {
                    "query": explanation_search_query,
                    "size": 20 # عدد النتائج للشروح
                }
                logging.info(f"وضع الحديث مفعل. البحث في كتب الشرح: {EXPLANATION_BOOKS}")
                explanation_sources_retrieved = execute_search(explanation_search_body)
                logging.info(f"تم استرجاع {len(explanation_sources_retrieved)} مصدر شرح.")
            
            return jsonify({
                "question": query,
                "hadith_mode": True,
                "hadith_sources": hadith_sources_retrieved,
                "explanation_sources": explanation_sources_retrieved
            })

        else:
            # الوضع العادي: البحث في جميع الكتب
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
                final_query = {
                    "bool": {
                        "should": query_clauses,
                        "minimum_should_match": 1
                    }
                }
            elif author_query:
                final_query = {
                    "bool": {
                        "should": author_clauses,
                        "minimum_should_match": 1
                    }
                }
            else:
                final_query = {"match_all": {}}

            search_body = {
                "query": final_query,
                "size": 50 # عدد النتائج للوضع العادي
            }
            general_sources_retrieved = execute_search(search_body)
            logging.info(f"تم استرجاع {len(general_sources_retrieved)} مصدر عام.")

            return jsonify({
                "question": query,
                "hadith_mode": False,
                "sources_retrieved": general_sources_retrieved
            })

    except Exception as e:
        logging.error(f"خطأ عام أثناء معالجة الطلب: {e}")
        import traceback
        traceback.print_exc() # طباعة تتبع الخطأ لمزيد من التفاصيل
        return jsonify({"error": f"حدث خطأ أثناء معالجة طلبك: {e}"}), 500

@app.route("/get_contextual_text")
def get_contextual_text():
    """
    تجلب النص السابق أو التالي بناءً على معلومات الكتاب والجزء والصفحة.
    """
    book_title = request.args.get("book_title")
    author_name = request.args.get("author_name")
    current_part_number_str = request.args.get("current_part_number", "1") # افتراضي 1
    current_page_number_str = request.args.get("current_page_number", "1") # افتراضي 1
    direction = request.args.get("direction") # 'next' أو 'prev'

    logging.info(f"طلب نص سياقي: الكتاب='{book_title}', المؤلف='{author_name}', الجزء='{current_part_number_str}', الصفحة='{current_page_number_str}', الاتجاه='{direction}'")

    # التحقق من المعلمات الأساسية فقط، والسماح لـ part/page بأن تكون قيمًا افتراضية
    if not all([book_title, author_name, direction]):
        logging.warning("معلمات أساسية مفقودة لطلب النص السياقي (الكتاب، المؤلف، الاتجاه).")
        return jsonify({"error": "يرجى توفير عنوان الكتاب، اسم المؤلف، والاتجاه."}), 400

    try:
        # تحويل أرقام الجزء والصفحة إلى أعداد صحيحة هنا
        # يجب أن تكون القيم الافتراضية قابلة للتحويل إلى أعداد صحيحة
        current_part_number = int(current_part_number_str)
        current_page_number = int(current_page_number_str)
    except ValueError:
        logging.error(f"أرقام الجزء أو الصفحة غير صالحة: part='{current_part_number_str}', page='{current_page_number_str}'")
        return jsonify({"error": "أرقام الجزء أو الصفحة يجب أن تكون أعدادًا صحيحة."}), 400

    try:
        # بناء استعلام المطابقة للكتاب والمؤلف
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

        # بناء استعلام الفلترة للصفحة والجزء
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
            "_source": ["book_title", "author_name", "part_number", "page_number", "text_content"], # لضمان استرجاع هذه الحقول
            "sort": [
                {"part_number": {"order": sort_order}},
                {"page_number": {"order": sort_order}}
            ],
            "size": 1,
            "min_score": 0.1 # لضمان استرجاع النتائج ذات الصلة فقط
        }
        
        logging.info(f"استعلام Elasticsearch للنص السياقي: {json.dumps(query_body, indent=2, ensure_ascii=False)}")

        # تنفيذ البحث في Elasticsearch
        res = es.search(index=INDEX_NAME, body=query_body)
        
        # تحويل استجابة Elasticsearch إلى قاموس Python قابل للتحويل إلى JSON
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
        traceback.print_exc() # طباعة تتبع الخطأ لمزيد من التفاصيل
        return jsonify({"error": f"فشل جلب النص السياقي: {e}"}), 500


if __name__ == "__main__":
    # تشغيل التطبيق في وضع التصحيح (debug mode) على المنفذ 5000
    # يجب تعيين PORT كمتغير بيئة عند النشر
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
