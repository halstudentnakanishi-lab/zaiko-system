from fastapi import FastAPI, HTTPException, Depends
import pymysql
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
import math


app = FastAPI()

from fastapi.staticfiles import StaticFiles

app.mount("/app", StaticFiles(directory="static", html=True), name="static")
app.mount("/static", StaticFiles(directory="static"), name="static")


# ===== JWT設定 =====
JWT_SECRET = "CHANGE_ME_TO_RANDOM_STRING"  # 本当は環境変数が理想
JWT_ALG = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 1日

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ===== DB接続 =====
def get_conn():
    return pymysql.connect(
        host="localhost",
        user="root",
        password="",
        database="inventory_db",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
    )

# ===== 認証（Bearer） =====
bearer_scheme = HTTPBearer()

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)

def create_access_token(user_id: int, role: str) -> str:
    payload = {
        "sub": str(user_id),
        "role": role,
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": datetime.utcnow(),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer_scheme)
) -> dict:
    token = creds.credentials

    # ---- デバッグ（原因確定したら消してOK）----
    print("=== AUTH DEBUG ===")
    print("scheme:", creds.scheme)
    print("token(raw) head:", token[:50])
    print("token(raw) len:", len(token))
    print("=== /AUTH DEBUG ===")
    # ---------------------------------------------

    # Swagger入力のゆれを吸収（Bearer付き/なし、空白改行）
    token = token.strip()
    if token.lower().startswith("bearer "):
        token = token.split(" ", 1)[1].strip()

    payload = decode_token(token)
    return {"user_id": int(payload["sub"]), "role": payload["role"]}

# ===== リクエストモデル =====
class StockTransactionRequest(BaseModel):
    product_id: int
    change_type: str  # "IN" or "OUT"
    quantity: int
    reason_code: str | None = None  # 追加：SALE / PURCHASE / ADJUST / DAMAGED など
    note: str | None = None


class LoginRequest(BaseModel):
    user_id: int
    password: str

class CreateUserRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    role: str  # "admin" or "staff"

class ResetPasswordRequest(BaseModel):
    new_password: str


# ===== API =====
@app.get("/categories")
def list_categories():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT category_id, category_name FROM categories ORDER BY category_id;")
            return cur.fetchall()
    finally:
        conn.close()

@app.get("/products")
def list_products():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    p.product_id,
                    p.product_name,
                    p.price,
                    p.is_active,
                    c.category_id,
                    c.category_name,
                    s.quantity
                FROM products p
                JOIN categories c ON p.category_id = c.category_id
                LEFT JOIN stocks s ON p.product_id = s.product_id
                ORDER BY p.product_id;
            """)
            return cur.fetchall()
    finally:
        conn.close()

@app.get("/stocks")
def list_stocks():
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    p.product_id,
                    p.product_name,
                    c.category_name,
                    s.quantity
                FROM stocks s
                JOIN products p ON s.product_id = p.product_id
                JOIN categories c ON p.category_id = c.category_id
                ORDER BY p.product_id;
            """)
            return cur.fetchall()
    finally:
        conn.close()

