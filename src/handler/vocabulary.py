"""Everyday vocabulary, independent of review decks.

Stable IDs must never be reassigned. Append new entries to extend the pool;
editing spelling does not make an already claimed entry eligible again.
Romanization is an approximate pronunciation aid.
"""

import secrets
from html import escape

# ID | English | Urdu | Roman Urdu | Spanish
_DATA = """
cup|cup|کپ|kap|taza
glass|drinking glass|گلاس|gilaas|vaso
plate|plate|پلیٹ|pleyt|plato
spoon|spoon|چمچ|chamach|cuchara
knife|knife|چھری|chhuri|cuchillo
bottle|bottle|بوتل|botal|botella
bag|bag|تھیلا|thaila|bolsa
key|key|چابی|chaabi|llave
door|door|دروازہ|darwaaza|puerta
window|window|کھڑکی|khirki|ventana
chair|chair|کرسی|kursi|silla
table|table|میز|mez|mesa
bed|bed|بستر|bistar|cama
pillow|pillow|تکیہ|takiya|almohada
blanket|blanket|کمبل|kambal|manta
towel|towel|تولیہ|tauliya|toalla
soap|soap|صابن|saaban|jabón
mirror|mirror|آئینہ|aaina|espejo
comb|comb|کنگھی|kanghi|peine
shoe|shoe|جوتا|joota|zapato
sock|sock|جراب|juraab|calcetín
shirt|shirt|قمیض|qameez|camisa
pocket|pocket|جیب|jeb|bolsillo
umbrella|umbrella|چھتری|chhatri|paraguas
book|book|کتاب|kitaab|libro
pen|pen|قلم|qalam|bolígrafo
paper|paper|کاغذ|kaaghaz|papel
wall|wall|دیوار|deewaar|pared
roof|roof|چھت|chhat|techo
stairs|stairs|سیڑھیاں|seerhiyaan|escaleras
room|room|کمرہ|kamra|habitación
kitchen|kitchen|باورچی خانہ|baawarchi khaana|cocina
fridge|fridge|فریج|frij|nevera
fan|fan|پنکھا|pankha|ventilador
water|water|پانی|paani|agua
milk|milk|دودھ|doodh|leche
tea|tea|چائے|chaaye|té
sugar|sugar|چینی|cheeni|azúcar
salt|salt|نمک|namak|sal
rice|rice|چاول|chaawal|arroz
egg|egg|انڈا|anda|huevo
onion|onion|پیاز|pyaaz|cebolla
potato|potato|آلو|aaloo|patata
tomato|tomato|ٹماٹر|tamaatar|tomate
apple|apple|سیب|seb|manzana
banana|banana|کیلا|kela|plátano
mango|mango|آم|aam|mango
lemon|lemon|لیموں|leemoon|limón
shop|shop|دکان|dukaan|tienda
street|street|گلی|gali|calle
road|road|سڑک|sarak|carretera
car|car|گاڑی|gaari|coche
bicycle|bicycle|سائیکل|saikil|bicicleta
tree|tree|درخت|darakht|árbol
flower|flower|پھول|phool|flor
rain|rain|بارش|baarish|lluvia
sun|sun|سورج|sooraj|sol
moon|moon|چاند|chaand|luna
cat|cat|بلی|billi|gato
dog|dog|کتا|kutta|perro
no-problem|No problem.|کوئی بات نہیں۔|Koi baat nahin.|No pasa nada.
one-moment|One moment.|ایک منٹ۔|Ek minute.|Un momento.
lets-go|Let's go.|چلو۔|Chalo.|Vamos.
whats-up|What's up? / What's going on?|کیا ہو رہا ہے؟|Kya ho raha hai?|¿Qué pasa?
all-good|Everything okay?|سب ٹھیک ہے؟|Sab theek hai?|¿Todo bien?
see-you|See you later.|پھر ملتے ہیں۔|Phir milte hain.|Nos vemos.
leave-it|Let it go. / Leave it.|چھوڑو۔|Chhoro.|Déjalo.
come-here|Come here. (informal)|یہاں آؤ۔|Yahaan aao.|Ven aquí.
sit-down|Sit down. (informal)|بیٹھو۔|Baitho.|Siéntate.
what-happened|What happened?|کیا ہوا؟|Kya hua?|¿Qué pasó?
""".strip()


def catalog(language):
    if language not in {"ur", "es"}:
        return []
    result = []
    for line in _DATA.splitlines():
        key, meaning, urdu, roman, spanish = line.split("|")
        result.append(
            {
                "id": f"{language}#{key}",
                "language": language,
                "text": urdu if language == "ur" else spanish,
                "pronunciation": roman if language == "ur" else "",
                "meaning": meaning,
            }
        )
    return result


