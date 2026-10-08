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
        f"Your daily {language}\n\n{entry['text']}\n"
        + (f"{pronunciation}\n" if pronunciation else "")
        + f"{entry['meaning']}\n\n{footer}"
    )
    direction = "rtl" if entry["language"] == "ur" else "ltr"
    html = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;'
        'max-width:560px;margin:auto;padding:32px;color:#222">'
        f"<p>Your daily {language}</p>"
        f'<h1 lang="{entry["language"]}" dir="{direction}">{escape(entry["text"])}</h1>'
        + (f"<p>{escape(pronunciation)}</p>" if pronunciation else "")
        + f"<p>{escape(entry['meaning'])}</p>"
        '<hr><p style="font-size:13px">To stop daily vocabulary emails, '
        f'<a href="{escape(web_url, quote=True)}/">sign in</a> and turn off '
        "reminders under Reminders.</p></body></html>"
    )
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
