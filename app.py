from flask import Flask, request, jsonify, render_template_string
from flask_cors import CORS
import sqlite3, os, json, requests
from datetime import datetime
import uuid

app = Flask(__name__)
CORS(app)

ADMIN_KEY = os.environ.get("ADMIN_KEY", "admin2024secret")
WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
VERIFY_TOKEN = os.environ.get("VERIFY_TOKEN", "shopbot2verify")
DB_PATH = os.environ.get("DB_PATH", "shopbot2.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db(); c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, description TEXT,
            price REAL NOT NULL, image_url TEXT, category TEXT, stock INTEGER DEFAULT 100,
            active INTEGER DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT UNIQUE NOT NULL,
            customer_name TEXT NOT NULL, customer_phone TEXT NOT NULL, customer_address TEXT NOT NULL,
            items TEXT NOT NULL, total REAL NOT NULL, payment_method TEXT DEFAULT 'COD',
            payment_status TEXT DEFAULT 'pending', order_status TEXT DEFAULT 'placed',
            upi_ref TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    """)
    for k,v in [("shop_name","My Online Store"),("shop_phone",""),("upi_id",""),("delivery_charge","40"),("free_delivery_above","500")]:
        c.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k,v))
    c.execute("SELECT COUNT(*) FROM products")
    if c.fetchone()[0] == 0:
        for p in [("Cotton T-Shirt","Premium cotton t-shirt",399,"","Clothing",50),
                  ("Denim Jeans","Slim fit jeans",799,"","Clothing",30),
                  ("Running Shoes","Lightweight shoes",1299,"","Footwear",20),
                  ("Backpack","15L waterproof",599,"","Accessories",40),
                  ("Water Bottle","1L steel bottle",249,"","Accessories",100)]:
            c.execute("INSERT INTO products (name,description,price,image_url,category,stock) VALUES (?,?,?,?,?,?)", p)
    conn.commit(); conn.close()

init_db()

def get_setting(k, d=""): 
    conn=get_db(); r=conn.execute("SELECT value FROM settings WHERE key=?",(k,)).fetchone(); conn.close()
    return r["value"] if r else d

def send_whatsapp(phone, message):
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        print(f"[WA] {phone}: {message[:50]}"); return False
    phone = phone.replace("+","").replace(" ","").replace("-","")
    if not phone.startswith("91"): phone = "91"+phone
    try:
        r = requests.post(f"https://graph.facebook.com/v18.0/{WHATSAPP_PHONE_ID}/messages",
            headers={"Authorization":f"Bearer {WHATSAPP_TOKEN}","Content-Type":"application/json"},
            json={"messaging_product":"whatsapp","to":phone,"type":"text","text":{"body":message}}, timeout=10)
        return r.status_code == 200
    except Exception as e: print(f"[WA Error] {e}"); return False

def confirm_msg(order):
    items = json.loads(order["items"])
    itxt = "\n".join([f"  • {i['name']} x{i['qty']} = ₹{i['price']*i['qty']}" for i in items])
    upi = get_setting("upi_id","")
    pinfo = f"\n💳 UPI ID: {upi}\nSend ₹{order['total']} & share screenshot." if order["payment_method"]=="UPI" and upi else "\n💵 Cash on Delivery"
    return f"""✅ Order Confirmed! — {get_setting('shop_name','Store')}

📦 Order ID: {order['order_id']}
👤 {order['customer_name']}
📍 {order['customer_address']}

🛍️ Items:
{itxt}

💰 Total: ₹{order['total']}{pinfo}

📱 Reply with Order ID to track. Thank you! 🙏"""

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

@app.route("/")
def storefront():
    return render_template_string(open(os.path.join(BASE_DIR,"storefront.html")).read())

@app.route("/api/products")
def api_products():
    cat = request.args.get("category","")
    conn=get_db()
    rows=conn.execute("SELECT * FROM products WHERE active=1"+((" AND category=?") if cat else "")+" ORDER BY id",((cat,) if cat else ())).fetchall()
    conn.close(); return jsonify([dict(r) for r in rows])

@app.route("/api/categories")
def api_categories():
    conn=get_db(); rows=conn.execute("SELECT DISTINCT category FROM products WHERE active=1").fetchall(); conn.close()
    return jsonify([r["category"] for r in rows])

@app.route("/api/settings/public")
def api_public_settings():
    conn=get_db(); rows=conn.execute("SELECT key,value FROM settings WHERE key IN ('shop_name','upi_id','delivery_charge','free_delivery_above')").fetchall(); conn.close()
    return jsonify({r["key"]:r["value"] for r in rows})

@app.route("/api/order", methods=["POST"])
def place_order():
    data=request.json
    for f in ["customer_name","customer_phone","customer_address","items","total","payment_method"]:
        if not data.get(f): return jsonify({"error":f"Missing: {f}"}),400
    oid = "SB"+datetime.now().strftime("%m%d%H%M")+str(uuid.uuid4())[:4].upper()
    conn=get_db()
    conn.execute("INSERT INTO orders (order_id,customer_name,customer_phone,customer_address,items,total,payment_method,notes) VALUES (?,?,?,?,?,?,?,?)",
        (oid,data["customer_name"],data["customer_phone"],data["customer_address"],json.dumps(data["items"]),data["total"],data["payment_method"],data.get("notes","")))
    conn.commit()
    order=dict(conn.execute("SELECT * FROM orders WHERE order_id=?",(oid,)).fetchone()); conn.close()
    send_whatsapp(order["customer_phone"], confirm_msg(order))
    return jsonify({"success":True,"order_id":oid})

@app.route("/api/track/<oid>")
def track_order(oid):
    conn=get_db(); o=conn.execute("SELECT order_id,customer_name,order_status,payment_status,payment_method,total,created_at FROM orders WHERE order_id=?",(oid.upper(),)).fetchone(); conn.close()
    return jsonify(dict(o)) if o else (jsonify({"error":"Not found"}),404)

@app.route("/webhook", methods=["GET","POST"])
def webhook():
    if request.method=="GET":
        return request.args.get("hub.challenge","") if request.args.get("hub.verify_token")==VERIFY_TOKEN else ("Forbidden",403)
    try:
        msg=request.json["entry"][0]["changes"][0]["value"]["messages"][0]
        text=msg["text"]["body"].strip().upper(); phone=msg["from"]
        conn=get_db(); o=conn.execute("SELECT * FROM orders WHERE order_id=?",(text,)).fetchone(); conn.close()
        if o:
            o=dict(o); em={"placed":"📋","confirmed":"✅","shipped":"🚚","out_for_delivery":"🛵","delivered":"🎉"}.get(o["order_status"],"📦")
            send_whatsapp(phone,f"{em} Order {o['order_id']}\nStatus: {o['order_status'].upper()}\nTotal: ₹{o['total']}")
        else:
            send_whatsapp(phone,"Hi! Reply with your Order ID to track your order.")
    except Exception as e: print(f"[Webhook] {e}")
    return jsonify({"status":"ok"})

def chk(req): return (req.args.get("key") or req.headers.get("X-Admin-Key")) == ADMIN_KEY

@app.route("/admin")
def admin():
    return render_template_string(open(os.path.join(BASE_DIR,"admin.html")).read()) if chk(request) else ("Unauthorized",401)

@app.route("/api/admin/orders")
def admin_orders():
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    s=request.args.get("status",""); conn=get_db()
    rows=conn.execute("SELECT * FROM orders"+(f" WHERE order_status=?" if s else "")+" ORDER BY created_at DESC",(s,) if s else ()).fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/admin/order/<oid>/status", methods=["POST"])
def update_status(oid):
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    ns=request.json.get("status")
    if ns not in ["placed","confirmed","shipped","out_for_delivery","delivered","cancelled"]: return jsonify({"error":"Invalid"}),400
    conn=get_db(); o=conn.execute("SELECT * FROM orders WHERE order_id=?",(oid,)).fetchone()
    if not o: conn.close(); return jsonify({"error":"Not found"}),404
    conn.execute("UPDATE orders SET order_status=?,updated_at=? WHERE order_id=?",(ns,datetime.now().isoformat(),oid)); conn.commit(); conn.close()
    msgs={"confirmed":"✅ Order confirmed!","shipped":"🚚 Order shipped!","out_for_delivery":"🛵 Out for delivery!","delivered":"🎉 Delivered! Thank you!","cancelled":"❌ Order cancelled."}
    if ns in msgs: send_whatsapp(o["customer_phone"],f"📦 Order {oid}\n{msgs[ns]}")
    return jsonify({"success":True})

@app.route("/api/admin/order/<oid>/payment", methods=["POST"])
def update_payment(oid):
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    d=request.json; conn=get_db()
    conn.execute("UPDATE orders SET payment_status=?,upi_ref=?,updated_at=? WHERE order_id=?",(d.get("status","paid"),d.get("upi_ref",""),datetime.now().isoformat(),oid))
    conn.commit(); conn.close(); return jsonify({"success":True})

@app.route("/api/admin/products")
def admin_products():
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    conn=get_db(); rows=conn.execute("SELECT * FROM products ORDER BY id").fetchall(); conn.close()
    return jsonify([dict(r) for r in rows])

@app.route("/api/admin/product", methods=["POST"])
def add_product():
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    d=request.json; conn=get_db()
    conn.execute("INSERT INTO products (name,description,price,image_url,category,stock) VALUES (?,?,?,?,?,?)",
        (d["name"],d.get("description",""),float(d["price"]),d.get("image_url",""),d.get("category","General"),int(d.get("stock",100))))
    conn.commit(); conn.close(); return jsonify({"success":True})

@app.route("/api/admin/product/<int:pid>", methods=["PUT","DELETE"])
def edit_product(pid):
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    conn=get_db()
    if request.method=="DELETE": conn.execute("UPDATE products SET active=0 WHERE id=?",(pid,))
    else:
        d=request.json
        conn.execute("UPDATE products SET name=?,description=?,price=?,image_url=?,category=?,stock=?,active=? WHERE id=?",
            (d["name"],d.get("description",""),float(d["price"]),d.get("image_url",""),d.get("category","General"),int(d.get("stock",0)),int(d.get("active",1)),pid))
    conn.commit(); conn.close(); return jsonify({"success":True})

@app.route("/api/admin/settings", methods=["GET","POST"])
def admin_settings():
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    conn=get_db()
    if request.method=="POST":
        for k,v in request.json.items(): conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)",(k,v))
        conn.commit(); conn.close(); return jsonify({"success":True})
    rows=conn.execute("SELECT key,value FROM settings").fetchall(); conn.close()
    return jsonify({r["key"]:r["value"] for r in rows})

@app.route("/api/admin/stats")
def admin_stats():
    if not chk(request): return jsonify({"error":"Unauthorized"}),401
    conn=get_db()
    s={"total_orders":conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
       "total_revenue":conn.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE order_status!='cancelled'").fetchone()[0],
       "pending":conn.execute("SELECT COUNT(*) FROM orders WHERE order_status='placed'").fetchone()[0],
       "delivered":conn.execute("SELECT COUNT(*) FROM orders WHERE order_status='delivered'").fetchone()[0]}
    conn.close(); return jsonify(s)

@app.route("/health")
def health(): return jsonify({"status":"✅ running","platform":"ShopBot 2"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT",5000)), debug=False)
