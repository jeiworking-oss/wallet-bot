from datetime import datetime
import json
import os
from google import genai
from google.genai import types
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Credenciales y URLs
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
WALLET_TOKEN = "eyJraWQiOiI1NmYxZjE1ZS1hZTllLTQzMzQtYjUzYS0zNGM1YWYyMzBiNjMiLCJhbGciOiJSUzI1NiJ9.eyJmbGF2b3IiOiI2NmMzODViOC1iMzU5LTQ3YjEtYmE3Ni0wMDNiM2UwYWRkNDAiLCJhdWQiOiJmMzE2MmFkNS00NmIwLTRiYTctYThmMy0yMzkxMTBkNzhkNjgiLCJpc3MiOiJXYWxsZXQtYXV0aCIsImV4cCI6MTgxOTc3NjkxMCwiZ3JhbnQiOiJhcGkiLCJpYXQiOjE3ODgyNDA5MTAsImp0aSI6ImIxNjgzNTdmLWQ2ZWYtNDZlZi1hYjc0LTAwNTFhNzliNGY5MCIsImVtYWlsIjoiYXBvbGluYXJlczIuMEBnbWFpbC5jb20ifQ.BYqlLm0cNUgt5zIsN8VPSLCt89x7IQmxL5a9IjfvmyyjBSh2eQgvmbQ1qqCG7L4OyjFOKzyHdExMyQ9m7g4fxrHF4I1ZtrIojhYljLvfZuKrgT-1IXGAQgF-tsIMAuaTQsPRrBLE28URn-ecAlvufTRW8yM9I6MSmQDL9PRBlSBhqxi-iVi1LfDpljrxsD2tpSWfZFJ8Ft9O7mlgPwvpNNJEZCKlOKteKyFRxQ7RkKWmKA_ekWi4ZCIsHWggUeod8SRp5GYO8ZWmLu8G_S82dEm3DPegt0nDooks-ff5PacXQ-vwviIFh9KiylFgCWG7u3WF2wqaIL80ONALRqcI8g"

WALLET_API_URL = "https://rest.budgetbakers.com/wallet/v1/api/records"

# Diccionario de cuentas alineado con tu Wallet
MIS_BILLETERAS = {
    "efectivo": "ID_REAL_EFECTIVO",
    "santander": "ID_REAL_SANTANDER",
    "naranja": "ID_REAL_NARANJAX",
    "naranjax": "ID_REAL_NARANJAX",
    "mp": "ID_REAL_MERCADOPAGO",
    "mercadopago": "ID_REAL_MERCADOPAGO",
    "astropay": "ID_REAL_ASTROPAY",
}

# Inicializar cliente oficial de Gemini
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
        - "amount": número decimal con el monto total de la transacción.
        - "note": concepto limpio y descriptivo del gasto.
        - "wallet": cuenta mencionada (efectivo, santander, naranja, mercadopago, astropay) o null si no se especifica.
        Ejemplo de salida esperada: [{"amount": 18409.0, "note": "burga mcdonals", "wallet": "mercadopago"}]
        Si no hay montos, devuelve [].
        """

    # Llamada blindada con el SDK oficial (evita bloqueos de timeout de requests)
    response = ai_client.models.generate_content(
        model="gemini-2.5-flash",
        contents=f"{prompt_base}\n\nMensaje del usuario: '{texto_usuario}'",
        config=types.GenerateContentConfig(response_mime_type="application/json"),
    )

    texto_respuesta = response.text if response.text else "[]"

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
            "amount": monto,
            "currency": "ARS",
            "note": concepto,
            "type": 1,
            "date": datetime.now().isoformat(),
        }

        for key, acc_id in MIS_BILLETERAS.items():
          if key in billetera_sugerida and not acc_id.startswith("ID_REAL_"):
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

    res_wallet = requests.post(
        WALLET_API_URL, json=payloads_wallet, headers=headers_wallet, timeout=15
    )

    if res_wallet.status_code in [200, 201]:
      enviar_respuesta_telegram(
          chat_id,
          f"✅ Se registraron {len(payloads_wallet)} transacción(es) con éxito.",
      )
    else:
      print(f"Error en Wallet API: {res_wallet.status_code} - {res_wallet.text}")
      enviar_respuesta_telegram(
          chat_id, "⚠️ La API de Wallet rechazó el lote de registros."
      )

  except Exception as e:
    print(f"Error crítico global en el webhook: {e}")
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
    requests.post(url, json={"chat_id": chat_id, "text": texto}, timeout=5)
  except Exception as e:
    print(f"Error enviando mensaje de respuesta a Telegram: {e}")


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
