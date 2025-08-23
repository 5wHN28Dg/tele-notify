<div align="center">

[🇮🇶 العربية](README.ar.md) | [🇬🇧 English](README.md)

</div>

<div dir="rtl">

# tele-notify

برنامج مكتوب بلغة Python يقوم بمراقبة محادثات Telegram المحددة وتحويل الرسائل التي تطابق عوامل التصفية المخصصة الخاصة بك إلى محادثة أخرى.  
يدعم أيضًا خاصية OCR للتعرف على النصوص في الصور المرفقة ويتجنب إعادة إرسال الرسائل المكررة.

**⚠️ تنويه**: هذا المشروع هو حل مخصص لمشكلة محددة واجهتها، تم تصميمه لتلبية احتياجاتي الشخصية فقط.  
لا يُقصد استخدامه في المهام ذات الحجم الكبير أو الحالات التي قد تصل إلى حدود معدلات استخدام API.  
لم يتم تنفيذ الميزات التي تقع خارج متطلباتي، لذا قد تحتاج إلى تعديل الكود ليناسب حالتك.

## ✨ المزايا

* **تصفية بناءً على الكلمات المفتاحية**: يطابق على الأقل كلمة مفتاحية واحدة من كل فئة من الفئات الثلاث.
* **التعرف على النصوص في الصور (OCR)**: يقوم بتحميل الصور المرفقة واستخراج النصوص منها باستخدام مكتبة `pytesseract`.
* **منع التكرار**: يتحقق من آخر 10 رسائل تم تحويلها قبل إرسال رسالة جديدة.
* **عوامل تصفية قابلة للتخصيص**: خزّن الكلمات المفتاحية وبيانات اعتماد Telegram API في ملف JSON.
* **مراقبة متعددة للمحادثات**: راقب عدة محادثات Telegram في وقت واحد.

</div>

<div dir="rtl">

## 📦 المتطلبات

</div>

<div dir="rtl">

* Python 3.8+
* بيانات اعتماد Telegram API (API ID و API Hash — راجع [وثائق Telethon](https://docs.telethon.dev/en/stable/basic/signing-in.html#signing-in) للحصول على التعليمات)
* تثبيت `tesseract-ocr` لدعم وظيفة OCR

## 📚 المكتبات المستخدمة
</div>

```txt
telethon
pillow
pytesseract
scikit-learn
cryptg
tenacity
````

<div dir="rtl">

## ⚙️ التثبيت

1. استنسخ المستودع:

</div>

```bash
git clone https://github.com/5wHN28Dg/tele-notify.git
cd tele-notify
```

<div dir="rtl">

2. أنشئ بيئة افتراضية:

</div>

```bash
python3 -m venv venv
source venv/bin/activate
```

<div dir="rtl">

3. ثبّت المكتبات:

</div>

```bash
pip install -r requirements.txt
```

<div dir="rtl">

4. ثبّت Tesseract OCR:

* **Ubuntu/Debian**:

</div>

```bash
sudo apt install tesseract-ocr tesseract-ocr-eng tesseract-ocr-ara # أو أي لغة أخرى تحتاجها
```

<div dir="rtl">

* **Windows**: [رابط التحميل](https://github.com/UB-Mannheim/tesseract/wiki)
* **MacOS**:

</div>

```bash
brew install tesseract tesseract-lang
```

<div dir="rtl">

* **ملاحظة**: إذا واجهت مشاكل في تثبيت Tesseract، راجع [الوثائق الرسمية](https://tesseract-ocr.github.io/tessdoc/Installation.html).

## 🛠 الإعداد

1. شغّل الكود التالي (بعد إدخال API ID و API Hash) للحصول على قائمة المحادثات مع أسمائها و**معرّفاتها**:

</div>

```python
from telethon import TelegramClient

api_id = YOUR_API_ID
api_hash = 'YOUR_API_HASH'

client = TelegramClient('session_name', api_id, api_hash)

async def main():
    async for dialog in client.iter_dialogs():
        print('{:>14}: {}'.format(dialog.id, dialog.title))

with client:
    client.loop.run_until_complete(main())
```

<div dir="rtl">

2. أنشئ ملف `config.json` داخل مجلد المشروع بالصيغة التالية:

</div>

```json
{
  "api_id": YOUR_API_ID,
  "api_hash": "YOUR_API_HASH",
  "target_chat": chat_id,
  "chats": [chat_id, chat_id],
  "level_keywords_en": ["keyword1", "keyword2"],
  "level_keywords_ar": ["keyword3", "keyword4"],
  "role_keywords_en": ["keyword1", "keyword2"],
  "role_keywords_ar": ["keyword3", "keyword4"],
  "location_keywords_en": ["keyword1", "keyword2"],
  "location_keywords_ar": ["keyword3", "keyword4"],
  "recent_messages": []
}
```

<div dir="rtl">

## 🚀 الاستخدام

لتشغيل البرنامج:

</div>

```bash
python main.py
```


<div dir="rtl">

سيقوم البرنامج بـ:

1. مطالبتك بتسجيل الدخول عن طريق إدخال رقم هاتفك والكود.
2. البدء في مراقبة المحادثات المحددة.
3. معالجة أي رسائل غير مقروءة ثم مراقبة الرسائل الجديدة:

   * استخراج النص من الرسالة والصورة (إن وجدت).
   * التحقق من الكلمات المفتاحية المطلوبة.
   * تجاهل الرسائل المكررة من آخر 10 رسائل.
   * إعادة توجيهها إلى المحادثة الهدف.

</div>

<div dir="rtl">
## 📝 قائمة المهام:

* [ ] إصلاح مشاكل السباق عند تحديث recent\_messages / الكتابة إلى config.json. 🔄
* [ ] معالجة خاصة للرسائل مثل: `#Basrah www.example.com/electrical-engineering-intern/`.
* [ ] إعداد طريقة للإشعار في حال توقف البرنامج عن العمل.
* [ ] واجهة CLI جميلة مع إحصائيات في الوقت الفعلي بدلًا من النصوص المتدفقة.
* [ ] تحليل الكود لإعادة هيكلته للمرة الثانية إذا لزم الأمر.

## 📜 الترخيص

هذا المشروع مرخّص تحت رخصة AGPL.
</div>