@app.post("/stocks/transactions")
def create_stock_transaction(
    req: StockTransactionRequest,
    me: dict = Depends(get_current_user)
):
    # 🔽 追加：入力ゆれ吸収（AI学習・実務どちらにも重要）
    req.change_type = req.change_type.upper()

    if req.change_type not in ("IN", "OUT"):
        raise HTTPException(status_code=400, detail="change_type must be IN or OUT")

    if req.quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 現在在庫を取得
            cur.execute(
                "SELECT quantity FROM stocks WHERE product_id = %s",
                (req.product_id,)
            )
            row = cur.fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Stock not found")

            current_qty = row["quantity"]

            # 出庫時の在庫チェック
            if req.change_type == "OUT" and current_qty < req.quantity:
                raise HTTPException(status_code=400, detail="Stock shortage")

            # 在庫更新後の数量
            new_qty = (
                current_qty + req.quantity
                if req.change_type == "IN"
                else current_qty - req.quantity
            )

            # 在庫更新
            cur.execute(
                "UPDATE stocks SET quantity = %s WHERE product_id = %s",
                (new_qty, req.product_id)
            )

            # 履歴登録（before / after 保存）
            cur.execute(
                """
                INSERT INTO inventory_transactions
                    (product_id, user_id, change_type, reason_code, quantity,
                    before_quantity, after_quantity, note)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    req.product_id,
                    me["user_id"],
                    req.change_type,
                    req.reason_code,
                    req.quantity,
                    current_qty,
                    new_qty,
                    req.note
                )
            )


        conn.commit()
        return {
            "message": "Stock updated",
            "before_quantity": current_qty,
            "after_quantity": new_qty
        }

    finally:
        conn.close()


@app.get("/stocks/transactions")
def list_stock_transactions(me: dict = Depends(get_current_user)):
    """
    在庫履歴一覧（ログイン必須）
    """
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    it.transaction_id,
                    it.created_at,
                    it.change_type,
                    it.quantity,
                    it.before_quantity,
                    it.after_quantity,
                    it.note,
                    it.reason_code,
                    u.user_id,
                    u.name AS user_name,
                    p.product_id,
                    p.product_name
                FROM inventory_transactions it
                JOIN users u ON it.user_id = u.user_id
                JOIN products p ON it.product_id = p.product_id
                ORDER BY it.created_at DESC
                LIMIT 100;
            """)
            return cur.fetchall()
    finally:
        conn.close()


from fastapi import Query

@app.get("/ai/analysis/stock_trend")
def stock_trend_analysis(
    days: int = Query(14, ge=1, le=365),
    lead_time_days: int = Query(7, ge=0, le=60),
    safety_days: int = Query(3, ge=0, le=60),
    reason: str | None = Query(None),  # ✅ 追加：reason_codeで絞り込み（例：SALE）
    me: dict = Depends(get_current_user)
):
    """
    在庫履歴（OUT）から消費傾向を分析し、在庫切れ予測と発注提案を返す
    - days: 直近何日分の履歴で平均を出すか
    - lead_time_days: 発注〜納品までの想定日数
    - safety_days: 余裕を持たせる安全在庫（日数）
    - reason: OUT履歴のreason_codeで絞る（例：SALE / ADJUST / DAMAGED）
    """
    # ✅ reasonの表記ゆれ吸収
    if reason:
        reason = reason.strip().upper()

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 現在庫（stocks）＋商品名
            cur.execute("""
                SELECT
                    p.product_id,
                    p.product_name,
                    COALESCE(s.quantity, 0) AS current_stock
                FROM products p
                LEFT JOIN stocks s ON s.product_id = p.product_id
                WHERE p.is_active = 1
                ORDER BY p.product_id;
            """)
            stocks = cur.fetchall()

            # 直近days日分の OUT 合計（商品ごと）
            if reason:
                cur.execute(f"""
                    SELECT
                        it.product_id,
                        COALESCE(SUM(it.quantity), 0) AS total_out
                    FROM inventory_transactions it
                    WHERE it.change_type = 'OUT'
                      AND it.created_at >= (NOW() - INTERVAL {days} DAY)
                      AND it.reason_code = %s
                    GROUP BY it.product_id;
                """, (reason,))
            else:
                cur.execute(f"""
                    SELECT
                        it.product_id,
                        COALESCE(SUM(it.quantity), 0) AS total_out
                    FROM inventory_transactions it
                    WHERE it.change_type = 'OUT'
                      AND it.created_at >= (NOW() - INTERVAL {days} DAY)
                    GROUP BY it.product_id;
                """)

            out_map = {row["product_id"]: row["total_out"] for row in cur.fetchall()}

        # Python側で計算（ここを将来自作AIに差し替えやすい）
        horizon = lead_time_days + safety_days  # 何日分を確保したいか
        results = []

        for s in stocks:
            pid = s["product_id"]
            name = s["product_name"]
            current_stock = int(s["current_stock"])
            total_out = int(out_map.get(pid, 0))

            avg_daily_out = total_out / days  # 平均出庫/日

            # 残り日数（推定）：消費が0なら推定不可
            if avg_daily_out <= 0:
                estimated_days_left = None
            else:
                estimated_days_left = round(current_stock / avg_daily_out, 2)

            # 推奨発注数：horizon日分の消費を満たすように
            target_stock = math.ceil(avg_daily_out * horizon)
            recommended_order_qty = max(0, target_stock - current_stock)

            # 発注推奨フラグ（残りがhorizon未満なら）
            recommend = False
            if estimated_days_left is not None and estimated_days_left < horizon:
                recommend = True

            results.append({
                "product_id": pid,
                "product_name": name,
                "current_stock": current_stock,
                "days_window": days,
                "filter_reason": reason,  # ✅ 追加：何で絞ってるか表示（Noneなら全OUT）
                "total_out_in_window": total_out,
                "avg_daily_out": round(avg_daily_out, 3),
                "estimated_days_left": estimated_days_left,
                "lead_time_days": lead_time_days,
                "safety_days": safety_days,
                "horizon_days": horizon,
                "recommended_order_qty": recommended_order_qty,
                "recommend": recommend,
                "reason": (
                    "直近の出庫が無いため推定不可（提案なし）"
                    if estimated_days_left is None
                    else ("在庫が確保日数を下回る可能性" if recommend else "現状は問題なし")
                )
            })

        # 発注推奨を上に並べる（見せ方）
        results.sort(key=lambda x: (not x["recommend"], x["product_id"]))
        return results

    finally:
        conn.close()



