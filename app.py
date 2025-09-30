# app.py
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from enum import Enum
from typing import Optional, Dict
from datetime import datetime
from dateutil import parser

app = FastAPI(title="VISE API", version="1.0.0")

# -----------------------------
# Modelos y almacenamiento simple
# -----------------------------
class CardType(str, Enum):
    Classic = "Classic"
    Gold = "Gold"
    Platinum = "Platinum"
    Black = "Black"
    White = "White"

class ClientIn(BaseModel):
    name: str = Field(..., examples=["John Doe"])
    country: str = Field(..., examples=["USA"])
    monthlyIncome: float = Field(..., ge=0, examples=[1200])
    viseClub: bool = Field(..., examples=[True])
    cardType: CardType = Field(..., examples=["Platinum"])

class ClientOut(BaseModel):
    clientId: int
    name: str
    cardType: CardType
    status: str
    message: str

class ErrorOut(BaseModel):
    status: str
    error: str

class PurchaseIn(BaseModel):
    clientId: int
    amount: float = Field(..., gt=0)
    currency: str = Field(..., examples=["USD"])
    purchaseDate: str = Field(..., examples=["2025-09-20T14:30:00Z"])
    purchaseCountry: str = Field(..., examples=["France"])

class PurchaseInfo(BaseModel):
    clientId: int
    originalAmount: float
    discountApplied: float
    finalAmount: float
    benefit: Optional[str] = None

class PurchaseOut(BaseModel):
    status: str
    purchase: PurchaseInfo

# Almacenamiento en memoria para simplificar (en producción usa BD)
CLIENTS: Dict[int, dict] = {}
NEXT_ID = 1

# Conjuntos de ayuda
BLACK_WHITE_COUNTRY_BLOCKLIST = {"China", "Vietnam", "India", "Irán", "Iran"}  # aceptar ambas grafías
WEEKDAY_NAME = {
    0: "Lunes",
    1: "Martes",
    2: "Miércoles",
    3: "Jueves",
    4: "Viernes",
    5: "Sábado",
    6: "Domingo",
}

# -----------------------------
# Validaciones de restricciones
# -----------------------------
def validate_client_restrictions(payload: ClientIn) -> Optional[str]:
    """Devuelve un string con el motivo de rechazo si NO cumple; si cumple, devuelve None."""
    ct = payload.cardType
    income = payload.monthlyIncome
    club = payload.viseClub
    country = payload.country

    if ct == CardType.Classic:
        return None

    if ct == CardType.Gold:
        if income < 500:
            return "El cliente no cumple con el ingreso mínimo de 500 USD mensuales para Gold"
        return None

    if ct == CardType.Platinum:
        if income < 1000:
            return "El cliente no cumple con el ingreso mínimo de 1000 USD mensuales para Platinum"
        if not club:
            return "El cliente no cumple con la suscripción VISE CLUB requerida para Platinum"
        return None

    if ct == CardType.Black:
        if income < 2000:
            return "El cliente no cumple con el ingreso mínimo de 2000 USD mensuales para Black"
        if not club:
            return "El cliente no cumple con la suscripción VISE CLUB requerida para Black"
        if country in BLACK_WHITE_COUNTRY_BLOCKLIST:
            return "El cliente con tarjeta Black no puede residir en China, Vietnam, India o Irán"
        return None

    if ct == CardType.White:
        # "Mismas restricciones que Black"
        if income < 2000:
            return "El cliente no cumple con el ingreso mínimo de 2000 USD mensuales para White"
        if not club:
            return "El cliente no cumple con la suscripción VISE CLUB requerida para White"
        if country in BLACK_WHITE_COUNTRY_BLOCKLIST:
            return "El cliente con tarjeta White no puede residir en China, Vietnam, India o Irán"
        return None

    return None

def purchase_restrictions(client: dict, purchase: PurchaseIn) -> Optional[str]:
    """
    Reglas de rechazo durante la compra.
    Nota: La consigna especifica la restricción de país para Black/White sobre la RESIDENCIA.
    Dado el ejemplo de error con compra desde China, agregamos además un bloqueo si la compra
    se origina en un país de la lista. Si no quieres este comportamiento, elimina este bloque.
    """
    card = client["cardType"]
    if card in (CardType.Black, CardType.White):
        if purchase.purchaseCountry in BLACK_WHITE_COUNTRY_BLOCKLIST:
            return f"El cliente con tarjeta {card} no puede realizar compras desde {purchase.purchaseCountry}"
    return None

