from datetime import datetime
import json
import os
import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

# Credenciales y URLs esenciales
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

WALLET_TOKEN = "eyJraWQiOiI1NmYxZjE1ZS1hZTllLTQzMzQtYjUzYS0zNGM1YWYyMzBiNjMiLCJhbGciOiJSUzI1NiJ9.eyJmbGF2b3IiOiI2NmMzODViOC1iMzU5LTQ3YjEtYmE3Ni0wMDNiM2UwYWRkNDAiLCJhdWQiOiJmMzE2MmFkNS00NmIwLTRiYTctYThmMy0yMzkxMTBkNzhkNjgiLCJpc3MiOiJXYWxsZXQtYXV0aCIsImV4cCI6MTgxOTc2NzM1MCwiZ3JhbnQiOiJhcGkiLCJpYXQiOjE3ODgyMzEzTSIsImp0aSI6ImZmYzhmZWRiLWE2ODQtNDY2ZC1iZDAxLTgzZTQxNjA1OGU2YiIsImVtYWlsIjoiYXBvbGluYXJlczIuMEBnbWFpbC5jb20ifQ.kYNZRFZqBXI3u25OuxZKGcGgx8TyAU3J4Y2ehrjojM5kEI2lbTfJHe5wYSuaGE0PXJN-CgxaB5KdFF3-3ogBIa1r0zG16RYnTDs6w2CDzAbWm5sRFO9EPCct5ZyGQ28AJddrHSAIhLODsyuGigl_xSmdtCwJwPTh7xxgnEcPfW5SyIx5AE0TY6EDnoXstPJ0kmszat3RGH_aD7G-ulXYDn5KIJbCgjv2if7l-Wal9mNjfKtcmYxVSGJSckwLSWqPldJonOR6_o6jKGQIyeP6pta0LT_Mnw8LgHp_EM78cmrHOI10kRdRxhCEErgNHW4FUdmj6ZhrCt-RdH37MsBbGA"
WALLET_API_URL = "https://rest.budgetbakers.com/wallet/v1/api/records"

