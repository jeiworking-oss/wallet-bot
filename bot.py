import json
import os
import requests
from flask import Flask, jsonify, request
from google import genai
from google.genai import types

app = Flask(__name__)

# Credenciales y URLs
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

WALLET_TOKEN = "eyJraWQiOiI1NmYxZjE1ZS1hZTllLTQzMzQtYjUzYS0zNGM1YWYyMzBiNjMiLCJhbGciOiJSUzI1NiJ9.eyJmbGF2b3IiOiJXYWxsZXQiLCJzdWIiOiI2NmMzODViOC1iMzU5LTQ3YjEtYmE3Ni0wMDNiM2UwYWRkNDAiLCJhdWQiOiJmMzE2MmFkNS00NmIwLTRiYTctYThmMy0yMzkxMTBkNzhkNjgiLCJpc3MiOiJXYWxsZXQtYXV0aCIsImV4cCI6MTgxOTc2NzM1MCwiZ3JhbnQiOiJhcGkiLCJpYXQiOjE3ODgyMzEzNTAsImp0aSI6ImZmYzhmZWRiLWE2ODQtNDY2ZC1iZDAxLTgzZTQxNjA1OGU2YiIsImVtYWlsIjoiYXBvbGluYXJlczIuMEBnbWFpbC5jb20ifQ.kYNZRFZqBXI3u25OuxZKGcGgx8TyAU3J4Y2ehrjojM5kEI2lbTfJHe5wYSuaGE0PXJN-CgxaB5KdFF3-3ogBIa1r0zG16RYnTDs6w2CDzAbWm5sRFO9EPCct5ZyGQ28AJddrHSAIhLODsyuGigl_xSmdtCwJwPTh7xxgnEcPfW5SyIx5AE0TY6EDnoXstPJ0kmszat3RGH_aD7G-ulXYDn5KIJbCgjv2if7l-Wal9mNjfKtcmYxVSGJSckwLSWqPldJonOR6_o6jKGQIyeP6pta0LT_Mnw8LgHp_EM78cmrHOI10kRdRxhCEErgNHW4FUdmj6ZhrCt-RdH37MsBbGA"
WALLET_API_URL = "https://rest.budgetbakers.com/wallet/v1/api/records"

# Mapeo de tus billeteras (puedes agregar los IDs reales de tus cuentas de BudgetBakers aquí)
MIS_BILLETERAS = {
    "efectivo": "ID_CUENTA_EFECTIVO_AQUI",
    "mercadopago": "ID_CUENTA_MP_AQUI",
    "uala": "ID_CUENTA_UALA_AQUI",
}

# Inicializar cliente de Gemini
client = genai.Client(api_key=GEMINI_API_KEY)


@app.route(f"/webhook/{TELEGRAM_TOKEN}", methods=["POST"])
def receive_telegram_message():
  data = request.get_json()
  chat_id = None
  try:
    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")

    if not chat_id:
      return jsonify({"status": "success"}), 200

    contenido_para_ia = []
    prompt_base = """
        Eres un asistente financiero experto en parsear gastos para BudgetBakers Wallet.
        Analiza el texto o la(s) imagen(es) provista(s) por el usuario.
        El usuario puede enviar una o varias transacciones juntas (en texto o en una captura de pantalla de comprobantes).
        Devuelve estrictamente un JSON que sea una LISTA de objetos (incluso si es solo uno), con este formato exacto por cada transacción:
        [
          {
            "amount": 1500.0,
            "note": "concepto limpio",
            "wallet": "nombre aproximado de la billetera mencionada o detectada (ej: efectivo, mercadopago, uala, o null si no se sabe)"
          }
        ]
        Si no hay montos válidos, devuelve una lista vacía []. No agregues markdown extra (como ```json), devuelve únicamente el texto JSON puro.
        """

    # Caso 1: Envía una foto o captura de pantalla
    if "photo" in message:
      # Telegram manda varios tamaños, agarramos el de mayor resolución (el último)
      photo = message["photo"][-1]
      file_id = photo["file_id"]

      # Obtener ruta de descarga en Telegram
      file_info_res = requests.get(
          f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_TOKEN}/getFile?file_id={file_id}"
      )
      file_path = file_info_res.json()["result"]["file_path"]

      # Descargar la imagen binaria
      img_res = requests.get(
          f"[https://api.telegram.org/file/bot](https://api.telegram.org/file/bot){TELEGRAM_TOKEN}/{file_path}"
      )
      image_bytes = img_res.content

      # Preparar contenido multimodal para Gemini
      contenido_para_ia = [
          prompt_base,
          types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
      ]

    # Caso 2: Envía texto plano coloquial
    elif "text" in message:
      texto_usuario = message["text"]
      contenido_para_ia = [f"{prompt_base}\n\nMensaje: '{texto_usuario}'"]

    if contenido_para_ia:
      # Llamada a Gemini (soporta texto e imágenes de manera nativa)
      response = client.models.generate_content(
          model="gemini-2.5-flash", contents=contenido_para_ia
      )

      # Limpiar la respuesta por si trae bloques de código markdown
      texto_respuesta = (
          response.text.replace("```json", "")
          .replace("```", "")
          .strip()
      )
      transacciones = json.loads(texto_respuesta)

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

          # Asignar billetera si coincide con el mapeo
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
          chat_id,
          f"✅ Se registraron {registros_exitosos} transacción(es) con"
          " éxito.",
      )

  except Exception as e:
    print(f"Error procesando solicitud: {e}")
    if chat_id:
      enviar_respuesta_telegram(
          chat_id, "❌ Ocurrió un error procesando tu gasto."
      )

  return jsonify({"status": "success"}), 200


def enviar_respuesta_telegram(chat_id, texto):
  url = f"[https://api.telegram.org/bot](https://api.telegram.org/bot){TELEGRAM_TOKEN}/sendMessage"
  requests.post(url, json={"chat_id": chat_id, "text": texto})


if __name__ == "__main__":
  port = int(os.environ.get("PORT", 5000))
  app.run(host="0.0.0.0", port=port)
