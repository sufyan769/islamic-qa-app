from flask import Flask, request, jsonify
from flask_cors import CORS
from elasticsearch import Elasticsearch
from elasticsearch.exceptions import ConnectionError, AuthenticationException
from anthropic import Anthropic
import os, sys, re, requests
import logging

# تهيئة التسجيل (Logging) لتحسين تتبع الأخطاء
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)
CORS(app) # تمكين CORS للسماح بالطلبات من الواجهة الأمامية

# متغيرات البيئة لـ Elasticsearch
CLOUD_ID = os.environ.get("CLOUD_ID")
ELASTIC_USERNAME = os.environ.get("ELASTIC_USERNAME")
ELASTIC_PASSWORD = os.environ.get("ELASTIC_PASSWORD")
INDEX_NAME = "islamic_texts" # اسم الفهرس في Elasticsearch

# متغيرات البيئة لنماذج الذكاء الاصطناعي
CLAUDE_KEY = os.environ.get("ANTHROPIC_API_KEY")
claude_client = None
if CLAUDE_KEY:
    try:
        claude_client = Anthropic(api_key=CLAUDE_KEY)
        logging.info("تم تهيئة عميل Claude بنجاح.")
    except Exception as e:
        logging.error(f"خطأ في تهيئة عميل Claude: {e}")