# Mapeo de billeteras (Reemplaza los strings por los IDs reales de tu API de BudgetBakers cuando gustes)
MIS_BILLETERAS = {
    "efectivo": "ID_REAL_EFECTIVO",
    "santander": "ID_REAL_SANTANDER",
    "naranjax": "ID_REAL_NARANJAX",
    "mercadopago": "ID_REAL_MERCADOPAGO",
    "tdcnaranjax": "ID_REAL_TDC_NARANJAX",
    "nexo": "ID_REAL_NEXO",
    "arq": "ID_REAL_ARQ",
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
      enviar_respuesta_telegram(
          chat_id, "⚠️ Por ahora solo estoy procesando mensajes de texto."
      )
      return jsonify({"status": "no_text"}), 200

    # 1. Prompt estructurado para Gemini
    prompt_base = """
        Analiza el texto del usuario y extrae los gastos o transacciones financieras.
        Devuelve estrictamente una lista JSON pura con objetos que tengan exactamente estas claves:
        - "amount": número decimal con el monto total de la transacción.
        - "note": concepto limpio y descriptivo del gasto.
        - "wallet": el nombre de la cuenta mencionada entre estas opciones exactas (efectivo, santander, naranjax, mercadopago, tdcnaranjax, nexo, arq) o null si no se especifica.
        Ejemplo de salida esperada: [{"amount": 18409.0, "note": "burga mcdonals", "wallet": "mercadopago"}]
        Si no hay montos, devuelve [].
        """

    gemini_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key={GEMINI_API_KEY}"
    payload_gemini = {
        "contents": [{
            "parts": [{
                "text": f"{prompt_base}\n\nMensaje del usuario: '{texto_usuario}'"
            }]
        }],
        "generationConfig": {"response_mime_type": "application/json"},
    }

    # 2. Petición segura a Gemini con control de errores HTTP
    res_gemini = requests.post(gemini_url, json=payload_gemini, timeout=10)
    if res_gemini.status_code != 200:
      print(f"Error en API Gemini: {res_gemini.text}")
      enviar_respuesta_telegram(
          chat_id, "❌ Error de comunicación con el motor de IA."
      )
      return jsonify({"status": "gemini_error"}), 200

    res_json = res_gemini.json()

    # 3. Extracción segura de la respuesta
    try:
      candidates = res_json.get("candidates", [])
      if not candidates:
        raise ValueError("No candidates found in Gemini response")

      texto_respuesta = (
          candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "[]")
      )
    except Exception as parse_err:
      print(f"Error extrayendo texto de Gemini: {parse_err}, Payload: {res_json}")
      enviar_respuesta_telegram(
          chat_id, "⚠️ No pude interpretar la respuesta estructurada de la IA."
      )
      return jsonify({"status": "extraction_error"}), 200

    # 4. Parseo seguro de JSON con manejo de excepciones
    try:
      parsed_data = json.loads(texto_respuesta)
      transacciones = (
          parsed_data if isinstance(parsed_data, list) else [parsed_data]
      )
    except json.JSONDecodeError:
      print(f"Error de JSON decodificando: {texto_respuesta}")
      enviar_respuesta_telegram(
          chat_id, "⚠️ El formato devuelto por la IA no fue un JSON válido."
      )
      return jsonify({"status": "json_decode_error"}), 200

    if not transacciones:
      enviar_respuesta_telegram(
          chat_id, "⚠️ No pude detectar ningún gasto válido en tu mensaje."
      )
      return jsonify({"status": "no_transactions"}), 200

    # 5. Iteración y registro en BudgetBakers Wallet
    registros_exitosos = 0
    headers_wallet = {
        "Authorization": f"Bearer {WALLET_TOKEN}",
        "Content-Type": "application/json",
    }

    for tx in transacciones:
      try:
        monto = float(tx.get("amount", 0.0))
        concepto = str(tx.get("note", "Gasto general"))
        billetera_sugerida = str(tx.get("wallet", "")).lower()

        if monto > 0:
          payload_wallet = {
              "amount": monto,
              "currency": "ARS",
              "note": concepto,
              "type": 1,  # Gasto
              "date": datetime.now().isoformat(),
          }

          # Asignar ID de cuenta si coincide con el mapeo
          for key, acc_id in MIS_BILLETERAS.items():
            if key in billetera_sugerida:
              payload_wallet["accountId"] = acc_id
              break

          res_wallet = requests.post(
              WALLET_API_URL, json=payload_wallet, headers=headers_wallet, timeout=10
          )
          
          if res_wallet.status_code in [200, 201]:
            registros_exitosos += 1
          else:
            print(f"Error en Wallet API: {res_wallet.status_code} - {res_wallet.text}")

      except Exception as tx_err:
        print(f"Error procesando transacción individual {tx}: {tx_err}")

    # 6. Respuesta final al usuario en Telegram
    if registros_exitosos > 0:
      enviar_respuesta_telegram(
          chat_id, f"✅ Se registraron {registros_exitosos} transacción(es) con éxito rey."
      )
    else:
      enviar_respuesta_telegram(
          chat_id, "⚠️ Se detectó el gasto pero la API de Wallet rechazó el registro."
      )

  except Exception as e:
    print(f"Error crítico global en webhook: {e}")
    if chat_id:
      try:
        enviar_respuesta_telegram(
            chat_id, "❌ Ocurrió un error inesperado procesando tu solicitud."
        )
      except:
        pass

  # Siempre devolvemos 200 a Telegram para evitar bucles de reintentos
  return jsonify({"status": "success"}), 200


def enviar_respuesta_telegram(chat_id, texto):
  if not TELEGRAM_TOKEN or not chat_id:
    return
  url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
  try:
    requests.post(url, json={"chat_id": chat_id, "text": texto}, timeout=5)
  except Exception as e:
    print(f"Error enviando mensaje a Telegram: {e}")


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