def choose(repository, user_id, language):
    seen = set()
    cursor = {}
    while True:
        page = repository.client.query(
            TableName=repository.table_name,
            ConsistentRead=True,
            KeyConditionExpression="userId = :user AND begins_with(itemId, :prefix)",
            ExpressionAttributeValues=repository.encode(
                {":user": user_id, ":prefix": f"VOCAB#{language}#"}
            ),
            **cursor,
        )
        seen.update(repository.decode(raw)["vocabularyId"] for raw in page["Items"])
        if "LastEvaluatedKey" not in page:
            break
        cursor = {"ExclusiveStartKey": page["LastEvaluatedKey"]}
    unseen = [entry for entry in catalog(language) if entry["id"] not in seen]
    return secrets.choice(unseen) if unseen else None


def email_content(entry, web_url):
    language = {"ur": "Urdu", "es": "Spanish"}[entry["language"]]
    pronunciation = entry["pronunciation"]
    footer = (
        "To stop daily vocabulary emails, sign in and turn off reminders "
        f"under Reminders: {web_url}/"
    )
    text = (
        f"lexikhan — A little, every day\n\nYour daily {language}\n\n"
        f"{entry['text']}\n"
        + (f"Romanized pronunciation: {pronunciation}\n" if pronunciation else "")
        + f"Meaning: {entry['meaning']}\n\n{footer}"
    )
    direction = "rtl" if entry["language"] == "ur" else "ltr"
    word_font = "56px" if entry["language"] == "ur" else "48px"
    url = escape(web_url.rstrip("/") + "/", quote=True)
    label_style = (
        "margin:0;color:#647268;font:12px/1.5 Arial,sans-serif;"
        "letter-spacing:1.5px;text-transform:uppercase"
    )
    pronunciation_html = ""
    if pronunciation:
        pronunciation_html = f"""
        <tr><td style="padding:24px 28px 0">
          <p style="{label_style}">Romanized pronunciation</p>
          <p lang="ur-Latn" dir="ltr" style="margin:8px 0 0;color:#224c3f;
            font:28px/1.5 Georgia,serif">{escape(pronunciation)}</p>
        </td></tr>"""
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Your daily {language} · Lexikhan</title>
</head>
<body style="margin:0;padding:0;background-color:#f5f3ed;color:#273c34">
  <div style="display:none;max-height:0;overflow:hidden;mso-hide:all">
    {escape(pronunciation)} — {escape(entry["meaning"])}. A little, every day.
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
    bgcolor="#f5f3ed" style="background-color:#f5f3ed">
    <tr><td align="center" style="padding:32px 16px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
        style="width:100%;max-width:600px">
        <tr><td style="padding:0 0 28px">
          <table role="presentation" cellpadding="0" cellspacing="0">
            <tr>
              <td width="42" height="44" align="center" bgcolor="#224c3f"
                style="border-radius:9px;background-color:#224c3f;
                font:30px/44px Georgia,serif;color:#faf9f2">
                L<span style="color:#d6dc92">•</span>
              </td>
              <td style="padding-left:12px">
                <a href="{url}" style="color:#273c34;text-decoration:none;
                  font:bold 26px/1.2 Arial,sans-serif;letter-spacing:-1px">
                  lexikhan</a>
              </td>
            </tr>
          </table>
          <p style="margin:16px 0 0;color:#647268;font:11px/1.5 Arial,sans-serif;
            letter-spacing:2px">A LITTLE, EVERY DAY</p>
        </td></tr>
        <tr><td bgcolor="#fffdf7" style="background-color:#fffdf7;
          border:1px solid #d9ddd2;border-radius:16px">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
            <tr><td style="padding:28px 28px 0">
              <p style="{label_style}">YOUR DAILY {language.upper()}</p>
            </td></tr>
            <tr><td style="padding:24px 28px 28px">
              <h1 lang="{entry["language"]}" dir="{direction}"
                style="margin:0;color:#224c3f;font-family:Georgia,
                'Times New Roman',serif;font-size:{word_font};font-weight:400;
                line-height:1.65;text-align:left;overflow-wrap:break-word">
                {escape(entry["text"])}</h1>
            </td></tr>
            <tr><td style="padding:0 28px">
              <table role="presentation" width="100%" cellpadding="0"
                cellspacing="0"><tr><td height="1" bgcolor="#d9ddd2"
                  style="font-size:0;line-height:0">&nbsp;</td></tr></table>
            </td></tr>
            {pronunciation_html}
            <tr><td style="padding:24px 28px 32px">
              <p style="{label_style}">Meaning</p>
              <p style="margin:8px 0 0;color:#273c34;
                font:24px/1.5 Arial,sans-serif">{escape(entry["meaning"])}</p>
            </td></tr>
          </table>
        </td></tr>
        <tr><td style="padding:24px 4px 0;color:#647268;
          font:14px/1.7 Arial,sans-serif">
          <p style="margin:0">One small addition to your everyday vocabulary.</p>
          <p style="margin:12px 0 0">
            <a href="{url}" style="color:#224c3f;text-decoration:underline">
              Manage your emails</a><br>
            To stop daily emails, sign in and turn off reminders under Reminders.
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""
    return {
        "Subject": {
            "Data": f"Your daily {language}: {entry['meaning']}",
            "Charset": "UTF-8",
        },
        "Body": {
            "Text": {"Data": text, "Charset": "UTF-8"},
            "Html": {"Data": html, "Charset": "UTF-8"},
        },
    }