else:
    logging.warning("لم يتم العثور على مفتاح API لـ Anthropic (CLAUDE_KEY). لن يتم استخدام Claude.")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    logging.warning("لم يتم العثور على مفتاح API لـ Gemini (GEMINI_API_KEY). لن يتم استخدام Gemini.")

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
    mode = request.args.get("mode", "default") # الوضع: default, ai_only, full
    frm = int(request.args.get("from", 0)) # لتقسيم النتائج (pagination)
    size = int(request.args.get("size", 20)) # عدد النتائج المطلوبة

    if not query:
        logging.warning("تم استلام طلب بحث فارغ.")
        return jsonify({"error": "يرجى إدخال استعلام."}), 400

    # تنظيف الاستعلام: استخراج الكلمات العربية وتصفية كلمات التوقف
    words = [w for w in re.findall(r"[\u0600-\u06FF]+", query) if len(w) > 2 and w not in AR_STOPWORDS]
    sources = [] # قائمة لتخزين المصادر المسترجعة من Elasticsearch

    def build_query(precise=True):
        """
        تبني استعلام Elasticsearch.
        @param precise: إذا كانت True، تعطي الأولوية للمطابقة الدقيقة للعبارة.
        """
        should_clauses = []
        if precise:
            # مطابقة العبارة الدقيقة مع تعزيز (boost) عالٍ
            should_clauses.append({"match_phrase": {"text_content": {"query": query, "boost": 100}}})
        # مطابقة الكلمات الفردية مع تعزيز أقل
        should_clauses.extend([{"match": {"text_content": {"query": w, "operator": "and", "boost": 10}}} for w in words])

        # إذا لم يكن هناك كلمات بعد التصفية، نضمن عدم وجود خطأ في الاستعلام
        if not should_clauses:
            return {"match_all": {}} # استعلام يطابق كل شيء إذا كان الاستعلام فارغًا بعد التصفية
        
        return {"bool": {"should": should_clauses, "minimum_should_match": 1}}

    try:
        if mode != "ai_only": # إذا لم يكن الوضع "ai_only"، نبحث في Elasticsearch
            # محاولة البحث أولاً بمطابقة دقيقة، ثم بحث أوسع إذا لم يتم العثور على نتائج
            for stage in [True, False]:
                search_body = {
                    "query": build_query(stage),
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

    claude_answer = ""
    if mode in ("default", "ai_only") and claude_client:
        if not sources and mode == "default":
            # إذا لم يتم العثور على مصادر، اطلب من Claude الإجابة مباشرة
            prompt = f"أجب باختصار عن السؤال التالي: {query}"
        else:
            # بناء السياق من المصادر المسترجعة لـ Claude
            context = "\n\n".join([
                f"الكتاب: {s['book_title']}\nالمؤلف: {s['author_name']}\nالجزء: {s['part_number']}\nالصفحة: {s['page_number']}\nالنص: {s['text_content']}"
                for s in sources
            ])
            # تم تعديل هذا الجزء لتوجيه Claude ليكون محايدًا قدر الإمكان
            prompt = f"أجب عن السؤال التالي بناءً على الفقرات المقدمة فقط. إذا لم يكن الجواب موجودًا في الفقرات، اذكر ذلك بوضوح. تجنب إضافة معلومات من معرفتك العامة أو التحيزات المذهبية. اذكر الجواب مع الإشارة إلى الفقرة والكتاب والصفحة.\n\nالسؤال: {query}\n\nالفقرات:\n{context}"

        try:
            msg = claude_client.messages.create(model="claude-3-5-sonnet-20241022", max_tokens=800, messages=[{"role": "user", "content": prompt}])
            claude_answer = msg.content[0].text.strip()
            logging.info("تم الحصول على إجابة من Claude.")
        except Exception as e:
            claude_answer = f"خطأ في Claude: {e}"
            logging.error(f"خطأ في استدعاء Claude API: {e}")

    gemini_answer = ""
    if mode in ("default", "ai_only") and GEMINI_API_KEY:
        try:
            # استخدام نموذج Gemini Flash بدلاً من Gemini Pro لزيادة التوافر وقد يحل مشكلة 404
            gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
            
            gemini_prompt = f"أجب باختصار عن السؤال التالي: {query}"
            if sources and mode == "default":
                context_for_gemini = "\n\n".join([s['text_content'] for s in sources])
                # توجيه Gemini ليكون محايدًا قدر الإمكان والاعتماد على السياق
                gemini_prompt = f"بناءً على النصوص التالية فقط، أجب باختصار عن السؤال التالي. إذا لم يكن الجواب موجودًا في النصوص، اذكر ذلك بوضوح. تجنب إضافة معلومات من معرفتك العامة أو التحيزات المذهبية:\n{context_for_gemini}\n\nالسؤال: {query}"

            gemini_payload = {
                "contents": [{"parts": [{"text": gemini_prompt}]}]
            }
            g_res = requests.post(gemini_url, json=gemini_payload)
            g_res.raise_for_status() # رفع استثناء لأخطاء HTTP (4xx أو 5xx)
            gemini_data = g_res.json()
            gemini_answer = gemini_data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            logging.info("تم الحصول على إجابة من Gemini.")
        except requests.exceptions.RequestException as e:
            gemini_answer = f"خطأ في الاتصال بـ Gemini: {e}"
            logging.error(f"خطأ في طلب Gemini API: {e}")
        except Exception as e:
            gemini_answer = f"خطأ غير متوقع في Gemini: {e}"
            logging.error(f"خطأ غير متوقع في Gemini: {e}")

    return jsonify({
        "claude_answer": claude_answer if mode != "full" else "",
        "gemini_answer": gemini_answer if mode != "full" else "",
        "sources_retrieved": [] if mode == "ai_only" else sources
    })

@app.route("/get_contextual_text")
def get_contextual_text():
    """
    تجلب النص السابق أو التالي بناءً على معلومات الكتاب والجزء والصفحة.
    """
    book_title = request.args.get("book_title")
    author_name = request.args.get("author_name")
    current_part_number = int(request.args.get("current_part_number", 1))
    current_page_number = int(request.args.get("current_page_number", 1))
    direction = request.args.get("direction") # 'next' أو 'prev'

    if not all([book_title, author_name, direction]):
        logging.warning("معلمات مفقودة لطلب النص السياقي.")
        return jsonify({"error": "يرجى توفير عنوان الكتاب، اسم المؤلف، والاتجاه."}), 400

    try:
        if direction == 'next':
            # البحث عن النص التالي: جزء أكبر، أو نفس الجزء وصفحة أكبر
            query_body = {
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"book_title.keyword": book_title}},
                            {"term": {"author_name.keyword": author_name}}
                        ],
                        "filter": {
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
                        }
                    }
                },
                "sort": [
                    {"part_number": {"order": "asc"}},
                    {"page_number": {"order": "asc"}}
                ],
                "size": 1
            }
        elif direction == 'prev':
            # البحث عن النص السابق: جزء أصغر، أو نفس الجزء وصفحة أصغر
            query_body = {
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"book_title.keyword": book_title}},
                            {"term": {"author_name.keyword": author_name}}
                        ],
                        "filter": {
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
                        }
                    }
                },
                "sort": [
                    {"part_number": {"order": "desc"}},
                    {"page_number": {"order": "desc"}}
                ],
                "size": 1
            }
        else:
            return jsonify({"error": "الاتجاه غير صالح. يجب أن يكون 'next' أو 'prev'."}), 400

        res = es.search(index=INDEX_NAME, body=query_body)

        if res["hits"]["hits"]:
            hit = res["hits"]["hits"][0]["_source"]
            return jsonify({
                "book_title": hit.get("book_title", "غير معروف"),
                "author_name": hit.get("author_name", "غير معروف"),
                "part_number": hit.get("part_number", "غير متوفر"),
                "page_number": hit.get("page_number", "غير متوفر"),
                "text_content": hit.get("text_content", "")
            })
        else:
            return jsonify({"message": "لم يتم العثور على نص في هذا الاتجاه."}), 404

    except Exception as e:
        logging.error(f"خطأ في جلب النص السياقي من Elasticsearch: {e}")
        return jsonify({"error": f"فشل جلب النص السياقي: {e}"}), 500


if __name__ == "__main__":
    # تشغيل التطبيق في وضع التصحيح (debug mode) على المنفذ 5000
    # يجب تعيين PORT كمتغير بيئة عند النشر
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=True, host="0.0.0.0", port=port)