@app.post("/auth/login")
def login(req: LoginRequest):
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT user_id, name, password_hash, role FROM users WHERE user_id=%s",
                (req.user_id,)
            )
            user = cur.fetchone()

            if user is None:
                raise HTTPException(status_code=401, detail="Invalid user_id or password")

            if not verify_password(req.password, user["password_hash"]):
                raise HTTPException(status_code=401, detail="Invalid email or password")

            token = create_access_token(user_id=user["user_id"], role=user["role"])
            return {
                "user_id": user["user_id"],
                "name": user["name"],
                "role": user["role"],
                "access_token": token,
                "token_type": "bearer"
            }
    finally:
        conn.close()


@app.get("/users")
def list_users(me: dict = Depends(get_current_user)):
    if me["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT user_id, name, email, role, created_at
                FROM users
                ORDER BY user_id;
            """)
            return cur.fetchall()
    finally:
        conn.close()



@app.post("/users")
def create_user(req: CreateUserRequest, me: dict = Depends(get_current_user)):
    # 管理者のみ
    if me["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    if req.role not in ("admin", "staff"):
        raise HTTPException(status_code=400, detail="role must be admin or staff")

    hashed = hash_password(req.password)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # email重複チェック
            cur.execute("SELECT user_id FROM users WHERE email=%s", (req.email,))
            if cur.fetchone() is not None:
                raise HTTPException(status_code=409, detail="Email already exists")

            cur.execute(
                """
                INSERT INTO users (name, email, password_hash, role)
                VALUES (%s, %s, %s, %s)
                """,
                (req.name, req.email, hashed, req.role)
            )
        conn.commit()
        return {"message": "User created"}
    finally:
        conn.close()


@app.put("/users/{target_user_id}/password")
def reset_user_password(
    target_user_id: int,
    req: ResetPasswordRequest,
    me: dict = Depends(get_current_user)
):
    # 管理者のみ
    if me["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin only")

    # パスワード最低限チェック
    if len(req.new_password) < 6:
        raise HTTPException(
            status_code=400,
            detail="Password must be at least 6 characters"
        )

    hashed = hash_password(req.new_password)

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            # 対象ユーザー存在確認
            cur.execute(
                "SELECT user_id FROM users WHERE user_id=%s",
                (target_user_id,)
            )
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=404,
                    detail="User not found"
                )

            # パスワード更新
            cur.execute(
                "UPDATE users SET password_hash=%s WHERE user_id=%s",
                (hashed, target_user_id)
            )
        conn.commit()
        return {
            "message": "Password updated",
            "user_id": target_user_id
        }
    finally:
        conn.close()

