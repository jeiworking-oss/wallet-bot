import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Credenciales de Meta
WHATSAPP_TOKEN = "AQUÍ_PEGA_TU_TOKEN_DE_ACCESO_DE_META"
PHONE_NUMBER_ID = "1295700236962501"

# Credenciales de Wallet by BudgetBakers
WALLET_TOKEN = "AQUÍ_PEGA_TU_TOKEN_DE_WALLET"
WALLET_API_URL = "https://rest.budgetbakers.com/wallet/v1/api/records"


@app.route("/webhook", methods=["GET"])
def verify_webhook():
  verify_token = "mi_token_secreto_123"
  mode = request.args.get("hub.mode")
  token = request.args.get("hub.verify_token")
  challenge = request.args.get("hub.challenge")

  if mode and token:
    if mode == "subscribe" and token == verify_token:
      return challenge, 200
  return "Error", 403


@app.route("/webhook", methods=["POST"])
def receive_message():
  body = request.get_json()
  try:
    mensaje_texto = (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("messages", [{}])[0]
        .get("text", {})
        .get("body")
    )
    remitente = (
        body.get("entry", [{}])[0]
        .get("changes", [{}])[0]
        .get("value", {})
        .get("messages", [{}])[0]
        .get("from")
    )

    if mensaje_texto:
      print(f"Mensaje recibido de {remitente}: {mensaje_texto}")

      partes = mensaje_texto.strip().split(" ", 1)
      monto = float(partes[0])
      concepto = partes[1] if len(partes) > 1 else "Gasto general"

      payload_wallet = {
          "amount": monto,
          "currency": "ARS",
          "note": concepto,
          "type": 1,
          "date": requests.utils.datetime.datetime.now().isoformat(),
      }

      headers_wallet = {
          "Authorization": f"Bearer {WALLET_TOKEN}",
          "Content-Type": "application/json",
      }

      response = requests.post(
          WALLET_API_URL, json=payload_wallet, headers=headers_wallet
      )

      if response.status_code in [200, 201]:
        enviar_respuesta_whatsapp(remitente, f"✅ Gasto de ${monto} registrado.")
      else:
        enviar_respuesta_whatsapp(
            remitente, "❌ Error al guardar en Wallet."
        )

  except Exception as e:
    print(f"Error procesando mensaje: {e}")

  return jsonify({"status": "success"}), 200


def enviar_respuesta_whatsapp(destinatario, texto):
  url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
  headers = {
      "Authorization": f"Bearer {WHATSAPP_TOKEN}",
      "Content-Type": "application/json",
  }
  payload = {
      "messaging_product": "whatsapp",
      "to": destinatario,
      "type": "text",
      "text": {"body": texto},
  }
  requests.post(url, json=payload, headers=headers)


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