# -----------------------------
# Beneficios / Descuentos
# -----------------------------
def is_abroad(client_country: str, purchase_country: str) -> bool:
    return (purchase_country or "").strip().lower() != (client_country or "").strip().lower()

def compute_discount(client: dict, purchase: PurchaseIn) -> tuple[float, Optional[str]]:
    """
    Devuelve (discount_amount, benefit_label).
    Regla: aplicamos UN solo beneficio, el MEJOR (el mayor descuento) que corresponda.
    """
    card = client["cardType"]
    amount = purchase.amount
    dt = parser.isoparse(purchase.purchaseDate)  # maneja Z/offset
    weekday = dt.weekday()  # lunes=0 .. domingo=6
    weekday_name = WEEKDAY_NAME[weekday]

    candidates: list[tuple[float, str]] = []

    if card == CardType.Classic:
        pass  # sin beneficios

    elif card == CardType.Gold:
        if weekday in (0, 1, 2) and amount > 100:
            candidates.append((amount * 0.15, f"{weekday_name} - Descuento 15%"))

    elif card == CardType.Platinum:
        if weekday in (0, 1, 2) and amount > 100:
            candidates.append((amount * 0.20, f"{weekday_name} - Descuento 20%"))
        if weekday == 5 and amount > 200:
            candidates.append((amount * 0.30, "Sábado - Descuento 30%"))
        if is_abroad(client["country"], purchase.purchaseCountry):
            candidates.append((amount * 0.05, "Exterior - Descuento 5%"))

    elif card == CardType.Black:
        if weekday in (0, 1, 2) and amount > 100:
            candidates.append((amount * 0.25, f"{weekday_name} - Descuento 25%"))
        if weekday == 5 and amount > 200:
            candidates.append((amount * 0.35, "Sábado - Descuento 35%"))
        if is_abroad(client["country"], purchase.purchaseCountry):
            candidates.append((amount * 0.05, "Exterior - Descuento 5%"))

    elif card == CardType.White:
        if weekday in (0, 1, 2, 3, 4) and amount > 100:
            candidates.append((amount * 0.25, f"{weekday_name} - Descuento 25%"))
        if weekday in (5, 6) and amount > 200:
            # 5=Sábado, 6=Domingo
            label = f"{weekday_name} - Descuento 35%"
            candidates.append((amount * 0.35, label))
        if is_abroad(client["country"], purchase.purchaseCountry):
            candidates.append((amount * 0.05, "Exterior - Descuento 5%"))

    if not candidates:
        return 0.0, None

    # Elegimos el mayor descuento
    best = max(candidates, key=lambda x: x[0])
    return best

# -----------------------------
# Rutas
# -----------------------------
@app.post("/client", response_model=ClientOut, responses={400: {"model": ErrorOut}})
def register_client(payload: ClientIn):
    global NEXT_ID

    error = validate_client_restrictions(payload)
    if error:
        raise HTTPException(status_code=400, detail={"status": "Rejected", "error": error})

    client_id = NEXT_ID
    NEXT_ID += 1

    CLIENTS[client_id] = {
        "clientId": client_id,
        "name": payload.name,
        "country": payload.country,
        "monthlyIncome": payload.monthlyIncome,
        "viseClub": payload.viseClub,
        "cardType": payload.cardType,
    }

    return ClientOut(
        clientId=client_id,
        name=payload.name,
        cardType=payload.cardType,
        status="Registered",
        message=f"Cliente apto para tarjeta {payload.cardType}",
    )

@app.post("/purchase", response_model=PurchaseOut, responses={400: {"model": ErrorOut}})
def register_purchase(payload: PurchaseIn):
    client = CLIENTS.get(payload.clientId)
    if not client:
        raise HTTPException(status_code=400, detail={"status": "Rejected", "error": "Cliente no encontrado"})

    # Restricciones durante la compra (ver nota dentro de la función)
    pr_error = purchase_restrictions(client, payload)
    if pr_error:
        raise HTTPException(status_code=400, detail={"status": "Rejected", "error": pr_error})

    discount, label = compute_discount(client, payload)
    final_amount = round(payload.amount - discount, 2)

    return PurchaseOut(
        status="Approved",
        purchase=PurchaseInfo(
            clientId=payload.clientId,
            originalAmount=payload.amount,
            discountApplied=round(discount, 2),
            finalAmount=final_amount,
            benefit=label,
        ),
    )

# Ruta simple para verificar que corre
@app.get("/")
def root():
    return {"ok": True, "service": "VISE API"}