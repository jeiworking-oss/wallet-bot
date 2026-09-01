import uuid
from datetime import datetime, timezone
import json
import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WALLET_TOKEN = os.environ.get("WALLET_TOKEN")

WALLET_API_URL = "https://rest.budgetbakers.com/wallet/v1/api/records"

# Mapeo oficial con los IDs reales de tus cuentas
MIS_BILLETERAS = {
    "arq": "19240bbe-84a9-4ae6-b826-f3ecbeff1cf0",
    "efectivo": "db88efbb-5174-47ac-ac9e-d22c0b894dca",
    "mercadopago": "bcd72d7e-f54c-4eb6-8e5f-2e593a215ac0",
    "mp": "bcd72d7e-f54c-4eb6-8e5f-2e593a215ac0",
    "naranjax": "f82db9a5-869a-4e4b-9132-4754117fb6f9",
    "naranja": "f82db9a5-869a-4e4b-9132-4754117fb6f9",
    "nexo": "83ab2474-6b86-48e1-995b-ad6a8a0a0b1a",
    "santander": "0d855ecf-3dd0-4954-a4de-08673da9a535",
    "tdcnaranjax": "1aa446c0-d01e-4f22-b572-238979fc0a48",
    "tarjetanaranja": "1aa446c0-d01e-4f22-b572-238979fc0a48",
}

@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def receive_telegram_message():
  chat_id = None
  try:
    data = request.get_json()
    if not data or "message" not in data:
      return jsonify({"status": "ignored"}), 200

    message = data["message"]
    chat_id = message.get("chat", {}).get("id")
    texto_usuario = message.get("text", "")

    if not chat_id:
      return jsonify({"status": "no_chat_id"}), 200

    if not texto_usuario:
      enviar_respuesta_telegram(chat_id, "⚠️ Por ahora solo estoy procesando mensajes de texto.")
      return jsonify({"status": "no_text"}), 200

    prompt_base = """
        Analiza el texto del usuario y extrae los gastos o transacciones financieras.
        Devuelve estrictamente una lista JSON pura con objetos que tengan exactamente estas claves:
        - "amount": número decimal positivo con el monto total de la transacción.
        - "note": concepto limpio y descriptivo del gasto.
        - "wallet": cuenta mencionada (arq, efectivo, mercadopago, naranjax, nexo, santander, tdcnaranjax) o null si no se especifica.
        Ejemplo de salida esperada: [{"amount": 18409.0, "note": "burga mcdonals", "wallet": "mercadopago"}]
        Si no hay montos, devuelve [].
        """

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    payload_gemini = {
        "contents": [{"parts": [{"text": f"{prompt_base}\n\nMensaje del usuario: '{texto_usuario}'"}]}],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    try:
      res_gemini = requests.post(gemini_url, json=payload_gemini, timeout=180)
      if res_gemini.status_code != 200:
        enviar_respuesta_telegram(chat_id, "❌ Error de comunicación con el motor de IA.")
        return jsonify({"status": "gemini_error"}), 200
    except Exception as gemini_err:
      enviar_respuesta_telegram(chat_id, "⚠️ El motor de IA tardó demasiado en responder.")
      return jsonify({"status": "gemini_timeout"}), 200

    res_json = res_gemini.json()
    candidates = res_json.get("candidates", [])
    if not candidates:
      enviar_respuesta_telegram(chat_id, "⚠️ No obtuve respuesta válida de la IA.")
      return jsonify({"status": "no_candidates"}), 200

    texto_respuesta = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "[]")

    try:
      parsed_data = json.loads(texto_respuesta)
      transacciones = parsed_data if isinstance(parsed_data, list) else [parsed_data]
    except json.JSONDecodeError:
      enviar_respuesta_telegram(chat_id, "⚠️ El formato devuelto por la IA no fue un JSON válido.")
      return jsonify({"status": "json_decode_error"}), 200

    if not transacciones:
      enviar_respuesta_telegram(chat_id, "⚠️ No pude detectar ningún gasto válido en tu mensaje.")
      return jsonify({"status": "no_transactions"}), 200

    payloads_wallet = []
    for tx in transacciones:
      monto = float(tx.get("amount", 0.0))
      concepto = str(tx.get("note", "Gasto general"))
      billetera_sugerida = str(tx.get("wallet", "")).lower()

      if monto > 0:
        # Objeto estructuralmente perfecto para la Sync API de BudgetBakers
        item = {
            "id": str(uuid.uuid4()), # Previene duplicados absolutos
            "amount": -abs(monto),
            "recordDate": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z'), # Formato ISO estricto
            "note": concepto,
            "paymentType": "debit_card", # Valor estándar
            "recordState": "cleared"     # Forzamos estado liquidado
        }

        # Búsqueda de cuenta con Fallback obligatorio
        cuenta_asignada = False
        for key, acc_id in MIS_BILLETERAS.items():
          if key in billetera_sugerida:
            item["accountId"] = acc_id
            cuenta_asignada = True
            break
        
        # Si la IA no detectó billetera, lo mandamos a Efectivo para que no explote la API por falta de accountId
        if not cuenta_asignada:
            item["accountId"] = MIS_BILLETERAS["efectivo"]

        payloads_wallet.append(item)

    if not payloads_wallet:
      enviar_respuesta_telegram(chat_id, "⚠️ Se detectó texto pero ningún monto válido para registrar.")
      return jsonify({"status": "no_valid_amounts"}), 200

    headers_wallet = {
        "Authorization": f"Bearer {WALLET_TOKEN}",
        "Content-Type": "application/json",
    }

    res_wallet = requests.post(WALLET_API_URL, json=payloads_wallet, headers=headers_wallet, timeout=180)

    if res_wallet.status_code in [200, 201]:
      enviar_respuesta_telegram(chat_id, f"✅ Hit conectado: Se registraron {len(payloads_wallet)} transacción(es) con éxito en tu Wallet.")
    else:
      print(f"Error en Wallet API ({res_wallet.status_code}): {res_wallet.text}")
      enviar_respuesta_telegram(chat_id, f"⚠️ La API de Wallet rechazó el registro: {res_wallet.text}")

  except Exception as e:
    print(f"Error crítico global: {e}")
    if chat_id:
      try:
        enviar_respuesta_telegram(chat_id, "❌ Ocurrió un error inesperado procesando tu solicitud.")
      except:
        pass

  return jsonify({"status": "success"}), 200

def enviar_respuesta_telegram(chat_id, texto):
  if not TELEGRAM_TOKEN or not chat_id:
    return
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  try:
    requests.post(url, json={"chat_id": chat_id, "text": texto}, timeout=15)
  except Exception as e:
    print(f"Error enviando mensaje a Telegram: {e}")

if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
