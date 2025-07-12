# -*- coding: utf-8 -*-
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
import sys

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
        print("Successfully connected to Elasticsearch!")
    else:
        print("Elasticsearch environment variables not fully set. Skipping connection.")
except ConnectionError as ce:
    print(f"خطأ في الاتصال بـ Elasticsearch (ConnectionError): {ce}")
    print("يرجى التحقق من اتصالك بالإنترنت، إعدادات جدار الحماية، وفلاتر IP في Elastic Cloud.")
    sys.exit(1)
except AuthenticationException as ae:
    print(f"خطأ في المصادقة مع Elasticsearch (AuthenticationException): {ae}")
    print("يرجى التحقق من اسم المستخدم وكلمة المرور الخاصة بـ Elastic Cloud. تأكد من أنها مطابقة تمامًا.")
    sys.exit(1)
except Exception as e:
    print(f"حدث خطأ غير متوقع أثناء الاتصال بـ Elasticsearch: {e}")
    sys.exit(1)

# اسم الفهرس الذي قمنا بإنشائه
INDEX_NAME = "islamic_texts"

# نقطة نهاية (Endpoint) للبحث في Elasticsearch فقط
@app.route('/ask', methods=['GET'])
def ask_ai():
    query = request.args.get('q', '')
    author_query = request.args.get('author', '')

    if not query and not author_query:
        return jsonify({"error": "يرجى تقديم سؤال أو اسم مؤلف."}), 400

    sources_retrieved = []
    try:
        if es:
            # بناء استعلام Elasticsearch لتحسين دقة البحث
            query_clauses = [] # شروط البحث الخاصة بالسؤال
            author_clauses = [] # شروط البحث الخاصة بالمؤلف

            if query:
                # البحث عن الكلمات الرئيسية في حقول متعددة مع تعزيزات مختلفة
                query_clauses.append({
                    "multi_match": {
                        "query": query,
                        "fields": [
                            "text_content^3",         # تعزيز عالي لمحتوى النص
                            "text_content.ngram^2",   # تعزيز للبحث الجزئي (N-gram)
                            "book_title^1.5",         # تعزيز لعنوان الكتاب
                            "author_name^1.2"         # تعزيز لاسم المؤلف
                        ],
                        "type": "best_fields",  # أخذ النقاط من أفضل حقل مطابق
                        "fuzziness": "AUTO",    # السماح ببعض الأخطاء الإملائية
                        "operator": "or"        # مطابقة أي من المصطلحات
                    }
                })
                # مطابقة العبارة الدقيقة مع تعزيز عالي جداً
                query_clauses.append({
                    "match_phrase": {
                        "text_content": {
                            "query": query,
                            "boost": 100,           # تعزيز عالي جداً للمطابقة الدقيقة للعبارة
                            "slop": 2               # السماح بوجود الكلمات متباعدة بمقدار 2 موضع
                        }
                    }
                })
                # مطابقة جميع الكلمات الرئيسية كشروط إلزامية (AND)
                query_clauses.append({
                    "match": {
                        "text_content": {
                            "query": query,
                            "operator": "and",      # يجب أن تكون جميع المصطلحات موجودة
                            "boost": 20             # تعزيز عالي لوجود جميع المصطلحات
                        }
                    }
                })

            if author_query:
                # البحث عن اسم المؤلف في حقول متعددة مع تعزيزات
                author_clauses.append({
                    "multi_match": {
                        "query": author_query,
                        "fields": [
                            "author_name^5",          # تعزيز عالي جداً لاسم المؤلف
                            "author_name.ngram^3"     # تعزيز للبحث الجزئي عن اسم المؤلف
                        ],
                        "type": "best_fields",
                        "fuzziness": "AUTO"
                    }
                })
                # مطابقة العبارة الدقيقة لاسم المؤلف مع تعزيز أعلى
                author_clauses.append({
                    "match_phrase": {
                        "author_name": {
                            "query": author_query,
                            "boost": 150 # تعزيز أعلى لمطابقة عبارة المؤلف الدقيقة
                        }
                    }
                })

            final_query = {}

            if query and author_query:
                # إذا كان هناك سؤال ومؤلف، يجب أن تتطابق كليهما
                final_query = {
                    "bool": {
                        "must": [
                            {"bool": {"should": query_clauses, "minimum_should_match": 1}},
                            {"bool": {"should": author_clauses, "minimum_should_match": 1}}
                        ]
                    }
                }
            elif query:
                # إذا كان البحث بالسؤال فقط
                final_query = {
                    "bool": {
                        "should": query_clauses,
                        "minimum_should_match": 1
                    }
                }
            elif author_query:
                # إذا كان البحث بالمؤلف فقط
                final_query = {
                    "bool": {
                        "should": author_clauses,
                        "minimum_should_match": 1
                    }
                }
            else:
                # هذا الشرط لا ينبغي أن يحدث بسبب التحقق الأولي، ولكن كاحتياطي
                final_query = {"match_all": {}}

            search_body = {
                "query": final_query,
                "size": 50 # تم زيادة عدد النتائج المسترجعة إلى 50
            }
            
            res = es.search(index=INDEX_NAME, body=search_body)

            for hit in res['hits']['hits']:
                source = hit['_source']
                sources_retrieved.append({
                    "book_title": source.get('book_title', 'غير معروف'),
                    "author_name": source.get('author_name', 'غير معروف'),
                    "part_number": source.get('part_number', 'غير معروف'),
                    "page_number": source.get('page_number', 'غير معروف'),
                    "text_content": source.get('text_content', 'لا يوجد نص.')
                })
            print(f"Retrieved {len(sources_retrieved)} sources from Elasticsearch.")
        else:
            print("Elasticsearch client not initialized. Skipping source retrieval.")

        return jsonify({
            "question": query,
            "sources_retrieved": sources_retrieved
        })

    except Exception as e:
        print(f"خطأ عام أثناء معالجة السؤال أو البحث في Elasticsearch: {e}")
        return jsonify({"error": f"حدث خطأ أثناء معالجة طلبك: {e}"}), 500


