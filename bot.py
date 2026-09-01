from datetime import datetime
import json
import os
from google import genai
from google.genai import types
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
    "santander": "0d855ecf-3ddd-4954-a4de-08673da9a535",
    "tdcnaranjax": "1aa446c0-d01e-4f22-b572-238979fc0a48",
    "tarjetanaranja": "1aa446c0-d01e-4f22-b572-238979fc0a48",
}

# Inicializar cliente oficial de Google GenAI
ai_client = genai.Client(api_key=GEMINI_API_KEY)


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
      enviar_respuesta_telegram(
          chat_id, "⚠️ Por ahora solo estoy procesando mensajes de texto."
      )
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

    # Llamada a Gemini con manejo de excepciones por timeout holgado
    try:
      response = ai_client.models.generate_content(
          model="gemini-2.5-flash",
          contents=f"{prompt_base}\n\nMensaje del usuario: '{texto_usuario}'",
          config=types.GenerateContentConfig(
              response_mime_type="application/json"
          ),
      )
      texto_respuesta = response.text if response.text else "[]"
    except Exception as gemini_err:
      print(f"Error con Gemini (timeout de 180s alcanzado): {gemini_err}")
      enviar_respuesta_telegram(
          chat_id,
          "⚠️ El servidor despertó tarde o la IA tardó demasiado. Intenta de"
          " nuevo.",
      )
      return jsonify({"status": "gemini_timeout"}), 200

    try:
      parsed_data = json.loads(texto_respuesta)
      transacciones = (
          parsed_data if isinstance(parsed_data, list) else [parsed_data]
      )
    except json.JSONDecodeError:
      enviar_respuesta_telegram(
          chat_id, "⚠️ El formato devuelto por la IA no fue un JSON válido."
      )
      return jsonify({"status": "json_decode_error"}), 200

    if not transacciones:
      enviar_respuesta_telegram(
          chat_id, "⚠️ No pude detectar ningún gasto válido en tu mensaje."
      )
      return jsonify({"status": "no_transactions"}), 200

    payloads_wallet = []
    for tx in transacciones:
      monto = float(tx.get("amount", 0.0))
      concepto = str(tx.get("note", "Gasto general"))
      billetera_sugerida = str(tx.get("wallet", "")).lower()

      if monto > 0:
        item = {
            "amount": -abs(monto),  # Negativo para registrar como gasto
            "note": concepto,
            "date": datetime.now().isoformat() + "Z",
        }

        for key, acc_id in MIS_BILLETERAS.items():
          if key in billetera_sugerida:
            item["accountId"] = acc_id
            break

        payloads_wallet.append(item)

    if not payloads_wallet:
      enviar_respuesta_telegram(
          chat_id, "⚠️ Se detectó texto pero ningún monto válido para registrar."
      )
      return jsonify({"status": "no_valid_amounts"}), 200

    headers_wallet = {
        "Authorization": f"Bearer {WALLET_TOKEN}",
        "Content-Type": "application/json",
    }

    # Petición a Wallet con timeout blindado de 180 segundos (3 minutos)
    res_wallet = requests.post(
        WALLET_API_URL, json=payloads_wallet, headers=headers_wallet, timeout=180
    )

    if res_wallet.status_code in [200, 201]:
      enviar_respuesta_telegram(
          chat_id,
          f"✅ Se registraron {len(payloads_wallet)} transacción(es) con éxito en"
          " tu Wallet.",
      )
    else:
      print(f"Error en Wallet API ({res_wallet.status_code}): {res_wallet.text}")
      enviar_respuesta_telegram(
          chat_id,
          f"⚠️ La API de Wallet rechazó el registro: {res_wallet.text}",
      )

  except Exception as e:
    print(f"Error crítico global: {e}")
    if chat_id:
      try:
        enviar_respuesta_telegram(
            chat_id, "❌ Ocurrió un error inesperado procesando tu solicitud."
        )
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
