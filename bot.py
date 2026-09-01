import json
import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Credenciales y URLs
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

WALLET_TOKEN = "eyJraWQiOiI1NmYxZjE1ZS1hZTllLTQzMzQtYjUzYS0zNGM1YWYyMzBiNjMiLCJhbGciOiJSUzI1NiJ9.eyJmbGF2b3IiOiJXYWxsZXQiLCJzdWIiOiI2NmMzODViOC1iMzU5LTQ3YjEtYmE3Ni0wMDNiM2UwYWRkNDAiLCJhdWQiOiJmMzE2MmFkNS00NmIwLTRiYTctYThmMy0yMzkxMTBkNzhkNjgiLCJpc3MiOiJXYWxsZXQtYXV0aCIsImV4cCI6MTgxOTc2NzM1MCwiZ3JhbnQiOiJhcGkiLCJpYXQiOjE3ODgyMzEzTSIsImp0aSI6ImZmYzhmZWRiLWE2ODQtNDY2ZC1iZDAxLTgzZTQxNjA1OGU2YiIsImVtYWlsIjoiYXBvbGluYXJlczIuMEBnbWFpbC5jb20ifQ.kYNZRFZqBXI3u25OuxZKGcGgx8TyAU3J4Y2ehrjojM5kEI2lbTfJHe5wYSuaGE0PXJN-CgxaB5KdFF3-3ogBIa1r0zG16RYnTDs6w2CDzAbWm5sRFO9EPCct5ZyGQ28AJddrHSAIhLODsyuGigl_xSmdtCwJwPTh7xxgnEcPfW5SyIx5AE0TY6EDnoXstPJ0kmszat3RGH_aD7G-ulXYDn5KIJbCgjv2if7l-Wal9mNjfKtcmYxVSGJSckwLSWqPldJonOR6_o6jKGQIyeP6pta0LT_Mnw8LgHp_EM78cmrHOI10kRdRxhCEErgNHW4FUdmj6ZhrCt-RdH37MsBbGA"
WALLET_API_URL = "https://rest.budgetbakers.com/wallet/v1/api/records"

# Mapeo de tus billeteras reales de Wallet
MIS_BILLETERAS = {
    "efectivo": "efectivo",
    "santander": "santander",
    "naranjax": "naranjax",
    "mercadopago": "mercadopago",
    "tdcnaranjax": "tdc_naranjax",
    "nexo": "nexo",
    "arq": "arq",
}


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def receive_telegram_message():
  data = request.get_json()
  chat_id = None
  try:
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if not chat_id:
      return jsonify({"status": "success"}), 200

    texto_usuario = ""
    if "text" in message:
      texto_usuario = message["text"]

    if not texto_usuario:
      enviar_respuesta_telegram(
          chat_id, "⚠️ Por ahora solo estoy procesando mensajes de texto."
      )
      return jsonify({"status": "success"}), 200

    prompt_base = """
        Eres un asistente financiero experto en parsear gastos para BudgetBakers Wallet.
        Analiza el texto provisto por el usuario. Puede contener una o varias transacciones juntas.
        Tiene configuradas las siguientes cuentas o billeteras: Efectivo, Santander, NaranjaX, MercadoPago, TDC NaranjaX, Nexo, ARQ.
        Devuelve estrictamente un JSON que sea una LISTA de objetos, con este formato exacto por cada transacción:
        [
          {
            "amount": 1500.0,
            "note": "concepto limpio",
            "wallet": "nombre exacto o aproximado de la billetera mencionada o detectada de la lista anterior, o null si no se sabe"
          }
        ]
        Si no hay montos válidos, devuelve una lista vacía []. No agregues markdown ni bloques de código extra, devuelve únicamente el texto JSON puro.
        """

    # Usamos gemini-2.0-flash para asegurar compatibilidad total con la API REST
    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload_gemini = {
        "contents": [{
            "parts": [{
                "text": f"{prompt_base}\n\nMensaje del usuario: '{texto_usuario}'"
            }]
        }]
    }

    res_gemini = requests.post(gemini_url, json=payload_gemini)
    res_json = res_gemini.json()

    # Extracción robusta del texto de respuesta
    try:
      texto_respuesta = (
          res_json.get("candidates", [])[0]
          .get("content", {})
          .get("parts", [])[0]
          .get("text", "[]")
      )
    except Exception as ex:
      print(f"Error parseando JSON de Gemini: {ex}, Respuesta: {res_json}")
      enviar_respuesta_telegram(
          chat_id, "⚠️ Error interpretando la respuesta de la IA."
      )
      return jsonify({"status": "success"}), 200

    texto_respuesta = (
        texto_respuesta.replace("```json", "").replace("```", "").strip()
    )
    parsed_data = json.loads(texto_respuesta)

    transacciones = (
        parsed_data if isinstance(parsed_data, list) else [parsed_data]
    )

    if not transacciones:
      enviar_respuesta_telegram(
          chat_id, "⚠️ No pude detectar ningún gasto válido en tu mensaje."
      )
      return jsonify({"status": "success"}), 200

    registros_exitosos = 0
    for tx in transacciones:
      monto = tx.get("amount", 0)
      concepto = tx.get("note", "Gasto general")
      billetera_sugerida = str(tx.get("wallet", "")).lower()

      if monto > 0:
        payload_wallet = {
            "amount": monto,
            "currency": "ARS",
            "note": concepto,
            "type": 1,
            "date": requests.utils.datetime.datetime.now().isoformat(),
        }

        for key, acc_id in MIS_BILLETERAS.items():
          if key in billetera_sugerida:
            payload_wallet["accountId"] = acc_id
            break

        headers_wallet = {
            "Authorization": f"Bearer {WALLET_TOKEN}",
            "Content-Type": "application/json",
        }

        res_wallet = requests.post(
            WALLET_API_URL, json=payload_wallet, headers=headers_wallet
        )
        if res_wallet.status_code in [200, 201]:
          registros_exitosos += 1

    enviar_respuesta_telegram(
        chat_id, f"✅ Se registraron {registros_exitosos} transacción(es) con éxito."
    )

  except Exception as e:
    print(f"Error procesando solicitud: {e}")
    if chat_id and TELEGRAM_TOKEN:
      enviar_respuesta_telegram(
          chat_id, "❌ Ocurrió un error procesando tu gasto."
      )

  return jsonify({"status": "success"}), 200


def enviar_respuesta_telegram(chat_id, texto):
  if not TELEGRAM_TOKEN:
    return
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  requests.post(url, json={"chat_id": chat_id, "text": texto})


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