# نقطة نهاية جديدة لجلب النص السياقي (التالي/السابق)
@app.route('/get_contextual_text', methods=['GET'])
def get_contextual_text():
    book_title = request.args.get('book_title')
    author_name = request.args.get('author_name')
    current_part_str = request.args.get('current_part_number')
    current_page_str = request.args.get('current_page_number')
    direction = request.args.get('direction') # 'next' أو 'prev'

    if not all([book_title, author_name, current_part_str, current_page_str, direction]):
        return jsonify({"error": "يرجى تقديم جميع المعلمات المطلوبة (book_title, author_name, current_part_number, current_page_number, direction)."}), 400

    try:
        current_part_number = int(current_part_str)
        current_page_number = int(current_page_str)
    except ValueError:
        return jsonify({"error": "أرقام الجزء والصفحة يجب أن تكون أعداد صحيحة."}), 400

    if not es:
        return jsonify({"error": "Elasticsearch غير مهيأ."}), 500

    try:
        search_body = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"book_title.keyword": book_title}},
                        {"match": {"author_name.keyword": author_name}}
                    ],
                    "filter": [] # شروط التصفية للجزء والصفحة
                }
            },
            "sort": [
                {"part_number": {"order": "asc"}},
                {"page_number": {"order": "asc"}}
            ],
            "size": 1 # نريد نتيجة واحدة فقط (النص التالي أو السابق)
        }

        if direction == 'next':
            search_body["query"]["bool"]["filter"].append({
                "range": {
                    "part_number": {
                        "gte": current_part_number
                    }
                }
            })
            search_body["query"]["bool"]["filter"].append({
                "range": {
                    "page_number": {
                        "gt": current_page_number if current_part_number == current_part_number else current_page_number - 1 # إذا كان نفس الجزء، ابحث عن صفحة أكبر، وإلا ابحث عن أي صفحة في الجزء التالي
                    }
                }
            })
            # لضمان الترتيب الصحيح عند الانتقال بين الأجزاء
            search_body["sort"] = [
                {"part_number": {"order": "asc"}},
                {"page_number": {"order": "asc"}}
            ]
            
        elif direction == 'prev':
            search_body["query"]["bool"]["filter"].append({
                "range": {
                    "part_number": {
                        "lte": current_part_number
                    }
                }
            })
            search_body["query"]["bool"]["filter"].append({
                "range": {
                    "page_number": {
                        "lt": current_page_number if current_part_number == current_part_number else current_page_number + 1 # إذا كان نفس الجزء، ابحث عن صفحة أصغر، وإلا ابحث عن أي صفحة في الجزء السابق
                    }
                }
            })
            # لضمان الترتيب الصحيح عند الانتقال بين الأجزاء
            search_body["sort"] = [
                {"part_number": {"order": "desc"}},
                {"page_number": {"order": "desc"}}
            ]
        else:
            return jsonify({"error": "اتجاه غير صالح. يجب أن يكون 'next' أو 'prev'."}), 400

        res = es.search(index=INDEX_NAME, body=search_body)

        if res['hits']['hits']:
            source = res['hits']['hits'][0]['_source']
            return jsonify({
                "book_title": source.get('book_title', 'غير معروف'),
                "author_name": source.get('author_name', 'غير معروف'),
                "part_number": source.get('part_number', 'غير معروف'),
                "page_number": source.get('page_number', 'غير معروف'),
                "text_content": source.get('text_content', 'لا يوجد نص.')
            })
        else:
            return jsonify({"message": "لم يتم العثور على نص في هذا الاتجاه."}), 404

    except Exception as e:
        print(f"خطأ في جلب النص السياقي: {e}")
        return jsonify({"error": f"حدث خطأ أثناء جلب النص السياقي: {e}"}), 500

# 4. تشغيل تطبيق Flask
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=os.environ.get("PORT", 5000)) # استخدام متغير البيئة PORT الذي يوفره Render
